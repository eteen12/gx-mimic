#!/usr/bin/env python3
"""Generate src/gxmimic/gx/params.py from a Guitarix `parameterlist` RPC dump.

Usage:
    python tools/gen_params.py [path/to/parameterlist.json] [--out OUT.py]

If no path is given, this launches an isolated, headless guitarix instance
(same XDG-isolated pattern used at runtime), calls the `parameterlist` RPC
method, and uses that result. That path requires a real guitarix install
and JACK and is meant for regenerating params.py against a new guitarix
version -- normal development/test runs use a checked-in parameterlist.json
snapshot and never hit this branch.

The RPC dump is a flat JSON-RPC result: a list alternating
    [type_name, param_object, type_name, param_object, ...]

type_name is one of: Bool, Int, Float, Enum, FloatEnum, String, JConv, Seq, Osc.

For Enum / FloatEnum, `value_names` is a flat list of [name, description]
PAIRS -- the display names are the EVEN indices (0, 2, 4, ...).
"""
from __future__ import annotations

import argparse
import json
import pprint
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "src" / "gxmimic" / "gx" / "params.py"

# Structured / non-scalar types that are not settable through plain
# get/set knob validation. They still get an entry (for completeness /
# introspection) but are marked structured=True and have no bounds.
STRUCTURED_TYPES = {"JConv", "Seq", "Osc"}


def fetch_via_isolated_guitarix() -> dict:
    """Launch an isolated guitarix, call `parameterlist` over RPC, return result."""
    # Imported lazily -- only needed for the live-fetch path.
    sys.path.insert(0, str(HERE.parent / "src"))
    from gxmimic.gx.process import launch_isolated_guitarix
    from gxmimic.gx.rpc import RpcClient

    with tempfile.TemporaryDirectory(prefix="gxmimic-genparams-") as tmp:
        home = Path(tmp)
        proc = launch_isolated_guitarix(home, port=0)
        try:
            time.sleep(1.0)
            client = RpcClient("127.0.0.1", proc.port)
            client.connect()
            try:
                result = client.call("parameterlist", [])
            finally:
                client.close()
        finally:
            proc.shutdown()
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def load_dump(path: Path | None) -> list:
    if path is not None:
        data = json.loads(path.read_text())
    else:
        data = fetch_via_isolated_guitarix()
    result = data["result"]
    if len(result) % 2 != 0:
        raise ValueError(f"expected even-length [type, obj, ...] list, got {len(result)}")
    return result


def _param_meta(entry: dict) -> dict:
    """Pull the common {id,name,group,desc} fields out of any of the
    Parameter / IntParameter / FloatParameter wrapper shapes."""
    p = entry.get("Parameter")
    if p is None:
        p = entry.get("IntParameter", {}).get("Parameter")
    if p is None:
        p = entry.get("FloatParameter", {}).get("Parameter")
    if p is None:
        raise ValueError(f"no Parameter block found in {entry!r}")
    return p


def build_params(dump: list) -> dict:
    params: dict[str, dict] = {}
    for i in range(0, len(dump), 2):
        type_name = dump[i]
        entry = dump[i + 1]
        meta = _param_meta(entry)
        pid = meta["id"]

        rec: dict = {
            "type": type_name,
            "name": meta.get("name", ""),
            "group": meta.get("group", ""),
            "structured": type_name in STRUCTURED_TYPES,
        }

        if type_name == "Bool":
            rec["default"] = bool(entry.get("value", 0))
        elif type_name == "Int":
            rec["lower"] = entry.get("lower")
            rec["upper"] = entry.get("upper")
            rec["default"] = entry.get("value")
        elif type_name == "Float":
            rec["lower"] = entry.get("lower")
            rec["upper"] = entry.get("upper")
            rec["step"] = entry.get("step", 0.0)
            rec["default"] = entry.get("value")
        elif type_name == "Enum":
            inner = entry["IntParameter"]
            rec["lower"] = inner.get("lower")
            rec["upper"] = inner.get("upper")
            names = entry.get("value_names", [])
            rec["enum"] = list(names[0::2])
            default_idx = inner.get("value", 0)
            rec["default_index"] = default_idx
            rec["default"] = rec["enum"][default_idx] if 0 <= default_idx < len(rec["enum"]) else default_idx
        elif type_name == "FloatEnum":
            inner = entry["FloatParameter"]
            rec["lower"] = inner.get("lower")
            rec["upper"] = inner.get("upper")
            rec["step"] = inner.get("step", 0.0)
            names = entry.get("value_names", [])
            rec["enum"] = list(names[0::2])
            rec["default"] = inner.get("value")
        elif type_name == "String":
            rec["default"] = entry.get("value", "")
        elif type_name in STRUCTURED_TYPES:
            rec["default"] = entry.get("value")
        else:
            raise ValueError(f"unknown parameter type {type_name!r} for {pid}")

        params[pid] = rec
    return params


