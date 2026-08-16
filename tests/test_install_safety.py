"""Tier 2 (grouped with the jack-marked suite per design-contract.md
section 9, though `install` itself never touches JACK -- it's pure
filesystem safety logic): `test_install_safety`. NOT run by the mechanic
agent -- written for the JACK-phase agent to execute alongside the rest of
Tier 2.

Uses a FAKE ~/.config/guitarix (api.install's `config_dir` override) built
from the tests/fixtures/ copies of a real bank + gx_head_rc -- the real
~/.config/guitarix is never touched by this test, or by install() itself
unless config_dir is omitted (which only the real CLI does).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gxmimic import api
from gxmimic.errors import GxError
from gxmimic.gx import bank as bankmod
from gxmimic.loop import build as buildmod

pytestmark = pytest.mark.jack

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fake_gx_config(tmp_path) -> Path:
    cfg = tmp_path / "config" / "guitarix"
    banks = cfg / "banks"
    banks.mkdir(parents=True)
    shutil.copyfile(FIXTURES_DIR / "GreenDay.gx", banks / "GreenDay.gx")
    shutil.copyfile(FIXTURES_DIR / "banklist.js", banks / "banklist.js")
    shutil.copyfile(FIXTURES_DIR / "gx_head_rc", cfg / "gx_head_rc")
    return cfg


def _snapshot(cfg: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in cfg.rglob("*") if p.is_file()}


def _test_preset():
    fp = {
        "descriptors": {
            "gain_class": "crunch", "gain_score": 0.4, "brightness_hz": 2000,
            "scoop_index_db": 0.0, "presence_ratio": 0.08, "fizz_ratio": 0.02,
            "tightness": 0.5, "rolloff85_hz": 5000, "warmth_ratio_db": 0.0,
            "crest_db": 10.0, "zcr": 0.1, "flatness_4to8k": 0.1,
            "rolloff15_hz": 300, "clipping_ratio": 0.0,
        },
        "bands": {"low": 0.3, "low_mid": 0.2, "mid": 0.4, "presence": 0.08, "fizz": 0.02},
    }
    return buildmod.build_preset(fp, name="install-safety-test")


def test_no_yes_refused_and_unchanged(tmp_path):
    cfg = _fake_gx_config(tmp_path)
    before = _snapshot(cfg)

    with pytest.raises(GxError) as exc_info:
        api.install(_test_preset(), config_dir=cfg, yes=False)
    assert exc_info.value.kind == "refused"
    assert exc_info.value.exit_code == 7

    assert _snapshot(cfg) == before


def test_yes_installs_with_backup_single_bank_originals_untouched(tmp_path):
    cfg = _fake_gx_config(tmp_path)
    before_greenday = (cfg / "banks" / "GreenDay.gx").read_bytes()
    before_rc = (cfg / "gx_head_rc").read_bytes()

    result = api.install(_test_preset(), config_dir=cfg, yes=True)
    assert result["verified"] is True

    bank_file = cfg / "banks" / "gx-mimic.gx"
    assert bank_file.is_file()

    # only banks/gx-mimic.gx was added -- originals byte-identical.
    assert (cfg / "banks" / "GreenDay.gx").read_bytes() == before_greenday
    assert (cfg / "gx_head_rc").read_bytes() == before_rc

    backups = list((cfg / ".gx-mimic-backups").glob("backup-*.tar.gz"))
    assert len(backups) == 1

    banklist = json.loads((cfg / "banks" / "banklist.js").read_text())
    gxm_entries = [e for e in banklist if e[1] == "gx-mimic.gx"]
    assert len(gxm_entries) == 1


def test_undo_restores_exactly(tmp_path):
    cfg = _fake_gx_config(tmp_path)
    before = _snapshot(cfg)

    api.install(_test_preset(), config_dir=cfg, yes=True)
    assert _snapshot(cfg) != before  # sanity: install actually changed something

    api.install(preset=None, config_dir=cfg, undo=True)

    after = _snapshot(cfg)
    for key, data in before.items():
        assert after.get(key) == data, f"undo did not exactly restore {key}"


def test_unstamped_overwrite_refused(tmp_path):
    cfg = _fake_gx_config(tmp_path)
    banks = cfg / "banks"

    # Simulate a pre-existing banks/gx-mimic.gx that gx-mimic itself never
    # wrote (no `_gx_mimic` stamp in the engine dict).
    unstamped_engine = dict(bankmod.load_bank(banks / "GreenDay.gx")["presets"]["When I Come Around"])
    unstamped_bank = bankmod.single_preset_bank("gx-mimic", unstamped_engine)
    bankmod.write_bank(banks / "gx-mimic.gx", unstamped_bank)
    before = _snapshot(cfg)

    with pytest.raises(GxError) as exc_info:
        api.install(_test_preset(), config_dir=cfg, yes=True)
    assert exc_info.value.kind == "refused"

    assert _snapshot(cfg) == before


def test_replace_flag_overrides_unstamped_refusal(tmp_path):
    cfg = _fake_gx_config(tmp_path)
    banks = cfg / "banks"
    unstamped_engine = dict(bankmod.load_bank(banks / "GreenDay.gx")["presets"]["When I Come Around"])
    bankmod.write_bank(banks / "gx-mimic.gx", bankmod.single_preset_bank("gx-mimic", unstamped_engine))

    result = api.install(_test_preset(), config_dir=cfg, yes=True, replace=True)
    assert result["verified"] is True
