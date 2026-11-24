"""사진 한 장에서 물체를 여러 방향에서 본 그림을 한 번에 그린다 (MV-Adapter, SDXL).

    python mv_views.py --image photo.png --output outputs/x.png [--prompt-file desc.txt] [--views 6|4] [--seed 21]

왜 한 번에 그리나: 방향마다 따로 그리면(각도 LoRA) 모델이 안 보이는 쪽을 그릴 때마다 새로 상상한다. 그러면 무늬·귀 모양·
크기·실제로 돈 각도가 그림마다 달라지고, 그 그림들로 3D 를 만들면 뒷머리에 얼굴이 비치는 것처럼 망가진다.
MV-Adapter 는 여러 방향을 한 덩어리로 함께 그리며 서로 맞춘다. 2026-11-16 개 사진으로 잰 값: 6방향 768px 61 s,
GPU 메모리 최대 15.1 GiB, 털 무늬·귀·목줄이 모든 방향에서 맞았고, 이 그림들로 만든 3D 는 얼굴이 하나였다.

방향 이름은 3D 만들기(pixal3d_run.py)와 같은 약속이다. 정면 0° 에서 카메라가 보는 사람의 오른쪽으로 돈다.
90°(right) 그림에서는 물체의 앞쪽(코)이 그림의 왼쪽을 본다.
    front 0°, front-right 45°, right 90°, back 180°, left 270°, front-left 315°   (--views 4 이면 front·right·back·left)
모든 방향은 같은 틀로 그린다: 눈높이, 원근 없음(정사영), 물체 가운데에서 화면 폭 1.1. 이 정보를 --views-out 의
묶음 파일(JSON)에 적어 3D 만들기가 그대로 쓰게 한다.

결과
- --output: 모든 방향을 3열로 모은 그림 (6방향이면 3×2). 목록과 노드에서 보는 그림이다.
- 방향마다 한 장: --out-<방향> 경로, 주지 않으면 <output 이름>.<방향>.png
- --views-out: 묶음 파일 (주지 않으면 <output 이름>.views.json). 그림 경로는 묶음 파일 기준 상대 경로다.
- --thumb: 목록에 보일 작은 미리보기 (JPEG)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 방향 이름 → MV-Adapter 에 주는 각도. MV-Adapter 저장소는 이 각도에서 90 을 빼서 카메라를 놓는다 (inference_i2mv_sdxl.py 와 같다).
# 그렇게 놓았을 때 90° 그림은 코가 왼쪽을 본다 (2026-11-16 개 사진으로 확인) — 3D 만들기의 right 와 같다.
AZIMUTHS = {"front": 0, "front-right": 45, "right": 90, "back": 180, "left": 270, "front-left": 315}
VIEW_SETS = {"6": ("front", "front-right", "right", "back", "left", "front-left"), "4": ("front", "right", "back", "left")}
SIZE = 768                      # MV-Adapter SDXL 이 배운 크기
HALF_WIDTH = 0.55               # 정사영 틀의 반폭 (left=-0.55, right=0.55)
DEFAULT_TEXT = "a high quality object"
HOME = os.path.expanduser("~")
WEIGHTS = os.path.join(HOME, "models", "mvadapter-hub")       # SDXL(fp16)·VAE·어댑터 (홈 링크 → 공용)
CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MV-Adapter")
BIREFNET = "ZhengPeng7/BiRefNet"                               # 배경 지우기 (Pixal3D 와 같은 공용 모델)


def say(text: str) -> None:
    print(f"[mv] {text}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="사진 한 장 → 여러 방향에서 본 그림 (MV-Adapter)")
    ap.add_argument("--image", required=True, help="물체 하나가 크게 찍힌 사진. 배경은 지운다 (이미 투명하면 그대로 쓴다)")
    ap.add_argument("--prompt-file", help="물체 설명 (영어 한 줄, 예: a dog). 없으면 'a high quality object'")
    ap.add_argument("--prompt", help="물체 설명을 바로 줄 때")
    ap.add_argument("--output", required=True, help="모든 방향을 모은 그림 (PNG)")
    ap.add_argument("--views-out", help="3D 만들기에 넘길 묶음 파일 (JSON)")
    for name in AZIMUTHS:
        ap.add_argument(f"--out-{name}", help=f"{name} 방향 그림을 쓸 경로")
    ap.add_argument("--thumb", help="목록에 보일 작은 미리보기 (JPEG)")
    ap.add_argument("--views", choices=sorted(VIEW_SETS), default="6", help="그릴 방향 수: 6 (45° 둘 포함) 또는 4 (앞·오른쪽·뒤·왼쪽)")
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--steps", type=int, default=50, help="잡음을 걷어 내는 횟수 (기본 50)")
    ap.add_argument("--guidance", type=float, default=3.0, help="설명 글을 따르는 세기 (기본 3)")
    ap.add_argument("--ref-scale", type=float, default=1.0, help="사진을 따르는 세기 (기본 1)")
    return ap


def check(args) -> None:
    if args.prompt and args.prompt_file:
        raise SystemExit("[mv] 실패: --prompt 와 --prompt-file 가운데 하나만 주세요")
    if not os.path.isfile(args.image):
        raise SystemExit(f"[mv] 실패: 사진이 없습니다 (받은 경로 {args.image})")
    if not 1 <= args.steps <= 200:
        raise SystemExit(f"[mv] 실패: --steps 는 1~200 이어야 합니다 (받은 값 {args.steps})")
    if not 1.0 <= args.guidance <= 20:
        raise SystemExit(f"[mv] 실패: --guidance 는 1~20 이어야 합니다 (받은 값 {args.guidance})")
    if not 0.0 <= args.ref_scale <= 2.0:
        raise SystemExit(f"[mv] 실패: --ref-scale 은 0~2 이어야 합니다 (받은 값 {args.ref_scale})")


def describe(args) -> str:
    text = args.prompt or ""
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as f:
            text = f.read()
    text = " ".join(text.split())
    if not text:
        return DEFAULT_TEXT
    if any(ord(c) > 127 for c in text):
        say("참고: 설명 글은 영어만 알아듣습니다 (SDXL 의 글 이해 모델). 영어가 아닌 글자는 거의 무시됩니다.")
    return text


def out_paths(args, names) -> dict[str, str]:
    stem = os.path.splitext(args.output)[0]
    return {n: getattr(args, f"out_{n.replace('-', '_')}") or f"{stem}.{n}.png" for n in names}


def bundle(names, paths: dict[str, str], bundle_path: str, args) -> dict:
    """3D 만들기가 읽는 묶음. 그림 경로는 묶음 파일이 있는 폴더 기준이다."""
    base = os.path.dirname(os.path.abspath(bundle_path))
    return {
        "version": 1,
        "generator": "MV-Adapter i2mv SDXL",
        "projection": "orthographic",
        "half_width": HALF_WIDTH,
        "elevation_deg": 0,
        "size": SIZE,
        "seed": args.seed,
        "views": [{"name": n, "azimuth_deg": AZIMUTHS[n], "file": os.path.relpath(os.path.abspath(paths[n]), base)}
                  for n in names],
    }


def is_cut_out(im) -> bool:
    """이미 배경을 지운 그림인지: 투명도가 있고 눈에 띄게 투명한 자리가 있다."""
    import numpy as np

    if im.mode != "RGBA":
        return False
    a = np.asarray(im.getchannel("A"))
    return float((a < 250).mean()) > 0.01


def cut_out(im):
    """배경을 지워 RGBA 로 돌려준다 (BiRefNet, 저장소 스크립트와 같은 전처리)."""
    import torch
    from huggingface_hub import snapshot_download
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    path = snapshot_download(BIREFNET, cache_dir=os.environ.get("HF_HUB_CACHE", "/shared/models/hub"), local_files_only=True)
    net = AutoModelForImageSegmentation.from_pretrained(path, trust_remote_code=True).to("cuda").eval()
    tf = transforms.Compose([transforms.Resize((1024, 1024)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    rgb = im.convert("RGB")
    with torch.no_grad():
        pred = net(tf(rgb).unsqueeze(0).to("cuda"))[-1].sigmoid().cpu()[0].squeeze()
    mask = transforms.ToPILImage()(pred).resize(rgb.size)
    out = rgb.copy()
    out.putalpha(mask)
    del net
    torch.cuda.empty_cache()
    return out


def load_pipe(n_views: int):
    import torch
    from diffusers import AutoencoderKL
    from huggingface_hub import snapshot_download

    from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
    from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler

    def local(repo):
        return snapshot_download(repo, cache_dir=WEIGHTS, local_files_only=True)

    dtype = torch.float16
    vae = AutoencoderKL.from_pretrained(local("madebyollin/sdxl-vae-fp16-fix"), torch_dtype=dtype)
    # SDXL 은 반정밀도(fp16) 파일만 받아 두었다. 저장소 스크립트는 variant 없이 읽어 전체 정밀도 파일을 찾으므로 여기서 직접 읽는다.
    pipe = MVAdapterI2MVSDXLPipeline.from_pretrained(local("stabilityai/stable-diffusion-xl-base-1.0"), vae=vae,
                                                     variant="fp16", torch_dtype=dtype)
    pipe.scheduler = ShiftSNRScheduler.from_scheduler(pipe.scheduler, shift_mode="interpolated", shift_scale=8.0,
                                                      scheduler_class=None)
    pipe.init_custom_adapter(num_views=n_views)
    pipe.load_custom_adapter(local("huanngzh/mv-adapter"), weight_name="mvadapter_i2mv_sdxl.safetensors")
    pipe.to(device="cuda", dtype=dtype)
    pipe.cond_encoder.to(device="cuda", dtype=dtype)
    pipe.enable_vae_slicing()
    return pipe


def grid(images, cols: int, cell: int):
    from PIL import Image

    rows = (len(images) + cols - 1) // cols
    g = Image.new("RGB", (cols * cell, rows * cell), (128, 128, 128))
    for i, im in enumerate(images):
        g.paste(im.resize((cell, cell)), ((i % cols) * cell, (i // cols) * cell))
    return g


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    check(args)
    names = VIEW_SETS[args.views]
    text = describe(args)
    paths = out_paths(args, names)
    bundle_path = args.views_out or os.path.splitext(args.output)[0] + ".views.json"
    sys.path.insert(0, CODE)

    import torch
    from PIL import Image

    started = time.time()
    photo = Image.open(args.image)
    photo.load()
    if is_cut_out(photo):
        ref = photo.convert("RGBA")
        say("배경 지우기: 이미 투명한 배경이라 그대로 씁니다")
    else:
        say("배경 지우기: 사진의 배경을 지웁니다 (BiRefNet)")
        ref = cut_out(photo)
    from scripts.inference_i2mv_sdxl import preprocess_image   # 물체를 잘라 768px 의 90% 로 맞추고 회색 바탕 가운데에 놓는다

    if not (ref.getchannel("A").getextrema()[1] > 0):
        say("실패: 배경을 지우니 물체가 남지 않았습니다. 물체가 크고 또렷하게 찍힌 사진을 쓰세요")
        return 2
    ref = preprocess_image(ref, SIZE, SIZE)

    say(f"모델 올리기: SDXL + MV-Adapter ({len(names)}방향)")
    pipe = load_pipe(len(names))
    from mvadapter.utils import get_orthogonal_camera, get_plucker_embeds_from_cameras_ortho

    az = [AZIMUTHS[n] for n in names]
    cams = get_orthogonal_camera(elevation_deg=[0] * len(az), distance=[1.8] * len(az), left=-HALF_WIDTH, right=HALF_WIDTH,
                                 bottom=-HALF_WIDTH, top=HALF_WIDTH, azimuth_deg=[a - 90 for a in az], device="cuda")
    control = ((get_plucker_embeds_from_cameras_ortho(cams.c2w, [2 * HALF_WIDTH] * len(az), SIZE) + 1.0) / 2.0).clamp(0, 1)
    say(f"시작: {', '.join(names)}, {SIZE}px, 시드 {args.seed}, 단계 {args.steps}, 설명 '{text[:60]}'")
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    drawing = time.time()
    images = pipe(text, height=SIZE, width=SIZE, num_inference_steps=args.steps, guidance_scale=args.guidance,
                  num_images_per_prompt=len(names), control_image=control, control_conditioning_scale=1.0,
                  reference_image=ref, reference_conditioning_scale=args.ref_scale,
                  negative_prompt="watermark, ugly, deformed, noisy, blurry, low contrast",
                  cross_attention_kwargs={"scale": 1.0},
                  generator=torch.Generator(device="cuda").manual_seed(args.seed)).images

    for n, im in zip(names, images):
        os.makedirs(os.path.dirname(os.path.abspath(paths[n])), exist_ok=True)
        im.save(paths[n])
    sheet = grid(images, 3 if len(images) > 4 else 2, SIZE)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    sheet.save(args.output)
    with open(bundle_path, "w", encoding="utf-8") as f:
        json.dump(bundle(names, paths, bundle_path, args), f, indent=1, ensure_ascii=False)
    if args.thumb:
        grid(images, 3 if len(images) > 4 else 2, 160).save(args.thumb, quality=82)
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
    say(f"끝: {args.output} ({len(names)}방향 {SIZE}px), 그리는 데 {time.time() - drawing:.0f} s, 전체 {time.time() - started:.0f} s, "
        f"GPU 메모리 최대 {peak:.1f} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
