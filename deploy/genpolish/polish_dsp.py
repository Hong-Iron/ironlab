"""소리 다듬기의 계산 부분. numpy 와 scipy 만 쓴다 (모델도 ffmpeg 도 부르지 않는다). polish_run.py 가 부른다.

소리는 늘 (표본 수, 채널 수) 꼴의 float32 배열이고, 크기는 -1~1 이 0 dBFS(디지털로 낼 수 있는 가장 큰 소리)다.
여기 있는 처리는 믹싱 엔지니어가 AI 목소리를 다듬는 순서를 따른다.

    낮은 웅웅거림 자르기 → 거슬리는 울림 깎기 → 치찰음 줄이기 → 압축 두 번 (+ 세게 누른 사본 섞기)
    → 따뜻하게 → 위쪽 광택 → 잔향·메아리는 따로 만들어 섞기

사람이 귀로 하던 일(거슬리는 울림 찾기, 압축 문턱 정하기, 치찰음이 센 곳 찾기)은 소리를 재서 정한다.
무엇을 쟀고 무엇을 정했는지는 처리마다 돌려주는 dict 에 담아, 결과 보고(report.json)에 남긴다.
"""

from __future__ import annotations

import math
import re

import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

EPS = 1e-12
BLOCK_S = 0.001                 # 압축기·치찰음 줄이기가 소리 크기를 재는 칸 (1 ms)
STAT_S = 0.010                  # 문턱을 정할 때 소리 크기의 분포를 보는 칸 (10 ms)


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), EPS))


def undb(d):
    return 10.0 ** (np.asarray(d, dtype=np.float64) / 20.0)


