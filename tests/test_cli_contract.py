"""Tier 1 (no JACK): CLI contract (design-contract.md section 9
`test_cli_contract`): --help exits 0, documented error paths hit their
documented exit codes, and stdout is always valid JSON (or, for argparse's
own usage errors, empty -- argparse writes its own text to stderr and never
touches stdout).
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

CLI = [sys.executable, "-m", "gxmimic.cli"]


def run_cli(args, env=None):
    return subprocess.run(CLI + args, capture_output=True, text=True, timeout=60, env=env)


TOP_LEVEL_SUBCOMMANDS = [
    "doctor", "analyze", "build", "render", "score", "fit", "tweak", "set",
    "match", "show", "install", "session", "probes", "calibrate",
]


def test_top_level_help_exit_0():
    proc = run_cli(["--help"])
    assert proc.returncode == 0
    assert "gx-mimic" in proc.stdout


@pytest.mark.parametrize("subcommand", TOP_LEVEL_SUBCOMMANDS)
def test_subcommand_help_exit_0(subcommand):
    proc = run_cli([subcommand, "--help"])
    assert proc.returncode == 0, proc.stderr


def test_stdout_is_valid_json_on_success(fake_home):
    proc = run_cli(["doctor"])
    assert proc.returncode in (0, 3)
    data = json.loads(proc.stdout)
    assert data["schema"] == "gx-mimic/doctor/1"


def test_stdout_is_valid_json_on_gx_error(fake_home):
    proc = run_cli(["analyze", "/no/such/file.wav"])
    assert proc.returncode == 2  # usage
    data = json.loads(proc.stdout)
    assert data["schema"] == "gx-mimic/error/1"
    assert data["kind"] == "usage"
    assert data["exit_code"] == 2


def test_analyze_missing_file_is_usage_error(fake_home):
    proc = run_cli(["analyze", "/tmp/definitely-not-a-real-file-gxmimic.wav"])
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["kind"] == "usage"


def test_build_missing_target_file_is_usage_error(fake_home):
    proc = run_cli(["build", "--target", "/tmp/does-not-exist-gxmimic.json"])
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["kind"] == "usage"


def test_tweak_unknown_descriptor_is_usage_error(fake_home, tmp_path):
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps({"schema": "gx-mimic/preset/1", "params": {}, "chain": {"mono": [], "stereo": []}}))
    proc = run_cli(["tweak", "--preset", str(preset_path), "--deltas", '{"not_a_real_descriptor": 1}'])
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["kind"] == "usage"


def test_tweak_malformed_deltas_json_is_usage_error(fake_home, tmp_path):
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps({"schema": "gx-mimic/preset/1", "params": {}, "chain": {"mono": [], "stereo": []}}))
    proc = run_cli(["tweak", "--preset", str(preset_path), "--deltas", "{not valid json"])
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["kind"] == "usage"


def test_set_unknown_param_is_usage_error(fake_home, tmp_path):
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps({"schema": "gx-mimic/preset/1", "params": {}, "chain": {"mono": [], "stereo": []}}))
    proc = run_cli(["set", "--preset", str(preset_path), "--params", '{"totally.bogus.param": 1}'])
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["kind"] == "usage"


def test_missing_required_arg_exits_2_with_empty_stdout(fake_home):
    # argparse's own usage errors: exit 2, but they never touch stdout
    # (all of argparse's usage text goes to stderr) so there's nothing to
    # assert is valid JSON here -- just that stdout is empty.
    proc = run_cli(["build"])  # missing required --target
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""


def test_install_without_yes_is_refused(fake_home, tmp_path):
    preset_path = tmp_path / "preset.json"
    preset_path.write_text(json.dumps({"schema": "gx-mimic/preset/1", "params": {}, "chain": {"mono": [], "stereo": []}}))
    proc = run_cli(["install", "--preset", str(preset_path)])
    assert proc.returncode == 7
    data = json.loads(proc.stdout)
    assert data["kind"] == "refused"


def test_analyze_and_build_end_to_end(fake_home, chord_wav):
    proc = run_cli(["analyze", str(chord_wav)])
    assert proc.returncode == 0, proc.stderr
    fp = json.loads(proc.stdout)
    assert fp["schema"] == "gx-mimic/fingerprint/1"

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(fp, f)
        fp_file = f.name

    proc2 = run_cli(["build", "--target", fp_file])
    assert proc2.returncode == 0, proc2.stderr
    preset = json.loads(proc2.stdout)
    assert preset["schema"] == "gx-mimic/preset/1"


def test_score_on_identical_fingerprints(fake_home, chord_wav, tmp_path):
    proc = run_cli(["analyze", str(chord_wav)])
    fp = json.loads(proc.stdout)
    fp_path = tmp_path / "fp.json"
    fp_path.write_text(json.dumps(fp))

    proc2 = run_cli(["score", "--target", str(fp_path), "--render", str(fp_path)])
    assert proc2.returncode == 0, proc2.stderr
    result = json.loads(proc2.stdout)
    assert result["match"] == pytest.approx(100.0, abs=0.01)
    assert result["converged"] is True
