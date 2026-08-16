"""Long-Term Average Spectrum (LTAS) computation, per tone-analysis.md
section 3 and design-contract.md `analyze`:

    scipy.signal.stft(nperseg=4096, noverlap=3072) -> per-frame power ->
    gate frames < -40dB rel. 95th-pct frame level -> MEDIAN across kept ->
    10log10.

Two band groupings are exposed:
    - THIRD_OCTAVE_CENTERS: the standard 31-band 1/3-octave series (20Hz-
      20kHz) used for the human-readable `ltas.third_octave` report.
    - BARK_EDGES: the 24-band Bark scale (edges from tone-analysis.md) used
      internally for scoring.

Deviation note: the design contract describes the third-octave grid as "31
ANSI 1/3-oct (40Hz-16k)" -- the standard 31-band series spans 20Hz-20kHz,
not 40Hz-16k; 40Hz-16k under the standard 1/3-octave progression is only 27
bands. We use the full standard 31-band series (this is the count the
contract explicitly asks for) and treat "(40Hz-16k)" as describing the
guitar-relevant portion of it, not literal cutoffs -- the extra low/high
bands simply report near-silent energy for guitar material.
"""
from __future__ import annotations

import numpy as np
from scipy import signal

NPERSEG = 4096
NOVERLAP = 3072
GATE_DB = -40.0

# Standard 31-band third-octave series, 20Hz - 20kHz (ANSI S1.11 preferred numbers).
THIRD_OCTAVE_CENTERS = [
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500,
    630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300,
    8000, 10000, 12500, 16000, 20000,
]
assert len(THIRD_OCTAVE_CENTERS) == 31

# 24 Bark bands (25 edges), from tone-analysis.md section 3.
BARK_EDGES = [
    0, 100, 200, 300, 400, 510, 630, 770, 920, 1080, 1270, 1480, 1720, 2000,
    2320, 2700, 3150, 3700, 4400, 5300, 6400, 7700, 9500, 12000, 15500,
]
assert len(BARK_EDGES) == 25

_THIRD_OCTAVE_FACTOR = 2 ** (1 / 6)  # half-band-width multiplier for a 1/3-octave band


def to_db(power: np.ndarray, eps: float = 1e-20) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(power, eps))


def frame_signal(x: np.ndarray, nperseg: int = NPERSEG, hop: int | None = None) -> np.ndarray:
    """Split `x` into overlapping frames aligned with scipy.signal.stft's
    frame positions (boundary=None, padded=False): shape (n_frames, nperseg)."""
    hop = hop or (nperseg - NOVERLAP)
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n < nperseg:
        x = np.pad(x, (0, nperseg - n))
        n = len(x)
    n_frames = 1 + (n - nperseg) // hop
    idx = np.arange(nperseg)[None, :] + hop * np.arange(n_frames)[:, None]
    return x[idx]


def gate_mask(power: np.ndarray, gate_db: float = GATE_DB) -> tuple[np.ndarray, float]:
    """power: (n_freq, n_frames). Returns (keep_bool_mask, gate_ref_db)."""
    frame_level = power.mean(axis=0)
    frame_db = to_db(frame_level)
    ref = float(np.percentile(frame_db, 95))
    keep = frame_db >= (ref + gate_db)
    if not np.any(keep):
        keep = np.ones_like(keep, dtype=bool)
    return keep, ref


def compute_power_frames(x: np.ndarray, sr: int = 48000,
                          nperseg: int = NPERSEG, noverlap: int = NOVERLAP) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs, power) where power has shape (n_freq, n_frames)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < nperseg:
        x = np.pad(x, (0, nperseg - len(x)))
    f, _, Z = signal.stft(x, fs=sr, nperseg=nperseg, noverlap=noverlap, window="hann", boundary=None, padded=False)
    power = np.abs(Z) ** 2  # (n_freq, n_frames)
    if power.shape[1] == 0:
        raise ValueError("no STFT frames produced (signal too short)")
    return f, power


def compute_median_power_spectrum(x: np.ndarray, sr: int = 48000,
                                   nperseg: int = NPERSEG, noverlap: int = NOVERLAP,
                                   gate_db: float = GATE_DB) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return (freqs, median_power_spectrum, meta). meta reports how many
    frames were gated out (useful for a `warnings` entry upstream)."""
    f, power = compute_power_frames(x, sr, nperseg, noverlap)
    keep, ref = gate_mask(power, gate_db)
    median_power = np.median(power[:, keep], axis=1)
    meta = {"n_frames": int(power.shape[1]), "n_kept": int(keep.sum()), "gate_ref_db": ref}
    return f, median_power, meta


def _band_power(freqs: np.ndarray, power_spectrum: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    if not np.any(mask):
        idx = int(np.argmin(np.abs(freqs - (lo + hi) / 2.0)))
        return float(power_spectrum[idx])
    return float(power_spectrum[mask].sum())


def third_octave_bands(freqs: np.ndarray, power_spectrum: np.ndarray,
                        centers: list[float] = THIRD_OCTAVE_CENTERS) -> np.ndarray:
    return np.array([
        _band_power(freqs, power_spectrum, fc / _THIRD_OCTAVE_FACTOR, fc * _THIRD_OCTAVE_FACTOR)
        for fc in centers
    ])


def bark_bands(freqs: np.ndarray, power_spectrum: np.ndarray,
                edges: list[float] = BARK_EDGES) -> np.ndarray:
    return np.array([
        _band_power(freqs, power_spectrum, edges[i], edges[i + 1])
        for i in range(len(edges) - 1)
    ])


def bark_centers(edges: list[float] = BARK_EDGES) -> list[float]:
    return [(edges[i] + edges[i + 1]) / 2.0 for i in range(len(edges) - 1)]
