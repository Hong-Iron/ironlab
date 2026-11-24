import pytest
from fastapi.testclient import TestClient

from ironlab import users as um
from ironlab.api import create_app
from ironlab.config import load_settings


@pytest.fixture
def settings(tmp_path):
    s = load_settings(mock=True, data_dir=tmp_path / "data", users_root=tmp_path / "users",
                      shared_root=tmp_path / "shared", scratch_root=tmp_path / "scratch",
                      public_host="gpubox.test", idle_stop_hours=0)
    s.ensure_dirs()
    return s


@pytest.fixture
def env(settings):
    app = create_app(settings, start_monitor=False)
    ctx = app.state.ctx
    _, admin_tok = um.create_user(ctx.db, settings, "boss", role="admin")
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {admin_tok}"}
    r = client.post("/api/admin/users", json={"name": "alice", "cpus": 4, "mem_gb": 8}, headers=admin)
    assert r.status_code == 200, r.text
    alice = {"Authorization": f"Bearer {r.json()['token']}"}
    return client, ctx, admin, alice
