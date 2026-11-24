#!/bin/bash
# 사진 한 장 → 3D 모델(GLB), "모양 먼저": 모양은 사진 한 장 3D(Pixal3D)로 먼저 정하고, MV-Adapter(ig2mv)가 그 모양 위에
# 여섯 방향(앞·오른쪽·뒤·왼쪽·위·아래)의 색만 칠해 텍스처에 다시 입힌다. ironlab 콘솔의 노드 "3D 만들기 (모양 먼저)"가 부른다.
#
#   shapefirst.sh --image <사진> --output <결과.glb> [--prompt-file <설명.txt>] [--views-image <칠한 여섯 방향.png>]
#                 [--thumb <미리보기.jpg>] [--seed N] [--paint-seed N] [--color auto|none|group|overlap]
#                 [--faces N] [--texture-size 1024|2048] [--keep-work]
#
#   예) ~/shapefirst-run/shapefirst.sh --image inputs/cow.png --output outputs/cow.glb
#
# 왜 이 순서인가: '여러 방향 그리기'(MV-Adapter i2mv)는 사진에서 모양까지 상상해 그려서, 사진의 자세가 모델이 배운 자세
# (정면, 눈높이)와 다르면 소 두 마리가 겹치거나 뿔이 두 겹인 3D 가 나왔다. 여기서는 모양을 사진 한 장 3D 가 정하고 모델은
# 색만 칠하므로 그런 모양 오류가 생기지 않는다. 사진이 본 쪽은 원래 텍스처(사진 색)를 그대로 둔다.
#
# 단계 (로그에 "[shape] 단계 k/5" 로 찍는다. 콘솔이 이 줄로 진행을 보인다)
#   1 배경 지우기 (BiRefNet, mvadapter 환경) → 2 사진 한 장 3D (~/pixal3d-run/pixal3d.sh) → 3 모양 그림 렌더링 (trellis2 환경,
#     사진 쪽은 Pixal3D 의 약속대로 -z)
#   → 4 색 칠하기 (MV-Adapter ig2mv, mvadapter 환경) → 5 텍스처 입히기·색 맞추기 (trellis2 환경)
# 2026-11-16 이 서버(RTX 3090)에서 잰 값: 소·고양이·그루터기·거북이, 한 물체 약 3분 반 (사진 한 장 3D 99~143 s,
# 색 칠하기 약 75 s). GPU 메모리는 단계마다 따로 써서 가장 큰 때가 약 16 GB (사진 한 장 3D). 콘솔은 18 GB 를 잡는다.
# 필요한 것: ~/pixal3d-run/pixal3d.sh, ~/mvadapter-run/MV-Adapter, ~/models/mvadapter-hub, ~/models/mvadapter-ig2mv,
#   conda 환경 mvadapter·trellis2 (모두 공용 영역의 링크).
# 모델: Pixal3D, MV-Adapter(Apache-2.0) + SDXL 1.0(CreativeML Open RAIL++-M), 배경 지우기 BiRefNet(MIT).
set -o pipefail

say() { echo "[shape] $*"; }
fail() { echo "[shape] 실패: $*" >&2; exit 2; }

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="" OUTPUT="" PROMPT="" VIEWS_IMAGE="" THUMB="" SEED=42 PAINT_SEED=1 COLOR=auto FACES=200000 TEX=2048 KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --image) IMAGE=$2; shift 2 ;;
        --output) OUTPUT=$2; shift 2 ;;
        --prompt-file) PROMPT=$2; shift 2 ;;
        --views-image) VIEWS_IMAGE=$2; shift 2 ;;
        --thumb) THUMB=$2; shift 2 ;;
        --seed) SEED=$2; shift 2 ;;
        --paint-seed) PAINT_SEED=$2; shift 2 ;;
        --color) COLOR=$2; shift 2 ;;
        --faces) FACES=$2; shift 2 ;;
        --texture-size) TEX=$2; shift 2 ;;
        --keep-work) KEEP=1; shift ;;
        -h|--help) sed -n 2,7p "$0"; exit 0 ;;
        *) fail "모르는 옵션입니다 (받은 값 '$1'). 위 사용법의 옵션만 받습니다" ;;
    esac
