"""`fit` primitive: the offline EQ solve behind D4 (post-amp chain is
linear => EQ matching doesn't need renders in an inner loop).

Pipeline (design-contract.md `fit`, tone-analysis.md section 4, AutoEQ-style):
    E = T - R0 on 31 third-octave centers
    -> normalize 0dB mean over 100Hz-10k
    -> freq-dependent smoothing (1/6-oct <1k ramping to 1/3-oct >6k)
    -> slope-limit 18dB/oct (L->R and R->L elementwise min)
    -> clamp +-max_boost
    -> solve min_g ||W(E - S*g)||^2 + lam*||g||^2  s.t. g in [-max_boost,max_boost]^10
       (+ cab.bass/treble in [-10,10] with include_cab_eq)
       via scipy.optimize.lsq_linear
S columns come from the shipped atlas (data/atlas-eqs-v1.npz): per-dB shapes
for each `eqs` band (+ cab.bass/treble), sampled at the 31 third-octave
centers.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

import numpy as np
from scipy.optimize import lsq_linear

SCHEMA_FIT = "gx-mimic/fit/1"
ATLAS_FILE = "atlas-eqs-v1.npz"

EQS_PARAM_IDS = [
    "eqs.fs31_25", "eqs.fs62_5", "eqs.fs125", "eqs.fs250", "eqs.fs500",
    "eqs.fs1k", "eqs.fs2k", "eqs.fs4k", "eqs.fs8k", "eqs.fs16k",
]

BOOST_DEFAULT = 12.0
CAB_LO, CAB_HI = -10.0, 10.0
NORM_RANGE = (100.0, 10000.0)
SLOPE_LIMIT_DB_PER_OCT = 18.0


@lru_cache(maxsize=1)
def load_atlas() -> dict:
    data_path = resources.files("gxmimic.data").joinpath(ATLAS_FILE)
    with resources.as_file(data_path) as p:
        npz = np.load(p, allow_pickle=False)
        meta = json.loads(str(npz["meta_json"]))
        return {
            "centers_hz": np.asarray(npz["centers_hz"], dtype=float),
            "eqs_bands_hz": np.asarray(npz["eqs_bands_hz"], dtype=float),
            "eqs_shapes_db_per_db": np.asarray(npz["eqs_shapes_db_per_db"], dtype=float),
            "cab_bass_shape_db_per_db": np.asarray(npz["cab_bass_shape_db_per_db"], dtype=float),
            "cab_treble_shape_db_per_db": np.asarray(npz["cab_treble_shape_db_per_db"], dtype=float),
            "meta": meta,
        }


def atlas_matrix(include_cab_eq: bool = False) -> tuple[np.ndarray, np.ndarray, list[str], list[float], list[float]]:
    """Returns (centers_hz, S, param_ids, lower_bounds_per_db, upper_bounds_per_db)
    where S has shape (n_centers, n_params) and columns are per-dB shapes."""
    atlas = load_atlas()
    cols = [atlas["eqs_shapes_db_per_db"][:, i] for i in range(len(EQS_PARAM_IDS))]
    param_ids = list(EQS_PARAM_IDS)
    if include_cab_eq:
        cols += [atlas["cab_bass_shape_db_per_db"], atlas["cab_treble_shape_db_per_db"]]
        param_ids += ["cab.bass", "cab.treble"]
    S = np.stack(cols, axis=1)
    return atlas["centers_hz"], S, param_ids


def _smoothing_window_oct(fc: float) -> float:
    """1/6-oct below 1kHz, ramping (linear in log2 f) to 1/3-oct above 6kHz."""
    if fc <= 1000:
        return 1.0 / 6.0
    if fc >= 6000:
        return 1.0 / 3.0
    t = (np.log2(fc) - np.log2(1000.0)) / (np.log2(6000.0) - np.log2(1000.0))
    return (1.0 / 6.0) + t * (1.0 / 3.0 - 1.0 / 6.0)


def smooth_curve(centers: np.ndarray, curve: np.ndarray) -> np.ndarray:
    log_c = np.log2(centers)
    out = np.empty_like(curve, dtype=float)
    for i, fc in enumerate(centers):
        half = _smoothing_window_oct(fc) / 2.0
        mask = np.abs(log_c - log_c[i]) <= half
        out[i] = curve[mask].mean()
    return out


def slope_limit(centers: np.ndarray, curve: np.ndarray, max_db_per_oct: float = SLOPE_LIMIT_DB_PER_OCT) -> np.ndarray:
    log_c = np.log2(centers)
    n = len(curve)
    lr = curve.astype(float).copy()
    for i in range(1, n):
        limit = max_db_per_oct * (log_c[i] - log_c[i - 1])
        lr[i] = np.clip(lr[i], lr[i - 1] - limit, lr[i - 1] + limit)
    rl = curve.astype(float).copy()
    for i in range(n - 2, -1, -1):
        limit = max_db_per_oct * (log_c[i + 1] - log_c[i])
        rl[i] = np.clip(rl[i], rl[i + 1] - limit, rl[i + 1] + limit)
    return np.minimum(lr, rl)


def _zero_mean(centers: np.ndarray, curve: np.ndarray, rng=NORM_RANGE) -> np.ndarray:
    lo, hi = rng
    mask = (centers >= lo) & (centers <= hi)
    m = curve[mask].mean() if mask.any() else 0.0
    return curve - m


def build_error_curve(centers: np.ndarray, target_db: np.ndarray, render_db: np.ndarray,
                       max_boost: float = BOOST_DEFAULT) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """AutoEQ-style E = T-R0 pipeline. Returns
    (target_norm_db, render_norm_db, error_clamped_db)."""
    t_norm = _zero_mean(centers, target_db)
    r_norm = _zero_mean(centers, render_db)
    e = t_norm - r_norm
    e = smooth_curve(centers, e)
    e = slope_limit(centers, e)
    e = _zero_mean(centers, e)
    e_clamped = np.clip(e, -max_boost, max_boost)
    return t_norm, r_norm, e_clamped


def solve_gains(centers: np.ndarray, error_db: np.ndarray, max_boost: float = BOOST_DEFAULT,
                 lam: float = 0.05, include_cab_eq: bool = False,
                 S: np.ndarray | None = None, param_ids: list[str] | None = None) -> dict:
    """Core numeric solve: min_g ||E - S g||^2 + lam ||g||^2, bounded, via
    scipy.optimize.lsq_linear (ridge term folded in as extra rows).

    The ridge penalty is applied in COLUMN-NORMALIZED space, not raw gain
    units. Reason: the shipped analytical placeholder atlas had every
    column normalized to peak magnitude ~1.0 ("per dB of gain, response at
    f0 == 1.0"), so a fixed `lam` regularized every parameter comparably.
    The real, measured atlas (`gx-mimic calibrate eqs`, see
    tools/build_eqs_atlas.py's docstring) has WILDLY different column
    magnitudes -- eqs bands peak around 0.03-0.14 dB/dB (guitarix's `eqs`
    bands run a very high default Q, so a third-octave-band LTAS measurement
    only sees a diluted sliver of each band's true, much larger, narrowband
    boost) vs cab.bass/treble shelves around 0.57-0.84 dB/dB. Against that
    atlas, an un-normalized fixed ridge term over-shrinks the low-magnitude
    eqs columns almost to zero (empirically: recovered gains near ~20% of
    the true value even at negligible noise) while barely touching the
    high-magnitude cab columns -- not a noise/SNR effect, a scale artifact
    of mixing lam's fixed absolute strength with heterogeneous column
    norms. Normalizing each column to unit peak magnitude before solving,
    then dividing the recovered coefficients back by that same per-column
    scale, makes `lam` regularize every parameter by the same RELATIVE
    amount regardless of its raw physical effectiveness."""
    if S is None or param_ids is None:
        atlas_centers, S, param_ids = atlas_matrix(include_cab_eq)
        if not np.allclose(atlas_centers, centers):
            S = np.stack([np.interp(centers, atlas_centers, S[:, i]) for i in range(S.shape[1])], axis=1)

    n_params = S.shape[1]
    # Derive bounds from the actual column count (self-consistent even if a
    # caller passes an explicit S/param_ids without also flipping
    # include_cab_eq) rather than trusting include_cab_eq alone.
    lower = np.array([-max_boost] * len(EQS_PARAM_IDS))
    upper = np.array([max_boost] * len(EQS_PARAM_IDS))
    if n_params > len(EQS_PARAM_IDS):
        lower = np.concatenate([lower, [CAB_LO] * (n_params - len(EQS_PARAM_IDS))])
        upper = np.concatenate([upper, [CAB_HI] * (n_params - len(EQS_PARAM_IDS))])

    col_scale = np.max(np.abs(S), axis=0)
    col_scale = np.where(col_scale > 1e-9, col_scale, 1.0)  # dead/all-zero column guard
    S_norm = S / col_scale

    A = np.vstack([S_norm, np.sqrt(lam) * np.eye(n_params)])
    b = np.concatenate([error_db, np.zeros(n_params)])

    res = lsq_linear(A, b, bounds=(lower * col_scale, upper * col_scale), method="bvls")
    gains = res.x / col_scale
    achieved = S @ gains
    residual = error_db - achieved

    return {
        "param_ids": param_ids,
        "gains": gains,
        "achieved": achieved,
        "residual_rms_db": float(np.sqrt(np.mean(residual ** 2))),
        "pre_fit_rms_db": float(np.sqrt(np.mean(error_db ** 2))),
    }


def solve_eq(target_fp: dict, flat_render_fp: dict, max_boost: float = BOOST_DEFAULT,
             lam: float = 0.05, include_cab_eq: bool = False) -> dict:
    atlas_centers, S, param_ids = atlas_matrix(include_cab_eq)

    t_centers = np.array(target_fp["ltas"]["third_octave"]["centers_hz"], dtype=float)
    t_db = np.array(target_fp["ltas"]["third_octave"]["db"], dtype=float)
    r_centers = np.array(flat_render_fp["ltas"]["third_octave"]["centers_hz"], dtype=float)
    r_db = np.array(flat_render_fp["ltas"]["third_octave"]["db"], dtype=float)

    if not (np.allclose(t_centers, atlas_centers) and np.allclose(r_centers, atlas_centers)):
        t_db = np.interp(atlas_centers, t_centers, t_db)
        r_db = np.interp(atlas_centers, r_centers, r_db)

    t_norm, r_norm, error_clamped = build_error_curve(atlas_centers, t_db, r_db, max_boost)

    unclamped = slope_limit(atlas_centers, smooth_curve(atlas_centers, t_norm - r_norm))
    clipped_bands = [
        {"center_hz": float(c), "requested_db": float(e), "clamped_db": float(ec)}
        for c, e, ec in zip(atlas_centers.tolist(), unclamped.tolist(), error_clamped.tolist())
        if abs(e - ec) > 1e-6
    ]

    solved_core = solve_gains(atlas_centers, error_clamped, max_boost, lam, include_cab_eq, S=S, param_ids=param_ids)
    solved = {pid: float(g) for pid, g in zip(solved_core["param_ids"], solved_core["gains"])}
    achieved_curve_db = r_norm + solved_core["achieved"]

    note = None
    if load_atlas()["meta"].get("analytical"):
        note = ("EQ shapes are ANALYTICAL placeholders (theoretical peaking-filter "
                "response), not measured against a live guitarix engine. Run "
                "`gx-mimic calibrate eqs` once a measured atlas is available.")

    return {
        "schema": SCHEMA_FIT,
        "solved": solved,
        "residual_rms_db": solved_core["residual_rms_db"],
        "pre_fit_rms_db": solved_core["pre_fit_rms_db"],
        "target_curve": {"centers_hz": atlas_centers.tolist(), "db": t_norm.tolist()},
        "achieved_curve": {"centers_hz": atlas_centers.tolist(), "db": achieved_curve_db.tolist()},
        "clipped_bands": clipped_bands,
        "note": note,
    }
