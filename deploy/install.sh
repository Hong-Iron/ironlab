#!/usr/bin/env bash
# ironlab 설치 스크립트 (Ubuntu / Debian / Arch Linux)
#
#   sudo ADMIN=admin bash deploy/install.sh
#
# 미리 되어 있어야 하는 것: NVIDIA 드라이버 (nvidia-smi 가 GPU 를 보여야 한다)
#
# 환경변수 (모두 선택)
#   ADMIN=이름          처음 만들 관리자 (소문자로 시작, 영문·숫자·-·_). 접근 토큰이 화면에 한 번 나온다
#   ACCESS=…            콘솔을 어떻게 열지
#       auto       (기본) Tailscale 에 로그인돼 있으면 tailscale, 아니면 lan
#       tailscale  tailnet 안에서만 HTTPS 로 연다 (tailscale serve). Tailscale 계정으로 자동 로그인
#       lan        같은 네트워크에 http://<서버 IP>:8700 으로 바로 연다. 접근 토큰으로 로그인
#       local      127.0.0.1 에만 연다. nginx·Caddy 같은 리버스 프록시를 직접 붙일 때
#   TS_LOGIN=name@github  (tailscale) 관리자의 Tailscale 로그인. 토큰 없이 들어온다
#   SITE_NAME="…"       화면에 보일 서버 이름 (예: "비전연구실 GPU")
#   PUBLIC_HOST=…       사용자에게 안내할 서버 주소 (lan/local 기본: 서버 IP). 도메인이 있으면 그 이름
#   PUBLIC_URL=…        사용자에게 안내할 콘솔 주소 (local 에서 프록시 주소, 예: https://gpu.example.ac.kr)
#   PORT=8700           콘솔(API) 포트
#   GPU_INDEX=0         GPU 가 여러 장이면 ironlab 이 맡을 GPU 번호 (nvidia-smi 순서)
#   BIG=/data           큰 디스크의 마운트 위치. Docker·containerd·/srv 를 그 위에 둔다 (기본: 옮기지 않음)
#   WITH_TRELLIS=1      TRELLIS.2/Pixal3D 이미지까지 빌드 (1시간 이상 걸림)
#   SKIP_PACKAGES=1     패키지 설치를 건너뜀 (Docker·NVIDIA Container Toolkit 을 직접 깔았거나 다른 배포판일 때)
#
# SITE_NAME·PUBLIC_*·PORT·GPU_INDEX·ACCESS 는 설정 파일을 처음 만들 때만 쓴다. 다시 실행해도 기존
# /etc/ironlab/config.toml 은 그대로 둔다 (워크스페이스 이미지 이름만 맞춘다). 여러 번 실행해도 안전하다.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/ironlab
CONF=/etc/ironlab/config.toml
ACCESS="${ACCESS:-auto}"
PORT="${PORT:-8700}"
GPU_INDEX="${GPU_INDEX:-0}"
BIG="${BIG:-}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m오류: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[[ $EUID -eq 0 ]] || die "sudo 로 실행하세요: sudo bash $0"
[[ -f "$SRC_DIR/pyproject.toml" ]] || die "ironlab 소스 폴더에서 실행하세요 ($SRC_DIR)"
case "$ACCESS" in auto|tailscale|lan|local) ;; *) die "ACCESS 는 auto, tailscale, lan, local 중 하나입니다 (받은 값: $ACCESS)" ;; esac
[[ "$PORT" =~ ^[0-9]+$ ]] || die "PORT 는 숫자여야 합니다 (받은 값: $PORT)"
[[ "$GPU_INDEX" =~ ^[0-9]+$ ]] || die "GPU_INDEX 는 0 이상의 숫자여야 합니다 (받은 값: $GPU_INDEX)"
if [[ -n "${ADMIN:-}" && ! "$ADMIN" =~ ^[a-z][a-z0-9_-]{1,31}$ ]]; then
    die "ADMIN 이름은 소문자로 시작하고 영문 소문자·숫자·-·_ 로 2~32자여야 합니다 (받은 값: $ADMIN)"
