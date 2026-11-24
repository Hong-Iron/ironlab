"""여러 방향 그림 (사진 한 장 → MV-Adapter 로 6·4방향 그림 → 묶음 → 3D) 의 콘솔 쪽과 두 실행 스크립트의 묶음 처리."""

import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

from ironlab import gen
from test_gen import PNG, finish, install_script, key_of, post, runs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MV = "--views 6 --steps 50 --guidance 3 --ref-scale 1"
SIDES = " ".join(f"--out-{k} outputs/{{key}}.{k}.png" for k, _ in gen.MV_VIEWS)


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 콘솔: 쉬운 메뉴 ──

def test_multiview_takes_one_photo_and_names_every_side_file(env):
    client, ctx, admin, alice = env
    d = install_script(ctx, "multiview")
    r = post(client, alice, "multiview", files=[("dog photo.JPG", PNG)], texts={"prompt": "a dog"}, params={"seed": 21})
    assert r.status_code == 200, r.text
    run = r.json()
    key = key_of(run)
    job = ctx.sched.get(run["job_id"])
    assert job["name"] == "여러 방향 그림" and job["workdir"] == "mvadapter-run" and job["vram_gb"] == 17
    assert job["command"] == (f"./mvadapter.sh --prompt-file inputs/{key}.prompt.txt --image inputs/{key}-1.jpg"
                              f" --output outputs/{key}.png --thumb outputs/{key}.thumb.jpg --views-out outputs/{key}.views.json "
                              + SIDES.format(key=key) + f" --seed 21 {DEFAULT_MV}")
    assert (d / "inputs" / f"{key}.prompt.txt").read_text(encoding="utf-8") == "a dog"
    assert run["params"] == {"seed": 21, "views": "6", "steps": 50, "guidance": 3.0, "ref_scale": 1.0}
    # 결과: 모은 그림 + 4방향이면 생긴 곁 파일만 목록에 나온다
    home = ctx.s.users_root / "alice"
    (home / run["output"]).write_bytes(b"x" * 300)
    stem = run["output"][: -len(".png")]
    for tail in (".views.json", ".front.png", ".right.png", ".back.png", ".left.png"):
        (home / f"{stem}{tail}").write_bytes(b"y")
    finish(ctx, run)
    cur = runs(client, alice, "multiview")[0]
    assert cur["status"] == "succeeded" and sorted(cur["extras"]) == [".back.png", ".front.png", ".left.png", ".right.png", ".views.json"]
    assert client.delete(f"/api/gen/multiview/{run['id']}", headers=alice).status_code == 200
    assert not any((home / "mvadapter-run" / "outputs").iterdir())


def test_multiview_needs_exactly_one_photo_and_the_description_is_optional(env):
    client, ctx, admin, alice = env
    install_script(ctx, "multiview")
    assert post(client, alice, "multiview").status_code == 400
    assert post(client, alice, "multiview", files=[("a.png", PNG), ("b.png", PNG)]).status_code == 400
    run = post(client, alice, "multiview", files=[("a.png", PNG)], params={"views": "4"}).json()
    cmd = ctx.sched.get(run["job_id"])["command"]
    assert "--prompt-file" not in cmd and " --views 4 " in cmd
    bad = post(client, alice, "multiview", files=[("a.png", PNG)], params={"views": "5"})
    assert bad.status_code == 400


def test_multiview_progress_reads_its_own_lines_and_the_drawing_bar():
    p = gen.progress_mv
    assert p("") is None and p("Loading pipeline components...: 100%|###| 7/7\n") is None
    assert p("[mv] 배경 지우기: 사진의 배경을 지웁니다 (BiRefNet)\n")["stage"] == "배경 지우기"
    assert p("[mv] 모델 올리기: SDXL + MV-Adapter (6방향)\nLoading pipeline components...: 100%|###| 7/7\n")["step"] == 2
    bar = "[mv] 시작: front, …\n  0%|          | 0/50 [00:00<?, ?it/s]\r 42%|####2     | 21/50 [00:25<00:35]"
    assert p(bar) == {"stage": "그리기", "step": 3, "steps": 4, "percent": 42}
    assert p("[mv] 끝: outputs/x.png (6방향 768px)\n")["stage"] == "그림 파일 쓰기"


# ── 실행 스크립트: 묶음 쓰기와 읽기 ──

