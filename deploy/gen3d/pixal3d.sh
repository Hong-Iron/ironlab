#!/bin/bash
# Pixal3D 로 사진 한 장을 3D 모델 파일(GLB)로 만든다.
# 워크스페이스 셸과 ironlab 콘솔의 "3D 생성" 화면 양쪽에서 쓴다.
#
#   pixal3d.sh <사진> [결과.glb] [옵션...]                 셸에서 쓰기 편한 꼴
#   pixal3d.sh --image <사진> --output <결과.glb> [옵션...]  콘솔이 쓰는 꼴 (모든 것을 옵션으로)
#   pixal3d.sh --views-json <묶음.views.json> --output <결과.glb> [옵션...]  여러 방향 그림(mvadapter.sh)으로 만들 때
#
#   예) ~/pixal3d-run/pixal3d.sh ~/pixal3d-run/inputs/cat.png
#       결과는 ~/pixal3d-run/outputs/cat.glb 에 쓴다.
#   예) ./pixal3d.sh inputs/cat.png outputs/cat-light.glb --seed 7 --faces 100000 --texture-size 2048
#
# 옵션은 pixal3d_run.py 로 그대로 넘어간다: --seed, 단계마다 --ss-steps --ss-guidance --ss-rescale --ss-rescale-t
# (ss 자리에 shape, tex 도 된다), --max-tokens, --fov-deg, --faces, --texture-size, --remesh / --no-remesh.
# 자세한 설명은 pixal3d_run.py --help.
#
# 기본은 GPU 메모리를 덜 쓰는 모드(해상도 1024)다.
# 2026-11-14 에 이 서버(RTX 3090, 24 GB)에서 잰 값: GPU 메모리 최대 약 16 GB, RAM 최대 약 25 GiB, 2~3분.
# 콘솔은 이 작업에 VRAM 을 18 GB 로 잡는다.
#
# 앞에 PIXAL3D_FULL=1 을 붙이면 모델을 한꺼번에 GPU 에 올리는 모드(해상도 1536)로 돈다.
# 이 모드는 24 GB 카드에서 GPU 메모리 부족(CUDA out of memory)으로 실패했다. 더 큰 카드에서만 쓴다.
set -o pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 <사진> [결과.glb] [옵션...]" >&2
    echo "    또는 $0 --image <사진> --output <결과.glb> [옵션...]" >&2
    exit 2
fi

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HUB="${HF_HUB_CACHE:-/shared/models/hub}"

if [ "${1#--}" != "$1" ]; then
    # 콘솔이 쓰는 꼴: 그대로 넘긴다. 끝난 뒤 파일을 확인하려고 --output 의 값만 찾아 둔다.
    output=""
    prev=""
    for a in "$@"; do
        [ "$prev" = "--output" ] && output="$a"
        prev="$a"
    done
    if [ -z "$output" ]; then
        echo "[pixal3d] --output <결과.glb> 가 없습니다." >&2
        exit 2
    fi
    args=("$@")
else
    image="$1"
    shift
    # 둘째 인자가 -- 로 시작하지 않으면 결과 파일 경로로 본다. 없으면 outputs/<사진 이름>.glb.
    if [ $# -ge 1 ] && [ "${1#--}" = "$1" ]; then
        output="$1"
        shift
    else
        stem="$(basename "${image%.*}")"
        output="$RUN_DIR/outputs/$stem.glb"
    fi
    args=(--image "$image" --output "$output" "$@")
fi
output="$(realpath -m -- "$output")"

# 배경 제거 모델: 관리자가 briaai/RMBG-2.0 을 공용 폴더에 받아 두었으면 Pixal3D 원래 설정을 쓴다.
# 없으면 로그인 없이 받을 수 있는 ZhengPeng7/BiRefNet 으로 바꾼 설정(open-rembg/pipeline.json)을 쓴다.
if [ -d "$HUB/models--briaai--RMBG-2.0/snapshots" ]; then
    model_path="TencentARC/Pixal3D"
else
    model_path="$RUN_DIR/open-rembg"
fi

mode="--low-vram"
[ "${PIXAL3D_FULL:-0}" = "1" ] && mode="--no-low-vram"

# conda 셸 함수는 자식 셸에 상속되지 않으므로 여기서 다시 정의한다.
. /opt/conda/etc/profile.d/conda.sh
conda activate "${PIXAL3D_ENV:-trellis2}"

# 보조 모델(NAF)은 torch.hub 으로 받는다. 기본 저장 폴더(/shared/models/torch)는 읽기 전용이라 홈으로 돌린다.
export TORCH_HOME="$HOME/.cache/torch"
# 진행 상황이 로그에 바로 찍히게 한다 (출력이 파일로 갈 때 파이썬은 기본으로 모아서 쓴다).
export PYTHONUNBUFFERED=1

echo "[pixal3d] 시작: 결과 $output, 모델 설정 $model_path, 모드 ${mode#--}"
SECONDS=0
python "$RUN_DIR/pixal3d_run.py" --model-path "$model_path" "$mode" "${args[@]}"
rc=$?
if [ $rc -eq 0 ] && [ -f "$output" ]; then
    echo "[pixal3d] 끝: $output ($(du -h "$output" | cut -f1)), ${SECONDS} s"
else
    echo "[pixal3d] 실패: 종료 코드 $rc, ${SECONDS} s" >&2
    [ $rc -eq 0 ] && rc=1
fi
exit $rc
