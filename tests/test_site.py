"""서버마다 다른 설정: 서버 이름, 콘솔 주소, 관리할 GPU 번호."""

from ironlab import gpu as gpu_mod
from ironlab.config import load_settings


def test_site_is_public_and_named(env, settings):
    client, ctx, admin, alice = env
    r = client.get("/api/site")
    assert r.status_code == 200 and r.json() == {"name": "ironlab", "tailscale": False}

    ctx.s.site_name = "비전연구실 GPU"
    assert client.get("/api/site").json()["name"] == "비전연구실 GPU"
    assert client.get("/api/me", headers=alice).json()["site_name"] == "비전연구실 GPU"


def test_console_url_defaults_to_https_public_host(env):
    client, ctx, admin, alice = env
    me = client.get("/api/me", headers=alice).json()
    assert me["public_url"] == "https://gpubox.test"
    assert client.get("/api/llm", headers=alice).json()["endpoints"]["external"] == "https://gpubox.test/v1"


def test_console_url_for_lan_install(env):
    """LAN 설치는 HTTPS 없이 IP:포트로 연다. 안내하는 주소도 그대로여야 한다."""
    client, ctx, admin, alice = env
    ctx.s.public_host, ctx.s.public_url = "192.168.0.10", "http://192.168.0.10:8700/"
    me = client.get("/api/me", headers=alice).json()
    assert me["public_url"] == "http://192.168.0.10:8700"
    assert client.get("/api/llm", headers=alice).json()["endpoints"]["external"] == "http://192.168.0.10:8700/v1"
    assert client.get("/api/workspace", headers=alice).json()["ssh_command"].endswith("dev@192.168.0.10")


def test_console_url_empty_without_host(tmp_path):
    s = load_settings(mock=True, data_dir=tmp_path, public_host="", public_url="")
    assert s.console_url == ""


def test_gpu_index_picks_the_nvml_device(monkeypatch, tmp_path):
    opened = []

    class FakeNvml(gpu_mod.GpuMonitor):
        def __init__(self, index=0):
            opened.append(index)

        @property
        def total_gb(self):
            return 48.0

        def snapshot(self):
            return {"available": True, "processes": [], "total_mb": 48 * 1024, "used_mb": 0}

    monkeypatch.setattr(gpu_mod, "NvmlGpu", FakeNvml)
    s = load_settings(mock=False, data_dir=tmp_path, gpu_index=2)
    g = gpu_mod.make_gpu(s, runtime=None)
    assert opened == [2] and g.total_gb == 48.0


def test_gpu_index_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("IRONLAB_GPU_INDEX", "1")
    monkeypatch.setenv("IRONLAB_SITE_NAME", "우리 연구실")
    s = load_settings(data_dir=tmp_path)
    assert s.gpu_index == 1 and s.site_name == "우리 연구실"


def test_vram_alert_follows_gpu_size(env):
    """vram_alert_gb 가 0이면 GPU 용량의 92% 에서 알린다 (48GB 카드에서 22GB 로 늘 울리지 않게)."""
    client, ctx, admin, alice = env
    ctx.s.vram_alert_gb = 0
    gpu = {"available": True, "used_mb": 44 * 1024, "total_mb": 48 * 1024, "processes": [], "temp": 50}
    assert not any("VRAM" in a for a in ctx.monitor._alerts(gpu, {}, 0))
    gpu["used_mb"] = 45 * 1024
    assert any("VRAM" in a for a in ctx.monitor._alerts(gpu, {}, 0))
    ctx.s.vram_alert_gb = 30                     # 직접 정한 값은 그대로 쓴다
    gpu["used_mb"] = 31 * 1024
    assert any("VRAM" in a for a in ctx.monitor._alerts(gpu, {}, 0))


def test_reset_token_from_server(settings, monkeypatch, capsys):
    """관리자 토큰을 잃어버려도 서버에서 다시 받을 수 있다."""
    from ironlab import server, users as um
    from ironlab.db import DB

    monkeypatch.setattr(server, "load_settings", lambda: settings)
    db = DB(settings.db_path)
    _, old = um.create_user(db, settings, "boss", role="admin")
    server.main(["reset-token", "boss"])
    new = capsys.readouterr().out.strip().rsplit(" ", 1)[-1]
    assert new.startswith("il_") and new != old
    assert db.one("SELECT name FROM users WHERE token_hash=?", (um.hash_token(new),))["name"] == "boss"
    assert db.one("SELECT 1 FROM users WHERE token_hash=?", (um.hash_token(old),)) is None