def test_mv_views_writes_relative_paths_and_default_side_names(tmp_path):
    mv = load("deploy/genmv/mv_views.py", "mv_views")
    args = mv.build_parser().parse_args(["--image", "p.png", "--output", str(tmp_path / "o" / "x.png"), "--seed", "3"])
    paths = mv.out_paths(args, mv.VIEW_SETS["6"])
    assert paths["front-right"] == str(tmp_path / "o" / "x.front-right.png")
    b = mv.bundle(mv.VIEW_SETS["6"], paths, str(tmp_path / "o" / "x.views.json"), args)
    assert [(v["name"], v["azimuth_deg"], v["file"]) for v in b["views"]][:3] == [
        ("front", 0, "x.front.png"), ("front-right", 45, "x.front-right.png"), ("right", 90, "x.right.png")]
    assert b["projection"] == "orthographic" and b["half_width"] == 0.55 and b["seed"] == 3
    # 4방향은 앞·오른쪽·뒤·왼쪽
    assert mv.VIEW_SETS["4"] == ("front", "right", "back", "left")
    # 설명 글: 비우면 기본 글, 영어가 아닌 글자는 알린다
    a2 = mv.build_parser().parse_args(["--image", "p.png", "--output", "x.png", "--prompt", "  "])
    assert mv.describe(a2) == mv.DEFAULT_TEXT


def test_mv_views_and_the_3d_runner_agree_on_directions():
    mv = load("deploy/genmv/mv_views.py", "mv_views")
    p3 = load("deploy/gen3d/pixal3d_run.py", "pixal3d_run")
    assert mv.AZIMUTHS == {k: p3.AZIMUTH[k] for k in mv.AZIMUTHS}
    assert [k for k, _ in gen.MV_VIEWS] == list(mv.AZIMUTHS)
    # 45° 카메라는 앞(0, -d, 0)과 오른쪽(d, 0, 0) 사이, 같은 거리에 있다
    m = p3.camera_to_world("front-right", 2.0)
    pos = [row[3] for row in m[:3]]
    assert pos[0] == pytest.approx(2 ** 0.5) and pos[1] == pytest.approx(-(2 ** 0.5)) and pos[2] == pytest.approx(0)
    # 사진 길(--views)은 45° 방향을 받지 않는다 (사람이 찍은 사진의 방향은 앞·옆·뒤·위·아래만)
    with pytest.raises(ValueError):
        p3.check_views(["front", "front-right"], 2)


def write_bundle(tmp_path, views=(("front", 0), ("right", 90), ("back", 180)), **extra):
    for name, _ in views:
        (tmp_path / f"x.{name}.png").write_bytes(PNG)
    data = {"version": 1, "projection": "orthographic", "half_width": 0.55,
            "views": [{"name": n, "azimuth_deg": a, "file": f"x.{n}.png"} for n, a in views]} | extra
    p = tmp_path / "x.views.json"
    p.write_text(json.dumps(data))
    return p


def test_3d_runner_reads_a_bundle_and_refuses_broken_ones(tmp_path):
    p3 = load("deploy/gen3d/pixal3d_run.py", "pixal3d_run")
    b = p3.load_bundle(str(write_bundle(tmp_path)))
    assert b["views"] == ["front", "right", "back"] and b["half_width"] == 0.55
    assert b["images"][0] == str(tmp_path / "x.front.png")
    with pytest.raises(ValueError, match="약속과 다릅니다"):
        p3.load_bundle(str(write_bundle(tmp_path, views=(("front", 0), ("right", 270)))))
    with pytest.raises(ValueError, match="front 부터"):
        p3.load_bundle(str(write_bundle(tmp_path, views=(("back", 180), ("front", 0)))))
    with pytest.raises(ValueError, match="형식"):
        p3.load_bundle(str(write_bundle(tmp_path, projection="perspective")))
    good = write_bundle(tmp_path)
    (tmp_path / "x.right.png").unlink()
    with pytest.raises(ValueError, match="그림이 없습니다"):
        p3.load_bundle(str(good))


def test_3d_runner_keeps_the_bundle_frame_instead_of_recentering():
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    p3 = load("deploy/gen3d/pixal3d_run.py", "pixal3d_run")
    a = np.zeros((768, 768, 4), np.uint8)
    a[300:500, 500:700] = 255                     # 물체가 오른쪽으로 치우친 옆모습
    side = Image.fromarray(a, "RGBA")
    kept, info = p3.keep_frames([side])
    assert kept[0].size == (p3.FRAME_SIZE, p3.FRAME_SIZE)
    alpha = np.asarray(kept[0].getchannel("A"))
    xs = np.nonzero(alpha.max(0) > 128)[0]
    assert xs.min() > p3.FRAME_SIZE * 0.6          # 그대로 오른쪽에 있다 (다시 자르면 가운데로 온다)
    framed, _ = p3.frame_views([side])
    xs2 = np.nonzero(np.asarray(framed[0].getchannel("A")).max(0) > 128)[0]
    assert abs((xs2.min() + xs2.max()) / 2 - p3.FRAME_SIZE / 2) < 20


