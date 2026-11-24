"""생성 화면의 서버 쪽 (3D, 이미지, 음악): 올린 것을 받아 GPU 작업으로 큐에 넣고, 상태와 결과 파일을 묶어 보여 준다."""

import io
import json
import re
import sqlite3

import pytest

from ironlab import gen
from ironlab.db import DB

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
KEY = r"\d{8}-\d{6}-[0-9a-f]{6}"
DEFAULT_3D = ("--ss-steps 12 --ss-guidance 7.5 --ss-rescale 0.7 --ss-rescale-t 5 --shape-steps 12 --shape-guidance 7.5"
              " --shape-rescale 0.5 --shape-rescale-t 3 --max-tokens 49152 --tex-steps 12 --tex-guidance 1"
              " --tex-rescale 0 --tex-rescale-t 3 --faces 1000000 --texture-size 4096 --remesh")
DEFAULT_MUSIC = ("--plan-temperature 0.7 --plan-top-p 0.9 --plan-top-k 30 --plan-repeat 1.005 --plan-max-tokens 4096"
                 " --song-temperature 1 --song-top-p 0.95 --song-top-k 100 --song-repeat 1.2 --song-max-tokens 9000"
                 " --audio-steps 32")


def install_script(ctx, kind="3d", name="alice"):
    """사용자 홈에 실행 스크립트를 둔다 (이 파일이 있어야 그 화면이 켜진다)."""
    k = gen.KINDS[kind]
    d = ctx.s.users_root / name / k.folder(ctx.s)
    d.mkdir(parents=True, exist_ok=True)
    (d / k.script(ctx.s)).write_text("#!/bin/bash\n")
    return d


def post(client, who, kind, *, files=(), texts=None, params=None, thumb=None, roles=None):
    parts = [("files", (n, io.BytesIO(data), "image/png")) for n, data in files]
    if thumb is not None:
        parts.append(("thumb", ("thumb.jpg", io.BytesIO(thumb), "image/jpeg")))
    data = {"texts": json.dumps(texts or {}), "params": json.dumps(params or {})}
    if roles is not None:
        data["roles"] = json.dumps(roles)
    if parts:
        return client.post(f"/api/gen/{kind}", files=parts, data=data, headers=who)
    return client.post(f"/api/gen/{kind}", data=data, headers=who)


def post3d(client, who, name="cat.png", data=PNG, thumb=None, **params):
    return post(client, who, "3d", files=[(name, data)], params=params, thumb=thumb)


def post_image(client, who, prompt="비 오는 밤의 네온 간판", refs=(), negative=None, **params):
    texts = {"prompt": prompt} | ({"negative": negative} if negative is not None else {})
    return post(client, who, "image", files=[(n, PNG) for n in refs], texts=texts, params=params)


def post_music(client, who, style="K-pop ballad, piano", lyrics="[Verse]\n창밖에 비가 내리는 밤", abc=None, **params):
    texts = {"style": style, "lyrics": lyrics} | ({"abc": abc} if abc is not None else {})
    return post(client, who, "music", texts=texts, params=params)


def runs(client, who, kind="3d"):
    return client.get(f"/api/gen/{kind}", headers=who).json()["runs"]


def finish(ctx, run):
    """모의 실행 환경에서 그 작업을 끝낸다."""
    ctx.monitor.collect()
    ctx.rt.containers[ctx.sched.get(run["job_id"])["container_id"]]["_ends_at"] = 0
    ctx.monitor.collect()


def key_of(run):
    return re.search(KEY, run["output"]).group(0)


# ── 화면이 읽는 정의 ──

def test_page_is_off_until_the_script_is_in_the_home(env):
    client, ctx, admin, alice = env
    s = client.get("/api/gen/3d", headers=alice).json()
    assert s["ready"] is False and "pixal3d-run/pixal3d.sh" in s["hint"] and s["runs"] == []
    r = post3d(client, alice)
    assert r.status_code == 400 and "pixal3d.sh" in r.json()["detail"]
    install_script(ctx)
    s = client.get("/api/gen/3d", headers=alice).json()
    assert s["ready"] is True and s["hint"] is None and s["vram_gb"] == 18 and s["title"] == "3D 생성"
    # 종류마다 따로 켜진다
    assert client.get("/api/gen/image", headers=alice).json()["ready"] is False
    assert client.get("/api/gen/music", headers=alice).json()["ready"] is False
    assert client.get("/api/gen/video", headers=alice).json()["ready"] is False
    assert client.get("/api/gen/hologram", headers=alice).status_code == 400


def test_each_kind_describes_what_it_takes_so_the_page_can_draw_the_form(env):
    client, ctx, admin, alice = env
    three = client.get("/api/gen/3d", headers=alice).json()
    assert three["texts"] == [] and (three["upload"]["min"], three["upload"]["max"]) == (1, 6) and three["client_thumb"] is True
    assert [r[0] for r in three["upload"]["roles"]] == ["front", "right", "back", "left", "top", "bottom"]
    assert three["upload"]["first_role"] == "front" and all(r[1] and r[2] for r in three["upload"]["roles"])
    by_key = {p["key"]: p for p in three["params"]}
    assert by_key["seed"]["group"] == "" and by_key["seed"]["default"] == 42          # 기본 양식에 늘 보이는 값
    assert by_key["fov_deg"]["group"].startswith("1.") and by_key["ss_steps"]["group"].startswith("2.")
    assert by_key["faces"]["group"].startswith("5.")
    assert by_key["fov_deg"]["default"] is None                                        # 비워 두면 모델(사진)에서 정한다
    assert all(p["help"] and p["label"] for p in three["params"])                      # 값마다 도움말이 있다
    image = client.get("/api/gen/image", headers=alice).json()
    assert [t["key"] for t in image["texts"]] == ["prompt", "negative"] and image["upload"]["max"] == 4
    assert [c[0] for c in {p["key"]: p for p in image["params"]}["size"]["choices"]][:2] == ["2048x2048", "1024x1024"]
    music = client.get("/api/gen/music", headers=alice).json()
    assert [t["key"] for t in music["texts"]] == ["style", "lyrics", "abc"] and music["upload"] is None
    assert [t["mono"] for t in music["texts"]] == [False, False, True]             # 악보는 고정폭 글자로 그린다
    assert music["vram_gb"] == 16 and [t["required"] for t in music["texts"]] == [True, True, False]


# ── 3D ──

def test_3d_submit_saves_the_photo_and_queues_one_job(env):
    client, ctx, admin, alice = env
    d = install_script(ctx)
    r = post3d(client, alice, name="내 고양이 (1).png", seed=7, thumb=b"small-jpeg")
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    assert run["status"] == "queued" and run["params"]["seed"] == 7 and run["title"] == "내 고양이 (1).png"
    job = ctx.sched.get(run["job_id"])
    assert job["workdir"] == "pixal3d-run" and job["vram_gb"] == 18 and job["user"] == "alice" and job["name"] == "3D 생성"
    assert job["command"] == f"./pixal3d.sh --image inputs/{key}-1.png --output outputs/{key}.glb --seed 7 {DEFAULT_3D}"
    assert (d / "inputs" / f"{key}-1.png").read_bytes() == PNG
    assert run["thumb"] == f"pixal3d-run/inputs/{key}.thumb.jpg" and run["thumb_size"] == 10
    assert run["output"] == f"pixal3d-run/outputs/{key}.glb" and run["output_size"] is None
    assert (d / "outputs").is_dir()
    assert [x["id"] for x in runs(client, alice)] == [run["id"]]
    assert any(e["kind"] == "gen.3d.submit" for e in ctx.db.recent_events(10))


def test_3d_settings_reach_the_command_and_wrong_ones_are_refused(env):
    client, ctx, admin, alice = env
    d = install_script(ctx)
    run = post3d(client, alice, ss_steps=20, tex_guidance=2.5, fov_deg=20, faces=50000, texture_size="2048", remesh=False).json()
    cmd = ctx.sched.get(run["job_id"])["command"]
    for part in ("--ss-steps 20", "--tex-guidance 2.5", "--fov-deg 20", "--faces 50000", "--texture-size 2048", "--no-remesh"):
        assert f" {part}" in cmd, (part, cmd)
    assert " --remesh" not in cmd and run["params"]["remesh"] is False and run["params"]["shape_steps"] == 12
    for bad, expect in ((dict(ss_steps=0), "단계 수"), (dict(ss_steps=1.5), "정수"), (dict(texture_size=4096), "텍스처 크기"),
                        (dict(remesh="no"), "참 또는 거짓"), (dict(faces=10), "10,000 이상 2,000,000 이하"), (dict(nope=1), "모르는 설정"),
                        (dict(ss_rescale=1.5), "0 이상 1 이하여야 합니다 (받은 값 1.5)"),
                        (dict(seed=-1), "시드"), (dict(seed=True), "숫자")):
        r = post3d(client, alice, **bad)
        assert r.status_code == 400 and expect in r.json()["detail"], (bad, r.text)
    assert len(list((d / "inputs").iterdir())) == 1          # 거절된 요청은 사진을 남기지 않는다


