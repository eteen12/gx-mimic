"""`tweak` primitive: apply the descriptor->knob table (design-contract.md
section 3, data/descriptor_map.json) to a preset. Fractional notches OK.
Moves are applied IN ORDER (dict iteration order of the `deltas` argument);
within a descriptor, saturation at one knob spills the unabsorbed notch
budget to the next knob in its move list. Topology changes (turning a new
unit on) only happen with --allow-structural OR when the move table itself
requires flipping a unit's on_off as part of a knob move (e.g. enabling
ts9sim/expander/antyfizz/low_highpass/compressor/stereoverb as *part of* a
non-structural descriptor's own move chain) -- those are considered part of
the knob move, not the "suggested_structural" escape hatch, which is
reserved for genuine model swaps (mids -> different tonestack).
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources

from gxmimic.errors import GxError
from gxmimic.gx import params as paramsmod
from gxmimic.gx import preset as gxpreset
from gxmimic.loop import drive as drivemod

SCHEMA_TWEAK = "gx-mimic/tweak/1"
DESCRIPTOR_MAP_FILE = "descriptor_map.json"


@lru_cache(maxsize=1)
def load_descriptor_map() -> dict:
    data_path = resources.files("gxmimic.data").joinpath(DESCRIPTOR_MAP_FILE)
    with resources.as_file(data_path) as p:
        return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# preset param get/set helpers
# ---------------------------------------------------------------------------
def get_current(preset: dict, pid: str, engine_template: dict):
    params = preset.get("params", {})
    if pid in params:
        return params[pid]
    return engine_template.get(pid)


def set_current(preset: dict, pid: str, value) -> None:
    preset.setdefault("params", {})[pid] = value


def _resolve_clamp(move: dict, pid: str):
    clamp = move.get("clamp")
    meta = paramsmod.PARAMS.get(pid, {})
    lo = meta.get("lower")
    hi = meta.get("upper")
    if clamp is not None:
        c_lo, c_hi = clamp
        if c_lo is not None:
            lo = c_lo
        if c_hi is not None:
            hi = c_hi
    return lo, hi


def _clip(v, lo, hi):
    if lo is not None:
        v = max(v, lo)
    if hi is not None:
        v = min(v, hi)
    return v


def _add_to_chain(preset: dict, unit: str) -> None:
    chain = preset.setdefault("chain", {"mono": [], "stereo": []})
    if unit == "stereoverb":
        lst = chain.setdefault("stereo", [])
        if unit not in lst:
            lst.append(unit)
        return
    lst = chain.setdefault("mono", [])
    if unit in lst:
        return
    if "eqs" in lst:
        lst.insert(lst.index("eqs"), unit)
    else:
        lst.append(unit)


def _ensure_enabled(preset: dict, pid: str, engine_template: dict, applied: list) -> None:
    cur = bool(get_current(preset, pid, engine_template))
    if not cur:
        set_current(preset, pid, True)
        applied.append({"param": pid, "from": cur, "to": True, "reason": "enable"})
    unit = pid.rsplit(".", 1)[0]
    _add_to_chain(preset, unit)


# ---------------------------------------------------------------------------
# generic additive move-group cascade
# ---------------------------------------------------------------------------
def _apply_group(preset: dict, group: list[dict], remaining: float, engine_template: dict,
                  applied: list, clamped: list) -> float:
    if abs(remaining) < 1e-9 or not group:
        return remaining

    delta_entries = [m for m in group if "delta" in m]
    gate_pid = delta_entries[0]["param"] if delta_entries else None
    leftover = 0.0

    for m in group:
        pid = m["param"]
        if m.get("one_shot_enable"):
            _ensure_enabled(preset, pid, engine_template, applied)
            continue
        if "set" in m:
            if m.get("enable"):
                _ensure_enabled(preset, m["enable"], engine_template, applied)
            cur = get_current(preset, pid, engine_template)
            val = m["set"]
            if cur != val:
                set_current(preset, pid, val)
                applied.append({"param": pid, "from": cur, "to": val, "reason": "fixed"})
            continue
        if "delta" in m:
            cur = get_current(preset, pid, engine_template)
            if m.get("base_if_enabling") is not None and pid not in preset.get("params", {}):
                cur = m["base_if_enabling"]
            desired = m["delta"] * remaining
            lo, hi = _resolve_clamp(m, pid)
            new = _clip(cur + desired, lo, hi)
            if m.get("enable"):
                _ensure_enabled(preset, m["enable"], engine_template, applied)
            absorbed = new - cur
            if abs(absorbed - desired) > 1e-9:
                if pid not in clamped:
                    clamped.append(pid)
            if abs(new - cur) > 1e-12:
                set_current(preset, pid, new)
                applied.append({"param": pid, "from": cur, "to": new, "reason": "notch"})
            if pid == gate_pid:
                leftover = remaining - (absorbed / m["delta"] if m["delta"] else 0.0)
    return leftover


def _apply_additive(preset: dict, entry: dict, notches: float, engine_template: dict,
                     applied: list, clamped: list) -> float:
    remaining = notches
    for group in entry.get("moves", []):
        if abs(remaining) < 1e-9:
            break
        remaining = _apply_group(preset, group, remaining, engine_template, applied, clamped)
    return remaining


def _apply_mids_structural(preset: dict, structural: dict, notches: float, leftover: float,
                            allow_structural: bool, applied: list, suggested_structural: list,
                            engine_template: dict) -> None:
    if abs(leftover) < 1e-9 or notches <= 0 or structural.get("direction") != "positive":
        return
    select_pid = structural["param"]
    cur_select = get_current(preset, select_pid, engine_template)
    if cur_select not in structural.get("when_select_in", []):
        return
    because = (
        f"tonestack '{cur_select}' is a scooped stack out of mids headroom; "
        f"switching to {structural['suggest']} (or {structural['alt']}) would free up more mids"
    )
    if allow_structural:
        set_current(preset, select_pid, structural["suggest"])
        applied.append({"param": select_pid, "from": cur_select, "to": structural["suggest"], "reason": "structural: " + because})
        preset.setdefault("models", {})["tonestack"] = structural["suggest"]
    else:
        suggested_structural.append({
            "param": select_pid, "from": cur_select, "suggest": structural["suggest"],
            "alt": structural["alt"], "because": because,
        })


def _apply_bass_extra(preset: dict, extra: dict, notches: float, engine_template: dict, applied: list) -> None:
    if notches > extra["threshold"]:
        return
    _ensure_enabled(preset, extra["enable"], engine_template, applied)
    pid = extra["param"]
    cur = get_current(preset, pid, engine_template)
    lo, hi = extra.get("clamp", [None, None])
    meta = paramsmod.PARAMS.get(pid, {})
    lo = lo if lo is not None else meta.get("lower")
    hi = hi if hi is not None else meta.get("upper")
    new = _clip(cur + extra["delta"], lo, hi)
    if new != cur:
        set_current(preset, pid, new)
        applied.append({"param": pid, "from": cur, "to": new, "reason": "bass extreme-cut extra step"})


def _apply_fizz(preset: dict, entry: dict, notches: float, engine_template: dict,
                 applied: list, clamped: list) -> None:
    if notches < 0:
        magnitude = -notches
        remaining = magnitude
        for m in entry["negative_moves"]:
            if abs(remaining) < 1e-9:
                break
            remaining = _apply_group(preset, [m], remaining, engine_template, applied, clamped)
        return
    if notches == 0:
        return

    # positive direction: disable antyfizz first, then mirror the negative
    # move list in reverse with sign flipped (raise highs back up / relax
    # the highpass tightening).
    disable_pid = entry.get("positive_disable_first")
    if disable_pid:
        cur = bool(get_current(preset, disable_pid, engine_template))
        if cur:
            set_current(preset, disable_pid, False)
            applied.append({"param": disable_pid, "from": cur, "to": False, "reason": "disable before raising fizz"})

    remaining = notches
    for m in reversed(entry["negative_moves"]):
        if abs(remaining) < 1e-9:
            break
        if m["param"] == disable_pid or m.get("one_shot_enable"):
            continue  # already handled as a one-shot disable above
        mirrored = {k: v for k, v in m.items() if k not in ("clamp", "enable")}
        if "delta" in mirrored:
            mirrored["delta"] = -mirrored["delta"]
        remaining = _apply_group(preset, [mirrored], remaining, engine_template, applied, clamped)


def _apply_reverb(preset: dict, entry: dict, notches: float, engine_template: dict,
                   applied: list, clamped: list) -> None:
    if notches == 0:
        return
    cur_on = bool(get_current(preset, "stereoverb.on_off", engine_template))
    if notches > 0:
        if not cur_on:
            boot = entry["bootstrap"]
            _ensure_enabled(preset, boot["enable"], engine_template, applied)
            for pid, val in boot["set"].items():
                old = get_current(preset, pid, engine_template)
                if old != val:
                    set_current(preset, pid, val)
                    applied.append({"param": pid, "from": old, "to": val, "reason": "reverb bootstrap"})
            return
        remaining = notches
        for group in entry["increment_moves"]:
            if abs(remaining) < 1e-9:
                break
            remaining = _apply_group(preset, group, remaining, engine_template, applied, clamped)
        return

    # notches < 0
    if not cur_on:
        return
    remaining = notches
    for group in entry["increment_moves"]:
        if abs(remaining) < 1e-9:
            break
        remaining = _apply_group(preset, group, remaining, engine_template, applied, clamped)
    wet_dry = get_current(preset, "stereoverb.wet_dry", engine_template)
    if wet_dry is not None and wet_dry <= entry["auto_off_wet_dry_le"]:
        set_current(preset, "stereoverb.on_off", False)
        applied.append({"param": "stereoverb.on_off", "from": True, "to": False, "reason": "wet/dry fell to auto-off threshold"})


def _apply_gain(preset: dict, entry: dict, notches: float, engine_template: dict, applied: list) -> None:
    if notches == 0:
        return
    old_axis = preset.get("drive_axis", 0.0) or 0.0
    new_axis = _clip(old_axis + entry["drive_axis_delta_per_notch"] * notches, 0.0, 1.0)
    sched_vals = drivemod.interpolate(new_axis)
    for pid, val in sched_vals.items():
        old = get_current(preset, pid, engine_template)
        if old != val:
            set_current(preset, pid, val)
            applied.append({"param": pid, "from": old, "to": val, "reason": "drive_axis re-interpolation"})
    if new_axis != old_axis:
        preset["drive_axis"] = new_axis


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def apply_tweaks(preset: dict, deltas: dict, allow_structural: bool = False) -> dict:
    dm = load_descriptor_map()
    preset = copy.deepcopy(preset)
    engine_template = gxpreset.load_engine_template()

    applied: list = []
    clamped: list = []
    suggested_structural: list = []

    for name, notches in deltas.items():
        entry = dm["descriptors"].get(name)
        if entry is None:
            raise GxError("usage", f"unknown tweak descriptor: {name!r}",
                          hint=f"available: {sorted(dm['descriptors'].keys())}")
        notches = float(notches)

        if "alias_of" in entry:
            sign = entry.get("sign", 1)
            base_name = entry["alias_of"]
            entry = dm["descriptors"][base_name]
            notches = notches * sign
            name = base_name

        kind = entry["kind"]
        if kind == "additive":
            leftover = _apply_additive(preset, entry, notches, engine_template, applied, clamped)
            if name == "mids" and "structural" in entry:
                _apply_mids_structural(preset, entry["structural"], notches, leftover, allow_structural,
                                        applied, suggested_structural, engine_template)
            if name == "bass" and "extra_if_le" in entry:
                _apply_bass_extra(preset, entry["extra_if_le"], notches, engine_template, applied)
        elif kind == "fizz":
            _apply_fizz(preset, entry, notches, engine_template, applied, clamped)
        elif kind == "reverb":
            _apply_reverb(preset, entry, notches, engine_template, applied, clamped)
        elif kind == "gain":
            _apply_gain(preset, entry, notches, engine_template, applied)
        else:
            raise ValueError(f"unknown descriptor kind: {kind!r}")

    return {
        "schema": SCHEMA_TWEAK,
        "applied": applied,
        "clamped": clamped,
        "suggested_structural": suggested_structural,
        "preset": preset,
    }
