"""Pixal3D 로 사진을 3D 모델 파일(GLB)로 만든다. 한 장도, 여러 방향에서 찍은 여러 장도 받는다.

    python pixal3d_run.py --image cat.png --output cat.glb
    python pixal3d_run.py --image cat.png --output cat.glb --shape-steps 20 --faces 100000 --texture-size 2048
    python pixal3d_run.py --image front.jpg --image right.jpg --image back.jpg --views front,right,back --output cat.glb

Pixal3D 는 네 단계로 만든다.

1. 뼈대 잡기 (--ss-*):     물체가 공간의 어느 칸을 차지하는지 정한다.
2. 형상 만들기 (--shape-*): 그 칸들의 겉면 모양을 만든다.
3. 색 입히기 (--tex-*):    겉면의 색과 재질을 만든다.
4. GLB 뽑기 (--faces, --texture-size, --remesh): 삼각형 메시와 텍스처 그림으로 바꿔 파일로 쓴다.

사진이 한 장이면 Pixal3D 저장소의 inference.py 가 하는 대로 돈다. 카메라의 화각은 사진에서 짐작한다.

사진이 여러 장이면 inference_mv.py 의 길로 돈다. 단계마다 모든 사진을 한꺼번에 본다. 이 길은 사진마다 "어디서 찍었는지"
(카메라 자리)를 받아야 한다. 이 스크립트는 --views 로 받은 방향(앞, 오른쪽, 뒤, 왼쪽, 위, 아래)에서 카메라 자리를 계산하고,
사진들의 크기를 하나의 배율로 맞춘다 (frame_views). 그래서 사진은 같은 거리, 같은 줌으로 찍어야 한다.
여러 장 길은 따로 학습된 모델 파일(ckpts/*_mv, pipeline_mv.json)을 쓴다.

--views-json 으로 여러 방향 그림 묶음(mvadapter.sh 가 사진 한 장에서 그린 6·4방향)을 받으면, 묶음에 적힌 방향과 틀을 그대로 쓴다.
그림을 다시 자르지 않고(keep_frames), 원근 없이 그린 그림에 맞춰 좁은 화각(BUNDLE_FOV_DEG)으로 카메라를 둔다.
여러 장 길은 사진들의 특징을 3D 공간에 비춰 평균 낸다. 서로 맞지 않는 그림(방향마다 따로 그린 각도 바꾸기 그림)을 넣으면
앞 사진의 얼굴이 뒷머리에 비치는 것처럼 망가진다 (2026-11-16 개 사진으로 확인).

Pixal3D 저장소의 두 스크립트는 1~3단계의 값을 함수 인자로는 받지만 명령 옵션으로는 내놓지 않고, 4단계의 값은 코드에 적어 두었다.
이 스크립트는 그 함수들을 그대로 부르고 값만 넘긴다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile

# (옵션 머리, run_inference 인자 머리, 설명, 단계 수, 가이던스, 가이던스 누르기, 앞 구간 몰기) — 기본값은 inference.py 와 같다
STAGES = (
    ("ss", "ss", "뼈대 잡기", 12, 7.5, 0.7, 5.0),
    ("shape", "shape_slat", "형상 만들기", 12, 7.5, 0.5, 3.0),
    ("tex", "tex_slat", "색 입히기", 12, 1.0, 0.0, 3.0),
)

# 방향 → 이름. 앞에서 물체를 보고 선 사람을 기준으로 한다.
#   오른쪽: 그 사람이 자기 오른쪽으로 90° 돌아가서 찍은 사진. 뒤: 180°. 왼쪽: 270°.
#   위: 물체 바로 위에서 내려다본 사진. 물체의 앞쪽이 사진의 아래쪽에 오게 찍는다.
#   아래: 물체 바로 밑에서 올려다본 사진. 이때도 물체의 앞쪽이 사진의 아래쪽에 오게 찍는다.
VIEWS = {"front": "앞", "right": "오른쪽", "back": "뒤", "left": "왼쪽", "top": "위", "bottom": "아래"}
AZIMUTH = {"front": 0, "front-right": 45, "right": 90, "back": 180, "left": 270, "front-left": 315}
MAX_VIEWS = len(VIEWS)
# 여러 방향 그림 묶음(--views-json, mv_views.py 가 쓴다)에서만 쓰는 45° 방향 이름
BUNDLE_VIEWS = {**VIEWS, "front-right": "오른쪽 앞(45°)", "front-left": "왼쪽 앞(315°)"}
# 묶음의 그림은 원근 없이(정사영) 그린 것이다. Pixal3D 는 원근 카메라를 받으므로 화각을 좁혀 비슷하게 맞춘다.
# 10° 는 Pixal3D 가 학습한 화각(10~70°)의 가장 좁은 끝이다. 개 사진 6방향으로 시험해 얼굴이 하나인 머리가 나왔다 (2026-11-16).
BUNDLE_FOV_DEG = 10.0
FRAME_MARGIN = 1.1              # 물체 둘레에 남기는 여백. inference.py 의 전처리(preprocess_image)와 같은 값
FRAME_SIZE = 1024               # 맞춘 사진의 한 변 (모델이 가장 크게 읽는 크기)
WORK_SIZE = 1536                # 크기를 맞추기 전에 사진의 긴 변을 이 길이로 맞춘다 (사진마다 해상도가 달라도 배율이 같게)


def say(text: str) -> None:
    print(f"[pixal3d] {text}", flush=True)


# ── 카메라 자리 ──

def camera_to_world(view: str, distance: float) -> list[list[float]]:
    """방향 하나의 카메라 자리 (4×4 camera-to-world, Pixal3D 의 transforms.json 과 같은 약속).

    세상은 Z 가 위다. 앞 카메라는 (0, -거리, 0)에서 원점을 본다. 카메라는 자기 -Z 쪽을 보고, 자기 +Y 가 사진의 위다.
    행렬의 열은 차례로 카메라의 오른쪽(X), 위(Y), 뒤(Z, 물체에서 카메라 쪽) 방향과 카메라 자리다.
    앞·오른쪽·뒤·왼쪽은 저장소 본보기(assets/mv_images/example/transforms.json)의 행렬과 같게 나온다.
    """
    if view in AZIMUTH:
        a = math.radians(AZIMUTH[view])
        s, c = round(math.sin(a), 12), round(math.cos(a), 12)
        right, up, back = (c, s, 0.0), (0.0, 0.0, 1.0), (s, -c, 0.0)
    elif view == "top":
        right, up, back = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)       # 사진 위쪽 = 물체의 뒤쪽
    elif view == "bottom":
        right, up, back = (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)     # 밑에서 보면 좌우가 뒤집힌다
    else:
        raise ValueError(f"모르는 방향입니다 (받은 값 {view!r}). {', '.join(VIEWS)} 가운데 하나여야 합니다")
    pos = tuple(distance * b + 0.0 for b in back)
    return [[right[i] + 0.0, up[i] + 0.0, back[i] + 0.0, pos[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def distance_for_fov(fov_rad: float) -> float:
    """화각이 fov 인 카메라가 한 변 1 인 정육면체를 화면 가로에 꽉 채우는 거리.

    inference.py 의 distance_from_fov 와 같은 식이다 (정육면체의 반폭 0.5 가 화면 가장자리에 온다).
    예: 화각 20° 면 0.5 / tan(10°) ≈ 2.84.
    """
    return 0.5 / math.tan(fov_rad / 2)


def check_views(views: list[str], n_images: int) -> None:
    if len(views) != n_images:
        raise ValueError(f"사진은 {n_images}장인데 방향은 {len(views)}개입니다 (받은 값 {','.join(views)}). 사진마다 방향을 하나씩 주세요")
    bad = [v for v in views if v not in VIEWS]
    if bad:
        raise ValueError(f"모르는 방향입니다 (받은 값 {', '.join(bad)}). {', '.join(VIEWS)} 가운데 하나여야 합니다")
    if len(set(views)) != len(views):
        raise ValueError(f"같은 방향이 두 번 나옵니다 (받은 값 {','.join(views)}). 방향마다 사진은 한 장입니다")
    if views[0] != "front":
        raise ValueError(f"첫 사진은 앞에서 찍은 사진이어야 합니다 (받은 값 {views[0]}). 다른 사진은 앞 사진을 기준으로 놓입니다")


# ── 사진 크기 맞추기 ──

def frame_views(images: list) -> tuple[list, list[dict]]:
    """배경을 지운 사진들(RGBA)을 하나의 배율로 잘라 FRAME_SIZE 정사각형으로 만든다.

    사진마다 물체를 꽉 채우게 자르면 안 된다. 긴 물체는 앞에서 보면 좁고 옆에서 보면 넓은데, 둘을 같은 크기로 늘리면
    모델이 받는 모양이 서로 어긋난다. 그래서 같은 거리·같은 줌으로 찍었다고 보고, 모든 사진을 같은 크기의 창으로 자른다.
    창의 한 변은 가장 크게 찍힌 사진의 물체 크기에 여백(FRAME_MARGIN)을 곱한 것이고, 창의 가운데는 사진마다 물체의 가운데다.
    예: 앞 사진의 물체가 400×600 px, 옆 사진이 800×600 px 이면 창은 880 px 이고, 앞 사진의 물체는 맞춘 사진 폭의 45% 를 차지한다.
    """
    import numpy as np
    from PIL import Image

    resized = []
    for im in images:
        k = WORK_SIZE / max(im.size)
        resized.append(im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.Resampling.LANCZOS))
    boxes = []
    for i, im in enumerate(resized):
        alpha = np.array(im.getchannel("A"))
        ys, xs = np.nonzero(alpha > 0.8 * 255)
        if len(xs) == 0:
            raise ValueError(f"{i + 1}번째 사진에서 물체를 찾지 못했습니다 (배경을 지우니 남은 것이 없습니다). 물체가 크게 보이게 다시 찍어 주세요")
        boxes.append((xs.min(), ys.min(), xs.max(), ys.max()))
    side = max(max(b[2] - b[0], b[3] - b[1]) for b in boxes) * FRAME_MARGIN
    out, info = [], []
    for im, (x0, y0, x1, y1) in zip(resized, boxes):
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        crop = im.crop((round(cx - side / 2), round(cy - side / 2), round(cx + side / 2), round(cy + side / 2)))  # 밖은 투명으로 채워진다
        out.append(crop.resize((FRAME_SIZE, FRAME_SIZE), Image.Resampling.LANCZOS))
        info.append({"object_px": (int(x1 - x0), int(y1 - y0)), "fill": round(max(x1 - x0, y1 - y0) / side, 3)})
    return out, info


# ── 실행 ──

def add_stage_args(ap: argparse.ArgumentParser) -> None:
    for flag, _, what, steps, guidance, rescale, rescale_t in STAGES:
        ap.add_argument(f"--{flag}-steps", type=int, default=steps, help=f"{what}: 잡음을 걷어 내는 횟수 (기본 {steps})")
        ap.add_argument(f"--{flag}-guidance", type=float, default=guidance,
                        help=f"{what}: 사진을 얼마나 세게 따를지 (기본 {guidance:g}, 1 이면 끔)")
        ap.add_argument(f"--{flag}-rescale", type=float, default=rescale,
                        help=f"{what}: 가이던스로 과해진 대비를 누르는 정도 0~1 (기본 {rescale:g})")
        ap.add_argument(f"--{flag}-rescale-t", type=float, default=rescale_t,
                        help=f"{what}: 단계를 잡음이 많은 앞 구간에 모는 정도 (기본 {rescale_t:g}, 1 이면 고르게)")


def stage_kwargs(args) -> dict:
    kw = {}
    for flag, name, *_ in STAGES:
        kw[f"{name}_sampling_steps"] = getattr(args, f"{flag}_steps")
        kw[f"{name}_guidance_strength"] = getattr(args, f"{flag}_guidance")
        kw[f"{name}_guidance_rescale"] = getattr(args, f"{flag}_rescale")
        kw[f"{name}_rescale_t"] = getattr(args, f"{flag}_rescale_t")
    return kw


def sampler_overrides(kw: dict) -> dict:
    """stage_kwargs 의 값을 run_mv 가 받는 꼴(단계마다 묶은 사전)로 바꾼다. inference_mv.run_inference 와 같은 꼴이다."""
    out = {}
    for _, name, *_ in STAGES:
        key = {"ss": "sparse_structure_sampler_params", "shape_slat": "shape_slat_sampler_params",
               "tex_slat": "tex_slat_sampler_params"}[name]
        out[key] = {"steps": kw[f"{name}_sampling_steps"], "guidance_strength": kw[f"{name}_guidance_strength"],
                    "guidance_rescale": kw[f"{name}_guidance_rescale"], "rescale_t": kw[f"{name}_rescale_t"]}
    return out


def use_user_autotune_cache(repo: str) -> None:
    """flex_gemm 이 조정 결과(autotune_cache.json)를 사용자 홈에 읽고 쓰게 한다.

    저장소 스크립트(inference.py)는 그 파일을 저장소 폴더에 두라고 정해 둔다. 저장소를 공용(읽기 전용)으로 옮기면
    flex_gemm 이 그 옆에 잠금 파일을 만들지 못해 불러오다 멈춘다. flex_gemm 은 경로를 처음 불러올 때 한 번만 읽으므로,
    저장소 스크립트보다 먼저 홈 경로로 불러 둔다. 처음이면 저장소에 있는 조정 결과를 복사해 다시 재지 않게 한다.
    """
    import shutil

    path = os.path.join(os.path.expanduser("~"), ".cache", "pixal3d", "autotune_cache.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seed = os.path.join(repo, "autotune_cache.json")
    if not os.path.exists(path) and os.path.isfile(seed):
        shutil.copyfile(seed, path)
    os.environ["FLEX_GEMM_AUTOTUNE_CACHE_PATH"] = path
    import flex_gemm  # noqa: F401  (경로를 여기서 정해 둔다)


def keep_frames(images: list) -> tuple[list, list[dict]]:
    """여러 방향 그림 묶음의 그림을 자르지 않고 FRAME_SIZE 로만 키운다.

    묶음의 그림은 모든 방향을 같은 틀(물체 가운데에서 같은 폭)로 그린 것이다. frame_views 처럼 그림마다 물체 가운데로
    다시 자르면, 옆모습처럼 물체가 한쪽으로 치우친 그림이 다른 그림과 어긋난다.
    """
    import numpy as np
    from PIL import Image

    out, info = [], []
    for im in images:
        alpha = np.array(im.getchannel("A"))
        ys, xs = np.nonzero(alpha > 0.8 * 255)
        w = int(xs.max() - xs.min()) if len(xs) else 0
        h = int(ys.max() - ys.min()) if len(ys) else 0
        out.append(im.resize((FRAME_SIZE, FRAME_SIZE), Image.Resampling.LANCZOS))
        info.append({"object_px": (w, h), "fill": round(max(w, h) / max(im.size), 3)})
    return out, info


def load_bundle(path: str) -> dict:
    """여러 방향 그림 묶음(mv_views.py 가 쓰는 JSON)을 읽고 검사한다. 틀리면 ValueError.

    돌려주는 값: {"images": [절대 경로…], "views": [방향 이름…], "half_width": 틀의 반폭}
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"여러 방향 그림 묶음을 읽지 못했습니다 ({path}): {e}") from None
    if data.get("version") != 1 or data.get("projection") != "orthographic":
        raise ValueError(f"모르는 묶음 형식입니다 (version {data.get('version')!r}, projection {data.get('projection')!r}). "
                         "mv_views.py 가 쓴 묶음이어야 합니다")
    half = float(data.get("half_width") or 0)
    if not 0.1 <= half <= 2.0:
        raise ValueError(f"묶음의 틀 반폭(half_width)이 이상합니다 (받은 값 {half})")
    base = os.path.dirname(os.path.abspath(path))
    images, views = [], []
    for v in data.get("views") or []:
        name, az = v.get("name"), v.get("azimuth_deg")
        if name not in AZIMUTH:
            raise ValueError(f"묶음에 모르는 방향이 있습니다 (받은 값 {name!r}). {', '.join(AZIMUTH)} 가운데 하나여야 합니다")
        if az is None or abs(float(az) - AZIMUTH[name]) > 1e-6:
            raise ValueError(f"묶음의 '{name}' 각도가 약속과 다릅니다 (받은 값 {az}, 약속 {AZIMUTH[name]}°)")
        p = os.path.normpath(os.path.join(base, v.get("file") or ""))
        if not os.path.isfile(p):
            raise ValueError(f"묶음의 '{name}' 그림이 없습니다 ({p})")
        images.append(p)
        views.append(name)
    if len(views) < 2 or views[0] != "front" or len(set(views)) != len(views):
        raise ValueError(f"묶음의 방향은 front 부터 서로 다른 둘 이상이어야 합니다 (받은 값 {', '.join(map(str, views)) or '없음'})")
    return {"images": images, "views": views, "half_width": half}