def test_3d_takes_several_photos_with_the_direction_of_each(env):
    """여러 방향에서 찍은 사진은 장마다 방향을 받아 `--image … --views front,right,…` 로 넘긴다."""
    client, ctx, admin, alice = env
    d = install_script(ctx)
    photos = [("앞.jpg", PNG), ("옆.png", PNG), ("위.webp", PNG)]
    r = post(client, alice, "3d", files=photos, roles=["front", "right", "top"], params={"seed": 5, "faces": 200000},
             thumb=b"small-jpeg")
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["command"].startswith(f"./pixal3d.sh --image inputs/{key}-1.jpg --image inputs/{key}-2.png --image inputs/{key}-3.webp"
                                     f" --views front,right,top --output outputs/{key}.glb --save-views outputs/{key}.views --seed 5 ")
    assert " --faces 200000 " in job["command"] and job["vram_gb"] == 20 and job["name"] == "3D 생성"     # 여러 장은 따로 예약한다
    assert run["params"]["views"] == "front,right,top" and "refs" not in run["params"] and run["title"] == "앞.jpg"
    assert [p.rsplit("/", 1)[-1] for p in run["inputs"]] == [f"{key}-1.jpg", f"{key}-2.png", f"{key}-3.webp"]
    assert all((d / "inputs" / f"{key}-{i}{e}").read_bytes() == PNG for i, e in ((1, ".jpg"), (2, ".png"), (3, ".webp")))
    # 작업이 모델이 받은 사진을 남기면 목록이 그 경로를 알려 주고, 지우면 폴더째 지운다
    seen = d / "outputs" / f"{key}.views"
    seen.mkdir()
    for n in ("view00_front.png", "view01_right.png", "transforms.json"):
        (seen / n).write_bytes(b"x")
    finish(ctx, run)
    assert runs(client, alice)[0]["seen"] == [f"pixal3d-run/outputs/{key}.views/view00_front.png",
                                              f"pixal3d-run/outputs/{key}.views/view01_right.png"]
    assert client.delete(f"/api/gen/3d/{run['id']}", headers=alice).status_code == 200
    assert not seen.exists()

    # 한 장이면 방향을 넘기지 않는다 (한 장의 길로 돈다). 방향을 'front' 로 주어도 같다
    for roles in (None, ["front"]):
        one = post(client, alice, "3d", files=[("a.png", PNG)], roles=roles).json()
        assert "--views" not in ctx.sched.get(one["job_id"])["command"] and "views" not in one["params"]
        assert "--save-views" not in ctx.sched.get(one["job_id"])["command"] and one["seen"] == []
        assert ctx.sched.get(one["job_id"])["vram_gb"] == 18
    s = client.get("/api/gen/3d", headers=alice).json()
    assert (s["vram_gb"], s["vram_multi_gb"]) == (18, 20)

    cases = [
        (["right", "front"], "첫 사진은 '앞' 방향이어야 합니다 (받은 값 '오른쪽')"),
        (["front", "front"], "같은 방향이 두 번 나옵니다 (앞)"),
        (["front"], "사진은 2장인데 방향은 1개입니다"),
        (["front", "upside"], "모르는 방향입니다 (받은 값 upside)"),
        ("front,right", "글자 목록이어야"),
        (["front", 3], "글자 목록이어야"),
    ]
    before = len(list((d / "inputs").iterdir()))
    for roles, expect in cases:
        r = post(client, alice, "3d", files=[("a.png", PNG), ("b.png", PNG)], roles=roles)
        assert r.status_code == 400 and expect in r.json()["detail"], (roles, r.text)
    r = post(client, alice, "3d", files=[(f"{i}.png", PNG) for i in range(7)], roles=["front"] * 7)
    assert r.status_code == 400 and "7장" in r.json()["detail"]
    assert len(list((d / "inputs").iterdir())) == before        # 거절된 요청은 사진을 남기지 않는다
    # 방향을 받지 않는 종류에 방향을 주면 거절한다
    install_script(ctx, "image")
    r = post(client, alice, "image", texts={"prompt": "x"}, files=[("a.png", PNG)], roles=["front"])
    assert r.status_code == 400 and "방향을 받지 않습니다" in r.json()["detail"]
    r = client.post("/api/gen/3d", data={"roles": "[front"}, files=[("files", ("a.png", io.BytesIO(PNG), "image/png"))], headers=alice)
    assert r.status_code == 400 and "방향(roles)" in r.json()["detail"]


def test_3d_run_shows_the_stage_and_then_the_result_file(env):
    client, ctx, admin, alice = env
    install_script(ctx)
    run = post3d(client, alice).json()
    ctx.monitor.collect()
    cid = ctx.sched.get(run["job_id"])["container_id"]
    ctx.rt.containers[cid]["_log"] += ["[Inference] Running 3D generation pipeline...",
                                       "Sampling HR shape SLat (proj, 1024):  58%|#####     | 7/12 [00:12<00:08,  1.70s/it]"]
    cur = runs(client, alice)[0]
    assert cur["status"] == "running"
    assert cur["progress"] == {"stage": "형상 다듬기", "step": 4, "steps": 6, "percent": 58}

    out = ctx.s.users_root / "alice" / run["output"]
    out.write_bytes(b"glTF" + b"0" * 96)                     # 스크립트가 결과를 썼다
    finish(ctx, run)
    cur = runs(client, alice)[0]
    assert cur["status"] == "succeeded" and cur["output_size"] == 100 and cur["progress"] is None
    assert cur["started_at"] and cur["finished_at"]


def test_a_finished_job_without_a_file_is_reported_as_such(env):
    client, ctx, admin, alice = env
    install_script(ctx)
    run = post3d(client, alice).json()
    finish(ctx, run)
    cur = runs(client, alice)[0]
    assert cur["status"] == "succeeded" and cur["output_size"] is None   # 화면은 이것을 "결과 없음"으로 보여 준다


def test_bad_3d_uploads_are_refused_and_leave_nothing_behind(env):
    client, ctx, admin, alice = env
    d = install_script(ctx)
    r = post3d(client, alice, name="notes.txt")
    assert r.status_code == 400 and "사진 파일이 아닙니다" in r.json()["detail"] and "notes.txt" in r.json()["detail"]
    r = post3d(client, alice, data=b"")
    assert r.status_code == 400 and "빈 파일" in r.json()["detail"]
    r = post(client, alice, "3d")
    assert r.status_code == 400 and "1~6장" in r.json()["detail"] and "0장" in r.json()["detail"]
    r = post(client, alice, "3d", files=[("a.png", PNG), ("b.png", PNG)])
    assert r.status_code == 400 and "방향은 0개" in r.json()["detail"]
    r = post(client, alice, "3d", files=[("a.png", PNG)], texts={"prompt": "x"})
    assert r.status_code == 400 and "모르는 글 칸" in r.json()["detail"]
    r = client.post("/api/gen/3d", data={"params": "{not json"}, headers=alice)
    assert r.status_code == 400 and "JSON" in r.json()["detail"]
    assert not (d / "inputs").exists() or not list((d / "inputs").iterdir())
    assert runs(client, alice) == [] and client.get("/api/jobs", headers=alice).json() == []


def test_a_refused_job_does_not_leave_the_photo(env):
    """큐가 작업을 받지 않으면 (여기서는 사용자 VRAM 한도) 올린 사진을 남기지 않는다."""
    client, ctx, admin, alice = env
    d = install_script(ctx)
    assert client.patch("/api/admin/users/alice", json={"max_vram_gb": 12}, headers=admin).status_code == 200
    r = post3d(client, alice, thumb=b"small-jpeg")
    assert r.status_code == 400 and "한도" in r.json()["detail"]
    assert list((d / "inputs").iterdir()) == [] and runs(client, alice) == []


def test_delete_waits_for_the_job_and_then_removes_the_files(env):
    client, ctx, admin, alice = env
    install_script(ctx)
    run = post3d(client, alice, thumb=b"small-jpeg").json()
    home = ctx.s.users_root / "alice"
    r = client.delete(f"/api/gen/3d/{run['id']}", headers=alice)
    assert r.status_code == 400 and "끝나지 않은" in r.json()["detail"]
    assert client.post(f"/api/jobs/{run['job_id']}/cancel", headers=alice).status_code == 200
    assert runs(client, alice)[0]["status"] == "cancelled"
    assert client.delete(f"/api/gen/image/{run['id']}", headers=alice).status_code == 400   # 종류가 다르면 못 지운다
    assert client.delete(f"/api/gen/3d/{run['id']}", headers=alice).status_code == 200
    assert not (home / run["inputs"][0]).exists() and not (home / run["thumb"]).exists()
    assert runs(client, alice) == []
    assert ctx.sched.get(run["job_id"]) is not None          # 작업 기록과 로그는 "GPU 작업"에 남는다


