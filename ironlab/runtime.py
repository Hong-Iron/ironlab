"""컨테이너 런타임. 실제 Docker 구현과 개발용 Mock 구현을 제공한다.

ironlab이 만든 컨테이너는 모두 라벨로 식별한다:
  ironlab.managed=1, ironlab.kind=workspace|job, ironlab.user=<이름>, ironlab.job_id=<id>
"""

from __future__ import annotations

import logging
import random
import re
import shlex
import time
from abc import ABC, abstractmethod
from pathlib import Path

from .config import Settings
from .users import ssh_port

log = logging.getLogger("ironlab.runtime")
CID_RE = re.compile(r"([0-9a-f]{64})")
LABEL = "ironlab.managed"
# 논리 이름. 보통 Docker 이름과 같지만, Docker 가 이름을 놓지 않을 때는 예비 이름으로 띄우고 이 라벨로 찾는다
NAME_LABEL = "ironlab.name"
SPARE_NAMES = 3      # 이름이 묶였을 때 써 볼 예비 이름 수 (.r2 .r3 .r4)
HOLDER_RE = re.compile(r"is already in use by container\W*([0-9a-f]{12,64})?")


def _parse_docker_time(value: str | None) -> float | None:
    """'2026-11-12T01:23:45.123456789Z' 형식(나노초 포함)을 epoch로."""
    if not value or value.startswith("0001"):
        return None
    from datetime import datetime, timezone
    base = value.rstrip("Z").split("+")[0]
    if "." in base:
        sec, frac = base.split(".", 1)
        base = f"{sec}.{frac[:6]}"
    try:
        return datetime.fromisoformat(base).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def ws_name(user: str) -> str:
    return f"ironlab-ws-{user}"


def job_name(job_id: int) -> str:
    return f"ironlab-job-{job_id}"


LLM_NAME = "ironlab-llm"


def spare_names(name: str) -> list[str]:
    """컨테이너를 띄울 때 차례로 써 볼 이름. 사용자 이름에는 점이 들어갈 수 없으므로(users.NAME_RE)
    예비 이름이 다른 사용자의 컨테이너 이름과 겹치지 않는다."""
    return [name] + [f"{name}.r{i}" for i in range(2, 2 + SPARE_NAMES)]


def name_holder(error: Exception) -> str | None:
    """'이미 쓰이는 이름' 오류면 그 이름을 쥔 컨테이너 ID 를 돌려준다 (ID 를 못 읽으면 빈 문자열). 다른 오류면 None.

    Docker 오류 예: Conflict. The container name "/ironlab-llm" is already in use by container "9805084b…".
    """
    m = HOLDER_RE.search(f"{getattr(error, 'explanation', '') or ''} {error}")
    return (m.group(1) or "") if m else None
HOST_HEADROOM_GB = 2      # 컨테이너가 다 써도 호스트(Docker, ironlab, 커널)에 남겨 둘 메모리


def mem_limit(requested_gb: float, host_total_bytes: int | None = None) -> str:
    """컨테이너 메모리 한도. 할당량이 실제 RAM 보다 크면(예: 62.6GB 기계에 64GB) 한도가 없는 것과 같아서,
    컨테이너 하나가 호스트 전체를 메모리 부족으로 몰 수 있다. 실제 RAM 에서 여유분을 뺀 값으로 자른다."""
    if host_total_bytes is None:
        import psutil
        host_total_bytes = psutil.virtual_memory().total
    cap_mb = max(1024, int(host_total_bytes / 2**20) - HOST_HEADROOM_GB * 1024)
    return f"{min(int(requested_gb * 1024), cap_mb)}m"


def llm_command(model: dict) -> list[str]:
    """llama-server 실행 인자. 모델 파일은 /models 아래에 읽기 전용으로 붙는다."""
    cmd = ["-m", f"/models/{model['file']}", "--host", "0.0.0.0", "--port", "8080",
           "-ngl", "999", "-c", str(model["ctx"]), "-np", str(model["parallel"]),
           "--jinja", "-fa", "on", "--metrics", "--alias", model["name"]]
    if model.get("mmproj"):
        cmd += ["--mmproj", f"/models/{model['mmproj']}"]
    if model.get("extra_args"):
        cmd += shlex.split(model["extra_args"])
    return cmd


