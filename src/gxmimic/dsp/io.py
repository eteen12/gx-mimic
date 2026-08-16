"""Audio I/O: decode arbitrary files via ffmpeg, read/write our own 48k mono
float32 WAVs via scipy.io.wavfile (no soundfile/librosa, per D9).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from gxmimic.errors import GxError

TARGET_SR = 48000


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise GxError("environment", f"{binary} not found on PATH", hint=f"install {binary}")
    return path


def ffprobe_stream_info(path) -> dict:
    ffprobe = _require("ffprobe")
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise GxError("audio", f"ffprobe failed on {path}: {res.stderr.strip()[:400]}",
                       hint="is this a valid, readable audio file?")
    info = json.loads(res.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise GxError("audio", f"no audio stream found in {path}")
    stream = streams[0]
    sample_rate = int(stream.get("sample_rate") or TARGET_SR)
    channels = int(stream.get("channels") or 1)
    duration = float(stream.get("duration") or info.get("format", {}).get("duration") or 0.0)
    return {"sample_rate": sample_rate, "channels": channels, "duration_s": duration}


def resample_to(x: np.ndarray, orig_sr: int, target_sr: int = TARGET_SR) -> np.ndarray:
    if orig_sr == target_sr:
        return x.astype(np.float32, copy=False)
    from math import gcd
    g = gcd(int(orig_sr), int(target_sr))
    up, down = target_sr // g, orig_sr // g
    y = resample_poly(x, up, down)
    return y.astype(np.float32)


def decode_audio(path) -> tuple[np.ndarray, int, int, float]:
    """Decode any audio file: ffmpeg -> mono float32 at its native rate,
    then our own resample_poly to 48k (D9: ffmpeg decodes, we resample).
    Returns (samples_48k_mono_f32, orig_sample_rate, channels, duration_s).
    """
    path = Path(path)
    if not path.is_file():
        raise GxError("usage", f"audio file not found: {path}")
    ffmpeg = _require("ffmpeg")
    info = ffprobe_stream_info(path)
    sr, channels, duration = info["sample_rate"], info["channels"], info["duration_s"]

    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    res = subprocess.run(cmd, capture_output=True, timeout=120)
    if res.returncode != 0:
        raise GxError("audio", f"ffmpeg decode failed on {path}: {res.stderr.decode(errors='replace')[:400]}")
    raw = np.frombuffer(res.stdout, dtype="<f4")
    if raw.size == 0:
        raise GxError("audio", f"ffmpeg produced no samples for {path}")
    if duration <= 0:
        duration = len(raw) / sr

    resampled = resample_to(raw, sr, TARGET_SR)
    return resampled, sr, channels, duration


def read_wav_48k_mono_f32(path) -> np.ndarray:
    """Read one of our own 48k mono float32 probe/render WAVs. Resamples +
    downmixes defensively if given something slightly off-spec."""
    sr, data = wavfile.read(str(path))
    data = np.asarray(data)
    if data.dtype.kind == "i":
        data = data.astype(np.float32) / np.iinfo(data.dtype).max
    else:
        data = data.astype(np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.float32)
    if sr != TARGET_SR:
        data = resample_to(data, sr, TARGET_SR)
    return data


def write_wav_f32(path, samples: np.ndarray, sr: int = TARGET_SR) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sr, np.asarray(samples, dtype=np.float32))
