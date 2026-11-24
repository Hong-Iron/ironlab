#!/bin/bash
# Fish Audio Drama 3 로 대본을 목소리로 읽는다. 워크스페이스 셸과 ironlab 콘솔의 "음성 생성" 화면 양쪽에서 같은 방식으로 쓴다.
#
#   drama3.sh --text-file <대본> --output <결과.wav> [--mp3 <사본.mp3>] [--ref <참고 목소리> --ref-text-file <그 목소리의 말>] [옵션...]
#
#   예) 한 사람:   ./drama3.sh --text-file inputs/a.txt --output outputs/a.wav
#   예) 두 사람:   대본에 "화자1: …" "화자2: …" 줄을 쓰고, --ref 를 화자 순서대로 두 번 준다.
#
# 옵션은 drama3_run.py 로 그대로 넘어간다: --seed, --temperature, --top-p, --top-k, --max-tokens, --chunk-size,
# --context, --cpu-layers, --fast. 자세한 설명은 drama3_run.py --help.
#
# 2026-11-14 에 이 서버(RTX 3090, 24 GB)에서 잰 값: CPU 층 16개로 8.5초 대사를 3.6초에 만들고(모델 올리기까지 8초),
# GPU 메모리는 최대 16.5 GB. 콘솔은 이 작업에 VRAM 을 18 GB 로 잡는다.
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /opt/conda/bin/python3 -u "$RUN_DIR/drama3_run.py" "$@"