def test_other_users_see_the_job_but_not_what_was_made(env):
    """작업 목록은 모두에게 보인다 (GPU 를 누가 쓰는지 알아야 하므로). 파일 이름과 글은 거기에 나오지 않아야 한다."""
    client, ctx, admin, alice = env
    for kind in ("3d", "image", "music"):
        install_script(ctx, kind)
    made = [post3d(client, alice, name="secret-project-draft.png").json(),
            post_image(client, alice, prompt="secret launch poster for project Falcon").json(),
            post_music(client, alice, style="secret anthem", lyrics="[Verse]\nFalcon rises tonight").json()]
    r = client.post("/api/admin/users", json={"name": "bob", "cpus": 4, "mem_gb": 8}, headers=admin)
    bob = {"Authorization": f"Bearer {r.json()['token']}"}
    seen = {j["id"]: j for j in client.get("/api/jobs?scope=all", headers=bob).json()}
    for run, name in zip(made, ("3D 생성", "이미지 생성", "음악 생성"), strict=True):
        j = seen[run["job_id"]]
        assert j["user"] == "alice" and j["name"] == name and j["command"] is None and j["workdir"] is None
        assert "secret" not in str(j).lower() and "falcon" not in str(j).lower()
        assert client.get(f"/api/jobs/{run['job_id']}/logs", headers=bob).status_code == 403
        # 관리자도 작업 명령에서 파일 이름이나 글을 볼 수 없다 (날짜와 무작위 글자뿐이다)
        command = client.get(f"/api/jobs/{run['job_id']}", headers=admin).json()["command"]
        assert "secret" not in command.lower() and "falcon" not in command.lower()
        assert re.fullmatch(r"[A-Za-z0-9_./ -]+", command), command
    for kind in ("3d", "image", "music"):
        assert runs(client, bob, kind) == [] and runs(client, admin, kind) == []
    assert client.delete(f"/api/gen/3d/{made[0]['id']}", headers=bob).status_code == 400
    assert client.get(f"/api/files/download?area=home&path={made[0]['inputs'][0]}", headers=bob).status_code == 400
    assert client.get("/api/gen/3d").status_code == 401


# ── 이미지 ──

def test_image_submit_writes_the_texts_to_files_and_queues_one_job(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "image")
    prompt = '간판에 "IRONLAB" 이라고 적힌 가게; rm -rf ~ $(whoami)'
    r = post_image(client, alice, prompt=prompt, negative="흐릿함, `backtick`", refs=("ref one.PNG",), model="base",
                   size="1024x1024", seed=7, transparent=True, steps=30, sigmas=" 1, 0.90 ,0.5,", true_cfg=3.5,
                   kv_cache=False, tile="512")
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    assert run["texts"] == {"prompt": prompt, "negative": "흐릿함, `backtick`"} and run["title"].startswith("간판에")
    assert run["params"] == {"size": "1024x1024", "model": "base", "seed": 7, "transparent": True, "steps": 30,
                             "sigmas": "1,0.9,0.5", "true_cfg": 3.5, "kv_cache": False, "tile": "512", "view": None, "elevation": None, "close_up": False, "lora_scale": None, "refs": 1}
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "이미지 생성" and job["workdir"] == "qwen-image-run" and job["vram_gb"] == 21
    assert job["command"] == (f"./qwen-image.sh --prompt-file inputs/{key}.prompt.txt --negative-file inputs/{key}.negative.txt"
                              f" --image inputs/{key}-1.png --output outputs/{key}.png --thumb outputs/{key}.thumb.jpg"
                              f" --size 1024x1024 --model base --seed 7 --transparent --steps 30 --sigmas 1,0.9,0.5"
                              f" --true-cfg 3.5 --no-kv-cache --tile 512")
    assert (d / "inputs" / f"{key}.prompt.txt").read_text(encoding="utf-8") == prompt
    assert (d / "inputs" / f"{key}-1.png").read_bytes() == PNG
    assert run["thumb"] == f"qwen-image-run/outputs/{key}.thumb.jpg" and run["thumb_size"] is None
    assert len(run["inputs"]) == 3


def test_image_defaults_leave_model_defaults_alone(env):
    client, ctx, admin, alice = env
    install_script(ctx, "image")
    run = post_image(client, alice).json()
    key = key_of(run)
    assert run["params"] == {"size": "2048x2048", "model": "turbo", "seed": 42, "transparent": False, "steps": None,
                             "sigmas": None, "true_cfg": None, "kv_cache": True, "tile": "1024", "view": None, "elevation": None, "close_up": False, "lora_scale": None, "refs": 0}
    # 비워 둔 값(단계 수, 시간표, 가이던스)과 적지 않은 글(피할 내용)은 명령에 넘기지 않는다
    assert ctx.sched.get(run["job_id"])["command"] == (
        f"./qwen-image.sh --prompt-file inputs/{key}.prompt.txt --output outputs/{key}.png --thumb outputs/{key}.thumb.jpg"
        f" --size 2048x2048 --model turbo --seed 42 --kv-cache --tile 1024")
    home = ctx.s.users_root / "alice"
    (home / run["output"]).write_bytes(b"x" * 500)           # 작업이 그림과 작은 미리보기를 썼다
    (home / run["thumb"]).write_bytes(b"y" * 50)
    finish(ctx, run)
    cur = runs(client, alice, "image")[0]
    assert cur["status"] == "succeeded" and cur["output_size"] == 500 and cur["thumb_size"] == 50
    assert client.delete(f"/api/gen/image/{run['id']}", headers=alice).status_code == 200
    assert not (home / run["output"]).exists() and not (home / run["thumb"]).exists()
    assert not any((home / "qwen-image-run" / "inputs").iterdir())


def test_bad_image_requests_are_refused_and_leave_nothing_behind(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "image")
    cases = [
        (dict(prompt="   "), "그릴 내용"),
        (dict(prompt="x" * 4001), "4001자"),
        (dict(negative="x" * 2001), "피할 내용"),
        (dict(size="999x999"), "999x999"),
        (dict(model="xl"), "'xl'"),
        (dict(seed="7"), "시드"),
        (dict(transparent="yes"), "투명 배경"),
        (dict(true_cfg=0.5), "가이던스"),
        (dict(sigmas="0.5,0.9"), "큰 것부터"),
        (dict(sigmas="1,abc"), "쉼표로 이은 수"),
        (dict(sigmas="1; rm -rf ~"), "쉼표로 이은 수"),
        (dict(sigmas=[1, 0.5]), "쉼표로 이은 수"),
        (dict(sigmas="1.5,0.5"), "1 이하"),
        (dict(refs=("a.png", "b.png", "c.png", "d.png", "e.png")), "5장"),
        (dict(refs=("notes.txt",)), "notes.txt"),
    ]
    for kwargs, expect in cases:
        r = post_image(client, alice, **kwargs)
        assert r.status_code == 400 and expect in r.json()["detail"], (kwargs, r.text)
    assert not (d / "inputs").exists() or not list((d / "inputs").iterdir())
    assert runs(client, alice, "image") == []


def test_image_angle_settings_need_a_reference_picture(env):
    """각도 바꾸기(각도 LoRA)는 참고 그림의 물체를 다른 방향에서 그린다. 참고 그림 없이 고르면 받지 않는다."""
    client, ctx, admin, alice = env
    install_script(ctx, "image")
    r = post_image(client, alice, prompt="-", view="back", elevation="high", close_up=True)
    assert r.status_code == 400 and "참고 그림이 있어야" in r.json()["detail"]
    run = post_image(client, alice, prompt="-", refs=["house.png"], view="back", elevation="high", close_up=True, lora_scale=0.9).json()
    assert ctx.sched.get(run["job_id"])["command"].endswith("--view back --elevation high --close-up --lora-scale 0.9")
    plain = post_image(client, alice).json()
    assert "--view" not in ctx.sched.get(plain["job_id"])["command"]
    r = post_image(client, alice, prompt="x", model="uncensored")     # 무검열판은 없앴다 (2026-11-15)
    assert r.status_code == 400 and "'uncensored'" in r.json()["detail"]


def test_image_progress_reads_the_drawing_bar_not_the_loading_bars():
    p = gen.progress_image
    assert p("") is None and p("hello\n") is None
    start = "[qwen-image] 시작: 모델 Qwen/Qwen-Image-2.1-Turbo, 크기 2048x2048\n"
    loading = "Loading weights: 100%|##########| 750/750 [00:00<00:00]\nLoading pipeline components...: 100%|###| 5/5\n"
    assert p(start + loading) == {"stage": "모델 올리기", "step": 1, "steps": 3, "percent": None}
    bar = "  0%|          | 0/8 [00:00<?, ?it/s]\r 38%|###8      | 3/8 [00:18<00:31,  6.25s/it]"
    assert p(start + loading + bar) == {"stage": "그리기", "step": 2, "steps": 3, "percent": 38}
    assert p(bar + "\r100%|##########| 8/8 [00:49<00:00,  6.25s/it]\n")["stage"] == "그림 파일 쓰기"
    assert p("[qwen-image] 끝: out.png (2048x2048, RGB)\n")["step"] == 3


# ── 음악 ──

