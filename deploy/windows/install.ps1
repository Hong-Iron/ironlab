#Requires -RunAsAdministrator
<#
.SYNOPSIS
    ironlab 을 Windows PC 에 설치한다 (WSL2 안의 Ubuntu 에서 돈다).

.DESCRIPTION
    ironlab 은 리눅스의 Docker·cgroup·systemd 위에서 돈다. Windows 에서는 ironlab 전용 WSL2 배포판(기본 이름
    'ironlab')을 하나 만들고 그 안에서 deploy/install.sh 를 돌린다. 이미 쓰던 WSL 배포판은 건드리지 않는다.

    하는 일
      1. Windows 판, NVIDIA 드라이버 확인 (Windows 의 AMD GPU 는 지원하지 않는다)
      2. WSL 이 없으면 설치 (그때는 재부팅 뒤 다시 실행)
      3. %UserProfile%\.wslconfig: 메모리·CPU, 네트워크 방식 (LAN 으로 열려면 Windows 11 의 mirrored)
      4. Ubuntu 24.04 공식 WSL 이미지를 받아 SHA256 확인 뒤 'ironlab' 배포판으로 등록, systemd 켜기
      5. 이 폴더의 소스를 배포판 안 /opt/ironlab-src 로 복사하고 install.sh 실행
      6. 방화벽 규칙 (LAN 일 때), 부팅 때 WSL 을 깨워 두는 예약 작업

    여러 번 실행해도 안전하다. 업데이트도 같은 명령이다 (설정·사용자·파일은 그대로).

.EXAMPLE
    # 관리자 PowerShell 에서, 저장소 폴더 안에서
    powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Admin admin -SiteName "우리 연구실 GPU"

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -Admin admin -Access tailscale -InstallDir D:\ironlab\wsl
#>
[CmdletBinding()]
param(
    [string]$Admin = "",                     # 처음 만들 관리자 이름 (영문 소문자로 시작). 비우면 물어본다
    [string]$SiteName = "",                  # 화면에 보일 서버 이름
    [ValidateSet("auto", "lan", "tailscale", "local")]
    [string]$Access = "auto",                # auto: Windows 11 이면 lan, Windows 10 이면 tailscale
    [string]$TsLogin = "",                   # (tailscale) 관리자의 Tailscale 계정 (예: name@github)
    [string]$PublicHost = "",
    [string]$PublicUrl = "",
    [int]$Port = 8700,
    [int]$GpuIndex = 0,
    [string]$Distro = "ironlab",             # 만들 WSL 배포판 이름
    [string]$InstallDir = "",                # WSL 디스크(ext4.vhdx)를 둘 폴더. 기본 %ProgramData%\ironlab\wsl. 큰 드라이브면 D:\ironlab\wsl
    [int]$MemoryGB = 0,                      # WSL 에 줄 메모리. 0 이면 실메모리의 80% (.wslconfig 에 값이 이미 있으면 그대로)
    [switch]$WithTrellis,                    # TRELLIS.2/Pixal3D 이미지까지 (1시간 이상)
    [switch]$DisableSleep                    # 전원이 연결돼 있을 때 절전·최대 절전을 끈다 (서버로 쓸 PC 라면 권장)
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3
$ProgressPreference = "SilentlyContinue"      # Invoke-WebRequest 진행 막대가 PowerShell 5.1 에서 다운로드를 크게 늦춘다
$env:WSL_UTF8 = "1"                           # wsl.exe 출력을 UTF-16 대신 UTF-8 로
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$WslCreator = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"   # Hyper-V 방화벽에서 WSL VM 을 가리키는 ID
$RootfsBase = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current"
$RootfsName = "ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"
$TaskName = "ironlab WSL"
if (-not $InstallDir) { $InstallDir = Join-Path $env:ProgramData "ironlab\wsl" }

function Step([string]$msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok([string]$msg) { Write-Host "  v $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Die([string]$msg) { Write-Host ""; Write-Host "오류: $msg" -ForegroundColor Red; exit 1 }

function Quiet([scriptblock]$cmd) {
    # 외부 프로그램을 조용히 돌리고 표준 출력만 받는다. PowerShell 5.1 은 ErrorActionPreference=Stop 일 때
    # 외부 프로그램의 stderr 를 돌려받기만 해도 예외를 던지므로, 이 안에서만 Continue 로 둔다
    $eap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return (& $cmd 2>$null) } finally { $ErrorActionPreference = $eap }
}

function Clean([object]$out) {
    # 예전 WSL 은 WSL_UTF8 을 모르고 UTF-16 으로 내보내 글자 사이에 NUL 이 낀다
    return (($out | Out-String) -replace "`0", "").Trim()
}

function Get-Distros {
    $raw = Quiet { & wsl.exe -l -q }
    return @((Clean $raw) -split "\r?\n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Invoke-Wsl([string]$script, [switch]$Quiet) {
    # bash 스크립트를 배포판 안에서 root 로 돌린다. 따옴표가 PowerShell → wsl.exe 를 지나며 깨지지 않게 base64 로 넘긴다.
    # 값은 환경변수로 넘긴다 (WSLENV). /p 가 붙은 것은 Windows 경로를 리눅스 경로로 바꿔 준다.
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script -replace "`r", "")))
    if ($Quiet) {
        return (Clean (Quiet { & wsl.exe -d $Distro -u root -e bash -c "echo $b64 | base64 -d | bash" }))
    }
    # 출력은 화면으로 바로 보낸다 (함수의 반환값에 섞이면 종료 코드를 읽을 수 없다)
    & wsl.exe -d $Distro -u root -e bash -c "echo $b64 | base64 -d | bash" | Out-Host
    return $LASTEXITCODE
}

