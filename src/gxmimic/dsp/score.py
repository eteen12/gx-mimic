"""`score` primitive: pure computation, target fingerprint vs render
fingerprint -> gx-mimic/score/1. No I/O, no rendering -- just numbers in,
numbers out, per design-contract.md `score`.

Note on field naming: the design contract computes the per-band delta array
on the 24 BARK bands ("delta_i = target_db[i]-render_db[i] on 24 Bark
bands... w_i = sqrt(target_power_i)...") but names the *output* field
`third_octave_delta_db`. We follow the contract literally: the computation
runs on Bark bands, and the result is reported under the
`third_octave_delta_db` key exactly as specified.
"""
from __future__ import annotations

import numpy as np

SCHEMA_SCORE = "gx-mimic/score/1"

DEFAULT_WEIGHTS = {"spectral": 0.65, "gain": 0.25, "tightness": 0.10}
NORM_SPECTRAL_DB = 12.0
NORM_GAIN = 0.35
NORM_TIGHT = 0.50
BARK_NORM_RANGE = (100.0, 10000.0)

CONVERGE_MATCH = 85.0
CONVERGE_RMS_DB = 1.5
CONVERGE_GAIN_DELTA = 0.06

# descriptor -> (getter, notch_size, inverted)
# getter(fp) pulls the raw value this descriptor's notch table is defined
# against, per design-contract.md section 3.
def _get_bands_low(fp):
    return fp.get("bands", {}).get("low")


def _get_descr(name):
    return lambda fp: fp.get("descriptors", {}).get(name)


NOTCH_TABLE = {
    "brightness": (_get_descr("brightness_hz"), 220.0, False),
    "presence": (_get_descr("presence_ratio"), 0.02, False),
    "fizz": (_get_descr("fizz_ratio"), 0.015, False),
    "mids": (_get_descr("scoop_index_db"), 1.5, True),
    "bass": (_get_bands_low, 0.035, False),
    "warmth": (_get_descr("warmth_ratio_db"), 1.5, False),
    "gain": (_get_descr("gain_score"), 0.07, False),
    "tightness": (_get_descr("tightness"), 0.10, False),
    "reverb": (_get_descr("rt60_s"), 0.15, False),
    "compression": (_get_descr("crest_db"), 2.0, True),
}


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def suggested_tweaks(target_fp: dict, render_fp: dict) -> dict:
    """Deterministic inverse of the descriptor->knob table (section 3):
    how many notches (clamped to +-3, rounded) would move the render toward
    the target for each descriptor. Zero-notch entries are omitted."""
    out = {}
    for name, (getter, notch, inverted) in NOTCH_TABLE.items():
        t, r = getter(target_fp), getter(render_fp)
        if t is None or r is None:
            continue
        delta = (t - r) if not inverted else (r - t)
        n = int(round(_clip(delta / notch, -3.0, 3.0)))
        if n != 0:
            out[name] = n
    return out


def descriptor_delta(target_fp: dict, render_fp: dict) -> dict:
    td = target_fp.get("descriptors", {})
    rd = render_fp.get("descriptors", {})
    out = {}
    for key in td:
        if key == "gain_class":
            continue
        tv, rv = td.get(key), rd.get(key)
        if tv is None or rv is None:
            out[key] = None
        else:
            out[key] = tv - rv
    return out


def band_delta_db(target_fp: dict, render_fp: dict) -> dict:
    tb = target_fp.get("bands", {})
    rb = render_fp.get("bands", {})
    out = {}
    for name in tb:
        t, r = tb.get(name, 0.0), rb.get(name, 0.0)
        out[name] = 10.0 * np.log10((t + 1e-9) / (r + 1e-9))
    return out