class Runtime(ABC):
    def __init__(self, settings: Settings):
        self.s = settings

    @abstractmethod
    def start_workspace(self, user: dict) -> dict: ...
    @abstractmethod
    def stop_workspace(self, username: str) -> bool: ...
    @abstractmethod
    def get_workspace(self, username: str) -> dict | None: ...
    @abstractmethod
    def run_job(self, job: dict, user: dict) -> str: ...
    @abstractmethod
    def inspect(self, cid: str) -> dict | None: ...
    @abstractmethod
    def logs(self, cid: str, tail: int | None = None) -> str: ...
    @abstractmethod
    def kill(self, cid: str) -> None: ...
    @abstractmethod
    def remove(self, cid: str) -> None: ...
    @abstractmethod
    def list_managed(self) -> list[dict]: ...
    @abstractmethod
    def stats(self, cid: str) -> dict: ...
    def fast_stats(self, cid: str, pid: int | None = None) -> dict | None:
        """stats 와 같은 모양을 즉시 돌려준다. 빠른 경로가 없으면 None (호출한 쪽이 stats 로 대신한다)."""
        return None

    @abstractmethod
    def ssh_sessions(self, cid: str) -> int: ...
    @abstractmethod
    def container_for_pid(self, pid: int) -> str | None: ...
    @abstractmethod
    def ensure_network(self) -> str: ...
    @abstractmethod
    def ip_map(self) -> dict[str, dict]: ...
    @abstractmethod
    def start_llm(self, model: dict) -> dict: ...
    @abstractmethod
    def stop_llm(self) -> bool: ...
    @abstractmethod
    def get_llm(self) -> dict | None: ...

    # 공통: 워크스페이스/작업 컨테이너의 마운트 구성
    def mounts_for(self, user: dict, include_keys: bool) -> list[tuple[str, str, bool]]:
        name = user["name"]
        m = [
            (str(self.s.users_root / name), "/home/dev", False),
            (str(self.s.scratch_root / name), "/scratch", False),
            (str(self.s.shared_root / "models"), "/shared/models", True),
            (str(self.s.shared_root / "datasets"), "/shared/datasets", True),
        ]
        if include_keys:
            m.append((str(self.s.keys_dir / name), "/etc/ironlab-keys", True))
        return m

    def env_for(self, user: dict) -> dict:
        env = {
            "IRONLAB_USER": user["name"],
            "IRONLAB_UID": str(user["uid"]),
            "HOME": "/home/dev",
            "HF_HUB_CACHE": "/shared/models/hub",
            "TORCH_HOME": "/shared/models/torch",
        }
        if self.s.hf_offline:
            env["HF_HUB_OFFLINE"] = "1"
        # 공유 LLM: 컨테이너 IP 로 사용자를 알아보므로 키는 아무 값이나 된다
        base = f"http://{self.s.network_gateway}:{self.s.internal_port}/v1"
        env.update({"OPENAI_BASE_URL": base, "OPENAI_API_BASE": base, "OPENAI_API_KEY": "ironlab",
                    "IRONLAB_LLM_URL": base})
        return env


# 컨테이너 안 프로세스가 죽을 때 코어 덤프를 만들지 않게 한다.
# 호스트(systemd-coredump)는 덤프를 파이프로 받는데, 컨테이너 프로세스의 덤프가 끝나지 않으면
# 그 프로세스는 SIGKILL 로도 죽지 않아 컨테이너를 멈출 수도 지울 수도 없게 된다 (재부팅만 답).
# 값이 0이 아니라 1인 이유: 파이프 방식에서는 커널이 0을 '제한 없음'으로 취급해 덤프를 그대로 보내고,
# 정확히 1바이트일 때만 덤프를 건너뛴다. 파일 방식에서는 0과 1 모두 덤프를 만들지 않는다.
NO_CORE_DUMP = [{"name": "core", "soft": 1, "hard": 1}]