function Set-Ini([string]$path, [string]$section, [string]$key, [string]$value, [switch]$KeepExisting) {
    # .wslconfig 의 [section] key=value 를 넣거나 바꾼다. 바뀌었으면 $true
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path $path) { foreach ($l in (Get-Content -LiteralPath $path)) { $lines.Add($l) } }
    $start = -1; $end = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $t = $lines[$i].Trim()
        if ($t -match '^\[(.+)\]$') {
            if ($start -ge 0) { $end = $i; break }
            if ($Matches[1].Trim() -ieq $section) { $start = $i }
        }
    }
    if ($start -lt 0) {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim()) { $lines.Add("") }
        $lines.Add("[$section]"); $lines.Add("$key=$value")
    } else {
        $found = $false
        for ($i = $start + 1; $i -lt $end; $i++) {
            if ($lines[$i] -match "^\s*$([regex]::Escape($key))\s*=\s*(.*)$") {
                $found = $true
                if ($KeepExisting -or $Matches[1].Trim() -eq $value) { return $false }
                $lines[$i] = "$key=$value"
                break
            }
        }
        if (-not $found) {
            $at = $end                                   # 구역 끝의 빈 줄 앞에 넣는다
            while ($at -gt $start + 1 -and -not $lines[$at - 1].Trim()) { $at-- }
            $lines.Insert($at, "$key=$value")
        }
    }
    [IO.File]::WriteAllLines($path, $lines, (New-Object Text.UTF8Encoding($false)))
    return $true
}

# ───────────────────────────────────────────────
Step "1/8 Windows 와 GPU"
$build = [Environment]::OSVersion.Version.Build
if (-not [Environment]::Is64BitOperatingSystem) { Die "64비트 Windows 가 필요합니다." }
if ($build -lt 19044) { Die "Windows 10 21H2(빌드 19044) 이상이 필요합니다 (지금 $build). Windows 업데이트 뒤 다시 실행하세요." }
$win11 = $build -ge 22621                # mirrored 네트워크는 Windows 11 22H2 부터
Ok ("Windows 빌드 $build" + $(if ($win11) { " (Windows 11)" } else { " (Windows 10)" }))

$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
$smi = Join-Path $env:SystemRoot "System32\nvidia-smi.exe"
if (-not (Test-Path $smi)) { $c = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue; if ($c) { $smi = $c.Source } }
if (-not (Test-Path $smi)) {
    if ($gpus -match "AMD|Radeon") {
        Die "Windows 에서는 NVIDIA GPU 만 지원합니다 (찾은 GPU: $($gpus -join ', ')).
    WSL2 의 Docker 컨테이너에 Radeon 을 넘기는 표준 방법이 없습니다. AMD 카드는 Ubuntu 를 직접 깐 PC 에서 deploy/install.sh 로 설치하세요."
    }
    Die "NVIDIA 드라이버(nvidia-smi.exe)를 찾지 못했습니다. https://www.nvidia.com/drivers 에서 드라이버를 설치하고 다시 실행하세요.
    찾은 GPU: $($gpus -join ', ')"
}
$smiList = @(Quiet { & $smi -L } | Where-Object { $_ -match '^GPU \d+' })
if ($smiList.Count -eq 0) { Die "nvidia-smi 가 GPU 를 읽지 못했습니다. 드라이버를 다시 설치하거나 재부팅해 보세요." }
if ($GpuIndex -ge $smiList.Count) { Die "-GpuIndex $GpuIndex 인데 GPU 는 $($smiList.Count)장입니다." }
Ok $smiList[$GpuIndex]