def _verdict_lines(descr_delta: dict, band_delta: dict) -> list[str]:
    lines = []

    def note(cond, text):
        if cond:
            lines.append(text)

    bh = descr_delta.get("brightness_hz")
    if bh is not None:
        note(bh > 200, "target is brighter than the render (raise treble/presence)")
        note(bh < -200, "target is darker than the render (cut treble/presence)")
    gs = descr_delta.get("gain_score")
    if gs is not None:
        note(gs > 0.05, "target has more gain/distortion than the render")
        note(gs < -0.05, "target has less gain/distortion than the render (render is over-driven)")
    scoop = descr_delta.get("scoop_index_db")
    if scoop is not None:
        note(scoop > 1.0, "target's mids are scooped relative to the render (pull mids down or switch to a scooped stack)")
        note(scoop < -1.0, "target has more mids than the render (push mids up)")
    warmth = descr_delta.get("warmth_ratio_db")
    if warmth is not None:
        note(warmth > 1.0, "target is warmer (more low end relative to highs) than the render")
        note(warmth < -1.0, "target is thinner/brighter-weighted than the render")
    tight = descr_delta.get("tightness")
    if tight is not None:
        note(tight > 0.08, "target's low end is tighter (faster decay) than the render")
        note(tight < -0.08, "target's low end is looser than the render")
    fizz = descr_delta.get("fizz_ratio")
    if fizz is not None:
        note(fizz > 0.01, "target has more high-frequency fizz than the render")
        note(fizz < -0.01, "render is fizzier than the target")
    if not lines:
        lines.append("render is already close to the target across the board")
    return lines


def compute_score(target_fp: dict, render_fp: dict, weights: dict | None = None) -> dict:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    centers = np.array(target_fp["ltas"]["bark"]["centers_hz"], dtype=float)
    t_db = np.array(target_fp["ltas"]["bark"]["db"], dtype=float)
    r_db = np.array(render_fp["ltas"]["bark"]["db"], dtype=float)

    lo, hi = BARK_NORM_RANGE
    mask = (centers >= lo) & (centers <= hi)
    t_mean = float(t_db[mask].mean()) if mask.any() else 0.0
    r_mean = float(r_db[mask].mean()) if mask.any() else 0.0
    t_norm = t_db - t_mean
    r_norm = r_db - r_mean
    delta = t_norm - r_norm

    target_power = 10.0 ** (t_db / 10.0)
    w_raw = np.sqrt(target_power)
    floor = 0.05 * (w_raw.max() if w_raw.size else 1.0)
    weight = np.maximum(w_raw, floor)

    spectral_rms_db = float(np.sqrt(np.sum(weight * delta ** 2) / np.sum(weight))) if weight.sum() > 0 else 0.0

    gain_delta = target_fp["descriptors"]["gain_score"] - render_fp["descriptors"]["gain_score"]
    t_tight = target_fp["descriptors"].get("tightness")
    r_tight = render_fp["descriptors"].get("tightness")
    tight_delta = (t_tight - r_tight) if (t_tight is not None and r_tight is not None) else 0.0

    n_spectral = _clip(spectral_rms_db / NORM_SPECTRAL_DB, 0.0, 1.0)
    n_gain = _clip(abs(gain_delta) / NORM_GAIN, 0.0, 1.0)
    n_tight = _clip(abs(tight_delta) / NORM_TIGHT, 0.0, 1.0)

    composite = w["spectral"] * n_spectral + w["gain"] * n_gain + w["tightness"] * n_tight
    match = 100.0 * (1.0 - composite)

    converged = (match >= CONVERGE_MATCH) and (spectral_rms_db <= CONVERGE_RMS_DB) and (abs(gain_delta) <= CONVERGE_GAIN_DELTA)

    ddelta = descriptor_delta(target_fp, render_fp)
    bdelta = band_delta_db(target_fp, render_fp)

    third_octave_delta_db = [
        {"center_hz": float(c), "delta_db": float(d), "weight": float(x)}
        for c, d, x in zip(centers.tolist(), delta.tolist(), weight.tolist())
    ]

    return {
        "schema": SCHEMA_SCORE,
        "match": match,
        "components": {
            "spectral": n_spectral,
            "gain": n_gain,
            "tightness": n_tight,
            "composite": composite,
            "weights": w,
        },
        "spectral_rms_db": spectral_rms_db,
        "third_octave_delta_db": third_octave_delta_db,
        "band_delta_db": bdelta,
        "descriptor_delta": ddelta,
        "verdict": _verdict_lines(ddelta, bdelta),
        "suggested_tweaks": suggested_tweaks(target_fp, render_fp),
        "converged": bool(converged),
    }
