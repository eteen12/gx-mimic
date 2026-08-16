"""Output schema name constants + a couple of small JSON helpers shared by
cli.py, api.py and mcp_server.py. Keeping the schema strings in one place
means every producer/consumer agrees on the literal value.
"""
from __future__ import annotations

import json

SCHEMA_ERROR = "gx-mimic/error/1"
SCHEMA_DOCTOR = "gx-mimic/doctor/1"
SCHEMA_FINGERPRINT = "gx-mimic/fingerprint/1"
SCHEMA_PRESET = "gx-mimic/preset/1"
SCHEMA_RENDER = "gx-mimic/render/1"
SCHEMA_SCORE = "gx-mimic/score/1"
SCHEMA_FIT = "gx-mimic/fit/1"
SCHEMA_TWEAK = "gx-mimic/tweak/1"
SCHEMA_SET = "gx-mimic/set/1"
SCHEMA_MATCH = "gx-mimic/match/1"
SCHEMA_SESSION = "gx-mimic/session/1"
SCHEMA_TARGET = "gx-mimic/target/1"
SCHEMA_PROBES = "gx-mimic/probes/1"
SCHEMA_INSTALL = "gx-mimic/install/1"
SCHEMA_SHOW = "gx-mimic/show/1"
SCHEMA_CALIBRATE = "gx-mimic/calibrate/1"


def dumps(obj, pretty: bool = False) -> str:
    """Canonical JSON stdout serialization. Recursively sanitizes numpy
    scalars/arrays and NaN/Infinity (-> None) BEFORE dumping with
    allow_nan=False, so the result is always strict, parseable JSON."""
    clean = _sanitize(obj)
    if pretty:
        return json.dumps(clean, indent=2, sort_keys=False, allow_nan=False)
    return json.dumps(clean, sort_keys=False, allow_nan=False)


def _sanitize(o):
    import math

    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, (str, int, bool)) or o is None:
        return o
    if hasattr(o, "item"):  # numpy scalar
        return _sanitize(o.item())
    if hasattr(o, "tolist"):  # numpy array
        return _sanitize(o.tolist())
    return o
