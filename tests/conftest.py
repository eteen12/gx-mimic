from __future__ import annotations

import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """An isolated $GX_MIMIC_HOME under tmp_path. Also points $HOME at a
    tmp_path subdirectory so nothing under test can ever touch the real
    ~/.config/guitarix, even by accident (design-contract.md safety rule 1)."""
    home = tmp_path / "gx-mimic-home"
    fake_user_home = tmp_path / "fake-user-home"
    fake_user_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_user_home))
    monkeypatch.setenv("GX_MIMIC_HOME", str(home))
    from gxmimic import session as sessionmod
    sessionmod.ensure_home(home)
    return home


@pytest.fixture
def greenday_bank_copy(tmp_path):
    """A tmp_path copy of the real GreenDay.gx fixture -- safe to read AND
    write in tests without ever touching the user's real bank file."""
    dest = tmp_path / "GreenDay.gx"
    shutil.copyfile(FIXTURES_DIR / "GreenDay.gx", dest)
    return dest


@pytest.fixture
def gx_head_rc_copy(tmp_path):
    dest = tmp_path / "gx_head_rc"
    shutil.copyfile(FIXTURES_DIR / "gx_head_rc", dest)
    return dest


@pytest.fixture(scope="session")
def probes_dir():
    return Path(resources.files("gxmimic.data").joinpath("probes"))


@pytest.fixture(scope="session")
def chord_wav(probes_dir):
    return probes_dir / "chord.wav"


@pytest.fixture(scope="session")
def chug_wav(probes_dir):
    return probes_dir / "chug.wav"


@pytest.fixture(scope="session")
def lead_wav(probes_dir):
    return probes_dir / "lead.wav"


# ---------------------------------------------------------------------------
# Tier 2 (jack-marked) fixture: switch JACK to a dummy driver for the
# duration of the test, restoring whatever was configured before (e.g.
# alsa/hw:CI1) even if the test fails. Skipped entirely if jack_control
# isn't available.
# ---------------------------------------------------------------------------
@pytest.fixture
def jack_dummy():
    jack_control = shutil.which("jack_control")
    if not jack_control:
        pytest.skip("jack_control not available")

    def run(*args):
        return subprocess.run([jack_control, *args], capture_output=True, text=True, timeout=10)

    dg = run("dg")
    dp = run("dp")
    prior_driver = dg.stdout.strip().splitlines()[-1] if dg.returncode == 0 and dg.stdout.strip() else None
    prior_params = dp.stdout

    run("stop")
    run("ds", "dummy")
    run("dps", "rate", "48000")
    run("dps", "period", "1024")
    run("start")
    try:
        yield {"prior_driver": prior_driver, "prior_params": prior_params}
    finally:
        run("stop")
        if prior_driver:
            run("ds", prior_driver)
        run("start")
