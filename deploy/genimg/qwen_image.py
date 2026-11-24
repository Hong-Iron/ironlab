"""Qwen-Image-2.1 로 그림 한 장을 만든다.

글만 주면 새 그림을 그리고(--prompt), 참고 그림을 함께 주면 그 그림을 고친다(--image, 최대 10장).

    python qwen_image.py --prompt "비 오는 밤의 네온 간판" --output out.png
    python qwen_image.py --prompt "배경을 노을 지는 바다로" --image in.png --output edit.png
    python qwen_image.py --prompt "귀여운 용 스티커" --transparent --output dragon.png

모델은 두 가지다. turbo(기본)는 8단계로 그려 빠르고, base 는 40단계로 그린다.
GPU 메모리가 24GB 인 카드에서는 글 이해 모델(약 16GB)과 그림 모델(약 13GB)을 한꺼번에 올릴 수 없다.
그래서 쓰는 쪽만 GPU 에 올리고 나머지는 RAM 에 두는 방식(enable_model_cpu_offload)으로 돈다.

그리는 과정은 세 토막이다. 조절 값도 그 토막을 따라 나뉜다.

1. 글 읽기:      글(과 참고 그림)을 모델이 알아듣는 숫자로 바꾼다.
2. 그리기:       잡음에서 시작해 단계마다 잡음을 걷어 낸다 (--steps, --sigmas, --true-cfg, --negative, --no-kv-cache).
3. 그림으로 풀기: 모델 안의 압축된 표현을 실제 픽셀로 바꾼다 (--tile).

참고 그림을 고칠 때 어떤 시드에서는 모델이 글을 따르지 않고 참고 그림을 베끼며 윤곽을 태운다.
각도 바꾸기에서는 그런 결과를 알아보고 시드를 하나씩 올려 두 번까지 다시 그린다 (--copy-retries, copy_ratio).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

MODELS = {"turbo": "Qwen/Qwen-Image-2.1-Turbo", "base": "Qwen/Qwen-Image-2.1"}
# 모델 카드가 지원한다고 적은 화면 비율과 크기 (가로, 세로)
ASPECTS = {"1:1": (2048, 2048), "4:3": (2400, 1792), "3:4": (1792, 2400), "3:2": (2528, 1696),
           "2:3": (1696, 2528), "16:9": (2752, 1536), "9:16": (1536, 2752)}
# 투명 배경 그림은 모델 카드가 권하는 문장을 앞뒤에 붙인다
RGBA_HEAD = "This is an RGBA image with transparency. "
RGBA_TAIL = " The image has alpha channel and the background is transparent."
MAX_IMAGES = 10
TILES = (0, 256, 512, 1024)
# 각도 바꾸기: 참고 그림의 물체를 다른 카메라 자리에서 다시 찍은 그림으로 그린다.
# Qwen-Image-2.1 Multiple-Angles LoRA v2 (dsaasaiaos/Qwen-Image-2.1-Multiple-Angles-LoRA, Apache-2.0, 1,500단계 판).
# 글은 LoRA 가 배운 꼴 '<mva> {방향}, {높이}[ close-up]' 이어야 한다 (모델 카드).
ANGLE_LORA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loras", "multiple-angles-v2-step1500.safetensors")
AZIMUTHS = {   # 옵션 값 → LoRA 가 배운 이름 (30° 간격, 정면 0° 에서 오른쪽으로 돈다)
    "front": "front view", "front-right": "front-right view", "front-right-quarter": "front-right quarter view",
    "right": "right side view", "back-right-quarter": "back-right quarter view", "back-right": "back-right view",
    "back": "back view", "back-left": "back-left view", "back-left-quarter": "back-left quarter view",
    "left": "left side view", "front-left-quarter": "front-left quarter view", "front-left": "front-left view",
}
ELEVATIONS = {"eye": "eye-level shot", "elevated": "elevated shot", "high": "high-angle shot", "top": "top-down shot"}
# 각도 LoRA 의 기본 세기. 기본판(base)은 바로 옆(90°, 270°)에서 1.0 이면 45~60° 까지만 돌고, 1.2 에서 거의 옆모습이 된다.
# 뒤와 위는 1.0 에서 잘 돈다 (나무 공구 상자 앞모습 한 장, 시드 42, 1024x1024 로 잰 값). Turbo 는 1.0 에서 거의 돌지 않아 1.6 을 쓴다.
SIDE_VIEWS = ("left", "right")
LORA_SCALE_BASE = 1.0
LORA_SCALE_BASE_SIDE = 1.2
LORA_SCALE_TURBO = 1.6
# 참고 그림을 고칠 때, 어떤 시작 잡음(시드)에서는 모델이 글을 따르지 않고 참고 그림을 거의 그대로 베낀다.
# 그렇게 베낀 그림은 털·잎 같은 윤곽이 지나치게 세지고 색이 타 버린 것처럼 보인다.
# 탄 자국은 한 픽셀 단위의 잡음이 아니라 털 몇 가닥 굵기의 굵은 붓질이다. 그래서 두 그림을 긴 변 256px 로 줄인 뒤
# 윤곽 세기(라플라시안 절댓값 평균)를 견준다. 2026-11-15 에 개 사진 두 장·공구 상자 한 장으로 그린 37장에서,
# 탄 12장은 참고 그림의 4.3~5.8배, 멀쩡한 25장은 0.65~1.2배였다 (1024px 로 재면 0.91~1.12 와 0.59~0.94 로 겹쳤다).
# 같은 사진·같은 설정에서 시드 42 는 늘 탔고, 시드 1 과 2 는 깨끗하게 돌았다. 각도 LoRA 세기(1.5)나 가이던스(4)를 올려도 그대로였다.
COPY_EDGE_SIZE = 256
COPY_EDGE_LIMIT = 2.0
COPY_RETRIES_ANGLE = 2              # 각도 바꾸기에서 기준을 넘으면 시드를 하나씩 올려 다시 그리는 횟수


def say(text: str) -> None:
    print(f"[qwen-image] {text}", flush=True)


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"크기는 '가로x세로' 꼴이어야 합니다 (받은 값 {text!r}). 예: 2048x2048") from None
    if not (256 <= w <= 4096 and 256 <= h <= 4096) or w % 16 or h % 16:
        raise argparse.ArgumentTypeError(f"가로와 세로는 256~4096 사이의 16의 배수여야 합니다 (받은 값 {w}x{h})")
    return w, h


def parse_sigmas(text: str) -> list[float]:
    """잡음 시간표: 1 에서 시작해 내려가는 수를 쉼표로 이은 글. 수의 개수가 그리는 단계 수가 된다."""
    try:
        values = [float(v) for v in text.split(",") if v.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"시간표는 쉼표로 이은 수여야 합니다 (받은 값 {text!r}). 예: 1,0.95,0.85,0.6") from None
    if not 1 <= len(values) <= 200 or any(not 0 < v <= 1 for v in values) or values != sorted(values, reverse=True):
        raise argparse.ArgumentTypeError(
            f"시간표는 0 보다 크고 1 이하인 수 1~200개를 큰 것부터 적어야 합니다 (받은 값 {text!r})")
    return values


def read_text(path: str | None) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Qwen-Image-2.1 로 그림 한 장(또는 --batch-file 로 여러 장)을 만든다")
    ap.add_argument("--prompt", help="그릴 내용 (참고 그림이 있으면 어떻게 고칠지)")
    ap.add_argument("--prompt-file", help="그릴 내용을 적은 글 파일 (UTF-8). 따옴표가 섞인 긴 글은 이쪽이 안전하다")
    ap.add_argument("--negative", help="그림에 나오지 않았으면 하는 것. --true-cfg 가 1 보다 클 때만 듣는다")
    ap.add_argument("--negative-file", help="피할 내용을 적은 글 파일 (UTF-8)")
    ap.add_argument("--output", required=True, help="결과 PNG 경로")
    ap.add_argument("--model", choices=sorted(MODELS), default="turbo", help="turbo: 8단계로 빠르게, base: 40단계")
    ap.add_argument("--aspect", choices=sorted(ASPECTS), default="1:1", help="화면 비율 (모델 카드가 지원하는 크기를 쓴다)")
    ap.add_argument("--size", type=parse_size, help="크기를 직접 정할 때: 가로x세로 (예: 1024x1024). --aspect 보다 우선")
    ap.add_argument("--steps", type=int,
                    help="그리는 단계 수. 주지 않으면 base 는 40, turbo 는 모델에 저장된 8단계 시간표를 쓴다. "
                         "turbo 에 주면 그 시간표 대신 1 에서 고르게 내려가는 시간표를 쓴다 (8단계로 해 보니 그림이 흐려졌다. "
                         "turbo 의 단계를 바꿀 때는 --sigmas 가 낫다)")
    ap.add_argument("--sigmas", type=parse_sigmas,
                    help="잡음 시간표를 직접 줄 때: 1 에서 시작해 내려가는 수를 쉼표로 (예: 1,0.95,0.85,0.6). --steps 보다 우선")
    ap.add_argument("--true-cfg", type=float,
                    help="글을 얼마나 세게 따를지 (기본 1 = 끔). 1 보다 크면 단계마다 두 번 계산해 시간이 두 배쯤 든다")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--image", action="append", default=[], help=f"참고 그림. 여러 번 쓸 수 있다 (최대 {MAX_IMAGES}장)")
    ap.add_argument("--transparent", action="store_true", help="배경이 투명한 그림(RGBA)으로 만든다")
    ap.add_argument("--kv-cache", action=argparse.BooleanOptionalAction, default=True,
                    help="글과 참고 그림의 계산을 첫 단계에서 한 번만 하고 다시 쓴다 (기본 켬). 끄면 느려지고 그림이 조금 달라진다")
    ap.add_argument("--tile", type=int, choices=TILES, default=1024,
                    help="마지막에 픽셀로 풀 때 한 번에 푸는 조각의 한 변 (기본 1024). 0 이면 나누지 않는다")
    ap.add_argument("--thumb", help="목록에 보일 작은 미리보기(긴 변 320px JPEG)를 함께 쓸 경로. 콘솔의 이미지 생성 화면이 쓴다")
    ap.add_argument("--view", choices=tuple(AZIMUTHS),
                    help="각도 바꾸기: 참고 그림의 물체를 이 방향에서 본 모습으로 그린다 (각도 LoRA 를 붙인다). 참고 그림이 있어야 한다")
    ap.add_argument("--elevation", choices=tuple(ELEVATIONS), default="eye", help="각도 바꾸기의 카메라 높이 (eye 눈높이 ~ top 바로 위)")
    ap.add_argument("--close-up", action="store_true", help="각도 바꾸기에서 가까이 찍은 모습으로")
    ap.add_argument("--lora-scale", type=float,
                    help="각도 LoRA 의 세기. 주지 않으면 turbo 1.6, base 는 바로 옆(left, right) 1.2, 그 밖의 방향 1.0. "
                         "base 의 옆모습은 1.0 에서 45~60° 까지만 돌았다. 결과가 참고 그림을 그대로 베끼면 올린다")
    ap.add_argument("--angle-text", action="store_true",
                    help="각도 바꾸기에서 그릴 내용 글을 각도 글 뒤에 붙인다. 기본은 붙이지 않고 각도 글만 쓴다 "
                         "(설명 글을 붙이면 옆모습이 앞모습 그대로 나왔다)")
    ap.add_argument("--copy-retries", type=int,
                    help="참고 그림을 고친 결과가 참고 그림을 거의 그대로 베끼고 윤곽만 지나치게 세졌을 때, 시드를 하나씩 올려 "
                         f"다시 그리는 횟수 (0~5). 주지 않으면 각도 바꾸기는 {COPY_RETRIES_ANGLE}, 그 밖의 고치기는 0 "
                         "(0 이어도 그런 결과면 기록에 알린다)")
    return ap


class BadRequest(ValueError):
    pass


def prepare(argv: list[str]):
    """옵션 한 벌을 읽고 검사한다. 틀리면 BadRequest (묶음에서는 그 한 장만 빠진다)."""
    ap = build_parser()
    ap.error = lambda msg: (_ for _ in ()).throw(BadRequest(msg))          # 끝내지 말고 그 한 장만 실패로 돌린다
    args = ap.parse_args(argv)
    if len(args.image) > MAX_IMAGES:
        ap.error(f"참고 그림은 {MAX_IMAGES}장까지입니다 (받은 수 {len(args.image)}장)")
    if args.steps is not None and not 1 <= args.steps <= 200:
        ap.error(f"단계 수는 1~200 사이여야 합니다 (받은 값 {args.steps})")
    if args.true_cfg is not None and not 1 <= args.true_cfg <= 20:
        ap.error(f"--true-cfg 는 1~20 사이여야 합니다 (받은 값 {args.true_cfg})")
    if bool(args.prompt) == bool(args.prompt_file):
        ap.error("--prompt 와 --prompt-file 가운데 하나만 주세요")
    if args.negative and args.negative_file:
        ap.error("--negative 와 --negative-file 가운데 하나만 주세요")
    if args.copy_retries is not None and not 0 <= args.copy_retries <= 5:
        ap.error(f"--copy-retries 는 0~5 사이여야 합니다 (받은 값 {args.copy_retries})")
    args.text = args.prompt or read_text(args.prompt_file)
    if not args.text:
        ap.error("그릴 내용이 비어 있습니다")
    # 카메라 높이와 '가까이'는 각도 글의 일부라 보는 방향이 있어야 듣는다.
    # 높이만 골랐으면 사용자는 각도를 바꾸려던 것이므로, 참고 그림이 있을 때 방향을 정면으로 채운다.
    if not args.view and (args.elevation != "eye" or args.close_up):
        if args.image:
            args.view = "front"
            say("각도 바꾸기: 보는 방향을 고르지 않아 정면으로 둡니다 (카메라 높이와 '가까이'는 보는 방향이 있어야 듣습니다)")
        else:
            say("참고: 카메라 높이와 '가까이'는 참고 그림을 넣어 각도를 바꿀 때만 듣습니다. 이번에는 쓰지 않습니다.")
    if args.view:
        if args.lora_scale is None:
            if args.model == "turbo":
                args.lora_scale = LORA_SCALE_TURBO
            else:
                args.lora_scale = LORA_SCALE_BASE_SIDE if args.view in SIDE_VIEWS else LORA_SCALE_BASE
        if not args.image:
            ap.error("각도 바꾸기는 참고 그림이 있어야 합니다. 다른 방향에서 볼 물체의 사진을 참고 그림으로 넣으세요")
        if len(args.image) > 1:
            # 각도 LoRA 는 사진 한 장을 다른 자리에서 다시 찍는 법으로 배웠다. 여러 장을 섞어 주면 무엇을 돌릴지 정해지지 않는다.
            say(f"각도 바꾸기: 참고 그림 {len(args.image)}장 가운데 첫 장({os.path.basename(args.image[0])})만 씁니다 "
                "(각도 LoRA 는 사진 한 장을 돌려 보는 법으로 배웠습니다)")
            args.image = args.image[:1]
        if not 0.3 <= args.lora_scale <= 2.0:
            ap.error(f"--lora-scale 은 0.3~2.0 이어야 합니다 (받은 값 {args.lora_scale})")
        if not os.path.isfile(ANGLE_LORA):
            ap.error(f"각도 LoRA 파일이 없습니다: {ANGLE_LORA}")
        # LoRA 는 '<mva> 방향, 높이' 글로 배웠다. 사용자가 적은 글은 --angle-text 를 줄 때만 그 뒤에 덧붙인다.
        # 설명 글을 붙이면 뒤·위는 돌았지만 옆모습은 앞모습 그대로 나왔다.
        pose = f"<mva> {AZIMUTHS[args.view]}, {ELEVATIONS[args.elevation]}" + (" close-up" if args.close_up else "")
        user_text = args.text.strip()
        if user_text in ("", "-"):
            args.text = pose
        elif args.angle_text:
            args.text = f"{pose}. {user_text}"
        else:
            say("각도 바꾸기: 그릴 내용 글은 쓰지 않고 각도 글만 씁니다 (설명 글을 붙이면 옆모습이 돌지 않습니다. "
                "붙이려면 --angle-text)")
            args.text = pose
    if args.copy_retries is None:
        args.copy_retries = COPY_RETRIES_ANGLE if args.view else 0
    args.negative_text = args.negative or read_text(args.negative_file)
    args.width, args.height = args.size or ASPECTS[args.aspect]
    for path in (args.output, args.thumb):
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return args


def load_pipe(model: str):
    import torch
    from diffusers import QwenImage21Pipeline

    started = time.time()
    pipe = QwenImage21Pipeline.from_pretrained(MODELS[model], torch_dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload()
    say(f"모델을 읽었습니다: {MODELS[model]}, {time.time() - started:.0f} s")
    return pipe


def set_angle_lora(pipe, args) -> None:
    """각도 바꾸기면 각도 LoRA 를 켜고 세기를 맞춘다. 아니면 끈다. 한 번 읽은 LoRA 는 묶음 안에서 다시 읽지 않는다."""
    on = bool(getattr(args, "view", None))
    if on and not getattr(pipe, "_angle_lora", False):
        started = time.time()
        pipe.load_lora_weights(ANGLE_LORA, adapter_name="angles")
        pipe._angle_lora = True
        say(f"각도 LoRA 를 붙였습니다: {os.path.basename(ANGLE_LORA)}, {time.time() - started:.0f} s")
    if getattr(pipe, "_angle_lora", False):
        pipe.set_adapters(["angles"], [args.lora_scale if on else 0.0])


def edge_energy(image, size: tuple[int, int]) -> float:
    """그림을 size 로 줄인 흑백 그림에서, 칸마다 '이웃 넷의 평균과 얼마나 다른지'(라플라시안 절댓값)의 평균. 윤곽이 셀수록 크다."""
    import numpy as np
    from PIL import Image

    g = np.asarray(image.convert("L").resize(size, Image.LANCZOS), dtype=np.float32) / 255
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(np.abs(lap).mean())


def copy_ratio(image, ref) -> float:
    """결과의 윤곽 세기를 참고 그림의 것으로 나눈 값. COPY_EDGE_LIMIT 이상이면 참고 그림을 베끼고 타 버린 그림으로 본다.

    두 그림을 결과 그림 모양으로, 긴 변 COPY_EDGE_SIZE(256px)에 맞춰 똑같이 줄여 잰다 (크기가 다르면 윤곽 세기를 견줄 수 없다).
    예: 참고 그림 0.020, 결과 0.096 이면 4.8 → 다시 그린다. 결과 0.016 이면 0.8 → 그대로 둔다.
    """
    scale = COPY_EDGE_SIZE / max(image.size)
    size = (max(16, round(image.width * scale)), max(16, round(image.height * scale)))
    ref_energy = edge_energy(ref, size)
    return edge_energy(image, size) / ref_energy if ref_energy > 1e-6 else 0.0


def draw(pipe, args) -> None:
    """준비한 옵션 한 벌로 그림 한 장을 그려 저장한다."""
    import numpy as np
    import torch
    from PIL import Image

    # 마지막에 잠재 표현을 그림으로 바꾸는 단계(VAE)는 2048x2048 을 한 번에 풀면 24GB 로도 모자란다
    # (CUDA out of memory, 4.5GiB 를 더 잡으려다 실패). 조각으로 나눠 풀게 한다.
    # 조각이 256px(192px 간격)이면 2048x2048 에 가로세로 11줄씩 이음매가 생긴다. 어두운 면을 밝게 늘려 보면
    # 격자가 비친다. 기본은 1024px(768px 간격, 256px 겹침)로, 이음매가 가로세로 2줄이다.
    if args.tile:
        stride = args.tile * 3 // 4
        pipe.vae.enable_tiling(tile_sample_min_height=args.tile, tile_sample_min_width=args.tile,
                               tile_sample_stride_height=stride, tile_sample_stride_width=stride)
    else:
        pipe.vae.disable_tiling()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()            # 묶음에서 장마다 따로 잰다
    set_angle_lora(pipe, args)
    refs = [Image.open(p).convert("RGBA") for p in args.image]
    prompt = RGBA_HEAD + args.text + RGBA_TAIL if args.transparent else args.text
    say(f"시작: 모델 {MODELS[args.model]}, 크기 {args.width}x{args.height}, 시드 {args.seed}, 참고 그림 {len(refs)}장"
        + (f", 각도 {args.view}/{args.elevation}{' 가까이' if args.close_up else ''} (LoRA 세기 {args.lora_scale:g})" if args.view else ""))
    call = dict(prompt=prompt, width=args.width, height=args.height, use_kv_cache=args.kv_cache)
    if refs:
        call["image"] = refs[0] if len(refs) == 1 else refs
    # 단계 수와 시간표. 모델은 "시간표의 길이 = 단계 수"로 그린다.
    # turbo 는 모델에 저장된 8단계 시간표(sample_sigmas)를 쓰도록 되어 있어, 단계 수만 주면 무시한다.
    # 그래서 turbo 에 단계 수를 주면 base 가 쓰는 것과 같은 꼴(1 에서 1/단계 수 까지 고르게)의 시간표를 직접 넘긴다.
    if args.sigmas:
        call["sigmas"] = args.sigmas
        plan = f"직접 준 시간표 {len(args.sigmas)}단계"
    elif args.steps is not None:
        if args.model == "turbo":
            call["sigmas"] = np.linspace(1.0, 1 / args.steps, args.steps).tolist()
        else:
            call["num_inference_steps"] = args.steps
        plan = f"{args.steps}단계 (고르게 내려가는 시간표)"
    else:
        plan = "모델에 저장된 8단계 시간표" if args.model == "turbo" else "40단계"
    if args.true_cfg is not None and args.true_cfg > 1:
        call["true_cfg_scale"] = args.true_cfg
        # 가이던스는 "피할 내용"으로 계산한 결과에서 멀어지는 방식이라 피할 내용이 있어야 켜진다.
        # 적지 않았으면 빈 글(공백 한 칸)을 넣는다. 모델 카드의 예제가 쓰는 방식이다.
        call["negative_prompt"] = args.negative_text or " "
    elif args.negative_text:
        say("참고: 피할 내용은 가이던스(--true-cfg)가 1 보다 클 때만 듣습니다. 이번에는 쓰지 않습니다.")
    say(f"그리기: {plan}, 가이던스 {call.get('true_cfg_scale', 1):g}, 조각 {args.tile or '나누지 않음'}"
        + ("" if args.kv_cache else ", 계산 재사용 끔"))
    drawing = time.time()
    # 참고 그림을 베끼고 탄 결과면 시드를 하나씩 올려 다시 그린다. 모두 기준을 넘으면 그중 윤곽 비율이 가장 낮은 장을 남긴다.
    tries = 1 + (args.copy_retries if refs else 0)
    best = None                                         # (윤곽 비율, 시드, 그림)
    for k in range(tries):
        seed = args.seed + k
        if k:
            say(f"다시 그립니다 ({k}/{tries - 1}): 시드 {seed}")
        call["generator"] = torch.Generator("cpu").manual_seed(seed)
        image = pipe(**call).images[0]
        if not refs:
            best = (0.0, seed, image)
            break
        ratio = copy_ratio(image, refs[0])
        if best is None or ratio < best[0]:
            best = (ratio, seed, image)
        if ratio < COPY_EDGE_LIMIT:
            if k:
                say(f"시드 {seed} 의 결과는 참고 그림을 베끼지 않았습니다 (윤곽 비율 {ratio:.2f})")
            break
        note = (f"시드 {seed} 의 결과가 참고 그림을 거의 그대로 베끼고 윤곽만 지나치게 세졌습니다 "
                f"(윤곽 비율 {ratio:.2f}, 기준 {COPY_EDGE_LIMIT:g} 이상).")
        if k + 1 < tries:
            say(note + " 시작 잡음을 바꿔 다시 그립니다.")
        elif tries > 1:
            say(note + f" {tries}번 그려도 나아지지 않아 윤곽 비율이 가장 낮은 시드 {best[1]} 의 그림을 남깁니다. "
                       "다른 시드나 다른 참고 그림을 써 보세요.")
        else:
            say(note + " 그림이 타 보이면 다른 시드로 다시 그려 보세요 (일부러 윤곽을 세게 고친 것이면 그대로 두면 됩니다).")
    _, seed, image = best
    # 모델은 늘 투명도 채널이 붙은 그림(RGBA)을 낸다. 투명 배경을 청하지 않은 그림에서도 그 채널이 군데군데
    # 255 보다 조금 낮게 나와(잰 최솟값 190~207), 그대로 두면 보는 프로그램에 따라 살짝 비쳐 보인다.
    # 투명 배경을 청했을 때만 그 채널을 남기고, 아니면 버리고 RGB 로 저장한다.
    if not args.transparent and image.mode == "RGBA":
        image = image.convert("RGB")
    image.save(args.output)
    if args.thumb:
        small = image.convert("RGBA")
        small.thumbnail((320, 320))
        flat = Image.new("RGB", small.size, (255, 255, 255))      # 투명한 곳은 흰색으로 채운다
        flat.paste(small, mask=small.getchannel("A"))
        flat.save(args.thumb, quality=82)
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else 0.0
    say(f"끝: {args.output} ({image.width}x{image.height}, {image.mode})"
        + (f", 시드 {seed} (처음 시드 {args.seed} 대신)" if seed != args.seed else "")
        + f", 그리는 데 {time.time() - drawing:.0f} s, GPU 메모리 최대 {peak:.1f} GiB")


def free_gpu() -> None:
    import gc

    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def run_batch(path: str) -> int:
    """묶음 파일의 그림들을 차례로 그린다. 모델은 처음 한 번만 올리고, 모델이 바뀔 때만 다시 올린다.

    묶음 파일은 JSON 배열이고, 칸 하나가 그림 한 장의 옵션 목록이다 (한 장을 만들 때와 같은 옵션).
    예: [["--prompt-file", "a.txt", "--output", "a.png"], ["--prompt-file", "b.txt", "--output", "b.png", "--seed", "7"]]
    한 장이 틀리거나 실패해도 나머지는 그린다. 하나라도 실패하면 종료 코드 1.
    """
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list) or not items or not all(isinstance(i, list) and all(isinstance(t, str) for t in i) for i in items):
        say(f"실패: 묶음 파일은 옵션 목록의 배열이어야 합니다 ({path})")
        return 2
    started = time.time()
    say(f"묶음: {len(items)}장을 한 번에 그립니다 (모델을 한 번만 올립니다)")
    pipe, loaded, failed = None, None, 0
    for n, argv in enumerate(items, 1):
        say(f"묶음 {n}/{len(items)}")
        try:
            args = prepare(argv)
        except (BadRequest, OSError) as e:
            say(f"실패 ({n}번째): {e}")
            failed += 1
            continue
        try:
            if loaded != args.model:
                pipe = None                                 # 앞 모델을 놓아 RAM·GPU 메모리를 비운 뒤 새로 올린다
                free_gpu()
                pipe, loaded = load_pipe(args.model), args.model
            draw(pipe, args)
        except Exception as e:                              # 한 장이 실패해도 (메모리 부족 등) 나머지는 그린다
            say(f"실패 ({n}번째, {args.output}): {type(e).__name__}: {e}")
            failed += 1
            free_gpu()                                      # 실패한 장이 잡고 있던 GPU 메모리를 비우고 다음 장을 그린다
    say(f"묶음 끝: {len(items) - failed}/{len(items)}장, 전체 {time.time() - started:.0f} s")
    return 1 if failed else 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--batch-file":
        if len(sys.argv) != 3:
            print("사용법: qwen_image.py --batch-file <묶음.json>", file=sys.stderr)
            return 2
        return run_batch(sys.argv[2])
    try:
        args = prepare(sys.argv[1:])
    except BadRequest as e:
        build_parser().error(str(e))
    started = time.time()
    pipe = load_pipe(args.model)
    draw(pipe, args)
    say(f"전체 {time.time() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