def as2d(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x[:, None] if x.ndim == 1 else x


# ── 필터 (RBJ Audio EQ Cookbook 의 2차 필터) ──

def biquad(kind: str, f: float, sr: int, q: float = 0.707, gain_db: float = 0.0) -> np.ndarray:
    """2차 필터 하나를 scipy 의 sos 꼴(1×6)로 돌려준다. kind: highpass, lowpass, bandpass, peak, highshelf, lowshelf."""
    f = min(max(f, 5.0), sr * 0.49)
    w0 = 2 * math.pi * f / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    a = 10 ** (gain_db / 40)
    if kind == "highpass":
        b0, b1, b2 = (1 + cw) / 2, -(1 + cw), (1 + cw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    elif kind == "lowpass":
        b0, b1, b2 = (1 - cw) / 2, 1 - cw, (1 - cw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    elif kind == "bandpass":                        # 가운데에서 0 dB
        b0, b1, b2 = alpha, 0.0, -alpha
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    elif kind == "peak":
        b0, b1, b2 = 1 + alpha * a, -2 * cw, 1 - alpha * a
        a0, a1, a2 = 1 + alpha / a, -2 * cw, 1 - alpha / a
    elif kind in ("highshelf", "lowshelf"):
        s = 1 if kind == "highshelf" else -1
        sq = 2 * math.sqrt(a) * alpha
        b0 = a * ((a + 1) + s * (a - 1) * cw + sq)
        b1 = -2 * s * a * ((a - 1) + s * (a + 1) * cw)
        b2 = a * ((a + 1) + s * (a - 1) * cw - sq)
        a0 = (a + 1) - s * (a - 1) * cw + sq
        a1 = 2 * s * ((a - 1) - s * (a + 1) * cw)
        a2 = (a + 1) - s * (a - 1) * cw - sq
    else:
        raise ValueError(f"모르는 필터 종류입니다 (받은 값 {kind!r})")
    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


def filt(x: np.ndarray, sos: np.ndarray, zero_phase: bool = False) -> np.ndarray:
    if zero_phase:
        return signal.sosfiltfilt(sos, x, axis=0).astype(np.float32)
    return signal.sosfilt(sos, x, axis=0).astype(np.float32)


def highpass(x, f, sr, order=2):
    if f <= 0:
        return x
    sos = signal.butter(order, f, "highpass", fs=sr, output="sos")
    return filt(x, sos)


def lowpass(x, f, sr, order=2):
    if f <= 0 or f >= sr * 0.49:
        return x
    sos = signal.butter(order, f, "lowpass", fs=sr, output="sos")
    return filt(x, sos)


def band_split(x, lo, hi, sr):
    """x 를 [lo, hi] 띠와 나머지로 나눈다. 위상이 돌지 않는 필터라 두 쪽을 더하면 x 가 그대로 돌아온다."""
    sos = signal.butter(2, [lo, min(hi, sr * 0.49)], "bandpass", fs=sr, output="sos")
    band = filt(x, sos, zero_phase=True)
    return band, (x - band).astype(np.float32)


# ── 크기 재기 ──

def block_level_db(x: np.ndarray, sr: int, block_s: float = BLOCK_S) -> np.ndarray:
    """칸마다(기본 1 ms) 채널을 묶은 RMS 크기 (dBFS)."""
    x = as2d(x)
    n = max(1, round(sr * block_s))
    m = len(x) // n
    if m == 0:
        return np.array([float(db(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + EPS))])
    sq = (x[: m * n].astype(np.float64) ** 2).mean(axis=1).reshape(m, n).mean(axis=1)
    if len(x) > m * n:
        sq = np.append(sq, (x[m * n:].astype(np.float64) ** 2).mean())
    return 10 * np.log10(sq + EPS)


def active_levels(x: np.ndarray, sr: int, floor_db: float = -60.0, below_top: float = 45.0) -> np.ndarray:
    """10 ms 칸의 크기 가운데 소리가 나는 칸만 (가장 큰 칸보다 45 dB 넘게 작거나 -60 dBFS 아래인 칸은 쉬는 곳으로 본다)."""
    lv = block_level_db(x, sr, STAT_S)
    if lv.size == 0:
        return lv
    keep = lv[(lv > floor_db) & (lv > lv.max() - below_top)]
    return keep if keep.size else lv


def active_rms_db(x: np.ndarray, sr: int) -> float:
    lv = active_levels(x, sr)
    return float(10 * np.log10(np.mean(10 ** (lv / 10)) + EPS))


def true_peak_db(x: np.ndarray, sr: int, chunk_s: float = 10.0) -> float:
    """4배로 촘촘히 다시 뽑아 잰 가장 큰 곳 (dBTP). 표본 사이에서 튀는 봉우리까지 잡는다. 메모리를 아끼려 10초씩 잰다."""
    x = as2d(x)
    if len(x) == 0:
        return -120.0
    n, pad, top = int(sr * chunk_s), 64, 0.0
    for s in range(0, len(x), n):
        part = x[max(0, s - pad): s + n + pad]
        up = signal.resample_poly(part, 4, 1, axis=0)
        top = max(top, float(np.abs(up).max()))
    return float(db(top))


def oversampled_peaks(x: np.ndarray, sr: int, chunk_s: float = 10.0) -> np.ndarray:
    """표본마다, 그 표본과 다음 표본 사이를 4배로 촘촘히 뽑아 본 가장 큰 크기 (채널을 묶어). 제한기가 표본 사이 봉우리까지 보게 한다."""
    x = as2d(x)
    out = np.empty(len(x), np.float32)
    n, pad = int(sr * chunk_s), 64
    for s in range(0, len(x), n):
        lo = max(0, s - pad)
        part = x[lo: s + n + pad]
        up = np.abs(signal.resample_poly(part, 4, 1, axis=0)).max(axis=1)
        m = len(part)
        up = np.pad(up, (0, max(0, 4 * m - len(up))))[: 4 * m].reshape(m, 4).max(axis=1)
        k = min(n, len(x) - s)
        out[s: s + k] = np.maximum(up[s - lo: s - lo + k], np.abs(x[s: s + k]).max(axis=1))
    return out


def loudness_lufs(x: np.ndarray, sr: int) -> float | None:
    """ITU-R BS.1770 통합 음량 (LUFS). 사람 귀가 듣는 크기에 맞춘 값이다. 0.4초보다 짧거나 아주 조용하면 None."""
    x = as2d(x)
    if len(x) < int(0.4 * sr):
        return None
    import pyloudnorm
    v = pyloudnorm.Meter(sr).integrated_loudness(x.astype(np.float64))
    return float(v) if np.isfinite(v) else None


def band_share_db(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """[lo, hi] 띠가 전체 에너지에서 차지하는 몫 (dB). 치찰음 띠(5~9 kHz)가 줄었는지 볼 때 쓴다."""
    x = as2d(x).mean(axis=1)
    if len(x) < 2048:
        return -120.0
    f, p = signal.welch(x, sr, nperseg=4096)
    part = p[(f >= lo) & (f <= hi)].sum()
    return float(10 * np.log10(part / (p.sum() + EPS) + EPS))


# ── 거슬리는 울림 찾기 (사람이 좁은 종 모양 필터를 쓸어 찾던 일) ──

def find_resonances(x: np.ndarray, sr: int, bands=((2000.0, 4000.0), (6000.0, 9000.0)),
                    max_cut_db: float = 4.0, min_prominence_db: float = 2.0) -> list[dict]:
    """띠마다 주변보다 튀어나온 봉우리 하나를 찾는다.

    긴 시간 평균 스펙트럼을 1/3 옥타브 폭으로 고르게 편 것과 견줘, 봉우리가 min_prominence_db 넘게 튀어나왔으면
    그만큼(많아야 max_cut_db) 깎을 곳으로 고른다. 튀어나온 곳이 없으면 그 띠는 건드리지 않는다.
    """
    mono = as2d(x).mean(axis=1)
    if len(mono) < 8192:
        return []
    f, p = signal.welch(mono, sr, nperseg=8192)
    lp = 10 * np.log10(p + EPS)
    smooth = np.empty_like(lp)
    for i, fi in enumerate(f):                      # 1/3 옥타브 폭의 평균
        lo, hi = fi * 2 ** (-1 / 6), fi * 2 ** (1 / 6)
        sel = (f >= lo) & (f <= hi)
        smooth[i] = 10 * np.log10(np.mean(p[sel]) + EPS) if sel.any() else lp[i]
    found = []
    for lo, hi in bands:
        sel = np.where((f >= lo) & (f <= min(hi, sr * 0.45)))[0]
        if sel.size == 0:
            continue
        prom = lp[sel] - smooth[sel]
        i = int(np.argmax(prom))
        if prom[i] >= min_prominence_db:
            found.append({"hz": round(float(f[sel[i]]), 1), "prominence_db": round(float(prom[i]), 2),
                          "cut_db": round(float(min(max_cut_db, prom[i])), 2)})
    return found


def eq_cuts(x: np.ndarray, sr: int, cuts: list[dict], q: float = 4.0) -> np.ndarray:
    for c in cuts:
        x = filt(x, biquad("peak", c["hz"], sr, q=q, gain_db=-abs(c["cut_db"])))
    return x


# ── 치찰음 줄이기 ('ㅅ·ㅊ·ㅆ' 처럼 쉿 하는 소리만 잠깐 줄인다) ──

def find_sibilance_band(x: np.ndarray, sr: int) -> tuple[float, float]:
    """치찰음이 가장 센 곳을 찾아 그 둘레 2 kHz 를 돌려준다 (밝은 목소리는 6~8 kHz, 어두운 목소리는 5~7 kHz 쯤에 나온다).

    5~9 kHz 띠가 전체의 몫에서 가장 큰 칸(상위 5%)만 모아 평균 스펙트럼을 보고, 4.5~9 kHz 에서 가장 센 주파수를 고른다.
    """
    mono = as2d(x).mean(axis=1)
    hop = int(sr * 0.02)
    if len(mono) < hop * 20 or sr < 20000:
        return 6000.0, 8000.0
    f, _, z = signal.stft(mono, sr, nperseg=1024, noverlap=1024 - hop)
    pw = np.abs(z) ** 2
    hi = pw[(f >= 5000) & (f <= 9000)].sum(axis=0)
    share = hi / (pw.sum(axis=0) + EPS)
    loud = pw.sum(axis=0) > np.percentile(pw.sum(axis=0), 30)
    if not loud.any():
        return 6000.0, 8000.0
    cut = np.percentile(share[loud], 95)
    pick = loud & (share >= cut)
    spec = pw[:, pick].mean(axis=1)
    sel = (f >= 4500) & (f <= 9000)
    peak = float(f[sel][np.argmax(spec[sel])])
    centre = min(max(peak, 6000.0), 7500.0)         # 띠가 5~8.5 kHz 안에 들게
    return centre - 1000.0, centre + 1000.0


def _smooth_gain_db(target: np.ndarray, attack: float, release: float) -> np.ndarray:
    """칸마다의 목표 감쇠(dB, 0 이하)를 attack(줄일 때)·release(되돌릴 때) 계수로 부드럽게 잇는다."""
    out = np.empty_like(target)
    g = 0.0
    for i, t in enumerate(target):
        g = g + (t - g) * (attack if t < g else release)
        out[i] = g
    return out


def _coef(time_s: float, step_s: float = BLOCK_S) -> float:
    return 1.0 - math.exp(-step_s / max(time_s, step_s))


def _to_samples(gain_db_blocks: np.ndarray, n: int, sr: int) -> np.ndarray:
    step = sr * BLOCK_S
    centres = (np.arange(len(gain_db_blocks)) + 0.5) * step
    return undb(np.interp(np.arange(n), centres, gain_db_blocks)).astype(np.float32)


DEESS_PERCENTILE = 90.0         # 소리 나는 칸 가운데 치찰음 띠의 몫이 가장 큰 10% 만 줄인다
DEESS_THRESHOLD_RANGE = (-12.0, -6.0)   # 그렇게 정한 문턱을 이 사이에 둔다 (치찰음이 거의 없는 목소리에서 모음을 깎지 않게)


SLOW_S = 0.300                  # '둘레의 노래 크기'를 잴 때 고르게 펴는 폭 (0.3초)


def sibilance_levels(x: np.ndarray, sr: int, band: tuple[float, float], against: str = "instant"):
    """1 ms 칸마다 치찰음 띠의 크기를 무엇에 견줘 잰 값 (dB), 소리 나는 칸 표시, 띠, 나머지.

    against="instant": 그 순간의 전체 소리에 견준다. 모음 위에 얹힌 치찰음을 잘 잡는다.
    against="slow":    둘레 0.3초의 노래 크기에 견준다. 모음 사이에 홀로 나는 'ㅅ' 은 그 순간 전체가 곧 치찰음이라
                       순간 비교로는 얼마나 큰지 모른다. 둘레의 노래와 견주면 노래보다 얼마나 튀는지 안다.
    """
    b, rest = band_split(x, band[0], band[1], sr)
    full = block_level_db(x, sr)
    lv = block_level_db(b, sr)
    if against == "slow":
        k = max(1, int(SLOW_S / BLOCK_S))
        ref = 10 * np.log10(uniform_filter1d(10 ** (full / 10), k) + EPS)
    else:
        ref = full
    voiced = full > active_rms_db(x, sr) - 30
    return lv - ref, voiced, b, rest


def burst_level(x: np.ndarray, sr: int, band: tuple[float, float], percentile: float = 95.0) -> float:
    """치찰음이 둘레의 노래보다 얼마나 튀는지: 소리 나는 칸 가운데 상위 5% 의 값 (dB). 압축 앞에서 재 두고 뒤에서 되돌릴 때 쓴다."""
    rel, voiced, _, _ = sibilance_levels(as2d(x), sr, band, "slow")
    return float(np.percentile(rel[voiced], percentile)) if voiced.any() else 0.0


def deess(x: np.ndarray, sr: int, max_db: float, band: tuple[float, float] | None = None,
          ratio: float = 4.0, threshold_db: float | None = None, against: str = "instant") -> tuple[np.ndarray, dict]:
    """치찰음 띠가 견주는 소리(그 순간의 전체 소리, 또는 둘레의 노래)보다 커지는 순간에만 그 띠를 줄인다 (띠 나누기 방식).

    평소 모음에서 치찰음 띠는 전체보다 15~25 dB 작다. 'ㅅ' 소리에서는 그 차이가 몇 dB 로 좁혀진다.
    그 차이가 문턱보다 좁아진 만큼 ratio 로 줄이고, 많아야 max_db 까지 줄인다.
    문턱을 주지 않으면 이 목소리에서 잰다: 소리 나는 1 ms 칸 가운데 그 차이가 가장 좁은 10% 가 걸리는 값 (−12~−6 dB 사이로).
    """
    x = as2d(x)
    if max_db <= 0:
        return x, {"max_db": 0}
    lo, hi = band or find_sibilance_band(x, sr)
    rel, voiced, b, rest = sibilance_levels(x, sr, (lo, hi), against)
    if threshold_db is None:
        thr = float(np.percentile(rel[voiced], DEESS_PERCENTILE)) if voiced.any() else DEESS_THRESHOLD_RANGE[0]
        threshold_db = min(max(thr, DEESS_THRESHOLD_RANGE[0]), DEESS_THRESHOLD_RANGE[1])
    target = -np.clip((rel - threshold_db) * (1 - 1 / ratio), 0, max_db)
    g = _smooth_gain_db(target, _coef(0.001), _coef(0.060))
    x = (rest + b * _to_samples(g, len(x), sr)[:, None]).astype(np.float32)
    on = g < -0.5
    return x, {"band_hz": [round(lo), round(hi)], "max_db": max_db, "against": against, "threshold_db": round(threshold_db, 2),
               "active_share": round(float(np.mean(on[voiced])) if voiced.any() else 0.0, 4),
               "deepest_db": round(float(-g.min()), 2)}


# ── 압축 (큰 소리와 작은 소리의 차이를 줄인다) ──

def compress(x: np.ndarray, sr: int, threshold_db: float, ratio: float, attack_s: float, release_s: float,
             knee_db: float = 6.0, key: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """문턱을 넘은 만큼을 ratio 로 나눈다 (ratio 4 면 문턱을 4 dB 넘은 소리가 1 dB 만 넘게). 채널을 묶어 같은 만큼 줄인다.
    key 를 주면 그 소리의 크기를 보고 x 를 줄인다. 돌려주는 두 번째 값은 칸마다 줄인 양 (dB, 0 이하)."""
    x = as2d(x)
    lv = block_level_db(x if key is None else as2d(key), sr)
    over = lv - threshold_db
    gr = np.where(over <= -knee_db / 2, 0.0,
                  np.where(over >= knee_db / 2, (1 / ratio - 1) * over,
                           (1 / ratio - 1) * (over + knee_db / 2) ** 2 / (2 * knee_db)))
    g = _smooth_gain_db(gr, _coef(attack_s), _coef(release_s))
    return (x * _to_samples(g, len(x), sr)[:, None]).astype(np.float32), g


def threshold_for(x: np.ndarray, sr: int, target_gr_db: float, ratio: float, percentile: float = 90.0) -> float:
    """소리가 나는 칸 가운데 큰 쪽(상위 10%) 크기에서 target_gr_db 만큼 줄도록 문턱을 정한다.
    ratio r 이면 문턱을 o dB 넘은 소리가 o·(1−1/r) dB 줄므로, 문턱 = 그 크기 − target·r/(r−1)."""
    lv = active_levels(x, sr)
    ref = float(np.percentile(lv, percentile))
    return ref - target_gr_db * ratio / (ratio - 1)


def serial_compress(x, sr, target_db: float) -> tuple[np.ndarray, dict]:
    """압축기 두 대를 잇는다. 첫째(빠름)는 튀는 봉우리를, 둘째(느림)는 전체 흐름을 잡는다. 둘 다 큰 쪽에서 target_db 쯤 줄인다."""
    if target_db <= 0:
        return x, {"target_db": 0}
    report = {"target_db": target_db, "stages": []}
    for name, ratio, att, rel in (("봉우리", 4.0, 0.003, 0.060), ("흐름", 2.0, 0.020, 0.250)):
        thr = threshold_for(x, sr, target_db, ratio)
        x, g = compress(x, sr, thr, ratio, att, rel)
        on = g < -0.1
        report["stages"].append({"name": name, "ratio": ratio, "threshold_db": round(thr, 2),
                                 "mean_gr_db": round(float(-g[on].mean()) if on.any() else 0.0, 2),
                                 "p95_gr_db": round(float(-np.percentile(g, 5)), 2)})
    return x, report


def parallel_compress(x, sr, amount: float) -> tuple[np.ndarray, dict]:
    """세게 누른 사본을 원래 소리 밑에 조금 섞는다. 작은 소리(숨, 끝음)가 살아나 힘이 붙고, 큰 소리의 모양은 그대로 남는다."""
    if amount <= 0:
        return x, {"amount": 0}
    thr = threshold_for(x, sr, 10.0, 8.0, percentile=50.0)
    heavy, _ = compress(x, sr, thr, 8.0, 0.001, 0.100)
    heavy = heavy * undb(active_rms_db(x, sr) - active_rms_db(heavy, sr))   # 같은 크기로 맞춘 뒤 섞는다
    return (x + amount * heavy).astype(np.float32) / (1 + amount), {"amount": amount, "threshold_db": round(thr, 2)}


# ── 따뜻하게 (새추레이션) ──

def saturate(x, sr, amount: float, style: str = "tape", band: str = "lowmid") -> tuple[np.ndarray, dict]:
    """소리를 둥글게 눌러 배음을 더한다. tape 은 위아래가 같은 곡선(홀수 배음)에 높은 소리를 살짝 누르고,
    tube 는 위아래가 다른 곡선(짝수 배음)으로 더 두툼하게 만든다. band=lowmid 면 200~800 Hz 만 데우고 높은 소리는 깨끗이 둔다."""
    if amount <= 0:
        return x, {"amount": 0}
    x = as2d(x)
    if band == "lowmid":
        part, rest = band_split(x, 200.0, 800.0, sr)
    else:
        part, rest = x, np.zeros_like(x)
    ref = max(float(undb(active_rms_db(part, sr) + 12)), 1e-4)      # 그 띠의 큰 소리가 곡선의 무릎에 닿게
    drive = 1.0 + 4.0 * amount
    u = part / ref * drive
    if style == "tube":
        bias = 0.25
        y = np.tanh(u + bias) - np.tanh(bias)
    else:
        y = np.tanh(u)
    y = (y * ref / drive).astype(np.float32)
    y = highpass(y, 10.0, sr)                       # 비대칭 곡선이 만든 직류를 걷는다
    if style == "tape":
        y = lowpass(y, 16000.0, sr, order=1)
    wet = (part * (1 - amount) + y * amount).astype(np.float32)
    return (rest + wet).astype(np.float32), {"amount": amount, "style": style, "band": band}


def air(x, sr, gain_db: float, f: float = 10000.0) -> np.ndarray:
    """위쪽 광택: 10 kHz 위를 살짝 올린다. 많이 올리면 잡음까지 커지므로 1~2 dB 만."""
    if gain_db <= 0:
        return x
    return filt(x, biquad("highshelf", f, sr, q=0.707, gain_db=gain_db))


# ── 잔향·메아리 (원래 소리와 따로 만들어 섞는다) ──

def reverb_ir(sr: int, seconds: float, channels: int = 2, seed: int = 7) -> np.ndarray:
    """잔향의 모양 (임펄스 응답). 고르게 퍼진 잡음이 seconds 동안 60 dB 줄어든다. 채널마다 다른 잡음이라 좌우로 넓게 들린다."""
    n = max(16, int(sr * seconds * 1.2))
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    env = np.exp(-6.9078 * t / max(seconds, 0.05))
    ir = rng.standard_normal((n, channels)).astype(np.float32) * env[:, None].astype(np.float32)
    fade = min(n, int(sr * 0.005))
    ir[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
    return ir / np.sqrt((ir ** 2).sum(axis=0, keepdims=True))


def send_reverb(x, sr, amount: float, seconds: float = 1.6, predelay_ms: float = 30.0,
                lo: float = 300.0, hi: float = 8000.0) -> tuple[np.ndarray, dict]:
    """잔향만 만든다 (원래 소리는 들어 있지 않다). amount 는 원래 소리 크기에 견준 몫이다 (0.12 면 약 −18 dB).
    잔향에서 300 Hz 아래와 8 kHz 위를 걷어 목소리를 흐리지 않게 하고, 20~40 ms 늦게 시작해 말이 먼저 또렷이 들리게 한다."""
    x = as2d(x)
    if amount <= 0:
        return np.zeros((len(x), 2), np.float32), {"amount": 0}
    mono = x.mean(axis=1, keepdims=True)
    send = lowpass(highpass(mono, lo, sr), hi, sr)
    d = int(sr * predelay_ms / 1000)
    ir = reverb_ir(sr, seconds)
    wet = np.stack([signal.fftconvolve(send[:, 0], ir[:, c])[: len(x)] for c in range(2)], axis=1).astype(np.float32)
    if d:
        wet = np.concatenate([np.zeros((d, 2), np.float32), wet[:-d]])
    wet *= undb(active_rms_db(mono, sr) - active_rms_db(wet, sr) + db(amount))
    return wet.astype(np.float32), {"amount": amount, "seconds": seconds, "predelay_ms": predelay_ms, "band_hz": [lo, hi]}


NOTES = {"1/4": 1.0, "1/8": 0.5, "1/8.": 0.75, "1/16": 0.25}


def send_delay(x, sr, amount: float, bpm: float | None, note: str = "1/8", feedback: float = 0.35,
               repeats: int = 4) -> tuple[np.ndarray, dict]:
    """곡 빠르기에 맞춘 메아리만 만든다. 되울림이 왼쪽·오른쪽을 번갈아 오가며 점점 작아진다. 빠르기를 모르면 만들지 않는다."""
    x = as2d(x)
    if amount <= 0 or not bpm:
        return np.zeros((len(x), 2), np.float32), {"amount": max(0.0, amount), "skipped": None if bpm else "빠르기를 모름"}
    step = 60.0 / bpm * NOTES.get(note, 0.5)
    n = int(sr * step * repeats) + 1
    ir = np.zeros((n, 2), np.float32)
    for k in range(1, repeats + 1):
        i = min(n - 1, round(sr * step * k))
        pan = 0.3 if k % 2 else -0.3                   # 처음은 오른쪽으로 조금, 다음은 왼쪽으로 조금
        g = feedback ** (k - 1)
        ir[i, 0] = g * math.sqrt(0.5 - pan / 2)
        ir[i, 1] = g * math.sqrt(0.5 + pan / 2)
    mono = x.mean(axis=1)
    send = lowpass(highpass(mono, 300.0, sr), 5000.0, sr)
    wet = np.stack([signal.fftconvolve(send, ir[:, c])[: len(x)] for c in range(2)], axis=1).astype(np.float32)
    wet *= undb(active_rms_db(mono, sr) - active_rms_db(wet, sr) + db(amount))
    return wet.astype(np.float32), {"amount": amount, "bpm": round(bpm, 2), "note": note, "seconds": round(step, 4)}


def tempo_from_abc(abc: str) -> float | None:
    """ABC 악보의 빠르기 줄(Q:)에서 1분에 4분음표 몇 개인지 읽는다. 예) Q:1/4=92 → 92, Q:3/8=60 → 90. 없으면 None."""
    for line in (abc or "").splitlines():
        m = re.match(r'\s*Q:\s*(?:"[^"]*"\s*)?(?:(\d+)\s*/\s*(\d+)\s*=\s*)?(\d+(?:\.\d+)?)', line)
        if m:
            value = float(m.group(3))
            if m.group(1):
                value *= int(m.group(1)) * 4 / int(m.group(2))
            return value if 20 <= value <= 400 else None
    return None


# ── 다시 섞기 ──

def duck(backing, vocal, sr, depth_db: float) -> tuple[np.ndarray, dict]:
    """목소리가 나오는 동안만 반주를 depth_db 쯤 낮춘다 (목소리가 쉴 때는 그대로)."""
    backing = as2d(backing)
    if depth_db <= 0:
        return backing, {"depth_db": 0}
    lv = block_level_db(vocal, sr, 0.010)
    act = active_levels(vocal, sr)
    ref = float(np.percentile(act, 50))
    present = np.clip((lv - (ref - 15)) / 10, 0, 1)            # 목소리 크기의 가운데보다 15 dB 작으면 0, 5 dB 작으면 1
    target = np.repeat(-depth_db * present, 10)                # 10 ms 칸을 1 ms 칸으로
    g = _smooth_gain_db(target, _coef(0.030), _coef(0.300))
    return (backing * _to_samples(g, len(backing), sr)[:, None]).astype(np.float32), {
        "depth_db": depth_db, "share": round(float(np.mean(present > 0.5)), 3)}


def carve(backing, sr, gain_db: float, f: float = 3000.0) -> np.ndarray:
    """반주에서 목소리가 또렷이 들리는 자리(3 kHz 둘레)를 조금 비운다."""
    if gain_db <= 0:
        return backing
    return filt(backing, biquad("peak", f, sr, q=1.0, gain_db=-gain_db))


# ── 마무리 ──

def limit(x: np.ndarray, sr: int, ceiling_db: float = -1.0, lookahead_s: float = 0.005,
          release_s: float = 0.080) -> tuple[np.ndarray, dict]:
    """미리 보고 줄이는 제한기: 가장 큰 곳이 ceiling_db 를 넘지 않게, 넘을 곳 직전부터 부드럽게 줄였다가 천천히 되돌린다.

    소리를 lookahead 만큼 늦춰 내보내고, 그 사이에 올 봉우리를 보고 미리 줄인다. 마지막에 표본 사이의 봉우리까지 재서
    아직 넘으면 전체를 그만큼 낮춘다.
    """
    x = as2d(x)
    ceil = float(undb(ceiling_db - 0.1))
    L = max(1, int(sr * lookahead_s))
    peak = oversampled_peaks(x, sr)
    need = np.minimum(1.0, ceil / np.maximum(peak, EPS))
    g1 = minimum_filter1d(need, L + 1, origin=L // 2)     # [n−L, n] 의 가장 작은 값
    g2 = uniform_filter1d(g1, L + 1, origin=L // 2)       # 같은 폭으로 고르게 편다 (봉우리에서 need 를 넘지 않는다)
    # 천천히 되돌리기: 32 표본 칸마다 되돌림 속도를 제한한다
    B = 32
    m = -(-len(g2) // B)
    blocks = np.pad(g2, (0, m * B - len(g2)), constant_values=1.0).reshape(m, B).min(axis=1)
    a = 1.0 - math.exp(-B / (sr * release_s))
    rel = np.empty_like(blocks)
    g = 1.0
    for i, t in enumerate(blocks):
        g = t if t < g else g + (t - g) * a
        rel[i] = g
    g3 = np.minimum(g2, np.repeat(rel, B)[: len(g2)])
    y = np.concatenate([np.zeros((L, x.shape[1]), np.float32), x[:-L]]) if len(x) > L else x.copy()
    y = (y * g3[:, None]).astype(np.float32)
    y = np.concatenate([y[L:], np.zeros((L, x.shape[1]), np.float32)])   # 늦춘 만큼 앞으로 당겨 길이와 자리를 맞춘다
    tp = true_peak_db(y, sr)
    trim = 0.0
    if tp > ceiling_db:
        trim = ceiling_db - tp
        y = (y * undb(trim)).astype(np.float32)
    return y, {"ceiling_db": ceiling_db, "deepest_db": round(float(-db(g3.min())), 2),
               "active_share": round(float(np.mean(g3 < undb(-0.5))), 4), "trim_db": round(trim, 2)}


MAX_GAIN_DB = 30.0              # 음량을 맞출 때 올리는 한도
NEAR_SILENT_LUFS = -50.0        # 이보다 작으면 거의 소리가 없는 파일로 본다
NEAR_SILENT_GAIN_DB = 20.0      # 그런 파일은 이만큼까지만 올린다. 더 키우면 잡음만 커진다


def to_loudness(x: np.ndarray, sr: int, target_lufs: float, ceiling_db: float) -> tuple[np.ndarray, dict]:
    """음량을 target_lufs 로 맞추고 제한기로 가장 큰 곳을 ceiling_db 아래로 둔다. 제한기 때문에 음량이 조금 줄면 한 번 더 맞춘다.
    올리는 양은 모두 합해 30 dB 까지, 거의 소리가 없는 파일(−50 LUFS 아래)은 20 dB 까지다. 한도에서 멈추면 capped 를 참으로 둔다."""
    before = loudness_lufs(x, sr)
    max_gain_db = NEAR_SILENT_GAIN_DB if before is not None and before < NEAR_SILENT_LUFS else MAX_GAIN_DB
    if before is None:                              # 너무 짧은 소리: 가장 큰 곳만 맞춘다
        top = true_peak_db(x, sr)
        gain = min(MAX_GAIN_DB, ceiling_db - top) if top > -100 else 0.0
        return (as2d(x) * undb(gain)).astype(np.float32), {
            "lufs_before": None, "lufs_after": None, "gain_db": round(gain, 2), "capped": False, "short": True}
    y, info, capped = x, {}, False
    gain_total = 0.0
    for _ in range(2):
        cur = loudness_lufs(y, sr)
        gain = max(-30.0, min(max_gain_db - gain_total, target_lufs - cur))
        capped = capped or (gain_total + gain >= max_gain_db - 1e-6 and target_lufs - cur > gain)
        gain_total += gain
        y, info = limit((as2d(y) * undb(gain)).astype(np.float32), sr, ceiling_db)
        if capped or abs(loudness_lufs(y, sr) - target_lufs) < 0.5:
            break
    after = loudness_lufs(y, sr)
    return y, {"lufs_before": round(before, 2), "lufs_after": round(after, 2) if after is not None else None,
               "gain_db": round(gain_total, 2), "max_gain_db": max_gain_db, "capped": capped, "limiter": info}


def fades(x: np.ndarray, sr: int, fade_in_ms: float, fade_out_ms: float) -> np.ndarray:
    x = as2d(x).copy()
    for ms, rising in ((fade_in_ms, True), (fade_out_ms, False)):
        n = min(len(x), int(sr * ms / 1000))
        if n > 1:
            ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
            if rising:
                x[:n] *= ramp
            else:
                x[-n:] *= ramp[::-1]
    return x


def measure(x: np.ndarray, sr: int, vocal_band: bool = True) -> dict:
    """보고에 남길 값: 음량(LUFS), 가장 큰 곳(dBTP), 소리 나는 곳의 크기(dBFS), 치찰음 띠(5~9 kHz)와 거슬리는 띠(2~4 kHz)의 몫,
    치찰음이 둘레 0.3초의 소리보다 튀는 정도(상위 5%). 귀에 '쉿' 하고 거슬리는 정도에 가까운 값은 마지막 것이다."""
    out = {"lufs": loudness_lufs(x, sr), "true_peak_db": round(true_peak_db(x, sr), 2),
           "active_rms_db": round(active_rms_db(x, sr), 2), "seconds": round(len(x) / sr, 3)}
    if out["lufs"] is not None:
        out["lufs"] = round(out["lufs"], 2)
    if vocal_band and sr > 20000 and len(x) > sr * 0.5:
        out["sibilance_share_db"] = round(band_share_db(x, sr, 5000, 9000), 2)
        out["harsh_share_db"] = round(band_share_db(x, sr, 2000, 4000), 2)
        out["sibilance_burst_db"] = round(burst_level(x, sr, (5000.0, 9000.0)), 2)
    return out
