"""Tone descriptors computed from an LTAS + the raw signal, per
design-contract.md `analyze` and tone-analysis.md sections 1-2.

Implementation notes / deviations from the tone-analysis.md research doc
(the design contract only lists output field names + a few exact formulas;
where it is silent, this module makes the simplest deterministic choice
that still satisfies the documented invariants -- gain scaling
[-3dB-invariance] and resample stability, both exercised by
test_dsp_determinism):
  - crest_db and flatness_4to8k are computed once from the whole
    silence-gated signal / aggregate median spectrum, rather than per-4096-
    sample-frame-then-median-of-medians. Both remain scale-invariant
    (crest is a peak/rms ratio; flatness is geometric/arithmetic mean of
    power, both scale together) so the documented tests still hold, and the
    result is materially simpler.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from gxmimic.dsp import ltas as ltasmod

BANDS_HZ = {
    "low": (80.0, 250.0),
    "low_mid": (250.0, 500.0),
    "mid": (500.0, 2500.0),
    "presence": (2500.0, 5000.0),
    "fizz": (5000.0, 8000.0),
}
BAND_ORDER = ["low", "low_mid", "mid", "presence", "fizz"]

CENTROID_RANGE = (20.0, 16000.0)


def normalize(value: float, lo: float, hi: float, invert: bool = False) -> float:
    if hi == lo:
        return 0.0
    t = (value - lo) / (hi - lo)
    t = min(max(t, 0.0), 1.0)
    return (1.0 - t) if invert else t


def _band_power_sum(freqs, power_spectrum, lo, hi) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    if not np.any(mask):
        return 0.0
    return float(power_spectrum[mask].sum())


def band_powers(freqs: np.ndarray, power_spectrum: np.ndarray) -> dict:
    return {name: _band_power_sum(freqs, power_spectrum, lo, hi) for name, (lo, hi) in BANDS_HZ.items()}


def spectral_centroid(freqs: np.ndarray, power_spectrum: np.ndarray, rng=CENTROID_RANGE) -> float:
    lo, hi = rng
    mask = (freqs >= lo) & (freqs <= hi)
    f, p = freqs[mask], power_spectrum[mask]
    total = p.sum()
    if total <= 0:
        return 0.0
    return float((f * p).sum() / total)


def spectral_rolloff(freqs: np.ndarray, power_spectrum: np.ndarray, pct: float, rng=CENTROID_RANGE) -> float:
    lo, hi = rng
    mask = (freqs >= lo) & (freqs <= hi)
    f, p = freqs[mask], power_spectrum[mask]
    total = p.sum()
    if total <= 0:
        return 0.0
    cum = np.cumsum(p)
    idx = int(np.searchsorted(cum, pct * total))
    idx = min(idx, len(f) - 1)
    return float(f[idx])


def spectral_flatness(freqs: np.ndarray, power_spectrum: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    p = np.maximum(power_spectrum[mask], 1e-20)
    if p.size == 0:
        return 0.0
    gm = float(np.exp(np.mean(np.log(p))))
    am = float(np.mean(p))
    return gm / am if am > 0 else 0.0


def gated_samples(x: np.ndarray, sr: int = 48000) -> np.ndarray:
    """Concatenate the non-silent STFT frames of `x` (per the -40dB/95th-pct
    gate) into one flat sample array, used for crest/zcr so long silence
    padding around probe clips doesn't distort them."""
    freqs, power = ltasmod.compute_power_frames(x, sr)
    keep, _ = ltasmod.gate_mask(power)
    frames = ltasmod.frame_signal(x)
    kept = frames[keep]
    if kept.size == 0:
        return np.asarray(x, dtype=np.float64)
    return kept.reshape(-1)


def crest_factor_db(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
    if rms <= 1e-12:
        return 0.0
    return 20.0 * np.log10(max(peak, 1e-12) / rms)


def zero_crossing_rate(samples: np.ndarray) -> float:
    if samples.size < 2:
        return 0.0
    signs = np.sign(samples)
    signs[signs == 0] = 1.0
    crossings = np.abs(np.diff(signs)) > 0
    return float(np.mean(crossings))


def clipping_ratio(x: np.ndarray, threshold: float = 0.999) -> float:
    if x.size == 0:
        return 0.0
    return float(np.mean(np.abs(x) >= threshold))


def gain_score(crest_db: float, flatness_4to8k: float, zcr: float) -> float:
    n_crest = normalize(crest_db, 3.0, 18.0, invert=True)
    n_flat = normalize(flatness_4to8k, 0.02, 0.45)
    n_zcr = normalize(zcr, 0.02, 0.25)
    return 0.5 * n_crest + 0.3 * n_flat + 0.2 * n_zcr


def gain_class(score: float) -> str:
    if score < 0.25:
        return "clean"
    if score < 0.55:
        return "crunch"
    if score < 0.80:
        return "high_gain"
    return "extreme"


# ---------------------------------------------------------------------------
# Tightness: onset-gated low-band attack/decay ratio.
# ---------------------------------------------------------------------------
def _lowband_filter(x: np.ndarray, sr: int, lo: float = 80.0, hi: float = 250.0) -> np.ndarray:
    nyq = sr / 2.0
    hi = min(hi, nyq * 0.98)
    sos = butter(4, [lo / nyq, hi / nyq], btype="bandpass", output="sos")
    return sosfiltfilt(sos, x)


def _detect_onsets(envelope: np.ndarray, sr: int, hop: int, min_spacing_s: float = 0.15) -> list[int]:
    if envelope.size < 3:
        return []
    baseline = np.median(envelope) + 1e-12
    thresh = 2.0 * baseline
    above = envelope > thresh
    onsets = []
    min_gap_frames = max(1, int(min_spacing_s * sr / hop))
    last = -min_gap_frames
    for i in range(1, len(envelope) - 1):
        if above[i] and envelope[i] >= envelope[i - 1] and envelope[i] >= envelope[i + 1]:
            if i - last >= min_gap_frames:
                onsets.append(i * hop)
                last = i
    return onsets


def tightness(x: np.ndarray, sr: int = 48000) -> tuple[float | None, float]:
    """Returns (tightness_ratio_db_or_None, tightness_score). Score defaults
    to 0.5 (neutral) if no onsets could be detected."""
    try:
        low = _lowband_filter(x, sr)
    except ValueError:
        return None, 0.5
    win = max(1, int(0.01 * sr))
    hop = max(1, int(0.005 * sr))
    energy = low.astype(np.float64) ** 2
    n_frames = max(0, 1 + (len(energy) - win) // hop)
    if n_frames < 3:
        return None, 0.5
    env = np.array([energy[i * hop: i * hop + win].mean() for i in range(n_frames)])
    onset_samples = _detect_onsets(env, sr, hop)
    if not onset_samples:
        return None, 0.5

    early_len = int(0.06 * sr)
    late_end = int(0.25 * sr)
    ratios = []
    for onset in onset_samples:
        early = low[onset: onset + early_len]
        late = low[onset + early_len: onset + late_end]
        e_early = float(np.mean(early.astype(np.float64) ** 2)) if early.size else 0.0
        e_late = float(np.mean(late.astype(np.float64) ** 2)) if late.size else 0.0
        if e_early <= 1e-14 and e_late <= 1e-14:
            continue
        ratio_db = 10.0 * np.log10((e_early + 1e-14) / (e_late + 1e-14))
        ratios.append(ratio_db)
    if not ratios:
        return None, 0.5
    ratio_db = float(np.median(ratios))
    return ratio_db, normalize(ratio_db, -6.0, 12.0)
