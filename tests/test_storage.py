"""대시보드의 저장공간 점유: 사용자 홈·스크래치, 공용 모델, 그 밖을 디스크마다 잰다."""


def test_the_overview_says_who_and_what_fills_the_disk(env):
    client, ctx, admin, alice = env
    s = ctx.s
    (s.users_root / "alice" / "big.bin").write_bytes(b"x" * 3000)
    (s.scratch_root / "alice").mkdir(parents=True, exist_ok=True)
    (s.scratch_root / "alice" / "tmp.bin").write_bytes(b"y" * 500)
    blobs = s.shared_root / "models" / "hub" / "models--Qwen--Qwen-Image-2.1" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "a").write_bytes(b"z" * 7000)
    snap = s.shared_root / "models" / "hub" / "models--Qwen--Qwen-Image-2.1" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").symlink_to(blobs / "a")             # 링크는 두 번 세지 않는다
    (s.shared_root / "models" / "hub" / "datasets--org--set").mkdir()
    (s.shared_root / "models" / "hub" / ".locks").mkdir()            # 저장소가 아닌 폴더는 목록에 넣지 않는다
    llm = s.shared_root / "models" / "llm" / "qwen3.8-27b"
    llm.mkdir(parents=True)
    (llm / "m.gguf").write_bytes(b"w" * 2000)

    assert client.get("/api/overview", headers=alice).json().get("storage") is None     # 아직 재지 않았다
    ctx.monitor._scan_disks()
    assert ctx.monitor.storage is not None
    ctx.monitor.collect()                                           # 화면이 읽는 개요는 다음 수집 때 새로 만들어진다
    st = client.get("/api/overview", headers=alice).json()["storage"]

    assert st["scanned_at"] > 0 and len(st["disks"]) >= 1
    disk = st["disks"][0]
    assert disk["total"] >= disk["used"] > 0 and disk["free"] > 0 and "사용자 홈" in disk["holds"]
    parts = {(p["kind"], p["user"]): p["bytes"] for d in st["disks"] for p in d["parts"]}
    assert parts[("home", "alice")] == 3000 and parts[("scratch", "alice")] == 500
    # 모델 7000 + LLM 2000. 링크는 가리키는 파일을 다시 세지 않고, 링크 자체의 크기(경로 길이)만 더해진다
    assert 9000 <= parts[("shared", None)] < 9000 + 1000
    assert all(d["other"] >= 0 for d in st["disks"])
    assert [(m["name"], m["kind"]) for m in st["models"]] == [("Qwen/Qwen-Image-2.1", "hub"), ("qwen3.8-27b", "llm"), ("org/set", "hub")]
    assert 7000 <= st["models"][0]["bytes"] < 8000 and st["models"][1]["bytes"] == 2000 and st["models"][2]["bytes"] == 0
    assert 9000 <= st["models_total"] < 10000 and st["models_count"] == 3
    alice_row = next(u for u in st["users"] if u["name"] == "alice")
    assert alice_row == {"name": "alice", "home": 3000, "quota": 200 * 2**30, "scratch": 500}
    assert {u["name"] for u in st["users"]} == {"boss", "alice"}


def test_a_failed_scan_keeps_the_dashboard_running(env, monkeypatch):
    client, ctx, admin, alice = env

    def broken(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(ctx.monitor, "_storage", broken)
    ctx.monitor._scan_disks()                                       # 오류를 삼키고 알림만 남긴다
    assert client.get("/api/overview", headers=alice).status_code == 200
