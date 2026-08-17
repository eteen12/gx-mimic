"""Tier 1 (no JACK): the EQ solve (dsp/fit.py), design-contract.md section 9
`test_fit_solver`: recovers a known atlas combination + noise within 0.5dB,
residual < 0.6dB, bounds honoured, clipped_bands reported, deterministic.
"""
from __future__ import annotations

import numpy as np
import pytest

from gxmimic.dsp import fit as fitmod


def test_recovers_known_combination_within_tolerance():
    centers, S, param_ids = fitmod.atlas_matrix(include_cab_eq=False)
    rng = np.random.default_rng(1234)

    true_gains = np.zeros(len(param_ids))
    true_gains[param_ids.index("eqs.fs1k")] = 5.0
    true_gains[param_ids.index("eqs.fs4k")] = -3.5

    clean = S @ true_gains
    # Noise floor scaled to the measured atlas (gx-mimic calibrate eqs
    # replaced the analytical placeholder -- see atlas-eqs-v1.npz meta):
    # eqs band columns now peak around 0.03-0.14 dB/dB (guitarix's `eqs`
    # bands run a high default Q, so a third-octave LTAS measurement only
    # sees a diluted sliver of the true narrowband boost), not the
    # placeholder's near-unity normalization. A 5dB gain at fs1k now only
    # moves the measured curve by ~0.5dB at its own band, so 0.3dB of
    # injected noise (appropriate against a ~1.0-peak atlas) would swamp
    # the real signal outright; 0.03dB keeps a comparable SNR to what this
    # test originally exercised.
    noisy = clean + rng.normal(0, 0.03, size=clean.shape)

    result = fitmod.solve_gains(centers, noisy, max_boost=12.0, lam=0.05, S=S, param_ids=param_ids)
    recovered = dict(zip(result["param_ids"], result["gains"]))

    assert abs(recovered["eqs.fs1k"] - 5.0) <= 0.5
    assert abs(recovered["eqs.fs4k"] - (-3.5)) <= 0.5
    assert result["residual_rms_db"] < 0.6


def test_bounds_honoured():
    centers, S, param_ids = fitmod.atlas_matrix(include_cab_eq=True)
    # Ask for something far beyond any reasonable bound.
    huge_error = np.full(len(centers), 50.0)
    result = fitmod.solve_gains(centers, huge_error, max_boost=12.0, lam=0.05,
                                 include_cab_eq=True, S=S, param_ids=param_ids)
    for pid, gain in zip(result["param_ids"], result["gains"]):
        bound = 12.0 if pid.startswith("eqs.") else fitmod.CAB_HI
        assert gain <= bound + 1e-6
        assert gain >= -bound - 1e-6


def test_clipped_bands_reported_when_target_exceeds_max_boost():
    # Build fake fingerprints whose third-octave curves differ by more than
    # max_boost at some bands, after the AutoEQ pipeline (smoothing/slope
    # limiting can reduce peak error, so use a broad, sustained boost).
    centers = fitmod.load_atlas()["centers_hz"].tolist()

    def make_fp(db_curve):
        return {"ltas": {"third_octave": {"centers_hz": centers, "db": db_curve}}}

    target_db = [0.0] * len(centers)
    render_db = [0.0] * len(centers)
    # a wide, deep dip in the render around a low-mid region -> big boost needed
    for i, c in enumerate(centers):
        if 300 <= c <= 1200:
            render_db[i] = -25.0

    target_fp = make_fp(target_db)
    render_fp = make_fp(render_db)

    result = fitmod.solve_eq(target_fp, render_fp, max_boost=12.0, lam=0.05)
    assert isinstance(result["clipped_bands"], list)
    assert len(result["clipped_bands"]) > 0
    for band in result["clipped_bands"]:
        assert abs(band["clamped_db"]) <= 12.0 + 1e-6


def test_deterministic():
    centers, S, param_ids = fitmod.atlas_matrix(include_cab_eq=False)
    rng = np.random.default_rng(42)
    error = rng.normal(0, 2.0, size=len(centers))

    r1 = fitmod.solve_gains(centers, error, max_boost=12.0, lam=0.05, S=S, param_ids=param_ids)
    r2 = fitmod.solve_gains(centers, error, max_boost=12.0, lam=0.05, S=S, param_ids=param_ids)
    assert np.array_equal(r1["gains"], r2["gains"])
    assert r1["residual_rms_db"] == r2["residual_rms_db"]


def test_solve_eq_end_to_end_smoke():
    centers = fitmod.load_atlas()["centers_hz"].tolist()
    target_fp = {"ltas": {"third_octave": {"centers_hz": centers, "db": [0.0] * len(centers)}}}
    render_fp = {"ltas": {"third_octave": {"centers_hz": centers, "db": [0.0] * len(centers)}}}
    result = fitmod.solve_eq(target_fp, render_fp)
    assert result["schema"] == fitmod.SCHEMA_FIT
    assert set(result["solved"].keys()) == set(fitmod.EQS_PARAM_IDS)
    # atlas-eqs-v1.npz is now a measured atlas (gx-mimic calibrate eqs), not
    # the analytical placeholder, so solve_eq's placeholder warning is gone.
    assert result["note"] is None
    assert result["residual_rms_db"] == pytest.approx(0.0, abs=1e-6)
