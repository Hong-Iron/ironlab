#!/bin/bash
# Sony AI 의 Woosh 로 효과음을 만든다. 워크스페이스 셸과 ironlab 콘솔의 "효과음 생성" 화면 양쪽에서 같은 방식으로 쓴다.
#
#   woosh.sh --prompt-file <소리 설명(영어)> --output <결과.wav> [--mp3 <사본.mp3>] [옵션...]
#   woosh.sh --video <영상> [--prompt-file <설명>] --output <결과.wav> --video-out <소리를 붙인 영상.mp4> [옵션...]
#
#   예) 글로:    ./woosh.sh --prompt "footsteps on dry leaves in a forest" --output outputs/a.wav
#   예) 되풀이:  ./woosh.sh --prompt "steady rain on a tin roof" --output outputs/rain.wav --loop
#   예) 영상에:  ./woosh.sh --video inputs/clip.mp4 --output outputs/clip.wav --video-out outputs/clip.mp4
#
# 옵션은 woosh_run.py 로 그대로 넘어간다: --model fast|full, --seconds, --seed, --cfg, --steps, --renoise,
# --solver, --tolerance, --negative-file, --video-start, --trim-silence, --fade-in, --fade-out, --loop, --loop-crossfade, --peak, --thumb.
# 자세한 설명은 woosh_run.py --help.
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${WOOSH_PYTHON:-$(dirname "$RUN_DIR")/.conda/envs/woosh/bin/python}"
if [ ! -x "$PY" ]; then
    echo "[woosh] 실패: 파이썬 환경이 없습니다 ($PY). setup_woosh.sh 로 만드세요." >&2
    exit 1
fi
# 홈의 ~/.local 에 깐 파이썬 패키지를 섞어 쓰지 않는다. 섞으면 그 홈에서만 돌고 다른 사용자의 홈에서는 패키지가 없어 멈춘다
export PYTHONNOUSERSITE=1
exec "$PY" -u "$RUN_DIR/woosh_run.py" "$@"
