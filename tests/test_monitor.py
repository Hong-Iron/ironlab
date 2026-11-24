"""호스트 CPU·메모리 수집과 1초 주기 가벼운 갱신."""

from pathlib import Path

from ironlab.monitor import GB, _p_threads
from ironlab.runtime import CgroupStats


def test_overview_reports_cpu_and_memory_breakdown(env):
    client, ctx, admin, alice = env
    client.post("/api/workspace/start", headers=alice)
    ctx.monitor.collect()
    o = client.get("/api/overview", headers=alice).json()
    h = o["host"]
    assert len(h["per_cpu"]) == h["cores"] and all(0 <= v <= 100 for v in h["per_cpu"])
    assert 0 < h["mem_used"] <= h["mem_total"] and h["mem_cached"] <= h["mem_total"] - h["mem_used"]
    # 사용자 몫과 공유 LLM 몫을 합쳐도 호스트 전체 사용량을 넘지 않는다 (막대가 넘치지 않도록)
    user_mem = sum(u["mem_bytes"] for u in o["users"]) + o["services"]["mem_bytes"]
    assert user_mem <= h["mem_used"]
    assert sum(u["cpu_cores"] for u in o["users"]) <= h["cpu_percent"] * h["cores"] / 100 + 0.5
    p = o["procs"][0]
    assert set(p) >= {"pid", "name", "cpu_cores", "mem_bytes", "user", "kind"}
    assert any(q["user"] == "alice" and q["kind"] == "workspace" for q in o["procs"])


def test_history_points_carry_per_user_cpu_and_memory(env):
    client, ctx, admin, alice = env
    client.post("/api/workspace/start", headers=alice)
    ctx.monitor.collect()
    pt = client.get("/api/history", headers=alice).json()[-1]
    assert {"cpu_cores", "mem_gb", "cpu_users", "mem_users", "cpu_llm", "mem_llm"} <= set(pt)
    assert pt["mem_users"]["alice"] > 0


def test_sample_refreshes_usage_without_running_the_heavy_work(env):
    client, ctx, admin, alice = env
    m = ctx.monitor
    client.post("/api/workspace/start", headers=alice)
    m.collect()
    slow, fast = len(m.history), len(m.recent)
    calls = {"list": 0, "tick": 0}
    real_list, real_tick = m.rt.list_managed, m.sched.tick
    m.rt.list_managed = lambda: (calls.__setitem__("list", calls["list"] + 1), real_list())[1]
    m.sched.tick = lambda *a, **k: (calls.__setitem__("tick", calls["tick"] + 1), real_tick(*a, **k))[1]
    before = m.latest["ts"]
    snap = m.sample()
    assert calls == {"list": 0, "tick": 0}                      # Docker 목록 조회도, 스케줄러도 돌지 않는다
    assert snap["ts"] > before and snap["users"] and snap["host"]["per_cpu"]
    assert len(m.recent) == fast + 1 and len(m.history) == slow  # 촘촘한 기록에만 쌓인다
    assert len(client.get("/api/history?res=fast", headers=alice).json()) == fast + 1
    assert client.get("/api/history?res=bogus", headers=alice).status_code == 422


def test_reading_overview_marks_the_dashboard_as_watched(env):
    client, ctx, admin, alice = env
    assert ctx.monitor.last_watch == 0
    client.get("/api/overview", headers=alice)
    assert ctx.monitor.last_watch > 0


def test_memory_alert_when_ram_is_nearly_full(env):
    client, ctx, admin, alice = env
    gpu = {"available": True, "used_mb": 0, "temp": 40, "processes": []}
    assert ctx.monitor._alerts(gpu, {"mem_used": 50 * GB, "mem_total": 64 * GB}, 0) == []
    alerts = ctx.monitor._alerts(gpu, {"mem_used": 61 * GB, "mem_total": 64 * GB}, 0)
    assert len(alerts) == 1 and "메모리 61.0/64GB" in alerts[0]


