"""사진의 배경을 한 번 지워 RGBA 로 저장한다. 3D 만들기·방향 찾기·색 칠하기가 모두 이 한 장을 쓴다.

    python cutout.py --image photo.jpg --out cutout.png
긴 변이 2048 px 보다 크면 줄인다. 이미 투명 배경이면 그대로 쓴다.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/mvadapter-run"))
import mv_views as mv  # noqa: E402
from PIL import Image  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--image", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()
t0 = time.time()
im = Image.open(a.image)
im.load()
if max(im.size) > 2048:
    im.thumbnail((2048, 2048))
out = im.convert("RGBA") if mv.is_cut_out(im) else mv.cut_out(im)
out.save(a.out)
alpha = out.getchannel("A")
box = alpha.point(lambda v: 255 if v > 127 else 0).getbbox()
print(f"[cutout] 끝: {a.out} {out.size}, 물체 상자 {box}, {time.time() - t0:.1f} s")
