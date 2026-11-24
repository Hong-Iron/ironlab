#!/usr/bin/env bash
# ironlab 설치 스크립트 (Arch Linux)
#
#   sudo ADMIN=cheolmin TS_LOGIN=you@github bash deploy/install-arch.sh
#
# 환경변수
#   ADMIN          처음 만들 관리자 이름 (없으면 관리자 생성을 건너뜀)
#   TS_LOGIN       관리자의 Tailscale 로그인 (예: name@github) — 토큰 없이 로그인
#   WITH_TRELLIS=1 TRELLIS.2/Pixal3D 이미지까지 빌드 (1시간 이상 걸림)
#   BIG=/home      큰 파티션의 마운트 위치 (Docker, containerd, /srv 를 이 위에 둔다)
#
# 여러 번 실행해도 안전하다 (이미 된 단계는 건너뜀).
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIG="${BIG:-/home}"
PREFIX=/opt/ironlab
CONF=/etc/ironlab/config.toml

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m오류: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "sudo 로 실행하세요: sudo bash $0"
[[ -f "$SRC_DIR/pyproject.toml" ]] || die "ironlab 소스 폴더에서 실행하세요 ($SRC_DIR)"

# ───────────────────────────────────────────────
step "1/9 패키지 설치"
pacman -S --needed --noconfirm docker docker-buildx nvidia-container-toolkit python rsync tailscale curl uv
ok "패키지 준비됨"

# ───────────────────────────────────────────────
step "2/9 큰 파티션($BIG)에 저장 위치 연결"
# 루트가 작아도 이미지·사용자 파일이 큰 파티션에 쌓이도록 bind mount 한다.
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
    grep -qsE "^[^#]*[[:space:]]$dst[[:space:]]" /etc/fstab || echo "$src  $dst  none  bind  0 0" >> /etc/fstab
    systemctl daemon-reload
    mount "$dst"
    ok "$dst → $src"
}
# Docker 를 멈추면 실행 중인 워크스페이스·작업·LLM 이 모두 죽는다. 아직 연결하지 않은 위치가 있을 때만 멈춘다.
NEED_BIND=0
for d in /var/lib/docker /var/lib/containerd /srv; do findmnt -rn "$d" >/dev/null || NEED_BIND=1; done
if (( NEED_BIND )); then
    systemctl stop docker.socket docker containerd 2>/dev/null || true
fi
bind_dir "$BIG/docker" /var/lib/docker
bind_dir "$BIG/containerd" /var/lib/containerd
bind_dir "$BIG/srv" /srv

# ───────────────────────────────────────────────
step "3/9 Docker + NVIDIA 런타임"
mkdir -p /etc/docker
# 설정 내용이 실제로 바뀌었는지 보려고, 형식 차이를 없앤 지문을 떠 둔다
daemon_fp() { python3 -c 'import json,sys; print(json.dumps(json.load(open("/etc/docker/daemon.json")), sort_keys=True))' 2>/dev/null || echo none; }
DAEMON_BEFORE=$(daemon_fp)
if [[ -f /etc/docker/daemon.json ]]; then
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
systemctl enable --now containerd docker
# 다시 시작하면 실행 중인 컨테이너가 모두 죽으므로, Docker 설정이 바뀌었을 때만 한다
if [[ "$DAEMON_BEFORE" != "$(daemon_fp)" ]]; then
    RUNNING=$(docker ps -q 2>/dev/null | wc -l)
    (( RUNNING > 0 )) && warn "Docker 설정이 바뀌어 다시 시작합니다. 실행 중인 컨테이너 ${RUNNING}개가 종료됩니다."
    systemctl restart docker
    sleep 2
else
    ok "Docker 설정 변화 없음 (다시 시작하지 않음, 실행 중인 컨테이너 유지)"
fi
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L; then
    ok "컨테이너에서 GPU 확인"
else
    die "컨테이너에서 GPU가 보이지 않습니다. 'nvidia-smi' 와 'nvidia-ctk cdi list' 출력을 확인하세요."
fi

# ───────────────────────────────────────────────
step "4/9 Tailscale"
systemctl enable --now tailscaled
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
[[ -n "$TS_IP" ]] || die "Tailscale 에 로그인되어 있지 않습니다. 'sudo tailscale up' 을 먼저 실행하세요."
TS_NAME="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
ok "IP $TS_IP, 주소 $TS_NAME"

# ───────────────────────────────────────────────
step "5/9 디렉터리"
mkdir -p /srv/users /srv/shared/models/hub /srv/shared/models/torch /srv/shared/models/llm /srv/shared/datasets /srv/scratch /etc/ironlab
chmod 755 /srv /srv/shared /srv/shared/models /srv/shared/models/hub /srv/shared/models/llm /srv/shared/datasets
ok "/srv/users, /srv/shared (models/llm 포함), /srv/scratch"

# ───────────────────────────────────────────────
step "6/9 워크스페이스 이미지"
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
    step "6+/9 TRELLIS 이미지 (오래 걸림, 로그: /var/log/ironlab-trellis-build.log)"
    if ! docker build --progress=plain -t ironlab/workspace:trellis "$SRC_DIR/deploy/trellis" 2>&1 | tee /var/log/ironlab-trellis-build.log; then
        die "TRELLIS 이미지 빌드 실패: tail -60 /var/log/ironlab-trellis-build.log"
    fi
    IMAGE=ironlab/workspace:trellis
    ok "ironlab/workspace:trellis"
elif docker image inspect ironlab/workspace:trellis >/dev/null 2>&1; then
    IMAGE=ironlab/workspace:trellis
    ok "기존 TRELLIS 이미지 사용"
