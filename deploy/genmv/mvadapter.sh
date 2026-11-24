#!/bin/bash
# 사진 한 장에서 물체를 여러 방향(6 또는 4)에서 본 그림을 한 번에 그린다 (MV-Adapter).
# 워크스페이스 셸과 ironlab 콘솔의 "여러 방향 그림" 화면·노드 양쪽에서 쓴다.
#
#   mvadapter.sh --image <사진> --output <모은 그림.png> [--prompt-file <설명.txt>] [--views 6|4] [--seed N]
#                [--views-out <묶음.json>] [--out-front <그림>] … [--thumb <미리보기.jpg>]
#
#   예) ~/mvadapter-run/mvadapter.sh --image inputs/dog.png --output outputs/dog.png --prompt "a dog"
#       결과: outputs/dog.png (3×2 로 모은 그림), outputs/dog.front.png … (방향마다), outputs/dog.views.json (3D 만들기에 넘길 묶음)
#   3D 로 만들기: ~/pixal3d-run/pixal3d.sh --views-json outputs/dog.views.json --output outputs/dog.glb
#
# 2026-11-16 이 서버(RTX 3090)에서 잰 값: 6방향 768px 약 1분, GPU 메모리 최대 약 15 GB. 콘솔은 17 GB 를 잡는다.
# 모델: MV-Adapter(Apache-2.0) + SDXL 1.0(CreativeML Open RAIL++-M) + 배경 지우기 BiRefNet(MIT).
set -o pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 --image <사진> --output <모은 그림.png> [옵션...]" >&2
    exit 2
fi
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

output=""
prev=""
for a in "$@"; do
    [ "$prev" = "--output" ] && output="$a"
    prev="$a"
done
[ -n "$output" ] && output="$(realpath -m -- "$output")"

. /opt/conda/etc/profile.d/conda.sh
conda activate "${MVADAPTER_ENV:-mvadapter}"
export PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 HF_HUB_OFFLINE=1     # ~/.local 의 다른 torch 가 섞이지 않게, 인터넷 없이

SECONDS=0
python "$RUN_DIR/mv_views.py" "$@"
rc=$?
if [ $rc -eq 0 ] && [ -n "$output" ] && [ -f "$output" ]; then
    echo "[mv] 파일: $output ($(du -h "$output" | cut -f1)), 스크립트 전체 ${SECONDS} s"
else
    echo "[mv] 실패: 종료 코드 $rc, ${SECONDS} s" >&2
    [ $rc -eq 0 ] && rc=1
fi
exit $rc
