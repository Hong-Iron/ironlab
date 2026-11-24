"""YuE2 로 노래 한 곡을 만든다 (분위기 설명 + 가사 → 노래와 반주가 든 곡).

    python yue2_song.py --style "City Pop, upbeat, groovy bass" --lyrics-file lyrics.txt --output song.flac

YuE2 는 세 단계로 만든다. 조절 값도 그 단계를 따라 나뉜다.

1. 악보 쓰기 (--plan-*):    멜로디와 코드를 글자 악보(ABC 표기)로 먼저 쓴다.
2. 노래 만들기 (--song-*):  그 악보와 가사를 보고 소리의 설계도(토큰)를 한 개씩 뽑는다. 곡의 길이가 여기서 정해진다.
3. 소리로 바꾸기 (--audio-steps): 설계도를 실제 파형으로 바꾼다.

--stage plan 은 1단계까지만 하고 멈춘다. 그 악보를 고쳐 --abc-file 로 다시 주면 고친 악보대로 2~3단계를 한다.
가사는 [Verse], [Chorus] 같은 구획 표시를 줄 맨 앞에 두고 그 아래에 한 줄씩 적는다.

1~2단계는 다음 글자(토큰)를 확률로 고르는 방식이다. 단계마다 고르는 방식을 네 값으로 조절한다.

- temperature: 확률을 얼마나 평평하게 볼지. 낮추면 뻔한 것만 고르고, 높이면 엉뚱한 것도 고른다.
- top-k:       확률이 높은 순서로 k개만 후보로 둔다.
- top-p:       확률을 큰 것부터 더해 p 가 될 때까지만 후보로 둔다.
- repeat:      조금 전에 고른 것을 다시 고를 확률을 깎는 정도. 1 이면 깎지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

MODEL = "m-a-p/YuE2-3B"
COT = {"full": "멜로디와 코드를 먼저 쓴다 (기본)", "melody": "멜로디만 먼저 쓴다 (기존 곡을 다른 장르로 바꿀 때)",
       "off": "악보 없이 바로 만든다"}
# (옵션 머리, 설명, 모델 기본값: temperature, top_p, top_k, repetition_penalty, max_tokens) — 모델과 함께 온 설정 파일의 값
STAGES = (
    ("plan", "악보 쓰기", 0.7, 0.9, 30, 1.005, 4096),
    ("song", "노래 만들기", 1.0, 0.95, 100, 1.2, 9000),
)
MIN_TOKENS = {"plan": 32, "song": 200}          # 모델이 단계마다 "적어도 이만큼은 뽑는다"고 정해 둔 수


def say(text: str) -> None:
    print(f"[yue2] {text}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="YuE2 로 노래 한 곡을 만든다")
    ap.add_argument("--style", help="분위기와 장르 설명 (예: City Pop, upbeat, groovy bass, female vocal)")
    ap.add_argument("--style-file", help="분위기 설명을 적은 글 파일 (UTF-8)")
    ap.add_argument("--lyrics-file", required=True, help="가사 파일 (UTF-8). [Verse] 같은 구획 표시를 쓴다")
    ap.add_argument("--output", required=True, help="결과 소리 파일 경로 (.flac). --stage plan 이면 악보 파일 경로 (.abc)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cot", choices=sorted(COT), default="full",
                    help="악보를 먼저 쓰는 방식. " + "; ".join(f"{k}: {v}" for k, v in COT.items()))
    ap.add_argument("--cfg-scale", type=float, help="글을 얼마나 세게 따를지 (모델 기본값은 1.0, 실험할 때 1.2 쯤)")
    ap.add_argument("--abc-file", help="직접 주는 악보 (ABC 표기). 주면 모델이 악보를 새로 쓰지 않는다")
    for flag, what, temperature, top_p, top_k, repeat, max_tokens in STAGES:
        ap.add_argument(f"--{flag}-temperature", type=float, help=f"{what}: 고르는 폭 0~5 (모델 기본값 {temperature:g})")
        ap.add_argument(f"--{flag}-top-p", type=float, help=f"{what}: 후보로 둘 확률의 합 0~1 (모델 기본값 {top_p:g})")
        ap.add_argument(f"--{flag}-top-k", type=int, help=f"{what}: 후보로 둘 개수 (모델 기본값 {top_k})")
        ap.add_argument(f"--{flag}-repeat", type=float, help=f"{what}: 되풀이를 깎는 정도 (모델 기본값 {repeat:g}, 1 이면 끔)")
        ap.add_argument(f"--{flag}-max-tokens", type=int, help=f"{what}: 뽑을 토큰 수의 한도 (모델 기본값 {max_tokens:,})")
    ap.add_argument("--audio-steps", type=int, help="소리로 바꾸기: 파형을 다듬는 횟수 (모델 기본값 32)")
    ap.add_argument("--stage", choices=("all", "plan"), default="all", help="plan: 악보까지만 쓰고 멈춘다")
    ap.add_argument("--artifacts", help="악보·토큰·설정 등 중간 결과를 함께 저장할 폴더")
    ap.add_argument("--mp3", help="들어 보기 쉬운 mp3 사본을 함께 쓸 경로 (콘솔의 음악 생성 화면이 쓴다)")
    ap.add_argument("--score", help="이 곡에 쓴 악보(ABC 표기)를 함께 쓸 경로 (콘솔의 음악 생성 화면이 쓴다)")
    args = ap.parse_args()

    if bool(args.style) == bool(args.style_file):
        ap.error("--style 과 --style-file 가운데 하나만 주세요")
    style = args.style or Path(args.style_file).read_text(encoding="utf-8").strip()
    lyrics = Path(args.lyrics_file).read_text(encoding="utf-8").strip()
    if not style or not lyrics:
        ap.error("분위기 설명과 가사가 모두 있어야 합니다")
    if args.cfg_scale is not None and not 0.5 <= args.cfg_scale <= 5:
        ap.error(f"cfg-scale 은 0.5~5 사이여야 합니다 (받은 값 {args.cfg_scale})")
    if args.audio_steps is not None and not 1 <= args.audio_steps <= 200:
        ap.error(f"--audio-steps 는 1~200 사이여야 합니다 (받은 값 {args.audio_steps})")
    abc = Path(args.abc_file).read_text(encoding="utf-8") if args.abc_file else None
    if abc is not None and args.cot == "off":
        ap.error("악보를 직접 주면서 --cot off (악보 없이 바로) 를 함께 쓸 수 없습니다. --cot full 이나 melody 로 바꾸세요")

    # 단계마다 바꾼 값만 모은다. 주지 않은 값은 모델 기본값을 그대로 쓴다.
    sampling: dict[str, dict] = {}
    for flag, what, *_ in STAGES:
        given = {}
        for arg, key in (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k"),
                         ("repeat", "repetition_penalty"), ("max_tokens", "max_tokens")):
            value = getattr(args, f"{flag}_{arg}")
            if value is not None:
                given[key] = value
        if "max_tokens" in given and given["max_tokens"] < MIN_TOKENS[flag]:
            ap.error(f"--{flag}-max-tokens 는 {MIN_TOKENS[flag]} 이상이어야 합니다 (받은 값 {given['max_tokens']}). "
                     f"{what} 단계는 적어도 {MIN_TOKENS[flag]}개를 뽑게 되어 있습니다")
        sampling[flag] = given
    for path in (args.output, args.mp3, args.score):
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    started = time.time()
    import torch
    from yue2 import YuE2Pipeline
    from yue2.protocol import GenerationConfig

    say(f"시작: 모델 {MODEL}, 악보 방식 {args.cot}, 시드 {args.seed}, 가사 {len(lyrics)}자"
        + (", 받은 악보 사용" if abc else "") + (", 악보까지만" if args.stage == "plan" else ""))
    for flag, what, *_ in STAGES:
        if sampling[flag]:
            say(f"{what} 설정: " + ", ".join(f"{k} {v:g}" for k, v in sampling[flag].items()))
    load_kw = {}
    if args.audio_steps is not None:
        load_kw["generation_config"] = GenerationConfig(ode_steps=args.audio_steps)
        say(f"소리로 바꾸기 설정: {args.audio_steps}번")
    pipe = YuE2Pipeline.from_pretrained(MODEL, device="cuda", **load_kw)
    say(f"모델을 읽었습니다: {time.time() - started:.0f} s")
    call = dict(style=style, lyrics=lyrics, cot=args.cot, seed=args.seed)
    if args.cfg_scale is not None:
        call["cfg_scale"] = args.cfg_scale
    if abc is not None:
        call["abc"] = abc
    if sampling["plan"]:
        call["abc_sampling"] = sampling["plan"]
    making = time.time()
    try:
        if args.stage == "plan":
            plan = pipe.plan(**call)
            text = getattr(plan, "abc", None) or str(plan)
            Path(args.output).write_text(text, encoding="utf-8")
            say(f"끝: 악보 {args.output} ({len(text)}자), 쓰는 데 {time.time() - making:.0f} s, 전체 {time.time() - started:.0f} s")
            return 0
        if sampling["song"]:
            call["semantic_sampling"] = sampling["song"]
        song = pipe(**call)
        song.save(args.output)
        if args.artifacts:
            song.save_artifacts(args.artifacts)
        if args.score and song.abc:                 # 악보 없이 바로 만든 곡(--cot off)에는 악보가 없다
            Path(args.score).write_text(song.abc, encoding="utf-8")
        seconds = None
        try:
            import soundfile as sf
            info = sf.info(args.output)
            seconds = info.frames / info.samplerate
            if args.mp3:
                data, rate = sf.read(args.output)
                sf.write(args.mp3, data, rate, format="MP3")
        except Exception as e:                      # 길이를 못 읽거나 mp3 를 못 써도 곡 자체는 이미 저장됐다
            say(f"참고: 길이 확인이나 mp3 사본 쓰기를 건너뜁니다 ({type(e).__name__}: {e})")
        cut = [what for (flag, what, *_), key in zip(STAGES, ("abc", "semantic")) if song.truncated.get(key)]
        if cut:
            say(f"참고: {', '.join(cut)} 단계가 토큰 수 한도에 걸려 중간에 끊겼습니다. 한도(--plan-max-tokens, --song-max-tokens)를 늘려 보세요")
        peak = torch.cuda.max_memory_allocated() / 2**30
        took = time.time() - making
        tokens = len(song.semantic.tokens)
        say(f"끝: {args.output}" + (f" (곡 길이 {seconds:.0f} s, 토큰 {tokens:,}개)" if seconds else "")
            + f", 만드는 데 {took:.0f} s, 전체 {time.time() - started:.0f} s, GPU 메모리 최대 {peak:.1f} GiB")
        print(json.dumps({"output": args.output, "audio_seconds": seconds, "song_tokens": tokens,
                          "generate_seconds": round(took, 1), "peak_vram_gib": round(peak, 2)}, ensure_ascii=False), flush=True)
    finally:
        pipe.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
