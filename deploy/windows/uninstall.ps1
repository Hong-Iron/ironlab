#Requires -RunAsAdministrator
<#
.SYNOPSIS
    install.ps1 이 만든 것을 지운다: 예약 작업, 방화벽 규칙, 그리고 (확인 뒤) ironlab WSL 배포판.

.DESCRIPTION
    배포판을 지우면 그 안의 모든 것(사용자 홈, 작업 기록, 받은 모델, Docker 이미지)이 되돌릴 수 없이 사라진다.
    그래서 배포판 이름을 직접 입력해야 지운다. -KeepDistro 를 주면 배포판은 남기고 자동 시작·방화벽만 지운다.
    %UserProfile%\.wslconfig 은 다른 배포판도 쓰므로 건드리지 않는다.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1
#>
[CmdletBinding()]
param(
    [string]$Distro = "ironlab",
    [switch]$KeepDistro
)
$ErrorActionPreference = "Stop"
$env:WSL_UTF8 = "1"

$task = Get-ScheduledTask -TaskName "ironlab WSL" -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName "ironlab WSL" -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "ironlab WSL" -Confirm:$false
    Write-Host "  v 예약 작업 'ironlab WSL' 지움" -ForegroundColor Green
}
foreach ($n in @("ironlab-console", "ironlab-ssh")) {
    Get-NetFirewallRule -Name $n -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if (Get-Command Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        Get-NetFirewallHyperVRule -Name $n -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
    }
}
Write-Host "  v 방화벽 규칙 지움" -ForegroundColor Green

if ($KeepDistro) {
    Write-Host "  배포판 '$Distro' 는 남겼습니다. 안의 ironlab 은 'wsl -d $Distro' 로 들어가 그대로 쓸 수 있습니다."
    exit 0
}
$eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$names = ((& wsl.exe -l -q 2>$null | Out-String) -replace "`0", "") -split "\r?\n" | ForEach-Object { $_.Trim() }
$ErrorActionPreference = $eap
if ($names -notcontains $Distro) { Write-Host "  배포판 '$Distro' 는 없습니다."; exit 0 }

Write-Host ""
Write-Host "배포판 '$Distro' 를 지우면 사용자 홈, 작업 기록, 받은 모델, Docker 이미지가 모두 사라지고 되돌릴 수 없습니다." -ForegroundColor Yellow
$answer = Read-Host "지우려면 배포판 이름($Distro)을 그대로 입력하세요"
if ($answer -cne $Distro) { Write-Host "  지우지 않았습니다."; exit 0 }
& wsl.exe --unregister $Distro
if ($LASTEXITCODE -ne 0) { Write-Host "오류: wsl --unregister 가 실패했습니다." -ForegroundColor Red; exit 1 }
Write-Host "  v 배포판 '$Distro' 지움" -ForegroundColor Green