fi

# ───────────────────────────────────────────────
step "1/10 GPU 드라이버 확인"
have nvidia-smi || die "nvidia-smi 가 없습니다. NVIDIA 드라이버를 먼저 설치하세요.
    Ubuntu:  sudo ubuntu-drivers install   (그 뒤 재부팅)
    Debian:  https://wiki.debian.org/NvidiaGraphicsDrivers
    Arch:    sudo pacman -S nvidia-open   (또는 nvidia, 카드에 맞게. 그 뒤 재부팅)"
nvidia-smi -L >/dev/null 2>&1 || die "nvidia-smi 가 GPU 를 읽지 못했습니다. 드라이버를 설치한 뒤 재부팅했는지 확인하세요."
GPU_COUNT=$(nvidia-smi -L | grep -c '^GPU ')
(( GPU_INDEX < GPU_COUNT )) || die "GPU_INDEX=$GPU_INDEX 인데 GPU 는 ${GPU_COUNT}장입니다 (0 ~ $((GPU_COUNT - 1)))"
GPU_LINE=$(nvidia-smi -L | sed -n "$((GPU_INDEX + 1))p")
ok "$GPU_LINE"
(( GPU_COUNT > 1 )) && warn "GPU 가 ${GPU_COUNT}장입니다. ironlab 은 GPU $GPU_INDEX 한 장만 맡고, 컨테이너에도 그 한 장만 보입니다."

# ───────────────────────────────────────────────
step "2/10 패키지 설치"
. /etc/os-release
OS_ID="${ID:-}"; OS_LIKE="${ID_LIKE:-}"
UV="$(command -v uv || true)"

install_uv() {
    [[ -n "$UV" ]] && return
    if [[ -x "$PREFIX/bin/uv" ]]; then UV="$PREFIX/bin/uv"; return; fi
    echo "  uv(파이썬 설치 도구)를 $PREFIX/bin 에 받는 중..."
    mkdir -p "$PREFIX/bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$PREFIX/bin" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
    UV="$PREFIX/bin/uv"
    [[ -x "$UV" ]] || die "uv 를 받지 못했습니다. https://docs.astral.sh/uv/ 를 보고 직접 설치한 뒤 다시 실행하세요."
}

apt_repo_docker() {
    # Docker 공식 저장소 (Ubuntu 기본 docker.io 는 buildx 가 없는 판이 있다)
    local dist codename
    if [[ "$OS_ID" == ubuntu || " $OS_LIKE " == *" ubuntu "* ]]; then
        dist=ubuntu; codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    else
        dist=debian; codename="${VERSION_CODENAME:-}"
    fi
    [[ -n "$codename" ]] || die "배포판 코드명을 알 수 없어 Docker 저장소를 추가하지 못했습니다. Docker 를 직접 설치한 뒤 다시 실행하세요."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$dist/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$dist $codename stable" \
        > /etc/apt/sources.list.d/docker.list
}

apt_repo_nvidia() {
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
}

if [[ "${SKIP_PACKAGES:-0}" == 1 ]]; then
    warn "패키지 설치를 건너뜀 (SKIP_PACKAGES=1)"
elif have pacman; then
    pacman -S --needed --noconfirm docker docker-buildx nvidia-container-toolkit python rsync curl uv
    UV="$(command -v uv)"
elif have apt-get; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg rsync python3 >/dev/null
    NEED_UPDATE=0
    if ! have docker; then apt_repo_docker; NEED_UPDATE=1; fi
    if ! have nvidia-ctk; then apt_repo_nvidia; NEED_UPDATE=1; fi
    (( NEED_UPDATE )) && apt-get update -qq
    have docker || apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin >/dev/null
    have nvidia-ctk || apt-get install -y -qq nvidia-container-toolkit >/dev/null
    if ! docker buildx version >/dev/null 2>&1; then
        apt-get install -y -qq docker-buildx-plugin >/dev/null 2>&1 || apt-get install -y -qq docker-buildx >/dev/null 2>&1 \
            || warn "docker buildx 를 설치하지 못했습니다. 이미지 빌드가 예전 방식으로 돕니다 (동작은 같음)."
    fi
