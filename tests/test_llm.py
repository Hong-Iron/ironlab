import json
import time

import pytest
from fastapi.testclient import TestClient

from ironlab import users as um
from ironlab.api import create_app, create_internal_app


@pytest.fixture
def env(settings):
    settings.llm_idle_unload_min = 20
    app = create_app(settings, start_monitor=False)
    ctx = app.state.ctx
    _, at = um.create_user(ctx.db, settings, "boss", role="admin")
    _, ut = um.create_user(ctx.db, settings, "alice")
    _, bt = um.create_user(ctx.db, settings, "bob")
    c = TestClient(app)
    h = lambda t: {"Authorization": f"Bearer {t}"}
    return c, ctx, h(at), h(ut), h(bt)


QWEN = {"name": "qwen3.8-27b", "file": "Qwen3.8-27B-UD-Q4_K_XL.gguf", "vram_gb": 20, "ctx": 32768, "parallel": 2}


def ready(ctx):
    """mock llama-server 를 '로딩 끝' 상태로 만든다."""
    for c in ctx.rt.containers.values():
        if c["kind"] == "service":
            c["started_at"] -= 10
    ctx.monitor.collect()
    assert ctx.llm.state == "ready"


def test_register_admin_only(env):
    c, ctx, admin, alice, bob = env
    assert c.post("/api/llm/models", json=QWEN, headers=alice).status_code == 403
    r = c.post("/api/llm/models", json=QWEN, headers=admin)
    assert r.status_code == 200, r.text
    assert c.post("/api/llm/models", json=QWEN, headers=admin).status_code == 400  # 중복
    bad = dict(QWEN, name="x", file="../../etc/passwd")
    assert c.post("/api/llm/models", json=bad, headers=admin).status_code == 400
    assert [m["name"] for m in c.get("/api/llm", headers=alice).json()["models"]] == ["qwen3.8-27b"]


