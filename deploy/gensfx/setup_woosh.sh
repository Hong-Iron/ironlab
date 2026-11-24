#!/bin/bash
# Woosh(Sony AI 효과음 모델)를 워크스페이스에 설치한다. 다시 돌려도 된다 (있는 것은 건너뛴다).
#
#   1. conda 환경 woosh (python 3.12, torch 2.8.0 CUDA 12.8, Woosh 패키지)를 ~/.conda/envs/woosh 에 만든다.
#   2. Sony 가 공개한 가중치 7개(약 9 GB)를 GitHub 릴리스에서 받아 ~/models/woosh/checkpoints 에 푼다.
#   3. 글 이해 모델(roberta-large)과 영상 특징 모델(Synchformer)의 파일을 ~/models/woosh/hf 에 받는다.
#
# 따로 환경을 만드는 까닭: Woosh 는 torch 2.8.0 과 python 3.12 이상을 요구해서, 다른 생성기의 환경과 판이 맞지 않는다.
# 가중치의 라이선스는 CC BY-NC 4.0 이다 (비상업 용도만).
# 모든 사용자가 쓰게 하려면, 설치한 뒤 서버에서 sudo bash deploy/shared-apps.sh --migrate-from <설치한 사용자> 를 돌린다.
set -eo pipefail
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV="$HOME/.conda/envs/woosh"
PY="$ENV/bin/python"
M="$HOME/models/woosh"
WOOSH_REF="7f238bbd5d6dbc94d27190c07827b2d0eaefa268"          # SonyResearch/Woosh 의 main (2026-09-11)
REL="https://github.com/SonyResearch/Woosh/releases/download/v1.0.0"
# 파일 이름:바이트 수 (릴리스 페이지의 크기. 받은 파일이 이 크기여야 푼다)
ZIPS="Woosh-AE:822991075 TextConditionerA:1297121262 TextConditionerV:1298488076 Woosh-Flow:1253883744
Woosh-DFlow:1281505601 Woosh-VFlow-8s:1537130885 Woosh-DVFlow-8s:1564739862"

export PYTHONNOUSERSITE=1          # ~/.local 에 이미 깔린 패키지가 있어도 환경 안에 따로 깐다 (공용으로 옮겨 다른 사용자도 쓰게)
. /opt/conda/etc/profile.d/conda.sh
if [ ! -x "$PY" ]; then
    echo "[setup] conda 환경 woosh 만들기 (python 3.12)"
    conda create -y -p "$ENV" python=3.12 pip
fi
if ! "$PY" -c "import woosh" 2>/dev/null; then
    echo "[setup] torch 2.8.0 (CUDA 12.8) 설치. 몇 분 걸린다"
    "$PY" -m pip install -q --index-url https://download.pytorch.org/whl/cu128 torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0
    echo "[setup] 나머지 패키지 설치"
    "$PY" -m pip install -q "einops>=0.8.1" "hydra-core>=1.3.2" "lightning>=2.5.6" "timm>=1.0.22" "torchdiffeq>=0.2.5" \
        "transformers>=4.57.2,<5" "pydantic>=2.12.4" "omegaconf>=2.3.0" "av==16.1.0" requests soundfile safetensors huggingface_hub numpy
    # av 는 16 판에 고정한다: 17 판부터 영상의 초당 장 수가 int() 로 바뀌지 않는 꼴로 나와 Woosh 의 영상 읽기가 멈춘다.
    # hear21passt 는 의존 패키지 목록이 낡아 그대로 깔면 torch 판을 바꾼다. Woosh 도 의존 없이 깔라고 적어 둔다.
    "$PY" -m pip install -q --no-deps hear21passt==0.0.26 "git+https://github.com/SonyResearch/Woosh@$WOOSH_REF"
fi

mkdir -p "$M/zips" "$M/checkpoints"
for spec in $ZIPS; do
    name=${spec%%:*}; want=${spec##*:}; z="$M/zips/$name.zip"
    if [ -f "$M/checkpoints/$name/config.yaml" ] && ls "$M/checkpoints/$name"/weights.* >/dev/null 2>&1; then continue; fi
    have=$(stat -c %s "$z" 2>/dev/null || echo 0)
    if [ "$have" != "$want" ]; then
        echo "[setup] $name.zip 받기 ($((want / 1000000)) MB)"
        curl -fSL --retry 5 -C - -o "$z" "$REL/$name.zip" || curl -fSL --retry 5 -o "$z" "$REL/$name.zip"
    fi
    have=$(stat -c %s "$z")
    [ "$have" = "$want" ] || { echo "[setup] 실패: $name.zip 크기가 $have 바이트입니다 (기대 $want). 지우고 다시 돌리세요."; exit 1; }
    echo "[setup] $name 풀기"
    "$PY" "$RUN_DIR/setup_unzip.py" "$z" "$M"
done

echo "[setup] 글 이해 모델의 사전과 영상 특징 모델 받기"
WOOSH_ONLINE=1 HF_HUB_CACHE="$M/hf" "$PY" - <<'EOF'
import os
os.environ["HF_HUB_OFFLINE"] = "0"
from huggingface_hub import hf_hub_download, snapshot_download
# Woosh 는 글 이해 모델의 뼈대만 설정 파일로 짓고 가중치는 TextConditioner 에서 읽는다. 그래서 사전과 설정만 받는다.
# 이름은 코드가 부르는 그대로("roberta-large")여야 인터넷 없이 찾는다.
snapshot_download("roberta-large", allow_patterns=["config.json", "merges.txt", "vocab.json", "tokenizer.json", "tokenizer_config.json"])
hf_hub_download("hkchengrex/MMAudio", filename="synchformer_state_dict.pth", subfolder="ext_weights")
print("HF-READY")
EOF

echo "[setup] 시험: 글로 2초 소리 하나 만들기"
mkdir -p "$RUN_DIR/outputs"
WOOSH_ONLINE=1 "$RUN_DIR/woosh.sh" --prompt "a wooden door creaks open slowly" --seconds 2 --output "$RUN_DIR/outputs/setup-test.wav"
echo "[setup] 시험: 영상에 소리 붙이기 (Woosh 예제 영상, 영상 특징 모델이 설정 파일을 처음 한 번 받는다)"
if [ -f "$M/zips/samples.zip" ] || curl -fsSL -o "$M/zips/samples.zip" "$REL/samples.zip"; then
    "$PY" "$RUN_DIR/setup_unzip.py" "$M/zips/samples.zip" "$M" samples
    WOOSH_ONLINE=1 "$RUN_DIR/woosh.sh" --video "$M/samples/video_sample.mp4" --seconds 3 --output "$RUN_DIR/outputs/setup-video.wav"
fi
echo "[setup] 끝. 받은 zip 은 지워도 된다: rm -r $M/zips"