def test_music_submit_queues_a_song_and_lists_the_score_when_it_appears(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "music")
    lyrics = "[Verse]\n창밖에 비가 내리는 밤\n\n[Chorus]\n돌아라 돌아라 밤새도록"
    r = post_music(client, alice, lyrics=lyrics, cot="melody", seed=3)
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "음악 생성" and job["workdir"] == "yue2-run" and job["vram_gb"] == 16
    assert job["command"] == (f"./yue2.sh --style-file inputs/{key}.style.txt --lyrics-file inputs/{key}.lyrics.txt"
                              f" --output outputs/{key}.flac --mp3 outputs/{key}.mp3 --score outputs/{key}.abc"
                              f" --cot melody --seed 3 {DEFAULT_MUSIC}")
    assert (d / "inputs" / f"{key}.lyrics.txt").read_text(encoding="utf-8") == lyrics
    assert run["title"] == "K-pop ballad, piano" and run["texts"]["lyrics"] == lyrics and "abc" in run["texts"]
    assert run["thumb"] == f"yue2-run/outputs/{key}.mp3" and run["extras"] == {}       # 들어 볼 사본과 악보는 아직 없다
    home = ctx.s.users_root / "alice"
    for tail, size in ((".flac", 900), (".mp3", 90), (".abc", 9)):
        (home / f"yue2-run/outputs/{key}{tail}").write_bytes(b"z" * size)
    finish(ctx, run)
    cur = runs(client, alice, "music")[0]
    assert cur["status"] == "succeeded" and cur["output_size"] == 900 and cur["thumb_size"] == 90
    assert cur["extras"] == {".abc": f"yue2-run/outputs/{key}.abc"}
    assert client.delete(f"/api/gen/music/{run['id']}", headers=alice).status_code == 200
    assert not any((home / "yue2-run" / "outputs").iterdir()) and not any((home / "yue2-run" / "inputs").iterdir())


def test_music_takes_a_score_and_stage_settings_and_refuses_missing_texts(env):
    client, ctx, admin, alice = env
    install_script(ctx, "music")
    run = post_music(client, alice, abc="X:1\nK:Dm\n|f4|", cfg_scale=1.2, plan_temperature=0.9, song_top_k=60,
                     song_repeat=1.1, song_max_tokens=3000, audio_steps=16).json()
    key = key_of(run)
    cmd = ctx.sched.get(run["job_id"])["command"]
    assert f" --abc-file inputs/{key}.abc.abc " in cmd
    for part in ("--plan-temperature 0.9", "--cfg-scale 1.2", "--song-top-k 60", "--song-repeat 1.1", "--song-max-tokens 3000",
                 "--audio-steps 16", "--song-temperature 1 "):
        assert f" {part}" in cmd + " ", (part, cmd)
    by_key = {p["key"]: p for p in client.get("/api/gen/music", headers=alice).json()["params"]}
    assert by_key["plan_top_k"]["group"] == "1. 악보 쓰기" and by_key["cfg_scale"]["group"] == "2. 노래 만들기"
    assert by_key["audio_steps"]["group"] == "3. 소리로 바꾸기" and by_key["cot"]["group"] == ""
    for kwargs, expect in ((dict(style=""), "분위기와 장르"), (dict(lyrics="  "), "가사"), (dict(cot="half"), "'half'"),
                           (dict(cfg_scale=9), "가이던스"), (dict(lyrics="가" * 6001), "6001자"),
                           (dict(song_max_tokens=100), "곡 길이 한도"), (dict(plan_top_p=0), "후보 확률 합")):
        r = post_music(client, alice, **kwargs)
        assert r.status_code == 400 and expect in r.json()["detail"], (kwargs, r.text)
    r = post(client, alice, "music", files=[("a.png", PNG)], texts={"style": "s", "lyrics": "l"})
    assert r.status_code == 400 and "0장" in r.json()["detail"]


def test_music_progress_reads_the_stage_names_yue2_prints():
    p = gen.progress_music
    assert p("") is None and p("hello\n") is None
    assert p("[YuE2] Starting Loading model: elapsed 0.0s\n")["stage"] == "모델 올리기"
    assert p("[YuE2] Completed Loading model\n[YuE2] Running Planning score: 300 tokens | 120.6 tokens/s\n") == {
        "stage": "악보 쓰기", "step": 2, "steps": 4, "percent": None}
    assert p("[YuE2] Completed Using provided score: elapsed 0.0s\n[YuE2] Running Generating song: 596 tokens\n")["step"] == 3
    tail = "[YuE2] Completed Generating song: 2286 tokens\n[YuE2] Running Synthesizing audio: 16/32 steps (50%) | elapsed 5.0s\n"
    assert p(tail) == {"stage": "소리로 바꾸기", "step": 4, "steps": 4, "percent": 50}
    assert p(tail + "[YuE2] Completed Decoding audio: 3/3 chunks (100%)\n")["step"] == 4


# ── 영상 ──

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 64


def post_video(client, who, prompt="A small orange cat sits on a windowsill.", frames=(), sound=None, music=None, **params):
    texts = {"prompt": prompt} | ({"sound": sound} if sound is not None else {}) | ({"music": music} if music is not None else {})
    return post(client, who, "video", files=[(n, PNG) for n in frames], texts=texts, params=params)


def test_video_submit_passes_the_three_texts_and_both_frames_in_order(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "video")
    r = post_video(client, alice, prompt="The cat (S1) says: <d>[Korean] 안녕</d>; $(rm -rf ~)", frames=("first.png", "last.JPG"),
                   sound="Rain on the window.", music="Slow piano.", model="base", size="864x480", seconds=3, seed=9,
                   steps=12, shift=8, sampler="heun", attn_budget=0.5, vae_tiling=True, crf=20)
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "영상 생성" and job["workdir"] == "minimax-h3-run" and job["vram_gb"] == 22
    # 첫 장면이 먼저, 끝 장면이 나중에 간다 (실행 스크립트는 --image 의 순서로 둘을 가른다)
    assert job["command"] == (
        f"./h3.sh --prompt-file inputs/{key}.prompt.txt --sound-file inputs/{key}.sound.txt --music-file inputs/{key}.music.txt"
        f" --image inputs/{key}-1.png --image inputs/{key}-2.jpg --output outputs/{key}.mp4 --thumb outputs/{key}.thumb.jpg"
        f" --last-frame outputs/{key}.last.png --size 864x480 --model base --seconds 3 --seed 9 --steps 12 --shift 8"
        f" --sampler heun --attn-budget 0.5 --vae-tiling --crf 20")
    assert (d / "inputs" / f"{key}.prompt.txt").read_text(encoding="utf-8").endswith("$(rm -rf ~)")
    assert run["params"]["refs"] == 2 and run["thumb"] == f"minimax-h3-run/outputs/{key}.thumb.jpg"
    assert run["title"].startswith("The cat (S1) says: <d>[Korean] 안녕</d>;")    # 글에 든 '/' 에서 제목을 자르지 않는다
    home = ctx.s.users_root / "alice"
    for tail, size in ((".mp4", 700), (".thumb.jpg", 70), (".last.png", 7)):
        (home / f"minimax-h3-run/outputs/{key}{tail}").write_bytes(b"v" * size)
    finish(ctx, run)
    cur = runs(client, alice, "video")[0]
    assert cur["status"] == "succeeded" and cur["output_size"] == 700 and cur["thumb_size"] == 70
    assert cur["extras"] == {".last.png": f"minimax-h3-run/outputs/{key}.last.png"}      # 이어 만들기에 쓰는 끝 장면
    assert client.delete(f"/api/gen/video/{run['id']}", headers=alice).status_code == 200
    assert not any((home / "minimax-h3-run" / "outputs").iterdir()) and not any((home / "minimax-h3-run" / "inputs").iterdir())


def test_video_defaults_pick_the_fast_model_and_leave_the_rest_to_it(env):
    client, ctx, admin, alice = env
    install_script(ctx, "video")
    s = client.get("/api/gen/video", headers=alice).json()
    assert [t["key"] for t in s["texts"]] == ["prompt", "sound", "music"] and [t["required"] for t in s["texts"]] == [True, False, False]
    assert s["upload"]["slots"] == ["첫 장면", "끝 장면"] and s["upload"]["audio"] is False and s["upload"]["max"] == 2
    run = post_video(client, alice, sampler="").json()
    key = key_of(run)
    assert run["params"] == {"size": "auto", "model": "flash", "seconds": 5.0, "seed": 42, "steps": None, "shift": None,
                             "sampler": None, "sigmas": None, "attn_budget": None, "vae_tiling": False,
                             "temporal_tiling": False, "crf": 18, "ref_video_role": None, "ref_audio_role": None, "refs": 0}
    assert ctx.sched.get(run["job_id"])["command"] == (
        f"./h3.sh --prompt-file inputs/{key}.prompt.txt --output outputs/{key}.mp4 --thumb outputs/{key}.thumb.jpg"
        f" --last-frame outputs/{key}.last.png --size auto --model flash --seconds 5 --seed 42 --crf 18")


