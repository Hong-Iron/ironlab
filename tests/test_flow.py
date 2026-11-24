"""노드 편집기의 서버 쪽: 노드를 이어 붙인 그래프를 검사하고, 앞 노드가 끝나면 뒤 노드를 GPU 작업으로 큐에 넣는다."""

import io
import json
import re

import pytest

from ironlab import flow, gen

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
KEY = r"\d{8}-\d{6}-[0-9a-f]{6}"
IMAGE_FLAGS = "--size 2048x2048 --model turbo --seed 42 --kv-cache --tile 1024"
MESH_FLAGS = ("--seed 42 --ss-steps 12 --ss-guidance 7.5 --ss-rescale 0.7 --ss-rescale-t 5 --shape-steps 12 --shape-guidance 7.5"
              " --shape-rescale 0.5 --shape-rescale-t 3 --max-tokens 49152 --tex-steps 12 --tex-guidance 1"
              " --tex-rescale 0 --tex-rescale-t 3 --faces 1000000 --texture-size 4096 --remesh")
PLAN_FLAGS = "--plan-temperature 0.7 --plan-top-p 0.9 --plan-top-k 30 --plan-repeat 1.005 --plan-max-tokens 4096"
SONG_FLAGS = "--song-temperature 1 --song-top-p 0.95 --song-top-k 100 --song-repeat 1.2 --song-max-tokens 9000 --audio-steps 32"


def install(ctx, *kinds, name="alice", script="#!/bin/bash\n"):
    """사용자 홈에 실행 스크립트를 둔다 (이 파일이 있어야 그 종류의 노드를 쓸 수 있다)."""
    for kind in kinds:
        k = gen.KINDS[kind]
        d = ctx.s.users_root / name / k.folder(ctx.s)
        d.mkdir(parents=True, exist_ok=True)
        (d / k.script(ctx.s)).write_text(script)


def picture_chain(prompt="달빛 아래의 작은 등대", **mesh_params):
    """글 → 그림 그리기 → 3D 만들기."""
    return {"nodes": [{"id": "n1", "type": "text", "text": prompt, "x": 10, "y": 20},
                      {"id": "n2", "type": "image", "params": {}},
                      {"id": "n3", "type": "mesh", "params": mesh_params}],
            "edges": [{"from": "n1", "out": "text", "to": "n2", "in": "prompt"},
                      {"from": "n2", "out": "image", "to": "n3", "in": "image"}]}


def start(client, who, graph, name=""):
    return client.post("/api/flow/runs", json={"name": name, "graph": graph}, headers=who)


def detail(client, who, run_id):
    return client.get(f"/api/flow/runs/{run_id}", headers=who).json()


def end_job(ctx, job_id, files=(), *, home="alice", fail=False):
    """모의 실행 환경에서 그 작업을 시작시키고, 결과 파일을 쓴 뒤 끝낸다."""
    ctx.monitor.collect()
    job = ctx.sched.get(job_id)
    assert job["status"] == "running", job["status"]
    for rel in files:
        (ctx.s.users_root / home / rel).write_bytes(b"x" * 100)
    c = ctx.rt.containers[job["container_id"]]
    c["_ends_at"] = 0
    if fail:
        c["_exit"] = 1
    ctx.monitor.collect()


def my_jobs(client, who):
    return client.get("/api/jobs", headers=who).json()


# ── 화면이 읽는 정의 ──

def test_the_page_reads_the_node_types_and_which_ones_can_run(env):
    client, ctx, admin, alice = env
    s = client.get("/api/flow", headers=alice).json()
    types = {t["key"]: t for t in s["types"]}
    assert list(types) == ["text", "picture", "sound", "clip", "image", "mesh", "mesh_mv", "multiview", "mesh_views", "mesh_shape", "score", "song",
                           "video", "video_join", "voice", "sfx", "polish"]
    # 여러 방향 그리기: 사진 + (영어 설명) → 모은 그림, 여러 방향 묶음, 방향마다 한 장 (45° 둘은 4방향이면 생기지 않는다)
    assert [(i["key"], i["type"], i["required"]) for i in types["multiview"]["inputs"]] == [
        ("image", "image", True), ("prompt", "text", False)]
    assert [(o["key"], o["type"]) for o in types["multiview"]["outputs"]] == [
        ("image", "image"), ("views", "views"), ("front", "image"), ("front-right", "image"), ("right", "image"), ("back", "image"),
        ("left", "image"), ("front-left", "image")]
    assert types["multiview"]["vram_gb"] == 17 and types["multiview"]["ready"] is False and "mvadapter.sh" in types["multiview"]["hint"]
    # 3D 만들기 (여러 방향 그림): 묶음 하나만 받고, 조절 값은 3D 와 같다
    assert [(i["key"], i["type"], i["required"]) for i in types["mesh_views"]["inputs"]] == [("views", "views", True)]
    assert types["mesh_views"]["vram_gb"] == 20 and [p["key"] for p in types["mesh_views"]["params"]] == [p["key"] for p in types["mesh"]["params"]]
    assert [(i["key"], i["required"]) for i in types["mesh_mv"]["inputs"]] == [
        ("front", True), ("right", False), ("back", False), ("left", False), ("top", False), ("bottom", False)]
    assert types["mesh_mv"]["vram_gb"] == 20 and [p["key"] for p in types["mesh_mv"]["params"]] == [p["key"] for p in types["mesh"]["params"]]
    assert types["text"]["source"] == "text" and types["text"]["ready"] is True and types["text"]["params"] == []
    assert [i["key"] for i in types["image"]["inputs"]] == ["prompt", "negative", "refs"]
    assert [(i["type"], i["required"], i["max"]) for i in types["image"]["inputs"]] == [
        ("text", True, 1), ("text", False, 1), ("image", False, 4)]
    assert [o["key"] for o in types["image"]["outputs"]] == ["image"]           # 작은 미리보기는 잇는 출력이 아니다
    assert [o["key"] for o in types["song"]["outputs"]] == ["audio", "score"]
    assert types["mesh"]["ready"] is False and "pixal3d.sh" in types["mesh"]["hint"] and types["mesh"]["vram_gb"] == 18
    # 조절 값은 쉬운 메뉴와 같은 정의다
    assert [p["key"] for p in types["mesh"]["params"]] == [p["key"] for p in client.get("/api/gen/3d", headers=alice).json()["params"]]
    assert [c[0] for c in {p["key"]: p for p in types["score"]["params"]}["cot"]["choices"]] == ["full", "melody"]
    assert s["flows"] == [] and s["runs"] == [] and s["port_types"]["mesh"] == "3D 모델" and s["port_types"]["views"] == "여러 방향 묶음"
    install(ctx, "3d")
    assert {t["key"]: t for t in client.get("/api/flow", headers=alice).json()["types"]}["mesh"]["ready"] is True
    assert client.get("/api/flow").status_code == 401


# ── 실행 ──

