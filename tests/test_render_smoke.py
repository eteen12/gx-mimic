"""Tier 2 (pytest -m jack, needs a real/dummy-driver JACK server + guitarix):
design-contract.md section 9 `test_render_smoke`. NOT run by the mechanic
agent -- written for the JACK-phase agent to execute.

Renders a neutral preset through real Guitarix and checks the render
pipeline's basic health, plus the single most important safety invariant in
the whole project: ~/.config/guitarix is NEVER touched by a render.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from gxmimic import api
from gxmimic.loop import build as buildmod

pytestmark = pytest.mark.jack

REAL_GX_CONFIG = Path.home() / ".config" / "guitarix"


def _mtimes(root: Path) -> dict[str, float]:
    if not root.is_dir():
        return {}
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            out[str(p)] = p.stat().st_mtime
    return out


def _neutral_fingerprint():
    return {
        "descriptors": {
            "gain_class": "clean", "gain_score": 0.15, "brightness_hz": 1500,
            "scoop_index_db": 0.0, "presence_ratio": 0.05, "fizz_ratio": 0.01,
            "tightness": 0.5, "rolloff85_hz": 4000, "warmth_ratio_db": 0.0,
            "crest_db": 15.0, "zcr": 0.08, "flatness_4to8k": 0.05,
            "rolloff15_hz": 300, "clipping_ratio": 0.0,
        },
        "bands": {"low": 0.30, "low_mid": 0.20, "mid": 0.40, "presence": 0.05, "fizz": 0.01},
    }


def test_render_smoke(fake_home, jack_dummy, chord_wav):
    before_mtimes = _mtimes(REAL_GX_CONFIG)

    preset = buildmod.build_preset(_neutral_fingerprint(), name="neutral-smoke-test")
    result = api.render(preset, fake_home, clips=["chord"])

    assert result["schema"] == "gx-mimic/render/1"
    chord_fp = result["clips"]["chord"]["fingerprint"]

    rms_dbfs = chord_fp["levels"]["rms_dbfs"]
    assert -40.0 <= rms_dbfs <= -1.0, f"unexpected render RMS: {rms_dbfs} dBFS"

    expected_duration = 8.0  # chord.wav's nominal length
    actual_duration = chord_fp["source"]["duration_s"]
    period_s = 1024 / 48000  # one JACK period at the render sample rate
    assert abs(actual_duration - expected_duration) <= 10 * period_s + 0.5

    assert result["jack"]["xruns"] <= 5

    gxconfig = fake_home / "gxconfig" / "guitarix"
    assert gxconfig.is_dir()
    assert (gxconfig / "banks").is_dir()

    after_mtimes = _mtimes(REAL_GX_CONFIG)
    assert before_mtimes == after_mtimes, (
        "render touched ~/.config/guitarix -- this must NEVER happen "
        "(design-contract.md safety rule 1 / XDG isolation)"
    )