done
[ -n "$IMAGE" ] && [ -n "$OUTPUT" ] || fail "--image <사진> 과 --output <결과.glb> 를 함께 주세요"
[ -f "$IMAGE" ] || fail "사진이 없습니다 (받은 경로 $IMAGE)"
case "$OUTPUT" in *.glb) ;; *) fail "결과 파일은 .glb 로 끝나야 합니다 (받은 값 $OUTPUT)" ;; esac
case "$COLOR" in auto|none|group|overlap) ;; *) fail "--color 는 auto, none, group, overlap 가운데 하나입니다 (받은 값 $COLOR)" ;; esac
case "$TEX" in 1024|2048) ;; *) fail "--texture-size 는 1024 또는 2048 입니다 (받은 값 $TEX). 4096 은 시험하지 않았습니다" ;; esac
for n in "$SEED" "$PAINT_SEED" "$FACES"; do
    [[ "$n" =~ ^[0-9]+$ ]] || fail "시드와 삼각형 수는 0 이상의 정수입니다 (받은 값 $n)"
done
P3D="$HOME/pixal3d-run/pixal3d.sh"
[ -x "$P3D" ] || fail "사진 한 장 3D 실행 스크립트가 없습니다 ($P3D). 3D 생성기가 설치된 홈에서 돌리세요"
[ -d "$HOME/mvadapter-run/MV-Adapter" ] || fail "MV-Adapter 코드가 없습니다 (~/mvadapter-run/MV-Adapter). 여러 방향 그림 생성기가 필요합니다"
[ -d "$HOME/models/mvadapter-ig2mv" ] || fail "색 칠하기 모델이 없습니다 (~/models/mvadapter-ig2mv). 관리자가 공용 영역에 받아 두어야 합니다"

IMAGE=$(realpath -- "$IMAGE")
OUTPUT=$(realpath -m -- "$OUTPUT")
[ -n "$PROMPT" ] && PROMPT=$(realpath -- "$PROMPT")
[ -n "$VIEWS_IMAGE" ] && VIEWS_IMAGE=$(realpath -m -- "$VIEWS_IMAGE")
[ -n "$THUMB" ] && THUMB=$(realpath -m -- "$THUMB")
WORK="${OUTPUT%.glb}.work"
rm -rf "$WORK"
mkdir -p "$WORK" "$(dirname "$OUTPUT")" || fail "작업 폴더를 만들지 못했습니다 ($WORK)"

. /opt/conda/etc/profile.d/conda.sh || fail "conda 를 찾지 못했습니다 (/opt/conda/etc/profile.d/conda.sh)"
export PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 HF_HUB_OFFLINE=1      # ~/.local 의 다른 패키지가 섞이지 않게, 인터넷 없이
cd "$RUN_DIR" || fail "실행 폴더로 가지 못했습니다 ($RUN_DIR)"
SECONDS=0
stage() {                                   # $1 단계 번호, $2 이름, 나머지: 실행할 명령
    local k=$1 name=$2; shift 2
    say "단계 $k/5 $name (앞 단계까지 ${SECONDS} s)"
    "$@" || { echo "[shape] 실패: 단계 $k/5 $name 에서 멈췄습니다. 중간 파일: $WORK" >&2; exit 1; }
}
in_env() { conda activate "$1" && shift && "$@"; }

stage 1 "배경 지우기" in_env mvadapter python cutout.py --image "$IMAGE" --out "$WORK/cutout.png"
stage 2 "사진 한 장 3D" "$P3D" "$WORK/cutout.png" "$WORK/single.glb" --seed "$SEED" --faces "$FACES" --texture-size "$TEX"
stage 3 "모양 그림 렌더링" in_env trellis2 python geo_views.py --mesh "$WORK/single.glb" --photo "$WORK/cutout.png" --out-dir "$WORK/views"
TEXT_ARGS=()
[ -n "$PROMPT" ] && TEXT_ARGS=(--text-file "$PROMPT")
stage 4 "색 칠하기" in_env mvadapter python ig2mv_paint.py --control "$WORK/views/control.npz" --image "$WORK/cutout.png" \
    --out-dir "$WORK/paint" --seeds "$PAINT_SEED" "${TEXT_ARGS[@]}"
FRONT=$(cat "$WORK/views/front.txt")
THUMB_ARGS=()
[ -n "$THUMB" ] && THUMB_ARGS=(--thumb "$THUMB")
stage 5 "텍스처 입히기" in_env trellis2 python bake.py --mesh "$WORK/single.glb" --front="$FRONT" \
    --paint-dir "$WORK/paint_s$PAINT_SEED" --output "$OUTPUT" --color "$COLOR" "${THUMB_ARGS[@]}"
[ -n "$VIEWS_IMAGE" ] && cp "$WORK/paint_s$PAINT_SEED/grid.png" "$VIEWS_IMAGE"

[ -f "$OUTPUT" ] || fail "결과 파일이 생기지 않았습니다 ($OUTPUT). 중간 파일: $WORK"
[ "$KEEP" = 1 ] || rm -rf "$WORK"
say "끝: $OUTPUT ($(du -h "$OUTPUT" | cut -f1)), 전체 ${SECONDS} s"
