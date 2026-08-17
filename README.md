# gx-mimic

gx-mimic matches a [Guitarix](https://guitarix.org/) preset to a reference guitar tone. You give it a song; the agent (running as a Claude Code plugin) finds guitar-only reference audio for it, and the CLI fingerprints that audio, generates a starting Guitarix preset, and iterates render → score → adjust until the preset's tone matches the reference. Once it converges, you can keep steering it by ear ("brighter," "less gain on the chorus") and it maps that feedback back onto specific knobs.

## How it works

Tone matching is spectral matching, not machine learning: a target and a render are both reduced to a third-octave LTAS (long-term average spectrum) curve plus a handful of scalar descriptors (gain/drive estimate, transient tightness), and scored by weighted RMS difference between those curves. Gain is estimated separately from EQ so the loop doesn't chase drive amount and frequency balance as if they were the same knob.

All of that — analysis, scoring, preset generation, rendering through Guitarix, the match loop — is a deterministic Python CLI (`gx-mimic`) with no LLM calls inside it. The only place an LLM enters is the agent driving the CLI over MCP: it decides what to search for, which candidate audio to trust, how to interpret "make it brighter," and when a match is good enough to stop. Given the same target and preset, the CLI's output doesn't vary between runs.

## Status

Working and verified against a live Guitarix 0.46 install:
- The `find-backing-track` plugin skill: search, verify, and confirm a guitar-only reference recording for a song.
- The full `analyze → build → render → score → match` loop. The end-to-end self-match test starts from a deliberately wrong preset and confirms the loop converges to at least 90/100 against a known target within its render budget.
- The EQ atlas the fit solver works from is a measured atlas (14 pink-noise measurements against a real Guitarix instance, ~102s), not an analytical approximation.
- The CLI's write path auto-detects what it needs: Guitarix's RPC accepts live changes for continuous parameters, but topology changes (turning a module on/off, switching amp models) require writing the preset file instead. Both paths are used as needed.

Current limitation: the probe clips shipped in `src/gxmimic/data/probes/` are synthetic (Karplus-Strong), not real DI recordings. The loop and its tests all pass against them, but matching accuracy on a real reference recording is unproven until real DI clips replace the placeholders. One test (`test_e2e_pitch_invariance_cross_clip`) is skipped by design for this reason — it arms automatically once real clips are in place.

## Requirements

- Linux
- [Guitarix](https://guitarix.org/) (tested against 0.46)
- JACK (`jackd2`)
- `ffmpeg` / `ffprobe`
- [`uv`](https://docs.astral.sh/uv/)

Run `gx-mimic doctor` after install — it checks for all of the above on `PATH` and reports what's missing.

## Install

**As a Claude Code plugin** — add this repo as a plugin source from within Claude Code:

```
/plugin install <path-or-url-to-this-repo>
```

This registers the `/gx-mimic:learn` command, the `find-backing-track` skill, and the bundled MCP server (`.mcp.json`) that exposes the CLI to the agent.

**As a CLI only:**

```
git clone <this-repo> && cd gx-mimic
uv sync
uv run gx-mimic doctor
```

## Usage

Inside Claude Code, `/gx-mimic:learn <song>` runs the whole reference-finding flow: it searches for a guitar-only recording of the song, verifies candidates, asks you to confirm one, and records the choice as a session `target.json`.

From there, the CLI does the matching. The main subcommands:

```
gx-mimic analyze <audio> --save     # fingerprint reference/render audio
gx-mimic build --target <target.json>   # generate a starting preset
gx-mimic render --preset <preset.json>  # render a preset through Guitarix, get its fingerprint
gx-mimic score --target <fp> --render <fp>  # compare two fingerprints
gx-mimic fit    # solve EQ gains from a target vs. a flat-EQ render
gx-mimic tweak --deltas '{"brightness":1}'  # apply a descriptor-level nudge
gx-mimic match  # run render/score/adjust to convergence
gx-mimic install --yes  # write the finished preset into your real Guitarix config
```

Every subcommand prints JSON to stdout (progress text goes to stderr), so the agent can drive the whole loop without parsing anything fragile. Run any subcommand with `-h` for its full options.

## Safety

Only `gx-mimic install` touches your real Guitarix config (`~/.config/guitarix`), and only when you pass `--yes`. It backs up your existing config before writing anything, and refuses to overwrite a bank it didn't create unless you also pass `--replace`. Every other command — analysis, rendering, matching — runs against an isolated Guitarix instance and never reads or writes your real config.

## Roadmap

- Replace the synthetic probe clips with real DI recordings.
- Extend the measured atlas to the tonestack and cab stages (currently EQ-only).
- List the plugin on the Claude Code marketplace.

## License

MIT — see [LICENSE](LICENSE).
