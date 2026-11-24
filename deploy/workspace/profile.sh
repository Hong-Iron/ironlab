# ironlab 워크스페이스 공통 셸 환경 (sh 호환 문법으로 유지할 것).
#
# sshd 로 들어온 세션은 이미지의 ENV(PATH 등)를 물려받지 않는다. 그래서 여기서 다시 잡아 준다.
# 로그인 셸(/etc/profile.d) 뿐 아니라 `ssh 호스트 명령`, VS Code 원격 터미널 같은
# 비로그인 bash(/etc/bash.bashrc 첫 줄)에서도 불러온다.

if [ -z "${IRONLAB_PROFILE:-}" ]; then
    export IRONLAB_PROFILE=1
    export CUDA_HOME=/usr/local/cuda TORCH_CUDA_ARCH_LIST=8.6
    export HF_HUB_CACHE=/shared/models/hub TORCH_HOME=/shared/models/torch
    # 컨테이너 환경변수 (HF 캐시, 로컬 LLM 주소 등. 진입 스크립트가 만든다)
    [ -r /etc/ironlab-env.sh ] && . /etc/ironlab-env.sh
    PATH="$HOME/.local/bin:/opt/conda/bin:/usr/local/cuda/bin:$PATH"
    export PATH
fi

# conda 셸 함수는 자식 셸에 상속되지 않으므로 셸마다 정의한다 (conda activate 용)
[ -r /opt/conda/etc/profile.d/conda.sh ] && . /opt/conda/etc/profile.d/conda.sh
:
