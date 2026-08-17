# Design

This document is for contributors: why gx-mimic is built the way it is, not how to use it (see the [README](../README.md)) or what each CLI flag does (`gx-mimic <command> -h`).

## Two-stage matching: solve EQ, then bisect gain

The obvious approach — hill-climb every knob against the score until it converges — is slow (every step is a render, i.e. seconds) and unexplainable (there's no "because" to hand back to the agent for the user). gx-mimic avoids it by exploiting a structural fact about the signal chain instead.

The mono rack is fixed order: amp/pedal stack → tonestack → cab → `eqs`, with the correction EQ (`eqs`) hard-enforced as the *last* unit (`gx/chain.py` asserts this). Everything ahead of it — tube saturation, tonestack, cab simulation — is nonlinear or otherwise not representable as a simple gain-per-band map. But `eqs` itself, being last and linear, turns EQ matching into a closed-form problem: render once with `eqs` flat, take the log-domain difference between the target and that render's spectrum, and solve for the 10 band gains directly against a shipped per-band shape atlas (least-squares, bounded, `scipy.optimize.lsq_linear`). One render, no search.

Gain/drive doesn't have that property — it's a genuinely nonlinear coupling across several amp-stage parameters (tubescreamer drive, amp fuzz/highgain, preamp gains, tube type) — so it's handled separately as a 1-D monotone problem: a single scalar `drive_axis ∈ [0,1]` indexes a shipped drive schedule that maps it to concrete knob positions, and regula-falsi bisection against the target's `gain_score` finds the right axis value in about 4–5 renders.

A `match` round is: bisect `drive_axis` → one flat-EQ render + solve `eqs` → one verification render + score. A few rounds run because gain and EQ interact slightly (both move the spectrum somewhat), but each stage individually is well-conditioned, which is why a handful of rounds is enough rather than an open-ended search.

## Safety: an isolated Guitarix config

gx-mimic launches its own Guitarix process against an isolated config tree (`XDG_CONFIG_HOME=$GX_MIMIC_HOME/gxconfig`, `-n gx_mimic -N -K -J`) rather than the user's real one. Guitarix honors `XDG_CONFIG_HOME` for its config location, so this isolated instance never reads or writes `~/.config/guitarix`, and — because it's launched with a different JACK client name (`-n gx_mimic` instead of the default `gx_head`) — it coexists cleanly with a Guitarix instance the user already has open. `-J` also disables Guitarix's self-connection, including an internal link between its two JACK clients that gx-mimic has to make itself on launch (see `gx/process.py::_connect_internal_routing`).

Every measurement, render, and match round runs entirely inside this isolated tree. The **only** command that ever touches the real `~/.config/guitarix` is `gx-mimic install`, and only when the user explicitly passes `--yes`: it backs up the existing config first, writes only its own stamped bank file, refuses to overwrite a bank it didn't create (unless `--replace`), and re-reads what it wrote to verify. `--undo` restores the most recent backup. This is deliberately the one narrow crossing point between gx-mimic's sandbox and the user's actual setup.

## Write path: file, with an RPC probe for future use

Getting a preset into a running Guitarix instance can happen two ways: over its JSON-RPC socket (`set <param> <value>`, live, no restart), or by writing the preset to a bank file and the `<jack_name>_rc` file and relaunching the process. RPC is obviously preferable when it works — a restart costs several seconds a render adds up over a match loop.

`doctor --deep` empirically probes which one a given Guitarix build actually honors: it sets a continuous parameter (`amp.out_master`) over RPC and checks with a round-trip `get` whether the value actually changed, caching the result. On the installed 0.46.0+dfsg-1 build this was tested against, RPC `set` does not reliably take effect — its response is also malformed JSON regardless of parameter type (handled by patching the response before parsing; see `gx/rpc.py`). Given that, the render pipeline currently always applies preset changes through the file path (stop → write bank + rc → relaunch, `render_worker.py::_establish_engine`) rather than branching on the probed capability. The RPC path is implemented and exercised by `doctor --deep`'s probe, so it's there for a future Guitarix build (or a different install) where `set` turns out to be reliable — but nothing in the render loop currently takes that branch. Don't take "the CLI can use RPC" to mean "a given render will"; check `doctor --deep`'s `write_path` capability and the `engine.write_path` field on a render's own output if you need to know what actually happened.

## Division of labor: CLI measures, agent judges

Nothing in the CLI calls an LLM. Every subcommand is a pure function of its inputs — same target, same preset, same output, every time. That's deliberate: the things gx-mimic's CLI is good at (spectral analysis, a convex EQ solve, a monotone gain search, enforcing bounds, and a fixed table that inverts a spectral delta into "how many notches of brightness" — `suggested_tweaks`) are exactly the things that don't need judgment. What does need judgment — finding and vetting a reference recording, deciding "brighter" means +1 notch or +2, deciding a match is good enough to stop chasing, adjudicating "it matches the record" against "it sounds good to the player" when those disagree — is the agent's job, driving the CLI over MCP.

This is also why the MCP surface is deliberately smaller than the CLI surface: `score` and `fit` aren't exposed as separate MCP tools (they're folded into `render_and_score` / `match`) specifically so an agent can't invent its own unbounded render-score-adjust loop out of the primitives. `match --rounds N --budget-s S` is the one loop the CLI itself runs, and it's bounded on both axes.

