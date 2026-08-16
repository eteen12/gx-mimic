"""JACK server policy: auto/use-existing/dummy, per design-contract.md
section 5. Never reconfigures a JACK server that is already running; only
starts (and later stops) a dummy-driver server it started itself, recording
what it changed in jack-restore.json so `doctor --restore-jack` can put
things back.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path


def _jack_control(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["jack_control", *args], capture_output=True, text=True, timeout=10)


def jack_control_available() -> bool:
    return shutil.which("jack_control") is not None


def is_jack_running() -> bool:
    if not jack_control_available():
        return False
    res = _jack_control("status")
    return res.returncode == 0 and "started" in res.stdout.lower()


def current_driver() -> str | None:
    res = _jack_control("dd")
    if res.returncode != 0:
        return None
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    return lines[-1] if lines else None


def ensure_jack(gx_mimic_home: Path, policy: str = "auto") -> dict:
    """Make sure a JACK server is available. Returns a dict describing what
    was done: {"started_dummy": bool, "restore_file": str|None,
    "policy_used": str}."""
    restore_path = Path(gx_mimic_home) / "jack-restore.json"

    if policy == "use-existing":
        return {"started_dummy": False, "restore_file": None, "policy_used": "use-existing"}

    running = is_jack_running()
    if policy == "dummy" or (policy == "auto" and not running):
        return _start_dummy(restore_path)

    return {"started_dummy": False, "restore_file": None, "policy_used": "auto (existing server)"}


def _start_dummy(restore_path: Path) -> dict:
    prior_driver = current_driver()
    restore_info = {"prior_driver": prior_driver, "we_started": True}
    restore_path.write_text(json.dumps(restore_info, indent=2))

    _jack_control("ds", "dummy")
    _jack_control("dps", "rate", "48000")
    _jack_control("dps", "period", "1024")
    _jack_control("start")
    time.sleep(0.5)
    return {"started_dummy": True, "restore_file": str(restore_path), "policy_used": "dummy"}


def restore_jack(gx_mimic_home: Path) -> dict:
    """Undo `_start_dummy`: stop, restore the prior driver if we changed it,
    restart. Used by `doctor --restore-jack`."""
    restore_path = Path(gx_mimic_home) / "jack-restore.json"
    if not restore_path.exists():
        return {"restored": False, "reason": "no jack-restore.json record"}
    info = json.loads(restore_path.read_text())
    _jack_control("stop")
    prior = info.get("prior_driver")
    if prior:
        _jack_control("ds", prior)
        _jack_control("start")
    restore_path.unlink(missing_ok=True)
    return {"restored": True, "prior_driver": prior}
