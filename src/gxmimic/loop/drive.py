"""Gain/drive matching (D5): drive_axis is a single scalar in [0,1] that
interpolates a shipped schedule (data/drive_schedule.json) coordinating
ts9sim, amp.fuzz, amp.highgain, amp2 pregains and tube.select. Finding the
axis that matches a target gain_score is a 1-D monotone regula-falsi
bisection over that axis, capped at a small number of renders.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Callable

SCHEDULE_FILE = "drive_schedule.json"


@lru_cache(maxsize=1)
def load_schedule() -> dict:
    data_path = resources.files("gxmimic.data").joinpath(SCHEDULE_FILE)
    with resources.as_file(data_path) as p:
        return json.loads(p.read_text())


def _interp_continuous(axis: float, points: list[float], values: list[float]) -> float:
    if axis <= points[0]:
        return values[0]
    if axis >= points[-1]:
        return values[-1]
    for i in range(len(points) - 1):
        x0, x1 = points[i], points[i + 1]
        if x0 <= axis <= x1:
            if x1 == x0:
                return values[i]
            t = (axis - x0) / (x1 - x0)
            return values[i] + t * (values[i + 1] - values[i])
    return values[-1]


def _nearest_below(axis: float, points: list[float], values: list) -> object:
    idx = 0
    for i, x in enumerate(points):
        if x <= axis + 1e-9:
            idx = i
    return values[idx]


def interpolate(axis: float) -> dict:
    """Return the concrete param dict for a given drive_axis in [0,1]."""
    axis = max(0.0, min(1.0, axis))
    sched = load_schedule()
    points = sched["axis_points"]
    out = {}
    for pid, values in sched["continuous"].items():
        out[pid] = _interp_continuous(axis, points, values)
    for pid, values in sched["step"].items():
        out[pid] = _nearest_below(axis, points, values)
    return out


def seed_out_master(axis: float) -> float:
    return interpolate(axis)["amp.out_master"]


def solve_drive_axis(measure_fn: Callable[[float], float], target_gain_score: float,
                      lo: float = 0.0, hi: float = 1.0, tol: float = 0.04,
                      max_renders: int = 5) -> tuple[float, list[dict]]:
    """1-D regula-falsi bisection for drive_axis vs gain_score (D5).
    `measure_fn(axis) -> gain_score`, assumed monotone non-decreasing in
    axis. Bounded to at most `max_renders` calls to `measure_fn` total
    (each call is one real render in the caller)."""
    history: list[dict] = []

    def m(x: float) -> float:
        x = max(0.0, min(1.0, x))
        v = measure_fn(x)
        history.append({"drive_axis": x, "gain_score": v})
        return v

    f_lo = m(lo) - target_gain_score
    if abs(f_lo) <= tol or len(history) >= max_renders:
        return lo, history

    f_hi = m(hi) - target_gain_score
    if abs(f_hi) <= tol:
        return hi, history
    if f_lo > 0:  # even the least-driven point overshoots the target
        return lo, history
    if f_hi < 0:  # even the most-driven point undershoots the target
        return hi, history

    a, b, fa, fb = lo, hi, f_lo, f_hi
    x = a
    while len(history) < max_renders:
        denom = (fb - fa)
        x = a - fa * (b - a) / denom if denom != 0 else (a + b) / 2.0
        x = max(0.0, min(1.0, x))
        fx = m(x) - target_gain_score
        if abs(fx) <= tol:
            return x, history
        if fx * fa > 0:
            a, fa = x, fx
        else:
            b, fb = x, fx
    return x, history