else
    die "이 배포판($OS_ID)은 패키지 설치를 자동으로 하지 못합니다. 아래를 직접 설치한 뒤 SKIP_PACKAGES=1 로 다시 실행하세요.
    Docker (buildx 포함), NVIDIA Container Toolkit (nvidia-ctk), curl, rsync, python3
    https://docs.docker.com/engine/install/
    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
fi
for c in docker nvidia-ctk curl rsync python3; do have "$c" || die "$c 가 없습니다. 설치한 뒤 다시 실행하세요."; done
install_uv
ok "Docker $(docker --version | awk '{print $3}' | tr -d ,), nvidia-ctk, uv"

# ───────────────────────────────────────────────
step "3/10 저장 위치"
# 루트가 작아도 이미지·사용자 파일이 큰 디스크에 쌓이도록 bind mount 한다 (BIG 을 줬을 때만).
bind_dir() {
    local src="$1" dst="$2"
    mkdir -p "$src" "$dst"
    if findmnt -rn "$dst" >/dev/null; then
        ok "$dst 는 이미 연결됨 ($(findmnt -rno SOURCE "$dst"))"
        return
    fi
    if [[ -n "$(ls -A "$dst" 2>/dev/null)" ]]; then
        echo "  $dst 의 기존 내용을 $src 로 옮기는 중..."
        rsync -aHAX "$dst"/ "$src"/
        find "$dst" -mindepth 1 -delete
    fi
    grep -qsE "^[^#]*[[:space:]]${dst}[[:space:]]" /etc/fstab || echo "$src  $dst  none  bind  0 0" >> /etc/fstab
    systemctl daemon-reload
    mount "$dst"
    ok "$dst → $src"
}
if [[ -n "$BIG" ]]; then
    [[ -d "$BIG" ]] || die "BIG=$BIG 폴더가 없습니다"
    # Docker 를 멈추면 실행 중인 워크스페이스·작업·LLM 이 모두 죽는다. 아직 연결하지 않은 위치가 있을 때만 멈춘다.
    NEED_BIND=0
    for d in /var/lib/docker /var/lib/containerd /srv; do findmnt -rn "$d" >/dev/null || NEED_BIND=1; done
    if (( NEED_BIND )); then
        systemctl stop docker.socket docker containerd 2>/dev/null || true
    fi
    bind_dir "$BIG/docker" /var/lib/docker
    bind_dir "$BIG/containerd" /var/lib/containerd
    bind_dir "$BIG/srv" /srv
else
    FREE=$(df -h --output=avail "$( [[ -d /srv ]] && echo /srv || echo / )" 2>/dev/null | tail -1 | tr -d ' ' || true)
    ok "그대로 씀: /srv, /var/lib/docker (${FREE:-?} 남음). 큰 디스크가 따로 있으면 BIG=<마운트 위치>"
fi

# ───────────────────────────────────────────────
step "4/10 Docker + NVIDIA 런타임"
mkdir -p /etc/docker
# 설정 내용이 실제로 바뀌었는지 보려고, 형식 차이를 없앤 지문을 떠 둔다
daemon_fp() { python3 -c 'import json,sys; print(json.dumps(json.load(open("/etc/docker/daemon.json")), sort_keys=True))' 2>/dev/null || echo none; }
DAEMON_BEFORE=$(daemon_fp)
if [[ -f /etc/docker/daemon.json && -n "$BIG" ]]; then
    # 예전에 넣은 data-root 는 bind mount 로 대체했으므로 지운다
    python3 - <<'EOF'
