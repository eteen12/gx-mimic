# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-16

### Added
- Claude Code plugin manifest (`.claude-plugin/plugin.json`).
- `/gx-mimic:learn` command: thin wrapper that hands a song to the `find-backing-track` skill.
- `find-backing-track` skill: finds and confirms a guitar-only reference recording (and, optionally, a minus-guitar playalong track) for a song, and records the confirmed source(s) to a session `target.json`.
- Curated source reference (`skills/find-backing-track/references/sources.md`) covering official stems, the Cambridge-MT multitrack library, rhythm-game stem rips, isolated-guitar YouTube channels, and user-provided DI/stems.
- Core `gxmimic` Python package and `gx-mimic` CLI: tone fingerprinting (LTAS + descriptors), similarity scoring, Guitarix preset generation (file-based bank writer and JSON-RPC client), a JACK render worker, the descriptor-to-knob tweak engine, and the drive/EQ auto-match loop.
- `gx-mimic calibrate eqs`: measures the EQ atlas the fit solver works from against a live Guitarix instance (14 pink-noise measurements, ~102s), replacing the earlier analytical placeholder.
- README covering install, usage, and current limitations.

### Fixed
- Spectral score clamps per-band deltas beyond 18dB before the RMS, so bands near silence (where a delta carries no tonal information) can't dominate the match score.
- Guitarix's RPC `set` method returns malformed JSON on the installed 0.46.0+dfsg-1 build; the client now patches it before parsing instead of raising.
- Isolated Guitarix instances launched with `-J` don't self-connect their internal amp/fx JACK routing; gx-mimic now makes that connection itself on launch. Renders were silently capturing silence before this fix.

### Verified
- Full unit suite: 196 passed, 1 skipped, 0 failures, against live Guitarix 0.46.
- End-to-end self-match: starting from a deliberately wrong preset, the auto-match loop converges to at least 90/100 against a known target within its render budget.

### Known limitations
- Shipped probe clips (`src/gxmimic/data/probes/`) are synthetic placeholders, not real DI recordings. `test_e2e_pitch_invariance_cross_clip` is skipped for this reason and arms automatically once real clips replace them. Matching accuracy on real songs is unproven until then.
