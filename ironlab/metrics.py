"""Prometheus 텍스트 포맷 내보내기 (장기 보관이 필요하면 Prometheus가 /metrics를 긁어가게)."""

from __future__ import annotations

from .db import DB


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_prometheus(snap: dict, db: DB) -> str:
    out: list[str] = []

    def metric(name: str, help_: str, mtype: str, samples: list[tuple[dict, float]]):
        out.append(f"# HELP ironlab_{name} {help_}")
        out.append(f"# TYPE ironlab_{name} {mtype}")
        for labels, value in samples:
            if value is None:
                continue
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
            out.append(f"ironlab_{name}{{{lab}}} {value}" if lab else f"ironlab_{name} {value}")

    if not snap:
        return "# no data yet\n"
    gpu, host = snap.get("gpu", {}), snap.get("host", {})
    if gpu.get("available"):
        metric("gpu_utilization_percent", "GPU 사용률", "gauge", [({}, gpu.get("util"))])
        metric("gpu_memory_used_bytes", "GPU 메모리 사용량", "gauge", [({}, gpu["used_mb"] * 2**20)])
        metric("gpu_memory_total_bytes", "GPU 메모리 전체", "gauge", [({}, gpu["total_mb"] * 2**20)])
        metric("gpu_temperature_celsius", "GPU 온도", "gauge", [({}, gpu.get("temp"))])
        metric("gpu_power_watts", "GPU 전력", "gauge", [({}, gpu.get("power_w"))])
    metric("host_cpu_percent", "호스트 CPU 사용률", "gauge", [({}, host.get("cpu_percent"))])
    metric("host_cpu_core_percent", "논리 코어별 CPU 사용률", "gauge",
           [({"core": cpu}, v) for cpu, v in zip(host.get("cpu_ids") or range(len(host.get("per_cpu") or [])),
                                                host.get("per_cpu") or [])])
    metric("host_memory_used_bytes", "호스트 메모리 사용량", "gauge", [({}, host.get("mem_used"))])
    metric("host_memory_total_bytes", "호스트 메모리 전체", "gauge", [({}, host.get("mem_total"))])
    metric("host_swap_used_bytes", "스왑 사용량", "gauge", [({}, host.get("swap_used"))])
    metric("host_disk_used_bytes", "사용자 디스크 사용량", "gauge", [({}, host.get("disk_used"))])

    users = snap.get("users", [])
    metric("user_cpu_cores", "사용자별 CPU 코어 사용량", "gauge",
           [({"user": u["name"]}, u["cpu_cores"]) for u in users])
    metric("user_memory_bytes", "사용자별 메모리 사용량", "gauge",
           [({"user": u["name"]}, u["mem_bytes"]) for u in users])
    metric("user_gpu_memory_bytes", "사용자별 GPU 메모리 사용량", "gauge",
           [({"user": u["name"]}, u["vram_mb"] * 2**20) for u in users])
    metric("user_disk_bytes", "사용자별 홈 디스크 사용량", "gauge",
           [({"user": u["name"]}, u["disk_used"]) for u in users])
    metric("user_ssh_sessions", "사용자별 SSH 세션 수", "gauge",
           [({"user": u["name"]}, u["ssh_sessions"]) for u in users])
    metric("user_workspace_running", "워크스페이스 실행 여부", "gauge",
           [({"user": u["name"]}, 1 if u["workspace"] and u["workspace"]["state"] == "running" else 0)
            for u in users])

    counts = db.all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    metric("jobs", "상태별 작업 수", "gauge", [({"status": r["status"]}, r["n"]) for r in counts])
    return "\n".join(out) + "\n"
