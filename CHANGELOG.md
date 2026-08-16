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

### In progress
- Core `gxmimic` Python package and `gx-mimic` CLI (tone analysis, preset building, Guitarix rendering, matching loop) — not yet shipped in this release.