# 컨테이너 출력 로그의 크기 제한. Docker 기본값은 제한이 없어, 진행률을 계속 찍는 학습 작업 하나가
# 디스크를 채울 수 있다. 가장 최근 약 100MB 만 남긴다 (작업이 끝나면 이 내용이 작업 로그로 저장된다).
LOG_LIMIT = {"type": "json-file", "config": {"max-size": "50m", "max-file": "2"}}


class CgroupStats:
    """cgroup v2 파일에서 컨테이너의 CPU·메모리를 바로 읽는다.

    `docker stats` 는 호출마다 1~2초를 기다리므로 1초 주기 갱신에 쓸 수 없다. 커널이 내주는
    누적 CPU 시간(cpu.stat 의 usage_usec)을 직접 읽어, 지난번 읽은 값과의 차이로 코어 수를 계산한다.
    """

    def __init__(self, root: Path = Path("/sys/fs/cgroup")):
        self.root = root
        self._dirs: dict[str, Path] = {}
        self._prev: dict[str, tuple[int, float]] = {}   # cid → (누적 CPU 마이크로초, 읽은 시각)

    def locate(self, cid: str, pid: int | None = None) -> Path | None:
        d = self._dirs.get(cid)
        if d is not None:
            return d
        candidates = [self.root / "system.slice" / f"docker-{cid}.scope",   # systemd cgroup 드라이버
                      self.root / "docker" / cid]                            # cgroupfs 드라이버
        if pid:
            try:
                for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
                    if line.startswith("0::"):
                        candidates.append(self.root / line[3:].lstrip("/"))
            except OSError:
                pass
        for d in candidates:
            if (d / "cpu.stat").is_file() and (d / "memory.current").is_file():
                self._dirs[cid] = d
                return d
        return None

    def read(self, cid: str, pid: int | None = None, now: float | None = None) -> dict | None:
        d = self.locate(cid, pid)
        if d is None:
            return None
        try:
            usage = next(int(ln.split()[1]) for ln in (d / "cpu.stat").read_text().splitlines()
                         if ln.startswith("usage_usec"))
            current = int((d / "memory.current").read_text())
            inactive = next((int(ln.split()[1]) for ln in (d / "memory.stat").read_text().splitlines()
                             if ln.startswith("inactive_file ")), 0)
            raw_max = (d / "memory.max").read_text().strip()
            limit = 0 if raw_max == "max" else int(raw_max)
        except (OSError, ValueError, StopIteration):
            self.forget(cid)        # 컨테이너가 사라졌거나 다시 만들어졌다
            return None
        now = time.monotonic() if now is None else now
        prev = self._prev.get(cid)
        self._prev[cid] = (usage, now)
        cores = 0.0
        if prev and now > prev[1] and usage >= prev[0]:
            cores = (usage - prev[0]) / 1e6 / (now - prev[1])
        # 파일 캐시(inactive_file)는 커널이 언제든 비울 수 있어 사용량에서 뺀다 (docker stats 와 같은 기준)
        return {"cpu_cores": round(cores, 2), "mem_bytes": max(current - inactive, 0), "mem_limit": limit}

    def forget(self, cid: str) -> None:
        self._dirs.pop(cid, None)
        self._prev.pop(cid, None)

# ─────────────────────────────── Docker ───────────────────────────────

