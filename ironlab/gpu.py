"""GPU 상태 수집. 프로세스별 VRAM을 컨테이너·사용자에 매핑하는 건 monitor가 한다.

- NVIDIA: NVML (nvidia-ml-py)
- AMD: 커널의 amdgpu 드라이버가 내놓는 sysfs 와 /proc/<pid>/fdinfo. 호스트에 ROCm 이 없어도 된다
- Windows(WSL2) 의 NVIDIA: NVML 은 되지만 프로세스별 사용량을 알려 주지 않는다 (per_process=False)
"""

from __future__ import annotations

import math
import os
import random
import re
import shutil
import time
from pathlib import Path

from .config import Settings

AMD_VENDOR = "0x1002"


class GpuMonitor:
    def snapshot(self) -> dict:
        raise NotImplementedError

    @property
    def total_gb(self) -> float:
        raise NotImplementedError


def is_wsl(proc_root: Path = Path("/proc")) -> bool:
    """Windows 의 WSL2 안에서 도는지. 커널 이름에 microsoft 가 들어 있다."""
    try:
        return "microsoft" in (proc_root / "sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return default


def _int(path: Path) -> int | None:
    v = _read(path)
    try:
        return int(v)
    except ValueError:
        return None


def amd_cards(sys_root: Path = Path("/sys")) -> list[dict]:
    """amdgpu 드라이버가 잡은 GPU 목록 (card 번호 순). gpu_index 는 이 목록의 순서다.

    card 마다 render 노드(/dev/dri/renderD*)와 PCI 주소를 함께 돌려준다. 컨테이너에는 render 노드 하나와
    /dev/kfd 만 넘기면 그 GPU 한 장만 보인다 (ROCm 의 GPU 나누기 방식).
    """
    drm = sys_root / "class" / "drm"
    if not drm.is_dir():
        return []
    cards = []
    for d in drm.iterdir():
        m = re.fullmatch(r"card(\d+)", d.name)
        dev = d / "device"
        if not m or _read(dev / "vendor") != AMD_VENDOR or not (dev / "mem_info_vram_total").exists():
            continue
        render = sorted(p.name for p in (dev / "drm").glob("renderD*")) if (dev / "drm").is_dir() else []
        cards.append({"card": d.name, "n": int(m.group(1)), "device": dev,
                      "render": render[0] if render else None,
                      "pci": dev.resolve().name, "pci_id": _read(dev / "device")})
    cards.sort(key=lambda c: c["n"])
    return cards


def resolve_vendor(settings: Settings, sys_root: Path = Path("/sys")) -> str:
    """설정의 gpu_vendor 가 auto 면 이 기계의 GPU 로 정한다. 모르면 nvidia (NVML 이 오류를 알려 준다)."""
    v = (settings.gpu_vendor or "auto").lower()
    if v in ("nvidia", "amd"):
        return v
    if (Path("/dev/nvidiactl").exists() or Path("/usr/lib/wsl/lib/libnvidia-ml.so.1").exists()
            or shutil.which("nvidia-smi")):
        return "nvidia"
    if amd_cards(sys_root):
        return "amd"
    return "nvidia"


_PCI_IDS = (Path("/usr/share/hwdata/pci.ids"), Path("/usr/share/misc/pci.ids"), Path("/usr/share/pci.ids"))


def amd_name(dev: Path, pci_ids=_PCI_IDS) -> str:
    """카드 이름. product_name 이 있으면 그것, 없으면 pci.ids 에서 찾는다 ('Navi 31 [Radeon RX 7900 XTX]' → 'AMD Radeon RX 7900 XTX')."""
    name = _read(dev / "product_name")
    if name:
        return name
    did = _read(dev / "device").lower().removeprefix("0x")
    for f in pci_ids:
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        in_amd = False
        for line in lines:
            if line.startswith("1002 "):
                in_amd = True
                continue
            if in_amd:
                if line and not line.startswith("\t"):
                    break                                   # 다음 제조사
                if line.startswith("\t") and not line.startswith("\t\t") and line[1:5].lower() == did:
                    label = line[5:].strip()
                    m = re.search(r"\[(.+)\]", label)
                    return "AMD " + (m.group(1) if m else label)
        break
    return f"AMD GPU (1002:{did or '?'})"


_UNITS = {"": 1, "B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30}


def _fdinfo_bytes(value: str) -> int:
    num, _, unit = value.strip().partition(" ")
    try:
        return int(num) * _UNITS.get(unit.strip(), 1)
    except ValueError:
        return 0


def parse_drm_fdinfo(text: str) -> dict | None:
    """/proc/<pid>/fdinfo/<fd> 에서 amdgpu 클라이언트 정보를 읽는다. amdgpu 가 아니면 None.

    VRAM 키는 커널마다 다르다: drm-resident-vram (6.9~), drm-memory-vram (5.17~), drm-total-vram.
    """
    kv = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if sep:
            kv[k.strip()] = v.strip()
    if kv.get("drm-driver") != "amdgpu":
        return None
    vram = next((kv[k] for k in ("drm-resident-vram", "drm-memory-vram", "drm-total-vram") if k in kv), "0")
    return {"pdev": kv.get("drm-pdev", ""), "client": kv.get("drm-client-id", ""), "vram": _fdinfo_bytes(vram)}


class AmdGpu(GpuMonitor):
    """amdgpu sysfs 로 읽는 AMD GPU 한 장.

    프로세스별 VRAM 은 /proc/<pid>/fdinfo 의 DRM 클라이언트 정보로 잰다 (nvtop·amdgpu_top 과 같은 방법).
    모든 프로세스의 fd 를 훑는 일은 무거우므로 FULL_SCAN_S 마다 한 번만 하고, 그 사이에는 찾아 둔 fd 만 다시 읽는다.
    """

    FULL_SCAN_S = 5.0

    def __init__(self, index: int = 0, sys_root: Path = Path("/sys"), proc_root: Path = Path("/proc"),
                 clock=time.monotonic):
        cards = amd_cards(sys_root)
        if not cards:
            raise RuntimeError("amdgpu 드라이버가 잡은 GPU 가 없습니다 (/sys/class/drm/card*/device)")
        if not 0 <= index < len(cards):
            raise RuntimeError(f"gpu_index={index} 인데 AMD GPU 는 {len(cards)}장입니다")
        self.card = cards[index]
        self.dev: Path = self.card["device"]
        self.proc_root, self._clock = proc_root, clock
        self.name = amd_name(self.dev)
        total = _int(self.dev / "mem_info_vram_total")
        if not total:
            raise RuntimeError(f"{self.card['card']} 의 VRAM 크기를 읽지 못했습니다")
        self._total = total
        hw = sorted((self.dev / "hwmon").glob("hwmon*")) if (self.dev / "hwmon").is_dir() else []
        self.hwmon = hw[0] if hw else None
        self._fds: list[Path] = []          # 이 GPU 의 DRM 클라이언트를 가리키는 fdinfo 파일들
        self._next_scan = 0.0

    @property
    def total_gb(self) -> float:
        return self._total / 2**30

    def _hw(self, *names: str) -> int | None:
        if self.hwmon is None:
            return None
        for n in names:
            v = _int(self.hwmon / n)
            if v is not None:
                return v
        return None

    def _scan_fds(self) -> None:
        found = []
        for pid_dir in self.proc_root.iterdir():
            if not pid_dir.name.isdigit():
                continue
            fd_dir = pid_dir / "fd"
            try:
                fds = list(fd_dir.iterdir())
            except OSError:
                continue
            for fd in fds:
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("/dev/dri/render") or target.startswith("/dev/dri/card"):
                    found.append(pid_dir / "fdinfo" / fd.name)
        self._fds = found

    def processes(self) -> list[dict]:
        now = self._clock()
        if now >= self._next_scan:
            self._scan_fds()
            self._next_scan = now + self.FULL_SCAN_S
        by_pid: dict[int, int] = {}
        seen: set[str] = set()
        alive = []
        # 같은 클라이언트를 여러 fd·프로세스가 나눠 쥘 수 있어 클라이언트 번호로 한 번만 센다 (먼저 본 pid 몫)
        for f in sorted(self._fds, key=lambda p: (int(p.parent.parent.name), p.name)):
            try:
                info = parse_drm_fdinfo(f.read_text())
            except OSError:
                continue                     # 프로세스가 끝났다
            alive.append(f)
            if not info or (info["pdev"] and info["pdev"] != self.card["pci"]):
                continue
            key = info["client"] or str(f)
            if key in seen:
                continue
            seen.add(key)
            pid = int(f.parent.parent.name)
            by_pid[pid] = by_pid.get(pid, 0) + info["vram"]
        self._fds = alive
        return [{"pid": pid, "used_mb": int(b / 2**20)} for pid, b in sorted(by_pid.items()) if b > 0]

    def snapshot(self) -> dict:
        d = self.dev
        used = _int(d / "mem_info_vram_used")
        if used is None:
            raise RuntimeError(f"{self.card['card']} 의 VRAM 사용량을 읽지 못했습니다")
        temp = self._hw("temp1_input")
        power = self._hw("power1_average", "power1_input")
        cap = self._hw("power1_cap")
        pwm = self._hw("pwm1")
        return {
            "available": True,
            "name": self.name,
            "total_mb": int(self._total / 2**20),
            "used_mb": int(used / 2**20),
            "util": _int(d / "gpu_busy_percent"),
            "mem_util": _int(d / "mem_busy_percent"),
            "temp": round(temp / 1000) if temp is not None else None,
            "fan": round(pwm / 255 * 100) if pwm is not None else None,
            "power_w": round(power / 1e6, 1) if power else None,
            "power_limit_w": round(cap / 1e6) if cap else None,
            "processes": self.processes(),
            "per_process": True,
        }


class NvmlGpu(GpuMonitor):
    def __init__(self, index: int = 0):
        import pynvml
        self.nv = pynvml
        pynvml.nvmlInit()
        self.h = pynvml.nvmlDeviceGetHandleByIndex(index)
        name = pynvml.nvmlDeviceGetName(self.h)
        self.name = name.decode() if isinstance(name, bytes) else name
        self._total = pynvml.nvmlDeviceGetMemoryInfo(self.h).total
        # WSL2 의 NVML 은 어떤 프로세스가 GPU 를 얼마나 쓰는지 알려 주지 않는다 (빈 목록이거나 '지원 안 함')
        self.per_process = not is_wsl()

    @property
    def total_gb(self) -> float:
        return self._total / 2**30

    def _safe(self, fn, default=None):
        try:
            return fn()
        except self.nv.NVMLError:
            return default

    def snapshot(self) -> dict:
        nv, h = self.nv, self.h
        mem = nv.nvmlDeviceGetMemoryInfo(h)
        util = self._safe(lambda: nv.nvmlDeviceGetUtilizationRates(h))
        procs = []
        raw = []
        if self.per_process:
            try:
                raw = nv.nvmlDeviceGetComputeRunningProcesses(h) or []
            except getattr(nv, "NVMLError_NotSupported", ()):
                self.per_process = False
            except nv.NVMLError:
                raw = []
        for p in raw:
            used = p.usedGpuMemory
            procs.append({"pid": p.pid, "used_mb": int(used / 2**20) if used else 0})
        power = self._safe(lambda: nv.nvmlDeviceGetPowerUsage(h))
        limit = self._safe(lambda: nv.nvmlDeviceGetEnforcedPowerLimit(h))
        return {
            "available": True,
            "name": self.name,
            "total_mb": int(mem.total / 2**20),
            "used_mb": int(mem.used / 2**20),
            "util": util.gpu if util else None,
            "mem_util": util.memory if util else None,
            "temp": self._safe(lambda: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)),
            "fan": self._safe(lambda: nv.nvmlDeviceGetFanSpeed(h)),
            "power_w": round(power / 1000, 1) if power else None,
            "power_limit_w": round(limit / 1000) if limit else None,
            "processes": procs,
            "per_process": self.per_process,
        }


class MockGpu(GpuMonitor):
    """MockRuntime의 가짜 컨테이너를 GPU 프로세스로 보여준다."""

    def __init__(self, runtime, total_gb: float = 24.0):
        self.runtime = runtime
        self._total = total_gb

    @property
    def total_gb(self) -> float:
        return self._total

    def snapshot(self) -> dict:
        procs = self.runtime.mock_gpu_processes()
        used = sum(p["used_mb"] for p in procs) + 400  # 드라이버/디스플레이 몫
        busy = min(100, int(sum(p["used_mb"] for p in procs) / 120))
        wobble = int(8 * math.sin(time.time() / 7))
        util = max(0, min(100, busy + wobble + random.randint(-3, 3))) if procs else random.randint(0, 2)
        return {
            "available": True,
            "name": "NVIDIA GeForce RTX 3090 (mock)",
            "total_mb": int(self._total * 1024),
            "used_mb": used,
            "util": util,
            "mem_util": min(100, util // 2),
            "temp": 38 + util * 0.42,
            "fan": 30 + util // 2,
            "power_w": round(30 + util * 3.2, 1),
            "power_limit_w": 350,
            "processes": procs,
        }


class NoGpu(GpuMonitor):
    def __init__(self, reason: str):
        self.reason = reason

    @property
    def total_gb(self) -> float:
        return 0.0

    def snapshot(self) -> dict:
        return {"available": False, "error": self.reason, "processes": [],
                "total_mb": 0, "used_mb": 0}


class ResilientGpu(GpuMonitor):
    """NVML 이 실패해도 서버가 멈추지 않게 감싼다.

    드라이버가 아직 안 올라온 채로 서버가 먼저 뜨거나, 실행 중에 드라이버가 다시 올라오면(업데이트, 오류)
    처음 잡은 핸들은 못 쓰게 된다. 실패하면 'GPU 사용 불가'로 보고하고, 잠시 뒤 다시 연결을 시도한다.
    """

    RETRY_S = 30

    def __init__(self, factory=NvmlGpu, clock=time.monotonic):
        self._factory, self._clock = factory, clock
        self._inner: GpuMonitor | None = None
        self._error = "아직 연결하지 않음"
        self._next_try = 0.0
        self._total = 0.0
        self._connect()

    def _connect(self) -> None:
        self._next_try = self._clock() + self.RETRY_S
        try:
            self._inner = self._factory()
            self._total = self._inner.total_gb
        except Exception as e:
            self._inner, self._error = None, f"NVML 초기화 실패: {e}"

    @property
    def total_gb(self) -> float:
        return self._total       # 연결이 끊겨도 마지막으로 알던 용량을 유지한다 (스케줄러 계산용)

    def snapshot(self) -> dict:
        if self._inner is None and self._clock() >= self._next_try:
            self._connect()
        if self._inner is not None:
            try:
                return self._inner.snapshot()
            except Exception as e:
                self._inner, self._error = None, f"GPU 상태를 읽지 못했습니다: {e}"
                self._next_try = self._clock() + self.RETRY_S
        return {"available": False, "error": self._error, "processes": [], "total_mb": 0, "used_mb": 0}


def make_gpu(settings: Settings, runtime) -> GpuMonitor:
    if settings.mock:
        return MockGpu(runtime, settings.gpu_total_vram_gb or 24.0)
    index = settings.gpu_index
    if resolve_vendor(settings) == "amd":
        return ResilientGpu(factory=lambda: AmdGpu(index))
    return ResilientGpu(factory=lambda: NvmlGpu(index))
