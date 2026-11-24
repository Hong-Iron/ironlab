#!/bin/bash
# Qwen-Image-2.1 로 그림 한 장을 만든다.
# 워크스페이스 셸과 ironlab 콘솔의 "이미지 생성" 화면 양쪽에서 쓴다.
#
#   qwen-image.sh "<그릴 내용>" [결과.png] [옵션...]                    셸에서 쓰기 편한 꼴
#   qwen-image.sh --prompt-file <글 파일> --output <결과.png> [옵션...]   콘솔이 쓰는 꼴 (모든 것을 옵션으로)
#
#   예) ~/qwen-image-run/qwen-image.sh "비 오는 밤의 네온 간판"
#       결과는 ~/qwen-image-run/outputs/<날짜-시각>.png 에 쓴다.
#   예) ./qwen-image.sh "배경을 노을 지는 바다로" outputs/beach.png --image inputs/photo.png
#   예) ./qwen-image.sh "귀여운 용 스티커" outputs/dragon.png --transparent --aspect 3:4 --seed 7
#
# 옵션은 qwen_image.py 로 그대로 넘어간다: --model turbo|base, --aspect, --size 가로x세로, --steps, --sigmas,
# --true-cfg, --negative, --seed, --image (여러 번), --transparent, --no-kv-cache, --tile. 자세한 설명은 qwen_image.py --help.
set -o pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 \"<그릴 내용>\" [결과.png] [옵션...]" >&2
    echo "    또는 $0 --prompt-file <글 파일> --output <결과.png> [옵션...]" >&2
    exit 2
fi

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --batch-file <묶음.json>: 여러 장을 모델 한 번 올려 그린다 (노드 편집기가 같은 모델 노드를 묶어 부른다)
if [ "$1" = "--batch-file" ]; then
    . /opt/conda/etc/profile.d/conda.sh
    conda activate "${QWEN_IMAGE_ENV:-qwenimage}"
    export PYTHONUNBUFFERED=1
    exec python "$RUN_DIR/qwen_image.py" "$@"
fi

# --output 이 들어 있으면 콘솔이 쓰는 꼴이다: 그대로 넘긴다. 끝난 뒤 파일을 확인하려고 그 값만 찾아 둔다.
output=""
prev=""
for a in "$@"; do
    [ "$prev" = "--output" ] && output="$a"
    prev="$a"
done

if [ -n "$output" ]; then
    args=("$@")
else
    # 그릴 내용은 첫 인자로 바로 주거나, --prompt-file <글 파일> 로 준다.
    if [ "$1" = "--prompt-file" ]; then
        if [ $# -lt 2 ] || [ ! -f "$2" ]; then
            echo "[qwen-image] 글 파일이 없습니다: ${2:-(경로 없음)}" >&2
            exit 2
        fi
        args=(--prompt-file "$2")
        shift 2
    else
        args=(--prompt "$1")
        shift
    fi
    # 다음 인자가 -- 로 시작하지 않으면 결과 파일 경로로 본다. 없으면 outputs/<날짜-시각>.png.
    if [ $# -ge 1 ] && [ "${1#--}" = "$1" ]; then
        output="$1"
        shift
    else
        output="$RUN_DIR/outputs/$(date +%Y%m%d-%H%M%S).png"
    fi
    args+=(--output "$output" "$@")
fi
output="$(realpath -m -- "$output")"

# 참고 그림 경로(--image 뒤의 값)는 부른 자리 기준이다. python 을 다른 폴더에서 돌리지 않으므로 그대로 넘긴다.

# conda 셸 함수는 자식 셸에 상속되지 않으므로 여기서 다시 정의한다.
. /opt/conda/etc/profile.d/conda.sh
conda activate "${QWEN_IMAGE_ENV:-qwenimage}"

# 진행 상황이 로그에 바로 찍히게 한다 (출력이 파일로 갈 때 파이썬은 기본으로 모아서 쓴다).
export PYTHONUNBUFFERED=1

SECONDS=0
python "$RUN_DIR/qwen_image.py" "${args[@]}"
rc=$?
if [ $rc -eq 0 ] && [ -f "$output" ]; then
    echo "[qwen-image] 파일: $output ($(du -h "$output" | cut -f1)), 스크립트 전체 ${SECONDS} s"
else
    echo "[qwen-image] 실패: 종료 코드 $rc, ${SECONDS} s" >&2
    [ $rc -eq 0 ] && rc=1
fi
exit $rc
