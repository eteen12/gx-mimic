"""Tier 1 (no JACK): the descriptor->knob table (design-contract.md section
9 `test_tweak_table`): bounds never violated, +n/-n round-trips within one
step, every referenced param id is real, enum values are legal, and
suggested_tweaks is a correct deterministic inverse of the table.
"""
from __future__ import annotations

import json
from importlib import resources

import pytest

from gxmimic.dsp import fingerprint as fpmod
from gxmimic.dsp import score as scoremod
from gxmimic.gx import params as paramsmod
from gxmimic.loop import build as buildmod
from gxmimic.loop import tweak as tweakmod

ALL_DESCRIPTORS = list(tweakmod.load_descriptor_map()["descriptors"].keys())
NOTCHES = [-3, -2, -1, -0.5, 0.5, 1, 2, 3]


@pytest.fixture(scope="module")
def base_preset(chord_wav):
    fp = fpmod.analyze_file(chord_wav)
    return buildmod.build_preset(fp, name="base")


def _iter_numeric_params(preset):
    for pid, value in preset.get("params", {}).items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and paramsmod.exists(pid):
            meta = paramsmod.PARAMS[pid]
            if meta["type"] in ("Float", "Int", "FloatEnum"):
                yield pid, value, meta


@pytest.mark.parametrize("descriptor", ALL_DESCRIPTORS)
@pytest.mark.parametrize("notches", NOTCHES)
def test_bounds_never_violated(base_preset, descriptor, notches):
    result = tweakmod.apply_tweaks(base_preset, {descriptor: notches}, allow_structural=True)
    for pid, value, meta in _iter_numeric_params(result["preset"]):
        lo, hi = meta.get("lower"), meta.get("upper")
        if lo is not None:
            assert value >= lo - 1e-6, f"{descriptor}@{notches}: {pid}={value} < lower {lo}"
        if hi is not None:
            assert value <= hi + 1e-6, f"{descriptor}@{notches}: {pid}={value} > upper {hi}"


@pytest.mark.parametrize("descriptor", ["brightness", "presence", "warmth", "mids"])
def test_plus_minus_round_trips_within_one_step(base_preset, descriptor):
    dm = tweakmod.load_descriptor_map()["descriptors"][descriptor]
    # the largest single-move delta magnitude in this descriptor's table --
    # "within one step" tolerance.
    max_delta = max(abs(m["delta"]) for group in dm["moves"] for m in group if "delta" in m)

    forward = tweakmod.apply_tweaks(base_preset, {descriptor: 1})
    back = tweakmod.apply_tweaks(forward["preset"], {descriptor: -1})

    orig_params = base_preset.get("params", {})
    final_params = back["preset"].get("params", {})
    for pid, orig_value in orig_params.items():
        if isinstance(orig_value, (int, float)) and not isinstance(orig_value, bool):
            final_value = final_params.get(pid, orig_value)
            assert abs(final_value - orig_value) <= max_delta + 1e-6, (
                f"{descriptor}: {pid} drifted from {orig_value} to {final_value} after +1/-1"
            )


def test_all_referenced_param_ids_exist():
    dm = tweakmod.load_descriptor_map()["descriptors"]

    def check(pid):
        assert paramsmod.exists(pid), f"descriptor_map.json references unknown param id: {pid!r}"

    for name, entry in dm.items():
        if "alias_of" in entry:
            continue
        for group in entry.get("moves", []):
            for m in group:
                check(m["param"])
                if m.get("enable"):
                    check(m["enable"])
        for m in entry.get("negative_moves", []):
            check(m["param"])
            if m.get("enable"):
                check(m["enable"])
        if entry.get("positive_disable_first"):
            check(entry["positive_disable_first"])
        if "structural" in entry:
            check(entry["structural"]["param"])
        if "extra_if_le" in entry:
            check(entry["extra_if_le"]["param"])
            check(entry["extra_if_le"]["enable"])
        if "bootstrap" in entry:
            check(entry["bootstrap"]["enable"])
            for pid in entry["bootstrap"]["set"]:
                check(pid)
        for group in entry.get("increment_moves", []):
            for m in group:
                check(m["param"])


def test_enum_values_stay_legal(base_preset):
    result = tweakmod.apply_tweaks(base_preset, {"mids": 3}, allow_structural=True)
    select = result["preset"]["params"].get("amp.tonestack.select")
    if select is not None:
        assert select in paramsmod.PARAMS["amp.tonestack.select"]["enum"]

    result2 = tweakmod.apply_tweaks(base_preset, {"gain": 2})
    tube = result2["preset"]["params"].get("tube.select")
    if tube is not None:
        assert tube in paramsmod.PARAMS["tube.select"]["enum"]


# ---------------------------------------------------------------------------
# suggested_tweaks: deterministic inverse of the notch table
# ---------------------------------------------------------------------------
def _fp_with_descriptor(base_fp, key, value, band=None):
    fp = json.loads(json.dumps(base_fp))
    if band:
        fp["bands"][band] = value
    else:
        fp["descriptors"][key] = value
    return fp


@pytest.fixture(scope="module")
def sample_fp(chord_wav):
    return fpmod.analyze_file(chord_wav)


@pytest.mark.parametrize("name,field,band,notch_size,inverted,n", [
    ("brightness", "brightness_hz", None, 220.0, False, 2),
    ("presence", "presence_ratio", None, 0.02, False, -1),
    ("fizz", "fizz_ratio", None, 0.015, False, 3),
    ("mids", "scoop_index_db", None, 1.5, True, 1),
    ("warmth", "warmth_ratio_db", None, 1.5, False, -2),
    ("gain", "gain_score", None, 0.07, False, 1),
    ("tightness", "tightness", None, 0.10, False, -1),
    ("reverb", "rt60_s", None, 0.15, False, 2),
    ("compression", "crest_db", None, 2.0, True, -1),
    ("bass", None, "low", 0.035, False, 1),
])
def test_suggested_tweaks_round_trips(sample_fp, name, field, band, notch_size, inverted, n):
    target = json.loads(json.dumps(sample_fp))
    render = json.loads(json.dumps(sample_fp))

    if band:
        base = render["bands"][band]
        delta = notch_size * n if not inverted else -notch_size * n
        target["bands"][band] = base + delta
    else:
        base = render["descriptors"][field]
        delta = notch_size * n if not inverted else -notch_size * n
        target["descriptors"][field] = base + delta

    suggested = scoremod.suggested_tweaks(target, render)
    assert suggested.get(name) == n, f"{name}: expected {n}, got {suggested.get(name)} ({suggested})"


def test_suggested_tweaks_zero_omitted(sample_fp):
    suggested = scoremod.suggested_tweaks(sample_fp, sample_fp)
    assert suggested == {}