def test_bad_video_requests_are_refused_and_leave_nothing_behind(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "video")
    cases = [
        (dict(prompt=" "), "장면"),
        (dict(frames=("a.png", "b.png", "c.png")), "3장"),
        (dict(frames=("voice.wav",)), "voice.wav"),
        (dict(seconds=20), "길이"),
        (dict(seconds=1), "길이"),
        (dict(sampler="ddim"), "'ddim'"),
        (dict(model="xl"), "'xl'"),
        (dict(crf=60), "MP4 화질"),
        (dict(sigmas="1,2"), "1 이하"),
    ]
    for kwargs, expect in cases:
        r = post_video(client, alice, **kwargs)
        assert r.status_code == 400 and expect in r.json()["detail"], (kwargs, r.text)
    assert not (d / "inputs").exists() or not list((d / "inputs").iterdir())
    assert runs(client, alice, "video") == []


def test_video_progress_reads_the_stage_lines_and_the_step_bar_not_the_loading_bar():
    p = gen.progress_video
    assert p("") is None and p("hello\n") is None
    start = "[h3] 시작: 글 → 영상, HyperFlow Flash, 1344×768\n[h3] 단계 1/5: 모델 올리기\n"
    loading = "  |##############            | 149/535 -   2.89GB/s\x1b[K\r  |#####################     | 220/535 -   2.97GB/s\x1b[K"
    assert p(start + loading) == {"stage": "모델 올리기", "step": 1, "steps": 5, "percent": None}
    bar = "\n[h3] 단계 3/5: 영상 만들기\n  |==>      |  1/20 -  14.26s/it - ETA 04:30\x1b[K\r  |=====>   |  7/20 -   4.54s/it - ETA 01:17\x1b[K"
    assert p(start + loading + bar) == {"stage": "영상 만들기", "step": 3, "steps": 5, "percent": 35}
    # 단계 줄이 로그 끝에서 밀려나도 막대가 보이면 만드는 중이다
    assert p("  |=====>   | 15/20 -   5.18s/it - ETA 00:27\x1b[K")["percent"] == 75
    assert p(bar + "\n[h3] 단계 4/5: 영상과 소리 풀어내기\n")["step"] == 4
    assert p("[h3] 단계 5/5: MP4 만들기\n[h3] 끝: 5.17초 영상\n") == {"stage": "MP4 만들기", "step": 5, "steps": 5, "percent": None}


def post_refs(client, who, refs, frames=(), prompt="<Picture 1> 속 고양이가 (S1) 인사한다", **params):
    data = {"texts": json.dumps({"prompt": prompt}), "params": json.dumps(params)}
    parts = [("files", (n, io.BytesIO(PNG), "image/png")) for n in frames]
    parts += [("refs", (n, io.BytesIO(b"r" * 64), "application/octet-stream")) for n in refs]
    return client.post("/api/gen/video", files=parts, data=data, headers=who)


def test_video_references_go_to_their_own_options_numbered_per_kind(env):
    """참고 자료는 종류마다 올린 차례대로 번호가 붙는다 (<Picture 1>, <Video 1>, <Audio 1>). 종류마다 다른 옵션으로 넘긴다."""
    client, ctx, admin, alice = env
    d = install_script(ctx, "video")
    s = client.get("/api/gen/video", headers=alice).json()["refs"]
    assert [(k["kind"], k["tag"], k["max"]) for k in s["kinds"]] == [("image", "Picture", 4), ("video", "Video", 2), ("audio", "Audio", 2)]
    r = post_refs(client, alice, ["voice.mp3", "cat.png", "walk.mp4", "dog.webp"], ref_audio_role="voice", ref_video_role="continue")
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    cmd = ctx.sched.get(run["job_id"])["command"]
    assert (f"--ref-image inputs/{key}-r2.png --ref-image inputs/{key}-r4.webp --ref-video inputs/{key}-r3.mp4"
            f" --ref-audio inputs/{key}-r1.mp3 --output") in cmd
    assert cmd.endswith("--ref-video-role continue --ref-audio-role voice") and " --image " not in cmd
    assert run["params"]["references"] == {"image": 2, "video": 1, "audio": 1}
    assert sorted(p.name for p in (d / "inputs").iterdir() if "-r" in p.name) == [
        f"{key}-r1.mp3", f"{key}-r2.png", f"{key}-r3.mp4", f"{key}-r4.webp"]
    # 참고 자료가 없으면 쓰임새 설정은 명령에 넣지 않는다 (기본값)
    plain = post_video(client, alice).json()
    assert "--ref-" not in ctx.sched.get(plain["job_id"])["command"]
    # 지우면 참고 자료 파일도 지운다
    finish(ctx, run)
    assert client.delete(f"/api/gen/video/{run['id']}", headers=alice).status_code == 200
    assert not [p for p in (d / "inputs").iterdir() if p.name.startswith(key)]


def test_bad_video_references_are_refused(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "video")
    for refs, frames, expect in ((["a.png"], ["first.png"], "함께 쓸 수 없습니다"),
                                 (["a.txt"], [], "참고 자료로 받지 않는 파일"),
                                 (["a.mp4", "b.mov", "c.webm"], [], "영상은 2개까지")):
        r = post_refs(client, alice, refs, frames=frames)
        assert r.status_code == 400 and expect in r.json()["detail"], (refs, r.text)
    assert not (d / "inputs").exists() or not list((d / "inputs").iterdir())
    r = client.post("/api/gen/image", files=[("refs", ("a.png", io.BytesIO(PNG), "image/png"))],
                    data={"texts": json.dumps({"prompt": "x"}), "params": "{}"}, headers=alice)
    install_script(ctx, "image")
    r = client.post("/api/gen/image", files=[("refs", ("a.png", io.BytesIO(PNG), "image/png"))],
                    data={"texts": json.dumps({"prompt": "x"}), "params": "{}"}, headers=alice)
    assert r.status_code == 400 and "참고 자료를 받지 않습니다" in r.json()["detail"]


JOIN_SCRIPT ='#!/bin/bash\nif [ "$1" = "--join" ]; then exec python3 h3_join.py "$@"; fi\n'


def made_video(client, ctx, who, after=None, prompt="A paper airplane glides over the city."):
    """영상 하나를 만든 것으로 친다 (결과 파일을 쓰고 작업을 끝낸다). after 가 있으면 그 영상의 끝 장면에서 이어 만든다."""
    data = {"texts": json.dumps({"prompt": prompt}), "params": "{}"} | ({"after": str(after)} if after is not None else {})
    r = client.post("/api/gen/video", files=[("files", ("이전 영상의 끝 장면.png", io.BytesIO(PNG), "image/png"))],
                    data=data, headers=who)
    assert r.status_code == 200, r.text
    run = r.json()
    home = ctx.s.users_root / "alice"
    for tail in (".mp4", ".thumb.jpg", ".last.png"):
        (home / f"minimax-h3-run/outputs/{key_of(run)}{tail}").write_bytes(b"v" * 100)
    finish(ctx, run)
    return run


def test_videos_made_one_after_another_are_joined_in_order_without_touching_the_clips(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "video")
    (d / "h3.sh").write_text(JOIN_SCRIPT)
    a = made_video(client, ctx, alice)
    b = made_video(client, ctx, alice, after=a["id"], prompt="It lands in a child's hands.")
    c = made_video(client, ctx, alice, after=b["id"], prompt="The child throws it again.")
    listed = {r["id"]: r for r in runs(client, alice, "video")}
    assert "after" not in listed[a["id"]]["params"] and listed[b["id"]]["params"]["after"] == a["id"]
    assert listed[c["id"]]["params"]["after"] == b["id"]

    r = client.post("/api/gen/video/join", json={"ids": [a["id"], b["id"], c["id"]]}, headers=alice)
    assert r.status_code == 200, r.text
    joined = r.json()
    key = key_of(joined)
    job = ctx.sched.get(joined["job_id"])
    assert job["name"] == "영상 이어 붙이기" and job["vram_gb"] == 1 and job["workdir"] == "minimax-h3-run"
    assert job["command"] == (f"./h3.sh --join outputs/{key_of(a)}.mp4 --join outputs/{key_of(b)}.mp4 --join outputs/{key_of(c)}.mp4"
                              f" --output outputs/{key}.mp4 --thumb outputs/{key}.thumb.jpg --last-frame outputs/{key}.last.png")
    assert joined["params"] == {"joined": [a["id"], b["id"], c["id"]]} and joined["inputs"] == []
    assert joined["title"].startswith("이어 붙인 영상 3개: A paper airplane")
    # 이어 붙인 항목을 지워도 고른 영상의 파일은 남는다
    ctx.monitor.collect()
    ctx.rt.containers[ctx.sched.get(joined["job_id"])["container_id"]]["_log"] += ["[h3-join] 단계 3/3 MP4 쓰기 40%"]
    mid = next(x for x in runs(client, alice, "video") if x["id"] == joined["id"])
    assert mid["progress"] == {"stage": "MP4 쓰기", "step": 3, "steps": 3, "percent": 40}
    (ctx.s.users_root / "alice" / f"minimax-h3-run/outputs/{key}.mp4").write_bytes(b"j" * 300)
    finish(ctx, joined)
    assert client.delete(f"/api/gen/video/{joined['id']}", headers=alice).status_code == 200
    assert all((d / "outputs" / f"{key_of(x)}.mp4").exists() for x in (a, b, c))
    assert not (d / "outputs" / f"{key}.mp4").exists()


