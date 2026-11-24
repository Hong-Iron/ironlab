#!/bin/bash
# 생성기(그림·영상·음성·3D·음악·효과음·소리 다듬기·여러 방향 그림·모양 먼저 3D)를 모든 사용자가 쓰게 공용 영역에 두고, 사용자 홈마다 링크를 건다.
#
#   sudo bash deploy/shared-apps.sh                      # 실행 스크립트 올리기 + 홈마다 링크 (설치 스크립트가 배포 때마다 부른다)
#   sudo bash deploy/shared-apps.sh --migrate-from <사용자>  # 처음 한 번: 그 사용자 홈의 모델·환경·실행 파일을 공용으로 옮긴 뒤 위와 같이
#
# 무엇이 어디에 있게 되나 (서버 경로 → 워크스페이스·작업 안에서 보이는 경로)
#   /srv/shared/models/minimax-h3, drama3, woosh, demucs, mvadapter-hub, mvadapter-ig2mv → /shared/models/…
#       영상·음성·효과음·소리 나누기·여러 방향 그림·모양 먼저 3D(색 칠하기) 모델
#   /srv/shared/models/envs/<환경>               → /shared/models/envs/…   생성기가 도는 파이썬 환경 (conda)
#   /srv/shared/models/apps/<생성기 폴더>         → /shared/models/apps/…   실행 스크립트·실행 파일·LoRA·Pixal3D 코드
# 공용 영역은 워크스페이스 안에서 읽기 전용이다. 사용자가 만든 것(올린 파일, 결과)은 각자의 홈 <생성기 폴더>/inputs·outputs 에만 있다.
#
# 사용자 홈에는 같은 자리에 링크를 둔다: ~/models/<모델>, ~/.conda/envs/<환경>, ~/Pixal3D, ~/<생성기 폴더>/<파일>.
# 환경 안에는 만들 때의 경로(/home/dev/.conda/envs/…)가 박혀 있다. 워크스페이스마다 홈이 /home/dev 이므로 링크로 그 경로가 모두에게 맞는다.
# 시작 스크립트(qwen-image.sh 등)만은 링크가 아니라 사본을 둔다. 콘솔(서버)이 홈에서 이 파일을 찾고 내용을 읽기 때문이다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"            # 묶음의 deploy/
USERS="${IRONLAB_USERS_ROOT:-/srv/users}"
SHARED="${IRONLAB_SHARED_MODELS:-/srv/shared/models}"
IN="${IRONLAB_SHARED_IN_CONTAINER:-/shared/models}"            # 링크가 가리키는 경로 (컨테이너 안의 공용 경로)
RUNS="qwen-image-run:genimg:qwen-image.sh minimax-h3-run:genvideo:h3.sh drama3-run:genvoice:drama3.sh pixal3d-run:gen3d:pixal3d.sh yue2-run:genmusic:yue2.sh woosh-run:gensfx:woosh.sh polish-run:genpolish:polish.sh mvadapter-run:genmv:mvadapter.sh shapefirst-run:genshape:shapefirst.sh"
ENVS="qwenimage trellis2 yue2 media woosh polish mvadapter"
MODELS="minimax-h3 drama3 woosh demucs mvadapter-hub mvadapter-ig2mv"
EXTRAS="minimax-h3-run/bin drama3-run/bin qwen-image-run/loras mvadapter-run/MV-Adapter"  # 저장소에 없는 실행 파일·LoRA·코드 (처음 옮길 때만)

say() { echo "  $*"; }