import json
p = "/etc/docker/daemon.json"
d = json.load(open(p))
if d.pop("data-root", None):
    print("  daemon.json 에서 data-root 제거")
json.dump(d, open(p, "w"), indent=4)
EOF
fi
nvidia-ctk runtime configure --runtime=docker >/dev/null
mkdir -p /etc/cdi
nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml >/dev/null 2>&1 || warn "CDI 정의 생성 실패 (계속 진행)"
systemctl cat containerd.service >/dev/null 2>&1 && systemctl enable --now containerd
systemctl enable --now docker
# 다시 시작하면 실행 중인 컨테이너가 모두 죽으므로, Docker 설정이 바뀌었을 때만 한다
if [[ "$DAEMON_BEFORE" != "$(daemon_fp)" ]]; then
    RUNNING=$(docker ps -q 2>/dev/null | wc -l || true)
    (( RUNNING > 0 )) && warn "Docker 설정이 바뀌어 다시 시작합니다. 실행 중인 컨테이너 ${RUNNING}개가 종료됩니다."
    systemctl restart docker
    sleep 2
else
    ok "Docker 설정 변화 없음 (다시 시작하지 않음, 실행 중인 컨테이너 유지)"
fi
if docker run --rm --gpus "device=$GPU_INDEX" nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L; then
    ok "컨테이너에서 GPU 확인"
else
    die "컨테이너에서 GPU가 보이지 않습니다. 'nvidia-smi' 와 'nvidia-ctk cdi list' 출력을 확인하세요."
fi

# ───────────────────────────────────────────────
step "5/10 접속 방식"
primary_ip() {   # 밖으로 나가는 길의 내 주소. ip(iproute2)가 없는 최소 설치면 hostname -I 의 첫 주소
    local a=""
    if have ip; then
        a=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}' || true)
    fi
    [[ -n "$a" ]] || a=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    echo "$a"
}
is_public_ip() {   # 인터넷에서 바로 닿는 주소인지 (사설·CGNAT·링크 로컬이 아니면 공인)
    python3 -c 'import ipaddress, sys; sys.exit(0 if ipaddress.ip_address(sys.argv[1]).is_global else 1)' "$1" 2>/dev/null
}
IP="$(primary_ip)"
if [[ "$ACCESS" == auto ]]; then
    if have tailscale && tailscale ip -4 >/dev/null 2>&1; then
        ACCESS=tailscale
    elif [[ -n "$IP" ]] && is_public_ip "$IP"; then
        die "이 서버의 주소 $IP 는 인터넷에서 바로 닿는 공인 IP 입니다. 콘솔을 암호화 없는 http 로 열면 위험하니 직접 고르세요.
    ACCESS=tailscale  Tailscale 로 tailnet 안에서만 (권장. 먼저 tailscale 설치와 'sudo tailscale up')
    ACCESS=local      127.0.0.1 에만 열고 HTTPS 리버스 프록시를 붙임 (README '접속 방식')
    ACCESS=lan        그래도 http 로 바로 엶 (학교·회사 방화벽 안이라 밖에서 닿지 않을 때만)"
    else
        ACCESS=lan
    fi
    ok "ACCESS=auto → $ACCESS"
