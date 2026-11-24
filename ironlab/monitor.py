"""주기적으로 호스트·GPU·컨테이너 상태를 모으고, 스케줄러와 유휴 정리를 돌린다."""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import psutil

from .config import Settings
from .db import DB
from .files import disk_usage_bytes
from .scheduler import Scheduler

log = logging.getLogger("ironlab.monitor")
GB = 2**30
WATCH_WINDOW_S = 15     # 마지막으로 누가 수치를 읽어 간 뒤 이 시간 동안만 촘촘히 갱신한다
RECENT_POINTS = 240


def _cpu_model() -> str:
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _cpu_list(path: str) -> list[int] | None:
    """커널의 CPU 목록 파일("0-7,10-31" 꼴)을 번호 목록으로 읽는다. 파일이 없거나 읽을 수 없으면 None."""
    try:
        text = open(path).read().strip()
    except OSError:
        return None
    cpus: set[int] = set()
    try:
        for part in text.split(","):
            if not part:
                continue
            lo, _, hi = part.partition("-")
            cpus.update(range(int(lo), int(hi or lo) + 1))
    except ValueError:
        return None
    return sorted(cpus)


def _p_threads(path: str = "/sys/devices/cpu_core/cpus", online: list[int] | None = None) -> int | None:
    """P/E 코어가 섞인 인텔 CPU 에서, 켜져 있는 논리 코어를 번호순으로 늘어놓았을 때 앞쪽에 오는 P코어 쪽 개수
    (13900K: "0-15" → 16). 화면은 이 수만큼을 P코어 줄에, 나머지를 E코어 줄에 그린다.

    논리 코어 일부를 꺼 두어도(예: 고장 난 8·9번을 끄면 켜진 것은 0-7,10-31) 켜진 것만 센다 → 14.
    섞이지 않은 CPU 이거나 P코어가 앞쪽에 모여 있지 않으면 None.
    """
    p = _cpu_list(path)
    if not p:
        return None
    if online is None:
        return len(p) if p == list(range(len(p))) else None     # 켜진 코어를 모르면 0번부터 이어질 때만
    order = sorted(online)
    lead = 0
    for cpu in order:
        if cpu not in p:
            break
        lead += 1
    rest_has_p = any(cpu in p for cpu in order[lead:])
    return lead if lead and not rest_has_p else None


def _cpu_layout(base: str = "/sys/devices/system/cpu") -> tuple[list[int] | None, list[int]]:
    """(켜져 있는 논리 코어 번호, 꺼 둔 논리 코어 번호). 리눅스가 아니면 (None, [])."""
    online = _cpu_list(f"{base}/online")
    present = _cpu_list(f"{base}/present")
    if online is None:
        return None, []
    return online, sorted(set(present or online) - set(online))


