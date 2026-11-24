#!/bin/bash
# 소리 다듬기(polish.sh)가 쓰는 환경과 모델을 워크스페이스에 설치한다. 다시 돌려도 된다 (있는 것은 건너뛴다).
#
#   1. conda 환경 polish (python 3.11, torch 2.5.1 CUDA 12.4, Demucs 4.0.1, numpy, scipy, soundfile, pyloudnorm)를
#      ~/.conda/envs/polish 에 만든다.
#   2. 목소리와 반주를 나누는 Demucs 가중치 두 벌(htdemucs: 빠른 판, htdemucs_ft: 정밀한 판)을 ~/models/demucs 에 받는다.
#
# 따로 환경을 만드는 까닭: Demucs 4.0.1 은 torchaudio 의 옛 읽기·쓰기 함수를 써서, 2.9 판부터 그 함수가 빠진 torch 와 맞지 않는다.
# Demucs 는 Meta 가 MIT 라이선스로 공개했다.
# 모든 사용자가 쓰게 하려면, 설치한 뒤 서버에서 sudo bash deploy/shared-apps.sh --migrate-from <설치한 사용자> 를 돌린다.
set -eo pipefail
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV="$HOME/.conda/envs/polish"
PY="$ENV/bin/python"
M="$HOME/models/demucs"

export PYTHONNOUSERSITE=1          # ~/.local 에 이미 깔린 패키지가 있어도 환경 안에 따로 깐다 (공용으로 옮겨 다른 사용자도 쓰게)
. /opt/conda/etc/profile.d/conda.sh
if [ ! -x "$PY" ]; then
    echo "[setup] conda 환경 polish 만들기 (python 3.11)"
    conda create -y -p "$ENV" python=3.11 pip
fi
if ! "$PY" -c "import demucs, scipy, soundfile, pyloudnorm" 2>/dev/null; then
    echo "[setup] torch 2.5.1 (CUDA 12.4) 설치. 몇 분 걸린다"
    "$PY" -m pip install -q --index-url https://download.pytorch.org/whl/cu124 torch==2.5.1 torchaudio==2.5.1
    echo "[setup] Demucs 와 소리 계산 패키지 설치"
    "$PY" -m pip install -q demucs==4.0.1 "numpy<2.3" "scipy>=1.11" soundfile pyloudnorm
fi

echo "[setup] Demucs 가중치 받기 (~/models/demucs)"
mkdir -p "$M"
TORCH_HOME="$M" "$PY" - <<'PYEOF'
from demucs.pretrained import get_model
for name in ("htdemucs", "htdemucs_ft"):
    model = get_model(name)
    print(f"DEMUCS-READY {name} sources={model.sources}")
PYEOF
du -sh "$ENV" "$M"
echo "[setup] 끝"
