"""3D 생성 (모양 먼저): 사진 한 장 3D 의 모양 위에 MV-Adapter(ig2mv)로 여섯 방향 색을 칠해 입힌다.
콘솔 쪽(노드, 쉬운 메뉴 주소, 진행 읽기)과 실행 스크립트가 잘못된 옵션을 GPU 를 쓰기 전에 거절하는지를 본다.
모델을 돌리는 부분(cutout·geo_views·ig2mv_paint·bake)은 GPU 서버에서 확인했다 (README '3D 생성 (모양 먼저)')."""

import io
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ironlab import gen
from test_gen import PNG, finish, install_script, key_of, post, runs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "genshape" / "shapefirst.sh"
DEFAULT = "--seed 42 --paint-seed 1 --color auto --faces 200000 --texture-size 2048"


def upload(client, who):
    r = client.post("/api/flow/upload", files={"file": ("cow.png", io.BytesIO(PNG), "image/png")}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()["path"]


def shape_graph(photo, with_text=True):
    nodes = [{"id": "n1", "type": "picture", "path": photo}, {"id": "n3", "type": "mesh_shape", "params": {}}]
    edges = [{"from": "n1", "out": "image", "to": "n3", "in": "image"}]
    if with_text:
        nodes.append({"id": "n2", "type": "text", "text": "a cow"})
        edges.append({"from": "n2", "out": "text", "to": "n3", "in": "prompt"})
    return {"nodes": nodes, "edges": edges}


# ── 노드 편집기 ──

def test_the_node_takes_a_photo_and_an_optional_description_and_gives_a_mesh(env):
    client, ctx, admin, alice = env
    types = {t["key"]: t for t in client.get("/api/flow", headers=alice).json()["types"]}
    t = types["mesh_shape"]
    assert t["title"] == "3D 만들기 (모양 먼저)" and t["kind"] == "shapefirst"
    assert [(i["key"], i["type"], i["required"]) for i in t["inputs"]] == [("image", "image", True), ("prompt", "text", False)]
    assert [(o["key"], o["type"]) for o in t["outputs"]] == [("mesh", "mesh"), ("painted", "image")]   # 작은 미리보기는 숨은 출력
    assert [p["key"] for p in t["params"]] == ["seed", "paint_seed", "color", "faces", "texture_size"]
    assert t["vram_gb"] == 18 and t["ready"] is False and "shapefirst-run/shapefirst.sh" in t["hint"]
    # 사진 한 장일 때 권하는 노드가 이것임을 옛 노드의 설명이 알린다
    assert "모양 먼저" in types["mesh_mv"]["help"] and "모양 먼저" in types["mesh_views"]["help"]


def test_flow_runs_the_shape_first_node_with_relative_paths_and_default_settings(env):
    client, ctx, admin, alice = env
    from test_flow import detail, end_job, install, start

    install(ctx, "shapefirst")
    run = start(client, alice, shape_graph(upload(client, alice))).json()
    d = run["dir"]
    job = ctx.sched.get(run["nodes"]["n3"]["job_id"])
    assert job["name"] == "3D 만들기 (모양 먼저) (노드)" and job["workdir"] == "shapefirst-run" and job["vram_gb"] == 18
    assert job["command"] == (f"./shapefirst.sh --image ../{run['nodes']['n1']['outputs']['image']['path']}"
                              f" --prompt-file ../{d}/n2.text.txt --output ../{d}/n3.mesh.glb"
                              f" --views-image ../{d}/n3.painted.png --thumb ../{d}/n3.thumb.jpg {DEFAULT}")
    end_job(ctx, job["id"], [f"{d}/n3.mesh.glb", f"{d}/n3.painted.png", f"{d}/n3.thumb.jpg"])
    run = detail(client, alice, run["id"])
    assert run["nodes"]["n3"]["status"] == "succeeded" and run["status"] == "succeeded"
    assert set(run["nodes"]["n3"]["outputs"]) == {"mesh", "painted", "thumb"}     # 미리보기도 남는다 (잇지 않을 뿐)


def test_flow_passes_the_chosen_settings_and_leaves_out_an_unlinked_description(env):
    client, ctx, admin, alice = env
    from test_flow import install, start

    install(ctx, "shapefirst")
    g = shape_graph(upload(client, alice), with_text=False)
    g["nodes"][1]["params"] = {"seed": 7, "paint_seed": 3, "color": "overlap", "faces": 100000, "texture_size": "1024"}
    run = start(client, alice, g).json()
    cmd = ctx.sched.get(run["nodes"]["n3"]["job_id"])["command"]
    assert "--prompt-file" not in cmd
    assert cmd.endswith("--seed 7 --paint-seed 3 --color overlap --faces 100000 --texture-size 1024")
    g["nodes"][1]["params"] = {"color": "brightest"}
    bad = start(client, alice, g)
    assert bad.status_code == 400 and "색 맞추기" in bad.json()["detail"]


def test_a_mesh_from_the_shape_first_node_does_not_connect_to_a_photo_input(env):
    client, ctx, admin, alice = env
    from test_flow import install, start

    install(ctx, "shapefirst", "3d")
    g = shape_graph(upload(client, alice))
    g["nodes"].append({"id": "m", "type": "mesh", "params": {}})
    g["edges"].append({"from": "n3", "out": "mesh", "to": "m", "in": "image"})
    r = start(client, alice, g)
    assert r.status_code == 400 and "같은 종류끼리만" in r.json()["detail"]


# ── 쉬운 메뉴 주소 (화면은 아직 없지만 같은 정의로 받는다) ──

def test_the_api_takes_one_photo_and_names_the_side_files(env):
    client, ctx, admin, alice = env
    install_script(ctx, "shapefirst")
    r = post(client, alice, "shapefirst", files=[("cow photo.PNG", PNG)], texts={"prompt": "a cow"}, params={"paint_seed": 3})
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "3D 생성 (모양 먼저)" and job["workdir"] == "shapefirst-run"
    assert job["command"] == (f"./shapefirst.sh --prompt-file inputs/{key}.prompt.txt --image inputs/{key}-1.png"
                              f" --output outputs/{key}.glb --thumb outputs/{key}.thumb.jpg --views-image outputs/{key}.painted.png"
                              " --seed 42 --paint-seed 3 --color auto --faces 200000 --texture-size 2048")
    home = ctx.s.users_root / "alice"
    (home / run["output"]).write_bytes(b"x" * 300)
    (home / run["output"].replace(".glb", ".painted.png")).write_bytes(b"y")
    finish(ctx, run)
    cur = runs(client, alice, "shapefirst")[0]
    assert cur["status"] == "succeeded" and sorted(cur["extras"]) == [".painted.png"]
    assert post(client, alice, "shapefirst", files=[("a.png", PNG), ("b.png", PNG)]).status_code == 400
    assert post(client, alice, "shapefirst", files=[("a.png", PNG)], params={"texture_size": "4096"}).status_code == 400


# ── 진행 읽기 ──

def test_progress_reads_the_stage_lines_and_the_bars_inside_them():
    p = gen.progress_shape
    assert p("") is None
    assert p("[shape] 단계 1/5 배경 지우기 (앞 단계까지 0 s)\n") == {"stage": "배경 지우기", "step": 1, "steps": 5, "percent": None}
    pix = "[shape] 단계 2/5 사진 한 장 3D (앞 단계까지 4 s)\n[Pipeline] Loading from x\nSampling shape SLat: 42%|####2 | 5/12\n"
    assert p(pix) == {"stage": "사진 한 장 3D · 형상 만들기", "step": 2, "steps": 5, "percent": 42}
    # Pixal3D 가 줄을 많이 찍어 단계 줄이 로그 끝에서 밀려나도 그 막대로 단계를 안다
    assert p("Sampling texture SLat: 10%|# | 1/12\n")["step"] == 2
    paint = ("[shape] 단계 4/5 색 칠하기 (앞 단계까지 120 s)\n[ig2mv] 설명: 'a cow, high quality'\n"
             "Loading pipeline components...: 100%|###| 7/7\n  0%|          | 0/50\r 64%|######4   | 32/50 [00:36<00:20]")
    assert p(paint) == {"stage": "색 칠하기", "step": 4, "steps": 5, "percent": 64}
    assert p("[shape] 단계 5/5 텍스처 입히기 (앞 단계까지 199 s)\n[bake] 색 맞추기 점수 …\n")["step"] == 5
    assert p("[bake] 끝: …\n[shape] 끝: /x/out.glb (12M), 전체 214 s\n")["step"] == 5


# ── 실행 스크립트: GPU 를 쓰기 전에 거절하는 것 ──

@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 가 없다")
@pytest.mark.parametrize("args, says", [
    ([], "--image <사진> 과 --output <결과.glb> 를 함께"),
    (["--image", "PHOTO", "--output", "out.png"], ".glb 로 끝나야"),
    (["--image", "PHOTO", "--output", "o.glb", "--color", "bright"], "--color 는 auto, none, group, overlap"),
    (["--image", "PHOTO", "--output", "o.glb", "--texture-size", "4096"], "4096 은 시험하지 않았습니다"),
    (["--image", "PHOTO", "--output", "o.glb", "--seed", "-1"], "0 이상의 정수"),
    (["--image", "PHOTO", "--output", "o.glb", "--views", "6"], "모르는 옵션입니다 (받은 값 '--views')"),
    (["--image", "PHOTO", "--output", "o.glb"], "사진 한 장 3D 실행 스크립트가 없습니다"),
    (["--image", "nope.png", "--output", "o.glb"], "사진이 없습니다 (받은 경로 nope.png)"),
])
def test_the_runner_refuses_bad_options_before_using_the_gpu(tmp_path, args, says):
    photo = tmp_path / "cow.png"
    photo.write_bytes(PNG)
    env = dict(os.environ, HOME=str(tmp_path))          # 빈 홈: 3D 생성기도 MV-Adapter 도 없다
    r = subprocess.run(["bash", str(SCRIPT), *[str(photo) if a == "PHOTO" else a for a in args]],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=30)
    assert r.returncode == 2 and says in r.stderr, r.stderr
    assert not list(tmp_path.glob("*.work"))           # 거절하면 작업 폴더도 만들지 않는다


def test_the_runner_and_the_kind_agree_on_option_names():
    text = SCRIPT.read_text(encoding="utf-8")
    flags = {p.flag for p in gen.KIND_SHAPE.params} | {"--image", "--output", "--prompt-file", "--thumb", "--views-image"}
    for flag in flags:
        assert re.search(rf"^\s+{re.escape(flag)}\)", text, re.M), flag


def test_the_clean_up_example_graph_runs_the_image_node_first_then_the_shape_first_node(env):
    """화면의 본보기 '배경이 복잡한 사진 → 물체만 남긴 그림 → 3D (모양 먼저)' 와 같은 그래프 (app.js FLOW_EXAMPLES)."""
    client, ctx, admin, alice = env
    from test_flow import detail, end_job, install, start

    install(ctx, "image", "shapefirst")
    g = {"nodes": [{"id": "n1", "type": "picture", "path": upload(client, alice)},
                   {"id": "n2", "type": "text", "text": "Isolate the main object in this photo as a single standalone model."},
                   {"id": "n3", "type": "image", "params": {"size": "1024x1024", "seed": 1}},
                   {"id": "n4", "type": "text", "text": "a high quality object"},
                   {"id": "n5", "type": "mesh_shape", "params": {}}],
         "edges": [{"from": "n1", "out": "image", "to": "n3", "in": "refs"}, {"from": "n2", "out": "text", "to": "n3", "in": "prompt"},
                   {"from": "n3", "out": "image", "to": "n5", "in": "image"}, {"from": "n4", "out": "text", "to": "n5", "in": "prompt"}]}
    run = start(client, alice, g).json()
    d = run["dir"]
    assert run["nodes"]["n5"]["status"] == "waiting"
    img = ctx.sched.get(run["nodes"]["n3"]["job_id"])
    assert img["workdir"] == "qwen-image-run" and " --size 1024x1024 " in img["command"] and " --seed 1 " in img["command"]
    end_job(ctx, img["id"], [f"{d}/n3.image.png", f"{d}/n3.thumb.jpg"])
    run = detail(client, alice, run["id"])
    job = ctx.sched.get(run["nodes"]["n5"]["job_id"])
    assert job["command"].startswith(f"./shapefirst.sh --image ../{d}/n3.image.png --prompt-file ../{d}/n4.text.txt ")
