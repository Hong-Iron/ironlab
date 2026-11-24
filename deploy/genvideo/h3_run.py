#!/usr/bin/env python3
"""MiniMax H3 로 소리가 든 영상을 만든다. ironlab 의 '영상 생성' 화면과 '영상 만들기' 노드가 h3.sh 를 거쳐 부른다.

하는 일은 네 가지다.

1. 글 세 칸(장면, 주변 소리, 배경 음악)을 MiniMax 가 정한 프롬프트 모양으로 묶는다.
   첫 장면·끝 장면 사진이 있으면 안내서(docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)가 정한 첫 줄을 붙인다.
2. stable-diffusion.cpp 의 sd-cli 로 모델을 돌려 AVI(영상 + 32 kHz 스테레오 소리)를 쓴다.
3. ffmpeg 로 그것을 브라우저에서 바로 트는 MP4(H.264 + AAC)로 바꾼다.
4. 목록에 넣을 작은 미리보기 사진과, 다음 영상의 첫 장면으로 쓸 마지막 장면 사진을 뽑는다.

모델 파일은 ~/models/minimax-h3 에 있다 (H3_MODELS 로 바꿀 수 있다). 24 GB GPU 에 맞추려고 줄인 GGUF 를 쓰고,
쓰지 않는 무게는 CPU 메모리에 두었다가 차례가 오면 GPU 로 올린다 (--offload-to-cpu).

영상을 만드는 본체는 두 가지 가운데 고른다 (--model).
- flash: HyperFlow H3 Flash. H3 를 14B 로 줄이고 6단계로 만들도록 다시 배운 판 (Video Rebirth, 6비트 GGUF). 기본값.
- base:  MiniMax H3 본체 33B (4비트 GGUF), 20단계.
2026-11-14 이 서버(RTX 3090)에서 잰 값: 본체로 864×480 2.3초 영상 20단계에 131초 (한 단계 약 5.5초),
1344×768 5초 영상은 한 단계 약 54초.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.dirname(HERE)
MODELS = os.environ.get("H3_MODELS", os.path.join(HOME_DIR, "models", "minimax-h3"))
SD_CLI = os.environ.get("H3_SD_CLI", os.path.join(HERE, "bin", "sd-cli"))
MEDIA_BIN = os.environ.get("H3_MEDIA_BIN", os.path.join(HOME_DIR, ".conda", "envs", "media", "bin"))
FPS = 24
# sd-cli 가 무게와 계산 버퍼에 쓸 GPU 메모리 상한 (GiB). 주지 않으면 남은 GPU 메모리를 다 쓴다
# (2026-11-14 본체 1344×768 시험에서 24,006 MiB 까지 찼다). ironlab 은 이 작업에 22 GB 를 예약한다.
MAX_VRAM = os.environ.get("H3_MAX_VRAM", "19")
MAX_SECONDS = 15.0
DEFAULT_SIZE_LANDSCAPE = (1344, 768)

DIFFUSION = {
    "flash": "hyperflow/fl2va_gguf/minimax_h3_fl2va_hyperflow_flash_14b-Q6_K.gguf",
    "base": "minimax_h3_fl2va-Q4_K_M.gguf",
}
# 참고 자료(그림·영상·소리)를 받는 판 (Ref2VA). 글 인코더와 VAE 는 FL2VA 와 같은 파일을 쓴다
# (글 인코더에 그림을 읽는 부분이, 소리 VAE 에 소리를 읽는 부분이 들어 있다).
DIFFUSION_REF = {
    "flash": "hyperflow/ref2va_gguf/minimax_h3_ref2va_hyperflow_flash_14b-Q6_K.gguf",
    "base": "minimax_h3_ref2va_pruned-Q4_K_M.gguf",
}
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".gif")
MAX_REF = {"image": 4, "video": 2, "audio": 2}
# 참고 영상은 만드는 단계마다 함께 계산돼, 길고 클수록 GPU 메모리를 많이 쓴다. 그래서 짧게 자르고 작게 줄여 넣는다.
# 2026-11-15: 1344×768 5.17초를 만들며 참고 그림 하나와 1344×768 참고 영상 5초를 넣으니 22.5 GB 가 필요해 멈췄다 (24 GB GPU).
REF_VIDEO_SECONDS = {"motion": 3.0, "continue": 2.0}   # 움직임 참고는 앞 3초, 이어 만들기는 끝 2초
REF_VIDEO_SHORT = 480       # 참고 영상의 짧은 변 (픽셀). 만들 영상보다 작아도 된다 (864×480 참고로 1376×768 을 이어 만들어 보았다)
REF_AUDIO_SECONDS = 20.0
# 참고 영상·소리의 쓰임새 → (정의 문장, 지키는 정도, 작업 종류). MiniMax 의 Ref2VA 프롬프트 안내서
# (docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md) 의 subject_definitions / retention_analysis / summary 형식을 따른다
VIDEO_ROLES = {
    "motion": ("{v} is a reference video; the target video follows its subject motion, camera movement and pacing.",
               "partially_preserved - its subject motion, camera movement and pacing are followed in a new shot.",
               "reference generation"),
    "continue": ("{v} is the source video; the target video continues directly from the last frame of {v}.",
                 "fully_preserved - the target video starts exactly where {v} ends, keeping its subjects, scene, lighting and motion.",
                 "video continuation"),
}
AUDIO_ROLES = {
    "voice": ("{a} is the voice-timbre reference for the main speaker (S1).",
              "reference - the speaker's voice follows the timbre and delivery of {a} without copying the original signal.",
              "audio reference"),
    "music": ("{a} is the background-music style reference.",
              "reference - the score follows the instrumentation, tempo and mood of {a} without copying it.",
              "audio reference"),
    "copy": ("{a} is reused as the complete audio track of the target video.",
             "fully_copy - {a} is reused 1:1 as the target video's complete final audio track.",
             "audio reuse"),
}
FLASH_STEPS = (4, 6, 8)     # HyperFlow 는 이 단계 수로 배웠고, sd-cli 에 같은 이름의 시간표가 들어 있다 (hyperflow-h3-flash-6)
FILES = {
    "llm": "qwen3vl_32b_minimax_h3-Q4_K_M.gguf",
    "vae": "vae/minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "vae/minimax_h3_audio_vae_fp32.safetensors",
}

# sd-cli 가 찍는 글자 → 화면에 보일 단계. 아래로 갈수록 뒤 단계다
STAGES = [
    (1, "모델 올리기"),
    (2, "글과 사진 읽기"),
    (3, "영상 만들기"),
    (4, "영상과 소리 풀어내기"),
    (5, "MP4 만들기"),
]


def say(msg: str) -> None:
    print(f"[h3] {msg}", flush=True)


def stage(n: int) -> None:
    say(f"단계 {n}/{len(STAGES)}: {STAGES[n - 1][1]}")


def fail(msg: str, code: int = 1) -> None:
    say(f"실패: {msg}")
    sys.exit(code)


def read_text(path: str | None) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def frames_for(seconds: float) -> int:
    """영상 길이(초)를 H3 가 받는 프레임 수(17k + 5)로 바꾼다. 가장 가까운 칸을 고르되 15초를 넘지 않는다."""
    want = seconds * FPS
    k = max(0, round((want - 5) / 17))
    while 17 * k + 5 > MAX_SECONDS * FPS + 2:
        k -= 1
    return 17 * k + 5


def image_size(path: str) -> tuple[int, int] | None:
    """ffprobe 로 사진의 가로·세로 픽셀을 읽는다. 못 읽으면 None."""
    probe = os.path.join(MEDIA_BIN, "ffprobe")
    try:
        out = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0:s=x", path], capture_output=True, text=True, timeout=30).stdout.strip()
        w, h = (int(v) for v in out.split("x")[:2])
        return w, h
    except Exception:
        return None


def snap32(v: float) -> int:
    return max(256, int(round(v / 32)) * 32)


def pick_size(size: str, images: list[str]) -> tuple[int, int]:
    """'auto' 는 첫 장면 사진의 가로세로 비율에 짧은 변 768 픽셀. 사진이 없으면 가로 16:9 (1344×768)."""
    if size and size != "auto":
        m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", size)
        if not m:
            fail(f"--size 는 1344x768 처럼 가로x세로여야 합니다 (받은 값 {size!r})", 2)
        return snap32(int(m.group(1))), snap32(int(m.group(2)))
    if images:
        wh = image_size(images[0])
        if wh:
            w, h = wh
            k = 768 / min(w, h)
            return snap32(w * k), snap32(h * k)
    return DEFAULT_SIZE_LANDSCAPE


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in (".wav", ".mp3", ".flac", ".ogg", ".m4a"):
        return "audio"
    return "image"


def build_ref_prompt(scene: str, sound: str, music: str, n: dict, video_role: str, audio_role: str) -> str:
    """참고 자료가 있을 때의 프롬프트. Ref2VA 는 여섯 칸(참고 자료 정의, 요약, 지키는 정도, 장면, 주변 소리, 배경 음악)으로 쓴다.

    사용자는 장면 글에서 <Picture 1>, <Video 1>, <Audio 1> 처럼 참고 자료를 부른다. 나머지 칸은 쓰임새에 맞춰 채운다.
    이미 그 형식(subject_definitions: 로 시작)으로 쓴 글은 그대로 둔다.
    """
    if "subject_definitions:" in scene:
        return scene
    defs, keep, tasks = [], [], []
    for i in range(1, n["image"] + 1):
        defs.append(f"<Subject {i}> is the main subject shown in <Picture {i}>; keep its appearance, colors and identity.")
        keep.append(f"<Subject {i}> (appears in [Shot 1]): fully_preserved - its appearance, colors and identity are retained.")
    if n["image"]:
        tasks.append("reference generation")
    for i in range(1, n["video"] + 1):
        line, kept, task = VIDEO_ROLES[video_role]
        v = f"<Video {i}>"
        defs.append(line.format(v=v))
        keep.append(f"{v}: " + kept.format(v=v))
        tasks.append(task)
    for i in range(1, n["audio"] + 1):
        line, kept, task = AUDIO_ROLES[audio_role]
        a = f"<Audio {i}>"
        defs.append(line.format(a=a))
        keep.append(f"{a}: " + kept.format(a=a))
        tasks.append(task)
    tasks = list(dict.fromkeys(tasks))                  # 같은 종류는 한 번만
    desc = scene if scene.lstrip().startswith("[Shot") else f"[Shot 1] {scene}"
    summary = " ".join(scene.split())
    if len(summary) > 400:
        summary = summary[:400].rsplit(" ", 1)[0] + " ..."
    if not music and n["audio"] and audio_role == "music":
        music = "The score follows the style, tempo and instrumentation of " + ", ".join(f"<Audio {i}>" for i in range(1, n["audio"] + 1)) + "."
    return (f"subject_definitions:\n" + "\n".join(defs) + "\n\n"
            f"summary:\n[{' + '.join(tasks)}] {summary}\n\n"
            f"retention_analysis:\n" + "\n".join(keep) + "\n\n"
            f"detailed_description:\n{desc}\n\n"
            f"overall_soundscape:\n{sound or 'Natural ambient sound that matches the scene.'}\n\n"
            f"non_diegetic_music:\n{music or 'N/A'}")


def prepare_refs(refs: list[str], work: str, width: int, height: int, video_role: str) -> dict:
    """참고 자료를 sd-cli 가 받는 모양으로 바꾼다. 그림은 그대로, 영상은 1초에 24장짜리 장면 폴더(와 WAV 소리),
    소리는 32 kHz 스테레오 WAV. 반환: {"image": [...], "video": [...], "video_audio": [...], "audio": [...]}"""
    ffmpeg = os.path.join(MEDIA_BIN, "ffmpeg")
    q = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    out = {"image": [], "video": [], "video_audio": [], "audio": []}
    sounds = []
    for path in refs:
        k = kind_of(path)
        if k == "image":
            out["image"].append(path)
            continue
        if not os.path.isfile(ffmpeg):
            fail(f"참고 {('영상' if k == 'video' else '소리')}을 바꾸려면 ffmpeg 가 필요합니다 ({ffmpeg})")
        n = len(out[k]) + 1
        if k == "audio":
            wav = os.path.join(work, f"ref-audio-{n}.wav")
            r = subprocess.run([*q, "-t", f"{REF_AUDIO_SECONDS:g}", "-i", path, "-vn", "-ac", "2", "-ar", "32000",
                                "-c:a", "pcm_s16le", wav], stdin=subprocess.DEVNULL)
            if r.returncode != 0 or not os.path.isfile(wav):
                fail(f"참고 소리를 읽지 못했습니다: {path}", 2)
            out["audio"].append(wav)
            continue
        frames = os.path.join(work, f"ref-video-{n}")
        os.makedirs(frames, exist_ok=True)
        # 만들 영상 크기 안에 들어가게 줄인다 (더 크게 넣어도 모델이 줄인다. 미리 줄이면 빠르다)
        wh = image_size(path) or (width, height)
        k = min(1.0, REF_VIDEO_SHORT / min(wh))
        scale = f"scale={int(wh[0] * k) // 2 * 2}:{int(wh[1] * k) // 2 * 2}"
        secs = REF_VIDEO_SECONDS[video_role]
        # 이어 만들 때는 영상의 끝이 중요하다: 끝에서 secs 만큼을 쓴다. 움직임 참고는 앞에서 secs 만큼
        pre = ["-sseof", f"-{secs:g}"] if video_role == "continue" else []
        say(f"참고 영상 {n}: {'끝' if pre else '앞'} {secs:g}초를 {int(wh[0] * k) // 2 * 2}×{int(wh[1] * k) // 2 * 2} 로 줄여 넣습니다")
        r = subprocess.run([*q, *pre, "-i", path, "-t", f"{secs:g}", "-vf", f"fps={FPS},{scale}",
                            os.path.join(frames, "%05d.png")], stdin=subprocess.DEVNULL)
        got = len([f for f in os.listdir(frames) if f.endswith(".png")])
        if r.returncode != 0 or got == 0:
            fail(f"참고 영상에서 장면을 뽑지 못했습니다: {path}", 2)
        out["video"].append(frames)
        wav = os.path.join(work, f"ref-video-{n}.wav")
        has_audio = False
        if video_role == "continue":                     # 움직임만 참고할 때는 그 영상의 소리를 넣지 않는다
            r = subprocess.run([*q, *pre, "-i", path, "-t", f"{secs:g}", "-vn", "-ac", "2", "-ar", "32000",
                                "-c:a", "pcm_s16le", wav], stdin=subprocess.DEVNULL, capture_output=True)
            has_audio = r.returncode == 0 and os.path.isfile(wav) and os.path.getsize(wav) > 1024
        sounds.append((wav if has_audio else None, got / FPS))
    # 영상의 소리는 영상과 차례로 짝지어진다. 소리가 없는 영상 뒤에 소리가 있는 영상이 오면, 앞 영상에 조용한 소리를 채운다
    last = max((i for i, (w, _) in enumerate(sounds) if w), default=-1)
    for i, (wav, secs) in enumerate(sounds[:last + 1]):
        if wav is None:
            wav = os.path.join(work, f"ref-video-{i + 1}-silent.wav")
            subprocess.run([*q, "-f", "lavfi", "-t", f"{secs:.3f}", "-i", "anullsrc=r=32000:cl=stereo", "-c:a", "pcm_s16le", wav],
                           stdin=subprocess.DEVNULL)
        out["video_audio"].append(wav)
    return out


def build_prompt(scene: str, sound: str, music: str, n_images: int, seconds: float) -> str:
    """장면·소리·음악 세 칸을 H3 의 프롬프트 모양으로 묶는다. 이미 그 모양으로 쓴 글은 그대로 둔다."""
    if "integrated_multimodal_description:" in scene:
        body = scene
    else:
        desc = scene if scene.lstrip().startswith("[Shot") else f"[Shot 1] {scene}"
        body = (f"integrated_multimodal_description: {desc}\n"
                f"overall_soundscape: {sound or 'Natural ambient sound that matches the scene.'}\n"
                f"non_diegetic_music: {music or 'N/A'}")
    if n_images == 1 and "<Picture 1>" not in body.split("\n", 1)[0]:
        head = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        body = f"{head}\n\n{body}"
    elif n_images == 2 and "Picture 2" not in body.split("\n", 1)[0]:
        shots = max(1, len(re.findall(r"\[Shot \d+\]", body)))
        head = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                f"0.00-second mark of the target video; Picture 2 (from Shot {shots}) aligns with the "
                f"{seconds:.2f}-second mark of the target video.")
        body = f"{head}\n\n{body}"
    return body


LAST_LINES: list[str] = []      # sd-cli 출력의 마지막 줄들 (멈춘 까닭을 읽는다)
OOM_RE = re.compile(r"cannot make enough memory available.*?need ([\d.]+) MB device.*?available ([\d.]+) MB device|out of memory", re.S)


def run_sd(cmd: list[str]) -> int:
    """sd-cli 를 돌리며 출력을 그대로 넘기고, 단계가 바뀌는 줄이 나오면 단계 줄을 끼워 넣는다.

    sd-cli 의 순서 (2026-11-14 판 5dcfa79 에서 본 것): 모델 파일 읽기 → "running in MiniMax H3 AV FLOW mode" →
    글 읽기(qwen3vl) → "get_learned_condition completed" → 단계 막대 → "sampling completed" → 소리·영상 풀기 → "save result video".
    """
    marks = [
        (re.compile(r"running in MiniMax H3|qwen3vl build|get_sigmas"), 2),
        (re.compile(r"get_learned_condition completed|generate_video \d+x\d+"), 3),
        (re.compile(r"sampling completed|generating latent video completed"), 4),
    ]
    at = 1
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    buf = b""
    while True:
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
        buf = (buf + chunk)[-8192:]
        LAST_LINES[:] = (LAST_LINES + chunk.decode("utf-8", "replace").splitlines())[-60:]
        text = buf.decode("utf-8", "replace")
        for rx, n in reversed(marks):               # 가장 뒤 단계부터 본다. 한 덩어리에 두 단계가 함께 오면 뒤 단계로 간다
            if n > at and rx.search(text):
                at = n
                print("", flush=True)
                stage(n)
                buf = b""
                break
    return proc.wait()


def main() -> None:
    ap = argparse.ArgumentParser(description="MiniMax H3: 글(과 첫·끝 장면 사진)로 소리가 든 영상을 만든다")
    ap.add_argument("--prompt-file", required=True, help="장면 설명 (화면, 움직임, 대사). 영어를 권함")
    ap.add_argument("--sound-file", help="주변 소리 설명 (overall_soundscape)")
    ap.add_argument("--music-file", help="배경 음악 설명 (non_diegetic_music). 없으면 배경 음악 없음")
    ap.add_argument("--image", action="append", default=[], help="첫 장면 사진. 두 번 주면 두 번째가 끝 장면")
    ap.add_argument("--ref-image", action="append", default=[], help="참고 그림 (장면 글에서 <Picture 1> 처럼 부른다, 4장까지)")
    ap.add_argument("--ref-video", action="append", default=[], help="참고 영상 mp4 등 (<Video 1>, 2개까지, 앞 5초만 쓴다)")
    ap.add_argument("--ref-audio", action="append", default=[], help="참고 소리 wav·mp3 등 (<Audio 1>, 2개까지, 앞 20초만 쓴다)")
    ap.add_argument("--ref-video-role", choices=tuple(VIDEO_ROLES), default="motion",
                    help="참고 영상의 쓰임새: motion 움직임·카메라를 따른다, continue 그 영상의 끝에서 이어 만든다")
    ap.add_argument("--ref-audio-role", choices=tuple(AUDIO_ROLES), default="voice",
                    help="참고 소리의 쓰임새: voice 말하는 사람의 목소리, music 배경 음악 스타일, copy 그 소리를 그대로 쓴다")
    ap.add_argument("--output", required=True, help="결과 MP4 경로")
    ap.add_argument("--thumb", help="목록용 작은 미리보기 JPG 경로")
    ap.add_argument("--last-frame", help="마지막 장면 PNG 경로 (다음 영상의 첫 장면으로 쓴다)")
    ap.add_argument("--audio", help="소리만 따로 쓴 WAV 경로")
    ap.add_argument("--size", default="auto", help="가로x세로 픽셀 (32 의 배수로 맞춤). auto 는 첫 장면 사진의 비율")
    ap.add_argument("--seconds", type=float, default=5.0, help="영상 길이 (초, 2~15)")
    ap.add_argument("--model", choices=tuple(DIFFUSION), default="flash", help="flash: HyperFlow 14B 6단계, base: H3 본체 33B")
    ap.add_argument("--steps", type=int, help="잡음을 걷어 내는 횟수 (flash 는 4·6·8, 기본 6. base 는 기본 20)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--shift", type=float, help="흐름 이동 값 (기본은 모델 값 12)")
    ap.add_argument("--sampler", help="샘플러 (기본 euler)")
    ap.add_argument("--scheduler", help="잡음 시간표 이름")
    ap.add_argument("--sigmas", help="잡음 시간표를 쉼표로 이은 수로 직접 준다")
    ap.add_argument("--attn-budget", type=float, help="주의 계산을 몇 GiB 씩 나눠 할지 (기본 2, 줄이면 메모리를 덜 씀)")
    ap.add_argument("--vae-tiling", action="store_true", help="영상을 풀 때 공간을 조각으로 나눈다 (메모리 절약)")
    ap.add_argument("--temporal-tiling", action="store_true", help="영상을 풀 때 시간을 조각으로 나눈다 (메모리 절약)")
    ap.add_argument("--no-offload", action="store_true", help="무게를 모두 GPU 에 올린다 (24 GB 에서는 모자람)")
    ap.add_argument("--max-vram", default=MAX_VRAM, help="sd-cli 가 무게와 버퍼에 쓸 GPU 메모리 상한 (GiB, 0 이면 남은 만큼 다)")
    ap.add_argument("--crf", type=int, default=18, help="MP4 화질 (낮을수록 좋고 파일이 큼, 0~51)")
    ap.add_argument("--keep", action="store_true", help="중간 파일(AVI, 쓴 프롬프트)을 지우지 않는다")
    a = ap.parse_args()

    t0 = time.time()
    if not os.path.isfile(SD_CLI):
        fail(f"sd-cli 가 없습니다 ({SD_CLI}). setup_h3.sh 로 빌드하세요.")
    steps = a.steps if a.steps is not None else (6 if a.model == "flash" else 20)
    if a.model == "flash" and not a.sigmas and steps not in FLASH_STEPS:
        fail(f"Flash 모델의 단계 수는 4, 6, 8 가운데 하나여야 합니다 (받은 값 {steps}). 다른 수를 쓰려면 시간표를 직접 주세요.", 2)
    if steps < 1:
        fail(f"단계 수는 1 이상이어야 합니다 (받은 값 {steps})", 2)
    for key, name in {**FILES, "diffusion": DIFFUSION[a.model]}.items():
        if not os.path.isfile(os.path.join(MODELS, name)):
            fail(f"모델 파일이 없습니다: {os.path.join(MODELS, name)}. setup_h3.sh 의 내려받기를 먼저 하세요.")
    if len(a.image) > 2:
        fail(f"--image 는 두 장까지입니다 (첫 장면, 끝 장면). 받은 수 {len(a.image)}장", 2)
    for p in a.image:
        if not os.path.isfile(p):
            fail(f"사진이 없습니다: {p}", 2)
    refs = a.ref_image + a.ref_video + a.ref_audio
    for p in refs:
        if not os.path.isfile(p):
            fail(f"참고 자료가 없습니다: {p}", 2)
    counts = {"image": len(a.ref_image), "video": len(a.ref_video), "audio": len(a.ref_audio)}
    for k, n in counts.items():
        if n > MAX_REF[k]:
            name = {"image": "그림", "video": "영상", "audio": "소리"}[k]
            fail(f"참고 {name}은 {MAX_REF[k]}개까지입니다 (받은 수 {n}개)", 2)
    if refs and a.image:
        fail("첫·끝 장면 사진과 참고 자료는 함께 쓸 수 없습니다 (모델이 둘을 함께 받지 않습니다). 첫 장면을 참고 그림으로 넣고 "
             "장면 글에 'the shot begins from <Picture 1>' 처럼 쓰세요.", 2)
    if refs and not os.path.isfile(os.path.join(MODELS, DIFFUSION_REF[a.model])):
        fail(f"참고 자료를 받는 모델 파일(Ref2VA)이 없습니다: {os.path.join(MODELS, DIFFUSION_REF[a.model])}"
             + (" Flash 모델로 만드세요." if a.model == "base" else ""))
    if not 2 <= a.seconds <= MAX_SECONDS:
        fail(f"--seconds 는 2~15 여야 합니다 (받은 값 {a.seconds:g})", 2)

    scene = read_text(a.prompt_file)
    if not scene:
        fail("장면 설명이 비어 있습니다", 2)
    frames = frames_for(a.seconds)
    seconds = frames / FPS
    width, height = pick_size(a.size, a.image or a.ref_image)   # 'auto' 는 첫 장면·참고 그림의 비율에 짧은 변 768
    if a.size == "auto" and a.ref_video and (a.ref_video_role == "continue" or not (a.image or a.ref_image)):
        wh = image_size(a.ref_video[0])                 # 참고 영상만 있으면 그 영상의 크기를 따른다 (이어 만들 때 같은 크기여야 한다)
        if wh:
            k = min(1.0, 768 / min(wh))
            width, height = snap32(wh[0] * k), snap32(wh[1] * k)

    out = os.path.abspath(a.output)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    work = out + ".parts"
    os.makedirs(work, exist_ok=True)
    if refs:
        ref = prepare_refs(a.ref_image + a.ref_video + a.ref_audio, work, width, height, a.ref_video_role)
        prompt = build_ref_prompt(scene, read_text(a.sound_file), read_text(a.music_file), counts,
                                  a.ref_video_role, a.ref_audio_role)
    else:
        prompt = build_prompt(scene, read_text(a.sound_file), read_text(a.music_file), len(a.image), seconds)
    prompt_path = os.path.join(work, "prompt.txt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    raw = os.path.join(work, "raw.avi")

    mode = {0: "글 → 영상", 1: "첫 장면 → 영상", 2: "첫·끝 장면 → 영상"}[len(a.image)]
    if refs:
        parts = [f"{t} {counts[k]}" for k, t in (("image", "그림"), ("video", "영상"), ("audio", "소리")) if counts[k]]
        mode = f"참고 자료({', '.join(parts)}) → 영상"
    name = {"flash": "HyperFlow Flash", "base": "H3 본체"}[a.model]
    say(f"시작: {mode}, {name}, {width}×{height}, {frames}프레임 ({seconds:.2f}초), {steps}단계, 시드 {a.seed}")
    stage(1)
    cmd = [SD_CLI, "-M", "vid_gen",
           "--diffusion-model", os.path.join(MODELS, (DIFFUSION_REF if refs else DIFFUSION)[a.model]),
           "--llm", os.path.join(MODELS, FILES["llm"]),
           "--vae", os.path.join(MODELS, FILES["vae"]),
           "--audio-vae", os.path.join(MODELS, FILES["audio_vae"]),
           "--prompt-file", prompt_path,
           "--cfg-scale", "1.0",          # H3 는 가이던스를 미리 녹여 넣은 모델이다. 1 보다 크면 sd-cli 가 멈춘다
           "-W", str(width), "-H", str(height),
           "--fps", str(FPS), "--video-frames", str(frames),
           "--steps", str(steps), "-s", str(a.seed),
           "--rng", "cpu",
           "--compression-quality", "95",
           "-o", raw]
    if not a.no_offload:
        cmd.append("--offload-to-cpu")
    if a.max_vram and a.max_vram != "0":
        cmd += ["--max-vram", str(a.max_vram)]
    if refs:
        for path in ref["image"]:
            cmd += ["--ref-image", path]
        for path in ref["video"]:
            cmd += ["--ref-video", path]
        for path in ref["video_audio"]:
            cmd += ["--ref-video-audio", path]
        for path in ref["audio"]:
            cmd += ["--ref-audio", path]
    if a.image:
        cmd += ["-i", a.image[0]]
    if len(a.image) == 2:
        cmd += ["--end-img", a.image[1]]
    if a.shift is not None:
        cmd += ["--flow-shift", f"{a.shift:g}"]
    if a.sampler:
        cmd += ["--sampling-method", a.sampler]
    elif a.model == "flash":
        cmd += ["--sampling-method", "euler"]
    if a.scheduler:
        cmd += ["--scheduler", a.scheduler]
    if a.sigmas:
        cmd += ["--sigmas", a.sigmas]
    elif a.model == "flash":
        cmd += ["--sigmas", f"hyperflow-h3-flash-{steps}"]  # 흐름 이동 12 가 들어 있는 시간표다
    if a.attn_budget is not None:
        cmd += ["--model-args", f"minimax_h3_attn_chunk_budget={a.attn_budget:g}"]
    if a.vae_tiling:
        cmd.append("--vae-tiling")
    if a.temporal_tiling:
        cmd.append("--temporal-tiling")

    code = run_sd(cmd)
    if code != 0 or not os.path.isfile(raw):
        m = OOM_RE.search("\n".join(LAST_LINES))
        if m:
            need = f"{float(m.group(1)) / 1024:.1f} GB 가 필요했는데 {float(m.group(2)) / 1024:.1f} GB 만 비어 있었습니다. " if m.group(1) else ""
            fail(f"GPU 메모리가 모자라 멈췄습니다 ({width}×{height}, {seconds:.2f}초"
                 + (f", 참고 자료 {len(refs)}개" if refs else "") + f"). {need}"
                 "크기를 864×480 으로 줄이거나, 길이를 줄이거나, 참고 자료(특히 참고 영상)를 줄여 다시 만드세요. "
                 "상세 설정의 '주의 계산 묶음'을 0.5 로 줄이면 조금 더 들어갈 수 있습니다.")
        fail(f"sd-cli 가 끝 코드 {code} 로 멈췄습니다. 위 로그의 마지막 오류 줄을 보세요.")

    print("", flush=True)
    stage(5)
    ffmpeg = os.path.join(MEDIA_BIN, "ffmpeg")
    if not os.path.isfile(ffmpeg):
        shutil.move(raw, os.path.splitext(out)[0] + ".avi")
        fail(f"ffmpeg 가 없어 MP4 로 바꾸지 못했습니다 ({ffmpeg}). AVI 는 남겼습니다.")
    q = ["-hide_banner", "-loglevel", "error", "-y"]
    r = subprocess.run([ffmpeg, *q, "-i", raw, "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf),
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    if r.returncode != 0 or not os.path.isfile(out):
        fail("ffmpeg 가 MP4 를 쓰지 못했습니다")
    if a.thumb:
        subprocess.run([ffmpeg, *q, "-i", out, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", a.thumb])
    if a.last_frame:
        # 끝 0.5초를 읽으며 장면마다 같은 파일에 덮어써, 진짜 마지막 장면을 남긴다.
        # (-frames:v 1 을 붙이면 끝에서 0.5초 앞의 장면 하나만 남아, 이어 만든 영상이 0.5초를 되감는 것처럼 보였다)
        subprocess.run([ffmpeg, *q, "-sseof", "-0.5", "-i", out, "-update", "1", a.last_frame])
        if not os.path.isfile(a.last_frame):          # 아주 짧은 영상에서 -sseof 가 빗나가면 처음부터 끝까지 읽는다
            subprocess.run([ffmpeg, *q, "-i", out, "-update", "1", a.last_frame])
    if a.audio:
        subprocess.run([ffmpeg, *q, "-i", raw, "-vn", "-c:a", "pcm_s16le", a.audio])
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    size_mb = os.path.getsize(out) / 1e6
    say(f"끝: {seconds:.2f}초 영상, {width}×{height}, {size_mb:.1f} MB, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
