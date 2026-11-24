"""방향 그림들의 색을 앞 그림(사진과 같은 빛)에 맞춘다.

왜: ig2mv 는 사진의 햇빛을 이어 받아 사진 반대쪽을 그늘처럼 어둡게, 다리·목을 초록빛으로 칠한다.
그림 전체에 같은 값을 곱하면(첫 시도) 갈색은 밝아지지만 흰 털이 하얗게 날아간다.
그래서 털을 '갈색 무리'와 '흰 무리'로 나누고, 무리마다 따로 맞춘다.
- 밝기(L): 무리 안의 평균과 퍼진 정도를 앞 그림의 같은 무리에 맞춘다 (퍼진 정도 비는 0.7~1.4 로 묶음)
- 색 기운(a: 초록↔빨강, b: 파랑↔노랑): 무리 평균만 앞 그림 쪽으로 옮긴다 → 흰 털의 초록 기운이 빠진다
무리 나누기는 채도(Lab 의 C = √(a²+b²))를 밝기 L 로 나눈 값으로 한다. 0.4 보다 크면 갈색 쪽, 작으면 흰 쪽이고, 그 사이는 부드럽게 섞는다.
"""

from __future__ import annotations

import torch

_M = torch.tensor([[0.4124564, 0.3575761, 0.1804375],
                   [0.2126729, 0.7151522, 0.0721750],
                   [0.0193339, 0.1191920, 0.9503041]])
_WHITE = torch.tensor([0.95047, 1.0, 1.08883])
_D = 6 / 29


def rgb2lab(rgb: torch.Tensor) -> torch.Tensor:
    """(..., 3) sRGB 0~1 → Lab (L 0~100)"""
    lin = torch.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055).clamp(min=0) ** 2.4)
    xyz = lin @ _M.to(rgb).T / _WHITE.to(rgb)
    f = torch.where(xyz > _D ** 3, xyz.clamp(min=1e-9) ** (1 / 3), xyz / (3 * _D ** 2) + 4 / 29)
    return torch.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lab2rgb(lab: torch.Tensor) -> torch.Tensor:
    fy = (lab[..., 0] + 16) / 116
    f = torch.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = torch.where(f > _D, f ** 3, 3 * _D ** 2 * (f - 4 / 29)) * _WHITE.to(lab)
    lin = xyz @ torch.linalg.inv(_M).to(lab).T
    rgb = torch.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin.clamp(min=0) ** (1 / 2.4) - 0.055)
    return rgb.clamp(0, 1)


def brown_weight(lab: torch.Tensor, r0: float = 0.4, s: float = 0.06) -> torch.Tensor:
    """갈색 무리에 속하는 정도 0~1. 채도 C 를 밝기 L 로 나눈 값으로 판단한다.
    채도만 보면 그늘진 갈색(어둡고 채도 낮음)이 흰 무리로 들어간다. 예: 그늘 갈색 L 20, C 12 → 0.6 (갈색),
    흰 털 L 70, C 10 → 0.14 (흰색)."""
    c = (lab[..., 1] ** 2 + lab[..., 2] ** 2).sqrt()
    return torch.sigmoid((c / lab[..., 0].clamp(min=5) - r0) / s)


def class_stats(lab, mask, wb):
    """무리마다 (무게 합, L 평균, L 퍼짐, a 평균, b 평균)"""
    out = {}
    for name, w in (("brown", wb * mask), ("white", (1 - wb) * mask)):
        s = w.sum().clamp(min=1e-6)
        mu = (lab * w[..., None]).sum((0, 1)) / s
        var = ((lab[..., 0] - mu[0]) ** 2 * w).sum() / s
        out[name] = {"n": float(s), "L": float(mu[0]), "Lsd": float(var.sqrt()), "a": float(mu[1]), "b": float(mu[2])}
    return out


def image_stats(img: torch.Tensor, mask: torch.Tensor):
    """img (3,H,W) 0~1, mask (H,W) 0/1 → 무리별 통계"""
    lab = rgb2lab(img.permute(1, 2, 0))
    return class_stats(lab, mask, brown_weight(lab))


def match_views(images: torch.Tensor, masks: torch.Tensor, ref_index: int = 0, ref=None, skip=(0,)):
    """images (V,3,H,W) 0~1, masks (V,1,H,W) 0/1. ref(무리별 통계)가 없으면 ref_index 그림에 맞춘다.
    skip 에 든 그림은 건드리지 않는다. (새 그림, 맞추기 전·후 통계)"""
    out = images.clone()
    labs = [rgb2lab(images[k].permute(1, 2, 0)) for k in range(images.shape[0])]
    wbs = [brown_weight(x) for x in labs]
    ms = [masks[k, 0] for k in range(images.shape[0])]
    if ref is None:
        ref = class_stats(labs[ref_index], ms[ref_index], wbs[ref_index])
    before, after = [], []
    for k in range(images.shape[0]):
        st = class_stats(labs[k], ms[k], wbs[k])
        before.append(st)
        if k in skip:
            after.append(st)
            continue
        lab = labs[k]
        new = torch.zeros_like(lab)
        for name, w in (("brown", wbs[k]), ("white", 1 - wbs[k])):
            r, c = ref[name], st[name]
            if c["n"] < 200:            # 이 무리가 거의 없으면 옮기지 않음
                new += lab * w[..., None]
                continue
            g = min(max(r["Lsd"] / max(c["Lsd"], 1e-3), 0.7), 1.4)
            L = (lab[..., 0] - c["L"]) * g + r["L"]
            a = lab[..., 1] + (r["a"] - c["a"])
            b = lab[..., 2] + (r["b"] - c["b"])
            new += torch.stack([L, a, b], -1) * w[..., None]
        rgb = lab2rgb(new).permute(2, 0, 1)
        m = ms[k][None]
        out[k] = rgb * m + images[k] * (1 - m)
        after.append(class_stats(rgb2lab(out[k].permute(1, 2, 0)), ms[k], brown_weight(rgb2lab(out[k].permute(1, 2, 0)))))
    return out, before, after


def fmt(st) -> str:
    b, w = st["brown"], st["white"]
    return (f"갈색 L {b['L']:.0f} a {b['a']:+.0f} b {b['b']:+.0f} | 흰색 L {w['L']:.0f} a {w['a']:+.1f} b {w['b']:+.1f} "
            f"(갈색 비율 {b['n'] / max(b['n'] + w['n'], 1):.0%})")