if ($Access -eq "auto") { $Access = if ($win11) { "auto" } else { "tailscale" } }
if (($Access -eq "lan" -or $Access -eq "auto") -and -not $win11) {
    Die "Windows 10 은 WSL 을 같은 네트워크에 바로 열 수 없습니다 (mirrored 네트워크가 Windows 11 부터). -Access tailscale 로 다시 실행하세요."
}
$netMode = if ($Access -eq "tailscale") { "nat" } else { "mirrored" }

if (-not $Admin) {
    $Admin = Read-Host "처음 만들 관리자 이름 (영문 소문자로 시작, 엔터면 admin)"
    if (-not $Admin) { $Admin = "admin" }
}
if ($Admin -and $Admin -cnotmatch '^[a-z][a-z0-9_-]{1,31}$') { Die "관리자 이름은 영문 소문자로 시작하고 소문자·숫자·-·_ 로 2~32자여야 합니다 (받은 값: $Admin)" }

# ───────────────────────────────────────────────
Step "2/8 WSL"
$vmp = (Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform).State
$wslFeature = (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux).State
if ($vmp -ne "Enabled" -or $wslFeature -ne "Enabled") {
    Warn "WSL 을 설치합니다. 끝나면 재부팅하고 이 스크립트를 다시 실행하세요."
    & wsl.exe --install --no-distribution
    if ($LASTEXITCODE -ne 0) { Die "wsl --install 이 실패했습니다. BIOS 에서 가상화(Intel VT-x / AMD SVM)가 켜져 있는지 확인하세요." }
    Write-Host ""
    Write-Host "  재부팅한 뒤 같은 명령을 다시 실행하세요." -ForegroundColor Yellow
    exit 0
}
Quiet { & wsl.exe --update } | Out-Null      # GPU·systemd·mirrored 는 최신 WSL 이 필요하다 (실패해도 계속)
$ver = Clean (Quiet { & wsl.exe --version })
if (-not $ver) { Die "WSL 이 너무 예전 판입니다. 'wsl --update' 를 직접 실행한 뒤 다시 시도하세요." }
Ok (($ver -split "\r?\n")[0])

# ───────────────────────────────────────────────
Step "3/8 WSL 설정 (%UserProfile%\.wslconfig)"
$wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
$ramGB = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$memGB = if ($MemoryGB -gt 0) { $MemoryGB } else { [math]::Max(4, [math]::Floor($ramGB * 0.8)) }
$cpus = [Environment]::ProcessorCount
$changed = $false
if (Set-Ini $wslconfig "wsl2" "memory" "${memGB}GB" -KeepExisting:($MemoryGB -le 0)) { $changed = $true }
if (Set-Ini $wslconfig "wsl2" "processors" "$cpus" -KeepExisting) { $changed = $true }
if (Set-Ini $wslconfig "wsl2" "networkingMode" $netMode) { $changed = $true }
Ok "메모리 ${memGB}GB (실메모리 ${ramGB}GB), CPU $cpus, 네트워크 $netMode"

# ───────────────────────────────────────────────
Step "4/8 '$Distro' 배포판 (Ubuntu 24.04)"
if ((Get-Distros) -contains $Distro) {
    Ok "이미 있음 (그대로 씀)"
} else {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $tar = Join-Path $env:TEMP $RootfsName
    Write-Host "  Ubuntu 24.04 WSL 이미지를 받는 중 (약 350MB)..."
    & curl.exe -fL --retry 3 -o $tar "$RootfsBase/$RootfsName"
    if ($LASTEXITCODE -ne 0) { Die "이미지를 받지 못했습니다: $RootfsBase/$RootfsName" }
    $sumsFile = Join-Path $env:TEMP "ironlab-SHA256SUMS"
    & curl.exe -fsSL --retry 3 -o $sumsFile "$RootfsBase/SHA256SUMS"
    if ($LASTEXITCODE -ne 0) { Die "SHA256SUMS 를 받지 못했습니다: $RootfsBase/SHA256SUMS" }
    $sums = Get-Content -Raw -LiteralPath $sumsFile
    Remove-Item -LiteralPath $sumsFile -Force
    $want = ($sums -split "\r?\n" | Where-Object { $_ -match [regex]::Escape($RootfsName) + '$' } | Select-Object -First 1)
    if (-not $want) { Die "SHA256SUMS 에서 $RootfsName 을 찾지 못했습니다." }
    $want = ($want -split '\s+')[0].ToLower()
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $tar).Hash.ToLower()
    if ($got -ne $want) { Remove-Item -LiteralPath $tar -Force; Die "받은 이미지의 SHA256 이 맞지 않습니다 (받음 $got, 기대 $want). 다시 실행하세요." }
    Ok "SHA256 확인"
    & wsl.exe --import $Distro $InstallDir $tar --version 2
    if ($LASTEXITCODE -ne 0) { Die "wsl --import 가 실패했습니다." }
    Remove-Item -LiteralPath $tar -Force
    Ok "등록: $Distro → $InstallDir"
}
# systemd 를 켠다 (Docker·ironlab 서비스). Windows PATH 는 섞지 않는다 (같은 이름의 Windows 프로그램이 불리지 않게)
$conf = @"
[boot]
systemd=true

