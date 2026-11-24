#!/bin/bash
# 만든 소리를 다듬는다 (목소리와 반주 나누기 → 목소리 다듬기 → 다시 섞기 → 음량 맞추기).
# 워크스페이스 셸과 ironlab 콘솔의 "소리 다듬기" 화면 양쪽에서 같은 방식으로 쓴다.
#
#   polish.sh --input <소리나 영상> --output <결과.flac> [--mp3 <사본.mp3>] [--source song|voice|sfx|mix] [옵션...]
#
#   예) 노래:       ./polish.sh --input ../yue2-run/outputs/a.flac --score-file ../yue2-run/outputs/a.abc --output outputs/a.flac
#   예) 목소리만:   ./polish.sh --input ../drama3-run/outputs/b.wav --source voice --output outputs/b.flac
#   예) 영상 소리:  ./polish.sh --input clip.mp4 --output outputs/c.flac --video-out outputs/c.mp4
#
# 옵션은 polish_run.py 로 그대로 넘어간다. 자세한 설명은 polish_run.py --help.
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${POLISH_PYTHON:-$(dirname "$RUN_DIR")/.conda/envs/polish/bin/python}"
if [ ! -x "$PY" ]; then
    echo "[polish] 실패: 파이썬 환경이 없습니다 ($PY). setup_polish.sh 로 만드세요." >&2
    exit 1
fi
# 홈의 ~/.local 에 깐 파이썬 패키지를 섞어 쓰지 않는다. 섞으면 그 홈에서만 돌고 다른 사용자의 홈에서는 패키지가 없어 멈춘다
export PYTHONNOUSERSITE=1
exec "$PY" -u "$RUN_DIR/polish_run.py" "$@"
