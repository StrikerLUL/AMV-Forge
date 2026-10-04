"""Baut einen künstlichen EDM-Song mit bekannter Struktur, damit die Song-Analyse testbar ist.

128 BPM, 4/4, 36 Takte (67,5 s):
  Takt  0- 8  Intro      (Fläche, leise Hi-Hats)
  Takt  8-16  Verse      (Kick, Bass, Hi-Hats, Akkorde)
  Takt 16-20  Build-up   (Snare-Wirbel wird dichter, Rauschen steigt, keine Kick)
  Takt 20-28  Drop       (laute Kick, Sub-Bass, Clap, Lead)
  Takt 28-32  Breakdown  (nur Fläche)
  Takt 32-36  Outro      (leise Kick, Fläche)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 22050
BPM = 128.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT
SECTIONS = [("intro", 0, 8), ("verse", 8, 16), ("buildup", 16, 20), ("drop", 20, 28), ("breakdown", 28, 32), ("outro", 32, 36)]
DROP_TIME = 20 * BAR
TOTAL_BARS = 36

# Am F C G, Grundtöne in Hz
CHORDS = [(110.0, 130.81, 164.81), (87.31, 110.0, 130.81), (130.81, 164.81, 196.0), (98.0, 123.47, 146.83)]


def _env(n: int, decay: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (decay * SR))


def _kick() -> np.ndarray:
    n = int(0.3 * SR)
    t = np.arange(n) / SR
    freq = 45 + 75 * np.exp(-t / 0.04)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(n, 0.12)
    # kurzer Klick am Anfang, wie bei echten Kicks
    click = np.random.default_rng(1).standard_normal(n) * _env(n, 0.004) * 0.6
    return body + click


def _noise_hit(rng: np.random.Generator, length: float, decay: float, bright: bool) -> np.ndarray:
    n = int(length * SR)
    noise = rng.standard_normal(n)
    if bright:
        noise = np.diff(noise, prepend=0.0)
    return noise * _env(n, decay) * 0.5


def _tone(freq: float, length: float, harmonics: int = 1) -> np.ndarray:
    t = np.arange(int(length * SR)) / SR
    out = sum(np.sin(2 * np.pi * freq * h * t) / h for h in range(1, harmonics + 1))
    fade = np.minimum(1.0, np.minimum(t / 0.01, (length - t) / 0.02))
    return out * np.clip(fade, 0.0, 1.0)


def _add(track: np.ndarray, sound: np.ndarray, at: float, gain: float) -> None:
    i = int(round(at * SR))
    j = min(len(track), i + len(sound))
    if i < len(track):
        track[i:j] += gain * sound[: j - i]


def make_song(path: Path, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    track = np.zeros(int(TOTAL_BARS * BAR * SR) + SR)
    kick = _kick()

    for bar in range(TOTAL_BARS):
        t0 = bar * BAR
        chord = CHORDS[bar % 4]
        part = next(name for name, a, b in SECTIONS if a <= bar < b)

        if part in ("intro", "verse", "breakdown", "outro"):
            for f in chord:
                _add(track, _tone(f * 2, BAR, 3), t0, 0.05)
        if part == "intro":
            for b in range(4):
                _add(track, _noise_hit(rng, 0.05, 0.02, True), t0 + b * BEAT, 0.04)
        if part == "verse":
            for b in range(4):
                _add(track, kick, t0 + b * BEAT, 0.45)
                _add(track, _noise_hit(rng, 0.05, 0.02, True), t0 + (b + 0.5) * BEAT, 0.08)
            _add(track, _tone(chord[0] / 2, BAR * 0.9, 4), t0, 0.18)
        if part == "buildup":
            step = 1 if bar < 17 else (0.5 if bar < 19 else 0.25)
            progress = (bar - 16) / 4
            for k in range(int(4 / step)):
                at = t0 + k * step * BEAT
                _add(track, _noise_hit(rng, 0.1, 0.05, False), at, 0.12 + 0.25 * progress + 0.06 * k * step / 4)
            n = int(BAR * SR)
            riser = rng.standard_normal(n) * np.linspace(progress, progress + 0.25, n) * 0.08
            _add(track, riser, t0, 1.0)
        if part == "drop":
            for b in range(4):
                _add(track, kick, t0 + b * BEAT, 0.95)
                _add(track, _noise_hit(rng, 0.05, 0.02, True), t0 + (b + 0.5) * BEAT, 0.12)
                if b in (1, 3):
                    _add(track, _noise_hit(rng, 0.15, 0.06, False), t0 + b * BEAT, 0.35)
            _add(track, _tone(chord[0] / 2, BAR * 0.95, 1), t0, 0.5)
            for f in chord:
                _add(track, _tone(f * 2, BAR * 0.95, 6), t0, 0.08)
        if part == "outro":
            for b in range(4):
                _add(track, kick, t0 + b * BEAT, 0.3)

    track = track / np.max(np.abs(track)) * 0.9
    sf.write(str(path), track.astype(np.float32), SR)
    return path