fi
TS_IP=""; TS_NAME=""
case "$ACCESS" in
    tailscale)
        have tailscale || die "tailscale 이 없습니다. https://tailscale.com/download/linux 로 설치하고 'sudo tailscale up' 을 한 뒤 다시 실행하세요.
    Tailscale 없이 쓰려면 ACCESS=lan"
        systemctl enable --now tailscaled >/dev/null 2>&1 || true
        TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
        [[ -n "$TS_IP" ]] || die "Tailscale 에 로그인되어 있지 않습니다. 'sudo tailscale up' 을 먼저 실행하세요."
        TS_NAME="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
        CFG_HOST=127.0.0.1; CFG_TRUST=true; CFG_PUBLIC_HOST="${PUBLIC_HOST:-$TS_NAME}"; CFG_PUBLIC_URL="${PUBLIC_URL:-}"
        CFG_SSH_BIND="$TS_IP"
        ok "tailnet: https://$TS_NAME (IP $TS_IP)"
        ;;
    lan|local)
        CFG_PUBLIC_HOST="${PUBLIC_HOST:-${IP:-}}"
        [[ -n "$CFG_PUBLIC_HOST" ]] || warn "서버 IP 를 찾지 못했습니다. 설정의 public_host 를 직접 채우세요."
        CFG_TRUST=false; CFG_SSH_BIND=0.0.0.0
        if [[ "$ACCESS" == lan ]]; then
            CFG_HOST=0.0.0.0; CFG_PUBLIC_URL="${PUBLIC_URL:-http://$CFG_PUBLIC_HOST:$PORT}"
            ok "LAN: $CFG_PUBLIC_URL (접근 토큰으로 로그인)"
            [[ -n "$IP" ]] && is_public_ip "$IP" && warn "공인 IP($IP)에 http 로 엽니다. 밖에서 닿지 않는 망인지 꼭 확인하세요."
        else
            CFG_HOST=127.0.0.1; CFG_PUBLIC_URL="${PUBLIC_URL:-}"
            ok "local: 127.0.0.1:$PORT 에만 엶 (리버스 프록시를 직접 붙이세요)"
        fi
        ;;
esac

# ───────────────────────────────────────────────
step "6/10 디렉터리"
mkdir -p /srv/users /srv/shared/models/hub /srv/shared/models/torch /srv/shared/models/llm /srv/shared/datasets /srv/scratch /etc/ironlab
chmod 755 /srv /srv/shared /srv/shared/models /srv/shared/models/hub /srv/shared/models/llm /srv/shared/datasets
ok "/srv/users, /srv/shared (models/llm 포함), /srv/scratch"

# ───────────────────────────────────────────────
step "7/10 워크스페이스 이미지"
docker build -t ironlab/workspace:latest "$SRC_DIR/deploy/workspace"
docker run --rm --entrypoint bash ironlab/workspace:latest -lc "conda config --show channels | grep -q conda-forge" \
    || die "워크스페이스 이미지의 conda 설정이 잘못됐습니다"
ok "ironlab/workspace:latest"

# 자체 점검: 실제 워크스페이스와 같은 제한(권한 축소, 사용자 소유 700 홈)에서 sshd 가 뜨는지 확인한다
SELF_HOME=$(mktemp -d)
chown 20999:20999 "$SELF_HOME"; chmod 700 "$SELF_HOME"
SELF_CID=$(docker run -d -e IRONLAB_UID=20999 -v "$SELF_HOME":/home/dev \
    --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID --cap-add SYS_CHROOT \
    --cap-add AUDIT_WRITE --cap-add NET_BIND_SERVICE --cap-add FOWNER --cap-add KILL \
    --security-opt no-new-privileges --ulimit core=1:1 ironlab/workspace:latest 2>/dev/null || true)
sleep 3
if [[ -n "$SELF_CID" && "$(docker inspect -f '{{.State.Running}}' "$SELF_CID" 2>/dev/null)" == true ]]; then
    ok "워크스페이스 컨테이너 시작 점검 (sshd 실행 확인)"
else
    warn "워크스페이스 컨테이너가 시작 직후 종료됩니다. SSH 접속이 안 될 수 있습니다. 로그:"
    if [[ -n "$SELF_CID" ]]; then docker logs --tail 5 "$SELF_CID" 2>&1 | sed "s/^/      /" || true; fi
fi
if [[ -n "$SELF_CID" ]]; then docker rm -f "$SELF_CID" >/dev/null 2>&1 || true; fi
rmdir "$SELF_HOME" 2>/dev/null || true

echo "  llama.cpp 서버 이미지를 받는 중 (수 GB, 처음 한 번만)..."
if docker pull ghcr.io/ggml-org/llama.cpp:server-cuda; then
    ok "llama.cpp 서버 이미지 (로컬 LLM)"
