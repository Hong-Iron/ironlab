# ironlab

NVIDIA GPU 한 장을 여러 명이 같이 쓰는 서버를 위한 관리 도구입니다. 연구실·동아리·팀의 GPU 서버에 설치하면, 그 서버의 사용자들이 웹 콘솔과 CLI 로 GPU 를 나눠 씁니다.

- **사용자 워크스페이스** — 사람마다 GPU가 붙은 Docker 컨테이너를 하나씩. CPU·RAM 한도, 개인 홈, SSH 접속
- **GPU 작업 큐** — 필요 VRAM을 선언하고 줄을 서면, 자리가 날 때 자동 실행
- **모니터링** — 누가 접속했고, 누가 CPU·RAM·VRAM을 얼마나 쓰는지 (GPU 프로세스를 사용자까지 추적)
- **파일 관리** — 개인 홈(쿼터)·스크래치·공용 모델/데이터셋, 웹·CLI 업로드/다운로드
- **로컬 LLM** — llama.cpp 서버 하나를 모두가 OpenAI 호환 API 로 공유
- **생성 화면 (선택)** — 그림·3D·음악·영상·음성·효과음 생성 모델을 웹 양식과 노드 편집기로

웹 대시보드와 `ironlab` CLI가 같은 REST API(`/api/docs`)를 씁니다.

```
노트북 ──(Tailscale / 같은 네트워크 / HTTPS 프록시)──▶ ironlab 콘솔·API :8700
   │                                                    ├─ Docker: ironlab-ws-<사용자>, ironlab-job-<id>, ironlab-llm
   └──────────────── ssh -p 220xx dev@서버 ──────────▶ 사용자 워크스페이스 컨테이너
```

---

## 설치

### 필요한 것

