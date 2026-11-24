"""Docker·GPU 가 말을 듣지 않을 때의 동작."""

import time

from ironlab.runtime import job_name


def submit(client, who, **kw):
    r = client.post("/api/jobs", json={"command": "sleep 30", "vram_gb": 4} | kw, headers=who)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def status(ctx, job_id):
    return ctx.sched.get(job_id)["status"]


def test_job_start_is_retried_when_docker_hiccups(env, monkeypatch):
    client, ctx, admin, alice = env
    real = ctx.rt.run_job
    fails = {"left": 2}

    def flaky(job, user):
        if fails["left"]:
            fails["left"] -= 1
            raise RuntimeError("docker timeout")
        return real(job, user)
    monkeypatch.setattr(ctx.rt, "run_job", flaky)
    jid = submit(client, alice)
    ctx.monitor.collect()
    assert status(ctx, jid) == "queued"                         # 작업 잘못이 아니므로 실패로 처리하지 않는다
    assert "다시 시도" in client.get(f"/api/jobs/{jid}", headers=alice).json()["wait_reason"]
    ctx.monitor.collect()
    ctx.monitor.collect()
    assert status(ctx, jid) == "running"


def test_job_fails_after_repeated_start_failures(env, monkeypatch):
    client, ctx, admin, alice = env
    monkeypatch.setattr(ctx.rt, "run_job", lambda job, user: (_ for _ in ()).throw(RuntimeError("no such image")))
    jid = submit(client, alice)
    for _ in range(3):
        ctx.monitor.collect()
    job = ctx.sched.get(jid)
    assert job["status"] == "failed" and "no such image" in job["error"]


def test_lost_response_does_not_leave_a_second_container(env, monkeypatch):
    """Docker 는 컨테이너를 띄웠는데 응답만 못 받은 경우: 다시 시도할 때 먼저 떠 있던 것을 치운다."""
    client, ctx, admin, alice = env
    real = ctx.rt.run_job
    state = {"first": True}

    def lossy(job, user):
        cid = real(job, user)
        if state["first"]:
            state["first"] = False
            raise RuntimeError("read timeout")
        return cid
    monkeypatch.setattr(ctx.rt, "run_job", lossy)
    jid = submit(client, alice)
    ctx.monitor.collect()
    ctx.monitor.collect()
    assert status(ctx, jid) == "running"
    same_name = [c for c in ctx.rt.list_managed() if c["name"] == job_name(jid)]
    assert len(same_name) == 1 and same_name[0]["id"] == ctx.sched.get(jid)["container_id"]


def test_orphan_job_containers_are_removed(env):
    """기록에 없는데 GPU 를 쓰며 돌고 있는 작업 컨테이너 (띄운 직후 서버가 죽은 경우 등)."""
    client, ctx, admin, alice = env
    user = ctx.db.get_user("alice")
    ctx.rt.run_job({"id": 999, "command": "sleep 600", "vram_gb": 20, "mem_gb": 8, "cpus": 4, "workdir": ""}, user)
    jid = submit(client, alice)
    ctx.monitor.collect()                       # 진짜 작업이 시작된다
    ctx.monitor.collect()                       # 고아는 이때 정리된다
    names = [c["name"] for c in ctx.rt.list_managed() if c["kind"] == "job"]
    assert names == [job_name(jid)] and status(ctx, jid) == "running"
    assert any(e["kind"] == "job.orphan_removed" for e in ctx.db.recent_events(20))


def test_queue_pauses_while_docker_is_down_and_resumes(env, monkeypatch):
    client, ctx, admin, alice = env
    running = submit(client, alice)
    ctx.monitor.collect()
    assert status(ctx, running) == "running"
    real = ctx.rt.list_managed
    monkeypatch.setattr(ctx.rt, "list_managed", lambda: (_ for _ in ()).throw(ConnectionError("docker.sock refused")))
    queued = submit(client, admin)
    before = len(ctx.monitor.recent)
    ctx.monitor.collect()
    o = client.get("/api/overview", headers=alice).json()
    assert o["backend_error"] and any("Docker 가 응답하지 않아" in a for a in o["alerts"])
    assert len(ctx.monitor.recent) == before + 1                 # 호스트·GPU 수치는 계속 쌓인다
    assert status(ctx, running) == "running" and status(ctx, queued) == "queued"     # 멀쩡한 작업을 실패로 만들지 않는다
    assert client.get("/api/health").json()["backend_error"]
    monkeypatch.setattr(ctx.rt, "list_managed", real)
    ctx.monitor.collect()
    o = client.get("/api/overview", headers=alice).json()
    assert o["backend_error"] is None and not any("Docker" in a for a in o["alerts"])
    assert status(ctx, queued) == "running"
    kinds = [e["kind"] for e in ctx.db.recent_events(30)]
    assert kinds.count("backend.down") == 1 and kinds.count("backend.up") == 1      # 시작과 끝에 한 번씩만 기록