[user]
default=root

[interop]
appendWindowsPath=false
"@
$confB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($conf -replace "`r", "") + "`n"))
$old = Invoke-Wsl -Quiet "cat /etc/wsl.conf 2>/dev/null | base64 -w0"
if ($old -ne $confB64) {
    Invoke-Wsl -Quiet "echo $confB64 | base64 -d > /etc/wsl.conf" | Out-Null
    $changed = $true
}
if ($changed) {
    Warn "WSL 을 다시 시작해 설정을 적용합니다 (다른 WSL 배포판도 잠깐 멈춥니다)."
    & wsl.exe --shutdown
    Start-Sleep -Seconds 3
}
$state = Invoke-Wsl -Quiet "systemctl is-system-running --wait 2>/dev/null || true"
if ($state -notmatch "running|degraded") { Die "WSL 안의 systemd 가 뜨지 않았습니다 (상태: $state). 'wsl --update' 뒤 다시 실행하세요." }
Ok "systemd $state"

# ───────────────────────────────────────────────
Step "5/8 소스 복사"
$env:IL_SRC = $RepoRoot
$env:WSLENV = "IL_SRC/p"
$rc = Invoke-Wsl @'
set -e
rm -rf /opt/ironlab-src.new && mkdir -p /opt/ironlab-src.new
tar -C "$IL_SRC" --exclude=./.git --exclude=./.venv --exclude='*.egg-info' --exclude=__pycache__ -cf - . | tar -C /opt/ironlab-src.new -xf -
# Windows 에서 받은 저장소는 줄 끝이 CRLF 일 수 있다 (git 의 autocrlf). 리눅스에서 도는 파일은 LF 로
find /opt/ironlab-src.new -type f \( -name '*.sh' -o -name '*.py' -o -name '*.toml' -o -name '*.service' -o -name '*.json' \
     -o -name '*.txt' -o -name '*.js' -o -name '*.html' -o -name '*.css' -o -name Dockerfile -o -name sshd_config \) \
     -exec sed -i 's/\r$//' {} +
rm -rf /opt/ironlab-src && mv /opt/ironlab-src.new /opt/ironlab-src
echo "  $(find /opt/ironlab-src -type f | wc -l) files"
'@
if ($rc -ne 0) { Die "소스를 WSL 로 복사하지 못했습니다 ($RepoRoot)." }
Ok "$RepoRoot → /opt/ironlab-src"

# ───────────────────────────────────────────────
if ($Access -eq "tailscale") {
    Step "5+/8 Tailscale (WSL 안)"
    $rc = Invoke-Wsl @'
set -e
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled >/dev/null 2>&1
if ! tailscale ip -4 >/dev/null 2>&1; then
    echo "  아래에 나오는 주소를 브라우저에서 열어 이 WSL 을 tailnet 에 넣으세요."
    tailscale up --hostname="$(hostname)-ironlab"
fi
echo "  $(tailscale ip -4 | head -1)"
'@
    if ($rc -ne 0) { Die "Tailscale 을 준비하지 못했습니다." }
    Ok "tailnet 에 들어감"
}

# ───────────────────────────────────────────────
Step "6/8 ironlab 설치 (WSL 안에서 deploy/install.sh)"
$vars = [ordered]@{
    IL_ADMIN = $Admin; IL_SITE = $SiteName; IL_ACCESS = $Access; IL_TS = $TsLogin; IL_PHOST = $PublicHost
    IL_PURL = $PublicUrl; IL_PORT = "$Port"; IL_GPU = "$GpuIndex"; IL_TRELLIS = $(if ($WithTrellis) { "1" } else { "0" })
}
foreach ($k in $vars.Keys) { Set-Item -Path "env:$k" -Value $vars[$k] }
$env:WSLENV = (@("IL_SRC/p") + @($vars.Keys)) -join ":"
$rc = Invoke-Wsl @'
cd /opt/ironlab-src || exit 1
export ADMIN="$IL_ADMIN" ACCESS="$IL_ACCESS" TS_LOGIN="$IL_TS" PUBLIC_HOST="$IL_PHOST" PUBLIC_URL="$IL_PURL" \
       PORT="$IL_PORT" GPU_INDEX="$IL_GPU" WITH_TRELLIS="$IL_TRELLIS" GPU_VENDOR=nvidia