| | |
|---|---|
| 운영체제 | Ubuntu 22.04 / 24.04, Debian 12, Arch Linux (다른 배포판은 아래 `SKIP_PACKAGES=1`) |
| GPU | NVIDIA GPU 와 드라이버. `nvidia-smi` 가 GPU 를 보여야 합니다. 생성 화면의 기본값은 24GB 카드에 맞춰져 있습니다 ([GPU 가 다르면](#gpu-가-다르면)) |
| 디스크 | 이미지·모델에 수백 GB. 큰 디스크가 따로 있으면 `BIG=<마운트 위치>` |
| 네트워크 | 사용자가 서버에 닿을 방법 하나: Tailscale, 같은 네트워크(LAN), 또는 HTTPS 리버스 프록시 |

Docker, NVIDIA Container Toolkit, Python 3.12 는 설치 스크립트가 깝니다.

### 빠른 설치

```bash
git clone https://github.com/Hong-Iron/ironlab.git
cd ironlab
sudo ADMIN=admin SITE_NAME="우리 연구실 GPU" bash deploy/install.sh
```

끝나면 콘솔 주소와 관리자 접근 토큰이 화면에 나옵니다. 토큰은 이때 한 번만 보이니 적어 두세요. 잃어버리면 서버에서 `sudo IRONLAB_CONFIG=/etc/ironlab/config.toml /opt/ironlab/venv/bin/ironlab-server reset-token admin` 으로 다시 받습니다. 스크립트는 여러 번 실행해도 안전합니다.

| 옵션 (환경변수) | 뜻 |
|---|---|
| `ADMIN=이름` | 처음 만들 관리자. 소문자로 시작하는 영문·숫자·`-`·`_` |
| `ACCESS=auto` | 콘솔을 여는 방식. 아래 [접속 방식](#접속-방식) |
| `SITE_NAME="…"` | 로그인 화면·옆 메뉴·브라우저 탭에 보일 이름 |
| `TS_LOGIN=name@github` | (Tailscale) 관리자의 Tailscale 계정. 토큰 없이 들어옵니다 |
| `PUBLIC_HOST=…` `PUBLIC_URL=…` | 사용자에게 안내할 서버 주소·콘솔 주소 (도메인이 있거나 프록시를 붙일 때) |
| `PORT=8700` | 콘솔 포트 |
| `GPU_INDEX=0` | GPU 가 여러 장이면 맡을 GPU 번호 (`nvidia-smi -L` 순서) |
| `BIG=/data` | 큰 디스크. Docker·containerd·`/srv` 를 그 위에 bind mount 합니다 (기본: 옮기지 않음) |
| `WITH_TRELLIS=1` | TRELLIS.2/Pixal3D 가 든 워크스페이스 이미지까지 빌드 (1시간 이상) |
| `SKIP_PACKAGES=1` | 패키지 설치를 건너뜀. Docker(buildx)·NVIDIA Container Toolkit·curl·rsync·python3 를 직접 깔았을 때 |

`SITE_NAME`, `ACCESS`, `PUBLIC_*`, `PORT`, `GPU_INDEX` 는 설정 파일을 처음 만들 때만 씁니다. 나중에 바꾸려면 `/etc/ironlab/config.toml` 을 고치고 `sudo systemctl restart ironlab`.

### 접속 방식

| `ACCESS` | 콘솔 주소 | 로그인 | 언제 |
|---|---|---|---|
| `tailscale` | `https://<서버>.<tailnet>.ts.net` (tailnet 안에서만) | Tailscale 계정으로 자동, 또는 토큰 | 집·밖에서도 써야 할 때. 가장 권장 |
| `lan` | `http://<서버 IP>:8700` | 접근 토큰 | 모두 같은 사설망(공유기·사내망) 안에 있을 때 |
| `local` | `127.0.0.1:8700` 에만 열림 → 직접 붙인 HTTPS 프록시 주소 | 접근 토큰 | 도메인과 인증서가 있을 때 |
| `auto` (기본) | Tailscale 에 로그인돼 있으면 `tailscale`, 사설 IP 면 `lan` | | 공인 IP 서버에서는 고르라고 멈춥니다 |

- **tailscale:** 서버에 [Tailscale](https://tailscale.com/download/linux) 을 깔고 `sudo tailscale up` 을 한 뒤 설치합니다. 관리 콘솔에서 MagicDNS 와 HTTPS 인증서를 켜 두세요. 스크립트가 `tailscale serve --bg 8700` 으로 엽니다. 워크스페이스 SSH 도 Tailscale IP 에만 열립니다.
- **lan:** 암호화 없는 http 입니다. 접근 토큰이 망 안에서 그대로 오가므로, 밖에서 닿지 않는 망에서만 쓰세요. 방화벽(ufw·firewalld)이 켜져 있으면 스크립트가 열 명령을 알려 줍니다.
- **local + 리버스 프록시:** 예를 들어 Caddy 는 아래 두 줄이면 인증서까지 받습니다. `PUBLIC_URL=https://gpu.example.ac.kr` 를 함께 주면 화면의 안내 주소가 맞습니다.

  ```
  gpu.example.ac.kr {
      reverse_proxy 127.0.0.1:8700
  }
  ```

  nginx 는 업로드 크기 제한과 응답 버퍼를 풀어야 합니다 (`client_max_body_size 0; proxy_buffering off; proxy_read_timeout 1h;`). LLM 채팅과 로그가 흘러나오는 응답을 씁니다.

### 사용자 추가

콘솔 > 사용자 > 추가에서 이름과 한도를 넣으면 접근 토큰이 나옵니다. 그 토큰을 본인에게 전해 주세요. 사용자는 로그인한 뒤 SSH 공개키를 등록하고 워크스페이스를 켭니다. CLI 로는 아래 [사용법](#사용법).

### 업데이트

```bash
cd ironlab && git pull
sudo bash deploy/install.sh
```

설정 파일, 사용자, 홈의 파일, 작업 기록은 그대로입니다. 서비스가 다시 뜨는 몇 초 동안 콘솔이 끊기고, 실행 중인 워크스페이스·작업은 계속 돕니다 (Docker 설정이 바뀐 경우만 예외이며 스크립트가 미리 알립니다).

### GPU 가 다르면

- **용량:** 큐는 GPU 용량을 직접 읽습니다(`gpu_total_vram_gb = 0`). VRAM 알림도 용량의 92% 에서 울립니다.
- **24GB 보다 작은 카드 (12·16GB):** 워크스페이스, 작업 큐, 로컬 LLM 은 그대로 씁니다. 생성 화면은 모델마다 예약 VRAM(`gen*_vram_gb`, 4~22GB)이 정해져 있고, 카드보다 큰 예약은 제출할 때 "VRAM은 … 이하여야 합니다" 로 거절됩니다. 그 생성기는 켜지 않거나, 모델이 실제로 들어가는 설정을 찾은 뒤 예약 값을 낮추세요.
- **24GB 보다 큰 카드:** 바꿀 것이 없습니다. 여러 작업이 동시에 더 많이 돕니다.
- **여러 장:** ironlab 은 `gpu_index` 한 장만 맡습니다. 컨테이너에도 그 한 장만 보입니다. 나머지 GPU 는 ironlab 밖에서 쓸 수 있습니다.
- **MIG·vGPU 없는 카드:** VRAM 을 하드웨어로 나눌 수 없어 아래의 예약 방식으로 나눕니다.

### 손으로 설치하기

설치 스크립트가 하는 일을 나눠 적으면 이렇습니다.

1. **Docker + NVIDIA Container Toolkit:** 배포판에 맞게 [Docker](https://docs.docker.com/engine/install/)와 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)을 깔고 연결합니다.
   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   sudo systemctl restart docker
   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi   # GPU가 보이면 OK
   ```
2. **디렉터리:**
   ```bash
   sudo mkdir -p /srv/users /srv/shared/models/hub /srv/shared/models/llm /srv/shared/datasets /srv/scratch /etc/ironlab
   ```
   홈 쿼터는 **업로드할 때만 검사하는 소프트 쿼터**입니다. 컨테이너 안에서 직접 쓰는 양은 막지 못하고, 대시보드에 사용량으로만 드러납니다. 강제하려면 파일시스템 쿼터가 필요합니다: ext4는 프로젝트 쿼터(`tune2fs -O project,quota` 후 `prjquota` 마운트), btrfs는 사용자 홈을 서브볼륨으로 만들고 qgroup을 겁니다. 설치 스크립트는 이 설정을 하지 않습니다.
3. **이미지:**
   ```bash
   docker build -t ironlab/workspace:latest deploy/workspace
   docker build -t ironlab/workspace:trellis deploy/trellis    # TRELLIS.2·Pixal3D 사전 빌드 (선택, 오래 걸림)
   ```
4. **ironlab:** Python 3.11 이상이 필요합니다. [uv](https://docs.astral.sh/uv/) 로 3.12 를 받아 쓰는 것이 간단합니다.
   ```bash
   sudo uv venv --python 3.12 /opt/ironlab/venv
   sudo uv pip install --python /opt/ironlab/venv/bin/python .
   sudo cp deploy/config.example.toml /etc/ironlab/config.toml      # 접속 방식에 맞게 위쪽 값을 고침
   sudo IRONLAB_CONFIG=/etc/ironlab/config.toml /opt/ironlab/venv/bin/ironlab-server create-admin admin
   sudo cp deploy/ironlab.service /etc/systemd/system/
   sudo systemctl enable --now ironlab
   ```
5. **공개:** Tailscale 이면 `sudo tailscale serve --bg 8700`. 다른 방식은 [접속 방식](#접속-방식).

### 보안 메모

- Tailscale 헤더는 같은 기계(127.0.0.1)에서 넘어온 요청에서만, 그리고 `trust_tailscale_headers = true` 일 때만 믿습니다. 이때 API는 `127.0.0.1`에만 바인딩해 두세요.
- 다른 웹사이트의 페이지가 브라우저를 통해 보낸 변경 요청(업로드, 중지 등)은 거절합니다.
- `/metrics`는 서버 안에서 직접 긁을 때만 로그인 없이 열리고, 프록시나 네트워크를 거치면 관리자 로그인이 필요합니다.
- 웹 파일 관리자는 홈·스크래치 안의 심볼릭 링크를 따라가지 않습니다(목록에는 보이지만 열 수 없음). 공용 영역은 영역 안을 가리키는 링크만 따라갑니다.
- 사용자를 `docker` 그룹에 넣지 마세요 (사실상 root). 컨테이너는 ironlab만 만듭니다.
- 컨테이너는 `cap_drop=ALL`, `no-new-privileges` 로 뜨며 sudo가 없습니다. 워크스페이스 SSH 는 공개키로만 들어갑니다. 패키지는 conda로 홈에 설치합니다.
- 더 강하게 격리하려면 dockerd의 `userns-remap` 을 고려하세요 (사용자 UID 매핑을 함께 조정해야 함).
- 다른 사람의 작업 명령·로그는 볼 수 없고, 목록에는 이름·VRAM·상태만 보입니다.

### 공용 모델 채우기
```bash
sudo HF_HUB_CACHE=/srv/shared/models/hub /opt/ironlab/venv/bin/hf download microsoft/TRELLIS.2-4B
sudo chmod -R a+rX /srv/shared
```
컨테이너는 `HF_HUB_CACHE=/shared/models/hub`, `HF_HUB_OFFLINE=1` 로 뜨므로 사용자는 다운로드 없이 바로 씁니다.

---

## GPU를 어떻게 나누나

GeForce 계열(3090·4090 등)은 MIG(하드웨어 분할)가 없어서 VRAM을 강제로 쪼갤 수 없습니다. 그래서 ironlab은 **예약 + 감시** 방식을 씁니다.

| 상황 | 동작 |
|---|---|
| 작업 제출 | "이 작업은 VRAM 12GB 필요"처럼 선언 |
| 실행 판단 | `여유 = GPU 전체 − 안전여유(1) − 실행 중 작업 예약 − 큐 밖 사용량(워크스페이스 노트북 등)` 이 요청보다 크면 시작 |
| 순서 | 먼저 낸 작업 우선. 큰 작업이 못 돌면 뒤의 작은 작업을 끼워 넣음(backfill) |
| 기아 방지 | 맨 앞 작업이 30분 넘게 기다리면 끼워넣기를 멈추고 그 작업에 자리를 몰아줌 |
| 공정성 | 사용자당 동시 실행 1개(설정 가능), 사용자별 VRAM 상한 설정 가능 |
| 독점 | `--exclusive` 작업은 다른 작업이 다 끝난 뒤 GPU를 혼자 씀 |
| 약속 위반 | 선언보다 많이 쓰면 이벤트 로그에 경고(`warn`) 또는 종료(`kill`) |

실제 사용량은 NVML의 GPU 프로세스 PID → `/proc/<pid>/cgroup` → 컨테이너 → 사용자로 추적합니다. 큐 밖에서 GPU를 쓰는 프로세스도 대시보드에 "ironlab 외부"로 드러납니다.

워크스페이스 안에서 노트북으로 가볍게 GPU를 쓰는 건 막지 않습니다(그 사용량만큼 큐의 여유가 줄어듭니다). 무거운 작업은 큐로 보내는 걸 규칙으로 정해 주세요.

## 먼저 돌려보기 (GPU·Docker 없이)

설치 전에 화면을 보고 싶으면 노트북에서 mock 서버를 띄웁니다 (Python 3.11 이상).

```bash
pip install -e .
ironlab-server demo          # 가짜 사용자 3명 + 작업 4개로 mock 서버 실행, 토큰 출력
# 브라우저: http://127.0.0.1:8700  (admin 토큰으로 로그인)
```

## 생성기 켜기 (선택)

생성 화면(그림·3D·음악·영상·음성·효과음·소리 다듬기·여러 방향 그림)은 서버에 모델과 실행 환경이 있어야 켜집니다. 설치 스크립트는 이것을 받지 않습니다. 모델마다 수~수십 GB 이고 라이선스가 따로 있으니(아래 각 절), 필요한 것만 고르세요. ironlab 의 MIT 라이선스는 이 저장소의 코드에만 해당합니다.

1. 관리자 워크스페이스에서 아래 각 생성기 절의 **준비**대로 환경과 모델을 받고, `deploy/gen*/` 의 실행 스크립트를 홈의 생성기 폴더에 둡니다. 그 사용자에게 화면이 켜지면 한 번 돌려 봅니다.
2. 모두에게 엽니다. 관리자 홈의 모델·환경을 공용(`/srv/shared/models`)으로 옮기고 사용자 홈마다 링크를 겁니다.
   ```bash
   sudo bash deploy/shared-apps.sh --migrate-from admin
   ```
3. 그 뒤로는 `deploy/install.sh` 를 다시 돌릴 때마다 실행 스크립트를 새 판으로 바꾸고, 새로 추가한 사용자에게도 링크를 겁니다.

---

## 로컬 LLM

llama.cpp 서버 하나를 모두가 공유합니다. 콘솔의 **로컬 LLM** 페이지에서 모델을 올리고 내리며, 채팅으로 바로 써 볼 수 있습니다.

- **VRAM:** 올릴 때 GPU 큐의 여유를 확인하고, 올라가 있는 동안은 그만큼 큐 예약에서 빠집니다. `llm_idle_unload_min`(기본 20분) 동안 아무도 쓰지 않으면 자동으로 내려 3D 작업에 GPU를 돌려줍니다.
- **API:** OpenAI 호환. 노트북에서는 `<콘솔 주소>/v1` + 접근 토큰(또는 Tailscale 계정). 콘솔의 로컬 LLM 화면에 주소가 나옵니다. 워크스페이스·작업 컨테이너 안에는 `OPENAI_BASE_URL`이 미리 들어 있고, 컨테이너 IP로 사용자를 알아보므로 키가 필요 없습니다(내부 프록시는 ironlab Docker 네트워크 게이트웨이 `172.30.0.1:8701`에만 열림).
- **사용량:** 사용자별 요청 수와 입력·출력 토큰이 남습니다. 대화 내용은 저장하지 않습니다.
- **컨텍스트 길이:** 요청 하나가 쓸 수 있는 길이는 `컨텍스트 ÷ 동시 요청 수`입니다(32,768 토큰에 동시 요청 2개면 요청당 16,384 토큰). 이 값은 `/v1/models` 응답의 `context_length`와 `meta.n_ctx`에 실려 나가므로, 이 값을 읽는 에이전트 도구는 길이를 따로 적지 않아도 됩니다.
- **설정 고치기:** 모델 표의 **수정**에서 컨텍스트, 동시 요청 수, VRAM 예약, 추가 인자를 바꿉니다. 올라가 있는 모델은 고칠 수 없으므로 먼저 내립니다. 바꾼 설정은 다음에 올릴 때부터 쓰입니다.
- **실제 VRAM:** 상태 패널에 지금 실제로 쓰는 GPU 메모리가 나오고, 모델 표에는 올렸을 때의 최댓값이 남습니다. 컨텍스트를 늘리기 전에 이 값과 GPU 전체 용량의 차이를 보면 여유를 가늠할 수 있습니다. 컨텍스트나 파일을 바꾸면 이 기록은 지워지고 다음에 올릴 때 다시 잽니다.

컨텍스트를 늘리는 순서: 모델을 내린다 → **수정**에서 동시 요청 수를 1로 줄이거나 컨텍스트를 키운다 → 다시 올린다 → 상태 패널의 실제 VRAM을 본다. GPU 메모리가 모자라면 llama-server가 뜨지 못하고 상태가 **오류**로 바뀝니다. 그때는 컨텍스트를 줄이거나, 추가 인자에 `-ctk q8_0 -ctv q8_0`(대화 기억용 메모리를 절반쯤으로 줄임)을 넣습니다.

```bash
# 모델 받기 (Qwen3.8-27B 4bit, 약 17GB) — 공식 Qwen 조직이나 unsloth/bartowski 같은 검증된 곳에서만
sudo HF_HUB_CACHE=/tmp/hf /opt/ironlab/venv/bin/hf download unsloth/Qwen3.8-27B-GGUF \
     --include "*UD-Q4_K_XL*" --local-dir /srv/shared/models/llm/qwen3.8-27b
# 콘솔 > 로컬 LLM > 모델 등록 (VRAM 20, 컨텍스트 32768, 동시 요청 2) → 모델 올리기
ironlab llm chat "안녕"
```

## 생성 화면 (Easy 메뉴)

콘솔 왼쪽의 "생성 · Easy" 아래에 3D 생성, 이미지 생성, 음악 생성, 영상 생성, 음성 생성, 효과음 생성, 소리 다듬기 화면이 있습니다. 일곱 화면은 같은 틀로 돕니다. 화면은 올린 파일과 적은 글을 사용자 홈에 저장하고 GPU 작업 하나를 큐에 넣습니다. 모델을 돌리는 일은 사용자 홈의 실행 스크립트가 합니다. 그래서 순서 기다리기, VRAM 예약, 로그, 취소는 "GPU 작업"과 똑같이 동작합니다.

화면마다 양식은 두 층입니다.

- **기본 양식:** 꼭 필요한 것만 있습니다 (사진이나 글, 시드, 만들 개수). 이것만 채워도 만들어집니다.
- **상세 설정:** 모델이 결과를 만드는 단계마다 조절 값을 묶어 둔 것입니다. 3D 는 사진 읽기 → 뼈대 잡기 → 형상 만들기 → 색 입히기 → GLB 뽑기, 이미지는 그리기 → 그림으로 풀어내기, 음악은 악보 쓰기 → 노래 만들기 → 소리로 바꾸기, 영상은 영상 만들기 → 풀어내기와 저장, 음성은 말하기 → 메모리와 속도, 효과음은 소리 만들기 → 게임에 쓰기 좋게 다듬기 → 영상에 소리 붙이기, 소리 다듬기는 목소리와 반주 나누기 → 목소리 다듬기 → 다시 섞기 → 마무리입니다. 값마다 무엇이 바뀌는지와 쓸 만한 범위가 옆에 적혀 있고, 기본값에서 바꾼 칸에는 "바꿈" 표시가 붙습니다.

양식 둘레의 기능은 세 가지입니다.

- **만들 개수:** 2 이상을 넣으면 시드를 1씩 올려 가며 그만큼 큐에 넣습니다 (최대 8개). 여러 개를 만들어 놓고 고를 때 씁니다. 시드가 없는 소리 다듬기에는 이 칸이 없습니다 (같은 입력이면 같은 결과).
- **지금 설정 저장 / 불러오기:** 조절 값을 이름을 붙여 저장해 둡니다 (사용자마다, 종류마다 40개까지). 글과 올린 파일은 저장하지 않습니다.
- **이 설정으로 다시:** 만든 항목의 글과 조절 값을 양식에 다시 채웁니다. 올렸던 사진, 소리 파일, 영상도 홈에서 다시 가져옵니다. 한두 값만 고쳐 다시 만들 때 씁니다.

조절 값의 목록은 서버의 `ironlab/gen.py` 한 곳에 있습니다 (`KINDS`). 화면은 `/api/gen/<종류>` 가 돌려주는 그 정의를 읽어 입력 칸을 그리고, 서버는 같은 정의로 받은 값을 검사합니다. 값을 더하려면 `Param` 한 줄을 더하고 실행 스크립트가 그 옵션을 받게 하면 됩니다. 화면 코드는 고치지 않습니다.

다른 사용자에게 보이는 것은 작업이 있다는 사실, 주인, 상태, VRAM 뿐입니다. 작업 이름은 "3D 생성" 같은 종류 이름이고, 파일 이름은 날짜와 무작위 글자이며, 적은 글은 명령에 넣지 않고 파일로 넘깁니다.

한 화면의 결과를 다른 화면의 입력으로 넘기려면 아래의 노드 편집기(Advanced 메뉴)를 씁니다.

## 3D 생성

콘솔의 "3D 생성" 화면에 사진을 올리면 3D 모델 파일(GLB)이 만들어집니다. 사진은 한 장이어도 되고, 같은 물체를 여러 방향에서 찍은 사진을 여러 장(최대 6장) 넣어도 됩니다.

- **켜지는 조건:** 사용자 홈에 `pixal3d-run/pixal3d.sh` 가 있으면 그 사용자에게 화면이 켜집니다. 없으면 화면에 안내가 나옵니다.
- **하는 일:** 사진은 `pixal3d-run/inputs/` 에 저장하고, `./pixal3d.sh --image inputs/<이름>-1.png --output outputs/<이름>.glb --seed N --ss-steps 12 … --faces 1000000 --texture-size 4096 --remesh` 를 작업으로 넣습니다. 여러 장이면 `--image` 를 장마다 주고 `--views front,right,…` 와 `--save-views outputs/<이름>.views` 를 더합니다. 결과는 `pixal3d-run/outputs/` 에 생깁니다.
- **상세 설정:** 화각(비워 두면 사진에서 추정), 세 단계마다 단계 수·가이던스·가이던스 누르기·앞 구간 몰기, 모양 조각 수 한도, 삼각형 수, 텍스처 크기, 면 다시 짜기. Pixal3D 의 `inference.py` 는 뒤의 세 값을 코드에 적어 두었습니다. `pixal3d_run.py` 가 그 함수를 그대로 부르면서 값만 바꿔 넘깁니다.
- **미리보기:** 완성된 GLB 는 콘솔 안에서 돌려 볼 수 있습니다. 외부 라이브러리 없이 브라우저의 WebGL2 로 그리므로 인터넷이 없어도 됩니다.
- **잰 값 (RTX 3090 24GB, Pixal3D 절약 모드):** 기본값으로 사진 한 장에 2~3분, 파일 약 37MB, VRAM 최대 약 16GB, RAM 약 25GB. 단계 수를 8로, 삼각형을 10만 개로, 텍스처를 2048 로 줄이고 면 다시 짜기를 끄면 91초, 4.6MB 였습니다. LLM(20GB 예약)이 올라가 있으면 내려갈 때까지 기다립니다.

### 여러 방향에서 찍은 사진으로 만들기

사진 한 장으로 만들면 모델은 보이지 않는 뒤쪽과 옆쪽을 짐작해서 채웁니다. 같은 물체를 여러 방향에서 찍은 사진을 함께 넣으면, 모델은 그 사진들을 한꺼번에 보고 모양과 색을 맞춥니다. Pixal3D 가 따로 학습해 둔 여러 장용 모델(`ckpts/*_mv`)을 씁니다.

사진을 두 장 이상 올리면 사진마다 찍은 방향을 고르는 칸이 생깁니다. 방향은 여섯 가지이고, 앞에서 물체를 보고 선 사람을 기준으로 정합니다.

```text
                 위 (바로 위에서 내려다봄. 물체의 앞쪽이 사진 아래쪽에 오게)
                  │
     왼쪽 ◀──── [물체] ────▶ 오른쪽      (앞에서 보던 사람이 자기 왼쪽/오른쪽으로 90° 돌아가서 찍음)
                  │
                 앞 (정면. 첫 사진은 늘 이 방향)          뒤: 앞의 정반대   아래: 바로 밑에서 올려다봄
```

- **앞 사진은 꼭 있어야 합니다.** 다른 사진은 앞 사진을 기준으로 놓입니다. 방향마다 사진은 한 장입니다.
- **같은 거리, 같은 줌으로 찍습니다.** 서버는 모든 사진을 하나의 배율로 잘라 크기를 맞춥니다. 사진마다 물체를 꽉 채우게 자르면 안 되기 때문입니다. 긴 물체는 앞에서 보면 좁고 옆에서 보면 넓은데, 둘을 같은 크기로 늘리면 모델이 받는 모양이 서로 어긋납니다. 그래서 찍을 때 거리와 줌이 다르면 크기가 틀어집니다.
- **배경이 단순한 곳에서 찍거나, 배경을 지운 PNG 를 넣습니다.** 배경 지우기 모델이 물체가 아닌 것을 남기면 결과가 크게 틀어집니다 (아래 시험 B). 투명 배경 PNG 는 그 투명도를 그대로 씁니다.
- **모델이 받은 사진을 볼 수 있습니다.** 여러 장으로 만든 항목의 미리보기 창에 "모델이 받은 사진"이 나옵니다. 배경을 지우고 크기를 맞춘 뒤 모델이 본 그대로입니다. 결과가 이상하면 여기부터 봅니다.
- **화각:** 상세 설정의 화각을 비워 두면 앞 사진에서 짐작합니다 (사진 한 장일 때와 같은 방법).
- **VRAM:** 여러 장일 때는 20GB 를 예약합니다 (`gen3d_mv_vram_gb`). 한 장일 때는 그대로 18GB 입니다.

노드 편집기에는 "3D 만들기 (여러 장)" 노드가 있습니다. 방향마다 입력 점이 따로 있고, "앞"만 꼭 이어야 합니다. 본보기 그래프 "여러 방향의 사진 → 3D 하나"로 시작할 수 있습니다. 노드로 만든 결과에는 "모델이 받은 사진"이 남지 않습니다.

**그림 그리기의 "각도 바꾸기"로 만든 그림은 넣지 않습니다.** 그 그림들은 방향마다 따로 상상해 그린 것이라 무늬·크기·실제로 돈 각도가 서로 맞지 않습니다. 여러 장 모델은 사진들의 특징을 3D 공간에 비춰 평균 내므로, 서로 맞지 않는 그림을 넣으면 앞 얼굴이 뒷머리에도 생기는 것처럼 망가집니다 (2026-11-16, 개 사진으로 확인). 사진이 한 장뿐이면 아래 "여러 방향 그림"을 거칩니다.

잰 값입니다 (RTX 3090 24GB, 절약 모드, 삼각형 20만 개·텍스처 2048, 2026-11-15). 저장소에 든 본보기(외눈 괴물 머리 모형을 앞·오른쪽·뒤·왼쪽에서 그린 그림)로 돌렸습니다.

| 시험 | 넣은 것 | 걸린 시간 | GPU 메모리 최대 | 결과 |
|---|---|---|---|---|
| A | 본보기 4장 (투명 배경) | 132초 | 약 17.8GiB | 한 물체로 맞게 나옴 |
| B | A 를 사진처럼 바꾼 4장 (회색 배경 JPG, 물체를 작게) | 83초 | 약 17.0GiB | 틀어짐. 앞 사진의 배경 지우기가 물체 아래에 상반신 모양을 남겼다 |
| C | A 의 4장 + 위에서 본 그림 (A 의 결과를 위 방향 카메라로 그린 것) | 131초 | 약 17.9GiB | 한 물체로 맞게 나옴. 위에서 본 모양도 넣은 그림과 같다 |
| D | 앞 + 위, 2장 | 119초 | 약 17.0GiB | 한 물체로 나옴. 옆과 뒤는 모델이 채움 |

C 와 D 는 실제로 위에서 찍은 사진이 아니라 A 의 결과를 그린 그림을 넣었습니다. 그래서 위 방향의 카메라 계산이 다른 방향과 맞는지까지만 확인한 것이고, 손으로 찍은 위쪽 사진은 시험하지 못했습니다. 아래 방향은 시험하지 않았습니다.

### 준비

준비는 사용자마다 한 번입니다. 워크스페이스에서 [Pixal3D](https://github.com/TencentARC/Pixal3D)를 `~/Pixal3D` 에 받고 conda 환경 `trellis2` 를 만든 뒤, `deploy/gen3d/` 의 파일들을 홈에 둡니다.

```bash
mkdir -p ~/pixal3d-run/open-rembg
cp deploy/gen3d/pixal3d.sh deploy/gen3d/pixal3d_run.py ~/pixal3d-run/ && chmod +x ~/pixal3d-run/pixal3d.sh
cp deploy/gen3d/open-rembg/pipeline.json deploy/gen3d/open-rembg/pipeline_mv.json ~/pixal3d-run/open-rembg/
```

Pixal3D 설치에서 걸리는 곳이 세 군데 있습니다.

- `transformers` 는 4.57.3 이어야 합니다. 5.x 에서는 `'DINOv3ViTModel' object has no attribute 'layer'` 로 멈춥니다 (TRELLIS.2 도 같습니다).
- MoGe 는 `pip install --no-deps git+https://github.com/microsoft/MoGe.git` 로 깔고, `utils3d_moge` 를 MoGe 가 지정한 판으로 따로 깝니다. 의존 패키지까지 깔면 TRELLIS.2 용으로 빌드한 `flex_gemm` 이 바뀝니다.
- 보조 모델 NAF 는 `torch.hub` 으로 받는데 기본 폴더 `/shared/models/torch` 가 읽기 전용입니다. `pixal3d.sh` 가 `TORCH_HOME` 을 홈으로 돌립니다.

공용 모델은 관리자가 호스트에서 한 번 받습니다 (약 25GB).

```bash
sudo sh -c 'export HF_HUB_CACHE=/srv/shared/models/hub HF_HUB_DISABLE_XET=1
for repo in camenduru/dinov3-vitl16-pretrain-lvd1689m Ruicheng/moge-2-vitl ZhengPeng7/BiRefNet; do
    /opt/ironlab/venv/bin/hf download $repo
done
/opt/ironlab/venv/bin/hf download TencentARC/Pixal3D
chmod -R a+rX /srv/shared/models/hub'
```

여러 장용 모델 파일(`*_mv*`, 약 20.5GB)까지 받는 명령입니다. 한 장으로만 쓸 거면 마지막 `hf download` 에 `--exclude "*_mv*"` 를 붙여 빼도 됩니다. 나중에 더 받을 때는 이미 받은 판에 맞춰 받아야 합니다. `--revision <받은 판의 커밋>` 을 붙이지 않으면 새 판이 생겨, 한 장용 파일이 옛 판에만 남을 수 있습니다.

`HF_HUB_DISABLE_XET=1` 은 기본 전송 방식이 큰 파일에서 멈춘 적이 있어 넣었습니다. Pixal3D 가 원래 쓰는 배경 제거 모델 `briaai/RMBG-2.0` 은 Hugging Face 에서 이용 조건에 동의해야 받을 수 있습니다. 받아 두면 `pixal3d.sh` 가 원래 설정을 쓰고, 없으면 로그인 없이 받는 `ZhengPeng7/BiRefNet` 으로 바꾼 설정(`open-rembg/pipeline.json`)을 씁니다.

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `gen3d_dir`, `gen3d_script`, `gen3d_vram_gb`, `gen3d_mv_vram_gb` 로 바꿉니다.

## 여러 방향 그림

사진이 한 장뿐인 물체를 3D 로 만들 때, 모델이 보이지 않는 옆과 뒤를 짐작으로 채우면 뭉툭해집니다. 그렇다고 방향마다 따로 그린 그림(각도 바꾸기)을 넣으면 그림끼리 맞지 않아 3D 가 망가집니다. 이 화면은 사진 한 장에서 물체를 여러 방향에서 본 그림을 **한 번에 함께** 그려 서로 맞춥니다 ([MV-Adapter](https://github.com/huanngzh/MV-Adapter), ICCV 2025, Apache-2.0, SDXL 바탕).

- **켜지는 조건:** 사용자 홈에 `mvadapter-run/mvadapter.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **하는 일:** 사진의 배경을 지우고(BiRefNet), 6방향(앞, 오른쪽 앞 45°, 오른쪽, 뒤, 왼쪽, 왼쪽 앞 315°) 또는 4방향을 768px 로 그립니다. 모든 방향은 눈높이, 원근 없이(정사영), 같은 틀로 그립니다. 결과는 3×2 로 모은 그림, 방향마다 한 장, 그리고 3D 만들기가 읽는 묶음 파일(`.views.json`, 방향 이름·각도·틀·그림 경로)입니다.
- **방향 약속:** 3D 만들기와 같습니다. "오른쪽"은 앞에서 보던 사람이 자기 오른쪽으로 90° 돌아가서 본 모습이라, 물체의 앞쪽(코)이 그림의 왼쪽을 봅니다.
- **물체 설명:** 영어로 짧게 적습니다 (예: `a dog`). 비우면 `a high quality object` 로 그립니다.
- **3D 로 만들기:** 노드 편집기의 본보기 "사진 한 장 → 여러 방향 그림 → 3D 하나"를 씁니다. "여러 방향 그리기" 노드의 "여러 방향 묶음"을 "3D 만들기 (여러 방향 그림)" 노드에 이으면, 3D 실행 스크립트가 `--views-json` 으로 묶음을 받아 그림을 다시 자르지 않고 묶음의 각도에 카메라를 둡니다. 원근 없이 그린 그림이라 화각은 10° 로 좁게 둡니다 (Pixal3D 가 배운 범위 10~70° 의 끝).
- **잰 값 (RTX 3090 24GB, 2026-11-16, 개 사진 한 장):** 6방향 그리기 61초 (배경 지우기·모델 올리기까지 82초), GPU 메모리 최대 15.1GiB, 예약 17GB (`genmv_vram_gb`). 그 묶음으로 3D 194초. 털 무늬·귀·목줄이 모든 방향에서 맞았고, 3D 는 얼굴이 하나인 머리에 옆모습과 뒷머리가 깨끗했습니다. 같은 사진으로 비교한 다른 방법: 사진 한 장만 넣은 3D 는 옆과 뒤가 뭉툭했고, 각도 바꾸기 그림 6장은 뒷머리에도 얼굴이 생겼습니다. 4방향과 6방향은 비슷했습니다.
- **알려진 흠:** 정면도 새로 그리므로 원래 사진과 조금 다릅니다. 사진의 배경(풀밭)이 배경 지우기 가장자리에 남으면 목 둘레에 그 색이 묻습니다. 시험은 개 한 마리, 시드 하나뿐입니다.
- **자세가 다르면 모양이 어긋납니다:** MV-Adapter 는 3D 모델을 정면·눈높이에서 그린 그림을 '앞'으로 받아 배웠습니다. 몸은 옆을 보고 머리만 카메라 쪽으로 돌린 소 사진(2026-11-16)에서는 6방향 그림의 뒤쪽에 소 두 마리가 겹쳤고, 그 묶음으로 만든 3D 도 몸이 두 개였습니다. 3D 가 목적이면 아래 "3D 생성 (모양 먼저)"를 먼저 쓰세요.

준비는 한 사용자 홈에 한 번입니다. 환경(약 5.9GB), 코드, 가중치(약 11GB)를 받습니다.

```bash
mkdir -p ~/mvadapter-run
cp deploy/genmv/* ~/mvadapter-run/ && chmod +x ~/mvadapter-run/mvadapter.sh ~/mvadapter-run/setup_mvadapter.sh
~/mvadapter-run/setup_mvadapter.sh     # conda 환경 mvadapter(python 3.10, torch 2.5.1 CUDA 12.4, diffusers 0.34.0), 코드 7d37a97, 가중치 → ~/models/mvadapter-hub
```

그다음 서버에서 `sudo bash deploy/shared-apps.sh --migrate-from <설치한 사용자>` 를 돌리면 가중치(`/srv/shared/models/mvadapter-hub`), 환경(`envs/mvadapter`), 코드(`apps/mvadapter-run/MV-Adapter`)가 공용으로 옮겨지고, 모든 사용자 홈에 링크가 걸립니다. 배경 지우기 모델은 Pixal3D 가 쓰는 공용 hub 의 `ZhengPeng7/BiRefNet` 을 함께 씁니다. 작업 안에서는 인터넷을 쓰지 않습니다 (`HF_HUB_OFFLINE=1`).

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genmv_dir`, `genmv_script`, `genmv_vram_gb` 로 바꿉니다.

## 3D 생성 (모양 먼저)

사진 한 장으로 3D 를 만들 때, 사진 한 장 3D(Pixal3D)는 모양은 깔끔하지만 사진에 안 보이던 옆과 뒤의 색이 밋밋하거나 어둡습니다. 여러 방향 그림(위)은 옆과 뒤가 자세하지만 모양까지 그림 모델이 상상하므로, 사진 자세가 맞지 않으면 3D 가 망가집니다. 이 생성기는 둘을 나눠 맡깁니다. **모양은 사진 한 장 3D 가 정하고, MV-Adapter 는 그 모양 위에 색만 칠합니다.** 노드 편집기의 "3D 만들기 (모양 먼저)" 노드와 본보기 "사진 한 장 → 3D (모양 먼저)"로 씁니다 (쉬운 메뉴 화면은 아직 없습니다).

- **켜지는 조건:** 사용자 홈에 `shapefirst-run/shapefirst.sh` 가 있고, 3D 생성기(`pixal3d-run`)와 여러 방향 그림 생성기(`mvadapter-run`)가 함께 깔려 있어야 합니다.
- **하는 일 (다섯 단계, 로그에 `[shape] 단계 k/5`):**
  1. 배경 지우기 (BiRefNet). 지운 사진 한 장을 다음 단계가 모두 함께 씁니다.
  2. 사진 한 장 3D (`~/pixal3d-run/pixal3d.sh`, 삼각형 20만 개·텍스처 2048 이 기본).
  3. 그 모양을 앞·오른쪽·뒤·왼쪽·위·아래에서 렌더링해 표면의 위치와 기울기 그림을 만듭니다. 사진 쪽은 늘 GLB 의 -z 쪽입니다. Pixal3D 가 학습 때부터 입력 사진의 카메라를 같은 자리에 두기 때문입니다. 처음에는 실루엣과 색으로 사진 쪽을 찾았는데, 앞뒤가 비슷한 물체(그루터기, 건물)에서 반대쪽을 고른 적이 있어 고정했습니다. 실루엣 겹침은 확인용으로 로그에만 남깁니다.
  4. MV-Adapter 의 모양 따라 그리기 모드(ig2mv)가 그 모양 그림과 사진을 받아 여섯 방향의 색을 768px 로 칠합니다.
  5. 칠한 색을 텍스처에 다시 입힙니다. 텍스처의 칸마다 3D 자리와 표면 방향을 구해, 그 자리가 보이는 그림의 색을 마주 보는 정도(cos⁴)로 섞습니다. 사진이 본 쪽은 원래 텍스처(사진 색)를 세 배 무게로 남기고, 모델이 다시 그린 앞 그림은 쓰지 않습니다.
- **색 맞추기 (`--color`):** 모델은 사진의 햇빛을 이어 받아 사진 반대쪽을 그늘처럼 어둡게 칠하거나(소: 갈색 밝기 33 → 27), 재질 색을 바꾸기도 합니다(그루터기: 올리브색 껍질 → 회색). 그래서 세 가지를 만들어 봅니다. 그대로(`none`), 털·껍질을 진한 색 무리와 흰 무리로 나눠 무리마다 사진 쪽 색에 맞추기(`group`), 사진 쪽과 그 그림 양쪽에 다 보이는 띠에서 같은 표면 점끼리 색 맞추기(`overlap`)입니다. 기본 `auto` 는 셋을 0·90·180·270° 에서 렌더링해 사진 쪽(0°)과 색이 가장 덜 다른 것을 고릅니다. 시험한 네 물체에서는 소·고양이가 `group`, 그루터기·거북이가 `overlap` 으로 뽑혔고, 시드 둘 모두 눈으로 고른 것과 같았습니다.
- **잰 값 (RTX 3090 24GB, 2026-11-16):** 한 물체에 사진 한 장 3D 99~143초, 색 칠하기 약 75초(모델 올리기 포함), 나머지 20초 안쪽으로 모두 약 3분 반입니다. GPU 메모리는 단계마다 따로 쓰고 가장 큰 단계가 약 16GB(사진 한 장 3D)라 18GB 를 예약합니다 (`genshape_vram_gb`).
- **결과:** 3D 모델(GLB, 텍스처 PNG 라 사진 한 장 3D 보다 약 1.6배 큼), 칠한 여섯 방향을 3×2 로 모은 그림, 네 방향에서 본 작은 미리보기.
- **확인한 물체:** 소(옆으로 서서 머리를 돌린 사진), 고양이(누운 실제 사진), 참나무 그루터기, 판타지 거북이. 넷 모두 사진 한 장 3D 보다 사진 반대쪽이 자세했고(고양이 목줄이 목 뒤로 이어짐, 껍질 홈, 등껍질 판), 몸이 겹치는 일은 없었습니다.
- **알려진 흠:** 모델이 그려 넣은 털 주름·음영은 그대로 남습니다. 시드에 따라 작은 얼룩이 생깁니다(소 시드 2: 뒷다리 근처 회청색). 위에서 찍은 사진은 3D 가 바닥에 맞춰 서지 않습니다(사진 한 장 3D 가 카메라 기준으로 놓기 때문). 자동 색 맞추기는 물체의 앞뒤 색 구성이 비슷하다고 보고 고르므로, 앞과 뒤가 전혀 다른 물체에서는 틀릴 수 있습니다. Pixal3D 메쉬는 얇은 이중 벽이라 삼각형의 약 절반이 바깥에서 보이지 않는 안쪽 벽입니다.

준비: 3D 생성기와 여러 방향 그림 생성기가 먼저 있어야 합니다. 색 칠하기 가중치(`mvadapter_ig2mv_sdxl.safetensors`, 3.6GB)는 관리자가 호스트에서 한 번 받습니다.

```bash
sudo sh -c 'export HF_HUB_DISABLE_XET=1
/opt/ironlab/venv/bin/hf download huanngzh/mv-adapter mvadapter_ig2mv_sdxl.safetensors --cache-dir /srv/shared/models/mvadapter-ig2mv
chmod -R a+rX /srv/shared/models/mvadapter-ig2mv'
```

한 사용자 홈의 `~/models/mvadapter-ig2mv` 에 받아 두었다면 `sudo bash deploy/shared-apps.sh --migrate-from <그 사용자>` 가 공용으로 옮깁니다. 실행 스크립트(`deploy/genshape/`)는 배포 때마다 공용에 올라가고 홈마다 `~/shapefirst-run` 이 생깁니다. 폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genshape_dir`, `genshape_script`, `genshape_vram_gb` 로 바꿉니다.

## 이미지 생성

콘솔의 "이미지 생성" 화면에 그릴 내용을 적으면 그림(PNG)이 만들어집니다. 참고 그림을 함께 넣으면 글대로 그 그림을 고칩니다.

- **켜지는 조건:** 사용자 홈에 `qwen-image-run/qwen-image.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **하는 일:** 글은 `qwen-image-run/inputs/<이름>.prompt.txt` 에 저장하고, `./qwen-image.sh --prompt-file inputs/<이름>.prompt.txt --output outputs/<이름>.png --thumb outputs/<이름>.thumb.jpg --size … --model … --seed N --kv-cache --tile 1024` 를 작업으로 넣습니다. 글을 명령에 넣지 않으므로 작업 목록이나 작업 명령에 글이 남지 않습니다.
- **상세 설정:** 피할 내용, 단계 수, 잡음 시간표 직접 주기, 가이던스, 글 계산 다시 쓰기, 풀어낼 때의 조각 크기.
- **모델:** [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) 과 빠른 판 Turbo 입니다. 이용 조건은 연구용(Qwen Research License)입니다.
- **잰 값 (RTX 3090 24GB):** Turbo 2048×2048 한 장에 약 65초, 1024×1024 는 20~35초, 본체(40단계)는 약 5분. VRAM 은 글만으로 최대 약 17GB, 참고 그림 한 장이면 약 20GB, RAM 약 32GB.
- **참고 그림을 베끼고 타는 결과 (2026-11-15):** 참고 그림을 고칠 때, 어떤 시드에서는 모델이 글이나 각도를 따르지 않고 참고 그림을 거의 그대로 베낍니다. 그 결과는 털·잎 윤곽이 굵게 타고 색이 진해집니다. 같은 개 사진, 같은 설정에서 시드 42 는 늘 이렇게 됐고 시드 1·2 는 깨끗하게 돌았습니다. 각도 LoRA 세기(1.5)나 가이던스(4)를 올리거나 '글 계산 다시 쓰기'를 꺼도 그대로였습니다. 그래서 `qwen_image.py` 는 두 그림을 긴 변 256px 로 줄여 윤곽 세기를 견줍니다. 결과가 참고 그림의 2배 이상이면(잰 37장에서 탄 그림 4.3~5.8배, 멀쩡한 그림 0.65~1.2배) 각도 바꾸기는 시드를 하나씩 올려 두 번까지 다시 그리고, 그 밖의 고치기는 기록에 알리기만 합니다(`--copy-retries`). 노드 편집기는 새 노드마다 시드를 따로 정해, 모든 노드가 같은 시작 잡음에서 출발하지 않게 합니다.
- **각도 바꾸기의 기본 처리:** 보는 방향 없이 카메라 높이나 '가까이'만 고르면 정면에서 본 모습으로 그립니다. 참고 그림이 여러 장이면 첫 장만 씁니다 (각도 LoRA 는 사진 한 장을 돌려 보는 법으로 배웠습니다).
- **Turbo 에서 확인한 것 (1024×1024, 2026-11-14):** Turbo 는 모델에 저장된 8단계 시간표로 그리도록 만든 모델입니다. 단계 수만 8로 주면 고르게 내려가는 시간표로 바뀌어 그림이 흐려졌고, 6단계에 가이던스 3 을 함께 주니 그림이 깨졌습니다. 시간표를 `1,0.95,0.9,0.8,0.6,0.3` 으로 직접 주면 6단계로도 깨끗했고 그리는 시간이 17초였습니다. 가이던스만 3 으로 켠 것과 본체 20단계에 가이던스 3 은 깨끗했습니다.

준비는 사용자마다 한 번입니다. `deploy/genimg/` 의 세 파일을 홈에 두고 환경을 만듭니다.

```bash
mkdir -p ~/qwen-image-run
cp deploy/genimg/qwen-image.sh deploy/genimg/qwen_image.py deploy/genimg/setup_env.sh ~/qwen-image-run/
chmod +x ~/qwen-image-run/qwen-image.sh ~/qwen-image-run/setup_env.sh
~/qwen-image-run/setup_env.sh        # conda 환경 qwenimage (transformers 5.17 이상이 필요해 trellis2 와 따로 둔다)
```

공용 모델은 관리자가 호스트에서 한 번 받습니다 (각 약 31GB).

```bash
sudo sh -c 'export HF_HUB_CACHE=/srv/shared/models/hub HF_HUB_DISABLE_XET=1
/opt/ironlab/venv/bin/hf download Qwen/Qwen-Image-2.1-Turbo
/opt/ironlab/venv/bin/hf download Qwen/Qwen-Image-2.1
chmod -R a+rX /srv/shared/models/hub'
```

24GB 카드에서 걸리는 곳은 `qwen_image.py` 가 처리합니다: 글 이해 모델과 그림 모델을 번갈아 GPU 에 올리고(`enable_model_cpu_offload`), 그림으로 풀어내는 단계를 1024px 조각으로 나눕니다(`vae.enable_tiling`). 조각 없이 2048×2048 을 풀면 메모리가 모자랍니다.

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genimg_dir`, `genimg_script`, `genimg_vram_gb` 로 바꿉니다.

## 음악 생성

콘솔의 "음악 생성" 화면에 분위기 설명과 가사를 적으면 노래와 반주가 든 곡(FLAC)이 만들어집니다. 모델은 [YuE2](https://huggingface.co/m-a-p/YuE2-3B)이고, 이용 조건은 비상업(CC BY-NC 4.0)입니다.

YuE2 는 소리를 바로 만들지 않습니다. 멜로디와 코드를 글자 악보(ABC 표기)로 먼저 쓰고, 그 악보대로 소리를 만듭니다. 그래서 만든 곡의 악보를 가져와 빠르기나 코드를 고친 뒤 그 악보로 다시 만들 수 있습니다.

- **켜지는 조건:** 사용자 홈에 `yue2-run/yue2.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **하는 일:** 분위기 설명과 가사를 `yue2-run/inputs/` 에 저장하고, `./yue2.sh --style-file inputs/<이름>.style.txt --lyrics-file inputs/<이름>.lyrics.txt --output outputs/<이름>.flac --mp3 outputs/<이름>.mp3 --score outputs/<이름>.abc --cot full --seed N …` 을 작업으로 넣습니다. 곡 옆에 들어 보기 쉬운 mp3 사본과 악보가 함께 생깁니다.
- **가사 적는 법:** `[Verse]`, `[Chorus]` 같은 구획 표시를 줄 맨 앞에 두고 그 아래에 한 줄씩 적습니다. 곡의 길이는 가사의 길이를 따라갑니다.
- **상세 설정:** 악보 직접 주기, 악보 쓰기와 노래 만들기 단계의 고르는 폭·후보 확률 합·후보 개수·되풀이 줄이기·길이 한도, 가이던스, 소리로 바꿀 때 다듬는 횟수.
- **듣기:** 완성된 곡은 콘솔 안에서 듣습니다. 같은 창에 악보가 나오고, "이 악보를 고쳐서 다시 만들기"를 누르면 악보가 양식의 "악보 직접 주기" 칸에 들어갑니다.
- **잰 값 (RTX 3090 24GB, 2026-11-14):** 91초짜리 곡을 45초에 만들고(모델 읽기까지 52초) VRAM 은 최대 약 9GB. 소리의 설계도 조각(토큰) 25개가 소리 1초입니다 (2,285개 → 91.4초, 600개 → 24.0초). 화면은 여유를 두어 16GB 를 예약합니다 (만든 쪽이 가장 긴 입력에서 잰 최댓값이 14.1GiB).

준비는 사용자마다 한 번입니다. `deploy/genmusic/` 의 세 파일을 홈에 두고 환경을 만듭니다.

```bash
mkdir -p ~/yue2-run
cp deploy/genmusic/yue2.sh deploy/genmusic/yue2_song.py deploy/genmusic/setup_env.sh ~/yue2-run/
chmod +x ~/yue2-run/yue2.sh ~/yue2-run/setup_env.sh
~/yue2-run/setup_env.sh              # conda 환경 yue2 (실행 패키지가 torch 2.10.0, transformers 4.57.6 을 못 박아 따로 둔다)
```

공용 모델은 관리자가 호스트에서 한 번 받습니다 (약 7.3GB).

```bash
sudo sh -c 'export HF_HUB_CACHE=/srv/shared/models/hub HF_HUB_DISABLE_XET=1
/opt/ironlab/venv/bin/hf download m-a-p/YuE2-3B
/opt/ironlab/venv/bin/hf download m-a-p/YuE2-Vae
chmod -R a+rX /srv/shared/models/hub'
```

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genmusic_dir`, `genmusic_script`, `genmusic_vram_gb` 로 바꿉니다.

## 영상 생성

콘솔의 "영상 생성" 화면에 장면을 적으면 소리가 든 영상(MP4, 2~15초, 1초에 24장, 스테레오 소리)이 만들어집니다. 모델은 [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3)입니다. 화면과 소리를 한 번에 만들기 때문에, 장면에 적은 대사와 발소리가 화면의 입 모양·움직임과 맞습니다.

**라이선스를 먼저 봅니다.** MiniMax H3 Community License 는 유럽연합, 영국, 대한민국, 미국을 '제외 지역'으로 정합니다. 이 지역에서 쓰려면 MiniMax 에 신청해 허가를 받아야 합니다 ([신청서](https://platform.minimax.io/h3-license), [라이선스 Q&A](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/QA-about-License.md)). 설치할지는 각 서버의 관리자가 이 조건을 보고 정합니다.

- **켜지는 조건:** 사용자 홈에 `minimax-h3-run/h3.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **적는 칸 세 개:** 장면(화면, 움직임, 카메라, 대사), 주변 소리, 배경 음악. 실행 스크립트가 셋을 MiniMax 가 정한 프롬프트 모양(`integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music`)으로 묶습니다. 영어가 가장 잘 듣고, 대사는 `<d>[Korean] 안녕하세요</d>` 처럼 감쌉니다.
- **첫·끝 장면:** 사진 한 장을 넣으면 영상이 그 사진에서 시작하고, 두 장이면 두 번째 사진에서 끝납니다. 실행 스크립트가 안내서대로 "그림 1 이 0초, 그림 2 가 끝 초에 맞는다"는 첫 줄을 붙입니다.
- **모델 둘:** 기본은 HyperFlow H3 Flash 입니다 (H3 를 14B 로 줄이고 6단계로 만들도록 다시 배운 판, [Video Rebirth](https://huggingface.co/VideoRebirthAI/hyperflow-h3-flash), 6비트 GGUF). "H3 본체"(33B, 4비트 GGUF, 20단계)도 고를 수 있습니다.
- **하는 일:** `./h3.sh --prompt-file inputs/<이름>.prompt.txt [--sound-file …] [--music-file …] [--image <첫 장면> [--image <끝 장면>]] --output outputs/<이름>.mp4 --thumb outputs/<이름>.thumb.jpg --last-frame outputs/<이름>.last.png --size … --model flash --seconds 5 --seed N` 을 작업으로 넣습니다. 실행은 [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)의 `sd-cli` 가 하고, 쓰지 않는 무게는 CPU 메모리에 두었다가 차례가 오면 GPU 로 올립니다. 결과 AVI 는 ffmpeg 가 MP4(H.264 + AAC)로 바꿉니다.
- **이어 만들기:** 보기 창의 "끝 장면에서 이어 만들기"를 누르면 이 영상의 마지막 장면이 다음 영상의 첫 장면 칸에 들어갑니다. 장면 설명만 다음 이야기로 고쳐 만들면 두 영상이 이어집니다.
- **상세 설정:** 단계 수, 흐름 이동, 샘플러, 시간표 직접 주기, 주의 계산 묶음 크기, 공간·시간 조각으로 풀기, MP4 화질.
- **잰 값 (RTX 3090 24GB, 2026-11-14):** Flash 로 1344×768 5.2초 영상 355초 (영상 만들기 289초, 한 단계 약 48초, 풀어내기 51초), GPU 메모리 최대 21.9GB. 본체로 864×480 2.3초 영상 20단계 131초. 본체의 1344×768 5.2초 영상은 한 단계 약 54초라 20단계면 20분쯤 걸립니다. 시간은 크기와 길이보다 빠르게 늘어납니다. 864×480 2.3초에서 1344×768 5.2초로 화면 조각 수가 5.5배가 되자 한 단계 시간은 약 10배(5.5초 → 54초)가 되었습니다. 화면 조각끼리 서로를 보는 계산(주의)이 조각 수보다 빨리 늘기 때문입니다. 그래서 고를 때는 864×480 으로 빨리 보고, 마음에 드는 것을 큰 크기로 다시 만드는 편이 낫습니다.
- **VRAM 예약:** 22GB. 24GB 카드에서 늘 1GB 를 남기므로 23GB 이상을 예약하면 작업이 시작되지 못합니다 (24GB 보다 작은 카드에서는 이 생성기를 쓸 수 없습니다). 실행 스크립트는 `sd-cli` 의 무게·버퍼 몫을 19GiB 로 묶습니다 (묶지 않으면 남은 GPU 메모리를 다 써서 24,006MiB 까지 찼습니다).

준비는 사용자마다 한 번입니다. `deploy/genvideo/` 의 세 파일을 홈에 두고 설치 스크립트를 돌립니다. 빌드와 내려받기(약 58GB)를 함께 합니다.

```bash
mkdir -p ~/minimax-h3-run
cp deploy/genvideo/h3.sh deploy/genvideo/h3_run.py deploy/genvideo/setup_h3.sh ~/minimax-h3-run/
chmod +x ~/minimax-h3-run/h3.sh ~/minimax-h3-run/setup_h3.sh
~/minimax-h3-run/setup_h3.sh         # sd-cli 빌드, ffmpeg 환경(media), 모델 → ~/models/minimax-h3
```

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genvideo_dir`, `genvideo_script`, `genvideo_vram_gb` 로 바꿉니다.

## 음성 생성

콘솔의 "음성 생성" 화면에 대본을 적으면 목소리(WAV, 44.1kHz)가 만들어집니다. 모델은 [Fish Audio Drama 3](https://huggingface.co/fishaudio/drama-3)입니다. 한국어를 포함해 80여 개 언어를 읽고, 한 대본에 16명까지 나올 수 있습니다.

이용 조건은 Fish Audio Community License 입니다. 연구·비상업은 무료이고, 상업은 연 매출 50만 달러 미만일 때만 됩니다. 남에게 음성 합성·목소리 복제 서비스를 여는 것과, 동의 없이 실제 사람의 목소리를 본뜨는 것은 안 됩니다.

- **켜지는 조건:** 사용자 홈에 `drama3-run/drama3.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **대본 적는 법:** 한 사람이면 그냥 적습니다. 여러 사람이면 줄 맨 앞에 `화자1:`, `화자2:` 를 붙입니다. 실행 스크립트가 이것을 모델의 화자 표시(`<|speaker:0|>` …)로 바꿉니다. `[laughs]`, `[sighs]` 처럼 대괄호로 웃음·한숨을 넣습니다.
- **목소리 본뜨기:** 참고 목소리 녹음(wav, mp3, flac, ogg, m4a, 4개까지)과, 그 녹음에서 하는 말을 한 줄씩 같은 순서로 적습니다. 올린 순서가 화자1, 화자2 … 입니다. 녹음 수와 말의 줄 수가 다르면 큐에 넣기 전에 막습니다.
- **하는 일:** `./drama3.sh --text-file inputs/<이름>.script.txt [--ref-text-file … --ref …] --output outputs/<이름>.wav --mp3 outputs/<이름>.mp3 --seed N …` 을 작업으로 넣습니다. 실행은 [audio.cpp](https://github.com/0xShug0/audio.cpp)의 Drama 3 이식판이 하고, 모델은 4비트로 줄인 GGUF([Jmica/Drama-3-GGUF](https://huggingface.co/Jmica/Drama-3-GGUF), 약 23GB)입니다.
- **24GB 에 맞추는 법:** 모델은 전문가 여럿 가운데 몇 개만 골라 쓰는 구조(MoE)입니다. 전문가 층 40개 가운데 앞 16개를 CPU 메모리에 두고 CPU 로 계산합니다 (상세 설정 "CPU 에서 계산할 층"). 늘리면 GPU 메모리를 덜 쓰고 조금 느려집니다.
- **잰 값 (RTX 3090 24GB, 2026-11-14):** CPU 층 16개로 한국어 두 사람 대화 8.5초를 3.6초에 만들었고, 모델 읽기까지 8초, GPU 메모리 최대 16.5GB. 24층이면 13.0GB 에 4.3초. 참고 목소리 둘을 본뜬 9.8초 대화는 모델 읽기까지 10초, 16.7GB. 화면은 18GB 를 예약합니다.

준비는 사용자마다 한 번입니다.

```bash
mkdir -p ~/drama3-run
cp deploy/genvoice/drama3.sh deploy/genvoice/drama3_run.py deploy/genvoice/setup_drama3.sh ~/drama3-run/
chmod +x ~/drama3-run/drama3.sh ~/drama3-run/setup_drama3.sh
~/drama3-run/setup_drama3.sh         # audiocpp_cli 빌드, ffmpeg 환경(media), 모델 → ~/models/drama3
```

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genvoice_dir`, `genvoice_script`, `genvoice_vram_gb` 로 바꿉니다.

두 모델의 파일은 지금 각 사용자 홈의 `~/models/` 에 받습니다 (워크스페이스에서 공용 모델 폴더는 읽기만 됩니다). 다른 사용자도 쓰게 하려면 관리자가 호스트의 공용 폴더로 옮기고 `H3_MODELS`, `DRAMA3_MODEL` 을 그쪽으로 가리키게 합니다.

새 종류를 더할 때는 `ironlab/gen.py` 의 `KINDS` 에 종류 하나를 더하고, 화면 쪽 `app.js` 에 결과를 보는 방법(`open`)을 가진 `genPage` 하나를 더합니다.

## 효과음 생성

콘솔의 "효과음 생성" 화면에 들릴 소리를 영어로 적으면 효과음(WAV, 48kHz 모노)이 만들어집니다. 영상을 넣으면 영상의 움직임에 맞춘 소리를 만들고, 그 소리를 붙인 MP4 도 함께 만듭니다. 모델은 Sony AI 의 [Woosh](https://github.com/SonyResearch/Woosh)입니다 ([기술 보고서](https://arxiv.org/abs/2604.01929)).

이용 조건: 코드는 MIT·Apache-2.0 이지만 공개 가중치는 CC BY-NC 4.0 입니다. 만든 소리를 판매하는 게임이나 유료 작업에 쓸 수 없습니다.

- **켜지는 조건:** 사용자 홈에 `woosh-run/woosh.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **모델 넷:** 글만 주면 글로 만드는 모델(Woosh-DFlow 빠른 판, Woosh-Flow 본판)이, 영상을 주면 영상으로 만드는 모델(Woosh-DVFlow-8s 빠른 판, Woosh-VFlow-8s 본판)이 돕니다. 빠른 판은 본판을 4단계로 만들도록 다시 배운 판(증류)이고, 본판은 계산 횟수를 스스로 정하며 약 70번 계산합니다.
- **소리 설명 적는 법:** 영어로 짧게, 무엇이·어디서·어떻게 나는지를 적습니다 (예: `footsteps on dry leaves in a quiet forest, close up`). 글 이해 부분(RoBERTa-large)이 영어로만 배워서 한글은 알아듣지 못하므로, 한글이 든 설명은 큐에 넣기 전에 막습니다. 앞 77토큰(영어 단어 약 50개)까지만 읽습니다.
- **길이:** 글로 만드는 모델은 5초 조각으로 배워서 5초까지, 영상 모델은 8초 조각으로 배워서 8초까지입니다. 모델은 늘 그 길이를 만들고, 실행 스크립트가 앞에서부터 고른 길이만 남깁니다. 긴 배경 소리는 아래의 되풀이 소리로 만들어 게임에서 되풀이해 틉니다.
- **게임에 쓰기 좋게 다듬기:** 앞뒤 무음 자르기(가장 큰 곳보다 몇 dB 작은 구간), 끝 줄이기(기본 20ms, 자른 끝의 '틱' 소리를 막음), 처음 키우기, 소리 크기 맞추기(가장 큰 곳을 몇 dBFS 로), 되풀이 소리 만들기. 되풀이 소리는 끝 0.5초를 처음 위에 점점 작아지게 겹쳐, 끝에서 처음으로 넘어갈 때 이음매가 없게 합니다. 길이는 겹친 만큼 줄어듭니다 (5초 → 4.5초). 바람·비·모닥불처럼 고르게 이어지는 소리에 맞습니다.
- **영상에 붙이기:** 영상의 앞 8초(또는 '영상 시작 위치'부터 8초)를 1초에 24장씩 읽어 움직임에 맞춘 소리를 만듭니다. 영상에 원래 있던 소리는 쓰지 않고, 결과 MP4 는 새 소리로 바꿔 붙인 사본입니다.
- **하는 일:** `./woosh.sh --prompt-file inputs/<이름>.prompt.txt [--video inputs/<이름>-1.mp4] --output outputs/<이름>.wav --mp3 outputs/<이름>.mp3 --video-out outputs/<이름>.mp4 --thumb outputs/<이름>.jpg --model fast --seed N …` 을 작업으로 넣습니다. 영상이 없으면 mp4·jpg 는 생기지 않습니다.
- **잰 값 (RTX 3090 24GB, 2026-11-15, 다른 작업이 없을 때):** 글로 5초 소리 하나에 빠른 판 8~9초(모델 읽기 4.5초, 소리 만들기 0.2~0.3초), 본판 9.5초(계산 68번, 2.5초). GPU 메모리는 nvidia-smi 로 최대 3.9GB. 영상 8초에 빠른 판 10~16초, 본판 16~22초(소리 만들기 4.5초). 영상 특징 모델이 영상 조각을 4개씩 읽게 해서 torch 가 잡은 메모리가 최대 5.3GiB 입니다 (한 번에 다 읽으면 8.2GiB). 화면은 7GB 를 예약합니다. 같은 시드는 같은 소리를 비트까지 똑같이 냅니다.

설치는 한 사용자 홈에서 한 번 하고, 서버 관리자가 공용으로 옮깁니다 (모델·환경 약 18GB).

```bash
mkdir -p ~/woosh-run
cp deploy/gensfx/* ~/woosh-run/ && chmod +x ~/woosh-run/woosh.sh ~/woosh-run/setup_woosh.sh
~/woosh-run/setup_woosh.sh     # conda 환경 woosh(python 3.12, torch 2.8 CUDA 12.8), 가중치 7개(~9GB) → ~/models/woosh, 시험 소리 둘
```

그다음 서버에서 `sudo bash deploy/shared-apps.sh --migrate-from <설치한 사용자>` 를 돌리면 모델(`/srv/shared/models/woosh`)과 환경(`envs/woosh`)이 공용으로 옮겨지고, 모든 사용자 홈에 링크가 걸립니다. 실행 스크립트는 `PYTHONNOUSERSITE=1` 로 홈의 `~/.local` 패키지를 섞지 않고, 작업 안에서는 인터넷을 쓰지 않습니다 (`HF_HUB_OFFLINE=1`, 글자 사전과 영상 특징 모델은 `~/models/woosh/hf` 에서 읽음).

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `gensfx_dir`, `gensfx_script`, `gensfx_vram_gb` 로 바꿉니다.

## 소리 다듬기

콘솔의 "소리 다듬기" 화면에 곡·목소리·효과음이나 소리가 든 영상을 넣으면, 다듬은 소리(FLAC 24비트)와 들어 보기용 mp3 를 만듭니다. 음악·음성·효과음·영상 생성 화면의 결과 창에 있는 "소리 다듬기로 보내기"를 누르면 그 결과(곡이면 악보까지)가 양식에 들어갑니다.

왜 따로 다듬나: AI 가 부른 노래는 'ㅅ' 소리가 매번 같은 쇳소리로 나고, 특정 높이에서 울림이 튀고, 크기가 지나치게 고릅니다. 믹싱 엔지니어는 목소리 트랙 하나에 정해진 순서로 처리해 이것을 고칩니다. 그런데 음악 생성(YuE2)은 목소리와 반주를 한 파일로 섞어 내놓습니다. 섞인 곡에 목소리 처리를 걸면 'ㅅ' 소리를 줄이다 심벌즈도 함께 줄고, 잔향이 드럼에도 걸립니다. 그래서 먼저 둘을 나눕니다.

- **켜지는 조건:** 사용자 홈에 `polish-run/polish.sh` 가 있으면 그 사용자에게 화면이 켜집니다.
- **넣은 것 (`--source`):** 노래(목소리 + 반주, 기본)는 나누기 → 목소리 다듬기 → 다시 섞기 → 마무리를 다 합니다. 영상의 소리도 '노래'로 두면 대사와 배경 소리로 나눕니다. 목소리만(음성 생성 결과)은 나누지 않고 목소리 다듬기와 마무리만, 효과음은 낮은 웅웅거림 자르기·가벼운 압축·음량만, 곡 전체만은 나누지 않고 곡 전체에 가볍게 마무리만 합니다.
- **나누기:** Meta 의 [Demucs](https://github.com/facebookresearch/demucs) 4.0.1(MIT)로 목소리와 나머지를 나눕니다. 빠른 판(`htdemucs`)과, 같은 모델을 네 갈래로 다시 배운 것을 모두 돌리는 정밀한 판(`htdemucs_ft`, 약 4배 느림)이 있습니다. 나누기는 44.1 kHz 스테레오에서 하고, 결과는 넣은 소리의 표본 수로 되돌립니다.
- **목소리 다듬기 순서:** 낮은 웅웅거림 자르기(노래 90 Hz 아래) → 거슬리는 울림 깎기(2~4 kHz, 6~9 kHz 에서 1/3 옥타브 평균보다 2 dB 넘게 튀어나온 봉우리를 찾아 튀어나온 만큼, 많아야 4 dB) → 'ㅅ' 소리 줄이기 1차 → 압축 두 대(큰 쪽에서 각 3 dB) → 세게 누른 사본 15% 섞기 → 'ㅅ' 소리 줄이기 2차 → 200~800 Hz 만 따뜻하게 → 10 kHz 위 1 dB 올리기. 마지막에 처리 전의 크기로 되돌려, 반주와의 크기 차이는 원래 곡 그대로 둡니다.
- **'ㅅ' 소리를 두 번 나눠 줄이는 까닭:** 압축은 큰 모음을 누르는 만큼 작은 'ㅅ' 소리를 상대로 키웁니다. 예시 곡에서 압축 한 번에 'ㅅ' 소리가 둘레의 노래보다 튀는 정도가 4.3 dB 커졌습니다. 그래서 1차는 압축 앞에서 그 순간의 전체 소리와 견줘 줄이고, 2차는 압축 뒤에서 둘레 0.3초의 노래와 견줘, 압축 앞에서 잰 정도보다 튀는 만큼만 되돌립니다. 기본은 합해 6 dB(3 + 3)입니다.
- **다시 섞기와 마무리:** 반주의 3 kHz 둘레를 1.5 dB 비우고, 목소리가 나오는 동안 반주를 1.5 dB 낮춥니다. 잔향(목소리의 약 −18 dB, 1.6초, 30 ms 늦게 시작, 300 Hz~8 kHz 만)과 메아리(8분음표 간격, 악보의 빠르기 줄 `Q:` 에서 읽음)를 따로 만들어 섞습니다. 전체를 1.5 dB 가볍게 누른 뒤 음량을 −14 LUFS(목소리 −16, 효과음 −18)로 맞추고, 4배로 촘촘히 뽑아 본 봉우리가 −1 dBTP 를 넘지 않게 제한합니다. 넣은 소리가 −50 LUFS 보다 작으면 20 dB 까지만 올리고 그렇다고 알립니다.
- **결과 창:** 다듬은 소리, 원래 소리, 목소리만, 반주만을 번갈아 들을 수 있습니다. '같은 크기로 비교'를 켜 두면 둘 가운데 큰 쪽을 음량 차이만큼 줄여 틉니다 (큰 소리가 더 좋게 들리는 착각을 막음). 그 아래에 처리 전과 후를 같은 잣대로 잰 값(음량, 가장 큰 곳, 'ㅅ' 소리가 튀는 정도, 깎은 울림, 줄인 시간)을 보입니다.
- **하는 일:** `./polish.sh [--score-file inputs/<이름>.score.abc] --input inputs/<이름>-1.flac --output outputs/<이름>.flac --mp3 outputs/<이름>.mp3 --video-out outputs/<이름>.mp4 --vocals-out outputs/<이름>.vocals.flac --backing-out outputs/<이름>.backing.flac --report outputs/<이름>.report.json --source song …` 을 작업으로 넣습니다. 나누지 않으면 목소리·반주 파일이, 영상을 넣지 않으면 mp4 가 생기지 않습니다.
- **잰 값 (워크스페이스 CPU, 2026-11-15, YuE2 예시 곡 91초):** GPU 를 다른 사용자의 작업이 쓰고 있어 나누기를 CPU 로 돌렸습니다. 나누기 22초, 전체 30~35초. 음량 −16.6 → −14.0 LUFS, 가장 큰 곳 −1.85 → −1.1 dBTP, 나눈 목소리에서 'ㅅ' 소리가 튀는 정도 −8.4 → −9.8 dB. 같은 입력을 두 번 다듬으면 결과 파일이 바이트까지 같았습니다. 목소리만·효과음·곡 전체만은 1초 안팎입니다. 나누지 않으면 GPU 를 쓰지 않으므로 1GB 만 예약하고, '노래'는 4GB 를 예약합니다 (GPU 에서 잰 값이 아직 없어 넉넉히 잡은 값).

설치는 한 사용자 홈에서 한 번 하고, 서버 관리자가 공용으로 옮깁니다 (환경 5.5GB, 가중치 402MB).

```bash
mkdir -p ~/polish-run
cp deploy/genpolish/* ~/polish-run/ && chmod +x ~/polish-run/polish.sh ~/polish-run/setup_polish.sh
~/polish-run/setup_polish.sh     # conda 환경 polish(python 3.11, torch 2.5.1 CUDA 12.4, Demucs 4.0.1), 가중치 → ~/models/demucs
```

그다음 서버에서 `sudo bash deploy/shared-apps.sh --migrate-from <설치한 사용자>` 를 돌리면 가중치(`/srv/shared/models/demucs`)와 환경(`envs/polish`)이 공용으로 옮겨지고, 모든 사용자 홈에 링크가 걸립니다. 실행 스크립트는 `PYTHONNOUSERSITE=1` 로 홈의 `~/.local` 패키지를 섞지 않고, 작업 안에서는 가중치를 내려받지 않습니다 (`TORCH_HOME=~/models/demucs`).

폴더·스크립트 이름과 예약할 VRAM 은 설정의 `genpolish_dir`, `genpolish_script`, `genpolish_vram_gb` 로 바꿉니다.

## 노드 편집기 (Advanced 메뉴)

쉬운 메뉴는 양식 하나가 결과 하나를 만듭니다. 그래서 그림을 그린 뒤 그 그림으로 3D 를 만들려면, 그림을 내려받아 3D 화면에 다시 올려야 합니다. 노드 편집기는 이 사이를 잇습니다. 콘솔 왼쪽 "생성 · Advanced" 아래의 "노드 편집기"에서 노드를 놓고 선으로 이으면, 앞 노드가 만든 파일이 뒤 노드의 입력이 됩니다. "실행"을 한 번 누르면 차례대로 만들어집니다.

```text
[글] ──글──▶ [그림 그리기] ──그림──▶ [3D 만들기] ──3D 모델
                     └──────그림──▶ [그림 그리기] (글대로 고친 그림)

[글: 분위기] ─┬─▶ [악보 쓰기] ──악보──▶ [노래 만들기] ──곡
[글: 가사] ───┴──────────────────────▶

[글: 장면] ──글──▶ [영상 만들기] ──끝 장면──▶ [영상 만들기] ──영상
[그림 그리기] ──그림(첫 장면)──▲

[글: 대본] ──────────▶ [목소리 만들기] ──목소리
[소리 파일] ──참고 목소리──▲

[글: 소리 설명(영어)] ──▶ [효과음 만들기] ──효과음, 소리를 붙인 영상
[영상 파일] ──영상──────────▲

[노래 만들기] ──곡──────▶ [소리 다듬기] ──다듬은 소리, 목소리만, 반주만
             └──쓴 악보──▲
```

노드는 열네 가지입니다.

| 노드 | 받는 것 | 내는 것 | 하는 일 |
|---|---|---|---|
| 글 | 없음 (노드 안에 적는다) | 글 | 그릴 내용, 가사, 분위기 설명, 직접 쓴 악보 |
| 사진 | 없음 (노드에 올린다) | 그림 | 3D 로 만들 사진, 고칠 그림, 영상의 첫 장면 |
| 그림 그리기 | 그릴 내용(글), 피할 내용(글, 선택), 참고 그림(그림, 4장까지, 선택) | 그림 | Qwen-Image 로 그린다. 참고 그림을 이으면 그 그림을 고친다 |
| 3D 만들기 | 사진(그림) | 3D 모델 | Pixal3D 로 GLB 를 만든다 |
| 3D 만들기 (여러 장) | 앞(그림), 오른쪽·뒤·왼쪽·위·아래(그림, 선택) | 3D 모델 | 한 물체를 여러 방향에서 찍은 사진으로 GLB 를 만든다 |
| 악보 쓰기 | 분위기(글), 가사(글) | 악보(글) | YuE2 가 멜로디와 코드를 글자 악보로 쓴다. 소리는 만들지 않는다 |
| 노래 만들기 | 분위기(글), 가사(글), 악보(글, 선택) | 곡, 쓴 악보(글) | YuE2 로 곡을 만든다. 악보를 이으면 그 악보대로 만든다 |
| 소리 파일 | 없음 (노드에 올린다) | 소리 | 목소리를 본뜰 참고 녹음, 다듬을 곡 |
| 영상 만들기 | 장면(글), 주변 소리(글, 선택), 배경 음악(글, 선택), 첫 장면(그림, 선택), 끝 장면(그림, 선택) | 영상, 끝 장면(그림) | MiniMax H3 로 소리가 든 영상을 만든다. 끝 장면을 다음 영상의 첫 장면에 이으면 영상이 이어진다 |
| 목소리 만들기 | 대본(글), 참고 목소리(소리, 4개까지, 선택), 참고 목소리의 말(글, 선택) | 목소리 | Fish Audio Drama 3 로 대본을 읽는다. 참고 목소리를 이으면 그 목소리를 본뜬다 |
| 영상 이어 붙이기 | 영상 2~6개(차례대로) | 영상, 끝 장면(그림) | 영상을 차례대로 이어 하나로 만든다. GPU 를 거의 쓰지 않는다 |
| 영상 파일 | 없음 (노드에 올린다) | 영상 | 효과음을 붙일 영상, 이어 붙일 영상, 영상 만들기의 참고 영상 |
| 효과음 만들기 | 소리 설명(글, 영어), 피할 소리(글, 선택), 영상(선택) | 효과음(소리), 소리를 붙인 영상(영상을 이었을 때) | Woosh 로 효과음을 만든다. 소리 설명과 영상 가운데 하나는 이어야 한다 |
| 소리 다듬기 | 소리 또는 영상 (둘 가운데 하나), 악보(글, 선택) | 다듬은 소리, 다듬은 목소리만, 반주만, 다듬은 소리의 영상(영상을 이었을 때) | 곡을 목소리와 반주로 나눠 목소리를 다듬고 다시 섞어 음량을 맞춘다. 악보를 이으면 메아리를 박자에 맞춘다 |

- **잇는 규칙:** 선은 같은 종류끼리만 이어집니다 (글, 그림, 3D 모델, 소리, 영상). 영상 만들기의 끝 장면만 잇고 첫 장면을 비우면 실행하지 않습니다. 점의 색이 종류입니다. 악보는 글이라서, "악보 쓰기"가 쓴 악보 대신 "글" 노드에 고쳐 적은 악보를 이어도 됩니다.
- **조절 값:** 노드마다 쉬운 메뉴와 같은 조절 값을 가집니다. 자주 쓰는 값은 노드 안에, 나머지는 노드의 "상세 설정"에 있습니다. 정의가 같은 곳(`gen.py`)에 있어 한쪽에 값을 더하면 다른 쪽에도 생깁니다.
- **실행 순서:** 노드 하나가 GPU 작업 하나입니다. 서버는 앞 노드가 끝난 뒤에야 뒤 노드를 큐에 넣습니다. GPU 가 하나라 한 번에 하나씩 돌고, 작업은 "GPU 작업" 화면에도 "그림 그리기 (노드)" 같은 이름으로 보입니다.
- **고친 곳부터만 다시 만들기:** 다시 실행하면, 종류·조절 값·입력이 지난 실행과 똑같고 결과 파일이 남아 있는 노드는 새로 만들지 않고 그 결과를 다시 씁니다. 예를 들어 "3D 만들기"의 삼각형 수만 바꿔 다시 실행하면 그림은 다시 그리지 않습니다. 글을 고치면 그 뒤가 모두 다시 돕니다. 배포로 그 생성기의 실행 스크립트가 바뀌었을 때도 다시 만듭니다. 생성기 폴더의 시작 스크립트와, 그 폴더의 링크가 가리키는 공용 실행 파일(`/shared/models/apps/…`)의 내용을 지문으로 남겨 비교합니다. 모델·코드 저장소 같은 폴더는 보지 않습니다. 2026-11-16 '모양 먼저'의 정면 고침 뒤에 고치기 전 판의 3D 를 계속 다시 쓴 일이 있어 넣었습니다.
- **실패했을 때:** 한 노드가 실패하면 그 결과를 받는 노드는 건너뜁니다. 이어져 있지 않은 다른 가지는 계속 돕니다.
- **파일이 생기는 곳:** 실행마다 홈에 `flow-runs/<날짜-무작위 글자>/` 폴더가 하나 생기고, 노드의 결과가 `<노드 번호>.<출력 이름><확장자>` 로 들어갑니다 (예: `n2.image.png`, `n3.mesh.glb`). "사진"·"소리 파일"·"영상 파일" 노드에 올린 파일은 `flow-inputs/` 에 갑니다.
- **지난 실행:** 화면 아래 목록에서 "열기"를 누르면 그때의 그래프와 결과가 캔버스에 돌아옵니다. 지우면 그 실행 폴더의 파일도 지워지는데, 다른 실행이 다시 쓰고 있는 결과 파일은 남깁니다.
- **다른 사용자에게 보이는 것:** 쉬운 메뉴와 같습니다. 작업이 있다는 사실과 노드 종류 이름까지만 보이고, 글은 파일로 넘기므로 작업 명령에 남지 않습니다.

잰 값입니다 (RTX 3090 24GB, 2026-11-14). 편집기가 만드는 것과 같은 명령을 워크스페이스에서 차례로 돌렸습니다. 큐에서 기다리는 시간과 노드 사이에 서버가 다음 작업을 넣는 시간(몇 초)은 들어 있지 않습니다.

- 글 → 그림 그리기(1024×1024) 24초 → 그 그림으로 3D 만들기(삼각형 10만 개, 텍스처 2048) 103초. GLB 는 삼각형 96,410개, 3.9MB.
- 같은 그림을 참고 그림으로 이어 글대로 고치기 27초. GPU 메모리는 최대 약 20.5GiB 로, 예약한 21GB 에 거의 닿습니다.
- 분위기·가사 → 악보 쓰기 12초 → 그 악보로 노래 만들기 40초 (91초짜리 곡).

아직 못 하는 것이 있습니다. 노드 하나는 모델 한 번을 통째로 돌립니다. 한 모델 안의 단계를 따로 떼어 잇지는 못합니다. 예를 들어 3D 의 형상과 색까지 만든 것을 그대로 두고 GLB 만 다른 삼각형 수로 다시 뽑거나, 그림 모델의 중간 결과를 다른 노드로 넘기는 일은 안 됩니다. 또 작업마다 모델을 새로 읽으므로, 노드가 많으면 그만큼 모델 읽는 시간이 듭니다.

노드 종류를 더할 때는 `ironlab/flow.py` 의 `NODE_TYPES` 에 입력(`In`), 출력(`Out`), 조절 값을 적은 `NodeType` 하나를 더합니다. 실행 스크립트가 입력과 출력을 `--옵션 <파일>` 꼴로 받으면 됩니다. 폴더 이름은 설정의 `flow_dir`, `flow_inputs_dir` 로 바꿉니다.

## 사용법

### 관리자
```bash
ironlab login --url <콘솔 주소> --token il_...     # 예: https://gpubox.tailnet-xxxx.ts.net, http://192.168.0.10:8700
ironlab admin add seoyeon --tailscale seoyeon@github --cpus 8 --mem 24 --disk 300
ironlab admin set jihoon --max-vram 12        # VRAM 상한
ironlab admin set jihoon --disable            # 계정 정지 (워크스페이스도 중지)
ironlab admin token seoyeon                   # 토큰 재발급
ironlab admin events
```

### 사용자
```bash
ironlab key set ~/.ssh/id_ed25519.pub
ironlab ws start                              # → ssh -p 22002 dev@<서버 주소>
ironlab job submit --vram 12 --workdir projects/run1 -f -- \
    'conda activate trellis2 && python example.py --resolution 1536'
ironlab job ls -a
ironlab files put model.png --to inputs
ironlab files get scratch:out/result.glb
ironlab top                                   # 실시간 현황
```

## 문제가 생겼을 때

| 보이는 것 | 까닭 | 할 일 |
|---|---|---|
| 모델을 올리거나 워크스페이스를 켤 때 `The container name "/ironlab-llm" is already in use` (409) | Docker가 그 이름을 쥔 채 놓지 않습니다. `docker ps -a`에는 그 컨테이너가 없습니다. | 따로 할 일은 없습니다. ironlab이 이름을 쥔 컨테이너를 ID로 지워 보고, 안 되면 예비 이름(`ironlab-llm.r2` 등)으로 띄웁니다. 대시보드에 "예비 이름으로 띄웠습니다" 알림이 뜹니다. 서버를 재부팅한 뒤 다시 올리면 원래 이름으로 돌아옵니다. |
| 위 오류가 그대로 화면에 나옴 | 예비 이름 세 개까지 모두 막혔습니다. | 서버에서 Docker를 다시 시작하거나 재부팅합니다. |
| 콘솔이 502 | ironlab은 Docker가 떠 있어야 돕니다(`Requires=docker.service`). Docker가 멈추거나 다시 시작하는 동안에는 ironlab도 내려갑니다. | `systemctl is-active containerd docker ironlab`으로 어디서 멈췄는지 봅니다. Docker가 `deactivating`에서 넘어가지 않으면 재부팅합니다. |
| 재부팅 뒤 워크스페이스와 모델이 꺼져 있음 | 컨테이너를 자동으로 다시 켜지 않습니다. | 사용자가 콘솔에서 워크스페이스를 시작하고, 모델을 다시 올립니다. 홈과 스크래치의 파일은 그대로입니다. |
| 대시보드의 논리 코어 수가 줄어듦 | 서버에서 논리 코어 일부를 꺼 두었습니다(`/sys/devices/system/cpu/cpuN/online`에 0). | 정상입니다. 코어 칸과 `/metrics`의 `core` 라벨은 실제 코어 번호를 쓰고, 꺼 둔 번호는 "논리 코어별 사용률" 옆에 나옵니다. |

## 설정

`deploy/config.example.toml` 참고. 모든 항목은 `IRONLAB_<대문자>` 환경변수로 덮어쓸 수 있습니다. 서버마다 자주 바꾸는 값은 `site_name`(화면 이름), `public_host`·`public_url`(안내 주소), `gpu_index`, `default_cpus`·`default_mem_gb`·`default_disk_gb`(새 사용자 기본 한도), `max_running_jobs_per_user`, `idle_stop_hours`(유휴 워크스페이스 자동 중지)입니다. Prometheus는 `/metrics` (로컬 또는 관리자 토큰)를 긁어가면 사용자별 메트릭이 남습니다.

## 개발

```bash
pip install -e '.[dev]'
pytest                       # mock 백엔드로 스케줄러·파일 보호·API 전체 테스트
```

| 모듈 | 역할 |
|---|---|
| `scheduler.py` | VRAM 예약 기반 작업 큐 (`plan_admissions`는 순수 함수) |
| `runtime.py` | Docker / Mock 컨테이너 런타임. 컨테이너는 이름이 아니라 라벨(`ironlab.name`)로 찾습니다 |
| `gpu.py` | NVML 수집 |
| `monitor.py` | 주기 수집, 사용자별 집계, 유휴 워크스페이스 자동 중지, 알림 |
| `files.py` | 경로 보호·쿼터·업로드 |
| `api.py`, `web/` | REST API, 대시보드 |
| `cli.py` | `ironlab` CLI |

## 라이선스

[MIT](LICENSE). 생성 화면이 쓰는 모델(Pixal3D, Qwen-Image, YuE, MiniMax H3, Fish Audio, Woosh, Demucs, MV-Adapter 등)과 llama.cpp 로 올리는 LLM 은 각자의 라이선스를 따릅니다.
