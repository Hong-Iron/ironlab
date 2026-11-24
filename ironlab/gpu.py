"""GPU 상태 수집 (NVML). 프로세스별 VRAM을 컨테이너·사용자에 매핑하는 건 monitor가 한다."""

from __future__ import annotations

import math
import random
import time

from .config import Settings


class GpuMonitor:
    def snapshot(self) -> dict:
        raise NotImplementedError

    @property
    def total_gb(self) -> float:
        raise NotImplementedError


class NvmlGpu(GpuMonitor):
    def __init__(self, index: int = 0):
        import pynvml
        self.nv = pynvml
        pynvml.nvmlInit()
        self.h = pynvml.nvmlDeviceGetHandleByIndex(index)
        name = pynvml.nvmlDeviceGetName(self.h)
        self.name = name.decode() if isinstance(name, bytes) else name
        self._total = pynvml.nvmlDeviceGetMemoryInfo(self.h).total

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
        for p in self._safe(lambda: nv.nvmlDeviceGetComputeRunningProcesses(h), []) or []:
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
    return ResilientGpu(factory=lambda: NvmlGpu(index))
