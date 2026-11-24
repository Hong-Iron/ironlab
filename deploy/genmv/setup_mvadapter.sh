#!/bin/bash
# MV-Adapter 를 한 사용자 홈에 설치한다 (처음 한 번). 설치한 뒤에는 관리자가 공용으로 옮긴다:
#   sudo bash deploy/shared-apps.sh --migrate-from <그 사용자>
# 만드는 것
#   ~/.conda/envs/mvadapter          파이썬 3.10, torch 2.5.1(CUDA 12.4), diffusers 0.34.0 … (약 5.9 GB)
#   ~/mvadapter-run/MV-Adapter       huanngzh/MV-Adapter 코드 (커밋 7d37a97 에 고정, Apache-2.0)
#   ~/models/mvadapter-hub           SDXL 1.0 반정밀도 파일, sdxl-vae-fp16-fix, mvadapter_i2mv_sdxl.safetensors (약 11 GB)
# 배경 지우기 모델(ZhengPeng7/BiRefNet)은 공용 hub 의 것을 쓴다 (Pixal3D 와 같다).
set -euo pipefail
export PYTHONNOUSERSITE=1
. /opt/conda/etc/profile.d/conda.sh
RUN_DIR="$HOME/mvadapter-run"
mkdir -p "$RUN_DIR/inputs" "$RUN_DIR/outputs" "$HOME/models/mvadapter-hub"

[ -d "$RUN_DIR/MV-Adapter" ] || git clone -q https://github.com/huanngzh/MV-Adapter.git "$RUN_DIR/MV-Adapter"
git -C "$RUN_DIR/MV-Adapter" checkout -q 7d37a97

[ -d "$HOME/.conda/envs/mvadapter" ] || conda create -y -q -p "$HOME/.conda/envs/mvadapter" python=3.10 >/dev/null
conda activate "$HOME/.conda/envs/mvadapter"
pip install -q torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
# 저장소 requirements.txt 는 판을 고정하지 않는다. 코드(2025-06)와 같은 때의 판으로 고정한다.
# matplotlib·jaxtyping·typeguard 는 requirements.txt 에 없지만 mvadapter.utils 가 부른다.
pip install -q "diffusers==0.34.0" "transformers==4.52.4" "huggingface_hub==0.33.1" "accelerate==1.8.1" "peft==0.15.2" \
    safetensors einops omegaconf timm kornia opencv-python-headless scikit-image sentencepiece pillow "numpy<2.1" trimesh \
    matplotlib jaxtyping typeguard

HF_HUB_OFFLINE=0 HF_HUB_DISABLE_XET=1 python - <<'EOF'
import os
from huggingface_hub import snapshot_download
cache = os.path.expanduser("~/models/mvadapter-hub")
for repo, pats in [
    ("madebyollin/sdxl-vae-fp16-fix", ["config.json", "diffusion_pytorch_model.safetensors"]),
    ("huanngzh/mv-adapter", ["mvadapter_i2mv_sdxl.safetensors", "README.md"]),
    ("stabilityai/stable-diffusion-xl-base-1.0", ["model_index.json", "*/config.json", "*/*.fp16.safetensors",
                                                 "tokenizer/*", "tokenizer_2/*", "scheduler/*"]),
]:
    print("받음:", repo, snapshot_download(repo, allow_patterns=pats, cache_dir=cache), flush=True)
EOF
python -c "import torch, diffusers, transformers; print('torch', torch.__version__, torch.cuda.is_available(), 'diffusers', diffusers.__version__, 'transformers', transformers.__version__)"
echo "MVADAPTER-SETUP-OK"
