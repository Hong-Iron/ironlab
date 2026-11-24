"""모양 먼저 3D 의 공용 코드: MV-Adapter 의 렌더러·카메라 약속을 trellis2 환경(Pixal3D 환경)에서 그대로 쓴다.

MV-Adapter 의 mvadapter.utils 패키지는 __init__ 에서 matplotlib 를 읽는데 trellis2 환경에는 없다.
그래서 패키지 껍데기만 sys.modules 에 넣고 render.py·camera.py 만 읽는다.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np
import torch
import trimesh
from PIL import Image

U = os.path.expanduser("~/mvadapter-run/MV-Adapter/mvadapter")   # 여러 방향 그림 생성기가 쓰는 MV-Adapter 코드 (공용 링크)
if "mvadapter" not in sys.modules:
    _m = types.ModuleType("mvadapter")
    _m.__path__ = [U]
    sys.modules["mvadapter"] = _m
    _u = types.ModuleType("mvadapter.utils")
    _u.__path__ = [U + "/utils"]
    sys.modules["mvadapter.utils"] = _u

from mvadapter.utils.camera import get_orthogonal_camera  # noqa: E402
from mvadapter.utils.render import NVDiffRastContextWrapper, TexturedMesh, render  # noqa: E402, F401 (다른 파일이 G.render 로 쓴다)

# ig2mv 저장소 스크립트와 같은 여섯 방향 (앞, 오른쪽, 뒤, 왼쪽, 위, 아래)
VIEWS = ["front", "right", "back", "left", "top", "bottom"]
ELEV = [0, 0, 0, 0, 89.99, -89.99]
AZ = [x - 90 for x in [0, 90, 180, 270, 180, 180]]
HALF = 0.55
SIZE = 768
DEV = "cuda"

# glTF 좌표(Y 위)에서 어느 축을 정면(+Z, MV-Adapter 의 front 카메라 쪽)으로 돌릴지
FRONT_DEG = {"+z": 0, "-x": 90, "-z": 180, "+x": -90}


def rot_y(front: str) -> np.ndarray:
    t = np.deg2rad(FRONT_DEG[front])
    return np.array([[np.cos(t), 0, np.sin(t)], [0, 1, 0], [-np.sin(t), 0, np.cos(t)]])


def load_glb(path: str):
    """GLB 를 한 덩어리 메쉬로 읽는다. (메쉬, 색 텍스처 PIL 또는 None)"""
    m = trimesh.load(path, force="mesh", process=False)
    tex = None
    mat = getattr(m.visual, "material", None)
    if mat is not None and getattr(mat, "baseColorTexture", None) is not None:
        tex = mat.baseColorTexture.convert("RGB")
    return m, tex


def build(m, tex, front: str) -> TexturedMesh:
    """MV-Adapter load_mesh(rescale=True) 와 같은 틀로 놓는다: 정면을 +Z 로 돌리고, 상자 가운데를 원점에,
    가장 먼 좌표를 0.5 로 맞춘 뒤 (x, -z, y) 표준 좌표(Z 위)로 바꾼다."""
    v = np.asarray(m.vertices, dtype=np.float64) @ rot_y(front).T
    v = v - (v.max(0) + v.min(0)) / 2
    v = v / np.abs(v).max() * 0.5
    std = np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1)
    faces = np.asarray(m.faces, dtype=np.int64)
    uv = getattr(m.visual, "uv", None)
    v_tex = t_tex = texture = None
    if uv is not None:
        v_tex = torch.tensor(np.asarray(uv), dtype=torch.float32)
        v_tex[:, 1] = 1.0 - v_tex[:, 1]          # trimesh 는 v 를 뒤집어 읽는다 → glTF 약속(0 = 그림 맨 윗줄)으로 되돌림
        t_tex = torch.tensor(faces)
        if tex is not None:
            texture = torch.tensor(np.asarray(tex) / 255.0, dtype=torch.float32)
    tm = TexturedMesh(v_pos=torch.tensor(std, dtype=torch.float32), t_pos_idx=torch.tensor(faces),
                      v_tex=v_tex, t_tex_idx=t_tex, texture=texture)
    st = trimesh.Trimesh(vertices=std, faces=faces, process=False)
    st.merge_vertices()
    tm.set_stitched_mesh(torch.tensor(st.vertices, dtype=torch.float32), torch.tensor(st.faces, dtype=torch.int64))
    tm.to(DEV)
    return tm


def cameras(elev, az):
    return get_orthogonal_camera(elevation_deg=list(elev), distance=[1.8] * len(az), left=-HALF, right=HALF,
                                 bottom=-HALF, top=HALF, azimuth_deg=list(az), device=DEV)


def ctx():
    return NVDiffRastContextWrapper(device=DEV, context_type="cuda")


def to_pil(t: torch.Tensor) -> Image.Image:
    a = (t.detach().clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
    return Image.fromarray(a)


def photo_mask(path: str) -> np.ndarray:
    """사진에서 물체 자리 (참/거짓). 투명 배경이 있으면 그 투명도를 쓰고 (콘솔에서는 늘 cutout.py 가 지운 사진),
    없으면 보라 배경으로 본다 (시험에 쓴 첫 소 사진). 보라 배경일 때 가장자리 16 px 은 버린다 (사진 테두리 잡티)."""
    im = Image.open(path)
    if im.mode == "RGBA":
        return np.asarray(im.getchannel("A")) > 127
    p = np.asarray(im.convert("RGB")).astype(int)
    bg = (p[..., 2] - p[..., 1] > 60) & (p[..., 0] - p[..., 1] > 40)
    m = ~bg
    m[:16] = m[-16:] = False
    m[:, :16] = m[:, -16:] = False
    return m


def bbox_norm(mask: np.ndarray, size: int = 256) -> np.ndarray:
    """물체 상자를 잘라 긴 변을 size 로 맞춰 가운데에 놓는다 (실루엣 비교용)."""
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = crop.shape
    s = size / max(h, w)
    im = Image.fromarray(crop.astype(np.uint8) * 255).resize((max(1, round(w * s)), max(1, round(h * s))))
    out = np.zeros((size, size), bool)
    a = np.asarray(im) > 127
    y0, x0 = (size - a.shape[0]) // 2, (size - a.shape[1]) // 2
    out[y0:y0 + a.shape[0], x0:x0 + a.shape[1]] = a
    return out


def grid_img(images, cols: int, cell: int, bg=(128, 128, 128)) -> Image.Image:
    rows = (len(images) + cols - 1) // cols
    g = Image.new("RGB", (cols * cell, rows * cell), bg)
    for i, im in enumerate(images):
        g.paste(im.resize((cell, cell)), ((i % cols) * cell, (i // cols) * cell))
    return g
