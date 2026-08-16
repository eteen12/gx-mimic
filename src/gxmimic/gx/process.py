"""Launch/manage isolated Guitarix engine processes.

Safety-critical module (design-contract.md section 5/6): guitarix is ALWAYS
launched with XDG_CONFIG_HOME pointed at our own isolated config tree, and
we NEVER send a signal to a process unless we have verified -- by reading
its /proc/<pid>/cmdline -- that it is a `guitarix -n gx_mimic...` process we
started. The user's own `gx_head`-named guitarix is never touched.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from gxmimic.errors import GxError

RPC_PORT_LO = 7600
RPC_PORT_HI = 7699
DEFAULT_JACK_NAME = "gx_mimic"


def find_free_port(lo: int = RPC_PORT_LO, hi: int = RPC_PORT_HI) -> int:
    for p in range(lo, hi + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise GxError("environment", f"no free RPC port in {lo}-{hi}", hint="close other gx-mimic instances")


def _is_our_process(pid: int, jack_name: str) -> bool:
    """Verify /proc/<pid>/cmdline is a `guitarix ... -n <jack_name>` we could
    plausibly have started. This is the safety gate: refuse to signal
    anything else, most importantly a `gx_head`-named user session."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    argv = [a for a in raw.split(b"\0") if a]
    argv = [a.decode(errors="replace") for a in argv]
    if not argv or "guitarix" not in argv[0]:
        return False
    if "-n" not in argv:
        return False
    idx = argv.index("-n")
    name = argv[idx + 1] if idx + 1 < len(argv) else ""
    return name == jack_name


class GxProcess:
    """A running, isolated guitarix engine instance."""

    def __init__(self, popen: subprocess.Popen, port: int, config_home: Path, jack_name: str, log_path: Path):
        self.popen = popen
        self.port = port
        self.config_home = config_home
        self.jack_name = jack_name
        self.log_path = log_path

    @property
    def pid(self) -> int:
        return self.popen.pid

    def is_running(self) -> bool:
        return self.popen.poll() is None

    def log_tail(self, n_lines: int = 40) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n_lines:])

    def terminate(self, term_wait: float = 3.0, kill_wait: float = 5.0) -> None:
        pid = self.pid
        if not _is_our_process(pid, self.jack_name):
            # Process already exited/replaced, or (should never happen) not
            # ours -- refuse to signal in the latter case.
            if self.is_running():
                raise GxError(
                    "internal",
                    f"refusing to signal pid {pid}: cmdline no longer matches a "
                    f"'{self.jack_name}'-named guitarix process",
                )
            return
        if not self.is_running():
            return
        try:
            self.popen.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + term_wait
        while time.time() < deadline and self.is_running():
            time.sleep(0.1)
        if self.is_running():
            try:
                self.popen.send_signal(signal.SIGKILL)
            except ProcessLookupError:
                return
            deadline = time.time() + kill_wait
            while time.time() < deadline and self.is_running():
                time.sleep(0.1)

    def shutdown(self, rpc_client=None) -> None:
        """Graceful shutdown: RPC shutdown (if a connected client is given)
        -> SIGTERM -> SIGKILL, per design-contract.md section 5."""
        if rpc_client is not None:
            rpc_client.shutdown()
            deadline = time.time() + 2.0
            while time.time() < deadline and self.is_running():
                time.sleep(0.1)
        self.terminate()


def launch_isolated_guitarix(
    gx_mimic_home: Path,
    port: int | None = None,
    jack_name: str = DEFAULT_JACK_NAME,
    extra_args: list[str] | None = None,
) -> GxProcess:
    """Launch `guitarix -N -K -J -n <jack_name> -p <port>` with
    XDG_CONFIG_HOME pointed at gx_mimic_home/gxconfig. Never touches
    ~/.config/guitarix."""
    gx_mimic_home = Path(gx_mimic_home)
    config_home = gx_mimic_home / "gxconfig"
    config_home.mkdir(parents=True, exist_ok=True)
    log_dir = gx_mimic_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    guitarix_bin = shutil.which("guitarix")
    if not guitarix_bin:
        raise GxError("environment", "guitarix not found on PATH", hint="install guitarix (apt install guitarix)")

    if port is None:
        port = find_free_port()

    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(config_home)

    cmd = [guitarix_bin, "-N", "-K", "-J", "-n", jack_name, "-p", str(port)]
    if extra_args:
        cmd.extend(extra_args)

    log_path = log_dir / "guitarix.log"
    logf = open(log_path, "ab")
    popen = subprocess.Popen(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT, close_fds=True)
    proc = GxProcess(popen, port, config_home, jack_name, log_path)

    try:
        _wait_for_ports(jack_name, timeout=15.0)
    except GxError:
        tail = proc.log_tail()
        proc.terminate()
        raise GxError("render", f"guitarix ports not ready within timeout", hint=tail[-2000:] if tail else None)
    return proc


def _wait_for_ports(jack_name: str, timeout: float = 15.0) -> None:
    import jack  # JACK-Client; imported lazily so import-only environments don't need libjack

    deadline = time.time() + timeout
    amp_in = f"{jack_name}_amp:in_0"
    fx_out = f"{jack_name}_fx:out_0"
    last_err = None
    while time.time() < deadline:
        try:
            c = jack.Client(f"gxmimic-portcheck-{os.getpid()}", no_start_server=True)
            names = [p.name for p in c.get_ports()]
            c.close()
            if amp_in in names and fx_out in names:
                return
        except Exception as e:  # jack.JackError or similar
            last_err = e
        time.sleep(0.1)
    raise GxError("render", f"guitarix JACK ports not found within {timeout}s ({last_err})")


def kill_stale(gx_mimic_home: Path, jack_name: str = DEFAULT_JACK_NAME) -> int:
    """Kill any leftover process recorded in sessions/*/engine.pid that is
    still verifiably a `<jack_name>`-named guitarix. Returns count killed."""
    killed = 0
    sessions_dir = Path(gx_mimic_home) / "sessions"
    if not sessions_dir.is_dir():
        return 0
    for session_dir in sessions_dir.iterdir():
        pid_file = session_dir / "engine.pid"
        if not pid_file.is_file():
            continue
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            continue
        if _is_our_process(pid, jack_name):
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except ProcessLookupError:
                pass
        pid_file.unlink(missing_ok=True)
    return killed
