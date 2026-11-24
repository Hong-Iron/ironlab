#!/bin/bash
# YuE2 로 노래 한 곡을 만든다. 워크스페이스 셸과 ironlab 콘솔의 "음악 생성" 화면 양쪽에서 같은 방식으로 쓴다.
#
#   yue2.sh --style "<분위기 설명>" --lyrics-file <가사 파일> --output <결과.flac> [옵션...]
#
#   예) ~/yue2-run/yue2.sh --style "K-pop ballad, piano, soft female vocal" \
#           --lyrics-file inputs/song.txt --output outputs/song.flac --mp3 outputs/song.mp3
#   예) 악보까지만 쓰고, 고친 악보로 소리 만들기
#       ./yue2.sh --style "..." --lyrics-file inputs/song.txt --output outputs/song.abc --stage plan
#       ./yue2.sh --style "..." --lyrics-file inputs/song.txt --abc-file inputs/edited.abc --output outputs/song.flac
#
# 옵션은 yue2_song.py 로 그대로 넘어간다: --seed, --cot full|melody|off, --cfg-scale, --abc-file,
# --stage all|plan, --artifacts <폴더>, --mp3 <경로>, --score <경로>, --style-file,
# 단계마다 --plan-temperature --plan-top-p --plan-top-k --plan-repeat --plan-max-tokens (plan 자리에 song 도 된다),
# --audio-steps. 자세한 설명은 yue2_song.py --help.
#
# 2026-11-14 에 이 서버(RTX 3090, 24 GB)에서 잰 값: 91초짜리 곡을 46초에 만들고 GPU 메모리는 최대 약 9 GB.
# 콘솔은 이 작업에 VRAM 을 16 GB 로 잡는다 (만든 쪽이 가장 긴 입력에서 잰 최댓값이 14.1 GiB).
set -o pipefail

if [ $# -lt 1 ]; then
    echo "사용법: $0 --style \"<분위기 설명>\" --lyrics-file <가사 파일> --output <결과.flac> [옵션...]" >&2
    exit 2
fi

RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# conda 셸 함수는 자식 셸에 상속되지 않으므로 여기서 다시 정의한다.
. /opt/conda/etc/profile.d/conda.sh
conda activate "${YUE2_ENV:-yue2}"

# 진행 상황이 로그에 바로 찍히게 한다 (출력이 파일로 갈 때 파이썬은 기본으로 모아서 쓴다).
export PYTHONUNBUFFERED=1

SECONDS=0
python "$RUN_DIR/yue2_song.py" "$@"
rc=$?
if [ $rc -ne 0 ]; then
    echo "[yue2] 실패: 종료 코드 $rc, ${SECONDS} s" >&2
fi
exit $rc
