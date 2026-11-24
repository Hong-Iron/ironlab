#!/usr/bin/env python3
"""영상 여러 개를 차례대로 이어 붙여 하나로 만든다. ironlab 의 '영상 생성' 화면과 '영상 이어 붙이기' 노드가 h3.sh --join 으로 부른다.

GPU 메모리가 모자라 긴 영상을 한 번에 못 만들 때 쓴다. 짧은 영상을 만들고, 그 끝 장면을 다음 영상의 첫 장면으로 넣어
이어 만든 뒤, 이 스크립트로 붙인다.

그냥 붙이면 이음매에서 화면이 멈칫한다. 다음 영상의 첫 장면이 앞 영상의 끝 장면을 그대로 다시 그린 것이라 같은 장면이 두 번 나오기
때문이다. 예전 판의 '끝 장면'은 영상 끝에서 0.5초 앞의 장면이어서, 그대로 붙이면 0.5초를 되감는 것처럼 보이기도 한다.
그래서 이음매마다 다음 영상의 첫 장면과 가장 닮은 장면을 앞 영상의 끝 1.5초 안에서 찾고, 앞 영상을 그 장면 바로 앞에서 자른다.
닮은 장면이 없으면(이어 만든 영상이 아니면) 자르지 않고 그대로 붙인다. 소리도 같은 자리에서 자른다.

크기가 다른 영상은 첫 영상의 크기와 1초당 장면 수에 맞춘다 (비율이 다르면 남는 곳을 검게 채운다).

    h3.sh --join 앞.mp4 --join 뒤.mp4 [--join …] --output 결과.mp4 [--thumb 작은.jpg] [--last-frame 끝.png]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.dirname(HERE)
MEDIA_BIN = os.environ.get("H3_MEDIA_BIN", os.path.join(HOME_DIR, ".conda", "envs", "media", "bin"))
TINY_W, TINY_H = 64, 36         # 장면을 견줄 때 줄인 크기 (회색). 작아야 빠르고, 이 정도면 같은 장면인지 가리기에 충분하다
MATCH_SECONDS = 1.5             # 앞 영상의 끝에서 이만큼 안에서 다음 영상의 첫 장면을 찾는다
SAME_LIMIT = 8.0                # 줄인 회색 그림의 평균 밝기 차이(0~255). 이보다 작으면 같은 장면(되풀이)으로 본다.
                                # 끝 장면 사진으로 이어 만든 영상의 첫 장면은 1.6 쯤이었다. 참고 영상(Ref2VA)으로 이어 만든 영상은
                                # 마지막 장면의 다음 순간부터 시작해 13 쯤이었고, 이때는 자를 것이 없다
NEAR = 1.0                      # 가장 닮은 장면과 이만큼 안쪽으로 닮은 장면이 여럿이면 가장 뒤의 것을 고른다 (멈춰 있는 구간에서 앞쪽을 잘못 고르지 않게)
MAX_CLIPS = 20


def say(text: str) -> None:
    print(f"[h3-join] {text}", flush=True)


def fail(text: str) -> None:
    say(f"실패: {text}")
    sys.exit(1)


def tool(name: str) -> str:
    path = os.path.join(MEDIA_BIN, name)
    if not os.path.isfile(path):
        fail(f"{name} 가 없습니다 ({path}). 영상 생성 설치(ffmpeg 가 든 conda 환경 media)를 확인하세요.")
    return path


def probe(ffprobe: str, path: str) -> dict:
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,r_frame_rate,sample_rate:format=duration", "-of", "json", path],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        fail(f"영상을 읽지 못했습니다 ({path}): {r.stderr.strip()[-300:]}")
    info = json.loads(r.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        fail(f"영상이 들어 있지 않은 파일입니다 ({path})")
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    num, _, den = video.get("r_frame_rate", "24/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 24.0
    return {"path": path, "w": int(video["width"]), "h": int(video["height"]), "fps": fps or 24.0,
            "dur": float(info.get("format", {}).get("duration") or 0), "audio": audio is not None,
            "rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None}


def tiny_frames(ffmpeg: str, path: str, count: int | None = None) -> list[bytes]:
    """영상의 장면을 처음부터 64×36 회색으로 줄여 읽는다. 장면 번호 j 는 시각 j / fps 다."""
    cmd = [ffmpeg, "-nostdin", "-v", "error", "-i", path]
    if count:
        cmd += ["-frames:v", str(count)]
    cmd += ["-vf", f"scale={TINY_W}:{TINY_H}:flags=area,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL).stdout
    n = TINY_W * TINY_H
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]


def difference(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def main() -> None:
    ap = argparse.ArgumentParser(description="영상 여러 개를 차례대로 이어 붙인다 (이어 만든 영상의 겹치는 장면은 잘라 낸다)")
    ap.add_argument("--join", action="append", required=True, help="이을 영상. 붙일 차례대로 여러 번 쓴다")
    ap.add_argument("--output", required=True, help="결과 MP4 경로")
    ap.add_argument("--thumb", help="목록용 작은 미리보기 JPG 경로")
    ap.add_argument("--last-frame", help="마지막 장면 PNG 경로 (이어 붙인 영상에서 또 이어 만들 때 쓴다)")
    ap.add_argument("--crf", type=int, default=18, help="MP4 화질 (낮을수록 좋고 파일이 큼, 0~51)")
    a = ap.parse_args()
    if not 2 <= len(a.join) <= MAX_CLIPS:
        fail(f"이을 영상은 2~{MAX_CLIPS}개여야 합니다 (받은 수 {len(a.join)}개)")
    for path in a.join:
        if not os.path.isfile(path):
            fail(f"영상 파일이 없습니다: {path}")
    ffmpeg, ffprobe = tool("ffmpeg"), tool("ffprobe")
    t0 = time.time()
    out = os.path.abspath(a.output)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    say(f"단계 1/3 영상 {len(a.join)}개 읽기")
    clips = [probe(ffprobe, p) for p in a.join]
    base = clips[0]
    width, height, fps = base["w"], base["h"], base["fps"]
    rate = next((c["rate"] for c in clips if c["rate"]), 48000)
    for i, c in enumerate(clips, 1):
        say(f"{i}번째: {c['w']}×{c['h']}, {c['dur']:.2f}초, 1초에 {c['fps']:g}장, 소리 {'있음' if c['audio'] else '없음'}")
    if any((c["w"], c["h"]) != (width, height) for c in clips):
        say(f"크기가 다른 영상이 있습니다. 첫 영상 크기 {width}×{height} 에 맞추고, 비율이 다르면 남는 곳을 검게 채웁니다.")

    # 이음매마다 앞 영상을 어디서 자를지 정한다. ends[i] 는 i 번째 영상을 쓸 길이 (초). None 이면 끝까지
    say("단계 2/3 이어지는 장면 찾기")
    ends: list[float | None] = [None] * len(clips)
    frames = tiny_frames(ffmpeg, clips[0]["path"])
    for i in range(1, len(clips)):
        prev, cur = clips[i - 1], clips[i]
        first = tiny_frames(ffmpeg, cur["path"], 1)
        if not frames or not first:
            say(f"{i + 1}번째 이음매: 장면을 읽지 못해 자르지 않고 붙입니다")
        else:
            lo = max(1, len(frames) - round(MATCH_SECONDS * prev["fps"]) - 1)
            gaps = {j: difference(frames[j], first[0]) for j in range(lo, len(frames))}
            low = min(gaps.values()) if gaps else None
            best = max(j for j, g in gaps.items() if g <= low + NEAR) if gaps else None
            gap = gaps[best] if best is not None else None
            if best is not None and gap <= SAME_LIMIT:
                ends[i - 1] = best / prev["fps"]
                cut = prev["dur"] - ends[i - 1]
                say(f"{i + 1}번째 영상은 {i}번째 영상의 {ends[i - 1]:.2f}초 장면에서 이어집니다 (차이 {gap:.1f}). "
                    f"{i}번째 영상의 끝 {cut:.2f}초를 잘라 냅니다")
            else:
                say(f"{i + 1}번째 영상의 첫 장면은 {i}번째 영상 끝의 장면을 되풀이하지 않습니다 (가장 닮은 장면의 차이 "
                    f"{gap if gap is not None else float('nan'):.1f}, 같은 장면으로 보는 선 {SAME_LIMIT:g}). 자르지 않고 그대로 붙입니다")
        frames = tiny_frames(ffmpeg, cur["path"]) if i + 1 < len(clips) else []

    # ffmpeg 한 번으로: 영상마다 자르고 크기를 맞춘 뒤 차례대로 잇는다
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for c in clips:
        cmd += ["-i", c["path"]]
    silent = {}
    for i, c in enumerate(clips):
        if not c["audio"]:                              # 소리가 없는 영상은 그 길이만큼 조용한 소리를 붙인다
            silent[i] = len(clips) + len(silent)
            cmd += ["-f", "lavfi", "-t", f"{c['dur']:.3f}", "-i", f"anullsrc=r={rate}:cl=stereo"]
    parts, total = [], 0.0
    for i, c in enumerate(clips):
        end = ends[i]
        trim = f"trim=end={end:.4f}," if end is not None else ""
        atrim = f"atrim=end={end:.4f}," if end is not None else ""
        parts.append(f"[{i}:v]{trim}setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps:g}[v{i}]")
        src = f"{silent[i]}:a" if i in silent else f"{i}:a"
        parts.append(f"[{src}]{atrim}asetpts=PTS-STARTPTS,aresample={rate},"
                     f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        total += end if end is not None else c["dur"]
    parts.append("".join(f"[v{i}][a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=1[v][a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", out]

    say(f"단계 3/3 MP4 쓰기 0% (이어 붙인 길이 약 {total:.2f}초)")
    shown = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    for line in proc.stdout:
        if line.startswith("out_time_us=") and total > 0:
            try:
                pct = min(99, int(int(line.split("=", 1)[1]) / 1e6 / total * 100))
            except ValueError:
                continue
            if pct >= shown + 10:
                shown = pct - pct % 10
                say(f"단계 3/3 MP4 쓰기 {shown}%")
    if proc.wait() != 0 or not os.path.isfile(out):
        fail("ffmpeg 가 이어 붙인 MP4 를 쓰지 못했습니다. 위 오류 줄을 보세요.")
    q = ["-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if a.thumb:
        subprocess.run([ffmpeg, *q, "-i", out, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", a.thumb], stdin=subprocess.DEVNULL)
    if a.last_frame:                                    # 끝 0.5초를 읽으며 장면마다 덮어써 마지막 장면을 남긴다
        subprocess.run([ffmpeg, *q, "-sseof", "-0.5", "-i", out, "-update", "1", a.last_frame], stdin=subprocess.DEVNULL)
    real = probe(ffprobe, out)
    say(f"끝: 영상 {len(clips)}개를 {real['dur']:.2f}초 영상 하나로, {width}×{height}, "
        f"{os.path.getsize(out) / 1e6:.1f} MB, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
