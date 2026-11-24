"""3D 모양을 여섯 방향에서 렌더링해 MV-Adapter ig2mv 에 줄 '모양 그림'(위치·기울기)을 만든다.

    python geo_views.py --mesh single.glb --photo cutout.png --out-dir views [--front pixal3d|auto|+z|-z|+x|-x]

사진 쪽(정면)은 어느 축인가. Pixal3D 는 학습 때부터 입력 사진의 카메라를 늘 같은 자리(Blender 좌표 (0, -거리, 0))에 두고
3D 를 만든다 (Pixal3D 저장소 datasets/sparse_structure_latent.py 의 설명). 그래서 GLB 에서 사진 쪽은 늘 -z 다
(--front pixal3d, 기본값). 2026-11-16 소·고양이·그루터기 둘·거북이·건물 둘, 일곱 번 모두 -z 였다.
--front auto 는 다른 3D 생성기의 메쉬용이다: 네 방향의 실루엣을 사진 실루엣과 겹쳐 가장 많이 겹치는 쪽을 고르고,
차이가 0.03 미만이면 색으로 가린다. 그런데 앞뒤가 비슷한 물체에서는 둘 다 틀린다. 그루터기는 실루엣이 +z 를 골랐고
(0.838 대 0.836), 건물은 색이 +z 를 골랐다 (0.156 대 0.157). 두 번 모두 답은 -z 였다.
기본값에서도 실루엣 겹침은 계산해 기록하고, 다른 축이 -z 보다 0.05 넘게 더 겹치면 경고를 찍는다.
결과: control.npz (6, 6, 768, 768), pos.png·nor.png (확인용), tex.png (원래 텍스처를 그 방향에서 본 그림), probe.png, front.txt
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np
import torch

import geo_common as G


def front_view(tm, c, size=256):
    cam = G.cameras([0], [G.AZ[0]])
    out = G.render(c, tm, cam, size, size, render_attr=tm.texture is not None, render_depth=False, render_normal=False)
    mask = out.mask[0].cpu().numpy()
    img = out.attr[0] if tm.texture is not None else out.mask[0].float()[..., None].repeat(1, 1, 3)
    return mask, G.to_pil(img)


TIE = 0.03     # 실루엣 겹친 비율이 이만큼도 차이 나지 않으면 색으로 가린다


def crop_rgb(rgb: np.ndarray, mask: np.ndarray, size: int = 128) -> tuple[np.ndarray, np.ndarray]:
    """물체 상자로 잘라 긴 변을 size 로 맞춰 가운데 놓은 (색, 자리). bbox_norm 과 같은 틀."""
    from PIL import Image

    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    h, w = y1 - y0, x1 - x0
    s = size / max(h, w)
    nh, nw = max(1, round(h * s)), max(1, round(w * s))
    im = np.asarray(Image.fromarray(rgb[y0:y1, x0:x1]).resize((nw, nh))).astype(np.float32) / 255
    mk = np.asarray(Image.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255).resize((nw, nh))) > 127
    out_c, out_m = np.zeros((size, size, 3), np.float32), np.zeros((size, size), bool)
    oy, ox = (size - nh) // 2, (size - nw) // 2
    out_c[oy:oy + nh, ox:ox + nw] = im
    out_m[oy:oy + nh, ox:ox + nw] = mk
    return out_c, out_m


def color_gap(photo_rgb, photo_mask, shot_rgb, shot_mask) -> float:
    a, am = crop_rgb(photo_rgb, photo_mask)
    b, bm = crop_rgb(shot_rgb, shot_mask)
    both = am & bm
    return float(np.abs(a - b)[both].mean()) if both.any() else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--photo")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--front", default="pixal3d", choices=("pixal3d", "auto", *G.FRONT_DEG))
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    m, tex = G.load_glb(a.mesh)
    print(f"[geo] 메쉬: 점 {len(m.vertices):,}개, 삼각형 {len(m.faces):,}개, 텍스처 {None if tex is None else tex.size}")
    c = G.ctx()

    front = "-z" if a.front == "pixal3d" else a.front
    if a.front in ("auto", "pixal3d") and a.photo:
        pmask = G.photo_mask(a.photo)
        ref = G.bbox_norm(pmask)
        scores, shots = {}, {}
        for f in G.FRONT_DEG:
            mask, img = front_view(G.build(m, tex, f), c)
            cand = G.bbox_norm(mask)
            scores[f] = float((ref & cand).sum() / (ref | cand).sum())
            shots[f] = (np.asarray(img), mask)
        G.grid_img([G.Image.fromarray(v[0]) for v in shots.values()], 4, 256).save(os.path.join(a.out_dir, "probe.png"))
        listing = ", ".join(f"{k} {v:.3f}" for k, v in scores.items())
        if a.front == "pixal3d":
            print(f"[geo] 정면: -z (Pixal3D 가 사진 카메라를 두는 쪽). 확인용 실루엣 겹침: {listing}")
            other = max((f for f in scores if f != "-z"), key=scores.get)
            if scores[other] - scores["-z"] > 0.05:
                print(f"[geo] 경고: {other} 의 실루엣이 -z 보다 {scores[other] - scores['-z']:.2f} 더 겹칩니다. "
                      "사진 한 장 3D 가 아닌 메쉬라면 --front auto 를 쓰세요")
        else:
            front = max(scores, key=scores.get)
            print(f"[geo] 정면 찾기 (사진 실루엣과 겹친 비율): {listing} → {front}")
        close = [f for f in scores if scores[front] - scores[f] < TIE]
        if a.front == "auto" and len(close) > 1 and tex is not None:
            photo_rgb = np.asarray(G.Image.open(a.photo).convert("RGB"))
            gaps = {f: color_gap(photo_rgb, pmask, *shots[f]) for f in close}
            front = min(gaps, key=gaps.get)
            print("[geo] 실루엣이 비슷해 색으로 가림 (사진과의 평균 색 차이, 0~1): "
                  + ", ".join(f"{k} {v:.3f}" for k, v in gaps.items()) + f" → {front}")
    with open(os.path.join(a.out_dir, "front.txt"), "w") as f:
        f.write(front)

    tm = G.build(m, tex, front)
    cams = G.cameras(G.ELEV, G.AZ)
    out = G.render(c, tm, cams, G.SIZE, G.SIZE, render_attr=tm.texture is not None, render_depth=False,
                   normal_background=0.0)
    pos = (out.pos + 0.5).clamp(0, 1)
    nor = (out.normal / 2 + 0.5).clamp(0, 1)
    control = torch.cat([pos, nor], -1).permute(0, 3, 1, 2).contiguous()
    np.savez_compressed(os.path.join(a.out_dir, "control.npz"), control=control.half().cpu().numpy())
    G.grid_img([G.to_pil(x) for x in pos], 6, 256).save(os.path.join(a.out_dir, "pos.png"))
    G.grid_img([G.to_pil(x) for x in nor], 6, 256).save(os.path.join(a.out_dir, "nor.png"))
    if tm.texture is not None:
        G.grid_img([G.to_pil(x) for x in out.attr], 6, 256).save(os.path.join(a.out_dir, "tex.png"))
    cover = out.mask.float().mean(dim=(1, 2)).cpu().numpy()
    print("[geo] 방향마다 물체가 차지한 넓이: " + ", ".join(f"{n} {v:.0%}" for n, v in zip(G.VIEWS, cover)))
    print(f"[geo] 끝: {a.out_dir}/control.npz, {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