class Monitor:
    def __init__(self, db: DB, settings: Settings, runtime, gpu, scheduler: Scheduler):
        self.db, self.s, self.rt, self.gpu, self.sched = db, settings, runtime, gpu, scheduler
        self.llm = None  # api.Ctx 에서 연결 (LLMService)
        self.after_tick: list = []      # 작업 큐를 돌린 뒤에 부를 함수들 (api.Ctx 가 노드 편집기의 실행 엔진을 건다)
        self.latest: dict = {}
        self.history: deque = deque(maxlen=settings.history_points)     # 전체 수집 주기마다 한 점
        self.recent: deque = deque(maxlen=RECENT_POINTS)                # 가벼운 갱신까지 포함한 최근 기록
        self.last_watch = 0.0
        self._want_full = False
        self._containers: list[dict] = []
        self._sessions: dict[str, int] = {}
        self._last_stats: dict[str, dict] = {}
        self._base_alerts: list[str] = []
        self._complained: dict[str, float] = {}
        self.backend_error: str | None = None       # Docker 가 응답하지 않을 때의 오류 문구
        self.last_active: dict[str, float] = {}
        self.disk_used: dict[str, int] = {}
        self.storage: dict | None = None            # 저장공간 점유 (5분마다 _scan_disks 가 다시 잰다)
        self._disk_checked = 0.0
        self._disk_thread: threading.Thread | None = None
        self._maintained = time.time()      # 첫 정리는 띄운 지 한 시간 뒤
        self._last_alert = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=8)
        psutil.cpu_percent(percpu=True)  # 첫 호출은 0을 돌려주므로 미리 한 번
        self._cpu_model = _cpu_model()
        self._proc_prev: dict[int, tuple[float, float]] = {}  # pid → (생성 시각, 누적 CPU 초)
        self._proc_ts = 0.0

    # ── 수명주기 ──
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="ironlab-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._pool.shutdown(wait=False)

    def _loop(self) -> None:
        next_full = 0.0
        while not self._stop.is_set():
            started = time.time()
            try:
                if started >= next_full or self._want_full:
                    self._want_full = False
                    # 먼저 다음 시각을 잡는다: Docker 가 죽어 수집이 실패해도 1초마다 다시 두드리지 않는다
                    next_full = started + self.s.monitor_interval_s
                    self.collect()
                elif started - self.last_watch < WATCH_WINDOW_S:
                    self.sample()       # 누군가 보고 있을 때만 촘촘히 잰다
            except Exception:
                log.exception("monitor tick failed")
            spent = time.time() - started
            self._stop.wait(max(0.05, min(self.s.sample_interval_s - spent, next_full - time.time())))

    def touch(self) -> None:
        """대시보드가 수치를 읽어 갔음을 알린다. 이후 잠시 동안 sample_interval_s 주기로 갱신한다."""
        self.last_watch = time.time()

    def request_full(self) -> None:
        """컨테이너를 켜고 끈 직후처럼, 다음 주기를 기다리지 않고 전체 수집을 하게 한다."""
        self._want_full = True

    # ── 수집 ──
    def collect(self) -> dict:
        """전체 수집: 컨테이너 목록을 새로 읽고, 작업 큐·LLM·유휴 정리까지 돌린다 (monitor_interval_s 마다)."""
        try:
            containers = self.rt.list_managed()
        except Exception as e:
            # Docker 가 응답하지 않는다. 컨테이너 상태를 모르는 채로 작업 큐를 돌리면 멀쩡한 작업을 실패로
            # 처리하게 되므로 큐는 멈추고, 호스트·GPU 수치만 계속 갱신하면서 화면에 알린다.
            if self.backend_error is None:
                log.error("컨테이너 목록을 읽지 못했습니다 (Docker 응답 없음?): %s", e)
                self.db.log_event("backend.down", None, str(e)[:300])
            self.backend_error = str(e)[:300] or type(e).__name__
            return self._measure(time.time(), full=False)
        if self.backend_error is not None:
            log.warning("Docker 가 다시 응답합니다")
            self.db.log_event("backend.up", None, "")
            self.backend_error = None
        workspaces = [c for c in containers if c["state"] == "running" and c["kind"] == "workspace"]
        self._containers = containers
        self._sessions = dict(zip([c["id"] for c in workspaces], self._pool.map(self._safe_sessions, workspaces)))
        try:
            self.sched.reconcile(containers)
        except Exception:
            log.exception("orphan cleanup failed")
        return self._measure(time.time(), full=True)

    def _scan_disks(self) -> None:
        try:
            users = self.db.list_users()
            self.disk_used = {u["name"]: disk_usage_bytes(self.s.users_root / u["name"]) for u in users}
            self.storage = self._storage(users, self.disk_used)
        except Exception:
            self._complain("disk", "disk usage scan failed")

    def _storage(self, users: list[dict], homes: dict[str, int]) -> dict:
        """디스크마다 누가·무엇이 얼마나 차지하는지. 대시보드의 '저장공간 점유'가 그린다.

        사용자 홈, 사용자 스크래치, 공용(모델·데이터)을 직접 잰다. 디스크에서 쓰인 양과 이 합의 차이는
        '그 밖'(운영체제, Docker 이미지와 컨테이너, 작업 로그 등)이다. 영역들이 서로 다른 디스크에 있으면 디스크마다 따로 센다.
        공용 모델은 Hugging Face 캐시(models/hub)의 저장소마다, 로컬 LLM 폴더(models/llm)의 모델마다 크기를 잰다.
        """
        def device(path) -> int | None:
            try:
                return os.stat(path).st_dev
            except OSError:
                return None

        areas = []                                          # (디스크 번호, 이름표, 종류, 사용자, 바이트)
        for u in users:
            areas.append((device(self.s.users_root), u["name"], "home", u["name"], homes.get(u["name"], 0)))
            scratch = self.s.scratch_root / u["name"]
            if scratch.exists():
                areas.append((device(self.s.scratch_root), u["name"], "scratch", u["name"], disk_usage_bytes(scratch)))
        models: list[dict] = []
        hub = self.s.shared_root / "models" / "hub"
        llm = self.s.shared_root / "models" / "llm"
        for folder, kind in ((hub, "hub"), (llm, "llm")):
            try:
                entries = [e for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for e in entries:
                name = e.name
                if kind == "hub":
                    if not name.startswith(("models--", "datasets--")):
                        continue
                    name = name.split("--", 1)[1].replace("--", "/")
                models.append({"name": name, "kind": kind, "bytes": disk_usage_bytes(e.path)})
        models.sort(key=lambda m: -m["bytes"])
        shared_total = disk_usage_bytes(self.s.shared_root) if self.s.shared_root.exists() else 0
        areas.append((device(self.s.shared_root), "공용 (모델·데이터)", "shared", None, shared_total))

        disks = {}
        for path, label in ((self.s.users_root, "사용자 홈"), (self.s.shared_root, "공용"), (self.s.scratch_root, "스크래치")):
            dev = device(path)
            if dev is None or dev in disks:
                continue
            du = psutil.disk_usage(str(path))
            disks[dev] = {"path": str(path), "total": du.total, "used": du.used, "free": du.free, "holds": [label], "parts": []}
        for dev, label, kind, user, size in areas:
            if dev in disks and size > 0:
                disks[dev]["parts"].append({"label": label, "kind": kind, "user": user, "bytes": size})
        for path, label in ((self.s.shared_root, "공용"), (self.s.scratch_root, "스크래치")):
            dev = device(path)
            if dev in disks and label not in disks[dev]["holds"]:
                disks[dev]["holds"].append(label)
        for d in disks.values():
            known = sum(p["bytes"] for p in d["parts"])
            d["other"] = max(0, d["used"] - known)          # 운영체제, Docker, 로그 등 (직접 재지 않은 것)
        quota = {u["name"]: u["disk_gb"] * GB for u in users}
        return {"scanned_at": time.time(), "disks": list(disks.values()), "models": models[:12],
                "models_total": sum(m["bytes"] for m in models), "models_count": len(models),
                "users": [{"name": u["name"], "home": homes.get(u["name"], 0), "quota": quota[u["name"]],
                           "scratch": next((a[4] for a in areas if a[2] == "scratch" and a[3] == u["name"]), 0)}
                          for u in users]}

    def maintain(self, now: float) -> dict:
        """오래된 기록을 지운다 (1시간마다). 작업 로그는 시스템 파티션에 쌓이므로 그냥 두면 디스크가 찬다."""
        cutoff = now - self.s.log_retention_days * 86400
        removed = 0
        if self.s.log_retention_days > 0:
            for f in self.s.logs_dir.glob("job-*.log"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        events = usage = 0
        if self.s.event_retention_days > 0:
            old = now - self.s.event_retention_days * 86400
            events = self.db.execute("DELETE FROM events WHERE ts < ?", (old,)).rowcount
            usage = self.db.execute("DELETE FROM llm_usage WHERE ts < ?", (old,)).rowcount
        return {"job_logs": removed, "events": events, "llm_usage": usage}

    def _llm_status(self, full: bool) -> dict | None:
        """LLM 상태 확인은 Docker 에 묻는 일이라 전체 수집 때만 하고, 실패하면 마지막으로 알던 상태를 둔다."""
        if self.llm is None:
            return None
        if not full and "llm" in self.latest:
            return self.latest["llm"]
        try:
            return self.llm.status()
        except Exception:
            self._complain("llm-status", "llm status failed")
            return self.latest.get("llm")

    def _complain(self, key: str, message: str) -> None:
        """같은 실패가 주기마다 되풀이될 때 로그를 역추적으로 뒤덮지 않도록 1분에 한 번만 자세히 남긴다."""
        now = time.time()
        if now - self._complained.get(key, 0) >= 60:
            self._complained[key] = now
            log.exception(message)

    def _safe_sessions(self, c: dict) -> int:
        try:
            return self.rt.ssh_sessions(c["id"])
        except Exception:
            return self._sessions.get(c["id"], 0)       # 한 컨테이너를 못 읽었다고 수집 전체를 버리지 않는다

    def _safe_owner(self, pid: int) -> str | None:
        try:
            return self.rt.container_for_pid(pid)
        except Exception:
            return None

    def _safe_gpu(self) -> dict:
        try:
            return self.gpu.snapshot()
        except Exception as e:
            return {"available": False, "error": f"GPU 상태를 읽지 못했습니다: {e}", "processes": [],
                    "total_mb": 0, "used_mb": 0}

    def sample(self) -> dict:
        """가벼운 갱신: 마지막으로 읽은 컨테이너 목록을 그대로 두고 CPU·메모리·GPU 수치만 다시 잰다.
        Docker API 를 부르지 않으므로 1초마다 돌려도 부담이 없다."""
        if not self.latest:
            return self.collect()
        return self._measure(time.time(), full=False)

    def _measure(self, now: float, full: bool) -> dict:
        containers = self._containers
        by_id = {c["id"]: c for c in containers}
        running = [c for c in containers if c["state"] == "running"]
        gpu = self._safe_gpu()
        stats = dict(zip([c["id"] for c in running],
                         self._pool.map(lambda c: self._safe_stats(c, slow_ok=full), running)))
        self._last_stats = stats

        # GPU 프로세스 → 컨테이너 → 사용자
        vram_by_cid: dict[str, int] = {}
        for p in gpu.get("processes", []):
            cid = self._safe_owner(p["pid"])
            c = by_id.get(cid) if cid else None
            p["user"] = c["user"] if c else None
            p["kind"] = c["kind"] if c else "host"
            p["job_id"] = c["job_id"] if c else None
            p["model"] = c.get("model") if c else None
            if cid:
                vram_by_cid[cid] = vram_by_cid.get(cid, 0) + p["used_mb"]

        if full:
            actual_job_gb = {int(c["job_id"]): vram_by_cid.get(c["id"], 0) / 1024
                             for c in running if c["kind"] == "job" and c["job_id"]}
            service_gb = sum(vram_by_cid.get(c["id"], 0) for c in running if c["kind"] == "service") / 1024
            llm_reserve = 0.0
            if self.llm is not None:
                try:
                    self.llm.tick(now)
                    self.llm.observe_vram(service_gb)
                    llm_reserve = self.llm.reserve_gb(service_gb)
                except Exception:
                    self._complain("llm", "llm tick failed")
            hold = None if gpu.get("available") else "GPU 상태를 읽을 수 없어 새 작업을 시작하지 않습니다"
            try:
                self.sched.tick(gpu.get("used_mb", 0) / 1024, actual_job_gb, llm_reserve, hold=hold)
            except Exception:
                # 큐 처리가 실패해도 화면에 보일 수치는 계속 갱신한다. 다음 주기에 다시 시도한다
                self._complain("sched", "scheduler tick failed")
            for fn in self.after_tick:
                try:
                    fn()
                except Exception:
                    self._complain("after_tick", "after-tick hook failed")

            if now - self._disk_checked > 300 and not (self._disk_thread and self._disk_thread.is_alive()):
                # 홈 전체를 훑는 일은 파일이 많으면(conda 환경 등) 수십 초가 걸린다. 수집 스레드에서 하면
                # 그동안 화면 갱신과 작업 큐가 멈추므로 따로 돌린다.
                self._disk_checked = now
                self._disk_thread = threading.Thread(target=self._scan_disks, name="ironlab-disk", daemon=True)
                self._disk_thread.start()
            if now - self._maintained > 3600:
                self._maintained = now
                try:
                    self.maintain(now)
                except Exception:
                    self._complain("maintain", "maintenance failed")

        users = self._per_user(containers, stats, self._sessions, vram_by_cid, now)
        if full:
            self._idle_stop(users, now)
        # 공유 서비스(LLM) 컨테이너의 CPU·메모리: 어느 사용자 몫도 아니므로 따로 센다
        svc = [c for c in running if c["kind"] == "service"]
        services = {
            "cpu_cores": round(sum(stats.get(c["id"], {}).get("cpu_cores", 0.0) for c in svc), 2),
            "mem_bytes": sum(stats.get(c["id"], {}).get("mem_bytes", 0) for c in svc),
            "model": next((c.get("model") for c in svc if c.get("model")), None),
        }
        if self.s.mock:
            host = self._mock_host(users, services)
            procs = self._mock_procs(running, stats)
        else:
            host = self._host()
            procs = self._procs(by_id, now)
        # 알림은 감사 로그에도 남으므로 전체 수집 때만 다시 판단한다
        if full:
            self._base_alerts = self._alerts(gpu, host, now)
        alerts = list(self._base_alerts)
        if self.backend_error:
            alerts.insert(0, "Docker 가 응답하지 않아 작업 큐와 컨테이너 상태 갱신이 멈춰 있습니다. "
                             f"서버에서 'systemctl status docker' 를 확인하세요 ({self.backend_error[:120]})")

        jobs = self.db.one("""SELECT SUM(status='running') AS r, SUM(status='queued') AS q FROM jobs""")
        snap = {
            "ts": now,
            "mock": self.s.mock,
            "host": host,
            "gpu": gpu,
            "users": users,
            "services": services,
            "procs": procs,
            "queue": {"running": jobs["r"] or 0, "queued": jobs["q"] or 0,
                      "budget_gb": self.sched.budget_gb, "total_gb": round(self.sched.total_gb, 1),
                      "reserve_gb": self.s.vram_reserve_gb},
            "alerts": alerts,
            "backend_error": self.backend_error,
            "llm": self._llm_status(full),
            "storage": self.storage,
        }
        self.latest = snap
        point = {
            "ts": round(now, 1),
            "gpu_util": gpu.get("util") or 0,
            "vram_gb": round(gpu.get("used_mb", 0) / 1024, 2),
            "cpu": host["cpu_percent"],
            "mem": host["mem_percent"],
            "users": {u["name"]: round(u["vram_mb"] / 1024, 2) for u in users if u["vram_mb"]},
            # CPU(코어)·메모리(GB): 전체와 사용자별, 공유 LLM 몫
            "cpu_cores": round(host["cpu_percent"] * host["cores"] / 100, 2),
            "mem_gb": round(host["mem_used"] / GB, 2),
            "cpu_users": {u["name"]: u["cpu_cores"] for u in users if u["cpu_cores"] >= 0.05},
            "mem_users": {u["name"]: round(u["mem_bytes"] / GB, 2) for u in users if u["mem_bytes"] >= 0.05 * GB},
            "cpu_llm": services["cpu_cores"],
            "mem_llm": round(services["mem_bytes"] / GB, 2),
        }
        self.recent.append(point)           # 촘촘한 최근 기록 (1분 차트)
        if full:
            self.history.append(point)      # 긴 기록 (15분·1시간 차트)
        return snap

    def _safe_stats(self, c: dict, slow_ok: bool = True) -> dict:
        """컨테이너 CPU·메모리. cgroup 을 직접 읽는 빠른 경로를 먼저 쓰고, 안 되면 docker stats(1~2초 걸림)로
        대신한다. 가벼운 갱신 중에는 느린 경로를 쓰지 않고 마지막 값을 그대로 둔다."""
        cid = c["id"]
        try:
            st = self.rt.fast_stats(cid, c.get("pid"))
            if st is not None:
                return st
            if slow_ok:
                return self.rt.stats(cid)
        except Exception:
            pass
        return self._last_stats.get(cid, {"cpu_cores": 0.0, "mem_bytes": 0, "mem_limit": 0})

    def _host(self) -> dict:
        per_cpu = psutil.cpu_percent(percpu=True) or [0.0]
        # per_cpu 는 켜져 있는 코어만 번호순으로 담는다. 일부를 꺼 두면 자리(0,1,2…)와 실제 번호가 어긋나므로
        # 실제 번호를 함께 보낸다 (개수가 안 맞으면 방금 켜고 끈 것이니 이번에는 보내지 않는다)
        online, offline = _cpu_layout()
        if online is not None and len(online) != len(per_cpu):
            online = None
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        root = self.s.users_root if self.s.users_root.exists() else "/"
        du = psutil.disk_usage(str(root))
        temp = None
        try:
            temps = psutil.sensors_temperatures()
            for key in ("coretemp", "k10temp", "zenpower"):
                if temps.get(key):
                    temp = max(t.current for t in temps[key])
                    break
        except (AttributeError, OSError):
            pass
        return {
            "cpu_percent": round(sum(per_cpu) / len(per_cpu), 1),
            "per_cpu": [round(x) for x in per_cpu],          # 논리 코어별 사용률(%)
            "cores": len(per_cpu),
            "cpu_ids": online,                                # per_cpu 각 칸의 실제 논리 코어 번호 (모르면 None)
            "cpu_offline": offline,                           # 꺼 둔 논리 코어 번호
            "cpu_model": self._cpu_model,
            "p_threads": _p_threads(online=online),
            "load": [round(x, 2) for x in psutil.getloadavg()],
            "mem_used": vm.total - vm.available,
            "mem_total": vm.total,
            "mem_percent": vm.percent,
            # 파일 캐시·버퍼: 필요하면 커널이 바로 비우므로 '사용 중'에는 넣지 않는다
            "mem_cached": min(getattr(vm, "cached", 0) + getattr(vm, "buffers", 0), vm.available),
            "swap_used": sw.used, "swap_total": sw.total,
            "disk_used": du.used, "disk_total": du.total, "disk_path": str(root),
            "cpu_temp": temp,
            "uptime_s": int(time.time() - psutil.boot_time()),
        }

    def _procs(self, by_id: dict, now: float, limit: int = 8) -> list[dict]:
        """CPU·메모리를 많이 쓰는 프로세스. CPU 는 지난 수집 이후 쓴 코어 수(1.0 = 코어 하나를 꽉 채움).
        명령줄 인자에는 토큰 같은 것이 섞일 수 있어 프로세스 이름만 내보낸다."""
        dt = now - self._proc_ts if self._proc_ts else 0.0
        cur: dict[int, tuple[float, float]] = {}
        rows = []
        for p in psutil.process_iter(["pid", "name", "cpu_times", "memory_info", "create_time"]):
            i = p.info
            if not i["cpu_times"] or not i["memory_info"]:
                continue
            spent = i["cpu_times"].user + i["cpu_times"].system
            cur[i["pid"]] = (i["create_time"], spent)
            prev = self._proc_prev.get(i["pid"])
            same = prev is not None and prev[0] == i["create_time"]       # PID 재사용 구분
            cores = max(0.0, (spent - prev[1]) / dt) if same and dt > 0 else 0.0
            rows.append((i["pid"], i["name"] or "?", cores, i["memory_info"].rss))
        self._proc_prev, self._proc_ts = cur, now

        picked = {r[0]: r for r in sorted(rows, key=lambda r: r[2], reverse=True)[:limit] if r[2] >= 0.05}
        for r in sorted(rows, key=lambda r: r[3], reverse=True)[:limit]:
            picked.setdefault(r[0], r)
        out = []
        for pid, name, cores, rss in picked.values():
            cid = self._safe_owner(pid)
            c = by_id.get(cid) if cid else None
            out.append({"pid": pid, "name": name, "cpu_cores": round(cores, 2), "mem_bytes": rss,
                        "user": c["user"] if c else None, "kind": c["kind"] if c else "host",
                        "job_id": c["job_id"] if c else None, "model": c.get("model") if c else None})
        return sorted(out, key=lambda p: (p["cpu_cores"], p["mem_bytes"]), reverse=True)

    # ── mock 모드: 가짜 사용자 사용량과 앞뒤가 맞는 호스트 수치를 만든다 ──
    def _mock_host(self, users: list[dict], services: dict) -> dict:
        h = self._host()
        cores, total = 32, 64 * GB
        busy = sum(u["cpu_cores"] for u in users) + services["cpu_cores"] + 0.7
        weights = [random.random() ** 3 for _ in range(cores)]
        scale = busy / (sum(weights) or 1)
        per_cpu = [min(100, round(w * scale * 100)) for w in weights]
        used = min(int(total * 0.97), sum(u["mem_bytes"] for u in users) + services["mem_bytes"] + int(5.5 * GB))
        h.update({
            "cpu_percent": round(sum(per_cpu) / cores, 1), "per_cpu": per_cpu, "cores": cores,
            "cpu_model": "13th Gen Intel(R) Core(TM) i9-13900K (mock)", "p_threads": 16,
            "cpu_ids": list(range(cores)), "cpu_offline": [],
            "mem_used": used, "mem_total": total, "mem_percent": round(used / total * 100, 1),
            "mem_cached": min(18 * GB, total - used), "swap_used": 0, "swap_total": 0,
        })
        return h

    def _mock_procs(self, running: list[dict], stats: dict) -> list[dict]:
        names = {"job": "python", "workspace": "python", "service": "llama-server"}
        out = [{"pid": c.get("pid") or 40000 + i, "name": names.get(c["kind"], "python"),
                "cpu_cores": stats.get(c["id"], {}).get("cpu_cores", 0.0),
                "mem_bytes": stats.get(c["id"], {}).get("mem_bytes", 0),
                "user": c["user"], "kind": c["kind"], "job_id": c["job_id"], "model": c.get("model")}
               for i, c in enumerate(running)]
        out += [{"pid": 812, "name": "Xorg", "cpu_cores": 0.12, "mem_bytes": int(0.4 * GB),
                 "user": None, "kind": "host", "job_id": None, "model": None},
                {"pid": 1403, "name": "ironlab-server", "cpu_cores": 0.06, "mem_bytes": int(0.09 * GB),
                 "user": None, "kind": "host", "job_id": None, "model": None}]
        return sorted(out, key=lambda p: (p["cpu_cores"], p["mem_bytes"]), reverse=True)

    def _per_user(self, containers, stats, sessions, vram_by_cid, now) -> list[dict]:
        out = {}
        for u in self.db.list_users():
            out[u["name"]] = {
                "name": u["name"], "role": u["role"], "workspace": None,
                "cpu_cores": 0.0, "cpu_limit": u["cpus"], "mem_bytes": 0,
                "mem_limit": u["mem_gb"] * GB, "vram_mb": 0, "jobs_running": 0,
                "ssh_sessions": 0, "disk_used": self.disk_used.get(u["name"], 0),
                "disk_quota": u["disk_gb"] * GB, "last_seen": u["last_seen"],
                "active": False,
            }
        for c in containers:
            u = out.get(c["user"])
            if u is None:
                continue
            st = stats.get(c["id"], {})
            u["cpu_cores"] = round(u["cpu_cores"] + st.get("cpu_cores", 0.0), 2)
            u["mem_bytes"] += st.get("mem_bytes", 0)
            u["vram_mb"] += vram_by_cid.get(c["id"], 0)
            if c["kind"] == "workspace":
                u["workspace"] = {"state": c["state"], "started_at": c["started_at"]}
                u["ssh_sessions"] = sessions.get(c["id"], 0)
            elif c["kind"] == "job" and c["state"] == "running":
                u["jobs_running"] += 1
        for u in out.values():
            ws_running = u["workspace"] and u["workspace"]["state"] == "running"
            busy = u["cpu_cores"] > 0.1 or u["vram_mb"] > 0 or u["ssh_sessions"] > 0 or u["jobs_running"]
            recent_api = u["last_seen"] and now - u["last_seen"] < 300
            u["active"] = bool(busy or recent_api)
            if ws_running and busy:
                self.last_active[u["name"]] = now
            elif ws_running:
                self.last_active.setdefault(u["name"], now)
            else:
                self.last_active.pop(u["name"], None)  # 다시 켜면 유휴 시간을 새로 센다
            u["idle_since"] = None if busy or not ws_running else self.last_active.get(u["name"])
        return sorted(out.values(), key=lambda u: (not u["active"], u["name"]))

    def _idle_stop(self, users: list[dict], now: float) -> None:
        limit = self.s.idle_stop_hours * 3600
        if limit <= 0:
            return
        for u in users:
            since = u.get("idle_since")
            if since and now - since > limit:
                try:
                    self.rt.stop_workspace(u["name"])
                    self.db.log_event("workspace.idle_stop", u["name"],
                                      f"{self.s.idle_stop_hours:g}시간 유휴")
                    self.last_active.pop(u["name"], None)
                    u["workspace"] = None
                except Exception:
                    self._complain(f"idle-{u['name']}", f"idle stop failed for {u['name']}")

    def _alerts(self, gpu: dict, host: dict, now: float) -> list[str]:
        alerts = []
        if not gpu.get("available"):
            alerts.append(gpu.get("error", "GPU를 찾을 수 없음"))
        else:
            used_gb = gpu["used_mb"] / 1024
            if used_gb >= self.s.vram_alert_gb:
                alerts.append(f"VRAM {used_gb:.1f}GB 사용 중 — OOM 위험")
            if (gpu.get("temp") or 0) >= 85:
                alerts.append(f"GPU 온도 {gpu['temp']:.0f}°C")
            unknown = [p for p in gpu["processes"] if p["kind"] == "host" and p["used_mb"] > 512]
            if unknown:
                alerts.append(f"ironlab 밖에서 GPU를 쓰는 프로세스 {len(unknown)}개 "
                              f"({sum(p['used_mb'] for p in unknown) / 1024:.1f}GB)")
        renamed = sorted({c["renamed_from"] for c in self._containers if c.get("renamed_from")})
        if renamed:
            alerts.append(f"Docker 가 컨테이너 이름을 놓지 않아 예비 이름으로 띄웠습니다 ({', '.join(renamed)}). "
                          "동작은 그대로이고, 서버를 재부팅하면 풀립니다")
        if host.get("mem_total") and host["mem_used"] / host["mem_total"] >= 0.92:
            alerts.append(f"메모리 {host['mem_used'] / GB:.1f}/{host['mem_total'] / GB:.0f}GB 사용 중 — "
                          "부족하면 프로세스가 강제 종료됩니다")
        if alerts and now - self._last_alert > 600:
            self._last_alert = now
            self.db.log_event("alert", None, " / ".join(alerts))
        return alerts
