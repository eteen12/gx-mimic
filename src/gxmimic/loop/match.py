"""`match` primitive: the bounded auto-loop (D8) tying loop/drive.py's
drive-axis solve, dsp/fit.py's EQ solve, and dsp/score.py's scoring
together into a few rounds of render -> measure -> adjust
(design-contract.md `match`).

Rendering is injected via a `render_fn(preset, clips=None, flat_eq=False) ->
render_result_dict` callback rather than imported directly, so this module
never needs to know HOW a render happens (subprocess dispatch, sessions,
JACK...) -- that's api.py's job. Keeping the control flow decoupled from
render mechanics means it's exercisable with a fake/mocked render_fn
without JACK at all, even though the project's Tier 1 suite doesn't
currently do that (see test_e2e_selfmatch.py, Tier 2, for the real thing).
"""
from __future__ import annotations

import copy
import time
from typing import Callable

from gxmimic.dsp import fit as fitmod
from gxmimic.dsp import score as scoremod
from gxmimic.gx import params as paramsmod
from gxmimic.loop import build as buildmod
from gxmimic.loop import drive as drivemod

SCHEMA_MATCH = "gx-mimic/match/1"

RenderFn = Callable[..., dict]

DRIVE_SOLVE_MAX_RENDERS = 5
LEVEL_TRIM_TARGET_PEAK_DBFS = -6.0


def run_match(target_fp: dict, render_fn: RenderFn, rounds: int = 3, budget_s: float = 300.0,
              stop_at: float = 85.0, initial_preset: dict | None = None, name: str | None = None) -> dict:
    t0 = time.time()
    preset = initial_preset or buildmod.build_preset(target_fp, name=name)
    history: list[dict] = []
    best: dict | None = None
    renders_used = 0
    rounds_run = 0

    for rnd in range(1, rounds + 1):
        if time.time() - t0 > budget_s:
            break
        rounds_run = rnd

        def measure(axis, _preset=preset):
            nonlocal renders_used
            candidate = copy.deepcopy(_preset)
            candidate["params"].update(drivemod.interpolate(axis))
            candidate["drive_axis"] = axis
            r = render_fn(candidate, clips=["chug"])
            renders_used += 1
            return r["fingerprint"]["descriptors"]["gain_score"]

        # (a) regula-falsi drive_axis solve against gain_score.
        target_gain = target_fp["descriptors"]["gain_score"]
        axis, drive_history = drivemod.solve_drive_axis(measure, target_gain, max_renders=DRIVE_SOLVE_MAX_RENDERS)
        preset["params"].update(drivemod.interpolate(axis))
        preset["drive_axis"] = axis

        # (b) one --flat-eq render -> fit.
        flat_render = render_fn(preset, flat_eq=True)
        renders_used += 1
        fit_result = fitmod.solve_eq(target_fp, flat_render["fingerprint"])
        for pid, gain in fit_result["solved"].items():
            preset["params"][pid] = gain

        # Level trim (fit's last step): nudge amp.out_master so the NEXT
        # (EQ-corrected) render peaks at -6dBFS, using the flat render's
        # measured peak as the estimate (out_master is the final linear
        # gain stage before capture, so this is a direct dB shift).
        flat_peak_dbfs = flat_render["fingerprint"].get("levels", {}).get("peak_dbfs")
        if flat_peak_dbfs is not None:
            trim_db = LEVEL_TRIM_TARGET_PEAK_DBFS - flat_peak_dbfs
            lo = paramsmod.PARAMS["amp.out_master"]["lower"]
            hi = paramsmod.PARAMS["amp.out_master"]["upper"]
            cur_om = preset["params"].get("amp.out_master", -14.0)
            preset["params"]["amp.out_master"] = max(lo, min(hi, cur_om + trim_db))

        # (c) verification render -> score.
        verify = render_fn(preset)
        renders_used += 1
        score_result = scoremod.compute_score(target_fp, verify["fingerprint"])

        history.append({
            "round": rnd, "drive_axis": axis, "drive_history": drive_history,
            "fit_residual_rms_db": fit_result["residual_rms_db"], "score": score_result,
        })
        if best is None or score_result["match"] > best["score"]["match"]:
            best = {"round": rnd, "match": score_result["match"], "preset": copy.deepcopy(preset), "score": score_result}
        if score_result["converged"] or score_result["match"] >= stop_at:
            break

    converged = bool(best and best["score"]["converged"])
    next_steps: list[str] = []
    if not converged and best:
        next_steps = [f"try tweak {k}={v}" for k, v in best["score"].get("suggested_tweaks", {}).items()]

    return {
        "schema": SCHEMA_MATCH,
        "rounds_run": rounds_run,
        "renders": renders_used,
        "elapsed_s": time.time() - t0,
        "best": best,
        "history": history,
        "converged": converged,
        "next_steps": next_steps,
    }
