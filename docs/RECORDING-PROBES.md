# Recording real probe clips

gx-mimic ships with three synthetic probe clips (`src/gxmimic/data/probes/{chord,chug,lead}.wav`) so the whole pipeline is testable without a guitar in hand. They're Karplus-Strong synthesis, not real recordings, and the manifest flags them `placeholder: true`. This is a guide to replacing them with real DI recordings — the single biggest thing standing between gx-mimic and proven-accurate matching on real songs (see [DESIGN.md](DESIGN.md#probe-clips)).

## What to record

Three clips, each targeting a different set of descriptors (see DESIGN.md for why one clip can't cover all of them):

| Clip | Length | Content |
|---|---|---|
| `chord.wav` | 8.0s | Open power chords, in order: E5, G5, A5, D5. Let each ring, and let the final chord ring out for the last 2.5s of the clip (no muting). |
| `chug.wav` | 6.0s | Palm-muted low-E and A string eighth notes, ~100 BPM. |
| `lead.wav` | 6.0s | A single-note phrase on the 12th–17th frets, ending in a sustained full-step bend held to the end of the clip. |

All three, no exceptions:
- **DI, dry.** No amp sim, no cab sim, no reverb/delay/compression — a clean electrical signal straight from the guitar. gx-mimic supplies the amp; the probe is the input to it.
- **Humbucker, bridge pickup.**
- **48kHz, mono, 32-bit float (or 16/24-bit PCM) WAV.**
- **Peak level −6dBFS.** Not louder, not much quieter — this is what the analysis pipeline is normalized against.
- **0.5s of leading silence** before the first note, so onset detection has a clean reference point.

## Recording them on Linux

Any audio interface JACK (or PipeWire's JACK-compatible layer) can see will work. Roughly:

1. Plug the guitar into a USB interface's instrument input (DI — no pedals, no amp in the signal path).
2. Make sure JACK (or PipeWire) is running at 48kHz: `jack_control ds` / `pw-metadata -n settings` to check, or just start with `jack_control start` if nothing's running.
3. Capture with whichever tool matches your setup:

   ```bash
   # jackd2 / a2jmidid-style setup
   jack_capture --channels 1 --filename chord.wav
   # ...play the part, then Ctrl-C to stop...

   # PipeWire (pw-record speaks JACK's graph directly)
   pw-record --channels=1 --rate=48000 chord.wav
   ```

   Repeat for `chug.wav` and `lead.wav`. It's easier to record a few extra seconds and trim than to nail the exact timing live.

4. Trim, pad, and level with `ffmpeg` (or `sox`, or an editor if you prefer a GUI):

   ```bash
   # trim/pad to exact length and add leading silence, then normalize peak to -6dBFS
   ffmpeg -i raw_chord.wav -af "adelay=500|500,apad=whole_dur=8.0" -ar 48000 -ac 1 chord_padded.wav
   ffmpeg -i chord_padded.wav -af "volume=$(ffmpeg -i chord_padded.wav -af volumedetect -f null - 2>&1 | grep max_volume | awk '{print -6-$5}')dB" -ar 48000 -ac 1 chord.wav
   ```

   (The two-pass volume step is fiddly by hand — `sox chord_padded.wav chord.wav gain -n -6` does the same normalization in one command if you have SoX installed.) Check the result: `ffprobe chord.wav` for duration/rate/channels, `sox chord.wav -n stat` (or `ffmpeg -i chord.wav -af volumedetect -f null -`) for peak level.

## Installing the clips

From the repo root, with `gx-mimic` on your PATH (or `uv run gx-mimic`):

```bash
gx-mimic probes use chord chord.wav
gx-mimic probes use chug chug.wav
gx-mimic probes use lead lead.wav
```

`probes use` copies the file into `src/gxmimic/data/probes/<name>.wav`, recomputes its sha256/duration/peak level, sets that clip's `placeholder` flag to `false` in `manifest.json`, and updates the manifest's top-level `placeholder` flag once all three are real. Don't hand-edit `manifest.json` or the WAV files directly — this is the path that keeps the manifest's recorded metadata (sha256 especially) honest.

Then sanity-check:

```bash
gx-mimic probes validate
```

This confirms each file exists, its sha256 matches what the manifest recorded, and reports its actual duration and peak level — a last check that nothing got re-encoded or clipped on the way in.

## What this unlocks

`tests/test_e2e_selfmatch.py::test_e2e_pitch_invariance_cross_clip` is skipped whenever the probe manifest reports `placeholder: true` — the synthetic clips are near-pure harmonic combs with silence between partials, so their spectral envelopes genuinely differ across clip type in a way real broadband guitar audio doesn't, and the test can't demonstrate anything real against them. Once all three clips are real and `manifest.json`'s `placeholder` flag is `false` (which `probes use` handles for you), that test runs for real: it renders the same preset against two different real probe clips and checks the resulting spectral match is still high, which is the actual claim that gx-mimic's fingerprinting is pitch/content-invariant rather than an artifact of the synthetic clips.