class DockerRuntime(Runtime):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        import docker
        from docker.types import DeviceRequest, Mount
        self._docker = docker
        self._Mount = Mount
        # 관리하는 GPU 한 장만 컨테이너에 보인다. 여러 장인 서버에서 스케줄러가 모르는 GPU 를 쓰지 않게 한다
        self._gpu = [DeviceRequest(device_ids=[str(settings.gpu_index)], capabilities=[["gpu"]])]
        self.client = docker.from_env()
        self._cgroup = CgroupStats()

    def _get(self, name_or_id: str):
        try:
            return self.client.containers.get(name_or_id)
        except self._docker.errors.NotFound:
            return None

    def _find(self, name: str) -> list:
        """논리 이름으로 컨테이너를 모두 찾는다: Docker 이름이 그 이름인 것과, 예비 이름으로 띄운 것(라벨로)."""
        found = {}
        c = self._get(name)
        if c is not None:
            found[c.id] = c
        for c in self.client.containers.list(all=True, filters={"label": f"{NAME_LABEL}={name}"}):
            found.setdefault(c.id, c)
        return list(found.values())

    def _find_one(self, name: str):
        """논리 이름으로 컨테이너 하나를 찾는다 (실행 중인 것이 먼저). 이름이 그대로인 평소에는 Docker 를 한 번만 부른다."""
        c = self._get(name)
        if c is not None:
            return c
        cs = self.client.containers.list(all=True, filters={"label": f"{NAME_LABEL}={name}"})
        return next((c for c in cs if c.status == "running"), cs[0] if cs else None)

    def _discard(self, c) -> None:
        """컨테이너를 강제로 지운다. 찾은 뒤 지우기 전에 사라졌으면 넘어간다 (지우려던 것이 이미 없다)."""
        try:
            c.remove(force=True)
        except self._docker.errors.NotFound:
            pass

    def _evict(self, cid: str) -> bool:
        """이름을 쥐고 있는 컨테이너를 ID 로 강제로 지운다. 지웠으면 True."""
        if not cid:
            return False
        try:
            self.client.api.remove_container(cid, force=True)
            return True
        except Exception:
            return False

    def _run_named(self, name: str, image: str, *, labels: dict, **kwargs):
        """컨테이너를 만들어 띄운다. 모든 컨테이너에 코어 덤프 금지와 로그 크기 제한을 건다.

        Docker 가 이름을 쥐고 놓지 않을 때가 있다: 목록(docker ps -a)에는 그 컨테이너가 없는데, 같은 이름으로
        만들려고 하면 '이미 쓰이는 이름'(409 Conflict)이라며 거절한다. 그러면 이름을 쥔 컨테이너를 ID 로 지워 보고,
        그래도 안 되면 예비 이름(.r2 …)으로 띄운다. ironlab 은 컨테이너를 라벨로 찾으므로 이름이 달라도 똑같이 동작한다.
        """
        labels = labels | {NAME_LABEL: name}
        last: Exception | None = None
        for cand in spare_names(name):
            for second_try in (False, True):
                try:
                    c = self.client.containers.run(image, name=cand, detach=True, labels=labels,
                                                   ulimits=NO_CORE_DUMP, log_config=LOG_LIMIT, **kwargs)
                except self._docker.errors.APIError as e:
                    holder = name_holder(e)
                    if holder is None:
                        raise                   # 이름 문제가 아닌 오류 (이미지 없음, 포트 충돌 등)
                    last = e
                    if second_try or not self._evict(holder):
                        break                   # 이 이름은 포기하고 다음 이름으로
                    continue                    # 이름을 쥔 컨테이너를 지웠다. 같은 이름으로 한 번 더
                if cand != name:
                    log.warning("Docker 가 이름 %s 을(를) 놓지 않아 %s (으)로 띄웠습니다. 서버를 재부팅하면 풀립니다",
                                name, cand)
                return c
        raise last

    @staticmethod
    def _info(c) -> dict:
        st = c.attrs.get("State", {})
        started = st.get("StartedAt")
        ts = _parse_docker_time(started)
        labels = c.labels or {}
        logical = labels.get(NAME_LABEL)
        return {
            "id": c.id, "name": c.name, "kind": labels.get("ironlab.kind"), "pid": st.get("Pid") or None,
            "renamed_from": logical if logical and logical != c.name else None,
            "user": labels.get("ironlab.user"), "job_id": labels.get("ironlab.job_id"),
            "state": st.get("Status", c.status), "exit_code": st.get("ExitCode"),
            "started_at": ts, "model": labels.get("ironlab.model"),
            "loaded_by": labels.get("ironlab.loaded_by"),
        }

    def _mounts(self, user, include_keys):
        return [self._Mount(target=t, source=s, type="bind", read_only=ro)
                for s, t, ro in self.mounts_for(user, include_keys)]

    def start_workspace(self, user: dict) -> dict:
        name = ws_name(user["name"])
        found = self._find(name)
        alive = next((c for c in found if c.status == "running"), None)
        for c in found:
            if c is not alive:
                self._discard(c)  # 멈춘 컨테이너는 새 할당량으로 다시 만든다
        if alive is not None:
            return self._info(alive)
        c = self._run_named(
            name, self.s.workspace_image,
            hostname=f"{user['name']}-ws",
            labels={LABEL: "1", "ironlab.kind": "workspace", "ironlab.user": user["name"]},
            environment=self.env_for(user),
            mounts=self._mounts(user, include_keys=True),
            device_requests=self._gpu,
            nano_cpus=int(user["cpus"] * 1e9),
            mem_limit=mem_limit(user["mem_gb"]),
            memswap_limit=mem_limit(user["mem_gb"]),
            shm_size=f"{self.s.shm_gb}g",
            pids_limit=4096,
            ports={"22/tcp": (self.s.ssh_bind_ip, ssh_port(self.s, user))},
            network=self.s.network_name,
            cap_drop=["ALL"],
            cap_add=["CHOWN", "SETUID", "SETGID", "SYS_CHROOT", "AUDIT_WRITE",
                     "NET_BIND_SERVICE", "FOWNER", "KILL"],
            security_opt=["no-new-privileges"],
        )
        # 진입 스크립트가 실패하면 컨테이너가 곧바로 죽는다. '실행 중'으로 잘못 보고하지 않도록 잠깐 지켜본다.
        deadline = time.time() + 2.0
        while True:
            c.reload()
            if c.status not in ("created", "running"):
                tail = c.logs(tail=5).decode(errors="replace").strip()
                raise RuntimeError(f"컨테이너가 시작 직후 종료되었습니다: {tail or '(로그 없음)'}")
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        return self._info(c)

    def stop_workspace(self, username: str) -> bool:
        return self._stop_all(ws_name(username))

    def _stop_all(self, name: str) -> bool:
        """논리 이름의 컨테이너를 모두 멈추고 지운다. 지운 것이 있으면 True."""
        found = self._find(name)
        for c in found:
            # 이미 꺼진 컨테이너(서버·Docker 재시작 뒤 남은 것)도 지울 수 있어야 한다
            try:
                if c.status == "running":
                    c.stop(timeout=5)
            except Exception:
                pass  # 정상 종료에 실패해도 아래 강제 삭제가 처리한다
            self._discard(c)
        return bool(found)

    def get_workspace(self, username: str) -> dict | None:
        c = self._find_one(ws_name(username))
        return self._info(c) if c else None

    def run_job(self, job: dict, user: dict) -> str:
        workdir = "/home/dev/" + job["workdir"].strip("/") if job["workdir"] else "/home/dev"
        for old in self._find(job_name(job["id"])):
            self._discard(old)
        c = self._run_named(
            job_name(job["id"]), self.s.workspace_image,
            entrypoint=["bash", "-lc"],
            command=[job["command"]],
            user=f"{user['uid']}:{user['uid']}",
            working_dir=workdir,
            labels={LABEL: "1", "ironlab.kind": "job", "ironlab.user": user["name"],
                    "ironlab.job_id": str(job["id"])},
            # 작업은 진입 스크립트를 거치지 않아 /etc/passwd 에 사용자가 없다. 이름을 묻는 도구
            # (getpass, git 등)가 실패하지 않게 환경변수로 알려 준다
            environment=self.env_for(user) | {"IRONLAB_JOB_ID": str(job["id"]),
                                              "IRONLAB_VRAM_GB": str(job["vram_gb"]),
                                              "USER": "dev", "LOGNAME": "dev"},
            mounts=self._mounts(user, include_keys=False),
            network=self.s.network_name,
            device_requests=self._gpu,
            nano_cpus=int(job["cpus"] * 1e9),
            mem_limit=mem_limit(job["mem_gb"]),
            memswap_limit=mem_limit(job["mem_gb"]),
            shm_size=f"{self.s.shm_gb}g",
            pids_limit=4096,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        return c.id

    def inspect(self, cid: str) -> dict | None:
        c = self._get(cid)
        return self._info(c) if c else None

    def logs(self, cid: str, tail: int | None = None) -> str:
        c = self._get(cid)
        if c is None:
            return ""
        return c.logs(tail=tail if tail else "all", timestamps=False).decode(errors="replace")

    def kill(self, cid: str) -> None:
        c = self._get(cid)
        if c is not None and c.status == "running":
            c.kill()

    def remove(self, cid: str) -> None:
        """ID 또는 논리 이름으로 지운다 (스케줄러는 작업 이름으로 부른다)."""
        for c in self._find(cid):
            self._discard(c)

    def list_managed(self) -> list[dict]:
        cs = self.client.containers.list(all=True, filters={"label": f"{LABEL}=1"})
        return [self._info(c) for c in cs]

    def stats(self, cid: str) -> dict:
        c = self._get(cid)
        if c is None or c.status != "running":
            return {"cpu_cores": 0.0, "mem_bytes": 0, "mem_limit": 0}
        st = c.stats(stream=False)
        cpu, pre = st.get("cpu_stats", {}), st.get("precpu_stats", {})
        cd = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
        sd = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or [1])
        cores = (cd / sd) * ncpu if sd > 0 and cd > 0 else 0.0
        mem = st.get("memory_stats", {})
        used = mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0)
        return {"cpu_cores": round(cores, 2), "mem_bytes": max(used, 0), "mem_limit": mem.get("limit", 0)}

    def fast_stats(self, cid: str, pid: int | None = None) -> dict | None:
        return self._cgroup.read(cid, pid)

    def ssh_sessions(self, cid: str) -> int:
        c = self._get(cid)
        if c is None or c.status != "running":
            return 0
        try:
            top = c.top(ps_args="-eo args")
        except Exception:
            return 0
        return sum(1 for p in top.get("Processes", []) if p and "sshd:" in p[-1] and "@" in p[-1])

    def container_for_pid(self, pid: int) -> str | None:
        try:
            text = Path(f"/proc/{pid}/cgroup").read_text()
        except OSError:
            return None
        m = CID_RE.search(text)
        return m.group(1) if m else None


    # ── 네트워크·LLM ──
    def ensure_network(self) -> str:
        """워크스페이스·작업용 네트워크. 게이트웨이(호스트) IP 에서 내부 LLM 프록시를 연다."""
        from docker.types import IPAMConfig, IPAMPool
        try:
            self.client.networks.get(self.s.network_name)
        except self._docker.errors.NotFound:
            ipam = IPAMConfig(pool_configs=[IPAMPool(subnet=self.s.network_subnet, gateway=self.s.network_gateway)])
            self.client.networks.create(self.s.network_name, driver="bridge", ipam=ipam,
                                        labels={LABEL: "1"})
        return self.s.network_gateway

    def ip_map(self) -> dict[str, dict]:
        """ironlab 네트워크 위 컨테이너 IP → {user, kind, id}."""
        out = {}
        for c in self.client.containers.list(filters={"label": f"{LABEL}=1", "network": self.s.network_name}):
            net = c.attrs.get("NetworkSettings", {}).get("Networks", {}).get(self.s.network_name) or {}
            ip = net.get("IPAddress")
            if ip:
                out[ip] = {"user": c.labels.get("ironlab.user"), "kind": c.labels.get("ironlab.kind"), "id": c.id}
        return out

    def start_llm(self, model: dict, loaded_by: str = "") -> dict:
        for old in self._find(LLM_NAME):
            self._discard(old)
        c = self._run_named(
            LLM_NAME, self.s.llm_image,
            command=llm_command(model),
            labels={LABEL: "1", "ironlab.kind": "service", "ironlab.model": model["name"],
                    "ironlab.loaded_by": loaded_by},
            mounts=[self._Mount(target="/models", source=str(self.s.llm_models_dir), type="bind", read_only=True)],
            device_requests=self._gpu,
            ports={"8080/tcp": ("127.0.0.1", self.s.llm_port)},
            shm_size="4g",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        c.reload()
        return self._info(c)

    def stop_llm(self) -> bool:
        return self._stop_all(LLM_NAME)

    def get_llm(self) -> dict | None:
        c = self._find_one(LLM_NAME)
        return self._info(c) if c else None


# ─────────────────────────────── Mock ───────────────────────────────

class MockRuntime(Runtime):
    """Docker·GPU 없이 동작을 흉내 낸다. 작업 명령에 'sleep N'이 있으면 N초 걸리고,
    'fail'이 들어 있으면 exit 1로 끝난다."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.containers: dict[str, dict] = {}
        self._pid = 40000

    def _new_id(self) -> str:
        return "%064x" % random.getrandbits(256)

    def _refresh(self, c: dict) -> None:
        if c["kind"] == "job" and c["state"] == "running" and time.time() >= c["_ends_at"]:
            c["state"] = "exited"
            c["exit_code"] = c["_exit"]
            c["_log"].append("done." if c["_exit"] == 0 else "Error: simulated failure")

    def _public(self, c: dict) -> dict:
        self._refresh(c)
        return {k: v for k, v in c.items() if not k.startswith("_")} | {"pid": c["_pid"]}

    def _by_name(self, name: str) -> dict | None:
        return next((c for c in self.containers.values() if c["name"] == name), None)

    def start_workspace(self, user: dict) -> dict:
        c = self._by_name(ws_name(user["name"]))
        if c and c["state"] == "running":
            return self._public(c)
        if c:
            self.containers.pop(c["id"])
        self._pid += 7
        cid = self._new_id()
        self.containers[cid] = {
            "id": cid, "name": ws_name(user["name"]), "kind": "workspace", "user": user["name"],
            "job_id": None, "state": "running", "exit_code": None, "started_at": time.time(),
            "_pid": self._pid, "_vram": random.choice([0, 0, 1.2, 2.5]),
            "_mem_limit": user["mem_gb"] * 2**30, "_cpus": user["cpus"], "_log": [],
            "_ip": f"172.30.0.{10 + self._pid % 200}",
        }
        return self._public(self.containers[cid])

    def stop_workspace(self, username: str) -> bool:
        c = self._by_name(ws_name(username))
        if not c:
            return False
        self.containers.pop(c["id"])
        return True

    def get_workspace(self, username: str) -> dict | None:
        c = self._by_name(ws_name(username))
        return self._public(c) if c else None

    def run_job(self, job: dict, user: dict) -> str:
        m = re.search(r"sleep\s+(\d+(?:\.\d+)?)", job["command"])
        dur = float(m.group(1)) if m else 6.0
        self._pid += 7
        cid = self._new_id()
        self.containers[cid] = {
            "id": cid, "name": job_name(job["id"]), "kind": "job", "user": user["name"],
            "job_id": str(job["id"]), "state": "running", "exit_code": None,
            "started_at": time.time(), "_pid": self._pid,
            "_vram": job["vram_gb"] * random.uniform(0.6, 0.95),
            "_mem_limit": job["mem_gb"] * 2**30, "_cpus": job["cpus"],
            "_ends_at": time.time() + dur, "_exit": 1 if "fail" in job["command"] else 0,
            "_log": [f"$ {job['command']}", "loading model…", f"using {job['vram_gb']} GB VRAM budget"],
            "_ip": f"172.30.0.{10 + self._pid % 200}",
        }
        return cid

    def inspect(self, cid: str) -> dict | None:
        c = self.containers.get(cid)
        return self._public(c) if c else None

    def logs(self, cid: str, tail: int | None = None) -> str:
        c = self.containers.get(cid)
        if not c:
            return ""
        self._refresh(c)
        lines = c["_log"][-tail:] if tail else c["_log"]
        return "\n".join(lines) + "\n"

    def kill(self, cid: str) -> None:
        c = self.containers.get(cid)
        if c and c["state"] == "running":
            c["state"], c["exit_code"] = "exited", 137
            c["_log"].append("killed")

    def remove(self, cid: str) -> None:
        # Docker 처럼 이름으로도 찾는다
        for key in [k for k, c in list(self.containers.items()) if k == cid or c["name"] == cid]:
            self.containers.pop(key, None)

    def list_managed(self) -> list[dict]:
        return [self._public(c) for c in list(self.containers.values())]

    def stats(self, cid: str) -> dict:
        c = self.containers.get(cid)
        if not c:
            return {"cpu_cores": 0.0, "mem_bytes": 0, "mem_limit": 0}
        self._refresh(c)
        if c["state"] != "running":
            return {"cpu_cores": 0.0, "mem_bytes": 0, "mem_limit": c["_mem_limit"]}
        busy = 0.9 if c["kind"] == "job" else random.choice([0.02, 0.05, 0.4])
        return {"cpu_cores": round(c["_cpus"] * busy * random.uniform(0.5, 1.0), 2),
                "mem_bytes": int(c["_mem_limit"] * random.uniform(0.1, 0.6)),
                "mem_limit": c["_mem_limit"]}

    def ssh_sessions(self, cid: str) -> int:
        c = self.containers.get(cid)
        return random.choice([0, 1, 1, 2]) if c and c["state"] == "running" and c["kind"] == "workspace" else 0

    def container_for_pid(self, pid: int) -> str | None:
        return next((cid for cid, c in self.containers.items() if c["_pid"] == pid), None)

    # MockGpu가 사용하는 내부 정보
    def mock_gpu_processes(self) -> list[dict]:
        procs = []
        for c in self.containers.values():
            self._refresh(c)
            if c["state"] == "running" and c["_vram"] > 0:
                procs.append({"pid": c["_pid"], "used_mb": int(c["_vram"] * 1024)})
        return procs


    # ── 네트워크·LLM (mock) ──
    def ensure_network(self) -> str:
        return self.s.network_gateway

    def ip_map(self) -> dict[str, dict]:
        out = {}
        for i, c in enumerate(sorted(self.containers.values(), key=lambda c: c["_pid"])):
            self._refresh(c)
            if c["state"] == "running" and c["kind"] in ("workspace", "job"):
                out[c["_ip"]] = {"user": c["user"], "kind": c["kind"], "id": c["id"]}
        return out

    def start_llm(self, model: dict, loaded_by: str = "") -> dict:
        self.stop_llm()
        self._pid += 7
        cid = self._new_id()
        self.containers[cid] = {
            "id": cid, "name": LLM_NAME, "kind": "service", "user": None, "job_id": None,
            "state": "running", "exit_code": None, "started_at": time.time(),
            "model": model["name"], "loaded_by": loaded_by,
            "_pid": self._pid, "_vram": model["vram_gb"] * 0.96, "_mem_limit": 8 * 2**30,
            "_cpus": 4, "_log": [f"llama-server {' '.join(llm_command(model))}", "model loaded"], "_ip": "",
        }
        return self._public(self.containers[cid])

    def stop_llm(self) -> bool:
        c = self._by_name(LLM_NAME)
        if not c:
            return False
        self.containers.pop(c["id"])
        return True

    def get_llm(self) -> dict | None:
        c = self._by_name(LLM_NAME)
        return self._public(c) if c else None


def make_runtime(settings: Settings) -> Runtime:
    return MockRuntime(settings) if settings.mock else DockerRuntime(settings)


def quote_cmd(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