else
    warn "llama.cpp 이미지를 받지 못했습니다 — 처음 모델을 올릴 때 다시 받습니다"
fi
# 다시 빌드하면서 이름을 잃은 예전 이미지 층을 지운다 (쓰이는 것은 지워지지 않는다)
docker image prune -f >/dev/null 2>&1 || true
IMAGE=ironlab/workspace:latest
if [[ "${WITH_TRELLIS:-0}" == 1 ]]; then
    step "7+/10 TRELLIS 이미지 (오래 걸림, 로그: /var/log/ironlab-trellis-build.log)"
    if ! docker build --progress=plain -t ironlab/workspace:trellis "$SRC_DIR/deploy/trellis" 2>&1 | tee /var/log/ironlab-trellis-build.log; then
        die "TRELLIS 이미지 빌드 실패: tail -60 /var/log/ironlab-trellis-build.log"
    fi
    IMAGE=ironlab/workspace:trellis
    ok "ironlab/workspace:trellis"
elif docker image inspect ironlab/workspace:trellis >/dev/null 2>&1; then
    IMAGE=ironlab/workspace:trellis
    ok "기존 TRELLIS 이미지 사용"
else
    warn "TRELLIS 이미지는 건너뜀 (나중에: sudo WITH_TRELLIS=1 bash deploy/install.sh)"
fi

# ───────────────────────────────────────────────
step "8/10 ironlab 설치"
mkdir -p "$PREFIX"
if [[ "$SRC_DIR" != "$PREFIX/src" ]]; then
    rsync -a --delete --exclude '__pycache__' --exclude '*.egg-info' --exclude '/build' --exclude '/.venv' --exclude '/.git' \
        "$SRC_DIR"/ "$PREFIX/src"/
fi
# 지난 설치가 남긴 빌드 폴더를 지운다. 남아 있으면 지운 파일이나 예전 파일이 새 패키지에 섞여 들어갈 수 있다
rm -rf "$PREFIX/src/build"
# 시스템 파이썬은 배포판마다 판이 달라(3.10 은 tomllib 이 없다) uv 로 Python 3.12 를 따로 받아 쓴다.
export UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_PYTHON_BIN_DIR="$PREFIX/python/bin" UV_CACHE_DIR="$PREFIX/.uv-cache"
"$UV" python install 3.12
rm -rf "$PREFIX/venv"
"$UV" venv --python 3.12 "$PREFIX/venv"
"$UV" pip install --python "$PREFIX/venv/bin/python" "$PREFIX/src" huggingface_hub
"$PREFIX/venv/bin/python" -c "import ironlab, fastapi, docker, pynvml; print('  python', __import__('sys').version.split()[0])"
ok "$PREFIX/venv/bin/ironlab-server"

# ───────────────────────────────────────────────
step "9/10 설정과 서비스"
if [[ ! -f "$CONF" ]]; then
    CFG_HOST="$CFG_HOST" CFG_PORT="$PORT" CFG_TRUST="$CFG_TRUST" CFG_PUBLIC_HOST="$CFG_PUBLIC_HOST" \
    CFG_PUBLIC_URL="$CFG_PUBLIC_URL" CFG_SSH_BIND="$CFG_SSH_BIND" CFG_IMAGE="$IMAGE" CFG_GPU="$GPU_INDEX" \
    CFG_SITE="${SITE_NAME:-ironlab}" \
    "$PREFIX/venv/bin/python" - "$PREFIX/src/deploy/config.example.toml" "$CONF" <<'EOF'