[ -n "$IL_SITE" ] && export SITE_NAME="$IL_SITE"
exec bash deploy/install.sh
'@
if ($rc -ne 0) { Die "WSL 안의 설치가 실패했습니다 (위 메시지 참고). 고친 뒤 같은 명령을 다시 실행하면 이어서 합니다." }

$info = Invoke-Wsl -Quiet "IRONLAB_CONFIG=/etc/ironlab/config.toml /opt/ironlab/venv/bin/python -c 'from ironlab.config import load_settings as l; s = l(); print(s.port, s.host, s.console_url or chr(45))'"
$cfgPort, $cfgHost, $console = ($info -split "\r?\n" | Select-Object -Last 1) -split " "
$lanOpen = $cfgHost -eq "0.0.0.0"

# ───────────────────────────────────────────────
Step "7/8 방화벽"
foreach ($n in @("ironlab-console", "ironlab-ssh")) {
    Get-NetFirewallRule -Name $n -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Get-Command Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        Get-NetFirewallHyperVRule -Name $n -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
    }
}
if ($lanOpen) {
    $rules = @(@{ Name = "ironlab-console"; Ports = "$cfgPort"; Label = "ironlab 콘솔" },
               @{ Name = "ironlab-ssh"; Ports = "22001-22999"; Label = "ironlab 워크스페이스 SSH" })
    foreach ($r in $rules) {
        New-NetFirewallRule -Name $r.Name -DisplayName $r.Label -Direction Inbound -Protocol TCP -LocalPort $r.Ports `
            -Action Allow -Profile Domain, Private | Out-Null
        # mirrored 네트워크의 WSL 로 들어오는 연결은 Hyper-V 방화벽도 지난다
        if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
            New-NetFirewallHyperVRule -Name $r.Name -DisplayName $r.Label -Direction Inbound -VMCreatorId $WslCreator `
                -Protocol TCP -LocalPorts $r.Ports -Action Allow | Out-Null
        }
    }
    Ok "TCP $cfgPort, 22001-22999 열림 (도메인·개인 네트워크)"
    $public = @(Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" })
    if ($public.Count -gt 0) {
        Warn "'$($public[0].Name)' 네트워크가 '공용'이라 다른 PC 에서 닿지 않습니다. 믿을 수 있는 네트워크라면:
      Set-NetConnectionProfile -InterfaceIndex $($public[0].InterfaceIndex) -NetworkCategory Private"
    }
} else {
    Ok "열 필요 없음 (콘솔이 127.0.0.1 또는 tailnet 에만 열림)"
}

# ───────────────────────────────────────────────
Step "8/8 부팅 때 자동으로"
# WSL 은 쓰는 프로그램이 없으면 VM 을 끈다. 부팅·로그인 때 배포판 안에서 sleep 하나를 띄워 ironlab 이 계속 돌게 한다
$wslExe = Join-Path $env:SystemRoot "System32\wsl.exe"
$action = New-ScheduledTaskAction -Execute $wslExe -Argument "-d $Distro -u root -e sleep infinity"
$triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"))
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Ok "예약 작업 '$TaskName' (부팅·로그인 때 WSL 시작)"
if ($DisableSleep) {
    & powercfg.exe /change standby-timeout-ac 0
    & powercfg.exe /change hibernate-timeout-ac 0
    Ok "전원 연결 시 절전·최대 절전 끔"
} else {
    Warn "PC 가 절전에 들어가면 ironlab 도 멈춥니다. 서버로 쓸 PC 라면 -DisableSleep 을 붙여 다시 실행하거나 전원 설정에서 절전을 끄세요."
}

if ($console -eq "-") { $console = "http://127.0.0.1:$cfgPort (프록시를 붙인 뒤 그 주소)" }
Write-Host ""
Write-Host "────────────────────────────────────────────"
Write-Host " Windows 설치 완료"
Write-Host "   콘솔      $console"
Write-Host "   WSL       $Distro ($InstallDir), 네트워크 $netMode"
Write-Host "   업데이트  git pull (또는 새 ZIP) 뒤 같은 명령"
Write-Host "   셸        wsl -d $Distro"
Write-Host "   로그      wsl -d $Distro -e journalctl -u ironlab -f"
Write-Host "   지우기    deploy\windows\uninstall.ps1"
Write-Host "────────────────────────────────────────────"