else
    warn "TRELLIS 이미지는 건너뜀 (나중에: sudo WITH_TRELLIS=1 bash deploy/install-arch.sh)"
fi

# ───────────────────────────────────────────────
step "7/9 ironlab 설치"
mkdir -p "$PREFIX"
if [[ "$SRC_DIR" != "$PREFIX/src" ]]; then
    rsync -a --delete --exclude '__pycache__' --exclude '*.egg-info' --exclude '/build' "$SRC_DIR"/ "$PREFIX/src"/
fi
# 지난 설치가 남긴 빌드 폴더를 지운다. 남아 있으면 지운 파일이나 예전 파일이 새 패키지에 섞여 들어갈 수 있다
rm -rf "$PREFIX/src/build"
# Arch 의 최신 시스템 파이썬/pip 는 깨질 때가 있어(pip segfault 등) uv 로 Python 3.12 를 따로 받아 쓴다.
export UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_CACHE_DIR="$PREFIX/.uv-cache"
uv python install 3.12
rm -rf "$PREFIX/venv"
uv venv --python 3.12 "$PREFIX/venv"
uv pip install --python "$PREFIX/venv/bin/python" "$PREFIX/src" huggingface_hub
"$PREFIX/venv/bin/python" -c "import ironlab, fastapi, docker, pynvml; print('  python', __import__('sys').version.split()[0])"
ok "$PREFIX/venv/bin/ironlab-server"

# ───────────────────────────────────────────────
step "8/9 설정과 서비스"
if [[ ! -f "$CONF" ]]; then
    sed -e "s|^public_host = .*|public_host = \"$TS_NAME\"|" \
        -e "s|^ssh_bind_ip = .*|ssh_bind_ip = \"$TS_IP\"|" \
        -e "s|^workspace_image = .*|workspace_image = \"$IMAGE\"|" \
        "$PREFIX/src/deploy/config.example.toml" > "$CONF"
    ok "$CONF 생성"
else
    sed -i "s|^workspace_image = .*|workspace_image = \"$IMAGE\"|" "$CONF"
    ok "기존 $CONF 유지 (workspace_image = $IMAGE)"
fi

if [[ -n "${ADMIN:-}" && ! -f /var/lib/ironlab/ironlab.db ]]; then
    IRONLAB_CONFIG="$CONF" "$PREFIX/venv/bin/ironlab-server" create-admin "$ADMIN" ${TS_LOGIN:+--tailscale-login "$TS_LOGIN"}
elif [[ -z "${ADMIN:-}" ]]; then
    warn "ADMIN 이 없어 관리자 생성을 건너뜀"
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
    curl -fs localhost:8700/api/health >/dev/null 2>&1 && break
    sleep 1
done
HEALTH="$(curl -fs localhost:8700/api/health || true)"
[[ -n "$HEALTH" ]] || die "ironlab 이 뜨지 않았습니다: journalctl -u ironlab -n 50"
echo "  $HEALTH"
[[ "$HEALTH" == *'"gpu":true'* ]] || warn "ironlab 이 GPU를 읽지 못했습니다 (journalctl -u ironlab 확인)"
ok "ironlab 서비스 실행 중"

# 생성기 실행 스크립트를 공용(/srv/shared/models/apps)에 올리고, 사용자 홈마다 링크와 생성기 폴더를 둔다.
# 모델·환경을 처음 공용으로 옮기는 일은 따로 한 번: sudo bash deploy/shared-apps.sh --migrate-from <사용자>
step "8+/9 생성기를 모든 사용자에게"
if bash "$SRC_DIR/deploy/shared-apps.sh" > /var/log/ironlab-shared-apps.log 2>&1; then
    ok "실행 스크립트 공용에 올림, 사용자 홈 링크 (기록: /var/log/ironlab-shared-apps.log)"
else
    warn "생성기 공용 배치가 실패했습니다: /var/log/ironlab-shared-apps.log"
fi

# ───────────────────────────────────────────────
step "9/9 tailnet 에 공개"
tailscale serve --bg 8700 >/dev/null 2>&1 || warn "tailscale serve 실패 — 관리 콘솔에서 HTTPS 인증서를 켰는지 확인하세요"
ok "https://$TS_NAME"

cat <<EOF

────────────────────────────────────────────
 설치 완료
   콘솔     https://$TS_NAME
   SSH 포트 22001~ (사용자별), $TS_IP 에서만 열림
   이미지   $IMAGE
   로그     journalctl -u ironlab -f
   데이터   /srv (→ $BIG/srv), /var/lib/ironlab

 다음 할 일
   1. 브라우저에서 콘솔을 열고 SSH 키 등록 → 워크스페이스 시작
   2. 공용 모델 받기:
        sudo $PREFIX/venv/bin/hf auth login
        sudo HF_HUB_CACHE=/srv/shared/models/hub $PREFIX/venv/bin/hf download microsoft/TRELLIS.2-4B
        sudo chmod -R a+rX /srv/shared
   3. 로컬 LLM (Qwen3.8-27B, 4bit 약 17GB):
        sudo HF_HUB_CACHE=/tmp/hf $PREFIX/venv/bin/hf download unsloth/Qwen3.8-27B-GGUF \\
             --include "*UD-Q4_K_XL*" --local-dir /srv/shared/models/llm/qwen3.8-27b
        sudo chmod -R a+rX /srv/shared/models/llm
      → 콘솔 > 로컬 LLM > 모델 등록 → 모델 올리기
   ※ 이미 켜 둔 워크스페이스는 한 번 중지했다 켜야 LLM 주소(OPENAI_BASE_URL)가 들어갑니다
────────────────────────────────────────────
EOF
