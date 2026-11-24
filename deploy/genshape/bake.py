"""ig2mv 가 칠한 여섯 방향 그림을 사진 한 장 3D 의 색 텍스처로 다시 입힌다.

    python bake.py --mesh single.glb --front -z --paint-dir paint_s1 --output result.glb [--color auto] [--thumb t.jpg]

텍스처의 칸(텍셀)마다 3D 자리와 표면 방향을 구한다. 그 자리가 각 방향 그림에서 보이면 그 그림의 색을 가져온다.
정면으로 마주 볼수록 크게 섞는다 (cos^4). 사진이 본 쪽은 원래 텍스처(사진에서 온 색)를 세 배 무게로 남기고,
MV-Adapter 가 다시 그린 앞 그림은 쓰지 않는다. 어느 그림에서도 안 보이는 칸은 원래 텍스처를 둔다.

색 맞추기 (--color). ig2mv 는 사진의 햇빛을 이어 받아 사진 반대쪽을 그늘처럼 어둡게, 다리·목을 초록빛으로 칠하기도 하고,
재질 색을 바꾸기도 한다 (그루터기의 올리브색 껍질을 회색으로). 그래서 세 가지를 만들어 본다.
- none: 칠한 색 그대로
- group: 털·껍질을 '색이 진한 무리'와 '흰 무리'로 나눠 무리마다 사진 쪽 색에 맞춘다 (color.py). 소·고양이에 맞았다
- overlap: 같은 표면 점이 사진 쪽과 그 그림 양쪽에 다 보이는 띠에서 색을 맞춘다. 그루터기·거북이에 맞았다
- auto: 셋을 다 만들어 0·90·180·270° 에서 렌더링하고, 사진 쪽(0°)과 색이 가장 덜 다른 것을 고른다 (2026-11-16 네 물체,
  시드 둘 모두 눈으로 고른 것과 같았다)
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np
import torch
import torch.nn.functional as F
import trimesh
from PIL import Image

import color as CL
import geo_common as G

EPS = 0.01          # 보이는지 판단: 그 픽셀에 렌더링된 표면이 텍셀과 이 거리(물체 크기 1 기준) 안에 있어야 함
POWER = 4           # 마주 보는 정도(cos)를 몇 제곱해 섞을지
VIEW_W = {"front": 1.0, "right": 1.0, "back": 1.0, "left": 1.0, "top": 0.5, "bottom": 0.3}
KEEP_PHOTO = 3.0    # 사진 쪽 원래 텍스처의 무게 (앞 그림 무게의 배수)
COLOR_MODES = ("auto", "none", "group", "overlap")


def say(text: str) -> None:
    print(f"[bake] {text}", flush=True)


def texel_geometry(c, tm, H, W):
    uv = tm.v_tex
    clip = torch.cat([uv * 2 - 1, torch.zeros_like(uv[:, :1]), torch.ones_like(uv[:, :1])], -1)[None].contiguous()
    rast, _ = c.rasterize(clip, tm.t_tex_idx, (H, W))
    covered = rast[0, ..., 3] > 0
    P, _ = c.interpolate(tm.v_pos[None], rast, tm.t_pos_idx)
    N, _ = c.interpolate(tm.v_nrm[None], rast, tm.stitched_t_pos_idx)
    N = F.normalize(N, dim=-1)
    return covered, P[0][covered], N[0][covered]


def sample_views(c, tm, Pf, Nf, images):
    """텍셀마다 여섯 그림에서 가져온 색과 무게 (보이면 cos^4 × 방향 무게, 안 보이면 0)."""
    cams = G.cameras(G.ELEV, G.AZ)
    out = G.render(c, tm, cams, G.SIZE, G.SIZE, render_attr=False, render_depth=False, render_normal=False)
    posmap = out.pos.permute(0, 3, 1, 2)
    mask = out.mask[:, None].float()
    eroded = 1 - F.max_pool2d(1 - mask, 5, 1, 2)        # 실루엣 가장자리 2 px 은 배경이 섞이므로 버림
    Ph = torch.cat([Pf, torch.ones_like(Pf[:, :1])], -1)
    cols, ws = [], []
    for k, name in enumerate(G.VIEWS):
        clip = Ph @ cams.mvp_mtx[k].T
        ndc = clip[:, :2] / clip[:, 3:]
        grid = ndc.view(1, -1, 1, 2)
        col = F.grid_sample(images[k:k + 1], grid, mode="bilinear", align_corners=False)[0, :, :, 0].T
        ps = F.grid_sample(posmap[k:k + 1], grid, mode="nearest", align_corners=False)[0, :, :, 0].T
        ms = F.grid_sample(eroded[k:k + 1], grid, mode="nearest", align_corners=False)[0, 0, :, 0]
        vis = (ms > 0.5) & ((ps - Pf).norm(dim=-1) < EPS)
        cos = (Nf @ F.normalize(cams.cam_pos[k], dim=0)).clamp(min=0)
        cols.append(col)
        ws.append(cos.pow(POWER) * vis.float() * VIEW_W[name])
    return cols, ws, eroded


def fit_overlap(src, dst, w, min_n=2000):
    """같은 표면 점에서 본 두 색(src: 칠한 그림, dst: 기준)을 Lab 채널마다 평균·퍼짐으로 맞추는 변환.
    겹치는 점이 min_n 개보다 적으면 None (바꾸지 않음)."""
    if int((w > 0).sum()) < min_n:
        return None
    ls, ld = CL.rgb2lab(src), CL.rgb2lab(dst)
    sw = w.sum()
    out = []
    for ch, (lo, hi) in enumerate([(0.6, 1.8), (0.5, 2.0), (0.5, 2.0)]):
        mx = (w * ls[:, ch]).sum() / sw
        my = (w * ld[:, ch]).sum() / sw
        sx = ((w * (ls[:, ch] - mx) ** 2).sum() / sw).sqrt()
        sy = ((w * (ld[:, ch] - my) ** 2).sum() / sw).sqrt()
        k = float((sy / sx.clamp(min=1e-3)).clamp(lo, hi))
        out.append((k, float(my - k * mx)))
    return out


def apply_lab(rgb, params):
    if params is None:
        return rgb
    lab = CL.rgb2lab(rgb)
    return CL.lab2rgb(torch.stack([lab[:, ch] * k + b for ch, (k, b) in enumerate(params)], -1))


def overlap_correct(base, cols, ws, tau=0.05):
    """겹치는 띠로 방향 그림의 색을 맞춘다. 오른쪽·왼쪽·위·아래는 사진 쪽과 겹치는 칸으로,
    뒤는 앞과 겹치지 않으므로 먼저 고친 네 그림과 겹치는 칸으로 맞춘다."""
    new, log = list(cols), {}
    ref_w = ws[0]
    for k in (1, 3, 4, 5):
        w = torch.where((ws[k] > tau) & (ref_w > tau), torch.minimum(ws[k], ref_w), torch.zeros_like(ref_w))
        p = fit_overlap(cols[k], base, w)
        new[k] = apply_lab(cols[k], p)
        log[G.VIEWS[k]] = (int((w > 0).sum()), p)
    num = sum(new[k] * ws[k][:, None] for k in (1, 3, 4, 5))
    den = sum(ws[k] for k in (1, 3, 4, 5))
    ref = num / den.clamp(min=1e-6)[:, None]
    w = torch.where((ws[2] > tau) & (den > tau), torch.minimum(ws[2], den), torch.zeros_like(den))
    p = fit_overlap(cols[2], ref, w)
    new[2] = apply_lab(cols[2], p)
    log["back"] = (int((w > 0).sum()), p)
    return new, log


def seam_score(st0, st):
    """사진 쪽(0°) 렌더링과 다른 방향 렌더링의 색 차이. 작을수록 한 물체처럼 이어져 보인다.
    무리마다 (밝기 차 + 색 기운 차의 반) × 무리의 몫(사진 쪽 비율 × 2), 그리고 무리 비율 차 × 30."""
    r0 = st0["brown"]["n"] / max(st0["brown"]["n"] + st0["white"]["n"], 1)
    r = st["brown"]["n"] / max(st["brown"]["n"] + st["white"]["n"], 1)
    total = 30 * abs(r - r0)
    for g, share in (("brown", r0), ("white", 1 - r0)):
        a, b = st0[g], st[g]
        total += 2 * share * (abs(a["L"] - b["L"]) + 0.5 * (abs(a["a"] - b["a"]) + abs(a["b"] - b["b"])))
    return total


def blend(base, cols, ws):
    num = torch.zeros_like(base)
    den = torch.zeros_like(base[:, 0])
    for c_, w in zip(cols, ws):
        num += c_ * w[:, None]
        den += w
    out = base.clone()
    ok = den > 1e-4
    out[ok] = num[ok] / den[ok, None]
    return out, ok


def dilate(tex, covered, n=8):
    """UV 조각 바깥 틈을 가까운 안쪽 색으로 채운다 (조각 경계에서 옛 색이 비치지 않게)."""
    t = tex.permute(2, 0, 1)[None]
    m = covered[None, None].float()
    k = torch.ones(1, 1, 3, 3, device=t.device)
    for _ in range(n):
        s = F.conv2d(t * m, k.repeat(3, 1, 1, 1), padding=1, groups=3)
        cnt = F.conv2d(m, k, padding=1)
        grow = (cnt > 0) & (m == 0)
        t = torch.where(grow, s / cnt.clamp(min=1), t)
        m = torch.where(grow, torch.ones_like(m), m)
    return t[0].permute(1, 2, 0).contiguous()


def save_glb(src: str, tex_img: Image.Image, out: str) -> None:
    """원래 GLB 의 색 텍스처만 바꿔 쓴다 (모양, UV, 다른 재질 값은 그대로)."""
    scene = trimesh.load(src, process=False)
    geoms = list(scene.geometry.values())
    if len(geoms) != 1:
        raise SystemExit(f"[bake] 실패: 메쉬가 {len(geoms)}개입니다. 사진 한 장 3D 는 메쉬 하나를 냅니다 (받은 파일 {src})")
    geoms[0].visual.material.baseColorTexture = tex_img
    tmp = out + ".part.glb"
    scene.export(tmp)
    os.replace(tmp, out)


def main() -> int:
    ap = argparse.ArgumentParser(description="칠한 여섯 방향 그림 → 3D 텍스처")
    ap.add_argument("--mesh", required=True, help="사진 한 장 3D 결과 (GLB)")
    ap.add_argument("--front", required=True, help="geo_views.py 가 고른 정면 축 (front.txt). 예: --front=-z")
    ap.add_argument("--paint-dir", required=True, help="ig2mv_paint.py 결과 폴더 (view_<방향>.png)")
    ap.add_argument("--output", required=True, help="색을 입힌 GLB")
    ap.add_argument("--color", choices=COLOR_MODES, default="auto")
    ap.add_argument("--thumb", help="결과를 네 방향(0·90·180·270°)에서 본 작은 미리보기 (JPEG)")
    a = ap.parse_args()
    t0 = time.time()
    m, tex = G.load_glb(a.mesh)
    if tex is None:
        raise SystemExit(f"[bake] 실패: {a.mesh} 에 색 텍스처가 없습니다. 사진 한 장 3D 를 텍스처와 함께 만들어야 합니다")
    tm = G.build(m, tex, a.front)
    c = G.ctx()
    H, W = tm.texture.shape[:2]
    covered, Pf, Nf = texel_geometry(c, tm, H, W)
    imgs = torch.stack([torch.tensor(np.asarray(Image.open(os.path.join(a.paint_dir, f"view_{n}.png")).convert("RGB")),
                                     dtype=torch.float32) / 255 for n in G.VIEWS]).permute(0, 3, 1, 2).to(G.DEV)
    cols, ws, eroded = sample_views(c, tm, Pf, Nf, imgs)
    base = tm.texture[covered]
    seen = torch.stack(ws).sum(0) > 1e-4
    say(f"텍셀 {int(covered.sum()):,}개 가운데 어느 그림에서든 보인 칸 {seen.float().mean():.1%} "
        "(Pixal3D 메쉬는 얇은 이중 벽이라 안쪽 벽 칸은 어디서도 보이지 않는다)")

    modes = ("none", "group", "overlap") if a.color == "auto" else (a.color,)
    variants = {}
    if "none" in modes:
        variants["none"] = blend(base, [base] + cols[1:], [ws[0] * KEEP_PHOTO] + ws[1:])
    if "group" in modes:
        front_cam = G.cameras([G.ELEV[0]], [G.AZ[0]])
        orig = G.render(c, tm, front_cam, G.SIZE, G.SIZE, render_attr=True, render_depth=False, render_normal=False)
        ref = CL.image_stats(orig.attr[0].permute(2, 0, 1), eroded[0, 0])
        imgs_g, _, _ = CL.match_views(imgs, eroded, ref=ref)
        cols_g, _, _ = sample_views(c, tm, Pf, Nf, imgs_g)
        variants["group"] = blend(base, [base] + cols_g[1:], [ws[0] * KEEP_PHOTO] + ws[1:])
    if "overlap" in modes:
        cols_o, log = overlap_correct(base, cols, ws)
        say("겹침 맞추기: " + ", ".join(f"{n} {'그대로(겹침 부족)' if p is None else f'{cnt:,}칸'}" for n, (cnt, p) in log.items()))
        variants["overlap"] = blend(base, [base] + cols_o[1:], [ws[0] * KEEP_PHOTO] + ws[1:])

    cam4 = G.cameras([10] * 4, [d - 90 for d in (0, 90, 180, 270)])
    scores, textures, renders = {}, {}, {}
    for name, (newf, _) in variants.items():
        full = tm.texture.clone()
        full[covered] = newf
        full = dilate(full, covered)
        r4 = G.render(c, tm, cam4, 512, 512, render_attr=True, render_depth=False, render_normal=False,
                      texture_override=full, attr_background=0.92, antialias_attr=True)
        e4 = 1 - F.max_pool2d(1 - r4.mask[:, None].float(), 5, 1, 2)
        sts = [CL.image_stats(r4.attr[i].permute(2, 0, 1), e4[i, 0]) for i in range(4)]
        scores[name] = sum(seam_score(sts[0], x) for x in sts[1:]) / 3
        textures[name], renders[name] = full, r4.attr
    best = min(scores, key=scores.get)
    say("색 맞추기 점수 (작을수록 사진 쪽과 고르게 이어짐): " + ", ".join(f"{k} {v:.1f}" for k, v in scores.items())
        + f" → {best}")

    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    save_glb(a.mesh, G.to_pil(textures[best]), a.output)
    if a.thumb:
        G.grid_img([G.to_pil(x) for x in renders[best]], 2, 160, bg=(235, 235, 235)).save(a.thumb, quality=85)
    say(f"끝: {a.output} ({os.path.getsize(a.output) / 2**20:.1f} MB, 색 맞추기 {best}), {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
