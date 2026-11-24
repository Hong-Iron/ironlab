#!/usr/bin/env python3
"""Sony AI 의 Woosh 로 효과음을 만든다. ironlab 의 '효과음 생성' 화면과 '효과음 만들기' 노드가 woosh.sh 를 거쳐 부른다.

두 가지로 만든다.

1. 글만 주면: 영어로 적은 소리 설명대로 5초까지의 효과음을 만든다 (Woosh-DFlow, Woosh-Flow).
2. 영상을 함께 주면: 영상의 움직임에 맞춰 소리를 만들고(8초까지), 그 소리를 영상에 붙인 MP4 도 쓴다
   (Woosh-DVFlow-8s, Woosh-VFlow-8s). 글은 있어도 되고 없어도 된다.

모델마다 빠른 판과 본판이 있다. 빠른 판(fast)은 본판을 4단계로 만들도록 다시 배운 것(증류)이고, 본판(full)은
계산 횟수를 스스로 정하는 미분방정식 풀이로 약 70번 계산한다. 결과는 48 kHz 모노 WAV 다.

게임에 바로 넣기 좋게 다듬는 일도 한다: 앞뒤 무음 자르기, 끝을 부드럽게 줄이기, 소리 크기 맞추기,
끝과 처음을 겹쳐 이음매 없이 되풀이되는 소리(배경 소리용) 만들기.

모델 파일은 ~/models/woosh 에 둔다 (WOOSH_MODELS 로 바꿀 수 있다). 그 안의 checkpoints/<모델>/ 이 Sony 가 공개한
가중치이고, hf/ 는 글 이해 모델(roberta-large)의 글자 사전과 영상 특징 모델(Synchformer)의 파일이다.
가중치의 라이선스는 CC BY-NC 4.0 이다: 만든 소리를 상업용으로 쓸 수 없다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.dirname(HERE)
MODEL_ROOT = os.path.abspath(os.environ.get("WOOSH_MODELS", os.path.join(HOME_DIR, "models", "woosh")))
MEDIA_BIN = os.environ.get("WOOSH_MEDIA_BIN", os.path.join(HOME_DIR, ".conda", "envs", "media", "bin"))

# 글 이해 모델과 영상 특징 모델의 파일은 모델 폴더 안에서 찾는다 (작업 안에서는 인터넷을 쓰지 않는다).
# 처음 설치할 때만 WOOSH_ONLINE=1 로 내려받는다. 이 값들은 transformers 를 부르기 전에 정해야 한다.
os.environ["HF_HUB_CACHE"] = os.path.join(MODEL_ROOT, "hf")
os.environ["HF_HUB_OFFLINE"] = "0" if os.environ.get("WOOSH_ONLINE") == "1" else "1"
os.environ["TRANSFORMERS_OFFLINE"] = os.environ["HF_HUB_OFFLINE"]
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

SR = 48000                      # 소리 표본 수 (1초에)
LATENT_FPS = 100                # 모델이 다루는 잠재 칸 100개가 소리 1초 (48,000 ÷ 480)
TEXT_SECONDS = 5.0              # 글로 만드는 모델이 배운 길이
VIDEO_SECONDS = 8.0             # 영상으로 만드는 모델이 배운 길이
MODELS = {                      # (입력, 판) → (체크포인트 폴더, 불러오는 방식, 기본 가이던스)
    ("text", "fast"): ("Woosh-DFlow", "flowmap", 4.5),
    ("text", "full"): ("Woosh-Flow", "ldm", 4.5),
    ("video", "fast"): ("Woosh-DVFlow-8s", "flowmap", 3.0),
    ("video", "full"): ("Woosh-VFlow-8s", "kontext", 4.5),
}
SOLVERS = ("dopri5", "dopri8", "bosh3", "adaptive_heun")
SEGMENT_BATCH = 4               # 영상 특징 모델(Synchformer)이 한 번에 읽는 영상 조각 수
STAGES = ("모델 올리기", "글과 영상 읽기", "소리 만들기", "다듬어 저장하기")
HANGUL_RE = re.compile(r"[가-힣ㄱ-ㆎ]")


def say(msg: str) -> None:
    print(f"[woosh] {msg}", flush=True)


def stage(n: int, extra: str = "") -> None:
    say(f"단계 {n}/{len(STAGES)}: {STAGES[n - 1]}{extra}")


def fail(msg: str, code: int = 1) -> None:
    say(f"실패: {msg}")
    sys.exit(code)


def read_text(path: str | None) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return " ".join(f.read().split())


def renoise_list(raw: str | None, steps: int) -> list[float]:
    """단계마다 잡음을 다시 섞는 양. 주지 않으면 Sony 가 쓴 [0, 0.5, 0.5, 0.3] 을 단계 수에 맞춰 늘이거나 줄인다."""
    if raw:
        try:
            values = [float(v) for v in raw.split(",") if v.strip()]
        except ValueError:
            fail(f"--renoise 는 쉼표로 이은 수여야 합니다 (받은 값 {raw!r}). 예: 0,0.5,0.5,0.3", 2)
        if len(values) != steps or any(not 0 <= v <= 1 for v in values):
            fail(f"--renoise 는 0~1 사이의 수를 단계 수({steps})만큼 적어야 합니다 (받은 값 {raw!r})", 2)
        return values
    if steps == 1:
        return [0.0]
    return [0.0] + [0.5] * (steps - 2) + [0.3]


def video_length(path: str) -> float:
    import av
    with av.open(path) as c:
        v = next((s for s in c.streams if s.type == "video"), None)
        if v is None:
            fail(f"영상이 들어 있지 않은 파일입니다 ({os.path.basename(path)})", 2)
        if c.duration:
            return c.duration / 1_000_000
        if v.duration and v.time_base:
            return float(v.duration * v.time_base)
    fail(f"영상의 길이를 읽지 못했습니다 ({os.path.basename(path)})", 2)


def trim_silence(x, floor_db: float, keep_ms: float = 10.0, tail_ms: float = 60.0):
    """앞뒤의 조용한 부분을 자른다. 10 ms 마다 소리 세기(RMS)를 재서, 가장 큰 곳보다 floor_db 이상 작은 구간을 무음으로 본다."""
    import numpy as np
    win = SR // 100
    n = len(x) // win
    if n < 2:
        return x
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(rms / rms.max()) > -abs(floor_db))[0]
    if len(loud) == 0:
        return x
    a = max(0, loud[0] * win - int(SR * keep_ms / 1000))
    b = min(len(x), (loud[-1] + 1) * win + int(SR * tail_ms / 1000))
    return x[a:b]


def make_loop(x, cross_s: float):
    """끝과 처음을 겹쳐, 되풀이해 틀어도 이음매가 들리지 않는 소리를 만든다. 길이는 겹친 만큼 줄어든다.

    끝부분(cross_s 초)을 처음 부분 위에 점점 작아지게 겹친다. 그러면 결과의 끝은 원래 소리에서 바로 이어지는 곳이
    결과의 처음이 된다. 바람·빗소리처럼 고르게 이어지는 소리에 맞고, 발소리처럼 한 번 나고 끝나는 소리에는 맞지 않는다.
    """
    import numpy as np
    xf = int(SR * cross_s)
    if xf <= 0 or len(x) < 3 * xf:
        fail(f"되풀이 소리를 만들기에 소리가 너무 짧습니다 ({len(x) / SR:.2f}초, 겹치는 길이 {cross_s:g}초의 3배 이상이어야 합니다). "
             "겹치는 길이를 줄이거나 앞뒤 무음 자르기를 끄세요.", 2)
    t = np.linspace(0, 1, xf, endpoint=False, dtype=np.float32)
    fade_in, fade_out = np.sqrt(t), np.sqrt(1 - t)     # 서로 닮지 않은 두 소리를 겹칠 때 세기가 꺼지지 않는 곡선
    head = x[:xf] * fade_in + x[len(x) - xf:] * fade_out
    return np.concatenate([head, x[xf:len(x) - xf]])


def ffmpeg_path() -> str | None:
    for p in (os.path.join(MEDIA_BIN, "ffmpeg"), shutil.which("ffmpeg")):
        if p and os.path.isfile(p):
            return p
    return None


def run_ffmpeg(ff: str, args: list[str], what: str) -> None:
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"{what}에 실패했습니다: {r.stderr.strip()[-300:]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Woosh (Sony AI): 글이나 영상으로 효과음을 만든다")
    ap.add_argument("--prompt-file", help="소리 설명 (영어). 영상을 줄 때는 없어도 된다")
    ap.add_argument("--prompt", help="소리 설명을 파일 대신 바로 준다")
    ap.add_argument("--negative-file", help="나오지 않았으면 하는 소리 (본판에서만 듣는다)")
    ap.add_argument("--video", help="이 영상에 맞춰 소리를 만든다 (앞 8초까지)")
    ap.add_argument("--video-start", type=float, default=0.0, help="영상에서 소리를 만들기 시작할 곳 (초)")
    ap.add_argument("--output", required=True, help="결과 WAV 경로")
    ap.add_argument("--mp3", help="들어 보기용 MP3 경로")
    ap.add_argument("--video-out", help="소리를 붙인 영상(MP4) 경로. 영상을 준 때만 쓴다")
    ap.add_argument("--thumb", help="영상의 작은 미리보기(JPG) 경로. 영상을 준 때만 쓴다")
    ap.add_argument("--model", choices=("fast", "full"), default="fast", help="fast: 4단계 증류판, full: 본판")
    ap.add_argument("--seconds", type=float, help="소리 길이 (초). 글만이면 5초까지, 영상이면 8초까지")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cfg", type=float, help="가이던스. 비우면 글 4.5, 영상 빠른 판 3.0, 영상 본판 4.5")
    ap.add_argument("--steps", type=int, default=4, help="빠른 판의 단계 수")
    ap.add_argument("--renoise", help="빠른 판에서 단계마다 잡음을 다시 섞는 양 (쉼표로 이은 수, 단계 수만큼)")
    ap.add_argument("--solver", choices=SOLVERS, default="dopri5", help="본판의 미분방정식 풀이법")
    ap.add_argument("--tolerance", type=float, default=1e-3, help="본판의 허용 오차 (작을수록 더 많이 계산한다)")
    ap.add_argument("--trim-silence", type=float, metavar="DB", help="가장 큰 곳보다 DB 이상 작은 앞뒤 구간을 자른다 (예: 50)")
    ap.add_argument("--fade-in", type=float, default=0.0, help="처음을 이만큼(ms) 키우며 시작한다")
    ap.add_argument("--fade-out", type=float, default=0.0, help="끝을 이만큼(ms) 줄이며 끝낸다")
    ap.add_argument("--loop", action="store_true", help="끝과 처음을 겹쳐 이음매 없이 되풀이되는 소리로 만든다")
    ap.add_argument("--loop-crossfade", type=float, default=0.5, help="되풀이 소리에서 끝과 처음을 겹치는 길이 (초)")
    ap.add_argument("--peak", type=float, metavar="DBFS", help="가장 큰 곳이 이 크기가 되게 맞춘다 (예: -1)")
    a = ap.parse_args()

    t0 = time.time()
    prompt = a.prompt or read_text(a.prompt_file)
    negative = read_text(a.negative_file)
    mode = "video" if a.video else "text"
    if mode == "text" and not prompt:
        fail("소리 설명이 비어 있습니다. --prompt-file 이나 --video 를 주세요.", 2)
    if prompt and HANGUL_RE.search(prompt):
        say("주의: 소리 설명에 한글이 있습니다. Woosh 는 영어 설명으로 배웠으므로 한글은 거의 알아듣지 못합니다.")
    ckpt, how, cfg_default = MODELS[(mode, a.model)]
    cfg = cfg_default if a.cfg is None else a.cfg
    if not 0 <= cfg <= 15:
        fail(f"--cfg 는 0~15 여야 합니다 (받은 값 {cfg:g})", 2)
    if not 1 <= a.steps <= 16:
        fail(f"--steps 는 1~16 이어야 합니다 (받은 값 {a.steps})", 2)
    if not 0.05 <= a.loop_crossfade <= 2:
        fail(f"--loop-crossfade 는 0.05~2초여야 합니다 (받은 값 {a.loop_crossfade:g})", 2)

    limit = VIDEO_SECONDS if mode == "video" else TEXT_SECONDS
    clip = limit
    if mode == "video":
        if not os.path.isfile(a.video):
            fail(f"영상 파일이 없습니다 ({a.video})", 2)
        total = video_length(a.video)
        if a.video_start < 0 or a.video_start >= total - 0.5:
            fail(f"영상 시작 위치가 영상 밖입니다 (받은 값 {a.video_start:g}초, 영상 길이 {total:.2f}초)", 2)
        clip = min(VIDEO_SECONDS, total - a.video_start)
    seconds = clip if a.seconds is None else a.seconds
    if not 0.1 <= seconds <= clip + 1e-6:
        if mode == "video":
            fail(f"소리 길이는 0.1초부터 쓸 수 있는 영상 길이 {clip:.2f}초까지입니다 (받은 값 {seconds:g}초)", 2)
        fail(f"글로 만드는 소리는 0.1~{limit:g}초입니다 (받은 값 {seconds:g}초). 모델이 5초 조각으로 배웠습니다.", 2)

    paths = {k: (os.path.abspath(v) if v else None) for k, v in
             (("output", a.output), ("mp3", a.mp3), ("video", a.video), ("video_out", a.video_out), ("thumb", a.thumb))}
    for k in ("output", "mp3", "video_out", "thumb"):
        if paths[k]:
            os.makedirs(os.path.dirname(paths[k]), exist_ok=True)
    if not os.path.isdir(os.path.join(MODEL_ROOT, "checkpoints", ckpt)):
        fail(f"모델 파일이 없습니다 ({MODEL_ROOT}/checkpoints/{ckpt}). setup_woosh.sh 로 내려받으세요.")
    say(f"시작: {'영상' if mode == 'video' else '글'}로 만들기, 모델 {ckpt}, 길이 {seconds:.2f}초, 시드 {a.seed}, 가이던스 {cfg:g}")

    stage(1, f" ({ckpt})")
    import numpy as np
    import soundfile as sf
    import torch
    from woosh.components.base import LoadConfig

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        say("주의: GPU 가 보이지 않아 CPU 로 만듭니다. 몇 분 걸릴 수 있습니다.")
    os.chdir(MODEL_ROOT)                    # 체크포인트 설정 파일이 checkpoints/... 를 이 폴더 기준으로 적는다
    t_load = time.time()
    if how == "flowmap":
        from woosh.model.flowmap_from_pretrained import FlowMapFromPretrained
        ldm = FlowMapFromPretrained(LoadConfig(path=f"checkpoints/{ckpt}"))
    elif how == "ldm":
        from woosh.model.ldm import LatentDiffusionModel
        ldm = LatentDiffusionModel(LoadConfig(path=f"checkpoints/{ckpt}"))
    else:
        from woosh.model.video_kontext import VideoKontext
        ldm = VideoKontext(LoadConfig(path=f"checkpoints/{ckpt}"))
    ldm = ldm.eval().to(device)
    say(f"모델을 올렸습니다: {time.time() - t_load:.1f} s")

    stage(2)
    torch.manual_seed(a.seed)
    frames = int(round(limit * LATENT_FPS)) + 1          # 5초 → 501칸, 8초 → 801칸 (모델이 배운 길이 그대로)
    noise = torch.randn(1, 128, frames).to(device)
    batch = {"audio": None, "description": [prompt]}
    with torch.inference_mode():
        if mode == "video":
            import woosh.utils.video as wvideo
            from woosh.utils.videoio import extract_video_frames
            # Sony 코드는 영상 조각(16장씩) 400개를 한 번에 읽어, 8초 영상에서 GPU 메모리를 약 2.5 GB 더 쓴다.
            # SEGMENT_BATCH 개씩 나눠 읽어도 조각마다 따로 계산하므로 결과는 같다 (마지막 자리 반올림만 다를 수 있다).
            whole = wvideo.encode_video_with_sync
            wvideo.encode_video_with_sync = lambda model, x, batch_size=-1, pad=True: whole(model, x, batch_size=SEGMENT_BATCH, pad=pad)
            prompt = prompt.lower().strip()
            prompt = re.sub(r",([^ ])", r", \1", prompt).strip(",")
            batch["description"] = [prompt]
            vf, rate, _ = extract_video_frames(paths["video"], start_time=a.video_start, end_time=a.video_start + clip)
            if len(vf) == 0:
                fail(f"영상에서 장면을 읽지 못했습니다 ({os.path.basename(a.video)}, 시작 {a.video_start:g}초)", 2)
            say(f"영상 {len(vf)}장을 읽었습니다 ({rate} fps, {clip:.2f}초)")
            reader = wvideo.SynchformerProcessor(frame_rate=24).eval().to(device)
            batch["synch_out"] = reader(vf.to(device), rate)["synch_out"]
            del reader, vf
            if device == "cuda":
                torch.cuda.empty_cache()                # 영상 특징 모델이 쓴 메모리를 소리 만들기 전에 돌려준다
        cond = ldm.get_cond(batch, no_dropout=True, device=device)
        cond_neg = None
        if negative and a.model == "full":
            cond_neg = ldm.get_cond({**batch, "description": [negative]}, no_dropout=True, device=device)
        elif negative:
            say("알림: 피할 소리는 본판에서만 듣습니다. 빠른 판은 가이던스가 모델 안에 들어 있어 무시합니다.")

        stage(3)
        t_gen = time.time()
        calls = {"n": 0}
        inner = ldm._denoise_dict_no_param

        def counted(*args, **kw):
            calls["n"] += 1
            if a.model == "fast":
                say(f"소리 만들기 {calls['n']}/{a.steps}")
            elif calls["n"] % 20 == 0:
                say(f"계산 {calls['n'] // 2}번")
            return inner(*args, **kw)

        ldm._denoise_dict_no_param = counted
        if a.model == "fast":
            from woosh.inference.flowmap_sampler import sample_euler
            x = sample_euler(model=ldm, noise=noise, cond=cond, num_steps=a.steps,
                             renoise=renoise_list(a.renoise, a.steps), cfg=cfg)
        else:
            from woosh.inference.flowmatching_sampler import flowmatching_integrate
            x = flowmatching_integrate(ldm, noise=noise, cond=cond, cond_neg=cond_neg, cfg=cfg, atol=a.tolerance,
                                       rtol=a.tolerance, method=a.solver, device=device,
                                       dtype=torch.float32 if device == "mps" else torch.float64)
            say(f"계산 {calls['n'] // 2}번으로 풀었습니다")
        audio = ldm.autoencoder.inverse(x).float().cpu().numpy()[0, 0]
    gen_s = time.time() - t_gen
    say(f"소리를 만들었습니다: {gen_s:.1f} s")

    stage(4)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak                    # 넘친 소리는 Sony 의 시험 코드처럼 가장 큰 곳을 1 로 맞춘다
    audio = audio[: int(round(seconds * SR))].astype(np.float32)
    notes = []
    if a.trim_silence:
        before = len(audio)
        audio = trim_silence(audio, a.trim_silence)
        notes.append(f"앞뒤 무음 {(before - len(audio)) / SR:.2f}초 자름")
    if a.loop:
        audio = make_loop(audio, a.loop_crossfade)
        notes.append(f"끝과 처음을 {a.loop_crossfade:g}초 겹쳐 되풀이 소리로 만듦")
    else:
        for ms, rising in ((a.fade_in, True), (a.fade_out, False)):
            n = min(len(audio), int(SR * ms / 1000))
            if n > 1:
                ramp = np.linspace(0, 1, n, dtype=np.float32)
                if rising:
                    audio[:n] *= ramp
                else:
                    audio[-n:] *= ramp[::-1]
    if a.peak is not None:
        top = float(np.abs(audio).max()) if audio.size else 0.0
        if top > 1e-6:
            audio = audio * (10 ** (a.peak / 20) / top)
            notes.append(f"가장 큰 곳을 {a.peak:g} dBFS 로 맞춤")
    audio = np.clip(audio, -1.0, 1.0)
    sf.write(paths["output"], audio, SR, subtype="PCM_16")

    ff = ffmpeg_path()
    if (paths["mp3"] or paths["video_out"] or paths["thumb"]) and not ff:
        say("주의: ffmpeg 가 없어 mp3·영상 사본을 만들지 못했습니다 (media 환경을 확인하세요).")
    elif ff:
        if paths["mp3"]:
            run_ffmpeg(ff, ["-i", paths["output"], "-c:a", "libmp3lame", "-b:a", "192k", paths["mp3"]], "mp3 만들기")
        if mode == "video" and paths["video_out"]:
            run_ffmpeg(ff, ["-ss", f"{a.video_start:.3f}", "-t", f"{len(audio) / SR:.3f}", "-i", paths["video"],
                            "-i", paths["output"], "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
                            "-movflags", "+faststart", paths["video_out"]], "영상에 소리 붙이기")
        if mode == "video" and paths["thumb"]:
            src = paths["video_out"] if paths["video_out"] and os.path.isfile(paths["video_out"]) else paths["video"]
            run_ffmpeg(ff, ["-ss", "0.3", "-i", src, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", paths["thumb"]],
                       "미리보기 만들기")

    vram = torch.cuda.max_memory_allocated() / 2**30 if device == "cuda" else 0.0
    held = torch.cuda.max_memory_reserved() / 2**30 if device == "cuda" else 0.0
    info = {"model": ckpt, "seconds": round(len(audio) / SR, 3), "sample_rate": SR, "seed": a.seed, "cfg": cfg,
            "generate_s": round(gen_s, 2), "total_s": round(time.time() - t0, 1), "torch_peak_gib": round(vram, 2),
            "torch_reserved_gib": round(held, 2)}
    print("woosh_info=" + json.dumps(info, ensure_ascii=False), flush=True)
    say(f"끝: {len(audio) / SR:.2f}초, 48 kHz 모노{', ' + ', '.join(notes) if notes else ''}, "
        f"{time.time() - t0:.1f} s, GPU 메모리 최대 {vram:.1f} GiB (torch 기준)")


if __name__ == "__main__":
    main()