def test_p_core_thread_count(tmp_path):
    f = tmp_path / "cpus"
    f.write_text("0-15\n"); assert _p_threads(str(f)) == 16          # 13900K: P코어 8개 × 2스레드
    f.write_text("0-3,4-7\n"); assert _p_threads(str(f)) == 8
    f.write_text("8-15\n"); assert _p_threads(str(f)) is None         # 0번부터가 아니면 줄을 나누지 않는다
    assert _p_threads(str(tmp_path / "none")) is None                 # 섞이지 않은 CPU


def make_cgroup(root: Path, cid: str, usage_usec: int, current: int, inactive: int, limit: str = "max") -> Path:
    d = root / "system.slice" / f"docker-{cid}.scope"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 1\nsystem_usec 1\n")
    (d / "memory.current").write_text(f"{current}\n")
    (d / "memory.stat").write_text(f"anon 1\nfile 2\ninactive_file {inactive}\nactive_file 3\n")
    (d / "memory.max").write_text(f"{limit}\n")
    return d


def test_cgroup_stats_turns_cumulative_cpu_time_into_cores(tmp_path):
    cg = CgroupStats(tmp_path)
    make_cgroup(tmp_path, "abc", 10_000_000, 5 * GB, 1 * GB, str(16 * GB))
    first = cg.read("abc", now=100.0)
    assert first == {"cpu_cores": 0.0, "mem_bytes": 4 * GB, "mem_limit": 16 * GB}   # 첫 측정은 비교할 값이 없다
    make_cgroup(tmp_path, "abc", 13_000_000, 6 * GB, 1 * GB)                        # 1초 동안 CPU 3초 = 3코어
    assert cg.read("abc", now=101.0) == {"cpu_cores": 3.0, "mem_bytes": 5 * GB, "mem_limit": 0}
    make_cgroup(tmp_path, "abc", 13_500_000, 6 * GB, 1 * GB)
    assert cg.read("abc", now=103.0)["cpu_cores"] == 0.25                           # 2초 동안 0.5초


def test_cgroup_stats_handles_missing_and_recreated_containers(tmp_path):
    cg = CgroupStats(tmp_path)
    assert cg.read("gone") is None                                    # 없는 컨테이너: 호출한 쪽이 다른 방법을 쓴다
    d = make_cgroup(tmp_path, "abc", 5_000_000, GB, 0)
    cg.read("abc", now=1.0)
    (d / "cpu.stat").unlink()                                         # 컨테이너가 사라짐
    assert cg.read("abc", now=2.0) is None
    make_cgroup(tmp_path, "abc", 1_000_000, GB, 0)                    # 같은 이름으로 다시 생김: 누적값이 줄어든다
    assert cg.read("abc", now=3.0)["cpu_cores"] == 0.0                # 음수나 엉뚱한 값이 나오지 않는다
    # cgroupfs 드라이버 경로도 찾는다
    alt = tmp_path / "docker" / "xyz"; alt.mkdir(parents=True)
    for name in ("cpu.stat", "memory.current", "memory.stat", "memory.max"):
        (alt / name).write_text((d / name).read_text())
    assert cg.read("xyz", now=1.0) is not None


def test_gpu_monitor_recovers_after_driver_errors():
    """드라이버가 늦게 올라오거나 중간에 끊겨도 서버는 계속 돌고, 다시 연결되면 정상으로 돌아온다."""
    from ironlab.gpu import ResilientGpu

    class Flaky:
        total_gb = 24.0
        broken = False

        def snapshot(self):
            if Flaky.broken:
                raise RuntimeError("GPU is lost")
            return {"available": True, "used_mb": 100, "total_mb": 24576, "processes": []}

    state = {"now": 0.0, "driver_ready": False}

    def factory():
        if not state["driver_ready"]:
            raise RuntimeError("Driver Not Loaded")
        return Flaky()

    g = ResilientGpu(factory=factory, clock=lambda: state["now"])
    snap = g.snapshot()
    assert snap["available"] is False and "Driver Not Loaded" in snap["error"] and g.total_gb == 0
    state["driver_ready"] = True
    assert g.snapshot()["available"] is False               # 재시도 간격 전에는 다시 두드리지 않는다
    state["now"] = 31
    assert g.snapshot()["available"] is True and g.total_gb == 24.0
    Flaky.broken = True
    assert g.snapshot()["available"] is False               # 읽다가 실패해도 예외가 밖으로 나가지 않는다
    Flaky.broken = False
    state["now"] = 62
    assert g.snapshot()["available"] is True