# ── 콘솔: 노드 편집기 ──

def mv_graph(photo, with_text=True):
    nodes = [{"id": "n1", "type": "picture", "path": photo}, {"id": "n3", "type": "multiview", "params": {"seed": 5}},
             {"id": "n4", "type": "mesh_views", "params": {}}]
    edges = [{"from": "n1", "out": "image", "to": "n3", "in": "image"}, {"from": "n3", "out": "views", "to": "n4", "in": "views"}]
    if with_text:
        nodes.append({"id": "n2", "type": "text", "text": "a dog"})
        edges.append({"from": "n2", "out": "text", "to": "n3", "in": "prompt"})
    return {"nodes": nodes, "edges": edges}


def upload(client, who):
    r = client.post("/api/flow/upload", files={"file": ("dog.png", io.BytesIO(PNG), "image/png")}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()["path"]


def test_flow_passes_the_bundle_from_the_multiview_node_to_the_3d_node(env):
    client, ctx, admin, alice = env
    from test_flow import detail, end_job, install, start

    install(ctx, "multiview")
    install(ctx, "3d", script="#!/bin/bash\n# --views-json <묶음>\n")
    run = start(client, alice, mv_graph(upload(client, alice))).json()
    d = run["dir"]
    job = ctx.sched.get(run["nodes"]["n3"]["job_id"])
    assert job["name"] == "여러 방향 그리기 (노드)" and job["workdir"] == "mvadapter-run" and job["vram_gb"] == 17
    assert job["command"] == (f"./mvadapter.sh --image ../{run['nodes']['n1']['outputs']['image']['path']} --prompt-file ../{d}/n2.text.txt"
                              f" --output ../{d}/n3.image.png --views-out ../{d}/n3.views.json "
                              + " ".join(f"--out-{k} ../{d}/n3.{k}.png" for k, _ in gen.MV_VIEWS)
                              + f" --thumb ../{d}/n3.thumb.jpg --seed 5 {DEFAULT_MV}")
    assert run["nodes"]["n4"]["status"] == "waiting"
    # 4방향 그림이면 45° 둘은 생기지 않아도 노드가 끝난다 (없어도 되는 출력)
    end_job(ctx, job["id"], [f"{d}/n3.image.png", f"{d}/n3.views.json", f"{d}/n3.thumb.jpg"]
            + [f"{d}/n3.{k}.png" for k in ("front", "right", "back", "left")])
    run = detail(client, alice, run["id"])
    assert run["nodes"]["n3"]["status"] == "succeeded" and "front-right" not in run["nodes"]["n3"]["outputs"]
    job3d = ctx.sched.get(run["nodes"]["n4"]["job_id"])
    assert job3d["name"] == "3D 만들기 (여러 방향 그림) (노드)" and job3d["workdir"] == "pixal3d-run" and job3d["vram_gb"] == 20
    assert job3d["command"].startswith(f"./pixal3d.sh --views-json ../{d}/n3.views.json --output ../{d}/n4.mesh.glb --seed 42 ")
    assert "--image" not in job3d["command"]


def test_flow_refuses_the_bundle_3d_node_with_an_old_3d_runner(env):
    client, ctx, admin, alice = env
    from test_flow import detail, end_job, install, start

    install(ctx, "multiview", "3d")               # 예전 판 pixal3d.sh: --views-json 을 모른다
    run = start(client, alice, mv_graph(upload(client, alice), with_text=False)).json()
    d = run["dir"]
    job = ctx.sched.get(run["nodes"]["n3"]["job_id"])
    assert "--prompt-file" not in job["command"]
    end_job(ctx, job["id"], [f"{d}/n3.image.png", f"{d}/n3.views.json"]
            + [f"{d}/n3.{k}.png" for k, _ in gen.MV_VIEWS])
    run = detail(client, alice, run["id"])
    n4 = run["nodes"]["n4"]
    assert n4["status"] == "failed" and "예전 판" in n4["error"] and "--views-json" in n4["error"]


def test_a_bundle_only_connects_to_a_bundle_input(env):
    client, ctx, admin, alice = env
    from test_flow import install, start

    install(ctx, "multiview", "3d")
    g = mv_graph(upload(client, alice))
    g["nodes"].append({"id": "m", "type": "mesh", "params": {}})
    g["edges"].append({"from": "n3", "out": "views", "to": "m", "in": "image"})
    r = start(client, alice, g)
    assert r.status_code == 400 and "여러 방향 묶음" in r.json()["detail"] and "같은 종류끼리만" in r.json()["detail"]
