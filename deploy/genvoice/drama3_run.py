#!/usr/bin/env python3
"""Fish Audio Drama 3 로 대사를 목소리로 읽는다. ironlab 의 '음성 생성' 화면과 '목소리 만들기' 노드가 drama3.sh 를 거쳐 부른다.

하는 일은 세 가지다.

1. 대본을 모델이 읽는 모양으로 바꾼다. '화자1: 안녕' 같은 줄 머리를 모델의 화자 표시(<|speaker:0|>)로 바꾼다.
2. 참고 목소리(있으면)를 44.1 kHz 모노 WAV 로 바꾸고, audio.cpp 의 audiocpp_cli 로 모델을 돌려 WAV 를 쓴다.
3. 들어 보기 쉬운 MP3 사본을 만든다.

모델은 35B 짜리 MoE(전문가 여럿 가운데 몇 개만 골라 쓰는 구조)를 4비트로 줄인 GGUF 다 (~/models/drama3, DRAMA3_MODEL 로 바꿀 수 있다).
24 GB GPU 에 맞추려고 앞쪽 전문가 층 몇 개를 CPU 메모리에 두고 CPU 로 계산한다 (--cpu-layers).
2026-11-14 이 서버(RTX 3090)에서 잰 값: 16층을 CPU 에 두면 GPU 메모리 최대 16.5 GB, 8.5초 대사를 3.6초에 만들었다.
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
MODEL = os.environ.get("DRAMA3_MODEL", os.path.join(HOME_DIR, "models", "drama3", "drama-3-backbone-8bit-experts-Q4_K_S.gguf"))
CLI = os.environ.get("DRAMA3_CLI", os.path.join(HERE, "bin", "audiocpp_cli"))
MEDIA_BIN = os.environ.get("DRAMA3_MEDIA_BIN", os.path.join(HOME_DIR, ".conda", "envs", "media", "bin"))
MAX_REFS = 4
MAX_REF_SECONDS = 30
STAGES = ("모델 올리기", "말하기", "파일 쓰기")
SPEAKER_RE = re.compile(r"(?m)^[ \t]*(?:화자|speaker|S)[ \t]*(\d{1,2})[ \t]*[:：][ \t]*", re.IGNORECASE)
KEEP_RE = re.compile(r"(?i)(error|fail|warn|budget reached|reduced|reject|exception|abort)")


def say(msg: str) -> None:
    print(f"[drama3] {msg}", flush=True)


def stage(n: int) -> None:
    say(f"단계 {n}/{len(STAGES)}: {STAGES[n - 1]}")


def fail(msg: str, code: int = 1) -> None:
    say(f"실패: {msg}")
    sys.exit(code)


def read_text(path: str | None) -> str:
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def speaker_tags(script: str) -> str:
    """'화자1: 대사' 줄 머리를 <|speaker:0|> 로 바꾼다. 화자 번호는 1부터 16까지 (모델은 0부터 15까지)."""
    def tag(m: re.Match) -> str:
        n = int(m.group(1))
        if not 1 <= n <= 16:
            fail(f"화자 번호는 1~16 이어야 합니다 (받은 값 {m.group(0).strip()!r})", 2)
        return f"<|speaker:{n - 1}|>"
    return SPEAKER_RE.sub(tag, script)


def to_wav(ffmpeg: str, src: str, dst: str) -> float:
    """참고 목소리를 44.1 kHz 모노 16비트 WAV 로 바꾸고 길이(초)를 돌려준다. 30초가 넘으면 앞 30초만 쓴다."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-t", str(MAX_REF_SECONDS),
                        "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", dst], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(dst):
        fail(f"참고 목소리 파일을 읽지 못했습니다 ({os.path.basename(src)}): {r.stderr.strip()[-200:]}", 2)
    return (os.path.getsize(dst) - 44) / (44100 * 2)


