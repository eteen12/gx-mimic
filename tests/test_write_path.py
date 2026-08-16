"""Tier 2 (pytest -m jack): design-contract.md section 9 `test_write_path`
-- THE SPIKE. Resolves the rpc-set question empirically: does an RPC `set`
call actually take effect without a restart?

design-contract.md, MECHANIC note: "run test_write_path spike FIRST before
tuning match render budgets." NOT run by the mechanic agent -- written for
the JACK-phase agent to execute, and the one test in this suite whose
outcome should be read carefully rather than just checked green, since a
future guitarix version could change the answer.

Prior research (guitarix-control.md, verified against the installed
0.46.0+dfsg-1 build): RPC `set` is a silent no-op for EVERY parameter on
this build, not just topology ones -- `get` after `set` never reflects the
change. This test re-verifies that finding live and records it, rather than
trusting the research doc blindly.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from gxmimic.dsp import io as dspio
from gxmimic.gx import process as processmod
from gxmimic.gx.rpc import RpcClient

pytestmark = pytest.mark.jack


def _capture_short(jack_name: str, sr: int = 48000, dur_s: float = 0.3):
    import jack

    di = np.zeros(int(dur_s * sr), dtype=np.float32)
    di[: int(0.05 * sr)] = 0.4  # a short burst so there's SOMETHING to measure
    total = len(di) + sr  # +1s tail

    client = jack.Client(f"gxmimic-wp-{int(time.time() * 1000) % 100000}", no_start_server=True)
    outp = client.outports.register("out")
    in_l = client.inports.register("in_l")
    rec = np.zeros(total, dtype=np.float32)
    state = {"play": 0, "rec": 0, "done": False}

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

    client.activate()
    try:
        client.connect(outp.name, f"{jack_name}_amp:in_0")
        client.connect(f"{jack_name}_fx:out_0", in_l.name)
        deadline = time.time() + 5.0
        while not state["done"] and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)
    finally:
        client.deactivate()
        client.close()

    n = state["rec"]
    captured = rec[:n]
    rms = float(np.sqrt(np.mean(captured.astype(np.float64) ** 2))) if captured.size else 0.0
    return 20.0 * np.log10(max(rms, 1e-12))


def test_rpc_set_out_master_take_effect(fake_home, jack_dummy):
    proc = processmod.launch_isolated_guitarix(fake_home, jack_name="gx_mimic")
    try:
        client = RpcClient("127.0.0.1", proc.port)
        client.connect()
        try:
            client.set("amp.out_master", -6)
            time.sleep(0.3)
            rms_quiet_setting = _capture_short("gx_mimic")

            client.set("amp.out_master", -40)
            time.sleep(0.3)
            rms_loud_setting_after_minus40 = _capture_short("gx_mimic")

            delta_db = abs(rms_quiet_setting - rms_loud_setting_after_minus40)

            # RECORD THE OUTCOME. Per guitarix-control.md this is expected
            # to be a silent no-op (delta << 20dB) on the currently
            # installed guitarix build -- if a future version fixes RPC
            # `set`, this assertion flips and doctor's write-path detection
            # (`_probe_write_path` in api.py) should be revisited to prefer
            # "rpc" by default.
            print(f"[test_write_path] amp.out_master RPC set RMS delta = {delta_db:.1f} dB")
            assert delta_db < 20.0, (
                "RPC `set` for amp.out_master now appears to take effect without a "
                "restart (delta >= 20dB) -- this CONTRADICTS guitarix-control.md's "
                "verified finding. If this is real, api.py's write-path default "
                "should change from 'file' to 'rpc'."
            )
        finally:
            client.close()
    finally:
        proc.shutdown()


def test_rpc_set_cab_on_off_does_not_take_effect(fake_home, jack_dummy):
    proc = processmod.launch_isolated_guitarix(fake_home, jack_name="gx_mimic")
    try:
        client = RpcClient("127.0.0.1", proc.port)
        client.connect()
        try:
            before = client.get("cab.on_off")
            client.set("cab.on_off", 0 if before else 1)
            time.sleep(0.3)
            after = client.get("cab.on_off")
            assert after == before, (
                "RPC `set` for cab.on_off (a topology parameter) now appears to "
                "take effect -- re-run doctor --deep write-path detection."
            )
        finally:
            client.close()
    finally:
        proc.shutdown()
