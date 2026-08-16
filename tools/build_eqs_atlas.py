#!/usr/bin/env python3
"""Build data/atlas-eqs-v1.npz: the shape atlas `fit` solves against.

THIS IS A PLACEHOLDER. Real calibration (`gx-mimic calibrate eqs`) renders a
2s pink-noise burst per `eqs` band setting through an isolated live
guitarix and measures the actual magnitude response. Until that pass has
run, this script generates an ANALYTICAL atlas: each `eqs` band's shape is
the theoretical peaking-filter magnitude response (Q~=1.2), and cab.bass /
cab.treble are gentle shelves at 250Hz / 3kHz -- all sampled at the 31
third-octave centers, normalized to "per dB of gain" so `fit` can scale
them linearly. The embedded metadata is stamped analytical=true so
`doctor`/`fit` can warn while it's in use, and `calibrate` can overwrite it
with a measured atlas of the same schema.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from gxmimic.dsp.ltas import THIRD_OCTAVE_CENTERS  # noqa: E402

EQS_BANDS_HZ = [31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQS_PARAM_IDS = [
    "eqs.fs31_25", "eqs.fs62_5", "eqs.fs125", "eqs.fs250", "eqs.fs500",
    "eqs.fs1k", "eqs.fs2k", "eqs.fs4k", "eqs.fs8k", "eqs.fs16k",
]
Q = 1.2
SHELF_ORDER = 2.0
CAB_BASS_HZ = 250.0
CAB_TREBLE_HZ = 3000.0

OUT_PATH = HERE.parent / "src" / "gxmimic" / "data" / "atlas-eqs-v1.npz"


def peaking_shape_db_per_db(centers: np.ndarray, f0: float, q: float = Q) -> np.ndarray:
    """Constant-Q peaking-filter magnitude approximation, normalized so the
    response at f0 equals exactly 1.0 (i.e. "per dB of gain")."""
    ratio = centers / f0 - f0 / centers
    return 1.0 / np.sqrt(1.0 + (q * ratio) ** 2)


def low_shelf_shape_db_per_db(centers: np.ndarray, f0: float, n: float = SHELF_ORDER) -> np.ndarray:
    return 1.0 / (1.0 + (centers / f0) ** n)


def high_shelf_shape_db_per_db(centers: np.ndarray, f0: float, n: float = SHELF_ORDER) -> np.ndarray:
    return 1.0 / (1.0 + (f0 / centers) ** n)


def main() -> None:
    centers = np.array(THIRD_OCTAVE_CENTERS, dtype=float)
    eqs_shapes = np.stack([peaking_shape_db_per_db(centers, f0) for f0 in EQS_BANDS_HZ], axis=1)  # (31, 10)
    cab_bass = low_shelf_shape_db_per_db(centers, CAB_BASS_HZ)
    cab_treble = high_shelf_shape_db_per_db(centers, CAB_TREBLE_HZ)

    meta = {
        "analytical": True,
        "version": "v1",
        "gx_version": "0.46.0",
        "method": "theoretical peaking filter (Q=1.2) / gentle shelf (n=2) magnitude response, per dB of gain",
        "centers_hz": THIRD_OCTAVE_CENTERS,
        "eqs_bands_hz": EQS_BANDS_HZ,
        "eqs_param_ids": EQS_PARAM_IDS,
        "cab_bass_hz": CAB_BASS_HZ,
        "cab_treble_hz": CAB_TREBLE_HZ,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_PATH,
        centers_hz=centers,
        eqs_bands_hz=np.array(EQS_BANDS_HZ, dtype=float),
        eqs_shapes_db_per_db=eqs_shapes,
        cab_bass_shape_db_per_db=cab_bass,
        cab_treble_shape_db_per_db=cab_treble,
        meta_json=np.array(json.dumps(meta)),
    )
    print(f"wrote {OUT_PATH} (analytical placeholder atlas)", file=sys.stderr)


if __name__ == "__main__":
    main()
