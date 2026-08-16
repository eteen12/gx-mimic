"""Tier 1 (no JACK): model-selection rules (design-contract.md section 9
`test_chain_rules`): ~15 synthetic fingerprints exercising expected model
rows, every rule in data/chain_rules.json reachable (first-match, in
order).
"""
from __future__ import annotations

import pytest

from gxmimic.loop import build as buildmod

RULES = buildmod.load_chain_rules()


def make_fp(gain_class, brightness_hz=1500, scoop_index_db=0.0, presence_ratio=0.05,
            tightness=0.5, fizz_ratio=0.02, rolloff85_hz=4000, low=0.30, gain_score=0.3):
    return {
        "descriptors": {
            "gain_class": gain_class,
            "brightness_hz": brightness_hz,
            "scoop_index_db": scoop_index_db,
            "presence_ratio": presence_ratio,
            "tightness": tightness,
            "fizz_ratio": fizz_ratio,
            "rolloff85_hz": rolloff85_hz,
            "gain_score": gain_score,
        },
        "bands": {"low": low, "low_mid": 0.2, "mid": 0.4, "presence": presence_ratio, "fizz": fizz_ratio},
    }


# ---------------------------------------------------------------------------
# Tonestack: 15 synthetic fingerprints, one per rule row (first-match order).
# ---------------------------------------------------------------------------
TONESTACK_CASES = [
    # (gain_class, kwargs, expected select)
    ("clean", dict(brightness_hz=2200, scoop_index_db=3), "Twin Reverb"),
    ("clean", dict(brightness_hz=1900, scoop_index_db=0), "AC-30"),
    ("clean", dict(brightness_hz=1500, scoop_index_db=0), "Princeton"),
    ("clean", dict(brightness_hz=1800, scoop_index_db=3, low=0.40), "Bassman"),
    ("clean", dict(brightness_hz=1800, scoop_index_db=3, low=0.20), "Princeton"),

    ("crunch", dict(scoop_index_db=-2), "JTM-45"),
    ("crunch", dict(scoop_index_db=0, brightness_hz=2400), "JCM-800"),
    ("crunch", dict(scoop_index_db=0, brightness_hz=2000, presence_ratio=0.20), "AC-30"),
    ("crunch", dict(scoop_index_db=0, brightness_hz=2000, presence_ratio=0.05), "Bassman"),

    ("high_gain", dict(scoop_index_db=4), "Mesa Boogie"),
    ("high_gain", dict(scoop_index_db=0, brightness_hz=2700, tightness=0.7), "Engl"),
    ("high_gain", dict(scoop_index_db=-2, brightness_hz=2000, tightness=0.3), "SOL 100"),
    ("high_gain", dict(scoop_index_db=0, brightness_hz=2000, tightness=0.3), "JCM-2000"),

    ("extreme", dict(scoop_index_db=3), "Mesa Boogie"),
    ("extreme", dict(scoop_index_db=0), "Engl"),
]


@pytest.mark.parametrize("gain_class,kwargs,expected", TONESTACK_CASES)
def test_tonestack_rule_reachable(gain_class, kwargs, expected):
    fp = make_fp(gain_class, **kwargs)
    select, _reason = buildmod.select_tonestack(fp, RULES)
    assert select == expected


def test_all_tonestack_rules_covered():
    n_rules = sum(len(v) for v in RULES["tonestack_rules"].values())
    assert len(TONESTACK_CASES) == n_rules, "every tonestack rule row should have exactly one covering case"


# ---------------------------------------------------------------------------
# Cab: one synthetic fingerprint per rule row.
# ---------------------------------------------------------------------------
CAB_CASES = [
    ("high_gain", dict(fizz_ratio=0.02), "HighGain"),
    ("high_gain", dict(fizz_ratio=0.10, scoop_index_db=3), "Mesa Boogie"),
    ("crunch", dict(brightness_hz=2300), "Marshall"),
    ("crunch", dict(brightness_hz=1000), "4x12"),
    ("clean", dict(brightness_hz=2100), "Twin"),
    ("clean", dict(brightness_hz=1000, low=0.40), "Bassman"),
    ("clean", dict(brightness_hz=1000, low=0.10), "Princeton"),
    ("extreme", dict(fizz_ratio=0.5, scoop_index_db=0, rolloff85_hz=3000), "2x12"),
    ("extreme", dict(fizz_ratio=0.5, scoop_index_db=0, rolloff85_hz=8000), "4x12"),
]


@pytest.mark.parametrize("gain_class,kwargs,expected", CAB_CASES)
def test_cab_rule_reachable(gain_class, kwargs, expected):
    fp = make_fp(gain_class, **kwargs)
    select, _reason = buildmod.select_cab(fp, RULES)
    assert select == expected


def test_all_cab_rules_covered():
    assert len(CAB_CASES) == len(RULES["cab_rules"])


# ---------------------------------------------------------------------------
# drive_seed / tonestack_prior sanity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("gain_class,gain_score", [
    ("clean", 0.10), ("crunch", 0.40), ("high_gain", 0.65), ("extreme", 0.90),
])
def test_drive_seed_in_bounds(gain_class, gain_score):
    fp = make_fp(gain_class, gain_score=gain_score)
    axis = buildmod.drive_seed(fp)
    assert 0.0 <= axis <= 1.0


def test_tonestack_prior_in_bounds():
    fp = make_fp("crunch", brightness_hz=2500, scoop_index_db=-3, low=0.6)
    prior = buildmod.tonestack_prior(fp, RULES)
    assert 0.15 <= prior["amp.tonestack.Bass"] <= 0.90
    assert 0.15 <= prior["amp.tonestack.Middle"] <= 0.95
    assert 0.15 <= prior["amp.tonestack.Treble"] <= 0.92


def test_build_preset_end_to_end_smoke():
    fp = make_fp("high_gain", brightness_hz=2700, scoop_index_db=1, tightness=0.7, gain_score=0.65)
    preset = buildmod.build_preset(fp, name="smoke")
    assert preset["schema"] == "gx-mimic/preset/1"
    assert preset["chain"]["mono"][-1] == "eqs"
    assert preset["models"]["tonestack"]
    assert 0.0 <= preset["drive_axis"] <= 1.0