# 예시 설정의 'key = 값' 줄에서 값만 바꾼다 (줄 끝 설명은 남긴다). 문자열은 TOML 규칙대로 따옴표를 친다.
import json, os, re, sys
src, dst = sys.argv[1], sys.argv[2]
e = os.environ
values = {
    "host": json.dumps(e["CFG_HOST"]), "port": e["CFG_PORT"], "trust_tailscale_headers": e["CFG_TRUST"],
    "public_host": json.dumps(e["CFG_PUBLIC_HOST"], ensure_ascii=False),
    "public_url": json.dumps(e["CFG_PUBLIC_URL"], ensure_ascii=False),
    "site_name": json.dumps(e["CFG_SITE"], ensure_ascii=False),
    "ssh_bind_ip": json.dumps(e["CFG_SSH_BIND"]), "workspace_image": json.dumps(e["CFG_IMAGE"]),
    "gpu_index": e["CFG_GPU"],
}
out, seen = [], set()
for line in open(src, encoding="utf-8").read().splitlines():
    m = re.match(r"^(\w+)(\s*=\s*)(\"[^\"]*\"|[^#\s]+)(.*)$", line)
    if m and m.group(1) in values:
        out.append(f"{m.group(1)}{m.group(2)}{values[m.group(1)]}{m.group(4)}")
        seen.add(m.group(1))
    else:
        out.append(line)
missing = set(values) - seen
if missing:
    sys.exit(f"예시 설정에 {sorted(missing)} 줄이 없습니다")
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
EOF
    IRONLAB_CONFIG="$CONF" "$PREFIX/venv/bin/python" -c "from ironlab.config import load_settings; load_settings()" \
        || die "만든 설정 파일을 읽지 못했습니다: $CONF"
    ok "$CONF 생성"
else
    sed -i "s|^workspace_image = .*|workspace_image = \"$IMAGE\"|" "$CONF"
    ok "기존 $CONF 유지 (workspace_image = $IMAGE)"
fi
# 접속 방식·포트는 설정 파일이 정한다 (다시 실행할 때 환경변수와 달라도 설정을 따른다)
read -r CFG_PORT CFG_HOST CFG_TRUST CFG_URL < <(IRONLAB_CONFIG="$CONF" "$PREFIX/venv/bin/python" -c \
    "from ironlab.config import load_settings as l; s = l(); print(s.port, s.host, int(s.trust_tailscale_headers), s.console_url or '-')")

NEW_ADMIN=0
if [[ -n "${ADMIN:-}" && ! -f /var/lib/ironlab/ironlab.db ]]; then
    NEW_ADMIN=1
    TS_ARG=()
    [[ "$CFG_TRUST" == 1 && -n "${TS_LOGIN:-}" ]] && TS_ARG=(--tailscale-login "$TS_LOGIN")
    IRONLAB_CONFIG="$CONF" "$PREFIX/venv/bin/ironlab-server" create-admin "$ADMIN" "${TS_ARG[@]}"
elif [[ -z "${ADMIN:-}" && ! -f /var/lib/ironlab/ironlab.db ]]; then
    warn "ADMIN 이 없어 관리자 생성을 건너뜀 (나중에: sudo IRONLAB_CONFIG=$CONF $PREFIX/venv/bin/ironlab-server create-admin <이름>)"
fi

# 서비스 파일이 바뀌었을 때만 systemd 를 다시 읽힌다. daemon-reload 는 NVIDIA 런타임 구성에 따라
# 실행 중인 컨테이너의 GPU 접근을 끊을 수 있어(알려진 문제), 필요 없을 때는 하지 않는다.
if ! cmp -s "$PREFIX/src/deploy/ironlab.service" /etc/systemd/system/ironlab.service; then
    install -m 644 "$PREFIX/src/deploy/ironlab.service" /etc/systemd/system/ironlab.service
    systemctl daemon-reload
fi
systemctl enable ironlab >/dev/null
systemctl restart ironlab
for _ in $(seq 1 20); do
    curl -fs "localhost:$CFG_PORT/api/health" >/dev/null 2>&1 && break
    sleep 1