def test_failed_collect_does_not_spin(env, monkeypatch):
    """Docker 가 죽어 수집이 실패해도 1초마다 다시 시도하며 로그를 쏟아내지 않는다."""
    import threading
    import time as _time
    client, ctx, admin, alice = env
    m = ctx.monitor
    calls = []
    monkeypatch.setattr(m, "collect", lambda: (calls.append(_time.time()), (_ for _ in ()).throw(RuntimeError("docker down")))[1])
    monkeypatch.setattr(m.s, "monitor_interval_s", 5.0)
    monkeypatch.setattr(m.s, "sample_interval_s", 0.05)
    t = threading.Thread(target=m._loop, daemon=True)
    t.start()
    _time.sleep(0.6)
    m._stop.set()
    t.join(timeout=2)
    assert len(calls) == 1


# ── 논리 코어 일부를 꺼 둔 서버 (예: 고장 난 P코어의 8·9번을 끈 13900K) ──

def test_p_core_count_follows_the_cores_that_are_online(tmp_path):
    online = [*range(0, 8), *range(10, 32)]              # 8·9번을 끈 상태: 켜진 것은 0-7,10-31
    f = tmp_path / "cpus"
    f.write_text("0-15\n"); assert _p_threads(str(f), online) == 14            # 커널이 꺼진 코어까지 적어 줄 때
    f.write_text("0-7,10-15\n"); assert _p_threads(str(f), online) == 14       # 켜진 코어만 적어 줄 때
    f.write_text("0-15\n"); assert _p_threads(str(f), list(range(32))) == 16   # 다 켜져 있으면 예전과 같다
    f.write_text("16-31\n"); assert _p_threads(str(f), list(range(32))) is None    # P코어가 앞쪽에 없으면 줄을 나누지 않는다


def test_cpu_layout_lists_cores_that_are_switched_off(tmp_path):
    from ironlab.monitor import _cpu_layout
    (tmp_path / "online").write_text("0-7,10-31\n")
    (tmp_path / "present").write_text("0-31\n")
    online, offline = _cpu_layout(str(tmp_path))
    assert len(online) == 30 and online[8] == 10 and offline == [8, 9]
    assert _cpu_layout(str(tmp_path / "none")) == (None, [])                   # 리눅스가 아닌 곳


def test_core_numbers_are_real_ids_not_positions(env):
    """8·9번을 끄면 아홉 번째 칸은 10번 코어다. 화면과 지표가 자리 번호(8)가 아니라 실제 번호(10)를 쓰게 한다."""
    from ironlab.metrics import render_prometheus
    client, ctx, admin, alice = env
    ctx.monitor.collect()
    h = client.get("/api/overview", headers=alice).json()["host"]
    assert h["cpu_ids"] is None or len(h["cpu_ids"]) == len(h["per_cpu"])
    assert h["cpu_offline"] == []
    snap = ctx.monitor.latest | {"host": ctx.monitor.latest["host"] | {"per_cpu": [5, 6, 7], "cpu_ids": [7, 10, 11]}}
    text = render_prometheus(snap, ctx.db)
    assert 'ironlab_host_cpu_core_percent{core="10"} 6' in text and 'core="8"' not in text
    snap["host"]["cpu_ids"] = None                       # 번호를 모르면 예전처럼 자리 번호
    assert 'ironlab_host_cpu_core_percent{core="1"} 6' in render_prometheus(snap, ctx.db)
