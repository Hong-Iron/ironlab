#!/bin/bash
# 워크스페이스 컨테이너 진입점: 호스트와 같은 UID의 'dev' 사용자를 만들고 sshd를 띄운다.
#
# 주의: 이 컨테이너의 root 는 CAP_DAC_OVERRIDE 가 없다 (ironlab 이 cap_drop=ALL 로 띄운다).
# 사용자 홈(/home/dev)은 사용자 소유의 700 디렉터리라 root 로는 쓸 수 없다.
# 그래서 여기서는 홈을 건드리지 않는다. 홈에 써야 하는 일은 로그인한 사용자 권한으로 한다.
set -euo pipefail

: "${IRONLAB_UID:?IRONLAB_UID가 필요합니다}"
USER_NAME=dev

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
    groupadd -g "$IRONLAB_UID" "$USER_NAME"
    # 홈(/home/dev)은 호스트에서 마운트되므로 만들지 않는다
    useradd -M -d /home/dev -s /bin/bash -u "$IRONLAB_UID" -g "$IRONLAB_UID" "$USER_NAME"
    # 계정 잠금 해제 (비밀번호 로그인은 sshd_config에서 막혀 있음)
    usermod -p '*' "$USER_NAME"
fi

# AMD GPU: /dev/kfd·/dev/dri/renderD* 는 그 장치 파일의 그룹(render, video)에 들어 있어야 열린다.
# 작업 컨테이너는 Docker 의 group_add 로 되지만, SSH 세션은 /etc/group 을 따르므로 여기서 넣는다.
GPU_GIDS="${IRONLAB_GPU_GIDS:-}"
for gid in ${GPU_GIDS//,/ }; do
    [[ "$gid" =~ ^[0-9]+$ ]] || continue
    grp=$(getent group "$gid" | cut -d: -f1 || true)
    if [[ -z "$grp" ]]; then
        grp="gpu$gid"
        groupadd -g "$gid" "$grp" || { echo "ironlab-entrypoint: GPU 그룹 $gid 을 만들지 못했습니다" >&2; continue; }
    fi
    usermod -aG "$grp" "$USER_NAME" || echo "ironlab-entrypoint: $USER_NAME 을 GPU 그룹 $grp 에 넣지 못했습니다" >&2
done

# SSH 세션에도 컨테이너 환경변수(HF 캐시, 로컬 LLM 주소, CUDA 라이브러리 경로 등)가 보이게 파일로 남긴다.
# /etc/ironlab-profile.sh 가 읽는다. 부가 기능이므로 실패해도 sshd 는 띄운다.
{
    while IFS='=' read -r -d '' key value; do
        case "$key" in
            IRONLAB_*|HF_*|TORCH_HOME|NVIDIA_*|CUDA_*|HSA_*|HIP_*|ROCM_*|ROCR_*|OPENAI_*|LD_LIBRARY_PATH|LIBRARY_PATH)
                printf 'export %s=%q\n' "$key" "$value" ;;
        esac
    done < /proc/self/environ > /etc/ironlab-env.sh
} || echo "ironlab-entrypoint: 환경변수 파일을 만들지 못했습니다" >&2

exec /usr/sbin/sshd -D -e