# ── 1. 처음 한 번: 한 사용자 홈의 큰 것들을 공용으로 옮긴다 (같은 디스크 안의 이름 바꾸기라 크기와 상관없이 바로 끝난다) ──
# 새 생성기를 더한 뒤 다시 돌려도 된다. 이미 옮긴 것(링크)은 건너뛰고, 새로 설치한 모델·환경만 옮긴다.
if [ "${1:-}" = "--migrate-from" ]; then
    U="${2:?--migrate-from 뒤에 사용자 이름을 주세요}"
    H="$USERS/$U"
    [ -d "$H" ] || { echo "STOP. $H 가 없습니다"; exit 1; }
    if command -v nvidia-smi >/dev/null && nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -q .; then
        echo "STOP. GPU 를 쓰는 작업이 돌고 있습니다. 끝난 뒤 다시 하세요."; exit 1
    fi
    echo "==> $U 홈의 모델·환경·실행 파일을 공용으로 옮김"
    mkdir -p "$SHARED/envs" "$SHARED/apps"
    move_once() {                                   # $1 원래 자리, $2 공용 자리
        if [ -L "$1" ] || [ ! -e "$1" ]; then say "(건너뜀: $1 은 이미 옮겼거나 없음)"; return; fi
        if [ -e "$2" ]; then echo "STOP. $2 가 이미 있습니다. 직접 확인하세요."; exit 1; fi
        mkdir -p "$(dirname "$2")"
        mv "$1" "$2"
        say "옮김: $1 → $2"
    }
    for m in $MODELS; do move_once "$H/models/$m" "$SHARED/$m"; done
    for e in $ENVS; do move_once "$H/.conda/envs/$e" "$SHARED/envs/$e"; done
    move_once "$H/Pixal3D" "$SHARED/apps/Pixal3D"
    for x in $EXTRAS; do move_once "$H/$x" "$SHARED/apps/$x"; done
    # 생성기 폴더의 나머지(지금 판 스크립트, 예전 판 .v1 등, 설치 기록)는 홈의 runner-backups 로 치운다.
    # 지금 판은 아래 2 에서 묶음의 것을 공용에 올리고 3 에서 다시 건다. inputs·outputs(사용자가 만든 것)는 건드리지 않는다.
    for spec in $RUNS; do
        r=${spec%%:*}
        [ -d "$H/$r" ] || continue
        for f in "$H/$r"/* "$H/$r"/.[!.]*; do
            [ -e "$f" ] || [ -L "$f" ] || continue
            name=$(basename "$f")
            # __pycache__ 는 파이썬이 다시 만드는 것이라 치우지 않는다 (2026-11-16: 이미 치운 __pycache__ 와 이름이 겹쳐 mv 가 멈췄다)
            case "$name" in inputs|outputs|__pycache__) continue ;; esac
            [ -L "$f" ] && continue
            [ "$name" = "${spec##*:}" ] && continue     # 시작 스크립트 사본은 아래 3 이 바꾼다 (고친 판이면 .bak 으로 남긴다)
            mkdir -p "$H/runner-backups/$r"
            dest="$H/runner-backups/$r/$name"
            [ -e "$dest" ] && dest="$dest.$(date +%Y%m%d-%H%M%S)"     # 전에 치운 것과 이름이 겹치면 날짜를 붙인다
            mv "$f" "$dest"
        done
    done
    [ -d "$H/runner-backups" ] && chown -R --reference="$H" "$H/runner-backups"
fi

# ── 2. 실행 스크립트를 공용에 올린다 (이 묶음의 deploy/gen*/ 에서) ──
echo "==> 생성기 실행 스크립트를 공용(apps)에 올림"
for spec in $RUNS; do
    IFS=: read -r r src script <<< "$spec"
    [ -d "$HERE/$src" ] || continue
    mkdir -p "$SHARED/apps/$r"
    (cd "$HERE/$src" && find . -type f ! -path "*/__pycache__/*" ! -name "*.pyc" -print0 |
        while IFS= read -r -d '' f; do install -D -m 0644 "$f" "$SHARED/apps/$r/${f#./}"; done)
    chmod 0755 "$SHARED/apps/$r/$script"
    say "$r ← deploy/$src"
done
for d in $(for m in $MODELS; do echo "$SHARED/$m"; done) "$SHARED"/envs "$SHARED"/apps; do
    [ -e "$d" ] && chmod -R a+rX "$d"
done

# ── 3. 사용자 홈마다 링크와 생성기 폴더를 둔다 ──
link() {                                            # $1 홈 안의 자리, $2 가리킬 곳, $3 주인을 따를 홈
    if [ -L "$1" ]; then ln -sfn "$2" "$1"; chown -h --reference="$3" "$1"; return; fi
    if [ -e "$1" ]; then say "(그대로 둠: $1 은 사용자의 것)"; return; fi
    mkdir -p "$(dirname "$1")"
    ln -s "$2" "$1"
    chown -h --reference="$3" "$1"
}
echo "==> 사용자 홈마다 링크"
for H in "$USERS"/*/; do
    H=${H%/}
    [ -d "$H" ] || continue
    for m in $MODELS; do [ -e "$SHARED/$m" ] && link "$H/models/$m" "$IN/$m" "$H"; done
    for e in $ENVS; do [ -e "$SHARED/envs/$e" ] && link "$H/.conda/envs/$e" "$IN/envs/$e" "$H"; done
    [ -e "$SHARED/apps/Pixal3D" ] && link "$H/Pixal3D" "$IN/apps/Pixal3D" "$H"
    for spec in $RUNS; do
        IFS=: read -r r src script <<< "$spec"
        [ -d "$SHARED/apps/$r" ] || continue
        mkdir -p "$H/$r/inputs" "$H/$r/outputs"
        for f in "$SHARED/apps/$r"/*; do
            name=$(basename "$f")
            if [ "$name" = "$script" ]; then          # 시작 스크립트는 사본. 사용자가 고친 판이 있으면 .bak 으로 남긴다
                if [ -f "$H/$r/$name" ] && [ ! -L "$H/$r/$name" ] && ! cmp -s "$f" "$H/$r/$name"; then
                    cp -p "$H/$r/$name" "$H/$r/$name.bak"
                fi
                rm -f "$H/$r/$name"
                install -m 0755 "$f" "$H/$r/$name"
            else
                link "$H/$r/$name" "$IN/apps/$r/$name" "$H"
            fi
        done
        chown --reference="$H" "$H/$r" "$H/$r/inputs" "$H/$r/outputs" "$H/$r/$script"
        [ -e "$H/$r/$script.bak" ] && chown --reference="$H" "$H/$r/$script.bak"
    done
    for d in "$H/models" "$H/.conda" "$H/.conda/envs"; do [ -d "$d" ] && chown --reference="$H" "$d"; done
    say "$(basename "$H") 완료"
done
echo "SHARED-APPS-OK"
