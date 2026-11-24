"""설정 로딩.

우선순위: 환경변수(IRONLAB_*) > 설정 파일(TOML) > 기본값.
설정 파일 경로는 IRONLAB_CONFIG, 없으면 /etc/ironlab/config.toml.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    # 실행 모드: True면 Docker/GPU 없이 가짜 백엔드로 동작 (개발·데모·테스트용)
    mock: bool = False

    # API 서버
    host: str = "127.0.0.1"          # tailscale serve·리버스 프록시 뒤에 두면 127.0.0.1, LAN 에 바로 열면 0.0.0.0
    port: int = 8700
    trust_tailscale_headers: bool = False  # tailscale serve가 붙이는 사용자 헤더 신뢰 여부
    public_host: str = ""             # 사용자에게 안내할 서버 주소 (SSH 접속에 씀. 예: gpubox.tailnet-xxxx.ts.net, 192.168.0.10)
    public_url: str = ""              # 사용자에게 안내할 콘솔 주소 (예: http://192.168.0.10:8700). 비우면 https://<public_host>
    site_name: str = "ironlab"        # 로그인 화면·옆 메뉴·브라우저 탭에 보일 이름 (예: "비전연구실 GPU")

    # 경로
    data_dir: Path = Path("/var/lib/ironlab")     # DB, 작업 로그, SSH 키
    users_root: Path = Path("/srv/users")         # 사용자 홈
    shared_root: Path = Path("/srv/shared")       # models/, datasets/ (사용자에겐 읽기 전용)
    scratch_root: Path = Path("/srv/scratch")     # 사용자별 임시 공간

    # 컨테이너
    workspace_image: str = "ironlab/workspace:latest"
    ssh_bind_ip: str = "0.0.0.0"      # Tailscale IP(100.x.y.z)로 지정 권장
    ssh_port_base: int = 22000        # 사용자 SSH 포트 = base + (uid - uid_base)
    uid_base: int = 20000
    shm_gb: int = 8
    hf_offline: bool = True           # 공용 모델 캐시만 쓰도록 HF_HUB_OFFLINE=1

    # 사용자 기본 할당량
    default_cpus: float = 8.0
    default_mem_gb: int = 16
    default_disk_gb: int = 200

    # GPU 스케줄링
    gpu_vendor: str = "auto"          # nvidia | amd | auto (이 기계에서 찾는다)
    gpu_index: int = 0                # 관리할 GPU 번호 (nvidia-smi 순서, AMD 는 /sys/class/drm/card 번호 순). 컨테이너에는 이 한 장만 보인다
    rocm_gfx_override: str = ""       # AMD: ROCm 이 공식 지원하지 않는 카드에 HSA_OVERRIDE_GFX_VERSION 으로 넘길 값 (예: RX 6000 계열 10.3.0)
    gpu_total_vram_gb: float = 0.0    # 0이면 NVML에서 자동 감지 (mock은 24)
    vram_reserve_gb: float = 1.0      # 항상 남겨둘 여유분
    max_running_jobs_per_user: int = 1
    starvation_minutes: int = 30      # 맨 앞 작업이 이만큼 기다리면 뒤 작업 끼워넣기 중단
    vram_overuse_action: str = "warn"  # warn | kill — 선언보다 많이 쓰는 작업 처리

    # 로컬 LLM (llama.cpp 서버, 모두가 공유하는 인스턴스 하나)
    llm_image: str = ""               # 비우면 GPU 에 맞게: NVIDIA server-cuda, AMD server-rocm (ghcr.io/ggml-org/llama.cpp)
    llm_port: int = 8800              # llama-server 를 127.0.0.1 의 이 포트로만 연다
    llm_idle_unload_min: float = 20.0  # 이만큼 안 쓰면 자동으로 내려 GPU 를 돌려준다 (0이면 끔)
    network_name: str = "ironlab"     # 워크스페이스·작업 컨테이너용 Docker 네트워크
    network_subnet: str = "172.30.0.0/24"
    network_gateway: str = "172.30.0.1"
    internal_port: int = 8701         # 컨테이너 안에서 쓰는 LLM 프록시 (게이트웨이 IP 에만 열림)

    # 생성 화면. 사용자 홈의 폴더에 실행 스크립트가 있으면 그 사용자에게 그 화면이 켜진다
    # 3D 생성 (사진 한 장 → GLB)
    gen3d_dir: str = "pixal3d-run"    # 홈 기준 폴더. 그 안의 inputs/ 에 사진을 두고 outputs/ 에 결과를 쓴다
    gen3d_script: str = "pixal3d.sh"  # 실행 스크립트. `--image <사진> [--image …] [--views front,right,…] --output <결과.glb> …` 으로 불린다
    gen3d_vram_gb: float = 18.0       # 작업 하나에 예약할 VRAM (Pixal3D 절약 모드가 최대 약 16GB 를 쓴다)
    gen3d_mv_vram_gb: float = 20.0    # 사진을 여러 장 넣었을 때 예약할 VRAM (여러 장 모델이 4~5장에서 최대 약 17.9GB 를 썼다)
    # 이미지 생성 (글 → 그림, 참고 그림 + 글 → 고친 그림)
    genimg_dir: str = "qwen-image-run"      # 홈 기준 폴더. inputs/ 에 글과 참고 그림을 두고 outputs/ 에 결과를 쓴다
    genimg_script: str = "qwen-image.sh"    # 실행 스크립트. `--prompt-file <글> <결과.png> --model … --size … --seed N` 으로 불린다
    genimg_vram_gb: float = 21.0            # 예약할 VRAM (Qwen-Image-2.1 이 글만으로 최대 약 17GB, 참고 그림 한 장이면 약 20GB 를 쓴다)
    # 음악 생성 (분위기 설명 + 가사 → 노래)
    genmusic_dir: str = "yue2-run"          # 홈 기준 폴더. inputs/ 에 글과 가사를 두고 outputs/ 에 결과를 쓴다
    genmusic_script: str = "yue2.sh"        # 실행 스크립트. `--style-file <글> --lyrics-file <가사> --output <결과.flac> …` 으로 불린다
    genmusic_vram_gb: float = 16.0          # 예약할 VRAM (YuE2 가 91초 곡에 약 9GB, 만든 쪽이 잰 최댓값이 14.1GiB)
    # 영상 생성 (장면 설명 [+ 첫·끝 장면 사진] → 소리가 든 영상)
    genvideo_dir: str = "minimax-h3-run"    # 홈 기준 폴더. inputs/ 에 글과 사진을 두고 outputs/ 에 결과를 쓴다
    genvideo_script: str = "h3.sh"          # 실행 스크립트. `--prompt-file <장면> --output <결과.mp4> --size … --seconds …` 으로 불린다
    genvideo_vram_gb: float = 22.0          # 예약할 VRAM (MiniMax H3. 쓰지 않는 무게를 CPU 메모리로 내리며 GPU 를 거의 다 쓴다. 23 이면 여유(24-1)를 넘겨 시작하지 못한다)
    # 음성 생성 (대본 [+ 참고 목소리] → 목소리)
    genvoice_dir: str = "drama3-run"        # 홈 기준 폴더. inputs/ 에 대본과 참고 목소리를 두고 outputs/ 에 결과를 쓴다
    genvoice_script: str = "drama3.sh"      # 실행 스크립트. `--text-file <대본> --output <결과.wav> [--ref <참고 목소리>]` 로 불린다
    genvoice_vram_gb: float = 18.0          # 예약할 VRAM (Fish Audio Drama 3 4비트, CPU 층 16개로 최대 약 16.7GB)
    # 효과음 생성 (소리 설명 [+ 영상] → 효과음)
    gensfx_dir: str = "woosh-run"           # 홈 기준 폴더. inputs/ 에 글과 영상을 두고 outputs/ 에 결과를 쓴다
    gensfx_script: str = "woosh.sh"         # 실행 스크립트. `--prompt-file <설명> [--video <영상>] --output <결과.wav> …` 로 불린다
    gensfx_vram_gb: float = 7.0             # 예약할 VRAM (Woosh 영상 모델이 최대 약 6GB, 글 모델이 약 4GB 를 쓴다)
    # 소리 다듬기 (곡·목소리·효과음·영상의 소리 → 다듬은 소리)
    genpolish_dir: str = "polish-run"       # 홈 기준 폴더. inputs/ 에 올린 소리를 두고 outputs/ 에 결과를 쓴다
    genpolish_script: str = "polish.sh"     # 실행 스크립트. `--input <소리나 영상> --output <결과.flac> --source song|voice|sfx|mix …` 로 불린다
    genpolish_vram_gb: float = 4.0          # 예약할 VRAM ('노래'로 목소리와 반주를 나눌 때. 나누지 않으면 1GB 만 잡는다)
    # 여러 방향 그림 (사진 한 장 → 물체를 6·4방향에서 본 그림, 3D 만들기에 넘길 묶음)
    genmv_dir: str = "mvadapter-run"        # 홈 기준 폴더. inputs/ 에 사진과 설명을 두고 outputs/ 에 결과를 쓴다
    genmv_script: str = "mvadapter.sh"      # 실행 스크립트. `--image <사진> --output <모은 그림.png> --views-out <묶음.json> …` 으로 불린다
    genmv_vram_gb: float = 17.0             # 예약할 VRAM (MV-Adapter SDXL 6방향이 최대 약 15.1GiB 를 썼다)
    # 3D 생성 (모양 먼저): 사진 한 장 3D 로 모양을 정하고, 그 모양 위에 MV-Adapter(ig2mv)가 여섯 방향 색을 칠해 입힌 GLB
    genshape_dir: str = "shapefirst-run"    # 홈 기준 폴더. inputs/ 에 사진과 설명을 두고 outputs/ 에 결과를 쓴다
    genshape_script: str = "shapefirst.sh"  # 실행 스크립트. `--image <사진> --output <결과.glb> --views-image <칠한 그림.png> …` 로 불린다
    genshape_vram_gb: float = 18.0          # 예약할 VRAM (단계마다 따로 쓰고, 가장 큰 단계인 사진 한 장 3D 가 약 16GB)
    # 노드 편집기: 노드를 이어 붙인 그래프를 작업의 사슬로 실행한다. 노드는 위 아홉 종류의 실행 스크립트를 그대로 쓴다
    flow_dir: str = "flow-runs"             # 홈 기준 폴더. 실행마다 그 안에 폴더 하나를 만들어 노드의 결과를 쓴다
    flow_inputs_dir: str = "flow-inputs"    # 홈 기준 폴더. '사진'·'소리 파일' 노드에 올린 파일을 둔다

    # 모니터링
    monitor_interval_s: float = 10.0  # 전체 수집 주기: 컨테이너 목록, 작업 큐, 유휴 정리
    sample_interval_s: float = 1.0    # 누군가 대시보드를 보는 동안의 사용량 갱신 주기
    log_retention_days: int = 30      # 끝난 작업의 로그를 보관하는 기간 (0이면 지우지 않음)
    event_retention_days: int = 365   # 감사 로그와 LLM 사용 기록을 보관하는 기간 (0이면 지우지 않음)
    history_points: int = 360         # 10초 × 360 = 1시간
    idle_stop_hours: float = 12.0     # 0이면 비활성
    vram_alert_gb: float = 0.0       # 이만큼 넘게 쓰면 알림. 0이면 GPU 용량의 92% (24GB 카드면 약 22GB)

    def llm_image_for(self, vendor: str) -> str:
        if self.llm_image:
            return self.llm_image
        return "ghcr.io/ggml-org/llama.cpp:" + ("server-rocm" if vendor == "amd" else "server-cuda")

    @property
    def console_url(self) -> str:
        """사용자에게 안내할 콘솔 주소. 모르면 빈 문자열 (화면은 브라우저 주소를 대신 쓴다)."""
        if self.public_url:
            return self.public_url.rstrip("/")
        return f"https://{self.public_host}" if self.public_host else ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ironlab.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"

    @property
    def llm_models_dir(self) -> Path:
        return self.shared_root / "models" / "llm"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.logs_dir, self.keys_dir, self.users_root,
                  self.shared_root / "models", self.shared_root / "models" / "llm",
                  self.shared_root / "datasets",
                  self.scratch_root):
            p.mkdir(parents=True, exist_ok=True)


def _coerce(value: str, target_type):
    if target_type is bool:
        return value.strip().lower() in ("1", "true", "yes", "on")
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is Path:
        return Path(value)
    return value


def load_settings(**overrides) -> Settings:
    s = Settings()
    # 어노테이션은 문자열이므로 기본값의 실제 타입으로 변환 규칙을 정한다
    real_types = {}
    for f in fields(Settings):
        t = type(getattr(s, f.name))
        real_types[f.name] = Path if issubclass(t, Path) else t

    cfg_path = Path(os.environ.get("IRONLAB_CONFIG", "/etc/ironlab/config.toml"))
    if cfg_path.is_file():
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)
        for k, v in data.items():
            if k in real_types:
                setattr(s, k, _coerce(str(v), real_types[k]))

    for k, t in real_types.items():
        env = os.environ.get(f"IRONLAB_{k.upper()}")
        if env is not None:
            setattr(s, k, _coerce(env, t))

    for k, v in overrides.items():
        setattr(s, k, v)
    return s
