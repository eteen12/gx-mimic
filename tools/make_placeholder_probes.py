#!/usr/bin/env python3
"""Generate the placeholder DI probe clips (data/probes/{chord,chug,lead}.wav)
+ manifest.json.

THESE ARE PLACEHOLDERS. Karplus-Strong synthesis (seed 42, ported from the
JACK-injection pattern in ~/guitarix-tone-match/tone_test.py), NOT real
guitar DI recordings. Every probe is stamped `"placeholder": true` in
manifest.json so `doctor` can warn; the project owner replaces these with
real recorded DI clips later (same filenames, same nominal durations, sha256
bumped, placeholder flag cleared) -- see design-contract.md R3/section 5.

Spec (design-contract.md section 5, "Probes"):
    chord.wav 8.0s: E5,G5,A5,D5 open power chords, ring, final 2.5s
        ring-out; 0.5s lead silence, 1.5s tail -> LTAS/EQ, rt60
    chug.wav  6.0s: palm-muted low-E/A 8ths @100BPM -> gain, crest, tightness
    lead.wav  6.0s: single-note 12th-17th fret, sustained full-step bend end
        -> presence, fizz
All 48k mono f32 WAV, DI, peak -6dBFS, humbucker-bridge-ish (bright KS seed).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

FS = 48000
SEED = 42
OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "gxmimic" / "data" / "probes"
PEAK_DBFS = -6.0
PEAK_LIN = 10 ** (PEAK_DBFS / 20.0)

rng = np.random.default_rng(SEED)


# ---------------------------------------------------------------------------
# Karplus-Strong string synthesis (ported from ~/guitarix-tone-match/tone_test.py)
# ---------------------------------------------------------------------------
def ks_string(freq: float, dur: float, decay: float, level: float) -> np.ndarray:
    n = max(2, int(FS / freq))
    buf = rng.uniform(-1, 1, n)
    out = np.zeros(int(dur * FS), dtype=np.float64)
    i = 0
    for k in range(len(out)):
        nxt = decay * 0.5 * (buf[i] + buf[(i + 1) % n])
        out[k] = buf[i]
        buf[i] = nxt
        i = (i + 1) % n
    return level * out


def chord(freqs: list[float], dur: float, decay: float, level: float, stagger: float = 0.006) -> np.ndarray:
    n = int(dur * FS) + int(stagger * len(freqs) * FS) + 1
    sig = np.zeros(n)
    for j, f in enumerate(freqs):
        onset = int(j * stagger * FS)
        s = ks_string(f, dur, decay, level)
        sig[onset:onset + len(s)] += s
    return sig


def silence(dur: float) -> np.ndarray:
    return np.zeros(int(dur * FS))


def normalize_peak(x: np.ndarray, peak_lin: float = PEAK_LIN) -> np.ndarray:
    m = np.max(np.abs(x))
    if m <= 0:
        return x.astype(np.float32)
    return (x * (peak_lin / m)).astype(np.float32)


# ---------------------------------------------------------------------------
# chord.wav: E5, G5, A5, D5 power chords (root+5th+octave), ring, 2.5s ring-out
# ---------------------------------------------------------------------------
def make_chord() -> np.ndarray:
    E5 = [82.41, 123.47, 164.81]
    G5 = [98.00, 146.83, 196.00]
    A5 = [110.00, 164.81, 220.00]
    D5 = [146.83, 220.00, 293.66]

    parts = [silence(0.5)]
    for ch in (E5, G5, A5, D5):
        parts.append(chord(ch, 0.875, 0.986, 0.7))
    parts.append(chord(E5, 2.5, 0.9993, 0.75))  # final ring-out
    parts.append(silence(1.5))
    sig = np.concatenate(parts)
    target_len = int(8.0 * FS)
    if len(sig) < target_len:
        sig = np.pad(sig, (0, target_len - len(sig)))
    else:
        sig = sig[:target_len]
    return normalize_peak(sig)


# ---------------------------------------------------------------------------
# chug.wav: palm-muted low-E/A eighth notes @ 100 BPM, 6.0s
# ---------------------------------------------------------------------------
def make_chug() -> np.ndarray:
    bpm = 100.0
    eighth = 60.0 / bpm / 2.0
    low_e, a_str = 82.41, 110.00
    # a simple palm-muted riff pattern: E E E A E E A E ...
    pattern = [low_e, low_e, low_e, a_str, low_e, low_e, a_str, low_e]
    n_repeats = int(np.ceil(6.0 / (eighth * len(pattern))))
    parts = []
    for _ in range(n_repeats):
        for f in pattern:
            note = ks_string(f, eighth * 0.9, 0.90, 0.65)  # heavy damping = palm mute
            parts.append(note)
            parts.append(silence(eighth * 0.1))
    sig = np.concatenate(parts)
    target_len = int(6.0 * FS)
    if len(sig) < target_len:
        sig = np.pad(sig, (0, target_len - len(sig)))
    else:
        sig = sig[:target_len]
    return normalize_peak(sig)


# ---------------------------------------------------------------------------
# lead.wav: single sustained note, 12th-17th-fret register, full-step bend at end
# ---------------------------------------------------------------------------
def make_lead() -> np.ndarray:
    # B-string register, roughly 12th-17th fret (~494-659 Hz).
    notes = [493.88, 587.33, 659.25]  # B4->D5->E5, a short melodic run
    parts = [silence(0.3)]
    for f in notes[:-1]:
        parts.append(ks_string(f, 0.7, 0.994, 0.8))
        parts.append(silence(0.05))

    # Sustained final note that bends up a full step (2 semitones), simulated
    # as a crossfade from the held note into its bent pitch.
    base = notes[-1]
    bent = base * (2 ** (2.0 / 12.0))
    sustain_dur = 2.2
    held = ks_string(base, sustain_dur, 0.997, 0.85)
    bent_note = ks_string(bent, sustain_dur, 0.997, 0.85)
    bend_start = int(0.5 * FS)
    n = len(held)
    fade = np.ones(n)
    ramp_len = n - bend_start
    if ramp_len > 0:
        fade[bend_start:] = np.linspace(0, 1, ramp_len)
    bend_sig = held * (1 - fade) + bent_note * fade
    parts.append(bend_sig)
    parts.append(silence(1.0))

    sig = np.concatenate(parts)
    target_len = int(6.0 * FS)
    if len(sig) < target_len:
        sig = np.pad(sig, (0, target_len - len(sig)))
    else:
        sig = sig[:target_len]
    return normalize_peak(sig)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_probe(name: str, sig: np.ndarray) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    wavfile.write(str(path), FS, sig.astype(np.float32))
    data = path.read_bytes()
    peak_dbfs = 20 * np.log10(max(np.max(np.abs(sig)), 1e-12))
    return {
        "filename": name,
        "sha256": sha256_bytes(data),
        "duration_s": len(sig) / FS,
        "sample_rate": FS,
        "channels": 1,
        "peak_dbfs": float(peak_dbfs),
        "placeholder": True,
    }


def main() -> None:
    entries = {}
    entries["chord"] = write_probe("chord.wav", make_chord())
    entries["chug"] = write_probe("chug.wav", make_chug())
    entries["lead"] = write_probe("lead.wav", make_lead())

    manifest = {
        "schema": "gx-mimic/probes/1",
        "placeholder": True,
        "generated_by": "tools/make_placeholder_probes.py",
        "synthesis": "Karplus-Strong (numpy only)",
        "seed": SEED,
        "sample_rate": FS,
        "note": (
            "Synthetic DI clips, not real guitar recordings. Replace with real "
            "DI clips of the same name/nominal duration when available; bump "
            "sha256 and clear the placeholder flag."
        ),
        "files": entries,
    }
    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(entries)} probes + manifest -> {OUT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