def test_joining_refuses_what_cannot_be_joined(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "video")
    a = made_video(client, ctx, alice)
    join = lambda ids, who=alice: client.post("/api/gen/video/join", json={"ids": ids}, headers=who)  # noqa: E731
    r = join([a["id"], a["id"] + 1])
    assert r.status_code == 400 and "예전 판" in r.json()["detail"]                 # 스크립트가 --join 을 모른다
    (d / "h3.sh").write_text(JOIN_SCRIPT)
    b = made_video(client, ctx, alice)
    queued = post_video(client, alice).json()                                         # 아직 만드는 중
    for ids, expect in (([a["id"]], "2~20개"), ([a["id"], a["id"]], "두 번"), ([a["id"], 999], "#999"),
                        ([a["id"], queued["id"]], "다 만든 영상만"), (list(range(1, 23)), "2~20개")):
        r = join(ids)
        assert r.status_code == 400 and expect in r.json()["detail"], (ids, r.text)
    (d / "outputs" / f"{key_of(b)}.mp4").unlink()
    r = join([a["id"], b["id"]])
    assert r.status_code == 400 and "영상 파일이 없습니다" in r.json()["detail"]
    assert client.post("/api/gen/image/join", json={"ids": [1, 2]}, headers=alice).status_code == 404
    # 남의 영상은 고를 수 없다
    assert join([a["id"], b["id"]], admin).status_code == 400
    # 없는 앞 영상에서 이어 만든다고 하면 받지 않는다
    r = client.post("/api/gen/video", data={"texts": json.dumps({"prompt": "x"}), "params": "{}", "after": "999"}, headers=alice)
    assert r.status_code == 400 and "#999" in r.json()["detail"]
    r = client.post("/api/gen/image", data={"texts": json.dumps({"prompt": "x"}), "params": "{}", "after": str(a["id"])}, headers=alice)
    assert r.status_code == 400


def test_join_progress_reads_its_own_stage_lines():
    p = gen.progress_video
    assert p("[h3-join] 단계 1/3 영상 3개 읽기\n") == {"stage": "영상 읽기", "step": 1, "steps": 3, "percent": None}
    assert p("[h3-join] 단계 2/3 이어지는 장면 찾기\n[h3-join] 2번째 영상은 …\n")["step"] == 2
    assert p("[h3-join] 단계 3/3 MP4 쓰기 0% (이어 붙인 길이 약 13.00초)\n[h3-join] 단계 3/3 MP4 쓰기 70%\n")["percent"] == 70


# ── 음성 ──

def post_voice(client, who, script="화자1: 안녕하세요\n화자2: [laughs] 반가워요", refs=(), ref_text=None, **params):
    texts = {"script": script} | ({"ref_text": ref_text} if ref_text is not None else {})
    return post(client, who, "voice", files=[(n, WAV) for n in refs], texts=texts, params=params)


def test_voice_submit_pairs_each_reference_with_its_line(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "voice")
    s = client.get("/api/gen/voice", headers=alice).json()
    assert s["upload"]["audio"] is True and ".wav" in s["upload"]["exts"] and s["upload"]["max"] == 4
    assert s["vram_gb"] == 18 and [t["key"] for t in s["texts"]] == ["script", "ref_text"]
    r = post_voice(client, alice, refs=("me.wav", "you.MP3"), ref_text="첫 녹음에서 하는 말\n\n둘째 녹음에서 하는 말\n",
                   temperature=0.8, top_k=50, chunk_size=120, context=8192, cpu_layers=24, fast=True)
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "음성 생성" and job["workdir"] == "drama3-run" and job["vram_gb"] == 18
    assert job["command"] == (
        f"./drama3.sh --text-file inputs/{key}.script.txt --ref-text-file inputs/{key}.ref_text.txt"
        f" --ref inputs/{key}-1.wav --ref inputs/{key}-2.mp3 --output outputs/{key}.wav --mp3 outputs/{key}.mp3"
        f" --seed 42 --temperature 0.8 --top-p 0.9 --top-k 50 --max-tokens 1024 --chunk-size 120 --context 8192"
        f" --cpu-layers 24 --fast")
    assert (d / "inputs" / f"{key}-1.wav").read_bytes() == WAV and run["params"]["refs"] == 2
    assert run["thumb"] == f"drama3-run/outputs/{key}.mp3" and run["title"].startswith("화자1: 안녕하세요")


def test_voice_defaults_and_refusals(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "voice")
    run = post_voice(client, alice, script="오늘은 날씨가 맑습니다.").json()
    key = key_of(run)
    assert ctx.sched.get(run["job_id"])["command"] == (
        f"./drama3.sh --text-file inputs/{key}.script.txt --output outputs/{key}.wav --mp3 outputs/{key}.mp3"
        f" --seed 42 --temperature 1 --top-p 0.9 --max-tokens 1024 --context 4096 --cpu-layers 16")
    before = sorted(x.name for x in (d / "inputs").iterdir())
    cases = [
        (dict(script=""), "대본"),
        (dict(refs=("a.wav",)), "참고 목소리 1개에 '참고 목소리의 말'이 0줄"),
        (dict(refs=("a.wav", "b.wav"), ref_text="한 줄뿐"), "2개에 '참고 목소리의 말'이 1줄"),
        (dict(ref_text="말만 있다"), "파일이 없습니다"),
        (dict(refs=("photo.png",), ref_text="x"), "소리 파일이 아닙니다"),
        (dict(refs=("1.wav", "2.wav", "3.wav", "4.wav", "5.wav"), ref_text="a\nb\nc\nd\ne"), "5개"),
        (dict(cpu_layers=8), "CPU 에서 계산할 층"),
        (dict(temperature=0), "고르는 폭"),
        (dict(max_tokens=10), "조각 길이 한도"),
    ]
    for kwargs, expect in cases:
        r = post_voice(client, alice, **kwargs)
        assert r.status_code == 400 and expect in r.json()["detail"], (kwargs, r.text)
    assert sorted(x.name for x in (d / "inputs").iterdir()) == before          # 거절한 요청은 파일을 남기지 않는다
    assert len(runs(client, alice, "voice")) == 1


def test_voice_progress_reads_the_stage_and_piece_lines():
    p = gen.progress_voice
    assert p("") is None and p("ggml_cuda_init: found 1 CUDA devices\n") is None
    assert p("[drama3] 시작: 대본 113자\n[drama3] 단계 1/3: 모델 올리기\n") == {"stage": "모델 올리기", "step": 1, "steps": 3, "percent": None}
    tail = "[drama3] 단계 2/3: 말하기\n[drama3] 글 조각 1 읽는 중\n[drama3] 글 조각 1 끝: 210프레임 (9.8초)\n[drama3] 글 조각 2 읽는 중\n"
    assert p(tail) == {"stage": "말하기, 글 조각 2", "step": 2, "steps": 3, "percent": None}
    assert p("[drama3] 글 조각 3 읽는 중\n")["step"] == 2
    assert p(tail + "[drama3] 단계 3/3: 파일 쓰기\n")["stage"] == "파일 쓰기"



# ── 효과음 ──

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
SFX_DEFAULT = "--model fast --seed 42 --steps 4 --fade-out 20 --loop-crossfade 0.5"


def post_sfx(client, who, prompt="footsteps on dry leaves in a quiet forest", video=None, negative=None, videos=(), **params):
    texts = {"prompt": prompt} | ({"negative": negative} if negative is not None else {})
    files = [(video, MP4)] if video else [(n, MP4) for n in videos]
    return post(client, who, "sfx", files=files, texts=texts, params=params)


def test_sfx_from_text_uses_the_fast_model_and_names_every_side_file(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "sfx")
    s = client.get("/api/gen/sfx", headers=alice).json()
    assert s["title"] == "효과음 생성" and s["vram_gb"] == 7 and s["ready"] is True
    assert s["upload"]["video"] is True and s["upload"]["audio"] is False and s["upload"]["max"] == 1 and ".mov" in s["upload"]["exts"]
    assert [t["key"] for t in s["texts"]] == ["prompt", "negative"] and s["texts"][0]["required"] is False
    run = post_sfx(client, alice).json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "효과음 생성" and job["workdir"] == "woosh-run" and job["vram_gb"] == 7
    assert job["command"] == (f"./woosh.sh --prompt-file inputs/{key}.prompt.txt --output outputs/{key}.wav --mp3 outputs/{key}.mp3"
                              f" --video-out outputs/{key}.mp4 --thumb outputs/{key}.jpg {SFX_DEFAULT}")
    assert (d / "inputs" / f"{key}.prompt.txt").read_text() == "footsteps on dry leaves in a quiet forest"
    assert run["thumb"] == f"woosh-run/outputs/{key}.mp3" and run["title"].startswith("footsteps") and run["extras"] == {}
    # 영상을 넣은 작업만 소리를 붙인 영상을 쓴다. 목록은 생긴 곁 결과만 보인다
    (d / "outputs" / f"{key}.mp4").write_bytes(b"x" * 10)
    assert runs(client, alice, "sfx")[0]["extras"] == {".mp4": f"woosh-run/outputs/{key}.mp4"}


