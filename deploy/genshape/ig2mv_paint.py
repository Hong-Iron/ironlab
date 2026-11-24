"""정해진 3D 모양 위에 MV-Adapter(ig2mv)로 여섯 방향 색 그림을 칠한다.

    python ig2mv_paint.py --control views/control.npz --image cutout.png --out-dir paint --seeds 1 [--text-file 설명.txt]
결과: <out-dir>_s<시드>/view_<방향>.png (768 px 여섯 장), grid.png (3×2 로 모은 그림)

모양은 geo_views.py 가 렌더링한 위치·기울기 그림(control.npz)으로 고정되어 있다. 모델은 색만 정한다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.expanduser("~/mvadapter-run"))
sys.path.insert(0, os.path.expanduser("~/mvadapter-run/MV-Adapter"))
import mv_views as mv  # noqa: E402  (배경 지우기, 사진 틀 맞추기, 모델 경로)

VIEWS = ["front", "right", "back", "left", "top", "bottom"]
IG2MV = os.path.expanduser("~/models/mvadapter-ig2mv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--out-dir", required=True, help="시드마다 <out-dir>_s<시드> 폴더에 쓴다")
    ap.add_argument("--seeds", default="1", help="쉼표로 여러 개 (모델은 한 번만 올림)")
    ap.add_argument("--text", default="high quality", help="물체 설명 (영어). MV-Adapter 저장소 기본값은 'high quality'")
    ap.add_argument("--text-file", help="물체 설명을 담은 파일 (있으면 --text 대신 쓴다)")
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--guidance", type=float, default=3.0)
    ap.add_argument("--ref-scale", type=float, default=1.0)
    a = ap.parse_args()
    seeds = [int(x) for x in a.seeds.split(",")]
    if a.text_file:
        with open(a.text_file, encoding="utf-8") as f:
            words = " ".join(f.read().split())
        if words:
            a.text = f"{words}, high quality"
    print(f"[ig2mv] 설명: '{a.text[:80]}'", flush=True)
    t0 = time.time()

    from diffusers import AutoencoderKL
    from huggingface_hub import snapshot_download
    from PIL import Image

    from mvadapter.models.attention_processor import DecoupledMVRowColSelfAttnProcessor2_0
    from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline
    from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler
    from scripts.inference_i2mv_sdxl import preprocess_image

    photo = Image.open(a.image)
    photo.load()
    ref = photo.convert("RGBA") if mv.is_cut_out(photo) else mv.cut_out(photo)
    ref = preprocess_image(ref, 768, 768)

    def local(repo, cache=mv.WEIGHTS):
        return snapshot_download(repo, cache_dir=cache, local_files_only=True)

    dtype = torch.float16
    vae = AutoencoderKL.from_pretrained(local("madebyollin/sdxl-vae-fp16-fix"), torch_dtype=dtype)
    pipe = MVAdapterI2MVSDXLPipeline.from_pretrained(local("stabilityai/stable-diffusion-xl-base-1.0"), vae=vae,
                                                     variant="fp16", torch_dtype=dtype)
    pipe.scheduler = ShiftSNRScheduler.from_scheduler(pipe.scheduler, shift_mode="interpolated", shift_scale=8.0,
                                                      scheduler_class=None)
    pipe.init_custom_adapter(num_views=6, self_attn_processor=DecoupledMVRowColSelfAttnProcessor2_0)
    pipe.load_custom_adapter(local("huanngzh/mv-adapter", IG2MV), weight_name="mvadapter_ig2mv_sdxl.safetensors")
    pipe.to(device="cuda", dtype=dtype)
    pipe.cond_encoder.to(device="cuda", dtype=dtype)
    pipe.enable_vae_slicing()

    control = torch.from_numpy(np.load(a.control)["control"]).to("cuda", dtype)
    for seed in seeds:
        out_dir = f"{a.out_dir}_s{seed}"
        os.makedirs(out_dir, exist_ok=True)
        ref.save(os.path.join(out_dir, "reference.png"))
        torch.cuda.reset_peak_memory_stats()
        t1 = time.time()
        images = pipe(a.text, height=768, width=768, num_inference_steps=a.steps, guidance_scale=a.guidance,
                      num_images_per_prompt=6, control_image=control, control_conditioning_scale=1.0,
                      reference_image=ref, reference_conditioning_scale=a.ref_scale,
                      negative_prompt="watermark, ugly, deformed, noisy, blurry, low contrast",
                      cross_attention_kwargs={"scale": 1.0},
                      generator=torch.Generator(device="cuda").manual_seed(seed)).images
        for n, im in zip(VIEWS, images):
            im.save(os.path.join(out_dir, f"view_{n}.png"))
        mv.grid(images, 3, 768).save(os.path.join(out_dir, "grid.png"))
        peak = torch.cuda.max_memory_allocated() / 2**30
        print(f"[ig2mv] 시드 {seed} 끝: {out_dir}, 그리기 {time.time() - t1:.0f} s, GPU 최대 {peak:.1f} GiB", flush=True)
    print(f"[ig2mv] 전체 {time.time() - t0:.0f} s")

if __name__ == "__main__":
    main()
