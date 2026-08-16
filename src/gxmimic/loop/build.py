"""`build` primitive: deterministic fingerprint -> preset, NO renders
(design-contract.md `build`). Applies data/chain_rules.json + the drive
schedule + a tonestack knob prior; reverb only if rt60_s > threshold.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from gxmimic.gx import chain as chainmod
from gxmimic.loop import drive as drivemod

SCHEMA_PRESET = "gx-mimic/preset/1"
CHAIN_RULES_FILE = "chain_rules.json"

_OPS = {
    ">=": lambda v, a: v is not None and v >= a,
    "<=": lambda v, a: v is not None and v <= a,
    ">": lambda v, a: v is not None and v > a,
    "<": lambda v, a: v is not None and v < a,
    "==": lambda v, a: v == a,
    "in": lambda v, a: v in a,
}


@lru_cache(maxsize=1)
def load_chain_rules() -> dict:
    data_path = resources.files("gxmimic.data").joinpath(CHAIN_RULES_FILE)
    with resources.as_file(data_path) as p:
        return json.loads(p.read_text())


def _ctx_from_fingerprint(fp: dict) -> dict:
    d = fp.get("descriptors", {})
    b = fp.get("bands", {})
    ctx = dict(d)
    ctx["bands.low"] = b.get("low")
    ctx["bands.low_mid"] = b.get("low_mid")
    ctx["bands.mid"] = b.get("mid")
    ctx["bands.presence"] = b.get("presence")
    ctx["bands.fizz"] = b.get("fizz")
    return ctx


def _match_rule(when: dict, ctx: dict) -> bool:
    for field, (op, arg) in when.items():
        val = ctx.get(field)
        if not _OPS[op](val, arg):
            return False
    return True


def _first_match(rules: list[dict], ctx: dict) -> tuple[str, str]:
    for rule in rules:
        if _match_rule(rule.get("when", {}), ctx):
            return rule["select"], json.dumps(rule.get("when", {}))
    raise ValueError("no chain rule matched (missing fallback rule in chain_rules.json)")


def select_tonestack(fp: dict, rules: dict) -> tuple[str, str]:
    gain_class = fp["descriptors"]["gain_class"]
    ctx = _ctx_from_fingerprint(fp)
    return _first_match(rules["tonestack_rules"][gain_class], ctx)


def select_cab(fp: dict, rules: dict) -> tuple[str, str]:
    ctx = _ctx_from_fingerprint(fp)
    ctx["gain_class"] = fp["descriptors"]["gain_class"]
    return _first_match(rules["cab_rules"], ctx)


def drive_seed(fp: dict) -> float:
    """Drive-axis seed from gain_class (design-contract.md section 3,
    'Model selection'):
        clean    -> 1.7 * gs
        crunch   -> 0.25 + (gs - 0.25)
        high_gain-> 0.55 + (gs - 0.55)
        extreme  -> min(1, 0.80 + (gs - 0.80))
    (the last three reduce to gs / min(1,gs) -- kept in this form to match
    the design contract's literal per-class formulas.)
    """
    gs = fp["descriptors"]["gain_score"]
    gc = fp["descriptors"]["gain_class"]
    if gc == "clean":
        seed = 1.7 * gs
    elif gc == "crunch":
        seed = 0.25 + (gs - 0.25)
    elif gc == "high_gain":
        seed = 0.55 + (gs - 0.55)
    else:  # extreme
        seed = min(1.0, 0.80 + (gs - 0.80))
    return max(0.0, min(1.0, seed))


def tonestack_prior(fp: dict, rules: dict) -> dict:
    ctx = _ctx_from_fingerprint(fp)
    out = {}
    prior = rules["tonestack_prior"]
    for knob, spec in prior.items():
        val = spec["base"] + spec["coef"] * ((ctx.get(spec["field"]) or 0.0) - spec["ref"])
        val = max(spec["lo"], min(spec["hi"], val))
        out[knob] = val
    return {
        "amp.tonestack.Bass": out["bass"],
        "amp.tonestack.Middle": out["middle"],
        "amp.tonestack.Treble": out["treble"],
    }


def build_preset(fp: dict, name: str | None = None, hint: str | None = None) -> dict:
    rules = load_chain_rules()
    gain_class = fp["descriptors"]["gain_class"]
    rt60 = fp["descriptors"].get("rt60_s")

    tonestack_select, tonestack_reason = select_tonestack(fp, rules)
    cab_select, cab_reason = select_cab(fp, rules)
    axis = drive_seed(fp)

    mono = chainmod.build_mono_chain(axis, gain_class)
    stereo = chainmod.build_stereo_chain(rt60)

    params = {}
    params.update(drivemod.interpolate(axis))
    params.update(tonestack_prior(fp, rules))

    if "stereoverb" in stereo:
        params.update({
            "stereoverb.on_off": True,
            "stereoverb.RoomSize": 0.30,
            "stereoverb.damp": 0.50,
            "stereoverb.wet_dry": 8,
        })

    rationale = [
        {"choice": f"tonestack={tonestack_select}", "because": f"gain_class={gain_class}, matched rule {tonestack_reason}"},
        {"choice": f"cab={cab_select}", "because": f"matched rule {cab_reason}"},
        {"choice": f"drive_axis={axis:.3f}", "because": f"gain_class={gain_class}, gain_score={fp['descriptors']['gain_score']:.3f}"},
        {"choice": "tube=" + str(params.get("tube.select")), "because": "drive schedule at this axis"},
    ]
    if "stereoverb" in stereo:
        rationale.append({"choice": "reverb=stereoverb on", "because": f"rt60_s={rt60:.3f} > {rules['reverb_rt60_threshold_s']}"})
    else:
        rationale.append({"choice": "reverb=off", "because": f"rt60_s={rt60} did not exceed {rules['reverb_rt60_threshold_s']}"})

    preset = {
        "schema": SCHEMA_PRESET,
        "name": name or "gx-mimic-build",
        "chain": {"mono": mono, "stereo": stereo},
        "models": {"tube": params.get("tube.select"), "tonestack": tonestack_select, "cab": cab_select},
        "drive_axis": axis,
        "params": params,
        "rationale": rationale,
        "provenance": {
            "source": "build",
            "hint": hint,
            "target_source": fp.get("source", {}),
        },
    }
    return preset