def run_cli(cmd: list[str]) -> tuple[int, dict]:
    """audiocpp_cli 를 돌린다. 자세한 기록 줄은 걸러 내고, 글 조각마다 시작·끝 줄과 오류·경고 줄만 남긴다."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")
    piece, info = 0, {}
    for line in proc.stdout:
        line = line.rstrip("\n")
        body = re.sub(r"^\[(TRACE|TIMING) ts=[0-9-]+\] ", "", line)
        if "drama3.prompt_rows" in body:
            piece += 1
            if piece == 1:
                stage(2)
            say(f"글 조각 {piece} 읽는 중")
        elif "drama3.generated_frames" in body:
            frames = body.split()[-1]
            try:
                say(f"글 조각 {piece} 끝: {int(float(frames)):,}프레임 ({float(frames) / 21.5:.1f}초)")
            except ValueError:
                pass
        elif body.startswith("custom_schema_output="):
            try:
                info = json.loads(body.split("=", 1)[1]).get("data", {})
            except ValueError:
                pass
        elif body.startswith("metrics.") or body.startswith("audio_out="):
            print(body, flush=True)
        elif KEEP_RE.search(body) and "CUDA Graph" not in body:
            print(body, flush=True)
    return proc.wait(), info


def main() -> None:
    ap = argparse.ArgumentParser(description="Fish Audio Drama 3: 대본을 목소리로 읽는다")
    ap.add_argument("--text-file", required=True, help="대본. '화자1: 대사' 줄로 여러 사람을 나눈다")
    ap.add_argument("--ref", action="append", default=[], help="참고 목소리 파일 (화자 순서대로, 4개까지)")
    ap.add_argument("--ref-text-file", help="참고 목소리마다 그 목소리가 하는 말 (한 줄에 하나, --ref 순서대로)")
    ap.add_argument("--output", required=True, help="결과 WAV 경로")
    ap.add_argument("--mp3", help="들어 보기용 MP3 경로")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=0.9)
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--max-tokens", type=int, default=1024, help="글 조각 하나에서 만들 소리 프레임의 한도 (21.5개가 1초)")
    ap.add_argument("--chunk-size", type=int, help="한 번에 읽을 글 조각의 길이 (글자 수)")
    ap.add_argument("--context", type=int, default=4096, help="모델이 한 번에 기억하는 길이 (참고 목소리 + 글 + 만든 소리)")
    ap.add_argument("--cpu-layers", type=int, default=16, help="전문가 층 40개 가운데 CPU 에서 계산할 수")
    ap.add_argument("--fast", action="store_true", help="CUDA 빠른 경로 (perf_mode=cuda_perf)")
    ap.add_argument("--threads", type=int, default=16)
    a = ap.parse_args()

    t0 = time.time()
    if not os.path.isfile(CLI):
        fail(f"audiocpp_cli 가 없습니다 ({CLI}). setup_drama3.sh 로 빌드하세요.")
    if not os.path.isfile(MODEL):
        fail(f"모델 파일이 없습니다 ({MODEL}). setup_drama3.sh 의 내려받기를 먼저 하세요.")
    ffmpeg = os.path.join(MEDIA_BIN, "ffmpeg")
    if len(a.ref) > MAX_REFS:
        fail(f"참고 목소리는 {MAX_REFS}개까지입니다 (받은 수 {len(a.ref)}개)", 2)
    if not 0 <= a.cpu_layers <= 40:
        fail(f"--cpu-layers 는 0~40 이어야 합니다 (받은 값 {a.cpu_layers})", 2)

    script = speaker_tags(read_text(a.text_file))
    if not script:
        fail("대본이 비어 있습니다", 2)
    ref_lines = [x.strip() for x in read_text(a.ref_text_file).splitlines() if x.strip()]
    if a.ref and len(ref_lines) != len(a.ref):
        fail(f"참고 목소리 {len(a.ref)}개에 그 목소리가 하는 말이 {len(ref_lines)}줄 있습니다. "
             "한 줄에 하나씩, 올린 순서대로 같은 수만큼 적어 주세요.", 2)

    out = os.path.abspath(a.output)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    work = out + ".parts"
    os.makedirs(work, exist_ok=True)

    voices = f"참고 목소리 {len(a.ref)}개" if a.ref else "참고 목소리 없음"
    say(f"시작: 대본 {len(script):,}자, {voices}, CPU 층 {a.cpu_layers}개, 시드 {a.seed}")
    stage(1)
    cmd = [CLI, "--task", "tts", "--family", "fish_drama3", "--backend", "cuda", "--mode", "offline",
           "--model", MODEL, "--text", script, "--seed", str(a.seed), "--threads", str(a.threads),
           "--session-option", f"moe_cpu_expert_layers={a.cpu_layers}",
           "--session-option", f"fish_drama3.context_length={a.context}",
           "--request-option", f"temperature={a.temperature:g}",
           "--request-option", f"top_p={a.top_p:g}",
           "--request-option", f"max_tokens={a.max_tokens}",
           "--out", out, "--log", "--metrics"]
    if a.top_k is not None:
        cmd += ["--request-option", f"top_k={a.top_k}"]
    if a.chunk_size is not None:
        cmd += ["--request-option", f"text_chunk_size={a.chunk_size}"]
    if a.fast:
        cmd += ["--session-option", "fish_drama3.perf_mode=cuda_perf"]
    if a.ref:
        if not os.path.isfile(ffmpeg):
            fail(f"ffmpeg 가 없어 참고 목소리를 바꾸지 못했습니다 ({ffmpeg})")
        refs = []
        for i, (src, text) in enumerate(zip(a.ref, ref_lines)):
            if not os.path.isfile(src):
                fail(f"참고 목소리 파일이 없습니다: {src}", 2)
            wav = os.path.join(work, f"ref{i + 1}.wav")
            secs = to_wav(ffmpeg, src, wav)
            say(f"참고 목소리 {i + 1}: {secs:.1f}초")
            refs.append({"text": text if len(a.ref) == 1 else f"<|speaker:{i}|>{text}", "audio_path": wav})
        if len(refs) == 1:
            cmd += ["--voice-ref", refs[0]["audio_path"], "--reference-text", refs[0]["text"]]
        else:
            cmd += ["--request-option", "references=" + json.dumps(refs, ensure_ascii=False)]
            if "<|speaker:" not in script:
                cmd[cmd.index("--text") + 1] = f"<|speaker:0|>{script}"

    code, info = run_cli(cmd)
    if code != 0 or not os.path.isfile(out):
        fail(f"audiocpp_cli 가 끝 코드 {code} 로 멈췄습니다. 위 로그의 오류 줄을 보세요.")
    if info.get("reached_limit"):
        say("경고: 한 글 조각이 길이 한도에 닿아 끝이 잘렸을 수 있습니다. '조각 크기'를 줄이거나 '조각 길이 한도'를 늘려 보세요.")
    stage(3)
    if a.mp3 and os.path.isfile(ffmpeg):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", out, "-c:a", "libmp3lame",
                        "-b:a", "192k", a.mp3])
    shutil.rmtree(work, ignore_errors=True)
    secs = (os.path.getsize(out) - 44) / (44100 * 2)
    say(f"끝: 목소리 {secs:.1f}초, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
