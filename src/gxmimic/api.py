"""Contract layer: pure dict-in/dict-out functions. cli.py and
mcp_server.py both call into this module -- neither implements any logic of
its own beyond argument parsing / MCP tool wiring.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import numpy as np

from gxmimic import session as sessionmod
from gxmimic.dsp import fingerprint as fpmod
from gxmimic.dsp import fit as fitmod
from gxmimic.dsp import io as dspio
from gxmimic.dsp import score as scoremod
from gxmimic.errors import GxError
from gxmimic.gx import bank as bankmod
from gxmimic.gx import chain as chainmod
from gxmimic.gx import jackenv as jackenvmod
from gxmimic.gx import params as paramsmod
from gxmimic.gx import preset as gxpreset
from gxmimic.gx import process as processmod
from gxmimic.loop import build as buildmod
from gxmimic.loop import match as matchmod
from gxmimic.loop import tweak as tweakmod

TOOL_VERSION = "0.1.0"
SCHEMA_DOCTOR = "gx-mimic/doctor/1"
SCHEMA_RENDER = "gx-mimic/render/1"
SCHEMA_MATCH = "gx-mimic/match/1"
SCHEMA_SHOW = "gx-mimic/show/1"
SCHEMA_INSTALL = "gx-mimic/install/1"
SCHEMA_SET = "gx-mimic/set/1"
SCHEMA_PROBES = "gx-mimic/probes/1"
SCHEMA_CALIBRATE = "gx-mimic/calibrate/1"

DEFAULT_CLIPS = ("chord", "chug", "lead")
# Merge rule (design-contract.md `render`): each descriptor from its most
# diagnostic clip.
MERGE_SOURCE = {
    "brightness_hz": "chord", "rolloff15_hz": "chord", "rolloff85_hz": "chord",
    "warmth_ratio_db": "chord", "scoop_index_db": "chord", "rt60_s": "chord",
    "gain_score": "chug", "gain_class": "chug", "crest_db": "chug",
    "flatness_4to8k": "chug", "zcr": "chug", "tightness": "chug",
    "presence_ratio": "lead", "fizz_ratio": "lead",
    "clipping_ratio": "chug",
}


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def _probes_dir() -> Path:
    from importlib import resources
    return Path(resources.files("gxmimic.data").joinpath("probes"))


def doctor(home: Path, deep: bool = False, restore_jack: bool = False) -> dict:
    home = Path(home)
    sessionmod.ensure_home(home)
    checks = []
    blocking = []
    warnings = []

    def add(name, ok, detail, is_blocking=False):
        status = "ok" if ok else ("fail" if is_blocking else "warn")
        checks.append({"name": name, "status": status, "detail": detail})
        if not ok and is_blocking:
            blocking.append(f"{name}: {detail}")
        elif not ok:
            warnings.append(f"{name}: {detail}")

    guitarix_bin = shutil.which("guitarix")
    add("guitarix", bool(guitarix_bin), guitarix_bin or "guitarix not found on PATH", is_blocking=True)

    ffmpeg_bin, ffprobe_bin = shutil.which("ffmpeg"), shutil.which("ffprobe")
    add("ffmpeg", bool(ffmpeg_bin and ffprobe_bin),
        "found" if (ffmpeg_bin and ffprobe_bin) else "ffmpeg/ffprobe not found on PATH", is_blocking=True)

    try:
        import jack  # noqa: F401
        deps_ok, deps_detail = True, "numpy/scipy/JACK-Client/mcp import OK"
    except ImportError as e:
        deps_ok, deps_detail = False, f"python deps missing: {e}"
    add("python_deps", deps_ok, deps_detail, is_blocking=True)

    libjack = shutil.which("jackd") or shutil.which("jackdbus") or shutil.which("jack_control")
    add("libjack", bool(libjack), libjack or "no jack server binary found on PATH", is_blocking=True)

    jack_running = jackenvmod.is_jack_running()
    add("jack_server", jack_running, "running" if jack_running else "not running (doctor --deep can start a dummy driver)")

    config_home = home / "gxconfig"
    try:
        config_home.mkdir(parents=True, exist_ok=True)
        probe_file = config_home / ".write_probe"
        probe_file.write_text("ok")
        probe_file.unlink()
        add("isolated_config", True, str(config_home))
    except OSError as e:
        add("isolated_config", False, f"cannot write isolated config tree: {e}", is_blocking=True)

    probes_dir = _probes_dir()
    manifest_path = probes_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        missing = [f["filename"] for f in manifest["files"].values() if not (probes_dir / f["filename"]).is_file()]
        if missing:
            add("probes", False, f"missing probe files: {missing}", is_blocking=True)
        else:
            placeholder = manifest.get("placeholder", False)
            add("probes", not placeholder, "placeholder synthetic probes in use (not real DI recordings)" if placeholder else "real probes")
    else:
        add("probes", False, "no probes manifest found", is_blocking=True)

    try:
        atlas = fitmod.load_atlas()
        analytical = atlas["meta"].get("analytical", False)
        add("atlas", not analytical, "analytical placeholder atlas in use (run `gx-mimic calibrate eqs` once available)" if analytical else "measured atlas")
    except (FileNotFoundError, OSError) as e:
        add("atlas", False, f"atlas not found: {e}", is_blocking=True)

    capabilities = {"write_path": "unknown", "jack_policy": "auto", "sample_rate": 48000}
    cap_path = home / "capabilities.json"
    if cap_path.is_file():
        try:
            capabilities.update(json.loads(cap_path.read_text()))
        except json.JSONDecodeError:
            pass

    if deep:
        try:
            deep_caps = _probe_write_path(home, restore_jack=restore_jack)
            capabilities.update(deep_caps)
            cap_path.write_text(json.dumps(capabilities, indent=2))
            add("write_path", True, f"detected write_path={capabilities['write_path']}")
        except GxError as e:
            add("write_path", False, f"deep probe failed: {e.message}", is_blocking=True)
    else:
        add("write_path", capabilities["write_path"] != "unknown",
            capabilities["write_path"] if capabilities["write_path"] != "unknown" else "unknown (run `doctor --deep`)")

    if restore_jack and not deep:
        jackenvmod.restore_jack(home)

    return {
        "schema": SCHEMA_DOCTOR,
        "checks": checks,
        "capabilities": capabilities,
        "blocking": blocking,
        "warnings": warnings,
    }


def _probe_write_path(home: Path, restore_jack: bool = False) -> dict:
    jack_info = jackenvmod.ensure_jack(home, policy="auto")
    try:
        proc = processmod.launch_isolated_guitarix(home, jack_name="gx_mimic")
    except GxError:
        raise
    try:
        client = rpc_connect(proc.port)
        try:
            before = client.get("amp.out_master")
            client.set("amp.out_master", -40)
            time.sleep(0.2)
            after = client.get("amp.out_master")
            rpc_took_effect = _extract_scalar(after) is not None and abs(_extract_scalar(after) - (-40)) < 1.0
        finally:
            client.close()
        write_path = "rpc" if rpc_took_effect else "file"
    finally:
        proc.shutdown()
        if jack_info.get("started_dummy") and restore_jack:
            jackenvmod.restore_jack(home)
    return {"write_path": write_path, "sample_rate": 48000}


def _extract_scalar(rpc_get_result):
    if isinstance(rpc_get_result, dict):
        for v in rpc_get_result.values():
            return v
    return rpc_get_result


def rpc_connect(port: int, host: str = "127.0.0.1"):
    from gxmimic.gx.rpc import RpcClient
    client = RpcClient(host, port)
    client.connect()
    return client


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
def analyze(path: str, start: float | None = None, duration: float | None = None,
            label: str = "reference", save: bool = False, session: str | None = None,
            home: Path | None = None) -> dict:
    home = Path(home) if home else sessionmod.resolve_home()
    fp = fpmod.analyze_file(path, start=start, duration=duration, label=label)

    cache_dir = home / "cache" / "fingerprints"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sha = fp["source"]["sha256"]
    if sha:
        (cache_dir / f"{sha}.json").write_text(json.dumps(fp, indent=2, default=str))

    if save:
        slug = sessionmod.resolve_session_slug(home, session)
        sdir = sessionmod.session_dir(home, slug)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "target.json").write_text(json.dumps(fp, indent=2, default=str))
        samples, *_ = dspio.decode_audio(path)
        if start:
            samples = samples[int(start * fpmod.TARGET_SR):]
        if duration:
            samples = samples[: int(duration * fpmod.TARGET_SR)]
        dspio.write_wav_f32(sdir / "target-48k.wav", samples, fpmod.TARGET_SR)
    return fp


def load_fingerprint_arg(target: str, home: Path) -> dict:
    """`--target <fp|audio>`: accept a path to a fingerprint JSON, or an
    audio file (analyzed on the spot)."""
    p = Path(target)
    if p.is_file() and p.suffix == ".json":
        data = json.loads(p.read_text())
        if data.get("schema") == fpmod.SCHEMA_FINGERPRINT:
            return data
    return analyze(target, home=home)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build(target: str, name: str | None = None, hint: str | None = None,
          session: str | None = None, home: Path | None = None) -> dict:
    home = Path(home) if home else sessionmod.resolve_home()
    fp = load_fingerprint_arg(target, home)
    preset = buildmod.build_preset(fp, name=name, hint=hint)
    if session:
        slug = sessionmod.resolve_session_slug(home, session)
        sdir = sessionmod.session_dir(home, slug)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / "preset.json").write_text(json.dumps(preset, indent=2, default=str))
        (sdir / "target.json").write_text(json.dumps(fp, indent=2, default=str))
    return preset


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def render(preset: dict, home: Path, session: str | None = None, clips: list[str] | None = None,
           flat_eq: bool = False, no_reverb: bool = False, write_path: str = "auto",
           jack_policy: str = "auto", keep_alive: bool = False,
           timeout_s: float = 120.0) -> dict:
    home = Path(home)
    sessionmod.ensure_home(home)
    clips = clips or list(DEFAULT_CLIPS)

    render_preset = json.loads(json.dumps(preset))  # deep copy
    if flat_eq:
        for pid in list(render_preset.get("params", {})):
            if pid.startswith("eqs.fs"):
                render_preset["params"][pid] = 0.0
    if no_reverb:
        render_preset.get("params", {})["stereoverb.on_off"] = False
        render_preset["chain"]["stereo"] = [u for u in render_preset["chain"].get("stereo", []) if u != "stereoverb"]

    jackenvmod.ensure_jack(home, policy=jack_policy)

    probes_dir = _probes_dir()
    manifest = json.loads((probes_dir / "manifest.json").read_text())
    clip_paths = {}
    for c in clips:
        info = manifest["files"].get(c)
        if not info:
            raise GxError("usage", f"unknown clip {c!r}", hint=f"available: {list(manifest['files'])}")
        clip_paths[c] = str(probes_dir / info["filename"])

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if session:
        slug = sessionmod.resolve_session_slug(home, session)
        out_dir = sessionmod.session_dir(home, slug) / "renders" / ts
    else:
        out_dir = home / "cache" / "renders" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    job = {
        "gx_mimic_home": str(home),
        "jack_name": "gx_mimic",
        "write_path": write_path,
        "preset": render_preset,
        "clips": clip_paths,
        "sample_rate": 48000,
        "keep_alive": keep_alive,
        "session_slug": session or "_unknown",
        "tool_version": TOOL_VERSION,
        "out_dir": str(out_dir),
        "result_path": str(out_dir / "result.json"),
    }
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, indent=2))

    proc = subprocess.run(
        [sys.executable, "-m", "gxmimic.render_worker", str(job_path)],
        capture_output=True, text=True, timeout=timeout_s,
    )
    result_path = Path(job["result_path"])
    if not result_path.is_file():
        raise GxError("render", f"render_worker produced no result (exit {proc.returncode}): {proc.stderr[-2000:]}")
    result = json.loads(result_path.read_text())
    if not result.get("ok"):
        err = result.get("error") or {}
        raise GxError(err.get("kind", "render"), err.get("message", "render failed"), hint=err.get("hint"))

    clip_fps = {}
    for c, info in result["clips"].items():
        clip_fps[c] = fpmod.analyze_file(info["wav"], label="render")

    merged_descriptors = {}
    merged_bands = {}
    for key, src in MERGE_SOURCE.items():
        fp = clip_fps.get(src) or next(iter(clip_fps.values()))
        merged_descriptors[key] = fp["descriptors"].get(key)
    reference_fp = clip_fps.get("chord") or next(iter(clip_fps.values()))
    merged = json.loads(json.dumps(reference_fp))
    merged["descriptors"].update(merged_descriptors)
    merged["label"] = "render"

    return {
        "schema": SCHEMA_RENDER,
        "clips": {c: {"wav": clip_fps[c]["source"]["path"], "fingerprint": clip_fps[c]} for c in clip_fps},
        "fingerprint": merged,
        "engine": result["engine"],
        "jack": result["jack"],
        "warnings": result.get("warnings", []),
    }


# ---------------------------------------------------------------------------
# score / fit / tweak / set
# ---------------------------------------------------------------------------
def score(target_fp: dict, render_fp: dict, weights: dict | None = None) -> dict:
    return scoremod.compute_score(target_fp, render_fp, weights=weights)


def fit(target_fp: dict, flat_render_fp: dict, max_boost: float = 12.0, lam: float = 0.05,
        include_cab_eq: bool = False) -> dict:
    return fitmod.solve_eq(target_fp, flat_render_fp, max_boost=max_boost, lam=lam, include_cab_eq=include_cab_eq)


def tweak(preset: dict, deltas: dict, dry_run: bool = False, allow_structural: bool = False) -> dict:
    return tweakmod.apply_tweaks(preset, deltas, allow_structural=allow_structural)


def set_params(preset: dict, params: dict, force: bool = False) -> dict:
    preset = json.loads(json.dumps(preset))
    applied = {}
    topology_changed = False
    for pid, value in params.items():
        if not force and not paramsmod.exists(pid):
            raise GxError("usage", f"unknown parameter id: {pid!r}")
        if paramsmod.exists(pid) and not paramsmod.PARAMS[pid].get("structured"):
            value = paramsmod.validate(pid, value)
        preset.setdefault("params", {})[pid] = value
        applied[pid] = value
        if paramsmod.exists(pid) and paramsmod.is_topology(pid):
            topology_changed = True
    return {"schema": SCHEMA_SET, "applied": applied, "topology_changed": topology_changed, "preset": preset}


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------
def show(preset: dict, format: str = "json", vs: dict | None = None) -> dict:
    lines = [f"preset: {preset.get('name')}", f"drive_axis: {preset.get('drive_axis')}"]
    models = preset.get("models", {})
    lines.append(f"models: tube={models.get('tube')} tonestack={models.get('tonestack')} cab={models.get('cab')}")
    lines.append(f"chain(mono): {' -> '.join(preset.get('chain', {}).get('mono', []))}")
    lines.append(f"chain(stereo): {' -> '.join(preset.get('chain', {}).get('stereo', []))}")
    diff = None
    if vs:
        diff = {}
        a, b = preset.get("params", {}), vs.get("params", {})
        for k in set(a) | set(b):
            if a.get(k) != b.get(k):
                diff[k] = {"this": a.get(k), "vs": b.get(k)}
    return {"schema": SCHEMA_SHOW, "summary": "\n".join(lines), "preset": preset, "diff": diff}


# ---------------------------------------------------------------------------
# match
# ---------------------------------------------------------------------------
def match(target_fp: dict, home: Path, session: str | None = None, rounds: int = 3,
          budget_s: float = 300.0, stop_at: float = 85.0, initial_preset: dict | None = None,
          name: str | None = None) -> dict:
    """Thin wrapper: the control-flow lives in loop/match.py (kept
    JACK-agnostic there); this just injects the real render() as the
    callback loop/match.py drives."""
    home = Path(home)

    def render_fn(preset, clips=None, flat_eq=False):
        return render(preset, home, session=session, clips=clips, flat_eq=flat_eq, keep_alive=False)

    return matchmod.run_match(target_fp, render_fn, rounds=rounds, budget_s=budget_s,
                               stop_at=stop_at, initial_preset=initial_preset, name=name)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
REAL_GX_CONFIG = Path.home() / ".config" / "guitarix"


def _gx_head_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-af", "guitarix"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in out.stdout.splitlines():
        if "-n" in line and "gx_mimic" in line:
            continue  # our own isolated instance
        if "guitarix" in line:
            return True
    return False


def install(preset: dict, bank: str = "gx-mimic", preset_name: str | None = None,
            yes: bool = False, replace: bool = False, undo: bool = False,
            config_dir: Path | None = None) -> dict:
    gx_config = Path(config_dir) if config_dir else REAL_GX_CONFIG
    banks_dir = gx_config / "banks"
    backups_dir = gx_config / ".gx-mimic-backups"

    if undo:
        return _install_undo(gx_config, backups_dir)

    if not yes:
        raise GxError("refused", "install requires --yes (this writes to your real Guitarix config)")
    if _gx_head_running():
        raise GxError("refused", "guitarix (gx_head) appears to be running; close it before installing")

    bank_file = banks_dir / f"{bank}.gx"
    if bank_file.is_file() and not replace:
        try:
            existing = bankmod.load_bank(bank_file)
            first_engine = next(iter(existing["presets"].values()))
            if not gxpreset.is_stamped(first_engine):
                raise GxError("refused", f"refusing to overwrite unstamped bank file {bank_file}", hint="pass --replace to force")
        except (json.JSONDecodeError, ValueError):
            raise GxError("refused", f"refusing to overwrite unreadable bank file {bank_file}", hint="pass --replace to force")

    banks_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)
    _backup_config(gx_config, backups_dir)

    preset_name = preset_name or preset.get("name", "gx-mimic")
    engine = gxpreset.to_engine_dict(preset)
    engine = gxpreset.stamp_ownership(engine, TOOL_VERSION, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    ir_ref = engine.get("jconv.convolver", {}).get("jconv.IRFile") if isinstance(engine.get("jconv.convolver"), dict) else None
    if ir_ref and engine.get("jconv.on_off"):
        _copy_ir(gx_config, ir_ref, preset_name)

    new_bank = bankmod.single_preset_bank(preset_name, engine)
    bankmod.write_bank(bank_file, new_bank)

    banklist_path = banks_dir / "banklist.js"
    banklist = json.loads(banklist_path.read_text()) if banklist_path.is_file() else []
    banklist = [e for e in banklist if e[1] != f"{bank}.gx"]
    mtime = int(bank_file.stat().st_mtime)
    banklist.insert(0, [bank, f"{bank}.gx", 0, 0, [1, 2], mtime])
    banklist_path.write_text(json.dumps(banklist, indent=2))

    verify = bankmod.load_bank(bank_file)
    if preset_name not in verify["presets"]:
        raise GxError("internal", "install verification failed: preset not found after write")

    return {
        "schema": SCHEMA_INSTALL,
        "bank": bank, "preset": preset_name, "bank_file": str(bank_file),
        "backup": True, "verified": True,
    }


def _backup_config(gx_config: Path, backups_dir: Path) -> Path | None:
    banks_dir = gx_config / "banks"
    rc_path = gx_config / "gx_head_rc"
    if not banks_dir.is_dir() and not rc_path.is_file():
        return None
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tar_path = backups_dir / f"backup-{ts}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        if banks_dir.is_dir():
            tar.add(banks_dir, arcname="banks")
        if rc_path.is_file():
            tar.add(rc_path, arcname="gx_head_rc")
    backups = sorted(backups_dir.glob("backup-*.tar.gz"))
    for old in backups[:-10]:
        old.unlink(missing_ok=True)
    return tar_path


def _copy_ir(gx_config: Path, ir_filename: str, preset_name: str) -> None:
    ir_dir = gx_config / "IR"
    ir_dir.mkdir(parents=True, exist_ok=True)
    src_candidates = list(ir_dir.glob(ir_filename)) or glob.glob(f"/usr/share/gx_head/sounds/amps/{ir_filename}")
    if not src_candidates:
        return
    src = Path(src_candidates[0])
    safe_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in preset_name)
    dest = ir_dir / f"gx-mimic-{safe_name}.wav"
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)


def _install_undo(gx_config: Path, backups_dir: Path) -> dict:
    backups = sorted(backups_dir.glob("backup-*.tar.gz"))
    if not backups:
        raise GxError("usage", "no gx-mimic backup found to restore")
    latest = backups[-1]
    with tarfile.open(latest, "r:gz") as tar:
        tar.extractall(gx_config)
    return {"schema": SCHEMA_INSTALL, "undo": True, "restored_from": str(latest)}


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def session_new(home: Path, song: str, artist: str = "", slug: str | None = None) -> dict:
    return sessionmod.new_session(home, song, artist, slug)


def session_list(home: Path) -> list[dict]:
    return sessionmod.list_sessions(home)


def session_show(home: Path, slug: str) -> dict:
    return sessionmod.load_session(home, slug)


def session_use(home: Path, slug: str) -> dict:
    return sessionmod.use_session(home, slug)


def session_delete(home: Path, slug: str) -> None:
    sessionmod.delete_session(home, slug)


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------
def probes_list() -> dict:
    manifest = json.loads((_probes_dir() / "manifest.json").read_text())
    return {"schema": SCHEMA_PROBES, **manifest}


def probes_validate() -> dict:
    probes_dir = _probes_dir()
    manifest = json.loads((probes_dir / "manifest.json").read_text())
    results = {}
    for name, info in manifest["files"].items():
        path = probes_dir / info["filename"]
        ok = path.is_file()
        detail = {}
        if ok:
            actual_sha = dspio.sha256_file(path)
            samples = dspio.read_wav_48k_mono_f32(path)
            detail = {
                "sha256_match": actual_sha == info["sha256"],
                "duration_s": len(samples) / 48000,
                "peak_dbfs": float(20 * np.log10(max(np.max(np.abs(samples)), 1e-12))),
            }
        results[name] = {"exists": ok, **detail}
    return {"schema": SCHEMA_PROBES, "placeholder": manifest.get("placeholder", False), "results": results}


def probes_use(name: str, wav_path: str) -> dict:
    probes_dir = _probes_dir()
    manifest_path = probes_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if name not in manifest["files"]:
        raise GxError("usage", f"unknown probe name {name!r}", hint=f"available: {list(manifest['files'])}")
    samples = dspio.read_wav_48k_mono_f32(wav_path)
    dest = probes_dir / manifest["files"][name]["filename"]
    dspio.write_wav_f32(dest, samples, 48000)
    manifest["files"][name] = {
        "filename": manifest["files"][name]["filename"],
        "sha256": dspio.sha256_file(dest),
        "duration_s": len(samples) / 48000,
        "sample_rate": 48000, "channels": 1,
        "peak_dbfs": float(20 * np.log10(max(np.max(np.abs(samples)), 1e-12))),
        "placeholder": False,
    }
    manifest["placeholder"] = any(f.get("placeholder") for f in manifest["files"].values())
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return {"schema": SCHEMA_PROBES, "updated": name, "path": str(dest)}


# ---------------------------------------------------------------------------
# calibrate (maintainer-only; needs live guitarix -- exercised in Phase 3)
# ---------------------------------------------------------------------------
def calibrate(target: str, home: Path, out: str | None = None) -> dict:
    raise GxError("environment", f"calibrate {target!r} requires a live guitarix + JACK session",
                  hint="this is a maintainer command; run it interactively once JACK is available")
