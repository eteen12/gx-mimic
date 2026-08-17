# gx-mimic

gx-mimic matches a [Guitarix](https://guitarix.org/) preset to a reference guitar tone. You give it a song; the agent (running as a Claude Code plugin) finds guitar-only reference audio for it, and the CLI fingerprints that audio, generates a starting Guitarix preset, and iterates render → score → adjust until the preset's tone matches the reference. Once it converges, you can keep steering it by ear ("brighter," "less gain on the chorus") and it maps that feedback back onto specific knobs.

## How it works

Tone matching is spectral matching, not machine learning: a target and a render are both reduced to an LTAS (long-term average spectrum) curve — third-octave bands for human-readable reporting, Bark bands for the actual score — plus a handful of scalar descriptors (gain/drive estimate, transient tightness), and scored by weighted RMS difference between the Bark curves. Gain is estimated separately from EQ so the loop doesn't chase drive amount and frequency balance as if they were the same knob.

All of that — analysis, scoring, preset generation, rendering through Guitarix, the match loop — is a deterministic Python CLI (`gx-mimic`) with no LLM calls inside it. The only place an LLM enters is the agent driving the CLI over MCP: it decides what to search for, which candidate audio to trust, how to interpret "make it brighter," and when a match is good enough to stop. Given the same target and preset, the CLI's output doesn't vary between runs.

## Status

Working and verified against a live Guitarix 0.46 install:
- The `find-backing-track` plugin skill: search, verify, and confirm a guitar-only reference recording for a song.
- The full `analyze → build → render → score → match` loop. The end-to-end self-match test starts from a deliberately wrong preset and confirms the loop converges to at least 90/100 against a known target within its render budget.
- `session new → analyze → build → score` on real audio, not just synthetic test material: a chord riff captured from a live Guitarix instance scored against a real "When I Come Around" (Green Day) reference, with correct plain-English verdicts and actionable `suggested_tweaks` (see Example below).
- The EQ atlas the fit solver works from is a measured atlas (14 pink-noise measurements against a real Guitarix instance, ~102s), not an analytical approximation.
- `doctor --deep` empirically checks whether Guitarix's RPC can apply live knob changes on your install; on the 0.46.0+dfsg-1 build this was tested against, it can't reliably, so preset changes go through the file-based write-and-relaunch path. See [docs/DESIGN.md](docs/DESIGN.md) for the write-path detail.

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

## Example

Abbreviated, real shape of the session/analyze/build/score flow:

```
$ gx-mimic session new --song "When I Come Around" --artist "Green Day"
{"schema": "gx-mimic/session/1", "slug": "green-day-when-i-come-around", ...}

$ gx-mimic analyze reference.wav --save
{"schema": "gx-mimic/fingerprint/1",
 "descriptors": {"brightness_hz": 2340.1, "gain_score": 0.58, "gain_class": "high_gain", ...}, ...}

$ gx-mimic build --target target.json
{"schema": "gx-mimic/preset/1", "name": "green-day-when-i-come-around",
 "models": {"tonestack": "...", "cab": "..."}, "drive_axis": 0.58,
 "rationale": [{"choice": "...", "because": "..."}], ...}

$ gx-mimic analyze captured-chord-riff.wav --label render > render.json
{"schema": "gx-mimic/fingerprint/1", "descriptors": {"brightness_hz": 1610.4, "gain_score": 0.34, ...}, ...}

$ gx-mimic score --target target.json --render render.json
{"schema": "gx-mimic/score/1", "match": 21.8, "converged": false,
 "verdict": [
   "target is darker than the render (cut treble/presence)",
   "target is warmer (more low end relative to highs) than the render",
   "target has more gain/distortion than the render"
 ],
 "suggested_tweaks": {"brightness": -2, "warmth": 2, "gain": 2}}
```

`build` makes one deterministic guess with no rendering feedback, so the first score is usually far off — `match: 21.8` and the three verdicts above are the actual result of that sequence on real audio (the render step here was a chord riff captured from a live Guitarix instance, not yet the CLI's own automated `render`/`match` loop — see Status). Applying `suggested_tweaks` and re-scoring, or handing off to `gx-mimic match`, is what closes the gap from there; `match`'s convergence is proven so far on synthetic self-match material, not yet on a real reference like this one.

## Safety

Only `gx-mimic install` touches your real Guitarix config (`~/.config/guitarix`), and only when you pass `--yes`. It backs up your existing config before writing anything, and refuses to overwrite a bank it didn't create unless you also pass `--replace`. Every other command — analysis, rendering, matching — runs against an isolated Guitarix instance and never reads or writes your real config.

## Roadmap

- Replace the synthetic probe clips with real DI recordings.
- Extend the measured atlas to the tonestack and cab stages (currently EQ-only).
- List the plugin on the Claude Code marketplace.

## License

MIT — see [LICENSE](LICENSE).