def test_sfx_for_a_video_passes_every_setting(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "sfx")
    r = post_sfx(client, alice, prompt="", video="걷는 영상.MOV", negative="music, speech", model="full", seconds=6.5, seed=7,
                 loop=True, cfg=7, steps=8, solver="dopri8", tolerance=0.0005, trim_silence=50, fade_in=10, fade_out=0, peak=-1,
                 loop_crossfade=0.25, video_start=2)
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    assert ctx.sched.get(run["job_id"])["command"] == (
        f"./woosh.sh --negative-file inputs/{key}.negative.txt --video inputs/{key}-1.mov --output outputs/{key}.wav"
        f" --mp3 outputs/{key}.mp3 --video-out outputs/{key}.mp4 --thumb outputs/{key}.jpg --model full --seconds 6.5 --seed 7"
        f" --loop --cfg 7 --steps 8 --solver dopri8 --tolerance 0.0005 --trim-silence 50 --fade-in 10 --fade-out 0 --peak -1"
        f" --loop-crossfade 0.25 --video-start 2")
    assert (d / "inputs" / f"{key}-1.mov").read_bytes() == MP4 and run["title"] == "music, speech"
    # 영상과 함께면 8초까지 된다
    assert post_sfx(client, alice, video="a.mp4", seconds=8).status_code == 200


def test_sfx_refuses_what_the_model_cannot_do(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "sfx")
    post_sfx(client, alice)
    before = sorted(x.name for x in (d / "inputs").iterdir())
    cases = [
        (dict(prompt=""), "소리 설명을 적거나 영상을 넣어"),
        (dict(prompt="나뭇잎 밟는 소리"), "'소리 설명'에 한글이 7자"),
        (dict(negative="음악"), "'피할 소리'에 한글이"),
        (dict(seconds=6), "글만으로 만드는 소리는 5초까지"),
        (dict(video="a.mp4", seconds=9), "'길이 (초)'은 0.1 이상 8 이하"),
        (dict(video="photo.png"), "영상 파일이 아닙니다"),
        (dict(videos=("a.mp4", "b.mp4")), "받은 수 2개"),
        (dict(steps=0), "'단계 수'"),
        (dict(solver="euler"), "'풀이법'"),
        (dict(peak=3), "'소리 크기 맞추기 (dBFS)'"),
    ]
    for kwargs, expect in cases:
        r = post_sfx(client, alice, **kwargs)
        assert r.status_code == 400 and expect in r.json()["detail"], (kwargs, r.text)
    assert sorted(x.name for x in (d / "inputs").iterdir()) == before          # 거절한 요청은 파일을 남기지 않는다
    assert len(runs(client, alice, "sfx")) == 1


def test_sfx_progress_reads_the_stage_step_and_call_lines():
    p = gen.progress_sfx
    assert p("") is None and p("Loading weights: 100%|...\n") is None
    assert p("[woosh] 시작: 글로 만들기\n[woosh] 단계 1/4: 모델 올리기 (Woosh-DFlow)\n") == {
        "stage": "모델 올리기", "step": 1, "steps": 4, "percent": None}
    tail = "[woosh] 단계 3/4: 소리 만들기\n[woosh] 소리 만들기 1/4\n[woosh] 소리 만들기 2/4\n"
    assert p(tail) == {"stage": "소리 만들기", "step": 3, "steps": 4, "percent": 50}
    assert p("[woosh] 단계 3/4: 소리 만들기\n[woosh] 계산 20번\n") == {"stage": "소리 만들기, 계산 20번", "step": 3, "steps": 4, "percent": None}
    assert p(tail + "[woosh] 단계 4/4: 다듬어 저장하기\n")["stage"] == "다듬어 저장하기"

# ── 소리 다듬기 ──

POLISH_DEFAULT = ("--source song --separator htdemucs --shifts 1 --deess-band auto --warmth-style tape --warmth-band lowmid"
                  " --vocal-level 0 --reverb-seconds 1.6 --predelay 30 --delay-note 1/8")
FLAC = b"fLaC" + b"0" * 64


def post_polish(client, who, name="노래.flac", data=FLAC, score=None, files=None, **params):
    texts = {"score": score} if score is not None else {}
    return post(client, who, "polish", files=[(name, data)] if files is None else files, texts=texts, params=params)


def test_polish_takes_a_sound_and_its_score_and_names_every_side_file(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "polish")
    s = client.get("/api/gen/polish", headers=alice).json()
    assert s["title"] == "소리 다듬기" and s["vram_gb"] == 4 and s["ready"] is True
    up = s["upload"]
    assert up["media"] is True and up["audio"] is False and up["video"] is False and (up["min"], up["max"]) == (1, 1)
    assert {".flac", ".mp3", ".wav", ".mp4", ".mov"} <= set(up["exts"])
    assert [(t["key"], t["required"], t["mono"]) for t in s["texts"]] == [("score", False, True)]
    by_key = {p["key"]: p for p in s["params"]}
    assert [k for k, p in by_key.items() if not p["group"]] == ["source", "loudness"]     # 기본 양식에는 둘만 보인다
    assert by_key["deess"]["default"] is None and "노래 6 dB" in by_key["deess"]["help"]   # 비우면 넣은 것에 맞춘 값
    assert s["vram_by"] == {"param": "source", "gb": {"song": 4, "voice": 1, "sfx": 1, "mix": 1}}    # 화면이 고른 값에 맞춰 안내한다
    assert client.get("/api/gen/music", headers=alice).json()["vram_by"] is None
    run = post_polish(client, alice, score="X:1\nQ:1/4=92\nK:C").json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "소리 다듬기" and job["workdir"] == "polish-run" and job["vram_gb"] == 4
    assert job["command"] == (f"./polish.sh --score-file inputs/{key}.score.abc --input inputs/{key}-1.flac --output outputs/{key}.flac"
                              f" --mp3 outputs/{key}.mp3 --video-out outputs/{key}.mp4 --vocals-out outputs/{key}.vocals.flac"
                              f" --backing-out outputs/{key}.backing.flac --report outputs/{key}.report.json {POLISH_DEFAULT}")
    assert (d / "inputs" / f"{key}-1.flac").read_bytes() == FLAC and (d / "inputs" / f"{key}.score.abc").read_text().startswith("X:1")
    assert run["thumb"] == f"polish-run/outputs/{key}.mp3" and run["extras"] == {}
    # 나눈 목소리·반주와 보고는 생긴 것만 목록에 보인다 (영상을 넣지 않았으니 mp4 는 없다)
    for tail in (".vocals.flac", ".backing.flac", ".report.json"):
        (d / "outputs" / f"{key}{tail}").write_bytes(b"x" * 10)
    assert set(runs(client, alice, "polish")[0]["extras"]) == {".vocals.flac", ".backing.flac", ".report.json"}
    # 지우면 곁 결과도 함께 지운다
    finish(ctx, run)
    assert client.delete(f"/api/gen/polish/{run['id']}", headers=alice).status_code == 200
    assert not list((d / "outputs").iterdir())


def test_polish_without_separating_reserves_little_vram_and_takes_videos(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "polish")
    run = post_polish(client, alice, name="말.wav", source="voice", loudness=-16, deess=4, reverb=0, keep_loudness=True,
                      fade_out=3000, ceiling=-1.5).json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["vram_gb"] == gen.POLISH_CPU_VRAM_GB                     # 나누지 않으면 GPU 를 쓰지 않는다
    assert job["command"].endswith("--source voice --loudness -16 --separator htdemucs --shifts 1 --deess 4 --deess-band auto"
                                   " --warmth-style tape --warmth-band lowmid --vocal-level 0 --reverb 0 --reverb-seconds 1.6"
                                   " --predelay 30 --delay-note 1/8 --ceiling -1.5 --keep-loudness --fade-out 3000")
    clip = post_polish(client, alice, name="장면.MP4", data=MP4).json()
    assert f"--input inputs/{key_of(clip)}-1.mp4" in ctx.sched.get(clip["job_id"])["command"]
    assert ctx.sched.get(clip["job_id"])["vram_gb"] == 4                 # 영상의 소리도 '노래'면 나눈다
    assert post_polish(client, alice, name="a.mp3", source="sfx").status_code == 200
    assert (d / "inputs" / f"{key}-1.wav").exists()


