"""Song-Abschnitte ohne KI (Fallback, wenn allin1 fehlt).

Idee: Für jeden Takt einen "Klang-Fingerabdruck" bilden (Akkorde, Klangfarbe, Energie). Wo sich
der Fingerabdruck der Takte davor stark von denen danach unterscheidet, beginnt ein neuer Abschnitt.
Die Namen (Intro, Verse, Chorus ...) kommen danach aus der Energie: der lauteste Teil ist der
Refrain, ruhige Teile am Anfang/Ende sind Intro/Outro.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from backend.analysis.music.energy import mean_energy

HOP = 512


@dataclass(frozen=True)
class SegmentSettings:
    min_section_bars: int = 4
    novelty_bars: int = 4
    novelty_threshold: float = 0.5  # Anteil des stärksten Wechsels, ab dem ein neuer Abschnitt beginnt
    chorus_energy: float = 0.7  # ab dieser Energie (0-1) gilt ein Abschnitt als Refrain
    calm_energy: float = 0.35  # darunter ist ein Abschnitt "ruhig" (Intro, Outro, Bridge)


def bar_features(y: np.ndarray, sr: int, downbeats: list[float]) -> np.ndarray:
    """Ein Merkmalsvektor pro Takt: Akkorde (Chroma) + Klangfarbe (MFCC), jeweils standardisiert."""
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=HOP, n_mfcc=13)
    frames = librosa.time_to_frames(downbeats, sr=sr, hop_length=HOP)
    step = int(np.median(np.diff(frames))) if len(frames) > 1 else 1
    bounds = [*frames, frames[-1] + step]
    n = chroma.shape[1]
    rows = []
    for a, b in zip(bounds, bounds[1:]):
        a, b = int(min(a, n - 1)), int(min(max(a + 1, b), n))
        rows.append(np.concatenate([chroma[:, a:b].mean(axis=1), mfcc[:, a:b].mean(axis=1)]))
    feats = np.array(rows)
    std = feats.std(axis=0)
    return (feats - feats.mean(axis=0)) / np.where(std > 1e-9, std, 1.0)


def novelty(features: np.ndarray, energies: np.ndarray, width: int) -> np.ndarray:
    """Wie stark unterscheiden sich die width Takte vor einer Taktgrenze von den width Takten danach?"""
    n = len(features)
    result = np.zeros(n)
    for i in range(1, n):
        a, b = max(0, i - width), min(n, i + width)
        timbre = np.linalg.norm(features[a:i].mean(axis=0) - features[i:b].mean(axis=0)) / np.sqrt(features.shape[1])
        energy = abs(float(energies[a:i].mean() - energies[i:b].mean()))
        result[i] = timbre + 2.0 * energy
    return result


def pick_boundaries(nov: np.ndarray, min_bars: int, threshold: float) -> list[int]:
    """Taktnummern, an denen ein neuer Abschnitt beginnt (immer inklusive 0).

    Nur Grenzen, die mindestens min_bars vom Anfang/Ende weg sind. Die Schwelle bezieht sich auf
    den stärksten dieser Kandidaten, damit z. B. Stille am Songende nicht alles andere überstrahlt.
    """
    n = len(nov)
    eligible = [i for i in range(max(1, min_bars), n - min_bars + 1)
                if nov[i] >= nov[i - 1] and (i + 1 >= n or nov[i] >= nov[i + 1])]
    if not eligible:
        return [0]
    top = max(nov[i] for i in eligible)
    if top <= 0:
        return [0]
    chosen: list[int] = []
    for i in sorted((i for i in eligible if nov[i] >= threshold * top), key=lambda i: -nov[i]):
        if all(abs(i - j) >= min_bars for j in chosen):
            chosen.append(i)
    return [0, *sorted(chosen)]


def label_sections(energies: list[float], cfg: SegmentSettings) -> list[str]:
    """Namen nach Energie: laut = chorus, ruhig am Anfang = intro, ruhig am Ende = outro,
    ruhig zwischen zwei Refrains = bridge, sonst verse."""
    n = len(energies)
    if n == 0:
        return []
    top = max(energies)
    labels = ["chorus" if e >= max(cfg.chorus_energy, 0.85 * top) else "verse" for e in energies]
    if labels[0] != "chorus" and (energies[0] < cfg.calm_energy or n > 2):
        labels[0] = "intro"
    if n > 1 and labels[-1] != "chorus" and (energies[-1] < cfg.calm_energy or n > 2):
        labels[-1] = "outro"
    choruses = [i for i, label in enumerate(labels) if label == "chorus"]
    if choruses:
        for i in range(choruses[0] + 1, n - 1):
            if labels[i] == "verse" and energies[i] < cfg.calm_energy and any(c > i for c in choruses):
                labels[i] = "bridge"
    return labels


def segment_song(
    y: np.ndarray,
    sr: int,
    downbeats: list[float],
    duration: float,
    curve: np.ndarray,
    rate: float,
    cfg: SegmentSettings = SegmentSettings(),
) -> list[tuple[float, float, str]]:
    """Abschnitte als (start, ende, name). Grenzen liegen immer auf Taktanfängen."""
    if len(downbeats) < 2 * cfg.min_section_bars:
        energy = mean_energy(curve, rate, 0.0, duration)
        return [(0.0, duration, label_sections([energy], cfg)[0])]

    bar_ends = [*downbeats[1:], duration]
    bar_energy = np.array([mean_energy(curve, rate, a, b) for a, b in zip(downbeats, bar_ends)])
    nov = novelty(bar_features(y, sr, downbeats), bar_energy, cfg.novelty_bars)
    starts = pick_boundaries(nov, cfg.min_section_bars, cfg.novelty_threshold)

    times = [0.0, *(downbeats[i] for i in starts[1:]), duration]
    energies = [mean_energy(curve, rate, a, b) for a, b in zip(times, times[1:])]
    labels = label_sections(energies, cfg)
    # Nachbarn mit gleichem Namen zusammenlegen, für den Schnitt macht das keinen Unterschied
    merged: list[tuple[float, float, str]] = []
    for a, b, label in zip(times, times[1:], labels):
        if merged and merged[-1][2] == label:
            merged[-1] = (merged[-1][0], b, label)
        else:
            merged.append((a, b, label))
    return merged
