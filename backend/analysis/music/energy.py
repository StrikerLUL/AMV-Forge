"""Energiekurve eines Songs und Drop-Erkennung.

Die Energiekurve sagt für jeden Moment, wie "voll" der Song gerade ist (0 = ruhig, 1 = volle Power).
Sie mischt drei Signale: Lautstärke, Onset-Stärke (wie viele Anschläge) und Bass. Ein Drop ist ein
Taktanfang, an dem die Energie plötzlich nach oben springt und danach oben bleibt.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

HOP = 512


@dataclass(frozen=True)
class EnergyWeights:
    loudness: float = 0.5
    onset: float = 0.25
    bass: float = 0.25


@dataclass(frozen=True)
class Drop:
    time: float
    strength: float  # wie stark die Energie springt (0-1)


def _db_to_unit(power: np.ndarray, range_db: float = 40.0) -> np.ndarray:
    """Leistung in dB, dann die oberen range_db dB auf 0-1 abbilden."""
    db = 10.0 * np.log10(np.maximum(power, 1e-12))
    top = float(np.percentile(db, 99))
    return np.clip((db - (top - range_db)) / range_db, 0.0, 1.0)


def _robust_unit(x: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(x, [2, 98])
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return x
    kernel = np.ones(width) / width
    padded = np.pad(x, (width // 2, width - 1 - width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def energy_curve(
    y: np.ndarray,
    sr: int,
    rate: float = 10.0,
    weights: EnergyWeights = EnergyWeights(),
    smooth_seconds: float = 1.0,
) -> np.ndarray:
    """Energie pro 1/rate Sekunden, Werte 0-1 (relativ zum lautesten Teil des Songs)."""
    spec = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    loud = _db_to_unit(spec.sum(axis=0))
    bass = _db_to_unit(spec[freqs <= 150].sum(axis=0))
    onset = librosa.onset.onset_strength(S=librosa.power_to_db(spec), sr=sr, hop_length=HOP)
    onset = _smooth(onset, max(1, int(0.5 * sr / HOP)))  # Anschläge pro halbe Sekunde
    onset = _robust_unit(onset[: len(loud)])

    total = weights.loudness + weights.onset + weights.bass
    mixed = (weights.loudness * loud + weights.onset * onset + weights.bass * bass) / total
    mixed = _smooth(mixed, max(1, int(smooth_seconds * sr / HOP)))

    # Auf rate Werte pro Sekunde umrechnen
    frame_times = librosa.frames_to_time(np.arange(len(mixed)), sr=sr, hop_length=HOP)
    duration = len(y) / sr
    grid = np.arange(0.0, duration, 1.0 / rate)
    return _robust_unit(np.interp(grid, frame_times, mixed)).astype(np.float32)


def mean_energy(curve: np.ndarray | list[float], rate: float, start: float, end: float) -> float:
    """Durchschnittliche Energie zwischen start und end (Sekunden)."""
    values = np.asarray(curve, dtype=float)
    if len(values) == 0:
        return 0.0
    a = int(np.clip(np.floor(start * rate), 0, len(values) - 1))
    b = int(np.clip(np.ceil(end * rate), a + 1, len(values)))
    return float(values[a:b].mean())


def detect_drops(
    curve: np.ndarray | list[float],
    rate: float,
    downbeats: list[float],
    window_seconds: float,
    min_jump: float,
    min_level: float,
    min_distance_seconds: float,
    max_drops: int,
) -> list[Drop]:
    """Taktanfänge, an denen die Energie springt: Mittel danach minus Mittel davor.

    Nur Sprünge, nach denen der Song wirklich laut ist (min_level), zählen. Liegen zwei Kandidaten
    nah beieinander, gewinnt der stärkere.
    """
    candidates: list[Drop] = []
    for t in downbeats:
        if t - window_seconds < 0:
            continue
        before = mean_energy(curve, rate, t - window_seconds, t)
        after = mean_energy(curve, rate, t, t + window_seconds)
        jump = after - before
        if jump >= min_jump and after >= min_level:
            candidates.append(Drop(time=float(t), strength=round(float(jump), 3)))

    chosen: list[Drop] = []
    for cand in sorted(candidates, key=lambda d: -d.strength):
        if all(abs(cand.time - c.time) >= min_distance_seconds for c in chosen):
            chosen.append(cand)
        if len(chosen) >= max_drops:
            break
    return sorted(chosen, key=lambda d: d.time)