def test_polish_refuses_what_it_cannot_take(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "polish")
    post_polish(client, alice)
    before = sorted(x.name for x in (d / "inputs").iterdir())
    cases = [
        (dict(name="cover.png", data=PNG), "소리나 영상 파일이 아닙니다"),
        (dict(files=[]), "받은 수 0개"),
        (dict(files=[("a.flac", FLAC), ("b.flac", FLAC)]), "받은 수 2개"),
        (dict(source="podcast"), "'넣은 것'은 song, voice, sfx, mix"),
        (dict(loudness=-5), "'마지막 음량 (LUFS)'은 -30 이상 -8 이하"),
        (dict(deess=20), "'치찰음 줄이기 (dB)'"),
        (dict(shifts=9), "'나누기 반복'"),
        (dict(delay_note="1/3"), "'메아리 간격'"),
    ]
    for kwargs, expect in cases:
        r = post_polish(client, alice, **kwargs)
        assert r.status_code == 400 and expect in r.json()["detail"], (kwargs, r.text)
    assert sorted(x.name for x in (d / "inputs").iterdir()) == before          # 거절한 요청은 파일을 남기지 않는다


def test_polish_progress_reads_the_stage_lines_and_the_separation_bar():
    p = gen.progress_polish
    assert p("") is None and p("Downloading: 100%|...\n") is None
    assert p("[polish] 단계 1/5: 소리 읽기\n") == {"stage": "소리 읽기", "step": 1, "steps": 5, "percent": None}
    tail = "[polish] 단계 2/5: 목소리와 반주 나누기 (htdemucs)\n 45%|████▌     | 42.1/93.6 [00:09<00:11, 4.4seconds/s]\n"
    assert p(tail) == {"stage": "목소리와 반주 나누기", "step": 2, "steps": 5, "percent": 45}
    assert p(tail + "[polish] 단계 3/5: 목소리 다듬기\n") == {"stage": "목소리 다듬기", "step": 3, "steps": 5, "percent": None}


# ── 설정 묶음 ──

def test_settings_can_be_saved_under_a_name_and_come_back_with_the_page(env):
    client, ctx, admin, alice = env
    r = client.post("/api/gen/3d/presets", json={"name": "  게임용   가벼운 메시 ", "params": {"faces": 50000, "texture_size": "1024"}},
                    headers=alice)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == "게임용 가벼운 메시" and p["params"]["faces"] == 50000 and p["params"]["ss_steps"] == 12
    assert [x["name"] for x in client.get("/api/gen/3d", headers=alice).json()["presets"]] == ["게임용 가벼운 메시"]
    # 같은 이름으로 다시 저장하면 덮어쓴다
    again = client.post("/api/gen/3d/presets", json={"name": "게임용 가벼운 메시", "params": {"faces": 20000}}, headers=alice).json()
    assert again["id"] == p["id"] and again["params"]["faces"] == 20000 and again["params"]["texture_size"] == "4096"
    assert client.get("/api/gen/image", headers=alice).json()["presets"] == []         # 종류마다 따로다
    assert client.get("/api/gen/3d", headers=admin).json()["presets"] == []             # 사용자마다 따로다
    for body, expect in (({"name": "", "params": {}}, "이름"), ({"name": "x" * 41, "params": {}}, "41자"),
                         ({"name": "a", "params": {"faces": 1}}, "삼각형 수"), ({"name": "a", "params": {"nope": 1}}, "모르는 설정")):
        r = client.post("/api/gen/3d/presets", json=body, headers=alice)
        assert r.status_code == 400 and expect in r.json()["detail"], (body, r.text)
    assert client.delete(f"/api/gen/3d/presets/{p['id']}", headers=admin).status_code == 400    # 남의 것은 못 지운다
    assert client.delete(f"/api/gen/3d/presets/{p['id']}", headers=alice).status_code == 200
    assert client.get("/api/gen/3d", headers=alice).json()["presets"] == []


# ── 공통 ──

def test_3d_progress_reads_the_stage_from_the_end_of_the_log():
    p = gen.progress_3d
    assert p("") is None and p("hello\nworld\n") is None
    assert p("[Pipeline] Loading from TencentARC/Pixal3D...\n")["stage"] == "모델 올리기"
    tail = ("Sampling shape SLat (proj): 100%|##| 12/12 [00:04<00:00]\n"
            "\rSampling HR shape SLat (proj, 1024):   8%|8 | 1/12\rSampling HR shape SLat (proj, 1024):  17%|#6 | 2/12")
    assert p(tail) == {"stage": "형상 다듬기", "step": 4, "steps": 6, "percent": 17}
    # GLB 를 쓰는 동안에는 "Extracting GLB" 줄이 밀려나고 다른 막대 이름만 남는다
    tail = "Sampling texture SLat (proj): 100%|#| 12/12\nSampling attributes: 100%|#| 6/6\nFinalizing mesh:  50%|# | 3/6"
    assert p(tail) == {"stage": "GLB 파일 쓰기", "step": 6, "steps": 6, "percent": None}
    assert p("[Inference] Extracting GLB...\n")["step"] == 6


def test_file_names_say_nothing_about_what_was_made():
    a, b = gen.new_key(0), gen.new_key(0)
    assert re.fullmatch(KEY, a) and a != b                    # 같은 초에 만들어도 겹치지 않는다
    for bad in ("a.gif", "noext", "", ".png.exe"):
        with pytest.raises(gen.GenError):
            gen._image_ext(bad)
    assert gen._image_ext("../../etc/pass wd;rm -rf.PNG") == ".png"


def test_values_are_checked_against_the_definition():
    k = gen.KINDS["3d"]
    v = k.clean_params({"ss_steps": 20.0, "fov_deg": "", "remesh": False})
    assert v["ss_steps"] == 20 and isinstance(v["ss_steps"], int) and v["fov_deg"] is None and v["seed"] == 42
    flags = k.param_flags(v)
    assert "--ss-steps 20" in flags and "--no-remesh" in flags and "--remesh" not in flags
    assert not any(f.startswith("--fov-deg") for f in flags)
    assert "--tex-rescale 0" in flags                         # 0 은 "넘기지 않음"이 아니라 값이다


def test_rows_from_the_3d_only_table_move_to_the_shared_one(tmp_path):
    """앞선 판은 3D 생성 기록을 gen3d 표에 두었다. 서버를 새 판으로 띄우면 그 줄이 gens 표로 옮겨진다."""
    path = tmp_path / "ironlab.db"
    DB(path).close()                                          # 새 판의 표들을 만든다
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT INTO users(name, role, uid, cpus, mem_gb, disk_gb, created_at) VALUES ('alice', 'user', 20001, 4, 8, 100, 1);
        CREATE TABLE gen3d (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, job_id INTEGER, title TEXT NOT NULL,
                            input TEXT NOT NULL, output TEXT NOT NULL, thumb TEXT, seed INTEGER NOT NULL, created_at REAL NOT NULL);
        INSERT INTO gen3d(user, job_id, title, input, output, thumb, seed, created_at)
            VALUES ('alice', NULL, 'cat.png', 'pixal3d-run/inputs/a.png', 'pixal3d-run/outputs/a.glb', NULL, 7, 100.0);
    """)
    con.commit()
    con.close()
    db = DB(path)
    rows = db.all("SELECT * FROM gens")
    assert len(rows) == 1 and rows[0]["kind"] == "3d" and rows[0]["title"] == "cat.png" and rows[0]["texts"] == "{}"
    assert json.loads(rows[0]["params"]) == {"seed": 7} and json.loads(rows[0]["inputs"]) == ["pixal3d-run/inputs/a.png"]
    assert rows[0]["output"] == "pixal3d-run/outputs/a.glb" and rows[0]["created_at"] == 100.0
    assert db.one("SELECT 1 AS x FROM sqlite_master WHERE name='gen3d'") is None
    db.close()
    assert len(DB(path).all("SELECT * FROM gens")) == 1       # 다시 띄워도 두 번 옮기지 않는다


def test_a_database_from_the_previous_build_gets_the_new_column(tmp_path):
    """앞선 판의 gens 표에는 글을 모두 담는 texts 열이 없다. 그때 만든 그림의 글은 prompt 열에서 읽는다."""
    path = tmp_path / "ironlab.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE gens (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, user TEXT NOT NULL, job_id INTEGER,
                           title TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '', params TEXT NOT NULL DEFAULT '{}',
                           inputs TEXT NOT NULL DEFAULT '[]', output TEXT NOT NULL, thumb TEXT, created_at REAL NOT NULL);
        INSERT INTO gens(kind, user, title, prompt, params, output, created_at)
            VALUES ('image', 'alice', 'old', 'an old prompt', '{"seed": 1}', 'qwen-image-run/outputs/o.png', 5.0);
    """)
    con.commit()
    con.close()
    db = DB(path)
    assert db.one("SELECT texts FROM gens")["texts"] == "{}"

    class NoQueue:
        reasons = {}

    from ironlab.config import load_settings
    s = load_settings(mock=True, data_dir=tmp_path / "d", users_root=tmp_path / "u", shared_root=tmp_path / "s",
                      scratch_root=tmp_path / "x")
    row = gen.list_runs(db, NoQueue(), s, {"name": "alice", "role": "user"}, gen.KINDS["image"])[0]
    assert row["texts"] == {"prompt": "an old prompt"} and row["params"] == {"seed": 1}
