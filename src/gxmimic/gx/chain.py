"""Chain assembly rules (design-contract.md section 3, "Rack"):

    Rack (mono): ampstack -> amp.tonestack (pp post) -> cab -> eqs (pp post,
    LAST -- linearity depends on it, hard-enforce). ts9sim prepended (pp
    pre) when drive_axis>=0.30. expander prepended when hg/extreme.
    Stereo: stereoverb only when rt60>0.25.

"ampstack" in the contract text refers to the amp drive stage as a whole
(guitarix modules `amp` + `tube` + `amp2`, which always travel together and
are switched on/off via `amp.on_off`) -- there is no literal guitarix module
named "ampstack". This module treats "ampstack" as an alias for `amp` for
positioning purposes; preset.py is responsible for also flipping
`tube`/`amp2` state alongside `amp.on_off`.

`eqs` must always be last in the mono chain: it is the correction EQ the
whole linearity assumption behind `fit` depends on (D4/R2).
"""
from __future__ import annotations

AMP_ALIAS = "amp"  # what "ampstack" in the design contract maps onto
MONO_CORE = [AMP_ALIAS, "amp.tonestack", "cab", "eqs"]

# pp (pre/post amp) hints for every unit chain.py can place.
_PP = {
    "ts9sim": "pre",
    "expander": "pre",
    AMP_ALIAS: "pre",
    "amp.tonestack": "post",
    "cab": "post",
    "eqs": "post",
    "stereoverb": "post",
}


def build_mono_chain(drive_axis: float, gain_class: str) -> list[str]:
    """Return the ordered list of mono-rack unit ids for this preset.
    `eqs` is always last (hard-enforced, see module docstring)."""
    chain: list[str] = []
    if drive_axis >= 0.30:
        chain.append("ts9sim")
    if gain_class in ("high_gain", "extreme"):
        chain.append("expander")
    chain.extend(MONO_CORE)
    assert chain[-1] == "eqs", "eqs must be last in the mono chain (linearity invariant)"
    return chain


def build_stereo_chain(rt60_s: float | None) -> list[str]:
    return ["stereoverb"] if rt60_s is not None and rt60_s > 0.25 else []


def positions_for(chain: list[str], base: int = 0) -> dict[str, dict]:
    """Assign sequential .position ints (in signal-flow / chain order) and
    .pp values for every unit in `chain`. Absolute position values only need
    to preserve relative order -- guitarix sorts units within a rack by
    position."""
    out = {}
    for i, unit in enumerate(chain):
        out[unit] = {"position": base + i, "pp": _PP.get(unit, "post")}
    return out
