"""Tier 1 (no JACK): gx/bank.py round-trip fidelity against a COPY of a
real bank file and a real gx_head_rc (design-contract.md section 9
`test_schema_roundtrip`). "byte-stable after dumps-normalize" is checked by
re-parsing both the original file and our dump and comparing the resulting
Python structures for deep equality (whitespace/indent differences in the
raw bytes don't matter; the parsed structure must be identical).
"""
from __future__ import annotations

import json

from gxmimic.gx import bank as bankmod


def test_bank_has_1196_keys(greenday_bank_copy):
    loaded = bankmod.load_bank(greenday_bank_copy)
    engine = loaded["presets"][loaded["order"][0]]
    assert len(engine) == 1196


def test_bank_header_intact(greenday_bank_copy):
    original = json.loads(greenday_bank_copy.read_text())
    loaded = bankmod.load_bank(greenday_bank_copy)
    assert original[0] == "gx_head_file_version"
    assert loaded["version"] == original[1]


def test_bank_jconv_nested_verbatim(greenday_bank_copy):
    loaded = bankmod.load_bank(greenday_bank_copy)
    engine = loaded["presets"][loaded["order"][0]]
    convolver = engine["jconv.convolver"]
    assert isinstance(convolver, dict)
    assert convolver["jconv.IRDir"] == "%U"
    assert "jconv.IRFile" in convolver
    assert "jconv.gainline" in convolver


def test_bank_enums_are_strings(greenday_bank_copy):
    loaded = bankmod.load_bank(greenday_bank_copy)
    engine = loaded["presets"][loaded["order"][0]]
    assert isinstance(engine["amp.tonestack.select"], str)
    assert isinstance(engine["cab.select"], str)
    assert isinstance(engine["tube.select"], str)


def test_bank_roundtrip_structural_equality(greenday_bank_copy):
    original = json.loads(greenday_bank_copy.read_text())
    loaded = bankmod.load_bank(greenday_bank_copy)
    redumped = bankmod.dump_bank(loaded)
    # dumps-normalize: reparse both sides through json to strip any
    # incidental Python-vs-JSON representation differences, then compare.
    assert json.loads(json.dumps(redumped)) == original


def test_bank_roundtrip_via_file(greenday_bank_copy, tmp_path):
    loaded = bankmod.load_bank(greenday_bank_copy)
    out_path = tmp_path / "roundtrip.gx"
    bankmod.write_bank(out_path, loaded)
    reloaded = bankmod.load_bank(out_path)
    assert reloaded == loaded


def test_single_preset_bank_accepted(tmp_path):
    engine = {"amp.on_off": 1}
    bank = bankmod.single_preset_bank("Test Preset", engine)
    out_path = tmp_path / "single.gx"
    bankmod.write_bank(out_path, bank)
    dumped = json.loads(out_path.read_text())
    assert len(dumped) == 4
    assert dumped[2] == "Test Preset"
    loaded = bankmod.load_bank(out_path)
    assert loaded["presets"]["Test Preset"] == engine


# ---------------------------------------------------------------------------
# gx_head_rc
# ---------------------------------------------------------------------------
def test_rc_six_sections(gx_head_rc_copy):
    original = json.loads(gx_head_rc_copy.read_text())
    loaded = bankmod.load_rc(gx_head_rc_copy)
    assert original[0] == "gx_head_file_version"
    assert loaded["section_order"] == ["settings", "midi_controller", "midi_ctrl_names", "current_preset", "jack_connections"]
    assert set(loaded["sections"].keys()) == set(loaded["section_order"])


def test_rc_current_preset_engine_has_1196_keys(gx_head_rc_copy):
    loaded = bankmod.load_rc(gx_head_rc_copy)
    engine = loaded["sections"]["current_preset"]["engine"]
    assert len(engine) == 1196


def test_rc_jack_connections_shape(gx_head_rc_copy):
    loaded = bankmod.load_rc(gx_head_rc_copy)
    jc = loaded["sections"]["jack_connections"]
    for key in ("input", "output1", "output2", "midi_input", "midi_output", "insert_out", "insert_in"):
        assert key in jc


def test_rc_roundtrip_structural_equality(gx_head_rc_copy):
    original = json.loads(gx_head_rc_copy.read_text())
    loaded = bankmod.load_rc(gx_head_rc_copy)
    redumped = bankmod.dump_rc(loaded)
    assert json.loads(json.dumps(redumped)) == original


def test_rc_roundtrip_via_file(gx_head_rc_copy, tmp_path):
    loaded = bankmod.load_rc(gx_head_rc_copy)
    out_path = tmp_path / "roundtrip_rc"
    bankmod.write_rc(out_path, loaded)
    reloaded = bankmod.load_rc(out_path)
    assert reloaded == loaded


def test_set_current_preset_engine_updates_settings(gx_head_rc_copy):
    loaded = bankmod.load_rc(gx_head_rc_copy)
    engine = {"amp.on_off": 1}
    bankmod.set_current_preset_engine(loaded, engine, "gx-mimic", "My Preset")
    assert loaded["sections"]["current_preset"]["engine"] == engine
    assert loaded["sections"]["settings"]["system.current_bank"] == "gx-mimic"
    assert loaded["sections"]["settings"]["system.current_preset"] == "My Preset"


def test_new_rc_minimal_structure(tmp_path):
    engine = {"amp.on_off": 1}
    rc = bankmod.new_rc(engine, "gx-mimic", "Preset")
    dumped = bankmod.dump_rc(rc)
    assert dumped[0] == "gx_head_file_version"

    # dump_rc's output must itself be loadable.
    p = tmp_path / "rc"
    p.write_text(json.dumps(dumped))
    reloaded = bankmod.load_rc(p)
    assert reloaded["sections"]["current_preset"]["engine"] == engine
