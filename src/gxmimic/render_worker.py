"""The ONLY module in gx-mimic that imports `jack` / talks to a running
guitarix over JACK for actual audio capture. Runs as a standalone
subprocess (`python -m gxmimic.render_worker <job.json>`), spawned by
api.py's render step, so a JACK crash or audio-thread bug can never take
down the parent CLI/MCP server process (D10).

Job file (JSON, written by the caller) -> result file (JSON, written here).
See `JobSpec`/result shape in the module docstring below the imports.

Write path (D3): guitarix-control.md's empirical finding on the installed
0.46.0 build is that RPC `set` is a silent no-op for EVERY parameter, not
just topology ones -- so the file write path (stop -> write bank + rc ->
relaunch) is the one this module actually exercises by default. The RPC
path is still implemented (a future guitarix version may fix `set`, and
`doctor --deep` is what empirically decides which one `render` uses,
caching the result in capabilities.json) but is best-effort: if it's
selected and turns out not to have taken effect, that's exactly the
`test_write_path` spike's job to catch, not something this module can
verify on its own without a round-trip `get`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from gxmimic.dsp import io as dspio
from gxmimic.errors import GxError
from gxmimic.gx import bank as bankmod
from gxmimic.gx import preset as gxpreset
from gxmimic.gx import process as processmod
from gxmimic.gx import rpc as rpcmod

RESULT_SCHEMA = "gx-mimic/render_worker_result/1"


def _write_result(result_path: str, result: dict) -> None:
    Path(result_path).write_text(json.dumps(result, indent=2, default=str))


def _capture_clip(client_name: str, jack_name: str, di: "np.ndarray", sr: int,
                   tail_s: float = 1.5) -> tuple["np.ndarray", int]:
    """Play `di` into `<jack_name>_amp:in_0`, capture `<jack_name>_fx:out_0`.
    Ported from ~/guitarix-tone-match/tone_test.py's JACK client pattern.
    Returns (captured_samples, xrun_count)."""
    import jack
    import numpy as np

    total = len(di) + int(tail_s * sr)
    client = jack.Client(client_name, no_start_server=True)
    if client.samplerate != sr:
        # We don't reconfigure the server; render at whatever rate it's
        # actually running and let the caller resample the capture.
        sr = int(client.samplerate)
        total = len(di) + int(tail_s * sr)

    outp = client.outports.register("out")
    in_l = client.inports.register("in_l")

    rec = np.zeros(total + sr, dtype=np.float32)
    state = {"play": 0, "rec": 0, "done": False, "xruns": 0}

    @client.set_process_callback
    def process(frames):
        p = state["play"]
        chunk = di[p:p + frames]
        buf = outp.get_array()
        buf[:] = 0
        buf[:len(chunk)] = chunk
        state["play"] = p + frames
        r = state["rec"]
        if r + frames <= rec.shape[0]:
            rec[r:r + frames] = in_l.get_array()
            state["rec"] = r + frames
        if state["play"] >= total:
            state["done"] = True

    @client.set_xrun_callback
    def xrun(delay):
        state["xruns"] += 1

    client.activate()
    try:
        amp_in = f"{jack_name}_amp:in_0"
        fx_out = f"{jack_name}_fx:out_0"
        client.connect(outp.name, amp_in)
        client.connect(fx_out, in_l.name)

        deadline = time.time() + max(10.0, len(di) / sr + tail_s + 5.0)
        while not state["done"] and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.15)
    finally:
        client.deactivate()
        client.close()

    n = state["rec"]
    return rec[:n].astype(np.float32), state["xruns"]


def _establish_engine(job: dict) -> tuple["processmod.GxProcess", int, bool]:
    """Ensure a guitarix engine is running with `job['preset']` loaded, via
    the file write path (stop -> write bank+rc -> relaunch). Returns
    (proc, restarts, topology_changed)."""
    home = Path(job["gx_mimic_home"])
    jack_name = job.get("jack_name", "gx_mimic")
    preset = job["preset"]

    engine = gxpreset.to_engine_dict(preset)
    engine = gxpreset.stamp_ownership(engine, job.get("tool_version", "0.1.0"),
                                       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    config_home = home / "gxconfig"
    gx_dir = config_home / "guitarix"
    banks_dir = gx_dir / "banks"
    banks_dir.mkdir(parents=True, exist_ok=True)

    preset_name = preset.get("name", "gx-mimic-render")
    bank_name = "gx-mimic-work"
    bank = bankmod.single_preset_bank(preset_name, engine)
    bankmod.write_bank(banks_dir / f"{bank_name}.gx", bank)

    banklist_path = banks_dir / "banklist.js"
    banklist = []
    if banklist_path.is_file():
        try:
            banklist = json.loads(banklist_path.read_text())
        except json.JSONDecodeError:
            banklist = []
    banklist = [e for e in banklist if e[1] != f"{bank_name}.gx"]
    mtime = int(time.time())
    banklist.insert(0, [bank_name, f"{bank_name}.gx", 0, 0, [1, 2], mtime])
    banklist_path.write_text(json.dumps(banklist))

    rc_path = gx_dir / "gx_head_rc"
    if rc_path.is_file():
        try:
            rc = bankmod.load_rc(rc_path)
        except (json.JSONDecodeError, ValueError):
            rc = bankmod.new_rc(engine, bank_name, preset_name)
    else:
        rc = bankmod.new_rc(engine, bank_name, preset_name)
    bankmod.set_current_preset_engine(rc, engine, bank_name, preset_name)
    bankmod.write_rc(rc_path, rc)

    proc = processmod.launch_isolated_guitarix(home, jack_name=jack_name)
    return proc, 1, True


def run_job(job: dict) -> dict:
    import numpy as np

    home = Path(job["gx_mimic_home"])
    jack_name = job.get("jack_name", "gx_mimic")
    xrun_warn = job.get("xrun_warn", 5)
    xrun_fail = job.get("xrun_fail", 50)
    sr = job.get("sample_rate", 48000)

    proc = None
    restarts = 0
    topology_changed = False
    total_xruns = 0
    clip_results = {}
    warnings = []

    try:
        proc, restarts, topology_changed = _establish_engine(job)

        out_dir = Path(job["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)

        for clip_name, wav_path in job["clips"].items():
            di = dspio.read_wav_48k_mono_f32(wav_path)
            if proc is None:
                raise GxError("render", "guitarix engine not running")
            client_sr = sr
            captured, xruns = _capture_clip(f"gxmimic-cap-{clip_name}-{proc.pid}", jack_name, di, client_sr)
            total_xruns += xruns
            if xruns > xrun_warn:
                warnings.append(f"{clip_name}: {xruns} xruns during capture")

            captured_48k = dspio.resample_to(captured, client_sr, 48000) if client_sr != 48000 else captured
            out_wav = out_dir / f"{clip_name}.wav"
            dspio.write_wav_f32(out_wav, captured_48k, 48000)

            rms = float(np.sqrt(np.mean(captured_48k.astype(np.float64) ** 2))) if captured_48k.size else 0.0
            rms_dbfs = 20.0 * np.log10(max(rms, 1e-12))
            if rms_dbfs < -70.0:
                raise GxError("render", f"silent render on clip {clip_name!r} (RMS {rms_dbfs:.1f} dBFS)")

            clip_results[clip_name] = {"wav": str(out_wav), "rms_dbfs": rms_dbfs, "xruns": xruns}

        if total_xruns > xrun_fail:
            raise GxError("render", f"too many xruns during render ({total_xruns} > {xrun_fail})")

        result = {
            "schema": RESULT_SCHEMA,
            "ok": True,
            "error": None,
            "clips": clip_results,
            "engine": {
                "write_path": "file",
                "topology_changed": topology_changed,
                "guitarix_pid": proc.pid,
                "restarts": restarts,
                "rpc_port": proc.port,
            },
            "jack": {"sample_rate": sr, "xruns": total_xruns},
            "warnings": warnings,
        }
        return result
    except GxError as e:
        tail = proc.log_tail() if proc is not None else None
        return {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "error": e.to_json(),
            "clips": clip_results,
            "engine": {"guitarix_log_tail": tail},
            "jack": {"xruns": total_xruns},
            "warnings": warnings,
        }
    except Exception as e:  # noqa: BLE001 - convert anything unexpected into a structured result
        tail = proc.log_tail() if proc is not None else None
        err = GxError("render", f"render worker crashed: {e!r}")
        return {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "error": err.to_json(),
            "clips": clip_results,
            "engine": {"guitarix_log_tail": tail},
            "jack": {"xruns": total_xruns},
            "warnings": warnings,
        }
    finally:
        if proc is not None and not job.get("keep_alive", False):
            rpc_client = None
            try:
                rpc_client = rpcmod.RpcClient("127.0.0.1", proc.port, timeout=2.0)
                rpc_client.connect()
            except OSError:
                rpc_client = None
            proc.shutdown(rpc_client)
            if rpc_client is not None:
                rpc_client.close()
        elif proc is not None:
            pid_file = home / "sessions" / job.get("session_slug", "_unknown") / "engine.pid"
            try:
                pid_file.parent.mkdir(parents=True, exist_ok=True)
                pid_file.write_text(str(proc.pid))
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m gxmimic.render_worker <job.json>", file=sys.stderr)
        return 2
    job_path = argv[0]
    job = json.loads(Path(job_path).read_text())
    result = run_job(job)
    _write_result(job["result_path"], result)
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