done
HEALTH="$(curl -fs "localhost:$CFG_PORT/api/health" || true)"
[[ -n "$HEALTH" ]] || die "ironlab 이 뜨지 않았습니다: journalctl -u ironlab -n 50"
echo "  $HEALTH"
[[ "$HEALTH" == *'"gpu":true'* ]] || warn "ironlab 이 GPU를 읽지 못했습니다 (journalctl -u ironlab 확인)"
ok "ironlab 서비스 실행 중"

# 생성기 실행 스크립트를 공용(/srv/shared/models/apps)에 올리고, 사용자 홈마다 링크와 생성기 폴더를 둔다.
# 생성기 환경(conda)이 공용에 아직 없으면 건너뛴다: 실행 스크립트만 깔면 화면은 켜지는데 작업이 실패한다.
# 모델·환경을 처음 공용으로 옮기는 일은 따로 한 번: sudo bash deploy/shared-apps.sh --migrate-from <사용자>
step "9+/10 생성기"
if [[ -n "$(ls -A /srv/shared/models/envs 2>/dev/null)" ]]; then
    if bash "$SRC_DIR/deploy/shared-apps.sh" > /var/log/ironlab-shared-apps.log 2>&1; then
        ok "실행 스크립트 공용에 올림, 사용자 홈 링크 (기록: /var/log/ironlab-shared-apps.log)"
    else
        warn "생성기 공용 배치가 실패했습니다: /var/log/ironlab-shared-apps.log"
    fi
else
    ok "건너뜀: 공용 생성기 환경이 아직 없음 (README 의 '생성기 켜기' 참고)"
fi

# ───────────────────────────────────────────────
step "10/10 공개"
FIREWALL_HINT=""
if [[ "$CFG_TRUST" == 1 ]]; then
    tailscale serve --bg "$CFG_PORT" >/dev/null 2>&1 || warn "tailscale serve 실패 — 관리 콘솔에서 HTTPS 인증서를 켰는지 확인하세요"
    CONSOLE="https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    ok "$CONSOLE (tailnet 안에서만)"
else
    CONSOLE="$CFG_URL"
    [[ "$CONSOLE" == - ]] && CONSOLE="http://127.0.0.1:$CFG_PORT (프록시를 붙인 뒤 그 주소)"
    if [[ "$CFG_HOST" != 127.0.0.1 ]]; then
        if have ufw && ufw status 2>/dev/null | grep -q 'Status: active'; then
            FIREWALL_HINT="sudo ufw allow $CFG_PORT/tcp && sudo ufw allow 22001:22999/tcp"
        elif have firewall-cmd && firewall-cmd --state >/dev/null 2>&1; then
            FIREWALL_HINT="sudo firewall-cmd --permanent --add-port=$CFG_PORT/tcp --add-port=22001-22999/tcp && sudo firewall-cmd --reload"
        fi
        [[ -n "$FIREWALL_HINT" ]] && warn "방화벽이 켜져 있습니다. 다른 PC 에서 열려면: $FIREWALL_HINT"
    fi
    ok "$CONSOLE"
fi

cat <<EOF

────────────────────────────────────────────
 설치 완료
   콘솔     $CONSOLE
   GPU      ${GPU_LINE#GPU *: }
   SSH 포트 22001~ (사용자별)
   이미지   $IMAGE
   설정     $CONF  (고친 뒤: sudo systemctl restart ironlab)
   로그     journalctl -u ironlab -f
   데이터   /srv${BIG:+ (→ $BIG/srv)}, /var/lib/ironlab

EOF
if (( NEW_ADMIN )); then
    cat <<EOF
 다음 할 일
   1. 브라우저에서 콘솔을 열고 위에 나온 관리자 토큰으로 로그인 (토큰은 다시 볼 수 없습니다)
   2. 사용자 추가: 콘솔 > 사용자 > 추가 (토큰을 그 사람에게 전달)
   3. 각자 SSH 키 등록 → 워크스페이스 시작
   4. 로컬 LLM·생성기는 README 를 보고 필요한 것만
EOF
fi
echo "────────────────────────────────────────────"
