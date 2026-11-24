# TRELLIS.2 / Pixal3D 의존성을 미리 빌드해 둔 이미지 (사용자마다 flash-attn을 30분씩 컴파일하지 않도록)
#   docker build -t ironlab/workspace:trellis deploy/trellis
#   설정에서 workspace_image = "ironlab/workspace:trellis" 로 지정
#
# 사용자는 컨테이너 안에서:  conda activate trellis2
# 모델 가중치는 관리자가 /srv/shared/models/hub 에 한 번만 받아 둔다 (hf download …).
# 빌드에는 GPU가 필요 없지만 시간이 오래 걸린다 (flash-attn 빌드 30분+).
FROM ironlab/workspace:latest

ARG TRELLIS_REF=main
SHELL ["/bin/bash", "-lc"]

# pip(24.3.1, 26.2.1 모두)가 PyTorch 인덱스에서 후보를 정렬하다 TypeError 로 죽어서 torch 는 uv 로 설치한다
RUN conda create -y -p /opt/conda/envs/trellis2 python=3.10 pip=24.3.1 \
    && /opt/conda/envs/trellis2/bin/python -m pip install --no-cache-dir uv \
    && /opt/conda/envs/trellis2/bin/uv pip install --python /opt/conda/envs/trellis2/bin/python --no-cache \
        torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124 \
    && /opt/conda/envs/trellis2/bin/python -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda)"

# setup.sh 가 실패하면 빌드도 실패해야 한다 (|| true 로 삼키지 않는다)
RUN git clone -b ${TRELLIS_REF} --recursive https://github.com/microsoft/TRELLIS.2.git /opt/TRELLIS.2 \
    && cd /opt/TRELLIS.2 \
    && source /opt/conda/etc/profile.d/conda.sh && conda activate /opt/conda/envs/trellis2 \
    && export CUDA_HOME=/usr/local/cuda \
    && . ./setup.sh --basic --flash-attn --nvdiffrast --nvdiffrec --cumesh --o-voxel --flexgemm \
    && python -c "import importlib.util as u; missing=[m for m in ('torch','flash_attn','nvdiffrast','cumesh','o_voxel','flex_gemm') if u.find_spec(m) is None]; print('missing:', missing)" \
    && (pip cache purge || true)

RUN git clone https://github.com/TencentARC/Pixal3D.git /opt/Pixal3D \
    && source /opt/conda/etc/profile.d/conda.sh && conda activate /opt/conda/envs/trellis2 \
    && if [ -f /opt/Pixal3D/requirements.txt ]; then pip install --no-cache-dir -r /opt/Pixal3D/requirements.txt; fi

# setup.sh의 플래그는 저장소 버전에 따라 바뀔 수 있으니 빌드 실패 시 README를 확인하세요.
