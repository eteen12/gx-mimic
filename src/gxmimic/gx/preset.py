"""Preset <-> engine-dict conversion.

Follows the make_greenday_preset.py pattern (prior art, ~/guitarix-tone-match):
start from the full ~1196-key engine template, zero every `*.on_off`, then
write the on_off/position/pp triad for each active chain unit plus whatever
scalar params the preset specifies.

A "preset" here is the `gx-mimic/preset/1` object (design-contract.md
section 2, `build`): {name, chain:{mono:[...], stereo:[...]},
models:{tube,tonestack,cab}, drive_axis, params:{...}, rationale:[...],
provenance:{...}}. `to_engine_dict` is the only place that actually knows
how those fields turn into the 1196 flat "module.param" keys guitarix reads.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path

from gxmimic.gx import chain as chainmod
from gxmimic.gx import params as paramsmod

ENGINE_TEMPLATE_NAME = "engine_template-0.46.0.json"

# Model-selecting units and the engine key that carries the enum choice.
MODEL_SELECT_KEYS = {
    "tube": "tube.select",
    "tonestack": "amp.tonestack.select",
    "cab": "cab.select",
}

# Units whose on_off must be forced on whenever `amp` (ampstack alias) is
# active -- the amp drive stage is really amp+tube+amp2 traveling together.
AMP_FAMILY_ON_OFF = ["amp.on_off"]


@lru_cache(maxsize=1)
def load_engine_template() -> dict:
    data_path = resources.files("gxmimic.data").joinpath(ENGINE_TEMPLATE_NAME)
    with resources.as_file(data_path) as p:
        return json.loads(Path(p).read_text())


def blank_engine() -> dict:
    """Template copy with every `*.on_off` zeroed -- the silent starting
    point every preset write begins from."""
    engine = dict(load_engine_template())
    for k in engine:
        if k.endswith(".on_off"):
            engine[k] = 0
    return engine


def to_engine_dict(preset: dict) -> dict:
    engine = blank_engine()

    mono = list(preset.get("chain", {}).get("mono", []))
    stereo = list(preset.get("chain", {}).get("stereo", []))

    mono_positions = chainmod.positions_for(mono, base=0)
    stereo_positions = chainmod.positions_for(stereo, base=0)

    for unit, pos in mono_positions.items():
        _activate_unit(engine, unit, pos["position"], pos["pp"])
    for unit, pos in stereo_positions.items():
        _activate_unit(engine, unit, pos["position"], pos["pp"])

    models = preset.get("models", {})
    for model_key, engine_key in MODEL_SELECT_KEYS.items():
        if model_key in models and models[model_key] is not None:
            engine[engine_key] = models[model_key]

    for pid, value in preset.get("params", {}).items():
        if paramsmod.exists(pid) and not paramsmod.PARAMS[pid].get("structured"):
            value = paramsmod.validate(pid, value)
        engine[pid] = value

    return engine


def _activate_unit(engine: dict, unit: str, position: int, pp: str) -> None:
    if unit == chainmod.AMP_ALIAS:
        for key in AMP_FAMILY_ON_OFF:
            if key in engine:
                engine[key] = 1
        return  # `amp` has no .position/.pp triad (it's the root unit)
    on_key, pos_key, pp_key = f"{unit}.on_off", f"{unit}.position", f"{unit}.pp"
    if on_key in engine:
        engine[on_key] = 1
    if pos_key in engine:
        engine[pos_key] = position
    if pp_key in engine:
        engine[pp_key] = pp


def stamp_ownership(engine: dict, tool_version: str, written_iso: str) -> dict:
    """Mark an engine dict as gx-mimic-owned (install.py safety rule 3)."""
    engine = dict(engine)
    engine["_gx_mimic"] = {"tool_version": tool_version, "written": written_iso}
    return engine


def is_stamped(engine: dict) -> bool:
    return isinstance(engine.get("_gx_mimic"), dict)
