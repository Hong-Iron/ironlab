# ironlab 워크스페이스 베이스 이미지
#   docker build -t ironlab/workspace:latest deploy/workspace                                   # NVIDIA
#   docker build -t ironlab/workspace:latest --build-arg CUDA_ARCH=8.9 deploy/workspace         # 카드의 compute capability 에 맞춰
#   docker build -t ironlab/workspace:latest --build-arg BASE_IMAGE=rocm/dev-ubuntu-22.04:6.4.4 deploy/workspace   # AMD
#
# - NVIDIA: CUDA 12.4 개발 도구 + GCC 11 (TRELLIS.2 계열 CUDA 확장 빌드용)
# - AMD: ROCm 개발 도구 (hipcc, rocm-smi). PyTorch 는 pip 의 ROCm 판을 쓴다
# - Miniforge(conda) 는 /opt/conda 에 설치, 사용자 conda 환경은 홈(~/.conda)에 생긴다
# - 컨테이너 안 sshd 로 접속 (키는 ironlab 이 /etc/ironlab-keys 로 마운트)
ARG BASE_IMAGE=nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
FROM ${BASE_IMAGE}

# CUDA 확장을 빌드할 GPU 세대 (nvidia-smi --query-gpu=compute_cap). 3090 8.6, 4090 8.9, H100 9.0, 5090 12.0
ARG CUDA_ARCH=8.6
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    TZ=Asia/Seoul \
    CONDA_DIR=/opt/conda \
    PATH=/opt/conda/bin:$PATH \
    TORCH_CUDA_ARCH_LIST=${CUDA_ARCH} \
    MAX_JOBS=8

# 80 포트가 막힌 네트워크가 있어 우분투 저장소를 HTTPS 미러로 바꾼다.
# 다른 미러를 쓰려면: docker build --build-arg UBUNTU_MIRROR=https://ftp.kaist.ac.kr/ubuntu ...
ARG UBUNTU_MIRROR=https://mirror.kakao.com/ubuntu
RUN sed -i -E "s#http://(archive|security)\.ubuntu\.com/ubuntu#${UBUNTU_MIRROR}#g" /etc/apt/sources.list

# GPU 종류별 셸 환경. sshd 세션은 이미지의 ENV 를 물려받지 않아 profile.sh 가 이 파일을 읽는다
RUN if [ -d /usr/local/cuda ]; then \
        printf 'export CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST=%s\nPATH="/usr/local/cuda/bin:$PATH"\n' "${CUDA_ARCH}"; \
    elif [ -d /opt/rocm ]; then \
        printf 'export ROCM_PATH=/opt/rocm HIP_PATH=/opt/rocm\nPATH="/opt/rocm/bin:$PATH"\n'; \
    fi > /etc/ironlab-gpu.sh && chmod 644 /etc/ironlab-gpu.sh

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server ca-certificates curl wget git git-lfs build-essential ninja-build \
        tmux htop nvtop vim nano unzip rsync less tzdata \
        libgl1 libglib2.0-0 libegl1 libgles2 libjpeg-dev libpng-dev \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd

# latest 는 Python 3.14 기반 conda 가 패키지 설치 중 KeyError 로 실패하는 사례가 있어 버전을 고정한다 (base Python 3.12)
ARG MINIFORGE_VERSION=24.11.3-0
RUN curl -fsSL -o /tmp/miniforge.sh \
        https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh \
    && bash /tmp/miniforge.sh -b -p $CONDA_DIR && rm /tmp/miniforge.sh \
    && $CONDA_DIR/bin/conda clean -afy \
    && echo '. /opt/conda/etc/profile.d/conda.sh' > /etc/profile.d/10-conda.sh \
    && echo 'export PATH=/opt/conda/bin:$PATH CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST=8.6' > /etc/profile.d/11-ironlab.sh \
    && echo 'export HF_HUB_CACHE=/shared/models/hub TORCH_HOME=/shared/models/torch' >> /etc/profile.d/11-ironlab.sh

# 사용자 conda 환경·패키지 캐시는 홈에 (컨테이너를 지워도 유지)
# (Miniforge 기본 .condarc 의 conda-forge 채널 설정을 덮어쓰지 않도록 channels 를 함께 적는다)
# pinned_packages: 사용자 환경도 pip 26.2.x 버그를 피하도록 pip<25 로 고정
RUN printf 'channels:\n  - conda-forge\npinned_packages:\n  - pip<25\nenvs_dirs:\n  - ~/.conda/envs\n  - /opt/conda/envs\npkgs_dirs:\n  - ~/.conda/pkgs\n' > /opt/conda/.condarc

# 개발 도구: GitHub CLI, uv. GitHub 릴리스에서 받는다 (HTTPS 라 80 포트 차단과 무관).
# 다른 버전: docker build --build-arg GH_VERSION=x.y.z ...
ARG GH_VERSION=2.83.0
RUN set -eux; cd /tmp; \
    curl -fsSL -o gh.tgz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz"; \
    tar -xzf gh.tgz; install -m 755 "gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh; \
    curl -fsSL -o uv.tgz https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz; \
    tar -xzf uv.tgz; install -m 755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/; \
    rm -rf /tmp/gh* /tmp/uv*; \
    gh --version; uv --version

# 셸 환경: sshd 세션은 이미지의 ENV(PATH 등)를 물려받지 않으므로 셸 시작 파일에서 다시 잡는다.
# - 로그인 셸: /etc/profile.d/10-ironlab.sh
# - 그 밖의 bash (`ssh 호스트 명령`, VS Code 터미널): /etc/bash.bashrc 첫 줄
# (위 Miniforge 단계가 만든 10-conda.sh, 11-ironlab.sh 는 이 파일로 대체한다)
COPY profile.sh /etc/ironlab-profile.sh
COPY first-login.sh /etc/profile.d/30-ironlab-first-login.sh
COPY welcome.txt /etc/ironlab-welcome
RUN rm -f /etc/profile.d/10-conda.sh /etc/profile.d/11-ironlab.sh \
    && echo '. /etc/ironlab-profile.sh' > /etc/profile.d/10-ironlab.sh \
    && sed -i '1i [ -r /etc/ironlab-profile.sh ] \&\& . /etc/ironlab-profile.sh' /etc/bash.bashrc \
    && chmod 644 /etc/ironlab-profile.sh /etc/profile.d/30-ironlab-first-login.sh /etc/ironlab-welcome

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /usr/local/bin/ironlab-entrypoint
RUN chmod 755 /usr/local/bin/ironlab-entrypoint

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/ironlab-entrypoint"]
