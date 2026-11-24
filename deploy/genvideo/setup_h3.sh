#!/bin/bash
# 영상 생성(MiniMax H3)을 이 홈에 준비한다. 다시 돌려도 된다 (있는 것은 건너뛴다).
#
# 하는 일 세 가지:
#   1. stable-diffusion.cpp 를 CUDA 로 빌드해 bin/sd-cli 에 둔다 (2026-11-14 에 돌려 본 판으로 고정).
#   2. ffmpeg 만 든 conda 환경(media)을 만든다 (AVI → MP4, 미리보기 사진, 끝 장면 뽑기).
#   3. 모델 파일을 ~/models/minimax-h3 에 받는다 (약 58GB). H3_MODELS 로 다른 곳을 줄 수 있다.
#
# 라이선스: MiniMax H3 Community License. 유럽연합, 영국, 대한민국, 미국은 '제외 지역'이라 MiniMax 의 허가가 필요하다
# (https://platform.minimax.io/h3-license). 받기 전에 읽는다.
set -eo pipefail
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${H3_SRC:-$HOME/src/sdcpp}"
MODELS="${H3_MODELS:-$HOME/models/minimax-h3}"
SD_COMMIT=5dcfa79801213f23e27b3e83617350ebb8ea93aa
export PATH="$HOME/.local/bin:/usr/local/cuda/bin:$PATH"
. /opt/conda/etc/profile.d/conda.sh

command -v cmake >/dev/null || uv tool install cmake
command -v ninja >/dev/null || uv tool install ninja

if [ ! -x "$RUN_DIR/bin/sd-cli" ]; then
    echo "[setup] stable-diffusion.cpp 받기 ($SD_COMMIT)"
    [ -d "$SRC/.git" ] || git clone https://github.com/leejet/stable-diffusion.cpp.git "$SRC"
    git -C "$SRC" fetch --depth 1 origin "$SD_COMMIT" && git -C "$SRC" checkout -q "$SD_COMMIT"
    git -C "$SRC" submodule update --init --depth 1 ggml thirdparty/libwebp thirdparty/libwebm thirdparty/cutlass
    echo "[setup] 빌드 (RTX 3090 = CUDA 아키텍처 86, 30개 CPU 로 약 8분)"
    cmake -S "$SRC" -B "$SRC/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DSD_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH:-86}"
    cmake --build "$SRC/build" -j "$(nproc)" --target sd-cli
    mkdir -p "$RUN_DIR/bin" && cp "$SRC/build/bin/sd-cli" "$RUN_DIR/bin/"
fi

if [ ! -x "$HOME/.conda/envs/media/bin/ffmpeg" ]; then
    echo "[setup] conda 환경 media (ffmpeg) 만들기"
    conda create -y -q -p "$HOME/.conda/envs/media" -c conda-forge --override-channels ffmpeg
fi

echo "[setup] 모델 받기 → $MODELS"
mkdir -p "$MODELS" "$RUN_DIR/inputs" "$RUN_DIR/outputs"
get() { HF_HUB_OFFLINE=0 HF_HUB_DISABLE_XET=1 uvx --from huggingface_hub hf download "$@" > /dev/null; }
[ -f "$MODELS/vae/minimax_h3_video_vae_fp16.safetensors" ] || get Comfy-Org/MiniMax-H3 vae/minimax_h3_video_vae_fp16.safetensors vae/minimax_h3_audio_vae_fp32.safetensors --local-dir "$MODELS"
[ -f "$MODELS/qwen3vl_32b_minimax_h3-Q4_K_M.gguf" ] || get leejet/MiniMax-H3-GGUF qwen3vl_32b_minimax_h3-Q4_K_M.gguf --local-dir "$MODELS"
[ -f "$MODELS/minimax_h3_fl2va-Q4_K_M.gguf" ] || get leejet/MiniMax-H3-GGUF minimax_h3_fl2va-Q4_K_M.gguf --local-dir "$MODELS"
[ -f "$MODELS/hyperflow/fl2va_gguf/minimax_h3_fl2va_hyperflow_flash_14b-Q6_K.gguf" ] || get VideoRebirthAI/hyperflow-h3-flash fl2va_gguf/minimax_h3_fl2va_hyperflow_flash_14b-Q6_K.gguf LICENSE --local-dir "$MODELS/hyperflow"
[ -f "$MODELS/LICENSE" ] || get MiniMaxAI/MiniMax-H3 LICENSE --local-dir "$MODELS"
chmod +x "$RUN_DIR/h3.sh"
echo "[setup] 끝. 시험: $RUN_DIR/h3.sh --prompt-file <글 파일> --output outputs/test.mp4 --size 864x480 --seconds 2"