## Descriptor → knob mapping

The agent never sets a raw Guitarix parameter for a subjective request. It maps a word ("brighter," "a touch less gain," "way more low end") to a *notch* — a fixed unit of movement (`+1` normal, `+2` "way," `+0.5` "a touch," clamped to `±3`) against one of ten descriptors (brightness, presence, fizz, mids, bass, warmth, gain, tightness, reverb, compression). Each descriptor's notch expands to an ordered list of concrete parameter moves — e.g. one notch of "warmer" nudges `tonestack.Bass`/`Middle` up first, then trims `eqs.fs4k`/`fs8k`, then `cab.treble`, spilling to the next move only once the current one saturates. The same table runs in reverse to produce `suggested_tweaks` from a score's spectral delta, so "what the agent should say" and "what the score already computed" are the same source of truth. The full table lives in `dsp/score.py::NOTCH_TABLE` (the deltas) and `loop/tweak.py` (the parameter moves) — it's the kind of thing that's more trustworthy read from code than duplicated here.

Some moves can't be a continuous knob nudge — e.g. "less scooped" past a certain point means switching tonestack model families, not turning a knob further. Those are returned as `suggested_structural` advice rather than applied automatically; applying one requires `tweak --allow-structural`.

## Probe clips

Three distinct probe clips exist because no single clip cleanly isolates all the descriptors gx-mimic measures — a sustained chord tells you about spectral balance and decay, but nothing about pick-attack dynamics; a palm-muted riff tells you about gain and transient tightness, but its harmonic content is too narrow for high-frequency descriptors. Each render pulls its descriptors from whichever clip measures them best (chord → LTAS/EQ/reverb time; chug → gain/crest/tightness; lead → presence/fizz — see the merge rule in `api.py::render`).

The clips currently shipped in `src/gxmimic/data/probes/` are synthetic (Karplus-Strong synthesis, `tools/make_placeholder_probes.py`), not real DI recordings — the manifest (`probes/manifest.json`) flags them `placeholder: true`, and `doctor` warns about it. They're good enough to exercise and test the whole pipeline deterministically, but a synthetic clip's spectral behavior isn't guaranteed to match a real guitar's, which is why matching accuracy on real reference audio is still unproven. See [RECORDING-PROBES.md](RECORDING-PROBES.md) for the exact spec and how to record real replacements.

## Risks worth knowing about

- **RPC reliability is unresolved, not fixed.** The file write path is the safe default *because* RPC `set` wasn't reliable on the one build this was tested against, not because RPC was ruled out structurally. If you're touching the write path, re-run `doctor --deep` against your own Guitarix build before assuming either path.
- **The two-stage matching approach depends on `eqs` staying last in the chain.** If that invariant is ever violated (a new chain rule, a reordering bug), the EQ solve's linearity assumption breaks silently — it wouldn't error, it would just produce a worse fit. `gx/chain.py` asserts the ordering; keep it that way if you touch chain-building code.
- **Placeholder probes are a real gap, not a formality.** Every test currently passes against synthetic clips. That's necessary but not sufficient evidence the descriptor math generalizes to real guitar recordings.
- **The EQ atlas and drive schedule are per-Guitarix-version artifacts.** They were measured/tuned against 0.46; a different Guitarix version could need a re-run of `gx-mimic calibrate eqs`. `fit` degrades to reporting a larger residual rather than failing outright if the atlas doesn't match reality well, but it won't tell you *why* the residual is large.