def test_no_new_jobs_start_while_gpu_cannot_be_read(env, monkeypatch):
    client, ctx, admin, alice = env
    monkeypatch.setattr(ctx.gpu, "snapshot", lambda: (_ for _ in ()).throw(RuntimeError("GPU is lost")))
    jid = submit(client, alice)
    ctx.monitor.collect()
    assert status(ctx, jid) == "queued"
    assert "GPU 상태를 읽을 수 없어" in client.get(f"/api/jobs/{jid}", headers=alice).json()["wait_reason"]
    assert client.get("/api/overview", headers=alice).json()["gpu"]["available"] is False


def test_one_unreadable_container_does_not_stop_the_whole_collection(env, monkeypatch):
    client, ctx, admin, alice = env
    client.post("/api/workspace/start", headers=alice)
    monkeypatch.setattr(ctx.rt, "ssh_sessions", lambda cid: (_ for _ in ()).throw(RuntimeError("top failed")))
    monkeypatch.setattr(ctx.rt, "container_for_pid", lambda pid: (_ for _ in ()).throw(OSError("proc gone")))
    before = ctx.monitor.latest.get("ts", 0)
    ctx.monitor.collect()
    assert ctx.monitor.latest["ts"] > before and ctx.monitor.backend_error is None


def test_unexpected_errors_come_back_with_a_reason(settings):
    from fastapi.testclient import TestClient
    from ironlab import users as um
    from ironlab.api import create_app
    app = create_app(settings, start_monitor=False)
    ctx = app.state.ctx
    _, tok = um.create_user(ctx.db, settings, "alice")
    client = TestClient(app, raise_server_exceptions=False)
    ctx.rt.get_workspace = lambda name: (_ for _ in ()).throw(RuntimeError("socket closed"))
    r = client.get("/api/workspace", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 500 and "socket closed" in r.json()["detail"]


def test_overview_says_how_old_the_numbers_are(env):
    client, ctx, admin, alice = env
    ctx.monitor.collect()
    assert client.get("/api/overview", headers=alice).json()["age_s"] < 2
    ctx.monitor.latest = ctx.monitor.latest | {"ts": time.time() - 120}      # 수집이 2분 전에 멈춘 상황
    assert client.get("/api/overview", headers=alice).json()["age_s"] >= 120
    assert client.get("/api/health").json()["monitor_age_s"] >= 120


def test_key_file_is_restored_at_startup(settings):
    """키 파일이 비거나 사라져도(쓰는 도중 전원이 나간 경우 등) 서버를 다시 띄우면 등록된 키로 복구된다."""
    from ironlab import users as um
    from ironlab.api import create_app
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ" + "a" * 37 + " laptop"
    app = create_app(settings, start_monitor=False)
    ctx = app.state.ctx
    um.create_user(ctx.db, settings, "alice")
    um.update_user(ctx.db, "alice", ssh_pubkeys=key) if "ssh_pubkeys" in um.UPDATABLE else ctx.db.execute(
        "UPDATE users SET ssh_pubkeys=? WHERE name='alice'", (key,))
    f = settings.keys_dir / "alice" / "authorized_keys"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("")                                    # 쓰다 만 빈 파일
    ctx.db.conn.close() if hasattr(ctx.db, "conn") else None
    create_app(settings, start_monitor=False)           # 서버 재시작
    assert f.read_text().strip() == key
    assert not (f.parent / ".authorized_keys.new").exists()


def test_old_job_logs_and_records_are_pruned(env):
    import os
    client, ctx, admin, alice = env
    now = time.time()
    old, new = ctx.s.logs_dir / "job-1.log", ctx.s.logs_dir / "job-2.log"
    old.write_text("old"); new.write_text("new")
    os.utime(old, (now - 40 * 86400, now - 40 * 86400))
    ctx.db.execute("INSERT INTO events(ts, kind, user, detail) VALUES (?,?,?,?)", (now - 400 * 86400, "ancient", None, ""))
    ctx.db.log_event("recent", None, "")
    out = ctx.monitor.maintain(now)
    assert out["job_logs"] == 1 and not old.exists() and new.exists()
    kinds = [e["kind"] for e in ctx.db.recent_events(50)]
    assert "ancient" not in kinds and "recent" in kinds


def test_slow_disk_scan_does_not_block_collection(env, monkeypatch):
    """홈에 파일이 아주 많으면 사용량 계산이 오래 걸린다. 그동안에도 수집(화면 갱신·작업 큐)은 계속 돈다."""
    import ironlab.monitor as mon
    client, ctx, admin, alice = env
    monkeypatch.setattr(mon, "disk_usage_bytes", lambda path: (time.sleep(1.0), 123)[1])
    ctx.monitor._disk_checked = 0
    t0 = time.time()
    ctx.monitor.collect()
    assert time.time() - t0 < 0.8
    ctx.monitor._disk_thread.join(timeout=5)
    assert set(ctx.monitor.disk_used.values()) == {123}
