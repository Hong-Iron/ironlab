#!/bin/bash
# 음성 생성(Fish Audio Drama 3)을 이 홈에 준비한다. 다시 돌려도 된다 (있는 것은 건너뛴다).
#
# 하는 일 세 가지:
#   1. audio.cpp 를 CUDA 로, Drama 3 만 넣어 빌드해 bin/audiocpp_cli 에 둔다 (2026-11-14 에 돌려 본 판으로 고정).
#   2. ffmpeg 만 든 conda 환경(media)을 만든다 (참고 목소리 바꾸기, mp3 사본).
#   3. 4비트로 줄인 모델(GGUF, 약 23GB)을 ~/models/drama3 에 받는다. DRAMA3_MODEL 로 다른 파일을 줄 수 있다.
#
# 라이선스: Fish Audio Community License. 연구·비상업은 무료, 상업은 연 매출 50만 달러 미만일 때만.
# 남에게 음성 합성·목소리 복제 서비스를 여는 것과, 동의 없이 실제 사람의 목소리를 본뜨는 것은 안 된다.
set -eo pipefail
RUN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${DRAMA3_SRC:-$HOME/src/audiocpp}"
MODELS="$HOME/models/drama3"
AC_COMMIT=d38518a0d6a7aaab9d3f3dd91e9780c69857b0ac
export PATH="$HOME/.local/bin:/usr/local/cuda/bin:$PATH"
. /opt/conda/etc/profile.d/conda.sh

command -v cmake >/dev/null || uv tool install cmake
command -v ninja >/dev/null || uv tool install ninja

if [ ! -x "$RUN_DIR/bin/audiocpp_cli" ]; then
    echo "[setup] audio.cpp 받기 ($AC_COMMIT)"
    [ -d "$SRC/.git" ] || git clone https://github.com/0xShug0/audio.cpp.git "$SRC"
    git -C "$SRC" fetch --depth 1 origin "$AC_COMMIT" && git -C "$SRC" checkout -q "$AC_COMMIT"
    echo "[setup] 빌드 (Drama 3 만, CUDA 아키텍처 86, 30개 CPU 로 약 5분)"
    (cd "$SRC" && bash scripts/build_linux.sh --cuda on --cuda-arch "${CUDA_ARCH:-86}" --build-type Release \
        --model-set custom --models fish_drama3 --target audiocpp_cli -j "$(nproc)")
    mkdir -p "$RUN_DIR/bin" && cp "$SRC/build/linux-cuda-release/bin/audiocpp_cli" "$RUN_DIR/bin/"
fi

if [ ! -x "$HOME/.conda/envs/media/bin/ffmpeg" ]; then
    echo "[setup] conda 환경 media (ffmpeg) 만들기"
    conda create -y -q -p "$HOME/.conda/envs/media" -c conda-forge --override-channels ffmpeg
fi

mkdir -p "$MODELS" "$RUN_DIR/inputs" "$RUN_DIR/outputs"
if [ ! -f "$MODELS/drama-3-backbone-8bit-experts-Q4_K_S.gguf" ]; then
    echo "[setup] 모델 받기 → $MODELS (약 23GB)"
    HF_HUB_OFFLINE=0 HF_HUB_DISABLE_XET=1 uvx --from huggingface_hub hf download Jmica/Drama-3-GGUF \
        drama-3-backbone-8bit-experts-Q4_K_S.gguf LICENSE NOTICE --local-dir "$MODELS" > /dev/null
fi
chmod +x "$RUN_DIR/drama3.sh"
echo "[setup] 끝. 시험: echo '안녕하세요. 시험입니다.' > inputs/t.txt && $RUN_DIR/drama3.sh --text-file inputs/t.txt --output outputs/t.wav"