def test_a_chain_runs_one_node_after_another(env):
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    r = start(client, alice, picture_chain(), name="등대")
    assert r.status_code == 200, r.text
    run = r.json()
    key = re.search(KEY, run["dir"]).group(0)
    d = f"flow-runs/{key}"
    assert run["status"] == "running" and run["name"] == "등대" and run["dir"] == d
    # 글 노드는 바로 끝나고 글을 파일로 남긴다. 그림 노드만 큐에 들어가고, 3D 노드는 그림을 기다린다
    assert run["nodes"]["n1"]["status"] == "succeeded" and run["nodes"]["n1"]["outputs"]["text"]["path"] == f"{d}/n1.text.txt"
    assert (ctx.s.users_root / "alice" / d / "n1.text.txt").read_text(encoding="utf-8") == "달빛 아래의 작은 등대"
    assert run["nodes"]["n2"]["status"] == "queued" and run["nodes"]["n3"]["status"] == "waiting"
    assert run["nodes"]["n3"]["job_id"] is None and len(my_jobs(client, alice)) == 1
    job = ctx.sched.get(run["nodes"]["n2"]["job_id"])
    assert job["name"] == "그림 그리기 (노드)" and job["workdir"] == "qwen-image-run" and job["vram_gb"] == 21
    assert job["command"] == (f"./qwen-image.sh --prompt-file ../{d}/n1.text.txt --output ../{d}/n2.image.png"
                              f" --thumb ../{d}/n2.thumb.jpg {IMAGE_FLAGS}")

    end_job(ctx, job["id"], [f"{d}/n2.image.png", f"{d}/n2.thumb.jpg"])
    run = detail(client, alice, run["id"])
    assert run["nodes"]["n2"]["status"] == "succeeded"
    assert run["nodes"]["n2"]["outputs"] == {"image": {"path": f"{d}/n2.image.png", "size": 100},
                                             "thumb": {"path": f"{d}/n2.thumb.jpg", "size": 100}}
    # 그림이 생기자 3D 노드가 그 그림을 입력으로 큐에 들어갔다
    assert run["nodes"]["n3"]["status"] == "queued"
    job3 = ctx.sched.get(run["nodes"]["n3"]["job_id"])
    assert job3["name"] == "3D 만들기 (노드)" and job3["workdir"] == "pixal3d-run" and job3["vram_gb"] == 18
    assert job3["command"] == f"./pixal3d.sh --image ../{d}/n2.image.png --output ../{d}/n3.mesh.glb {MESH_FLAGS}"

    ctx.monitor.collect()
    ctx.rt.containers[ctx.sched.get(job3["id"])["container_id"]]["_log"] += [
        "Sampling HR shape SLat (proj, 1024):  58%|#####     | 7/12 [00:12<00:08,  1.70s/it]"]
    mid = detail(client, alice, run["id"])["nodes"]["n3"]
    assert mid["status"] == "running" and mid["progress"] == {"stage": "형상 다듬기", "step": 4, "steps": 6, "percent": 58}

    end_job(ctx, job3["id"], [f"{d}/n3.mesh.glb"])
    run = detail(client, alice, run["id"])
    assert run["status"] == "succeeded" and run["finished_at"] and run["nodes"]["n3"]["outputs"]["mesh"]["size"] == 100
    listed = client.get("/api/flow", headers=alice).json()["runs"]
    assert [(x["id"], x["status"], x["steps"], x["done"], x["reused"]) for x in listed] == [
        (run["id"], "succeeded", ["그림 그리기", "3D 만들기"], 2, 0)]
    assert any(e["kind"] == "flow.run" for e in ctx.db.recent_events(20))


def test_changing_a_later_node_makes_only_that_node_again(env):
    """뒤쪽 노드의 값만 고쳐 다시 실행하면, 앞 노드는 지난 결과를 다시 쓰고 고친 노드부터만 돈다."""
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    first = start(client, alice, picture_chain()).json()
    d1 = first["dir"]
    end_job(ctx, first["nodes"]["n2"]["job_id"], [f"{d1}/n2.image.png", f"{d1}/n2.thumb.jpg"])
    end_job(ctx, detail(client, alice, first["id"])["nodes"]["n3"]["job_id"], [f"{d1}/n3.mesh.glb"])
    assert detail(client, alice, first["id"])["status"] == "succeeded" and len(my_jobs(client, alice)) == 2

    second = start(client, alice, picture_chain(faces=100000, texture_size="2048")).json()
    d2 = second["dir"]
    n2, n3 = second["nodes"]["n2"], second["nodes"]["n3"]
    assert n2["status"] == "reused" and n2["reused_from"] == first["id"] and n2["job_id"] is None
    assert n2["outputs"]["image"]["path"] == f"{d1}/n2.image.png"          # 지난 실행의 그림을 그대로 가리킨다
    assert n3["status"] == "queued" and len(my_jobs(client, alice)) == 3    # 새 작업은 3D 하나뿐
    cmd = ctx.sched.get(n3["job_id"])["command"]
    assert cmd.startswith(f"./pixal3d.sh --image ../{d1}/n2.image.png --output ../{d2}/n3.mesh.glb ")
    assert " --faces 100000 --texture-size 2048 " in cmd
    end_job(ctx, n3["job_id"], [f"{d2}/n3.mesh.glb"])
    assert detail(client, alice, second["id"])["status"] == "succeeded"

    # 아무것도 고치지 않고 다시 실행하면 작업을 하나도 넣지 않고 바로 끝난다
    third = start(client, alice, picture_chain(faces=100000, texture_size="2048")).json()
    assert third["status"] == "succeeded" and len(my_jobs(client, alice)) == 3
    assert [third["nodes"][n]["status"] for n in ("n1", "n2", "n3")] == ["succeeded", "reused", "reused"]
    # 글을 고치면 그 뒤가 모두 다시 돈다
    fourth = start(client, alice, picture_chain(prompt="눈 덮인 등대", faces=100000, texture_size="2048")).json()
    assert fourth["nodes"]["n2"]["status"] == "queued" and fourth["nodes"]["n3"]["status"] == "waiting"
    # 결과 파일이 지워졌으면 다시 쓰지 않고 새로 만든다
    assert client.post(f"/api/flow/runs/{fourth['id']}/cancel", headers=alice).status_code == 200
    (ctx.s.users_root / "alice" / d1 / "n2.image.png").unlink()
    fifth = start(client, alice, picture_chain()).json()
    assert fifth["nodes"]["n2"]["status"] == "queued"


def test_a_failed_node_stops_what_follows(env):
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    run = start(client, alice, picture_chain()).json()
    end_job(ctx, run["nodes"]["n2"]["job_id"], fail=True)
    cur = detail(client, alice, run["id"])
    assert cur["status"] == "failed" and cur["nodes"]["n2"]["status"] == "failed" and cur["nodes"]["n2"]["error"]
    assert cur["nodes"]["n3"]["status"] == "skipped" and "건너뛰었습니다" in cur["nodes"]["n3"]["error"]
    assert cur["nodes"]["n3"]["job_id"] is None and len(my_jobs(client, alice)) == 1

    # 작업은 성공으로 끝났는데 결과 파일을 쓰지 않은 경우도 실패다
    run = start(client, alice, picture_chain(prompt="다른 그림")).json()
    end_job(ctx, run["nodes"]["n2"]["job_id"])
    cur = detail(client, alice, run["id"])
    assert cur["nodes"]["n2"]["status"] == "failed" and "결과 파일이 없습니다 (그림)" in cur["nodes"]["n2"]["error"]
    assert cur["status"] == "failed" and cur["nodes"]["n3"]["status"] == "skipped"

    # 큐가 작업을 받지 않으면 (여기서는 사용자 VRAM 한도) 그 노드가 실패한다
    assert client.patch("/api/admin/users/alice", json={"max_vram_gb": 12}, headers=admin).status_code == 200
    run = start(client, alice, picture_chain(prompt="또 다른 그림")).json()
    assert run["status"] == "failed" and "한도" in run["nodes"]["n2"]["error"] and run["nodes"]["n3"]["status"] == "skipped"


def test_cancel_stops_the_job_and_delete_keeps_what_other_runs_use(env):
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    home = ctx.s.users_root / "alice"
    first = start(client, alice, picture_chain()).json()
    d1 = first["dir"]
    assert client.delete(f"/api/flow/runs/{first['id']}", headers=alice).status_code == 400      # 도는 중에는 못 지운다
    end_job(ctx, first["nodes"]["n2"]["job_id"], [f"{d1}/n2.image.png", f"{d1}/n2.thumb.jpg"])
    job3 = detail(client, alice, first["id"])["nodes"]["n3"]["job_id"]
    ctx.monitor.collect()                                                    # 3D 작업이 시작됐다
    r = client.post(f"/api/flow/runs/{first['id']}/cancel", headers=alice)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["nodes"]["n3"]["status"] == "cancelled"
    assert ctx.sched.get(job3)["status"] == "cancelled" and r.json()["nodes"]["n2"]["status"] == "succeeded"
    assert client.post(f"/api/flow/runs/{first['id']}/cancel", headers=alice).status_code == 400  # 이미 끝난 실행

    # 둘째 실행이 첫 실행의 그림을 다시 쓴다. 첫 실행을 지워도 그 그림은 남아야 한다
    second = start(client, alice, picture_chain()).json()
    d2 = second["dir"]
    assert second["nodes"]["n2"]["status"] == "reused"
    end_job(ctx, second["nodes"]["n3"]["job_id"], [f"{d2}/n3.mesh.glb"])
    assert client.delete(f"/api/flow/runs/{first['id']}", headers=alice).status_code == 200
    assert (home / d1 / "n2.image.png").exists() and not (home / d1 / "n1.text.txt").exists()
    assert detail(client, alice, second["id"])["nodes"]["n2"]["outputs"]["image"]["size"] == 100
    assert client.get(f"/api/flow/runs/{first['id']}", headers=alice).status_code == 400
    # 둘째 실행까지 지우면 폴더가 통째로 없어진다. 작업 기록은 "GPU 작업"에 남는다
    assert client.delete(f"/api/flow/runs/{second['id']}", headers=alice).status_code == 200
    assert not (home / d2).exists() and ctx.sched.get(job3) is not None
    assert client.get("/api/flow", headers=alice).json()["runs"] == []


