#!/bin/bash
# MiniMax H3 로 소리가 든 영상을 만든다. 워크스페이스 셸과 ironlab 콘솔의 "영상 생성" 화면 양쪽에서 같은 방식으로 쓴다.
#
#   h3.sh --prompt-file <장면> [--sound-file <주변 소리>] [--music-file <배경 음악>] [--image <첫 장면> [--image <끝 장면>]]
#         --output <결과.mp4> [--thumb <작은 사진.jpg>] [--last-frame <끝 장면.png>] [옵션...]
#   h3.sh --join <앞.mp4> --join <뒤.mp4> [--join …] --output <결과.mp4> [--thumb …] [--last-frame …]   영상 이어 붙이기
#
# 옵션은 h3_run.py 로 그대로 넘어간다: --size, --seconds, --steps, --seed, --shift, --sampler, --scheduler, --sigmas,
# --attn-budget, --vae-tiling, --temporal-tiling, --crf. 자세한 설명은 h3_run.py --help.
#
# 2026-11-14 에 이 서버(RTX 3090, 24 GB)에서 잰 값: Flash 로 1344×768 5.2초 영상 355초 (GPU 메모리 최대 21.9 GB, 상한 없이),
# 864×480 3.75초 첫·끝 장면 영상 92초 (상한 19 GiB 로 최대 18.3 GB). 콘솔은 이 작업에 VRAM 을 22 GB 로 잡는다.
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# h3.sh --join 앞.mp4 --join 뒤.mp4 … --output 결과.mp4: 만든 영상들을 이어 붙인다 (GPU 를 쓰지 않는다. h3_join.py 참고)
if [ "$1" = "--join" ]; then
    exec /opt/conda/bin/python3 -u "$RUN_DIR/h3_join.py" "$@"
fi
exec /opt/conda/bin/python3 -u "$RUN_DIR/h3_run.py" "$@"