def test_load_checks_vram_budget(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    # 큰 3D 작업이 GPU를 잡고 있으면 올릴 수 없다
    c.post("/api/jobs", json={"command": "sleep 30", "vram_gb": 12}, headers=bob)
    ctx.monitor.collect()
    r = c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    assert r.status_code == 409 and "VRAM" in r.json()["detail"]


def test_load_proxy_and_usage(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    ctx.monitor.collect()
    # 올리기 전에는 503
    r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}, headers=alice)
    assert r.status_code == 503
    r = c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    assert r.status_code == 200 and r.json()["state"] == "loading"
    r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}, headers=alice)
    assert r.status_code == 503 and "올리는 중" in r.json()["error"]["message"]
    ready(ctx)

    assert c.get("/v1/models", headers=alice).json()["data"][0]["id"] == "qwen3.8-27b"
    r = c.post("/v1/chat/completions", json={"model": "anything", "messages": [{"role": "user", "content": "안녕"}]},
               headers=alice)
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "qwen3.8-27b" and "안녕" in body["choices"][0]["message"]["content"]

    with c.stream("POST", "/v1/chat/completions", headers=alice | {"x-ironlab-source": "console"},
                  json={"messages": [{"role": "user", "content": "스트림"}], "stream": True}) as s:
        lines = [ln for ln in s.iter_lines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    text = "".join(json.loads(ln[6:])["choices"][0]["delta"].get("content", "")
                   for ln in lines[:-1] if json.loads(ln[6:]).get("choices"))
    assert "스트림" in text

    rows = ctx.db.all("SELECT user, source, completion_tokens FROM llm_usage ORDER BY id")
    assert [(r["user"], r["source"]) for r in rows] == [("alice", "api"), ("alice", "console")]
    assert all(r["completion_tokens"] > 0 for r in rows)
    usage = c.get("/api/llm", headers=alice).json()["usage_today"]
    assert usage[0]["user"] == "alice" and usage[0]["requests"] == 2


def test_gpu_view_and_queue_see_llm(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    ctx.monitor.collect()
    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    snap = ctx.monitor.collect()
    svc = [p for p in snap["gpu"]["processes"] if p["kind"] == "service"]
    assert svc and svc[0]["model"] == "qwen3.8-27b"
    # LLM 이 20GB 가까이 쓰므로 큰 작업은 대기
    jid = c.post("/api/jobs", json={"command": "sleep 5", "vram_gb": 8}, headers=bob).json()["id"]
    ctx.monitor.collect()
    assert ctx.sched.get(jid)["status"] == "queued"
    assert snap["llm"]["model"]["name"] == "qwen3.8-27b"


def test_unload_permissions_and_idle(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    ctx.monitor.collect()
    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    assert c.post("/api/llm/unload", headers=bob).status_code == 403
    assert c.post("/api/llm/unload", headers=alice).json()["state"] == "off"

    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    ready(ctx)
    ctx.llm.last_used = time.time() - 21 * 60
    ctx.monitor.collect()
    assert ctx.llm.status()["state"] == "off"
    ev = [e["kind"] for e in ctx.db.recent_events(20)]
    assert "llm.unload" in ev


def test_internal_proxy_identifies_container(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    c.post("/api/workspace/start", headers=alice)
    ctx.monitor.collect()
    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    ready(ctx)
    ip = next(iter(ctx.llm.ip_users))
    assert ctx.llm.user_for_ip(ip) == "alice"
    assert ctx.llm.user_for_ip("10.9.9.9") is None
    # 컨테이너가 아닌 곳에서 온 요청은 거절
    ic = TestClient(create_internal_app(ctx))
    assert ic.post("/v1/chat/completions", json={"messages": []}).status_code == 403


def test_workspace_env_has_llm_url(env):
    c, ctx, *_ = env
    env_vars = ctx.rt.env_for({"name": "alice", "uid": 20002})
    assert env_vars["OPENAI_BASE_URL"] == "http://172.30.0.1:8701/v1"


# ── 컨텍스트 길이 알리기, 등록한 모델 고치기, 실제 VRAM 기록 ──

def test_models_endpoint_reports_the_context_each_request_gets(env):
    """클라이언트(에이전트 도구 등)가 길이를 짐작하지 않도록 /v1/models 에 요청당 컨텍스트를 싣는다."""
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    ctx.monitor.collect()
    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    ready(ctx)
    card = c.get("/v1/models", headers=alice).json()["data"][0]
    assert card["id"] == "qwen3.8-27b"
    assert card["context_length"] == 16384 == card["meta"]["n_ctx"]         # 32768 토큰 ÷ 동시 요청 2
    assert c.get("/api/llm", headers=alice).json()["ctx_per_request"] == 16384


def test_registered_model_can_be_edited_after_unloading(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    ctx.monitor.collect()
    url = "/api/llm/models/qwen3.8-27b"
    assert c.patch(url, json={"ctx": 65536}, headers=alice).status_code == 403          # 관리자만
    assert c.patch("/api/llm/models/nope", json={"ctx": 65536}, headers=admin).status_code == 400

    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    r = c.patch(url, json={"ctx": 65536}, headers=admin)
    assert r.status_code == 400 and "먼저 내린 뒤" in r.json()["detail"]                # 올라가 있으면 못 고친다
    c.post("/api/llm/unload", headers=alice)

    r = c.patch(url, json={"ctx": 65536, "parallel": 1, "extra_args": "-ctk q8_0 -ctv q8_0"}, headers=admin)
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["ctx"], m["parallel"], m["extra_args"], m["vram_gb"]) == (65536, 1, "-ctk q8_0 -ctv q8_0", 20)
    assert m["file"] == QWEN["file"]                                                    # 보내지 않은 값은 그대로
    ev = next(e for e in ctx.db.recent_events(20) if e["kind"] == "llm.update")
    assert ev["user"] == "boss" and "ctx: 32768 → 65536" in ev["detail"]

    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    ready(ctx)
    assert c.get("/v1/models", headers=alice).json()["data"][0]["context_length"] == 65536
    cmd = next(x for x in ctx.rt.containers.values() if x["kind"] == "service")["_log"][0]
    assert "-c 65536 -np 1" in cmd and cmd.endswith("-ctk q8_0 -ctv q8_0")


def test_edit_rejects_bad_values_and_says_what_it_got(env):
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    url = "/api/llm/models/qwen3.8-27b"
    r = c.patch(url, json={"ctx": 100}, headers=admin)
    assert r.status_code == 400 and "100" in r.json()["detail"] and "512" in r.json()["detail"]
    assert c.patch(url, json={"parallel": 0}, headers=admin).status_code == 400
    assert c.patch(url, json={"vram_gb": 200}, headers=admin).status_code == 400
    assert c.patch(url, json={"file": "../../etc/passwd"}, headers=admin).status_code == 400
    r = c.patch(url, json={"extra_args": '--chat-template "unclosed'}, headers=admin)
    assert r.status_code == 400 and "따옴표" in r.json()["detail"]
    assert c.post("/api/llm/models", json=dict(QWEN, name="other", extra_args="'x"), headers=admin).status_code == 400
    assert ctx.llm.model("qwen3.8-27b")["ctx"] == 32768                                 # 거절된 수정은 아무것도 바꾸지 않는다


def test_actual_vram_use_is_shown_and_kept_for_the_model(env):
    """예약값은 사람이 어림한 숫자다. 실제로 쓰는 양을 보여 주고 남겨 둬야 컨텍스트를 더 늘릴지 정할 수 있다."""
    c, ctx, admin, alice, bob = env
    c.post("/api/llm/models", json=QWEN, headers=admin)
    ctx.monitor.collect()
    assert c.get("/api/llm", headers=alice).json()["vram_used_gb"] is None
    c.post("/api/llm/load", json={"name": "qwen3.8-27b"}, headers=alice)
    ready(ctx)
    ctx.monitor.collect()
    d = c.get("/api/llm", headers=alice).json()
    assert d["vram_used_gb"] == pytest.approx(19.2, abs=0.05)                           # mock 은 예약의 96% 를 쓴다
    assert d["models"][0]["measured_vram_gb"] == pytest.approx(19.2, abs=0.05)

    c.post("/api/llm/unload", headers=alice)
    ctx.monitor.collect()
    d = c.get("/api/llm", headers=alice).json()
    assert d["vram_used_gb"] is None and d["models"][0]["measured_vram_gb"] == pytest.approx(19.2, abs=0.05)
    url = "/api/llm/models/qwen3.8-27b"
    assert c.patch(url, json={"vram_gb": 21}, headers=admin).json()["measured_vram_gb"] is not None     # 예약만 바꾸면 그대로
    assert c.patch(url, json={"ctx": 49152}, headers=admin).json()["measured_vram_gb"] is None           # 길이를 바꾸면 다시 재야 한다


def test_old_database_gets_the_new_column(tmp_path):
    """예전 버전이 만든 DB 에는 measured_vram_gb 열이 없다. 서버를 띄울 때 더한다 (등록해 둔 모델은 그대로)."""
    import sqlite3
    from ironlab.db import DB
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE llm_models (name TEXT PRIMARY KEY, file TEXT NOT NULL, mmproj TEXT NOT NULL DEFAULT '',
                   ctx INTEGER NOT NULL DEFAULT 32768, parallel INTEGER NOT NULL DEFAULT 2, vram_gb REAL NOT NULL,
                   extra_args TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)""")
    con.execute("INSERT INTO llm_models(name, file, vram_gb, created_at) VALUES ('qwen', 'q.gguf', 20, 0)")
    con.commit(); con.close()
    for _ in range(2):                                   # 두 번 띄워도 탈이 없다
        db = DB(path)
        row = db.one("SELECT * FROM llm_models WHERE name='qwen'")
        assert row["ctx"] == 32768 and row["measured_vram_gb"] is None
        db.close()