def run_multiview(args, images: list[str], views: list[str], model_path: str, stage_kw: dict, save_views: str | None,
                  bundle_half_width: float | None = None) -> None:
    """여러 장의 길. inference_mv.run_inference 와 같은 순서로 돌되, 사진의 배경을 지우고 크기를 맞추는 일을 앞에 더한다.

    bundle_half_width 가 있으면 여러 방향 그림 묶음이다: 그림을 다시 자르지 않고, 화각을 좁혀(기본 BUNDLE_FOV_DEG)
    카메라를 그 틀의 반폭이 화면 가장자리에 오는 거리에 둔다.
    """
    import inference as px1                   # 화각을 짐작하는 MoGe 함수
    import inference_mv as pxm
    import numpy as np
    import torch
    from PIL import Image

    pipeline = pxm.init_pipeline(model_path, "pipeline_mv.json", low_vram=args.low_vram)
    rembg = pxm.make_rembg(pipeline)
    loaded = []
    for path in images:
        im, matted = pxm.load_rgba(path, rembg)
        loaded.append(im)
        say(f"사진 읽기: {os.path.basename(path)} {im.width}x{im.height}{', 배경 지움' if matted else ', 투명 배경 그대로 씀'}")
    framed, info = keep_frames(loaded) if bundle_half_width else frame_views(loaded)
    say(("틀 그대로: " if bundle_half_width else "크기 맞추기: ")
        + ", ".join(f"{BUNDLE_VIEWS[v]} 물체 {i['object_px'][0]}x{i['object_px'][1]}px → 화면의 {i['fill']:.0%}"
                    for v, i in zip(views, info)))

    # 화각: 주면 그 값, 아니면 맞춘 앞 사진에서 MoGe 로 짐작한다 (한 장 길이 하는 것과 같다). 묶음은 좁은 화각을 기본으로 한다
    if bundle_half_width and args.fov_deg is None:
        fov = math.radians(BUNDLE_FOV_DEG)
        say(f"화각: 원근 없이 그린 묶음이라 좁은 화각 {BUNDLE_FOV_DEG:g}° 로 맞춥니다")
    elif args.fov_deg is not None:
        fov = math.radians(args.fov_deg)
        say(f"화각: 준 값 {args.fov_deg:g}°")
    else:
        front = framed[0]
        flat = Image.new("RGB", front.size, (0, 0, 0))
        flat.paste(front, mask=front.getchannel("A"))
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "front.png")
            flat.save(p)
            moge = px1.load_moge_model(device="cuda")
            fov = float(px1.get_camera_params_wild_moge(p, moge, device="cuda")["camera_angle_x"])
            moge.cpu()
            del moge
            torch.cuda.empty_cache()
        say(f"화각: 앞 사진에서 짐작한 값 {math.degrees(fov):.1f}°")
    # 묶음: 틀의 반폭(예: 0.55)이 화면 가장자리에 오는 거리. 사진: 한 변 1 인 정육면체의 반폭 0.5 가 가장자리에 오는 거리
    distance = bundle_half_width / math.tan(fov / 2) if bundle_half_width else distance_for_fov(fov)

    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, (v, im) in enumerate(zip(views, framed)):
            name = f"view{i:02d}_{v}.png"
            im.save(os.path.join(tmp, name))
            frames.append({"file_path": name, "name": v, "transform_matrix": camera_to_world(v, distance)})
        meta = {"camera_angle_x": fov, "mesh_scale": 1.0, "frames": frames}
        with open(os.path.join(tmp, "transforms.json"), "w") as f:
            json.dump(meta, f, indent=2)
        if save_views:                          # 모델이 받은 그대로를 남긴다 (사진이 어떻게 맞춰졌는지 볼 때)
            os.makedirs(save_views, exist_ok=True)
            for fr in frames:
                Image.open(os.path.join(tmp, fr["file_path"])).save(os.path.join(save_views, fr["file_path"]))
            with open(os.path.join(save_views, "transforms.json"), "w") as f:
                json.dump(meta, f, indent=2)
        bundle = pxm.load_views(tmp, None)
    pxm.check_main_view(bundle)

    print("[Inference] Running 3D generation pipeline...", flush=True)
    torch.manual_seed(args.seed)
    pipeline_type = f"{args.resolution if args.resolution > 0 else (1024 if args.low_vram else 1536)}_cascade"
    print(f"[Inference] Using pipeline_type={pipeline_type}", flush=True)
    mesh_list, (_, _, res) = pipeline.run_mv(bundle, seed=args.seed, return_latent=True, pipeline_type=pipeline_type,
                                             max_num_tokens=args.max_tokens, **sampler_overrides(stage_kw))
    mesh = mesh_list[0]
    print("[Inference] Extracting GLB...", flush=True)
    glb = pxm.o_voxel.postprocess.to_glb(
        vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
        coords=mesh.coords, attr_layout=pipeline.pbr_attr_layout,
        grid_size=res, aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=1000000, texture_size=4096,
        remesh=True, remesh_band=1, remesh_project=0, use_tqdm=True,
    )
    # 저장소 스크립트와 같은 회전: 모델 안의 좌표를 GLB 의 좌표(위가 +Y)로 돌린다
    glb.apply_transform(np.array([[-1, 0, 0, 0], [0, 0, -1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64))
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    glb.export(args.output, extension_webp=True)
    print(f"[Done] GLB saved to: {args.output}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Pixal3D 로 사진(한 장 또는 여러 방향의 여러 장)을 GLB 로 만든다")
    ap.add_argument("--image", action="append",
                    help=f"사진 파일. 여러 방향에서 찍었으면 여러 번 준다 (최대 {MAX_VIEWS}장, 첫 장은 앞에서 찍은 사진)")
    ap.add_argument("--views", help=f"사진마다 찍은 방향을 쉼표로 (예: front,right,back,left). 고를 수 있는 값: {', '.join(VIEWS)}. "
                                     "사진이 여러 장이면 꼭 준다")
    ap.add_argument("--views-json", help="여러 방향 그림 묶음 (mvadapter.sh 가 쓰는 .views.json). 주면 --image·--views 대신 "
                                         "묶음의 그림과 각도를 그대로 쓰고, 그림을 다시 자르지 않는다")
    ap.add_argument("--output", required=True, help="결과 GLB 경로")
    ap.add_argument("--seed", type=int, default=42)
    add_stage_args(ap)
    ap.add_argument("--max-tokens", type=int, default=49152, help="모양을 나타내는 조각(토큰) 수의 한도 (기본 49152)")
    ap.add_argument("--fov-deg", type=float, help="카메라의 가로 화각(도). 주지 않으면 사진(여러 장이면 앞 사진)에서 짐작한다")
    ap.add_argument("--faces", type=int, default=1_000_000, help="결과 메시의 삼각형 수 한도 (기본 1,000,000)")
    ap.add_argument("--texture-size", type=int, choices=(1024, 2048, 4096), default=4096, help="텍스처 그림의 한 변(픽셀)")
    ap.add_argument("--remesh", action=argparse.BooleanOptionalAction, default=True, help="삼각형을 고르게 다시 짠다 (기본 켬)")
    ap.add_argument("--model-path", default="TencentARC/Pixal3D", help="모델 설정이 있는 폴더나 Hugging Face 저장소 이름")
    ap.add_argument("--low-vram", action=argparse.BooleanOptionalAction, default=True,
                    help="쓰는 모델만 GPU 에 올린다 (기본 켬). 끄면 24 GB 카드에서 메모리가 모자란다")
    ap.add_argument("--resolution", type=int, default=-1, help="1024 또는 1536. 주지 않으면 low-vram 일 때 1024")
    ap.add_argument("--save-views", help="여러 장일 때, 크기를 맞춘 사진과 카메라 자리(transforms.json)를 이 폴더에 남긴다")
    ap.add_argument("--repo", default=os.environ.get("PIXAL3D_REPO", os.path.expanduser("~/Pixal3D")),
                    help="Pixal3D 저장소를 받아 둔 폴더")
    args = ap.parse_args()

    output, repo = (os.path.abspath(os.path.expanduser(p)) for p in (args.output, args.repo))
    args.output = output
    bundle_half = None
    if args.views_json:
        if args.image or args.views:
            ap.error("--views-json 을 주면 --image 와 --views 는 주지 않습니다 (묶음에 그림과 방향이 들어 있습니다)")
        try:
            b = load_bundle(os.path.abspath(os.path.expanduser(args.views_json)))
        except ValueError as e:
            ap.error(str(e))
        images, views, bundle_half = b["images"], b["views"], b["half_width"]
    else:
        if not args.image:
            ap.error("--image (사진) 나 --views-json (여러 방향 그림 묶음) 가운데 하나를 주세요")
        images = [os.path.abspath(os.path.expanduser(p)) for p in args.image]
        for p in images:
            if not os.path.isfile(p):
                ap.error(f"사진 파일이 없습니다 (받은 경로 {p})")
        if len(images) > MAX_VIEWS:
            ap.error(f"사진은 {MAX_VIEWS}장까지입니다 (받은 수 {len(images)}장)")
        views = [v.strip() for v in args.views.split(",") if v.strip()] if args.views else []
        if len(images) > 1 or views:
            try:
                check_views(views, len(images))
            except ValueError as e:
                ap.error(str(e))
    if not os.path.isfile(os.path.join(repo, "inference.py")):
        ap.error(f"Pixal3D 저장소를 찾지 못했습니다 (받은 경로 {repo}). --repo 나 PIXAL3D_REPO 로 알려 주세요")
    for flag, *_ in STAGES:
        steps = getattr(args, f"{flag}_steps")
        if not 1 <= steps <= 100:
            ap.error(f"--{flag}-steps 는 1~100 사이여야 합니다 (받은 값 {steps})")
    if not 1_000 <= args.faces <= 5_000_000:
        ap.error(f"--faces 는 1,000~5,000,000 사이여야 합니다 (받은 값 {args.faces})")
    if args.fov_deg is not None and not 1 <= args.fov_deg <= 170:
        ap.error(f"--fov-deg 는 1~170 도 사이여야 합니다 (받은 값 {args.fov_deg})")
    model_path = args.model_path
    if os.path.isdir(os.path.expanduser(model_path)):
        model_path = os.path.abspath(os.path.expanduser(model_path))
    if len(images) > 1 and os.path.isdir(model_path) and not os.path.isfile(os.path.join(model_path, "pipeline_mv.json")):
        ap.error(f"여러 장용 모델 설정이 없습니다 ({model_path}/pipeline_mv.json). deploy/gen3d/open-rembg/pipeline_mv.json 을 그 폴더에 두세요")
    os.makedirs(os.path.dirname(output), exist_ok=True)     # inference.py 가 결과 폴더에 임시 파일을 먼저 쓴다

    # 저장소의 스크립트들은 저장소 폴더에서 돌린다고 보고 짜여 있다 (pixal3d 패키지를 그 폴더에서 찾는다).
    os.chdir(repo)
    sys.path.insert(0, repo)
    use_user_autotune_cache(repo)
    import inference as px                                   # 무거운 모듈(torch 등)을 여기서 읽는다

    # 4단계의 값은 저장소 스크립트 안에 적혀 있다 (삼각형 1,000,000개, 텍스처 4096, 다시 짜기 켬).
    # GLB 를 만드는 함수를 감싸 그 세 값만 바꿔 넘긴다. 두 스크립트 모두 o_voxel.postprocess.to_glb 를 부른다.
    to_glb = px.o_voxel.postprocess.to_glb

    def to_glb_with_settings(*a, **kw):
        kw.update(decimation_target=args.faces, texture_size=args.texture_size, remesh=args.remesh)
        return to_glb(*a, **kw)

    px.o_voxel.postprocess.to_glb = to_glb_with_settings

    stage_kw = stage_kwargs(args)
    say("설정: 사진 {}장{}, 시드 {}, 단계 수 {}/{}/{}, 가이던스 {:g}/{:g}/{:g}, 조각 한도 {:,}, 화각 {}, 삼각형 {:,}개, 텍스처 {}px, 다시 짜기 {}".format(
        len(images), f" ({', '.join(BUNDLE_VIEWS[v] for v in views)}{', 여러 방향 그림 묶음' if bundle_half else ''})" if len(images) > 1 else "",
        args.seed, args.ss_steps, args.shape_steps, args.tex_steps, args.ss_guidance, args.shape_guidance, args.tex_guidance,
        args.max_tokens, f"{args.fov_deg:g}도" if args.fov_deg is not None else (f"{BUNDLE_FOV_DEG:g}도 (묶음)" if bundle_half else "사진에서 짐작"),
        args.faces, args.texture_size, "켬" if args.remesh else "끔"))
    if len(images) > 1:
        run_multiview(args, images, views, model_path, stage_kw,
                      os.path.abspath(os.path.expanduser(args.save_views)) if args.save_views else None,
                      bundle_half_width=bundle_half)
        return 0
    px.run_inference(
        image_path=images[0], output_path=output, seed=args.seed, max_num_tokens=args.max_tokens, model_path=model_path,
        manual_fov=math.radians(args.fov_deg) if args.fov_deg is not None else -1.0,
        low_vram=args.low_vram, resolution=args.resolution, **stage_kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