def test_a_song_is_made_from_a_score_that_another_node_wrote(env):
    client, ctx, admin, alice = env
    install(ctx, "music")
    graph = {"nodes": [{"id": "a", "type": "text", "text": "K-pop ballad, piano"},
                       {"id": "b", "type": "text", "text": "[Verse]\n창밖에 비가 내리는 밤"},
                       {"id": "c", "type": "score", "params": {"cot": "melody"}},
                       {"id": "d", "type": "song", "params": {"audio_steps": 16}}],
             "edges": [{"from": "a", "out": "text", "to": "c", "in": "style"}, {"from": "b", "out": "text", "to": "c", "in": "lyrics"},
                       {"from": "a", "out": "text", "to": "d", "in": "style"}, {"from": "b", "out": "text", "to": "d", "in": "lyrics"},
                       {"from": "c", "out": "score", "to": "d", "in": "score"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    job = ctx.sched.get(run["nodes"]["c"]["job_id"])
    assert job["name"] == "악보 쓰기 (노드)" and job["workdir"] == "yue2-run" and job["vram_gb"] == 16
    assert job["command"] == (f"./yue2.sh --style-file ../{d}/a.text.txt --lyrics-file ../{d}/b.text.txt --output ../{d}/c.score.abc"
                              f" --stage plan --cot melody --seed 42 {PLAN_FLAGS}")
    assert run["nodes"]["d"]["status"] == "waiting"
    end_job(ctx, job["id"], [f"{d}/c.score.abc"])
    cur = detail(client, alice, run["id"])
    song = ctx.sched.get(cur["nodes"]["d"]["job_id"])
    assert song["name"] == "노래 만들기 (노드)"
    assert song["command"] == (f"./yue2.sh --style-file ../{d}/a.text.txt --lyrics-file ../{d}/b.text.txt --abc-file ../{d}/c.score.abc"
                               f" --output ../{d}/d.audio.flac --mp3 ../{d}/d.listen.mp3 --score ../{d}/d.score.abc"
                               f" --cot full --seed 42 {PLAN_FLAGS} {SONG_FLAGS.replace('--audio-steps 32', '--audio-steps 16')}")
    # 곡만 쓰고 악보는 쓰지 않아도 (악보는 설정에 따라 안 생길 수 있는 결과다) 성공이다
    end_job(ctx, song["id"], [f"{d}/d.audio.flac", f"{d}/d.listen.mp3"])
    cur = detail(client, alice, run["id"])
    assert cur["status"] == "succeeded" and set(cur["nodes"]["d"]["outputs"]) == {"audio", "listen"}


def test_photos_from_several_directions_feed_one_3d_node(env):
    """'3D 만들기 (여러 장)' 노드는 방향마다 입력이 따로 있고, 이어진 방향을 입력 순서대로 --views 로 넘긴다."""
    client, ctx, admin, alice = env
    install(ctx, "3d")
    ups = {}
    for name in ("front", "left", "top"):
        r = client.post("/api/flow/upload", files={"file": (f"{name}.png", io.BytesIO(PNG), "image/png")}, headers=alice)
        ups[name] = r.json()["path"]
    graph = {"nodes": [{"id": "pf", "type": "picture", "path": ups["front"]},
                       {"id": "pt", "type": "picture", "path": ups["top"]},
                       {"id": "pl", "type": "picture", "path": ups["left"]},
                       {"id": "m", "type": "mesh_mv", "params": {"faces": 200000}}],
             "edges": [{"from": "pt", "out": "image", "to": "m", "in": "top"},          # 연결을 그린 순서와 상관없이
                       {"from": "pf", "out": "image", "to": "m", "in": "front"},        # 입력의 순서(앞, 오른쪽, 뒤, 왼쪽, 위, 아래)로 넘긴다
                       {"from": "pl", "out": "image", "to": "m", "in": "left"}]}
    run = start(client, alice, graph).json()
    job = ctx.sched.get(run["nodes"]["m"]["job_id"])
    assert job["name"] == "3D 만들기 (여러 장) (노드)" and job["workdir"] == "pixal3d-run" and job["vram_gb"] == 20
    assert job["command"].startswith(f"./pixal3d.sh --image ../{ups['front']} --image ../{ups['left']} --image ../{ups['top']}"
                                     f" --views front,left,top --output ../{run['dir']}/m.mesh.glb --seed 42 ")
    # 앞 사진 하나만 이으면 방향을 넘기지 않는다 (한 장의 길로 돈다)
    one = start(client, alice, {"nodes": graph["nodes"][:1] + graph["nodes"][3:], "edges": [graph["edges"][1]]}).json()
    assert "--views" not in ctx.sched.get(one["nodes"]["m"]["job_id"])["command"]
    assert ctx.sched.get(one["nodes"]["m"]["job_id"])["vram_gb"] == 18          # 한 장이면 한 장만큼 예약한다
    # 앞 사진이 없으면 실행하지 않는다
    r = start(client, alice, {"nodes": graph["nodes"], "edges": [graph["edges"][0], graph["edges"][2]]})
    assert r.status_code == 400 and "'앞'에 아무것도 이어져 있지 않습니다" in r.json()["detail"]


BATCH_RUNNER = '#!/bin/bash\nif [ "$1" = "--batch-file" ]; then exec python run.py "$@"; fi\n'


def four_views_graph(photo, texts=("해당 건물의 윗모습", "왼쪽 모습", "오른쪽 모습", "뒷모습"), models=("turbo",) * 4):
    """사진 한 장을 참고로 방향마다 다른 글로 그림 네 장을 그리고, 그 네 장과 사진을 '3D 만들기 (여러 장)'에 잇는다."""
    draw = [("n3", "n5", "top"), ("n4", "n6", "left"), ("n7", "n8", "right"), ("n10", "n9", "back")]
    nodes = [{"id": "n2", "type": "picture", "path": photo}]
    nodes += [{"id": t, "type": "text", "text": text} for (_, t, _), text in zip(draw, texts)]
    nodes += [{"id": i, "type": "image", "params": {"model": m}} for (i, _, _), m in zip(draw, models)]
    nodes.append({"id": "n1", "type": "mesh_mv", "params": {}})
    edges = [{"from": "n2", "out": "image", "to": "n1", "in": "front"}]
    for i, t, view in draw:
        edges += [{"from": "n2", "out": "image", "to": i, "in": "refs"},
                  {"from": t, "out": "text", "to": i, "in": "prompt"},
                  {"from": i, "out": "image", "to": "n1", "in": view}]
    return {"nodes": nodes, "edges": edges}


def test_drawing_nodes_of_one_model_run_as_one_job_and_each_gets_its_own_text(env):
    """같은 때 준비된 그림 노드들은 작업 하나로 묶여 모델을 한 번만 올린다. 노드마다 제 글·참고 그림·결과 경로를 받는다."""
    client, ctx, admin, alice = env
    install(ctx, "image", script=BATCH_RUNNER)
    install(ctx, "3d")
    photo = client.post("/api/flow/upload", files={"file": ("house.png", io.BytesIO(PNG), "image/png")}, headers=alice).json()["path"]
    run = start(client, alice, four_views_graph(photo)).json()
    d = run["dir"]
    nodes = run["nodes"]
    jobs = {nodes[i]["job_id"] for i in ("n3", "n4", "n7", "n10")}
    assert len(jobs) == 1 and len(my_jobs(client, alice)) == 1
    job = ctx.sched.get(jobs.pop())
    assert job["name"] == "그림 그리기 ×4 (노드)" and job["vram_gb"] == 21 and job["workdir"] == "qwen-image-run"
    assert job["command"] == f"./qwen-image.sh --batch-file ../{d}/batch-n3-4.json"
    assert [nodes[i]["batch"] for i in ("n3", "n4", "n7", "n10")] == [{"pos": p, "n": 4} for p in (1, 2, 3, 4)]
    assert nodes["n1"]["status"] == "waiting"

    # 묶음 파일: 칸마다 한 노드. 그 노드에 이은 글 파일, 참고 사진, 그 노드의 결과 경로가 들어 있다
    items = json.loads((ctx.s.users_root / "alice" / d / "batch-n3-4.json").read_text(encoding="utf-8"))
    home = ctx.s.users_root / "alice"
    wanted = {"n3": "해당 건물의 윗모습", "n4": "왼쪽 모습", "n7": "오른쪽 모습", "n10": "뒷모습"}
    for argv, (nid, text) in zip(items, wanted.items()):
        opt = dict(zip(argv[::2], argv[1::2]))
        assert (home / "qwen-image-run" / opt["--prompt-file"]).resolve().read_text(encoding="utf-8") == text
        assert opt["--image"] == f"../{photo}" and opt["--output"] == f"../{d}/{nid}.image.png" and opt["--model"] == "turbo"
        assert " ".join(argv) == (f"--prompt-file ../{d}/{opt['--prompt-file'].rsplit('/', 1)[1]} --image ../{photo}"
                                  f" --output ../{d}/{nid}.image.png --thumb ../{d}/{nid}.thumb.jpg {IMAGE_FLAGS}")

    # 도는 동안: 로그의 '묶음 k/n' 줄로 노드마다 차례를 읽는다
    ctx.monitor.collect()
    ctx.rt.containers[ctx.sched.get(job["id"])["container_id"]]["_log"] += [
        "[qwen-image] 묶음: 4장을 한 번에 그립니다 (모델을 한 번만 올립니다)", "[qwen-image] 묶음 1/4",
        "[qwen-image] 모델을 읽었습니다: Qwen/Qwen-Image-2.1-Turbo, 20 s", "[qwen-image] 끝: n3.image.png",
        "[qwen-image] 묶음 2/4", "[qwen-image] 시작: 모델 turbo", " 50%|█████     | 4/8 [00:10<00:10,  2.5s/it]"]
    mid = detail(client, alice, run["id"])["nodes"]
    assert mid["n3"]["progress"]["percent"] == 100 and "다 그림" in mid["n3"]["progress"]["stage"]
    assert mid["n4"]["progress"] == {"stage": "그리기 (묶음 2/4)", "step": 2, "steps": 3, "percent": 50}
    assert mid["n7"]["progress"]["waiting"] and "3번째" in mid["n7"]["progress"]["stage"]

    # 한 장(n10)만 실패하면 작업은 '실패'로 끝나도 나머지 세 노드는 성공이고, 뒤의 3D 노드는 건너뛴다
    files = [f"{d}/{i}.{p}" for i in ("n3", "n4", "n7") for p in ("image.png", "thumb.jpg")]
    end_job(ctx, job["id"], files, fail=True)
    after = detail(client, alice, run["id"])
    assert [after["nodes"][i]["status"] for i in ("n3", "n4", "n7", "n10", "n1")] == [
        "succeeded", "succeeded", "succeeded", "failed", "skipped"]
    assert "이 노드의 결과만 나오지 않았습니다" in after["nodes"]["n10"]["error"]
    assert after["status"] == "failed"


def test_drawing_nodes_are_split_by_model_and_old_runners_get_one_job_each(env):
    client, ctx, admin, alice = env
    install(ctx, "image", script=BATCH_RUNNER)
    install(ctx, "3d")
    photo = client.post("/api/flow/upload", files={"file": ("house.png", io.BytesIO(PNG), "image/png")}, headers=alice).json()["path"]
    # 모델이 다르면 따로 묶는다 (turbo 둘, base 둘 → 작업 둘)
    run = start(client, alice, four_views_graph(photo, models=("turbo", "base", "turbo", "base"))).json()
    by_job = {}
    for i in ("n3", "n4", "n7", "n10"):
        by_job.setdefault(run["nodes"][i]["job_id"], []).append(i)
    assert sorted(by_job.values()) == [["n3", "n7"], ["n4", "n10"]]
    assert {ctx.sched.get(j)["name"] for j in by_job} == {"그림 그리기 ×2 (노드)"}
    # 두 작업이 끝나면 그림 네 장이 '3D 만들기 (여러 장)'의 제 방향 입력으로 들어간다 (앞은 올린 사진)
    d = run["dir"]
    for job_id, ids in by_job.items():
        end_job(ctx, job_id, [f"{d}/{i}.{p}" for i in ids for p in ("image.png", "thumb.jpg")])
    after = detail(client, alice, run["id"])
    assert after["nodes"]["n3"]["status"] == "succeeded" and after["nodes"]["n1"]["status"] == "queued"
    mesh = ctx.sched.get(after["nodes"]["n1"]["job_id"])["command"]
    assert mesh.startswith(f"./pixal3d.sh --image ../{photo} --image ../{d}/n7.image.png --image ../{d}/n10.image.png"
                           f" --image ../{d}/n4.image.png --image ../{d}/n3.image.png --views front,right,back,left,top ")
    # 묶음을 모르는 예전 실행 스크립트면 예전처럼 노드마다 작업 하나
    install(ctx, "image")
    run = start(client, alice, four_views_graph(photo, texts=("위", "왼쪽", "오른쪽", "뒤"))).json()
    jobs = [ctx.sched.get(run["nodes"][i]["job_id"]) for i in ("n3", "n4", "n7", "n10")]
    assert len({j["id"] for j in jobs}) == 4 and {j["name"] for j in jobs} == {"그림 그리기 (노드)"}
    assert all("--batch-file" not in j["command"] and j["command"].startswith("./qwen-image.sh --prompt-file ") for j in jobs)
    assert run["nodes"]["n3"]["batch"] is None


def test_a_join_node_glues_two_videos_in_port_order_with_little_vram(env):
    client, ctx, admin, alice = env
    install(ctx, "video", script='#!/bin/bash\nif [ "$1" = "--join" ]; then exec python3 h3_join.py "$@"; fi\n')
    types = {t["key"]: t for t in client.get("/api/flow", headers=alice).json()["types"]}
    assert [(i["key"], i["required"]) for i in types["video_join"]["inputs"]][:3] == [("v1", True), ("v2", True), ("v3", False)]
    assert types["video_join"]["vram_gb"] == 1 and types["video_join"]["params"] == []
    graph = {"nodes": [{"id": "t1", "type": "text", "text": "A paper airplane glides."},
                       {"id": "t2", "type": "text", "text": "It lands softly."},
                       {"id": "a", "type": "video", "params": {"size": "864x480"}},
                       {"id": "b", "type": "video", "params": {"size": "864x480"}},
                       {"id": "j", "type": "video_join", "params": {}}],
             "edges": [{"from": "t1", "out": "text", "to": "a", "in": "prompt"},
                       {"from": "t2", "out": "text", "to": "b", "in": "prompt"},
                       {"from": "a", "out": "last", "to": "b", "in": "first"},
                       {"from": "b", "out": "video", "to": "j", "in": "v2"},            # 그린 순서와 상관없이 입력 차례대로 잇는다
                       {"from": "a", "out": "video", "to": "j", "in": "v1"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    end_job(ctx, run["nodes"]["a"]["job_id"], [f"{d}/a.video.mp4", f"{d}/a.last.png", f"{d}/a.thumb.jpg"])
    run = detail(client, alice, run["id"])
    end_job(ctx, run["nodes"]["b"]["job_id"], [f"{d}/b.video.mp4", f"{d}/b.last.png", f"{d}/b.thumb.jpg"])
    run = detail(client, alice, run["id"])
    job = ctx.sched.get(run["nodes"]["j"]["job_id"])
    assert job["name"] == "영상 이어 붙이기 (노드)" and job["vram_gb"] == 1 and job["workdir"] == "minimax-h3-run"
    assert job["command"] == (f"./h3.sh --join ../{d}/a.video.mp4 --join ../{d}/b.video.mp4 --output ../{d}/j.video.mp4"
                              f" --last-frame ../{d}/j.last.png --thumb ../{d}/j.thumb.jpg")
    end_job(ctx, job["id"], [f"{d}/j.video.mp4", f"{d}/j.last.png", f"{d}/j.thumb.jpg"])
    # 예전 판 스크립트면 작업을 넣지 않고 노드를 실패로 알린다
    install(ctx, "video")
    old = start(client, alice, {"nodes": graph["nodes"], "edges": graph["edges"]}).json()
    # 실행 스크립트가 바뀌었으므로 영상 두 개도 지난 결과를 다시 쓰지 않고 새로 만든다 (gen.runner_fingerprint)
    assert old["nodes"]["a"]["status"] == "queued" and old["nodes"]["j"]["status"] == "waiting"
    d2 = old["dir"]
    end_job(ctx, old["nodes"]["a"]["job_id"], [f"{d2}/a.video.mp4", f"{d2}/a.last.png", f"{d2}/a.thumb.jpg"])
    old = detail(client, alice, old["id"])
    end_job(ctx, old["nodes"]["b"]["job_id"], [f"{d2}/b.video.mp4", f"{d2}/b.last.png", f"{d2}/b.thumb.jpg"])
    old = detail(client, alice, old["id"])
    assert old["nodes"]["j"]["status"] == "failed" and "예전 판" in old["nodes"]["j"]["error"]


def test_a_video_node_takes_references_but_not_together_with_frames(env):
    client, ctx, admin, alice = env
    install(ctx, "video", "voice")
    pic = client.post("/api/flow/upload", files={"file": ("cat.png", io.BytesIO(PNG), "image/png")}, headers=alice).json()["path"]
    snd = client.post("/api/flow/upload", files={"file": ("me.wav", io.BytesIO(b"RIFF" + b"0" * 60), "audio/wav")}, headers=alice).json()["path"]
    graph = {"nodes": [{"id": "t", "type": "text", "text": "<Picture 1> 속 고양이가 (S1) 인사한다"},
                       {"id": "p", "type": "picture", "path": pic}, {"id": "s", "type": "sound", "path": snd},
                       {"id": "v", "type": "video", "params": {"size": "864x480", "ref_audio_role": "voice"}}],
             "edges": [{"from": "t", "out": "text", "to": "v", "in": "prompt"},
                       {"from": "s", "out": "audio", "to": "v", "in": "ref_audios"},
                       {"from": "p", "out": "image", "to": "v", "in": "ref_images"}]}
    run = start(client, alice, graph).json()
    cmd = ctx.sched.get(run["nodes"]["v"]["job_id"])["command"]
    assert f"--ref-image ../{pic} --ref-audio ../{snd} --output" in cmd and cmd.endswith("--ref-audio-role voice")
    r = start(client, alice, {"nodes": graph["nodes"], "edges": graph["edges"] + [{"from": "p", "out": "image", "to": "v", "in": "first"}]})
    assert r.status_code == 400 and "첫·끝 장면과 참고 자료가 함께" in r.json()["detail"]


def test_a_drawing_node_turned_to_another_angle_needs_a_reference_picture(env):
    client, ctx, admin, alice = env
    install(ctx, "image", script=BATCH_RUNNER)
    graph = {"nodes": [{"id": "t", "type": "text", "text": "-"}, {"id": "i", "type": "image", "params": {"view": "back"}}],
             "edges": [{"from": "t", "out": "text", "to": "i", "in": "prompt"}]}
    r = start(client, alice, graph)
    assert r.status_code == 400 and "참고 그림이 이어져 있지 않습니다" in r.json()["detail"]
    photo = client.post("/api/flow/upload", files={"file": ("cup.png", io.BytesIO(PNG), "image/png")}, headers=alice).json()["path"]
    graph["nodes"].append({"id": "p", "type": "picture", "path": photo})
    graph["edges"].append({"from": "p", "out": "image", "to": "i", "in": "refs"})
    run = start(client, alice, graph).json()
    assert " --view back" in ctx.sched.get(run["nodes"]["i"]["job_id"])["command"]


def test_an_uploaded_picture_feeds_a_node(env):
    client, ctx, admin, alice = env
    install(ctx, "3d")
    r = client.post("/api/flow/upload", files={"file": ("내 고양이 (1).PNG", io.BytesIO(PNG), "image/png")}, headers=alice)
    assert r.status_code == 200, r.text
    up = r.json()
    assert re.fullmatch(rf"flow-inputs/{KEY}\.png", up["path"]) and up["name"] == "내 고양이 (1).PNG" and up["size"] == len(PNG)
    assert (ctx.s.users_root / "alice" / up["path"]).read_bytes() == PNG
    graph = {"nodes": [{"id": "p", "type": "picture", "path": up["path"], "name": up["name"]}, {"id": "m", "type": "mesh"}],
             "edges": [{"from": "p", "out": "image", "to": "m", "in": "image"}]}
    run = start(client, alice, graph).json()
    assert run["nodes"]["p"]["status"] == "succeeded" and run["nodes"]["p"]["outputs"]["image"]["path"] == up["path"]
    cmd = ctx.sched.get(run["nodes"]["m"]["job_id"])["command"]
    assert cmd.startswith(f"./pixal3d.sh --image ../{up['path']} --output ../{run['dir']}/m.mesh.glb ")
    for name, data, expect in (("notes.txt", PNG, "사진 파일이 아닙니다"), ("a.png", b"", "빈 파일")):
        r = client.post("/api/flow/upload", files={"file": (name, io.BytesIO(data), "image/png")}, headers=alice)
        assert r.status_code == 400 and expect in r.json()["detail"], r.text
    assert len(list((ctx.s.users_root / "alice" / "flow-inputs").iterdir())) == 1
    # 홈에 없는 사진을 가리키면 실행하지 않는다
    graph["nodes"][0]["path"] = "flow-inputs/20260101-000000-abcdef.png"
    r = start(client, alice, graph)
    assert r.status_code == 400 and "홈에 없습니다" in r.json()["detail"]


VIDEO_FLAGS = "--size auto --model flash --seconds 5 --seed 42 --crf 18"
VOICE_FLAGS = "--seed 42 --temperature 1 --top-p 0.9 --max-tokens 1024 --context 4096 --cpu-layers 16"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 64


def test_a_video_starts_from_a_drawn_picture_and_the_next_one_from_its_last_frame(env):
    client, ctx, admin, alice = env
    install(ctx, "image", "video")
    graph = {"nodes": [{"id": "t1", "type": "text", "text": "Cinematic. A paper airplane leaves a rooftop."},
                       {"id": "i", "type": "image", "params": {}},
                       {"id": "v1", "type": "video", "params": {}},
                       {"id": "t2", "type": "text", "text": "It lands softly in a child's hands."},
                       {"id": "v2", "type": "video", "params": {"size": "864x480", "seconds": 3}}],
             "edges": [{"from": "t1", "out": "text", "to": "i", "in": "prompt"},
                       {"from": "t1", "out": "text", "to": "v1", "in": "prompt"},
                       {"from": "i", "out": "image", "to": "v1", "in": "first"},
                       {"from": "t2", "out": "text", "to": "v2", "in": "prompt"},
                       {"from": "v1", "out": "last", "to": "v2", "in": "first"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    assert run["nodes"]["i"]["status"] == "queued" and run["nodes"]["v1"]["status"] == "waiting"
    end_job(ctx, run["nodes"]["i"]["job_id"], [f"{d}/i.image.png", f"{d}/i.thumb.jpg"])
    run = detail(client, alice, run["id"])
    job = ctx.sched.get(run["nodes"]["v1"]["job_id"])
    assert job["name"] == "영상 만들기 (노드)" and job["workdir"] == "minimax-h3-run" and job["vram_gb"] == 22
    assert job["command"] == (f"./h3.sh --prompt-file ../{d}/t1.text.txt --image ../{d}/i.image.png --output ../{d}/v1.video.mp4"
                              f" --last-frame ../{d}/v1.last.png --thumb ../{d}/v1.thumb.jpg {VIDEO_FLAGS}")
    end_job(ctx, job["id"], [f"{d}/v1.video.mp4", f"{d}/v1.last.png", f"{d}/v1.thumb.jpg"])
    run = detail(client, alice, run["id"])
    assert set(run["nodes"]["v1"]["outputs"]) == {"video", "last", "thumb"}
    # 앞 영상의 끝 장면이 다음 영상의 첫 장면이 된다
    cmd = ctx.sched.get(run["nodes"]["v2"]["job_id"])["command"]
    assert cmd.startswith(f"./h3.sh --prompt-file ../{d}/t2.text.txt --image ../{d}/v1.last.png --output ../{d}/v2.video.mp4 ")
    assert "--size 864x480 --model flash --seconds 3 " in cmd
    # 끝 장면을 내지 못한 영상 노드는 실패로 본다 (뒤 노드가 그것을 기다린다)
    end_job(ctx, run["nodes"]["v2"]["job_id"], [f"{d}/v2.video.mp4", f"{d}/v2.thumb.jpg"])
    run = detail(client, alice, run["id"])
    assert run["nodes"]["v2"]["status"] == "failed" and "끝 장면" in run["nodes"]["v2"]["error"] and run["status"] == "failed"


def test_a_voice_node_copies_an_uploaded_voice(env):
    client, ctx, admin, alice = env
    install(ctx, "voice")
    r = client.post("/api/flow/upload", files={"file": ("내 목소리.WAV", io.BytesIO(WAV), "audio/wav")}, headers=alice)
    assert r.status_code == 200, r.text
    up = r.json()
    assert re.fullmatch(rf"flow-inputs/{KEY}\.wav", up["path"]) and up["name"] == "내 목소리.WAV"
    graph = {"nodes": [{"id": "s", "type": "text", "text": "화자1: 안녕하세요"},
                       {"id": "a", "type": "sound", "path": up["path"], "name": up["name"]},
                       {"id": "r", "type": "text", "text": "녹음에서 하는 말"},
                       {"id": "v", "type": "voice", "params": {}}],
             "edges": [{"from": "s", "out": "text", "to": "v", "in": "script"}, {"from": "a", "out": "audio", "to": "v", "in": "refs"},
                       {"from": "r", "out": "text", "to": "v", "in": "ref_text"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    assert run["nodes"]["a"]["status"] == "succeeded" and run["nodes"]["a"]["outputs"]["audio"]["path"] == up["path"]
    job = ctx.sched.get(run["nodes"]["v"]["job_id"])
    assert job["name"] == "목소리 만들기 (노드)" and job["workdir"] == "drama3-run" and job["vram_gb"] == 18
    assert job["command"] == (f"./drama3.sh --text-file ../{d}/s.text.txt --ref ../{up['path']} --ref-text-file ../{d}/r.text.txt"
                              f" --output ../{d}/v.audio.wav --mp3 ../{d}/v.listen.mp3 {VOICE_FLAGS}")
    # 소리 파일 노드는 소리만, 사진 노드는 사진만 받는다
    bad = {"nodes": [{"id": "a", "type": "sound", "path": "flow-inputs/x.png"}, *graph["nodes"][:1], graph["nodes"][3]],
           "edges": [graph["edges"][0], graph["edges"][1]]}
    r = start(client, alice, bad)
    assert r.status_code == 400 and "소리 파일이 아닙니다" in r.json()["detail"], r.text
    bad = {"nodes": [{"id": "a", "type": "sound"}, *graph["nodes"][:1], graph["nodes"][3]], "edges": [graph["edges"][0], graph["edges"][1]]}
    r = start(client, alice, bad)
    assert r.status_code == 400 and "'소리 파일' 노드(a)에 소리 파일이 없습니다" in r.json()["detail"], r.text


def test_a_video_with_only_a_last_frame_is_refused(env):
    client, ctx, admin, alice = env
    install(ctx, "video")
    graph = {"nodes": [{"id": "t", "type": "text", "text": "x"}, {"id": "p", "type": "picture", "path": "flow-inputs/a.png"},
                       {"id": "v", "type": "video"}],
             "edges": [{"from": "t", "out": "text", "to": "v", "in": "prompt"}, {"from": "p", "out": "image", "to": "v", "in": "last"}]}
    r = start(client, alice, graph)
    assert r.status_code == 400 and "끝 장면만 이어져 있습니다" in r.json()["detail"], r.text


def test_bad_graphs_are_refused_with_the_reason(env):
    client, ctx, admin, alice = env
    install(ctx, "image", "3d", "music")
    ok = picture_chain()

    def changed(**parts):
        g = {"nodes": [dict(n) for n in ok["nodes"]], "edges": [dict(e) for e in ok["edges"]]}
        g.update(parts)
        return g

    two_images = {"nodes": [{"id": "t", "type": "text", "text": "x"}, {"id": "a", "type": "image"}, {"id": "b", "type": "image"}],
                  "edges": [{"from": "t", "out": "text", "to": "a", "in": "prompt"}, {"from": "t", "out": "text", "to": "b", "in": "prompt"},
                            {"from": "a", "out": "image", "to": "b", "in": "refs"}, {"from": "b", "out": "image", "to": "a", "in": "refs"}]}
    song = {"nodes": [{"id": "s", "type": "text", "text": "ballad"}, {"id": "l", "type": "text", "text": "[Verse]\n가사"},
                      {"id": "x", "type": "text", "text": "X:1\nK:C\n|c4|"}, {"id": "g", "type": "song", "params": {"cot": "off"}}],
            "edges": [{"from": "s", "out": "text", "to": "g", "in": "style"}, {"from": "l", "out": "text", "to": "g", "in": "lyrics"},
                      {"from": "x", "out": "text", "to": "g", "in": "score"}]}
    cases = [
        ({"nodes": []}, "노드는 1~40개"),
        ({"edges": []}, "nodes"),
        (changed(nodes=[*ok["nodes"], {"id": "n4", "type": "hologram"}]), "없는 노드 종류"),
        (changed(nodes=[*ok["nodes"], {"id": "N 4", "type": "text", "text": "x"}]), "노드 번호는"),
        (changed(nodes=[*ok["nodes"], {"id": "n1", "type": "text", "text": "x"}]), "두 번 나옵니다"),
        (changed(nodes=[ok["nodes"][0], {"id": "n2", "type": "image", "params": {"size": "999x999"}}, ok["nodes"][2]]), "'그림 그리기' 노드(n2): '크기'"),
        (changed(nodes=[{"id": "n1", "type": "text", "text": "  "}, *ok["nodes"][1:]]), "'글' 노드(n1)가 비어 있습니다"),
        (changed(edges=[{"from": "n1", "out": "text", "to": "n3", "in": "image"}]), "같은 종류끼리만"),
        (changed(edges=[*ok["edges"], {"from": "n9", "out": "text", "to": "n2", "in": "prompt"}]), "없는 노드를 잇는"),
        (changed(edges=[{"from": "n1", "out": "words", "to": "n2", "in": "prompt"}]), "'words' 출력이 없습니다"),
        (changed(edges=[{"from": "n2", "out": "thumb", "to": "n3", "in": "image"}]), "'thumb' 출력이 없습니다"),
        (changed(edges=[ok["edges"][1]]), "'그릴 내용'에 아무것도 이어져 있지 않습니다"),
        (changed(nodes=[*ok["nodes"], {"id": "n4", "type": "text", "text": "둘째 글"}],
                 edges=[*ok["edges"], {"from": "n4", "out": "text", "to": "n2", "in": "prompt"}]), "1개까지 이을 수 있습니다"),
        (two_images, "한 바퀴 돌아"),
        (song, "악보 없이 바로"),
        ({"nodes": [{"id": "t", "type": "text", "text": "글만 있다"}], "edges": []}, "만드는 노드가 없습니다"),
        ({"nodes": [{"id": "p", "type": "picture"}, {"id": "m", "type": "mesh"}],
          "edges": [{"from": "p", "out": "image", "to": "m", "in": "image"}]}, "사진이 없습니다"),
        ({"nodes": [{"id": "p", "type": "picture", "path": "a b; rm -rf ~.png"}, {"id": "m", "type": "mesh"}],
          "edges": [{"from": "p", "out": "image", "to": "m", "in": "image"}]}, "쓸 수 없는 글자"),
        ({"nodes": [{"id": "p", "type": "picture", "path": "../../etc/x.png"}, {"id": "m", "type": "mesh"}],
          "edges": [{"from": "p", "out": "image", "to": "m", "in": "image"}]}, "쓸 수 없는 글자"),
    ]
    for graph, expect in cases:
        r = client.post("/api/flow/runs", json={"graph": graph}, headers=alice)
        assert r.status_code == 400 and expect in r.json()["detail"], (expect, r.text)
    assert my_jobs(client, alice) == [] and client.get("/api/flow", headers=alice).json()["runs"] == []
    assert not (ctx.s.users_root / "alice" / "flow-runs").exists()
    # 실행 스크립트가 없는 종류의 노드가 있으면 무엇을 깔아야 하는지 알려 준다
    r = start(client, admin, picture_chain())
    assert r.status_code == 400 and "'그림 그리기' 노드를 쓸 수 없습니다" in r.json()["detail"] and "qwen-image.sh" in r.json()["detail"]


def test_graphs_are_saved_by_name_even_when_unfinished(env):
    client, ctx, admin, alice = env
    half = {"nodes": [{"id": "n1", "type": "text", "text": "", "x": 12.34, "y": -5}, {"id": "n2", "type": "image", "params": {"seed": 7}}],
            "edges": []}                                        # 글이 비었고 이어지지 않았다: 실행은 못 하지만 저장은 된다
    r = client.post("/api/flow/flows", json={"name": "  등대   연습 ", "graph": half}, headers=alice)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["name"] == "등대 연습" and saved["graph"]["nodes"][0]["x"] == 12.3
    assert saved["graph"]["nodes"][1]["params"]["seed"] == 7 and saved["graph"]["nodes"][1]["params"]["model"] == "turbo"
    again = client.post("/api/flow/flows", json={"name": "등대 연습", "graph": picture_chain()}, headers=alice).json()
    assert again["id"] == saved["id"] and len(again["graph"]["nodes"]) == 3          # 같은 이름은 덮어쓴다
    assert [f["name"] for f in client.get("/api/flow", headers=alice).json()["flows"]] == ["등대 연습"]
    assert client.get("/api/flow", headers=admin).json()["flows"] == []               # 사용자마다 따로다
    for body, expect in (({"name": "", "graph": half}, "이름"), ({"name": "x" * 61, "graph": half}, "61자"),
                         ({"name": "a", "graph": {"nodes": [{"id": "n1", "type": "nope"}]}}, "없는 노드 종류")):
        r = client.post("/api/flow/flows", json=body, headers=alice)
        assert r.status_code == 400 and expect in r.json()["detail"], r.text
    assert client.delete(f"/api/flow/flows/{saved['id']}", headers=admin).status_code == 400     # 남의 것은 못 지운다
    assert client.delete(f"/api/flow/flows/{saved['id']}", headers=alice).status_code == 200
    assert client.get("/api/flow", headers=alice).json()["flows"] == []


def test_other_users_see_the_jobs_but_not_what_is_being_made(env):
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    run = start(client, alice, picture_chain(prompt="secret poster for project Falcon"), name="secret Falcon plan").json()
    r = client.post("/api/admin/users", json={"name": "bob", "cpus": 4, "mem_gb": 8}, headers=admin)
    bob = {"Authorization": f"Bearer {r.json()['token']}"}
    job_id = run["nodes"]["n2"]["job_id"]
    seen = next(j for j in client.get("/api/jobs?scope=all", headers=bob).json() if j["id"] == job_id)
    assert seen["user"] == "alice" and seen["name"] == "그림 그리기 (노드)" and seen["command"] is None
    assert "secret" not in str(seen).lower() and "falcon" not in str(seen).lower()
    command = client.get(f"/api/jobs/{job_id}", headers=admin).json()["command"]
    assert "secret" not in command.lower() and "falcon" not in command.lower()        # 관리자가 보는 명령에도 글이 없다
    assert re.fullmatch(r"[A-Za-z0-9_./ -]+", command), command
    for who in (bob, admin):
        assert client.get(f"/api/flow/runs/{run['id']}", headers=who).status_code == 400
        assert client.post(f"/api/flow/runs/{run['id']}/cancel", headers=who).status_code == 400
        assert client.delete(f"/api/flow/runs/{run['id']}", headers=who).status_code == 400
        assert client.get("/api/flow", headers=who).json()["runs"] == []
    assert detail(client, alice, run["id"])["status"] == "running"


# ── 엔진의 조각 ──

def test_the_order_puts_earlier_nodes_first_and_finds_loops():
    g = flow.clean_graph(picture_chain(), strict=True)
    assert flow.order_of(g) == ["n1", "n2", "n3"]
    g["edges"].append({"from": "n3", "to": "n2", "out": "mesh", "in": "refs"})        # 검사를 거치지 않고 넣은 되돌아오는 연결
    with pytest.raises(flow.FlowError, match="한 바퀴"):
        flow.order_of(g)


def test_a_run_whose_node_type_is_gone_fails_instead_of_blocking_the_others(env):
    """판이 바뀌어 저장된 그래프의 노드 종류가 없어져도, 그 실행만 실패로 끝내고 다른 실행은 계속 나아간다."""
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    broken = start(client, alice, picture_chain()).json()
    fine = start(client, alice, picture_chain(prompt="다른 그림")).json()
    ctx.db.execute("UPDATE flow_runs SET graph=replace(graph, '\"mesh\"', '\"hologram\"') WHERE id=?", (broken["id"],))
    ctx.monitor.collect()                                       # 감시 루프가 한 번 돈다
    cur = detail(client, alice, broken["id"])
    assert cur["status"] == "failed" and "지금 판에서 읽지 못했습니다" in cur["nodes"]["n3"]["error"]
    assert ctx.sched.get(broken["nodes"]["n2"]["job_id"])["status"] == "cancelled"      # 그 실행의 작업은 취소됐다
    # 다른 실행은 영향을 받지 않고 끝까지 간다
    d = fine["dir"]
    end_job(ctx, fine["nodes"]["n2"]["job_id"], [f"{d}/n2.image.png", f"{d}/n2.thumb.jpg"])
    end_job(ctx, detail(client, alice, fine["id"])["nodes"]["n3"]["job_id"], [f"{d}/n3.mesh.glb"])
    assert detail(client, alice, fine["id"])["status"] == "succeeded"


SFX_FLAGS = "--model fast --seed 42 --steps 4 --fade-out 20 --loop-crossfade 0.5"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64


def test_a_sound_effect_node_puts_sound_on_an_uploaded_video(env):
    client, ctx, admin, alice = env
    install(ctx, "sfx")
    s = client.get("/api/flow", headers=alice).json()
    t = {x["key"]: x for x in s["types"]}["sfx"]
    assert [(i["key"], i["type"], i["required"]) for i in t["inputs"]] == [
        ("prompt", "text", False), ("negative", "text", False), ("video", "video", False)]
    assert [o["key"] for o in t["outputs"]] == ["audio", "video"] and t["vram_gb"] == 7 and t["ready"] is True
    r = client.post("/api/flow/upload", files={"file": ("걷기.MP4", io.BytesIO(MP4), "video/mp4")}, headers=alice)
    assert r.status_code == 200, r.text
    up = r.json()
    assert re.fullmatch(rf"flow-inputs/{KEY}\.mp4", up["path"]) and up["name"] == "걷기.MP4"
    graph = {"nodes": [{"id": "c", "type": "clip", "path": up["path"], "name": up["name"]},
                       {"id": "t", "type": "text", "text": "a person walks on a gravel path"},
                       {"id": "s", "type": "sfx", "params": {"seconds": 6}}],
             "edges": [{"from": "c", "out": "video", "to": "s", "in": "video"}, {"from": "t", "out": "text", "to": "s", "in": "prompt"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    assert run["nodes"]["c"]["status"] == "succeeded" and run["nodes"]["c"]["outputs"]["video"]["path"] == up["path"]
    job = ctx.sched.get(run["nodes"]["s"]["job_id"])
    assert job["name"] == "효과음 만들기 (노드)" and job["workdir"] == "woosh-run" and job["vram_gb"] == 7
    assert job["command"] == (f"./woosh.sh --prompt-file ../{d}/t.text.txt --video ../{up['path']} --output ../{d}/s.audio.wav"
                              f" --mp3 ../{d}/s.listen.mp3 --video-out ../{d}/s.video.mp4 --thumb ../{d}/s.thumb.jpg"
                              f" --model fast --seconds 6 --seed 42 --steps 4 --fade-out 20 --loop-crossfade 0.5")
    end_job(ctx, job["id"], [f"{d}/s.audio.wav", f"{d}/s.listen.mp3", f"{d}/s.video.mp4", f"{d}/s.thumb.jpg"])
    run = detail(client, alice, run["id"])
    assert run["nodes"]["s"]["status"] == "succeeded" and set(run["nodes"]["s"]["outputs"]) == {"audio", "listen", "video", "thumb"}


def test_a_sound_effect_node_from_text_alone_and_its_refusals(env):
    client, ctx, admin, alice = env
    install(ctx, "sfx")
    text = {"id": "t", "type": "text", "text": "steady rain on a tin roof"}
    graph = {"nodes": [text, {"id": "s", "type": "sfx", "params": {"loop": True}}],
             "edges": [{"from": "t", "out": "text", "to": "s", "in": "prompt"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    job = ctx.sched.get(run["nodes"]["s"]["job_id"])
    assert job["command"] == (f"./woosh.sh --prompt-file ../{d}/t.text.txt --output ../{d}/s.audio.wav --mp3 ../{d}/s.listen.mp3"
                              f" --video-out ../{d}/s.video.mp4 --thumb ../{d}/s.thumb.jpg --model fast --seed 42 --loop --steps 4"
                              f" --fade-out 20 --loop-crossfade 0.5")
    # 영상 없이 만든 효과음은 소리만 낸다 (소리를 붙인 영상은 없어도 된다)
    end_job(ctx, job["id"], [f"{d}/s.audio.wav", f"{d}/s.listen.mp3"])
    assert detail(client, alice, run["id"])["nodes"]["s"]["status"] == "succeeded"
    cases = [
        ({"nodes": [{"id": "s", "type": "sfx", "params": {}}], "edges": []}, "소리 설명도 영상도 이어져 있지 않습니다"),
        ({"nodes": [{**text, "text": "빗소리"}, graph["nodes"][1]], "edges": graph["edges"]}, "한글이 있습니다"),
        ({"nodes": [text, {"id": "s", "type": "sfx", "params": {"seconds": 6}}], "edges": graph["edges"]}, "5초까지입니다"),
        ({"nodes": [{"id": "c", "type": "clip", "path": "flow-inputs/x.png"}, graph["nodes"][1]],
          "edges": [{"from": "c", "out": "video", "to": "s", "in": "video"}]}, "영상 파일이 아닙니다"),
    ]
    for g, expect in cases:
        r = start(client, alice, g)
        assert r.status_code == 400 and expect in r.json()["detail"], (expect, r.text)


FLAC = b"fLaC" + b"0" * 64


def test_a_polish_node_takes_a_song_and_its_score_and_reserves_by_what_it_does(env):
    client, ctx, admin, alice = env
    install(ctx, "music", "polish")
    t = {x["key"]: x for x in client.get("/api/flow", headers=alice).json()["types"]}["polish"]
    assert [(i["key"], i["type"], i["required"]) for i in t["inputs"]] == [
        ("audio", "audio", False), ("video", "video", False), ("score", "text", False)]
    assert [o["key"] for o in t["outputs"]] == ["audio", "vocals", "backing", "video"] and t["vram_gb"] == 4
    graph = {"nodes": [{"id": "st", "type": "text", "text": "K-pop ballad, piano"},
                       {"id": "ly", "type": "text", "text": "[Verse]\n창밖에 비가 내리는 밤"},
                       {"id": "so", "type": "song", "params": {}},
                       {"id": "po", "type": "polish", "params": {"loudness": -16}}],
             "edges": [{"from": "st", "out": "text", "to": "so", "in": "style"}, {"from": "ly", "out": "text", "to": "so", "in": "lyrics"},
                       {"from": "so", "out": "audio", "to": "po", "in": "audio"}, {"from": "so", "out": "score", "to": "po", "in": "score"}]}
    run = start(client, alice, graph).json()
    d = run["dir"]
    song = run["nodes"]["so"]["job_id"]
    end_job(ctx, song, [f"{d}/so.audio.flac", f"{d}/so.listen.mp3", f"{d}/so.score.abc"])
    ctx.monitor.collect()
    run = detail(client, alice, run["id"])
    job = ctx.sched.get(run["nodes"]["po"]["job_id"])
    assert job["name"] == "소리 다듬기 (노드)" and job["workdir"] == "polish-run" and job["vram_gb"] == 4
    assert job["command"] == (f"./polish.sh --input ../{d}/so.audio.flac --score-file ../{d}/so.score.abc --output ../{d}/po.audio.flac"
                              f" --mp3 ../{d}/po.listen.mp3 --vocals-out ../{d}/po.vocals.flac --backing-out ../{d}/po.backing.flac"
                              f" --video-out ../{d}/po.video.mp4 --report ../{d}/po.report.json --source song --loudness -16"
                              f" --separator htdemucs --shifts 1 --deess-band auto --warmth-style tape --warmth-band lowmid"
                              f" --vocal-level 0 --reverb-seconds 1.6 --predelay 30 --delay-note 1/8")
    # 영상이 아니니 다듬은 영상은 없어도 된다
    end_job(ctx, job["id"], [f"{d}/po.audio.flac", f"{d}/po.listen.mp3", f"{d}/po.vocals.flac", f"{d}/po.backing.flac", f"{d}/po.report.json"])
    po = detail(client, alice, run["id"])["nodes"]["po"]
    assert po["status"] == "succeeded" and {"audio", "vocals", "backing"} <= set(po["outputs"])
    # 목소리만 다듬으면 나누지 않으니 GPU 를 조금만 잡는다
    up = client.post("/api/flow/upload", files={"file": ("말.wav", io.BytesIO(WAV), "audio/wav")}, headers=alice).json()
    g = {"nodes": [{"id": "s", "type": "sound", "path": up["path"], "name": up["name"]},
                   {"id": "p", "type": "polish", "params": {"source": "voice"}}],
         "edges": [{"from": "s", "out": "audio", "to": "p", "in": "audio"}]}
    run2 = start(client, alice, g).json()
    assert ctx.sched.get(run2["nodes"]["p"]["job_id"])["vram_gb"] == gen.POLISH_CPU_VRAM_GB


def test_a_polish_node_needs_exactly_one_sound_or_video(env):
    client, ctx, admin, alice = env
    install(ctx, "polish")
    sound = {"id": "s", "type": "sound", "path": "flow-inputs/a.wav"}
    clip = {"id": "c", "type": "clip", "path": "flow-inputs/b.mp4"}
    node = {"id": "p", "type": "polish", "params": {}}
    cases = [
        ({"nodes": [node], "edges": []}, "다듬을 소리도 영상도 이어져 있지 않습니다"),
        ({"nodes": [sound, clip, node], "edges": [{"from": "s", "out": "audio", "to": "p", "in": "audio"},
                                                  {"from": "c", "out": "video", "to": "p", "in": "video"}]}, "소리와 영상이 함께"),
    ]
    for g, expect in cases:
        r = start(client, alice, g)
        assert r.status_code == 400 and expect in r.json()["detail"], (expect, r.text)


# ── 실행 스크립트가 바뀌면 지난 결과를 다시 쓰지 않는다 ──

def test_a_node_whose_runner_script_changed_runs_again_instead_of_reusing(env):
    """배포로 실행 스크립트가 고쳐지면 종류·설정·입력이 같아도 새로 만든다 (2026-11-16: 고치기 전 판의 결과를 계속 다시 썼다)."""
    client, ctx, admin, alice = env
    install(ctx, "image", "3d")
    first = start(client, alice, picture_chain()).json()
    d1 = first["dir"]
    end_job(ctx, first["nodes"]["n2"]["job_id"], [f"{d1}/n2.image.png", f"{d1}/n2.thumb.jpg"])
    end_job(ctx, detail(client, alice, first["id"])["nodes"]["n3"]["job_id"], [f"{d1}/n3.mesh.glb"])
    again = start(client, alice, picture_chain()).json()
    assert [again["nodes"][n]["status"] for n in ("n2", "n3")] == ["reused", "reused"]
    # 3D 실행 스크립트만 고치면 3D 노드만 다시 돈다 (그림 노드는 그대로 다시 쓴다)
    k = gen.KINDS["3d"]
    (ctx.s.users_root / "alice" / k.folder(ctx.s) / k.script(ctx.s)).write_text("#!/bin/bash\n# 정면을 고친 판\n")
    third = start(client, alice, picture_chain()).json()
    assert third["nodes"]["n2"]["status"] == "reused" and third["nodes"]["n3"]["status"] == "queued"


def test_the_runner_fingerprint_reads_shared_files_behind_links_and_nothing_else(env, tmp_path):
    client, ctx, admin, alice = env
    install(ctx, "3d")
    user = ctx.db.get_user("alice")
    k = gen.KINDS["3d"]
    home = ctx.s.users_root / "alice" / k.folder(ctx.s)
    apps = ctx.s.shared_root / "models" / "apps" / k.folder(ctx.s)
    apps.mkdir(parents=True, exist_ok=True)
    (apps / "pixal3d_run.py").write_text("v1")
    (home / "pixal3d_run.py").symlink_to("/shared/models/apps/pixal3d-run/pixal3d_run.py")   # 워크스페이스 안의 경로
    outside = tmp_path / "outside.txt"
    outside.write_text("a")
    (home / "notes").symlink_to(outside)                   # 공용 실행 파일이 아닌 곳을 가리키는 링크
    (home / "MV-Adapter").symlink_to("/shared/models/apps/pixal3d-run")                      # 폴더 링크
    first = gen.runner_fingerprint(ctx.s, user, k)
    assert first
    outside.write_text("changed elsewhere")
    assert gen.runner_fingerprint(ctx.s, user, k) == first            # 공용 실행 파일 밖은 읽지 않는다
    (apps / "pixal3d_run.py").write_text("version 2 of the runner")
    assert gen.runner_fingerprint(ctx.s, user, k) != first            # 링크 뒤의 공용 파일이 바뀌면 지문이 바뀐다
    # 홈에 생성기 폴더가 없으면 빈 지문 (설정과 입력만으로 다시 쓸지 정한다)
    assert gen.runner_fingerprint(ctx.s, user, gen.KINDS["music"]) == ""
