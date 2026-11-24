#!/usr/bin/env python3
"""만든 소리를 다듬는다. ironlab 의 '소리 다듬기' 화면과 '소리 다듬기' 노드가 polish.sh 를 거쳐 부른다.

AI 가 부른 노래는 사람이 부른 노래보다 'ㅅ' 소리가 쇳소리처럼 같고, 특정 높이에서 울림이 튀고, 크기가 지나치게 고르다.
믹싱 엔지니어는 이것을 목소리 트랙 하나에 정해진 순서로 처리해 고친다. 그런데 음악 생성(YuE2)은 목소리와 반주를 한 파일로
섞어 내놓는다. 그래서 먼저 둘을 나누고(Demucs), 목소리에만 그 순서를 걸고, 반주와 다시 섞어 크기를 맞춘다.

무엇을 넣느냐(--source)에 따라 하는 일이 다르다.

- song:  노래(목소리 + 반주). 나누기 → 목소리 다듬기 → 다시 섞기 → 마무리.  영상을 넣으면 대사와 배경 소리로 나눈다.
- voice: 목소리·말소리만 (음성 생성 결과, 녹음). 나누지 않고 목소리 다듬기 → 마무리.
- sfx:   효과음. 낮은 웅웅거림만 걷고, 가볍게 누르고, 크기를 맞춘다.
- mix:   나누지 않고 곡 전체에만 가볍게 마무리한다 (나누기가 소리를 망칠 때, 이미 섞어 둔 곡).

처리 순서와 기본값은 AI 목소리 다듬기 안내(peak-studios.de 의 'KI-Vocals bearbeiten')에 나온 일반 믹싱 요령을 따랐다.
그 값은 YuE2 곡에 맞춰 잰 값이 아니다. 좋아졌는지는 들어 보고 정해야 한다. 그래서 결과 보고(--report)에 처리 전과 후를
같은 잣대로 잰 값과, 재서 정한 것(깎은 주파수, 줄인 양)을 모두 남긴다.

Demucs 는 Meta 가 MIT 라이선스로 공개한 음원 분리 모델이다. 가중치는 ~/models/demucs 에 있다 (DEMUCS_MODELS 로 바꿀 수 있다).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.dirname(HERE)
MODEL_ROOT = os.path.abspath(os.environ.get("DEMUCS_MODELS", os.path.join(HOME_DIR, "models", "demucs")))
MEDIA_BIN = os.environ.get("POLISH_MEDIA_BIN", os.path.join(HOME_DIR, ".conda", "envs", "media", "bin"))
os.environ["TORCH_HOME"] = MODEL_ROOT           # Demucs 가 가중치를 여기서 찾는다 (작업 안에서는 내려받지 않는다)
sys.path.insert(0, HERE)

import numpy as np                              # noqa: E402
import soundfile as sf                          # noqa: E402

import polish_dsp as dsp                        # noqa: E402

STAGES = ("소리 읽기", "목소리와 반주 나누기", "목소리 다듬기", "다시 섞기", "마무리와 저장")
SOURCES = ("song", "voice", "sfx", "mix")
SEPARATORS = ("htdemucs", "htdemucs_ft")
SEP_RATE = 44100                                # Demucs 가 배운 표본 수 (1초에)
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv")
DEESS_BANDS = {"auto": None, "bright": (6000.0, 8000.0), "dark": (5000.0, 7000.0)}   # 밝은 목소리, 어두운 목소리

# 무엇을 넣었느냐에 따른 기본값. 화면에서 값을 비워 두면 이 값을 쓴다
DEFAULTS = {
    #          낮은소리  울림깎기  치찰음  압축  나란히  따뜻함  광택  잔향   메아리  반주낮춤 반주비우기 다듬기전체 음량  천장
    "song":  dict(highpass=90, resonance=4.0, deess=6.0, comp=3.0, parallel=0.15, warmth=0.2, air=1.0, reverb=0.12,
                  delay=0.06, duck=1.5, carve=1.5, glue=1.5, loudness=-14.0, ceiling=-1.0),
    "voice": dict(highpass=80, resonance=3.0, deess=5.0, comp=3.0, parallel=0.1, warmth=0.15, air=1.0, reverb=0.0,
                  delay=0.0, duck=0.0, carve=0.0, glue=0.0, loudness=-16.0, ceiling=-1.0),
    "sfx":   dict(highpass=30, resonance=0.0, deess=0.0, comp=2.0, parallel=0.0, warmth=0.0, air=0.0, reverb=0.0,
                  delay=0.0, duck=0.0, carve=0.0, glue=0.0, loudness=-18.0, ceiling=-1.0),
    "mix":   dict(highpass=25, resonance=0.0, deess=0.0, comp=0.0, parallel=0.0, warmth=0.0, air=0.5, reverb=0.0,
                  delay=0.0, duck=0.0, carve=0.0, glue=1.5, loudness=-14.0, ceiling=-1.0),
}


def say(msg: str) -> None:
    print(f"[polish] {msg}", flush=True)


def stage(n: int, extra: str = "") -> None:
    say(f"단계 {n}/{len(STAGES)}: {STAGES[n - 1]}{extra}")


def fail(msg: str) -> None:
    say(f"실패: {msg}")
    sys.exit(1)


def ffmpeg_path() -> str | None:
    for p in (os.path.join(MEDIA_BIN, "ffmpeg"), shutil.which("ffmpeg")):
        if p and os.path.isfile(p):
            return p
    return None


def run_ffmpeg(ff: str, args: list[str], what: str) -> None:
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"{what}에 실패했습니다: {r.stderr.strip()[-300:]}")


def has_video(ff: str, path: str) -> bool:
    """영상 줄기가 있는지. ffprobe 대신 ffmpeg 의 안내 글을 읽는다 (media 환경에 ffprobe 가 없을 수 있다)."""
    r = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True)
    return any("Video:" in line and "attached pic" not in line for line in r.stderr.splitlines())


def read_audio(path: str, ff: str | None, tmp: str) -> tuple[np.ndarray, int]:
    """wav·flac·ogg 는 바로 읽고, 그 밖(mp3, m4a, 영상)은 ffmpeg 로 소리만 풀어 읽는다."""
    try:
        if not path.lower().endswith(VIDEO_EXTS):
            x, sr = sf.read(path, dtype="float32", always_2d=True)
            return x, sr
    except Exception:
        pass
    if not ff:
        fail(f"이 파일을 읽으려면 ffmpeg 가 필요합니다 ({os.path.basename(path)}). media 환경을 확인하세요.")
    wav = os.path.join(tmp, "input.wav")
    run_ffmpeg(ff, ["-i", path, "-vn", "-c:a", "pcm_f32le", wav], "소리 풀기")
    x, sr = sf.read(wav, dtype="float32", always_2d=True)
    return x, sr


def resample(x: np.ndarray, sr: int, to: int) -> np.ndarray:
    if sr == to:
        return x
    from math import gcd

    from scipy.signal import resample_poly
    g = gcd(sr, to)
    return resample_poly(x, to // g, sr // g, axis=0).astype(np.float32)


def separate(x: np.ndarray, sr: int, name: str, shifts: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """Demucs 로 목소리와 나머지(반주)를 나눈다. 모델은 44.1 kHz 스테레오로 배웠으므로 그렇게 바꿔 넣고 다시 되돌린다."""
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    t0 = time.time()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        say("주의: GPU 가 보이지 않아 CPU 로 나눕니다. 몇 분 걸릴 수 있습니다.")
    random.seed(0)                              # shifts 가 고르는 어긋남을 늘 같게 (같은 입력이면 같은 결과)
    torch.manual_seed(0)
    model = get_model(name)
    model.eval().to(device)
    say(f"나누기 모델 {name} 을 올렸습니다: {time.time() - t0:.1f} s")
    mono = x.shape[1] == 1
    stereo = np.repeat(x, 2, axis=1) if mono else x[:, :2]
    wav = torch.from_numpy(resample(stereo, sr, SEP_RATE).T.copy())
    ref = wav.mean(0)
    mean, std = float(ref.mean()), float(ref.std()) or 1.0
    with torch.no_grad():
        out = apply_model(model, ((wav - mean) / std)[None], device=device, shifts=shifts, split=True,
                          overlap=0.25, progress=True)[0]
    out = out * std + mean
    voc_i = model.sources.index("vocals")
    vocals = out[voc_i].cpu().numpy().T
    backing = (out.sum(0) - out[voc_i]).cpu().numpy().T
    vocals, backing = resample(vocals, SEP_RATE, sr), resample(backing, SEP_RATE, sr)
    n = len(x)
    vocals = np.pad(vocals, ((0, max(0, n - len(vocals))), (0, 0)))[:n]
    backing = np.pad(backing, ((0, max(0, n - len(backing))), (0, 0)))[:n]
    if mono:
        vocals, backing = vocals.mean(axis=1, keepdims=True), backing.mean(axis=1, keepdims=True)
    info = {"model": name, "shifts": shifts, "device": device, "seconds": round(time.time() - t0, 1)}
    if device == "cuda":
        info["torch_peak_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    del model
    return vocals.astype(np.float32), backing.astype(np.float32), info


def polish_vocal(v: np.ndarray, sr: int, o: dict, report: dict) -> np.ndarray:
    """목소리 하나에 거는 순서: 낮은 소리 자르기 → 울림 깎기 → 치찰음 → 압축 두 번 (+ 나란히) → 따뜻하게 → 광택."""
    level_in = dsp.active_rms_db(v, sr)
    v = dsp.highpass(v, o["highpass"], sr)
    cuts = dsp.find_resonances(v, sr, max_cut_db=o["resonance"]) if o["resonance"] > 0 else []
    v = dsp.eq_cuts(v, sr, cuts)
    report["resonances"] = cuts
    if cuts:
        say("거슬리는 울림을 깎았습니다: " + ", ".join(f"{c['hz']:,.0f} Hz −{c['cut_db']:g} dB" for c in cuts))
    # 치찰음 줄이기는 두 번에 나눠 약하게 건다. 압축은 큰 모음을 누르는 만큼 작은 'ㅅ' 소리를 상대로 키우므로,
    # 한 번은 압축 앞에서, 한 번은 압축 뒤(따뜻하게 하기 앞)에서 건다. 띠는 처음에 한 번 정해 두 번 다 같은 띠를 쓴다
    band = DEESS_BANDS[o["deess_band"]] or (dsp.find_sibilance_band(v, sr) if o["deess"] > 0 else None)
    v, first = dsp.deess(v, sr, o["deess"] / 2, band)
    # 압축 앞에서 'ㅅ' 이 둘레의 노래보다 얼마나 튀었는지 재 두고, 압축 뒤에는 그보다 튀는 만큼만 되돌린다
    burst = dsp.burst_level(v, sr, band) if band else None
    v, report["compress"] = dsp.serial_compress(v, sr, o["comp"])
    v, report["parallel"] = dsp.parallel_compress(v, sr, o["parallel"])
    v, second = dsp.deess(v, sr, o["deess"] / 2, band, threshold_db=burst, against="slow")
    report["deess"] = {"before_compress": first, "after_compress": second}
    if o["deess"] > 0:
        say(f"치찰음을 줄였습니다: {first['band_hz'][0]:,}~{first['band_hz'][1]:,} Hz 를 압축 앞뒤에서 두 번, "
            f"목소리가 나는 시간의 {first['active_share'] * 100:.1f}%·{second['active_share'] * 100:.1f}% 동안, "
            f"가장 많이 줄인 곳 {first['deepest_db']:g}·{second['deepest_db']:g} dB")
    v, report["warmth"] = dsp.saturate(v, sr, o["warmth"], o["warmth_style"], o["warmth_band"])
    v = dsp.air(v, sr, o["air"])
    # 처리로 바뀐 크기를 처음 크기로 되돌린다 (반주와의 크기 차이를 그대로 두고, 바꾸고 싶으면 --vocal-level 로 바꾼다)
    v = (v * dsp.undb(level_in - dsp.active_rms_db(v, sr))).astype(np.float32)
    return v


def main() -> None:
    ap = argparse.ArgumentParser(description="만든 소리를 다듬는다 (나누기 → 목소리 다듬기 → 다시 섞기 → 마무리)")
    ap.add_argument("--input", required=True, help="다듬을 소리나 영상 파일 (wav, flac, mp3, ogg, m4a, mp4, mov, webm, mkv)")
    ap.add_argument("--output", required=True, help="다듬은 소리 (.flac 또는 .wav)")
    ap.add_argument("--mp3", help="들어 보기 쉬운 mp3 사본")
    ap.add_argument("--video-out", help="영상을 넣었을 때, 다듬은 소리로 바꾼 영상 (.mp4). 소리만 넣었으면 만들지 않는다")
    ap.add_argument("--vocals-out", help="나눈 목소리를 다듬은 것 (노래·영상을 나눴을 때만)")
    ap.add_argument("--backing-out", help="나눈 반주 (노래·영상을 나눴을 때만)")
    ap.add_argument("--report", help="처리 전후에 잰 값과 정한 것을 적은 JSON")
    ap.add_argument("--score-file", help="곡의 악보 (ABC 표기). 빠르기 줄(Q:)을 읽어 메아리를 박자에 맞춘다")
    ap.add_argument("--source", choices=SOURCES, default="song", help="넣은 것이 무엇인지: song 노래, voice 목소리만, sfx 효과음, mix 곡 전체만")
    ap.add_argument("--separator", choices=SEPARATORS, default="htdemucs", help="나누기 모델: htdemucs 빠름, htdemucs_ft 정밀(약 4배 느림)")
    ap.add_argument("--shifts", type=int, default=1, help="나누기를 조금씩 어긋나게 여러 번 해 평균 낸다 (0~5). 늘리면 깨끗해지고 느려진다")
    ap.add_argument("--highpass", type=float, help="이 주파수(Hz) 아래를 자른다. 0 이면 끔")
    ap.add_argument("--resonance", type=float, help="거슬리는 울림(2~4 kHz, 6~9 kHz)을 찾아 깎는 최대 양 (dB). 0 이면 끔")
    ap.add_argument("--deess", type=float, help="치찰음을 줄이는 최대 양 (dB). 0 이면 끔")
    ap.add_argument("--deess-band", choices=("auto", "bright", "dark"), default="auto",
                    help="치찰음 띠: auto 는 재서 찾음, bright 6~8 kHz, dark 5~7 kHz")
    ap.add_argument("--comp", type=float, help="압축기 두 대가 큰 소리에서 각각 줄이는 양 (dB). 0 이면 끔")
    ap.add_argument("--parallel", type=float, help="세게 누른 사본을 섞는 몫 (0~1). 0 이면 끔")
    ap.add_argument("--warmth", type=float, help="따뜻하게 하는 정도 (0~1). 0 이면 끔")
    ap.add_argument("--warmth-style", choices=("tape", "tube"), default="tape", help="tape 부드럽게, tube 두툼하게")
    ap.add_argument("--warmth-band", choices=("lowmid", "full"), default="lowmid", help="lowmid 200~800 Hz 만, full 전체")
    ap.add_argument("--air", type=float, help="10 kHz 위를 올리는 양 (dB). 0 이면 끔")
    ap.add_argument("--reverb", type=float, help="잔향 크기: 목소리에 견준 몫 (0~1, 0.12 면 약 −18 dB). 0 이면 끔")
    ap.add_argument("--reverb-seconds", type=float, default=1.6, help="잔향이 60 dB 줄어드는 데 걸리는 시간 (초)")
    ap.add_argument("--predelay", type=float, default=30.0, help="잔향이 늦게 시작하는 시간 (ms)")
    ap.add_argument("--delay", type=float, help="메아리 크기: 목소리에 견준 몫 (0~1). 0 이면 끔")
    ap.add_argument("--delay-note", choices=tuple(dsp.NOTES), default="1/8", help="메아리 간격 (음표)")
    ap.add_argument("--tempo", type=float, help="곡 빠르기 (1분에 4분음표 수). 비우면 악보에서 읽는다")
    ap.add_argument("--vocal-level", type=float, default=0.0, help="다시 섞을 때 목소리를 올리거나 내리는 양 (dB)")
    ap.add_argument("--duck", type=float, help="목소리가 나올 때 반주를 낮추는 양 (dB). 0 이면 끔")
    ap.add_argument("--carve", type=float, help="반주의 3 kHz 둘레를 비우는 양 (dB). 0 이면 끔")
    ap.add_argument("--glue", type=float, help="섞은 소리 전체를 가볍게 누르는 양 (dB). 0 이면 끔")
    ap.add_argument("--loudness", type=float, help="마지막 음량 (LUFS). 음원 사이트 −14, 말소리 −16, 게임 효과음 −18 쯤")
    ap.add_argument("--keep-loudness", action="store_true", help="음량을 맞추지 않는다 (가장 큰 곳만 천장 아래로)")
    ap.add_argument("--ceiling", type=float, help="가장 큰 곳의 천장 (dBTP)")
    ap.add_argument("--fade-in", type=float, default=0.0, help="처음을 키우며 시작하는 시간 (ms)")
    ap.add_argument("--fade-out", type=float, default=0.0, help="끝을 줄이며 끝내는 시간 (ms)")
    a = ap.parse_args()

    t0 = time.time()
    o = dict(DEFAULTS[a.source])
    for k in o:
        v = getattr(a, k)
        if v is not None:
            o[k] = v
    o |= {"warmth_style": a.warmth_style, "warmth_band": a.warmth_band, "deess_band": a.deess_band}
    if not 0 <= a.shifts <= 5:
        fail(f"--shifts 는 0~5 여야 합니다 (받은 값 {a.shifts})")
    if not os.path.isfile(a.input):
        fail(f"다듬을 파일이 없습니다 ({a.input})")
    for p in (a.output, a.mp3, a.video_out, a.vocals_out, a.backing_out, a.report):
        if p:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    ff = ffmpeg_path()
    report: dict = {"source": a.source, "settings": o | {"separator": a.separator, "shifts": a.shifts,
                                                        "vocal_level": a.vocal_level, "keep_loudness": a.keep_loudness}}
    times: dict[str, float] = {}

    with tempfile.TemporaryDirectory(prefix="polish-") as tmp:
        stage(1)
        t = time.time()
        video = bool(ff) and a.input.lower().endswith(VIDEO_EXTS) and has_video(ff, a.input)
        x, sr = read_audio(a.input, ff, tmp)
        if len(x) < sr * 0.05:
            fail(f"소리가 너무 짧습니다 ({len(x) / sr:.3f}초). 0.05초 넘는 소리를 넣으세요.")
        if x.shape[1] > 2:                          # 5.1 채널 같은 소리는 스테레오로 접는다
            x = np.stack([x[:, 0::2].mean(axis=1), x[:, 1::2].mean(axis=1)], axis=1).astype(np.float32)
        if a.source in ("song", "mix") and sr < 44100:
            x, sr = resample(x, sr, 44100), 44100
        report["input"] = dsp.measure(x, sr) | {"sample_rate": sr, "channels": x.shape[1], "video": video}
        say(f"읽었습니다: {len(x) / sr:.1f}초, {sr:,} Hz, {'스테레오' if x.shape[1] == 2 else f'{x.shape[1]}채널'}"
            + (", 영상의 소리" if video else "") + f", 음량 {report['input']['lufs']} LUFS")
        times["read"] = time.time() - t

        bpm = a.tempo
        if bpm is None and a.score_file and os.path.isfile(a.score_file):
            with open(a.score_file, encoding="utf-8") as f:
                bpm = dsp.tempo_from_abc(f.read())
            if bpm:
                say(f"악보에서 빠르기를 읽었습니다: 1분에 4분음표 {bpm:g}개")
        report["tempo_bpm"] = bpm

        vocals = backing = None
        if a.source == "song":
            stage(2, f" ({a.separator})")
            t = time.time()
            vocals, backing, report["separation"] = separate(x, sr, a.separator, a.shifts)
            report["vocals_in"] = dsp.measure(vocals, sr)
            say(f"나눴습니다: 목소리 {report['vocals_in']['active_rms_db']:g} dBFS, 반주 {dsp.active_rms_db(backing, sr):.1f} dBFS, "
                f"{report['separation']['seconds']:g} s")
            times["separate"] = time.time() - t
        else:
            stage(2, " (건너뜀: 나누지 않는 종류)")

        stage(3)
        t = time.time()
        if a.source == "song":
            vocals = polish_vocal(vocals, sr, o, report)
            vocals = (vocals * dsp.undb(a.vocal_level)).astype(np.float32)
            report["vocals_out"] = dsp.measure(vocals, sr)
        elif a.source == "voice":
            x = polish_vocal(x, sr, o, report)
        elif a.source == "sfx":
            level_in = dsp.active_rms_db(x, sr)
            x = dsp.highpass(x, o["highpass"], sr)
            x, report["compress"] = dsp.serial_compress(x, sr, o["comp"])
            x = (x * dsp.undb(level_in - dsp.active_rms_db(x, sr))).astype(np.float32)   # 누른 만큼 되돌린다
        else:                                       # mix: 곡 전체
            x = dsp.highpass(x, o["highpass"], sr)
            x, report["deess"] = dsp.deess(x, sr, o["deess"])
            x = dsp.air(x, sr, o["air"])
        times["vocal"] = time.time() - t

        stage(4)
        t = time.time()
        if a.source == "song":
            backing = dsp.carve(backing, sr, o["carve"])
            backing, report["duck"] = dsp.duck(backing, vocals, sr, o["duck"])
            rev, report["reverb"] = dsp.send_reverb(vocals, sr, o["reverb"], a.reverb_seconds, a.predelay)
            dly, report["delay"] = dsp.send_delay(vocals, sr, o["delay"], bpm, a.delay_note)
            if o["delay"] > 0 and not bpm:
                say("알림: 곡 빠르기를 몰라 메아리를 넣지 않았습니다. 악보를 넣거나 빠르기를 적으세요.")
            ch = x.shape[1]
            wide = (lambda s: s if ch == 2 else s.mean(axis=1, keepdims=True))
            x = (backing + vocals + wide(rev) + wide(dly)).astype(np.float32)
        elif a.source == "voice" and (o["reverb"] > 0 or o["delay"] > 0):
            rev, report["reverb"] = dsp.send_reverb(x, sr, o["reverb"], a.reverb_seconds, a.predelay)
            dly, report["delay"] = dsp.send_delay(x, sr, o["delay"], bpm, a.delay_note)
            if x.shape[1] == 1:
                rev, dly = rev.mean(axis=1, keepdims=True), dly.mean(axis=1, keepdims=True)
            x = (x + rev + dly).astype(np.float32)
        else:
            say("(건너뜀: 섞을 것이 없음)")
        times["mix"] = time.time() - t

        stage(5)
        t = time.time()
        if o["glue"] > 0:
            level = dsp.active_rms_db(x, sr)
            x, g = dsp.compress(x, sr, dsp.threshold_for(x, sr, o["glue"], 2.0), 2.0, 0.030, 0.250)
            x = (x * dsp.undb(level - dsp.active_rms_db(x, sr))).astype(np.float32)     # 누른 만큼 되돌린다 (음량을 그대로 둘 때도 맞게)
            report["glue"] = {"target_db": o["glue"], "p95_gr_db": round(float(-np.percentile(g, 5)), 2)}
        x = dsp.fades(x, sr, a.fade_in, a.fade_out)
        if a.keep_loudness:
            x, report["master"] = dsp.limit(x, sr, o["ceiling"])
        else:
            x, report["master"] = dsp.to_loudness(x, sr, o["loudness"], o["ceiling"])
            m = report["master"]
            if m.get("capped"):
                m["note"] = (f"넣은 소리가 {report['input']['lufs']} LUFS 로 아주 작아 {m['max_gain_db']:g} dB 까지만 올렸습니다 "
                             f"(목표 {o['loudness']:g} LUFS 에 못 미침)")
                say(f"알림: {m['note']}. 소리가 든 파일인지 확인하세요.")
            elif m.get("short"):
                m["note"] = "0.4초보다 짧아 음량 대신 가장 큰 곳만 천장에 맞췄습니다"
                say(f"알림: {m['note']}.")
        x = np.clip(x, -1.0, 1.0)
        report["output"] = dsp.measure(x, sr) | {"sample_rate": sr, "channels": x.shape[1]}
        subtype = "PCM_24" if a.output.lower().endswith(".flac") else "FLOAT"
        sf.write(a.output, x, sr, subtype=subtype)
        written = [os.path.basename(a.output)]
        if a.source == "song":
            if a.vocals_out:
                sf.write(a.vocals_out, np.clip(vocals, -1, 1), sr, subtype="PCM_24")
                written.append(os.path.basename(a.vocals_out))
            if a.backing_out:
                sf.write(a.backing_out, np.clip(backing, -1, 1), sr, subtype="PCM_24")
                written.append(os.path.basename(a.backing_out))
        if ff and a.mp3:
            run_ffmpeg(ff, ["-i", a.output, "-c:a", "libmp3lame", "-b:a", "192k", a.mp3], "mp3 만들기")
            written.append(os.path.basename(a.mp3))
        if video and a.video_out:
            run_ffmpeg(ff, ["-i", a.input, "-i", a.output, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                            "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", a.video_out],
                       "영상에 다듬은 소리 붙이기")
            written.append(os.path.basename(a.video_out))
        elif (a.mp3 or a.video_out) and not ff:
            say("주의: ffmpeg 가 없어 mp3·영상 사본을 만들지 못했습니다 (media 환경을 확인하세요).")
        times["save"] = time.time() - t

    report["times_s"] = {k: round(v, 2) for k, v in times.items()} | {"total": round(time.time() - t0, 1)}
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    i, out = report["input"], report["output"]
    print("polish_info=" + json.dumps({"source": a.source, "seconds": out["seconds"], "lufs_in": i["lufs"],
                                       "lufs_out": out["lufs"], "total_s": report["times_s"]["total"]},
                                      ensure_ascii=False), flush=True)
    # 치찰음이 튀는 정도는 목소리에서 잰다 (노래면 나눈 목소리의 처리 전후, 목소리만이면 전체의 처리 전후)
    vi, vo = (report.get("vocals_in"), report.get("vocals_out")) if a.source == "song" else (i, out)
    sib = (f", 'ㅅ' 소리가 둘레보다 튀는 정도 {vi['sibilance_burst_db']:g} → {vo['sibilance_burst_db']:g} dB"
           if a.source in ("song", "voice") and vi and vo and "sibilance_burst_db" in vi and "sibilance_burst_db" in vo else "")
    lufs = (lambda v: "잴 수 없음" if v is None else f"{v:g}")
    say(f"끝: {', '.join(written)}. 음량 {lufs(i['lufs'])} → {lufs(out['lufs'])} LUFS, 가장 큰 곳 {i['true_peak_db']:g} → "
        f"{out['true_peak_db']:g} dBTP{sib}, {report['times_s']['total']:g} s")


if __name__ == "__main__":
    main()
