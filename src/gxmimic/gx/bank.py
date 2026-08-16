"""Read/write Guitarix's flat-array on-disk formats: bank `.gx` files and
`gx_head_rc`. Verified schemas (guitarix-control.md):

.gx bank:
    ["gx_head_file_version", [1,2,"0.46.0"], "<preset name>", {"engine":{...}},
     "<name2>", {"engine":{...}}, ...]
    (a length-4 single-preset bank is accepted by guitarix.)

gx_head_rc:
    ["gx_head_file_version", [...], "settings", {...142 keys...},
     "midi_controller", [...], "midi_ctrl_names", {...},
     "current_preset", {"engine":{...}}, "jack_connections", {...}]

This module is purely structural: it does not validate or coerce engine
values against gx/params.py. Round-trip fidelity (load -> dump reproduces
the same structure) is the module's core contract, verified against a copy
of a real bank file in tests/test_schema_roundtrip.py.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE_VERSION = [1, 2, "0.46.0"]
RC_SECTIONS = ["settings", "midi_controller", "midi_ctrl_names", "current_preset", "jack_connections"]


# ---------------------------------------------------------------------------
# Bank files
# ---------------------------------------------------------------------------
def load_bank(path) -> dict:
    """Parse a .gx bank file into {version, order:[names], presets:{name: engine_dict}}."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list) or len(data) < 4 or data[0] != "gx_head_file_version":
        raise ValueError(f"not a gx bank file: {path}")
    version = data[1]
    order: list[str] = []
    presets: dict[str, dict] = {}
    i = 2
    while i < len(data):
        name = data[i]
        wrapper = data[i + 1]
        if "engine" not in wrapper:
            raise ValueError(f"bank preset {name!r} missing 'engine' key")
        order.append(name)
        presets[name] = wrapper["engine"]
        i += 2
    return {"version": version, "order": order, "presets": presets}


def dump_bank(bank: dict) -> list:
    out: list = ["gx_head_file_version", bank.get("version", FILE_VERSION)]
    for name in bank["order"]:
        out.append(name)
        out.append({"engine": bank["presets"][name]})
    return out


def write_bank(path, bank: dict) -> None:
    Path(path).write_text(json.dumps(dump_bank(bank), indent=2))


def single_preset_bank(name: str, engine: dict, version=None) -> dict:
    return {"version": version or list(FILE_VERSION), "order": [name], "presets": {name: dict(engine)}}


# ---------------------------------------------------------------------------
# gx_head_rc
# ---------------------------------------------------------------------------
def load_rc(path) -> dict:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list) or not data or data[0] != "gx_head_file_version":
        raise ValueError(f"not a gx_head_rc file: {path}")
    version = data[1]
    sections: dict = {}
    section_order: list[str] = []
    i = 2
    while i < len(data):
        key = data[i]
        val = data[i + 1]
        sections[key] = val
        section_order.append(key)
        i += 2
    return {"version": version, "sections": sections, "section_order": section_order}


def dump_rc(rc: dict) -> list:
    out: list = ["gx_head_file_version", rc.get("version", FILE_VERSION)]
    order = rc.get("section_order") or RC_SECTIONS
    for key in order:
        out.append(key)
        out.append(rc["sections"][key])
    return out


def write_rc(path, rc: dict) -> None:
    Path(path).write_text(json.dumps(dump_rc(rc), indent=2))


def set_current_preset_engine(rc: dict, engine: dict, bank_name: str, preset_name: str) -> dict:
    """Mutate `rc` in place: mirror `engine` into current_preset and stamp
    system.current_bank/current_preset, per the file write-path (D3)."""
    rc["sections"]["current_preset"] = {"engine": dict(engine)}
    settings = rc["sections"].setdefault("settings", {})
    settings["system.current_bank"] = bank_name
    settings["system.current_preset"] = preset_name
    return rc


def new_rc(engine: dict, bank_name: str, preset_name: str, jack_connections: dict | None = None) -> dict:
    """Build a minimal gx_head_rc structure from scratch (for isolated
    XDG_CONFIG_HOME trees that have never been launched by guitarix yet)."""
    rc = {
        "version": list(FILE_VERSION),
        "section_order": list(RC_SECTIONS),
        "sections": {
            "settings": {
                "system.current_bank": bank_name,
                "system.current_preset": preset_name,
            },
            "midi_controller": [],
            "midi_ctrl_names": {},
            "current_preset": {"engine": dict(engine)},
            "jack_connections": jack_connections
            or {
                "input": ["system:capture_1", "system:capture_2"],
                "output1": ["system:playback_1"],
                "output2": ["system:playback_2"],
                "midi_input": [],
                "midi_output": [],
                "insert_out": ["%F:in_0"],
                "insert_in": ["%A:out_0"],
            },
        },
    }
    return rc
