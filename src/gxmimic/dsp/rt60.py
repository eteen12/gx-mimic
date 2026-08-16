"""Blind RT60 estimation via backward Schroeder integration (Ratnam 2003),
per tone-analysis.md section 1 ("Reverb" row). Used on the `chord` clip's
ring-out tail to estimate reverb decay time.
"""
from __future__ import annotations

import numpy as np


def estimate_rt60(x: np.ndarray, sr: int = 48000) -> float | None:
    """Estimate RT60 (seconds) from the decay tail following the signal's
    peak. Returns None if no usable decay is found (e.g. too short, no
    clear -5..-25dB decay slope)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < int(sr * 0.3):
        return None

    peak_idx = int(np.argmax(np.abs(x)))
    tail = x[peak_idx:]
    if len(tail) < int(sr * 0.2):
        return None

    energy = tail ** 2
    if energy.sum() <= 0:
        return None

    # Schroeder backward integration: cumulative energy from the end.
    sch = np.cumsum(energy[::-1])[::-1]
    if sch[0] <= 0:
        return None
    sch_db = 10.0 * np.log10(sch / sch[0] + 1e-20)

    below5 = np.where(sch_db <= -5.0)[0]
    below25 = np.where(sch_db <= -25.0)[0]
    if below5.size == 0 or below25.size == 0:
        return None
    i5, i25 = int(below5[0]), int(below25[0])
    if i25 <= i5:
        return None

    t5, t25 = i5 / sr, i25 / sr
    slope = (sch_db[i25] - sch_db[i5]) / (t25 - t5)  # dB/s, expected negative
    if slope >= 0:
        return None

    rt60 = -60.0 / slope
    if not np.isfinite(rt60) or rt60 <= 0 or rt60 > 10.0:
        return None
    return float(rt60)
