"""`analyze` primitive: audio file -> gx-mimic/fingerprint/1 dict.

Pipeline (design-contract.md `analyze`):
    ffmpeg decode -> resample_poly 48k mono f32 -> STFT LTAS (gated, median)
    -> 31 third-octave + 24 Bark bins -> 5 named bands -> RMS-normalize to
    -18dBFS before band ratios (crest computed on the UN-normalized signal)
    -> descriptors.
"""
from __future__ import annotations

import time

import numpy as np

from gxmimic.dsp import descriptors as desc
from gxmimic.dsp import io as dspio
from gxmimic.dsp import ltas as ltasmod
from gxmimic.dsp import rt60 as rt60mod
from gxmimic.errors import GxError

SCHEMA_FINGERPRINT = "gx-mimic/fingerprint/1"
TARGET_SR = 48000
NORMALIZE_DBFS = -18.0
MIN_DURATION_S = 0.5


def _rms_dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0
    return 20.0 * np.log10(max(rms, 1e-12))


def _peak_dbfs(x: np.ndarray) -> float:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return 20.0 * np.log10(max(peak, 1e-12))


def analyze_samples(x: np.ndarray, sr: int = TARGET_SR, source_meta: dict | None = None,
                     label: str = "reference") -> dict:
    """Compute a full fingerprint dict from already-decoded 48k mono f32
    samples. `source_meta` (path/sha256/orig_sample_rate/channels) is
    merged in verbatim if given."""
    x = np.asarray(x, dtype=np.float32)
    warnings: list[str] = []

    if len(x) < int(MIN_DURATION_S * sr):
        raise GxError("audio", f"audio too short to analyze ({len(x) / sr:.2f}s < {MIN_DURATION_S}s)")

    peak_dbfs = _peak_dbfs(x)
    rms_dbfs = _rms_dbfs(x)

    # RMS-normalize to -18dBFS before band ratios; crest is computed on the
    # un-normalized signal below.
    gain_db = NORMALIZE_DBFS - rms_dbfs if rms_dbfs > -120 else 0.0
    gain_lin = 10.0 ** (gain_db / 20.0)
    x_norm = (x.astype(np.float64) * gain_lin).astype(np.float32)

    freqs, median_power, meta = ltasmod.compute_median_power_spectrum(x_norm, sr)

    third_octave_power = ltasmod.third_octave_bands(freqs, median_power)
    bark_power = ltasmod.bark_bands(freqs, median_power)

    bands_power = desc.band_powers(freqs, median_power)
    total_named_power = sum(bands_power.values()) or 1e-20
    # Internal dB levels (needed for the scoop_index_db formula, which is
    # explicitly a dB quantity per design-contract.md section 3).
    band_db = {name: float(ltasmod.to_db(np.array([bands_power[name]]))[0]) for name in desc.BAND_ORDER}
    # Reported `bands` field: fraction of total named-band (80-8000Hz) power
    # per band -- same scale as presence_ratio/fizz_ratio, and what the
    # descriptor->knob table's `Δbands.low=0.035` notch size assumes.
    band_ratio = {name: bands_power[name] / total_named_power for name in desc.BAND_ORDER}

    brightness_hz = desc.spectral_centroid(freqs, median_power)
    rolloff15_hz = desc.spectral_rolloff(freqs, median_power, 0.15)
    rolloff85_hz = desc.spectral_rolloff(freqs, median_power, 0.85)
    warmth_ratio_db = 10.0 * np.log10(
        (desc._band_power_sum(freqs, median_power, 100, 500) + 1e-20)
        / (desc._band_power_sum(freqs, median_power, 2000, 8000) + 1e-20)
    )
    scoop_index_db = (band_db["low"] + band_db["presence"]) / 2.0 - band_db["mid"]
    presence_ratio = bands_power["presence"] / total_named_power
    fizz_ratio = bands_power["fizz"] / total_named_power
    flatness_4to8k = desc.spectral_flatness(freqs, median_power, 4000, 8000)

    # Crest/ZCR on the UN-normalized, silence-gated signal.
    gated = desc.gated_samples(x, sr)
    crest_db = desc.crest_factor_db(gated)
    zcr = desc.zero_crossing_rate(gated)
    clip_ratio = desc.clipping_ratio(x)

    gs = desc.gain_score(crest_db, flatness_4to8k, zcr)
    gclass = desc.gain_class(gs)

    tight_ratio_db, tight_score = desc.tightness(x, sr)

    rt60_s = rt60mod.estimate_rt60(x, sr)

    descriptors = {
        "brightness_hz": brightness_hz,
        "rolloff15_hz": rolloff15_hz,
        "rolloff85_hz": rolloff85_hz,
        "warmth_ratio_db": warmth_ratio_db,
        "scoop_index_db": scoop_index_db,
        "presence_ratio": presence_ratio,
        "fizz_ratio": fizz_ratio,
        "flatness_4to8k": flatness_4to8k,
        "crest_db": crest_db,
        "zcr": zcr,
        "gain_score": gs,
        "gain_class": gclass,
        "tightness": tight_score,
        "rt60_s": rt60_s,
        "clipping_ratio": clip_ratio,
    }

    # --- R4/R7 heuristic warnings -----------------------------------------
    below80 = desc._band_power_sum(freqs, median_power, 0, 80)
    total_wide = below80 + total_named_power
    if total_wide > 0 and below80 / total_wide > 0.15:
        warnings.append(
            "significant energy below 80Hz: this may be a full mix with bass/drums, not an isolated guitar track"
        )
    flat_above_10k = desc.spectral_flatness(freqs, median_power, 10000, min(20000, sr / 2))
    if flat_above_10k > 0.5:
        warnings.append("high spectral flatness above 10kHz: may indicate cymbals/full-mix content, not a DI guitar")
    if crest_db > 15 and rt60_s is not None and rt60_s > 0.3:
        warnings.append("high crest factor with long decay: could be a roomy/live full mix rather than a clean DI")
    if scoop_index_db > 3 and crest_db > 12 and rt60_s is not None and rt60_s > 0.3:
        warnings.append(
            "spectral shape resembles a 'minus guitar' backing track (scooped mids, low gain, roomy): "
            "confirm this is the REFERENCE isolated guitar, not a playalong track"
        )
    if clip_ratio > 0.001:
        warnings.append(f"clipping detected in {clip_ratio * 100:.2f}% of samples")

    fp = {
        "schema": SCHEMA_FINGERPRINT,
        "source": {
            "path": None,
            "sha256": None,
            "duration_s": len(x) / sr,
            "orig_sample_rate": sr,
            "channels": 1,
        },
        "label": label,
        "analysis": {
            "sample_rate": sr,
            "nperseg": ltasmod.NPERSEG,
            "noverlap": ltasmod.NOVERLAP,
            "n_frames": meta["n_frames"],
            "n_kept": meta["n_kept"],
            "gate_ref_db": meta["gate_ref_db"],
            "normalize_gain_db": gain_db,
            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "ltas": {
            "third_octave": {
                "centers_hz": list(ltasmod.THIRD_OCTAVE_CENTERS),
                "db": [float(v) for v in ltasmod.to_db(third_octave_power)],
            },
            "bark": {
                "centers_hz": ltasmod.bark_centers(),
                "db": [float(v) for v in ltasmod.to_db(bark_power)],
            },
        },
        "bands": band_ratio,
        "descriptors": descriptors,
        "levels": {
            "peak_dbfs": peak_dbfs,
            "rms_dbfs": rms_dbfs,
            "normalized_to_dbfs": NORMALIZE_DBFS,
        },
        "warnings": warnings,
    }
    if source_meta:
        fp["source"].update(source_meta)
    return fp


def analyze_file(path, start: float | None = None, duration: float | None = None,
                  label: str = "reference") -> dict:
    samples, orig_sr, channels, file_duration = dspio.decode_audio(path)
    sha256 = dspio.sha256_file(path)

    sr = TARGET_SR
    if start:
        s0 = int(start * sr)
        samples = samples[s0:]
    if duration:
        n = int(duration * sr)
        samples = samples[:n]

    source_meta = {"path": str(path), "sha256": sha256, "orig_sample_rate": orig_sr, "channels": channels}
    fp = analyze_samples(samples, sr, source_meta=source_meta, label=label)
    fp["source"]["duration_s"] = file_duration
    return fp
