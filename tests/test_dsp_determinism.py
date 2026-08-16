"""Tier 1 (no JACK): determinism guarantees the whole matching loop leans
on -- design-contract.md section 9 `test_dsp_determinism`:
    - bit-identical repeat, including across a fresh subprocess
    - 44.1kHz vs 48kHz source within ~1%
    - a -3dB copy leaves ratios/LTAS/crest unchanged
    - locked against a golden JSON fixture
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import numpy as np
import pytest

from gxmimic.dsp import fingerprint as fpmod
from gxmimic.dsp import io as dspio

FIXTURES_DIR_NAME = "fixtures"


def _strip_volatile(fp: dict) -> dict:
    fp = copy.deepcopy(fp)
    fp.get("analysis", {}).pop("computed_at", None)
    fp.get("source", {})["path"] = None
    fp.get("source", {})["sha256"] = None
    return fp


def test_bit_identical_repeat_same_process(chord_wav):
    fp1 = fpmod.analyze_file(chord_wav)
    fp2 = fpmod.analyze_file(chord_wav)
    assert _strip_volatile(fp1) == _strip_volatile(fp2)


def test_bit_identical_fresh_subprocess(chord_wav):
    in_process = _strip_volatile(fpmod.analyze_file(chord_wav))

    code = (
        "import json, sys; "
        "from gxmimic.dsp import fingerprint as fpmod; "
        f"fp = fpmod.analyze_file({str(chord_wav)!r}); "
        "print(json.dumps(fp, default=str))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    subprocess_fp = _strip_volatile(json.loads(proc.stdout))
    assert subprocess_fp == in_process


@pytest.mark.parametrize("resample_rate", [44100])
def test_resample_stability_within_one_percent(chord_wav, tmp_path, resample_rate):
    x48 = dspio.read_wav_48k_mono_f32(chord_wav)
    x_resampled = dspio.resample_to(x48, 48000, resample_rate)
    resampled_path = tmp_path / f"chord_{resample_rate}.wav"
    dspio.write_wav_f32(resampled_path, x_resampled, resample_rate)

    fp48 = fpmod.analyze_file(chord_wav)
    fp_r = fpmod.analyze_file(resampled_path)

    d48, dr = fp48["descriptors"], fp_r["descriptors"]
    for key in d48:
        a, b = d48[key], dr[key]
        if not isinstance(a, (int, float)) or a is None or b is None:
            continue
        # Relative tolerance for descriptors with a meaningful magnitude,
        # plus a small absolute floor for near-zero ratio-style descriptors
        # (e.g. flatness_4to8k, fizz_ratio) where relative error is not a
        # meaningful measure.
        allowed = 0.01 * abs(a) + 0.02
        assert abs(a - b) <= allowed, f"{key}: 48k={a} vs {resample_rate}={b} (allowed {allowed})"


def test_minus_3db_copy_leaves_ratios_and_crest_unchanged(chord_wav):
    x = dspio.read_wav_48k_mono_f32(chord_wav)
    x_quiet = (x.astype(np.float64) * (10 ** (-3 / 20))).astype(np.float32)

    fp1 = fpmod.analyze_samples(x, 48000)
    fp2 = fpmod.analyze_samples(x_quiet, 48000)

    assert fp1["descriptors"]["crest_db"] == pytest.approx(fp2["descriptors"]["crest_db"], abs=1e-4)
    assert fp1["descriptors"]["warmth_ratio_db"] == pytest.approx(fp2["descriptors"]["warmth_ratio_db"], abs=1e-3)
    assert fp1["descriptors"]["scoop_index_db"] == pytest.approx(fp2["descriptors"]["scoop_index_db"], abs=1e-3)
    for band in fp1["bands"]:
        assert fp1["bands"][band] == pytest.approx(fp2["bands"][band], abs=1e-6)

    lt1 = np.array(fp1["ltas"]["third_octave"]["db"])
    lt2 = np.array(fp2["ltas"]["third_octave"]["db"])
    # RMS-normalization to -18dBFS happens before LTAS is computed, so a
    # pure gain change upstream should leave the LTAS curve itself (not
    # just its shape) effectively unchanged.
    non_silent = lt1 > -100
    assert np.allclose(lt1[non_silent], lt2[non_silent], atol=1e-2)


def test_golden_fingerprint(chord_wav, request):
    golden_path = request.path.parent / FIXTURES_DIR_NAME / "golden_chord_fingerprint.json"
    golden = json.loads(golden_path.read_text())
    fp = _strip_volatile(fpmod.analyze_file(chord_wav))

    assert fp["descriptors"].keys() == golden["descriptors"].keys()
    for key, expected in golden["descriptors"].items():
        actual = fp["descriptors"][key]
        if isinstance(expected, (int, float)):
            assert actual == pytest.approx(expected, abs=1e-6, rel=1e-6)
        else:
            assert actual == expected

    golden_db = np.array(golden["ltas"]["third_octave"]["db"])
    actual_db = np.array(fp["ltas"]["third_octave"]["db"])
    assert np.allclose(golden_db, actual_db, atol=1e-6)
