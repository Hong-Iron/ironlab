#!/bin/bash
# Qwen-Image-2.1 전용 conda 환경(qwenimage)을 만든다. 다시 돌려도 된다 (있는 것은 건너뛴다).
#
# 따로 만드는 까닭: 이 모델은 transformers 5.17 이상이 필요한데, Pixal3D·TRELLIS.2 환경(trellis2)은
# transformers 4.57.3 이어야 한다. 한 환경에 둘을 같이 둘 수 없다.
#
# torch 는 trellis2 와 같은 2.6.0 (CUDA 12.4) 을 쓴다. 이미 받아 둔 파일을 다시 쓰므로 빠르다.
set -eo pipefail
. /opt/conda/etc/profile.d/conda.sh

ENV_NAME="${QWEN_IMAGE_ENV:-qwenimage}"
PY="$HOME/.conda/envs/$ENV_NAME/bin/python"

if [ ! -x "$PY" ]; then
    echo "[setup] conda 환경 $ENV_NAME 만들기 (python 3.11)"
    conda create -y -n "$ENV_NAME" python=3.11 pip
fi
echo "[setup] torch 설치"
"$PY" -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
echo "[setup] transformers, accelerate, pillow 설치"
"$PY" -m pip install "transformers>=5.17" accelerate pillow
echo "[setup] diffusers 최신판 설치 (GitHub)"
"$PY" -m pip install "git+https://github.com/huggingface/diffusers"
echo "[setup] 불러오기 확인"
"$PY" - <<'EOF'
import torch, transformers, diffusers
from diffusers import QwenImage21Pipeline  # noqa: F401  이 이름이 없으면 diffusers 가 너무 옛 판이다
print("ENV-READY torch", torch.__version__, "cuda", torch.cuda.is_available(),
      "transformers", transformers.__version__, "diffusers", diffusers.__version__)
EOF