HEADER = '''"""Generated Guitarix parameter table -- DO NOT EDIT BY HAND.

Regenerate with:
    uv run python tools/gen_params.py /path/to/parameterlist.json

Source: JSON-RPC `parameterlist` dump from a live guitarix instance.
Each entry: {type, name, group, structured, lower?, upper?, step?, enum?,
default_index?, default}. `enum` values are display names (value_names
even-index entries -- odd indices are descriptions, discarded).
"""
from __future__ import annotations

TOPOLOGY_SUFFIXES = (".on_off", ".position", ".pp")

# id -> parameter metadata dict (see module docstring)
PARAMS: dict[str, dict] = __PARAMS_LITERAL__


def get(param_id: str) -> dict:
    """Return the metadata dict for `param_id`, raising KeyError if unknown."""
    return PARAMS[param_id]


def exists(param_id: str) -> bool:
    return param_id in PARAMS


def is_topology(param_id: str) -> bool:
    """True if writing this parameter requires the file write path
    (topology change: on_off / position / pp / enum select), per D3."""
    if param_id.endswith(TOPOLOGY_SUFFIXES):
        return True
    meta = PARAMS.get(param_id)
    return bool(meta and meta["type"] == "Enum")


def validate(param_id: str, value):
    """Validate + normalize `value` against the parameter's declared type
    and bounds. Returns the normalized value or raises ValueError."""
    meta = PARAMS.get(param_id)
    if meta is None:
        raise ValueError(f"unknown parameter id: {param_id!r}")
    if meta.get("structured"):
        raise ValueError(f"{param_id!r} is a structured parameter; not settable via scalar set")

    t = meta["type"]
    if t == "Bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        raise ValueError(f"{param_id!r}: expected bool, got {value!r}")
    if t == "Int":
        if not isinstance(value, (int, float)):
            raise ValueError(f"{param_id!r}: expected int, got {value!r}")
        iv = int(round(value))
        lo, hi = meta.get("lower"), meta.get("upper")
        if lo is not None and iv < lo:
            raise ValueError(f"{param_id!r}: {iv} below lower bound {lo}")
        if hi is not None and iv > hi:
            raise ValueError(f"{param_id!r}: {iv} above upper bound {hi}")
        return iv
    if t == "Float":
        if not isinstance(value, (int, float)):
            raise ValueError(f"{param_id!r}: expected float, got {value!r}")
        fv = float(value)
        lo, hi = meta.get("lower"), meta.get("upper")
        if lo is not None and fv < lo - 1e-9:
            raise ValueError(f"{param_id!r}: {fv} below lower bound {lo}")
        if hi is not None and fv > hi + 1e-9:
            raise ValueError(f"{param_id!r}: {fv} above upper bound {hi}")
        return fv
    if t == "Enum":
        names = meta.get("enum", [])
        if isinstance(value, str):
            if value not in names:
                raise ValueError(f"{param_id!r}: {value!r} not in {names}")
            return value
        if isinstance(value, (int, float)):
            iv = int(value)
            if 0 <= iv < len(names):
                return names[iv]
        raise ValueError(f"{param_id!r}: invalid enum value {value!r} (options: {names})")
    if t == "FloatEnum":
        if not isinstance(value, (int, float)):
            raise ValueError(f"{param_id!r}: expected float, got {value!r}")
        fv = float(value)
        lo, hi = meta.get("lower"), meta.get("upper")
        if lo is not None and fv < lo - 1e-9:
            raise ValueError(f"{param_id!r}: {fv} below lower bound {lo}")
        if hi is not None and fv > hi + 1e-9:
            raise ValueError(f"{param_id!r}: {fv} above upper bound {hi}")
        return fv
    if t == "String":
        if not isinstance(value, str):
            raise ValueError(f"{param_id!r}: expected str, got {value!r}")
        return value
    raise ValueError(f"{param_id!r}: cannot validate structured type {t!r}")
'''


def render_module(params: dict) -> str:
    body = pprint.pformat(params, indent=1, width=100, sort_dicts=True)
    return HEADER.replace("__PARAMS_LITERAL__", body)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dump", nargs="?", type=Path, default=None,
                     help="path to a parameterlist.json RPC dump; omit to launch isolated guitarix")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    dump = load_dump(args.dump)
    params = build_params(dump)
    module_src = render_module(params)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(module_src)
    print(f"wrote {len(params)} parameters -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
