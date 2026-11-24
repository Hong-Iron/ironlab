#!/bin/bash
# YuE2 전용 conda 환경(yue2)을 만든다. 다시 돌려도 된다 (있는 것은 건너뛴다).
#
# 따로 만드는 까닭: YuE2 의 실행 패키지(yue2-infer 0.1.5)가 torch 2.10.0, transformers 4.57.6 처럼
# 판을 하나로 못 박아 둔다. 3D(trellis2)나 이미지(qwenimage) 환경과 같이 쓸 수 없다.
set -eo pipefail
. /opt/conda/etc/profile.d/conda.sh

ENV_NAME="${YUE2_ENV:-yue2}"
PY="$HOME/.conda/envs/$ENV_NAME/bin/python"
WHEEL="yue2_infer-0.1.5-py3-none-any.whl"
WHEEL_URL="https://huggingface.co/m-a-p/YuE2-3B/resolve/main/$WHEEL"

if [ ! -x "$PY" ]; then
    echo "[setup] conda 환경 $ENV_NAME 만들기 (python 3.11)"
    conda create -y -n "$ENV_NAME" python=3.11 pip
fi
echo "[setup] YuE2 실행 패키지 받기"
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/$WHEEL" "$WHEEL_URL"
echo "[setup] 설치 (torch 2.10.0 을 함께 받는다. 몇 분 걸린다)"
"$PY" -m pip install "$tmp/$WHEEL"
rm -rf "$tmp"
echo "[setup] 불러오기 확인"
"$PY" - <<'EOF'
import torch, transformers
from yue2 import YuE2Pipeline  # noqa: F401
print("ENV-READY torch", torch.__version__, "cuda", torch.cuda.is_available(),
      "bf16", torch.cuda.is_bf16_supported(), "transformers", transformers.__version__)
EOF
