"""Stimmungsvektor pro Clip aus allen Signalen (Phase 4).

Jedes Signal wird zuerst innerhalb der Staffel in einen Rang 0-1 umgerechnet: 0 = der Clip mit dem
kleinsten Wert, 1 = der mit dem größten. So lassen sich Bewegung (Pixel), Lautstärke (dB) und
CLIP-Wahrscheinlichkeiten vergleichen. Danach ergibt sich jede Stimmung als gewichteter Mittelwert
der Signale, die Gewichte stehen in default.yaml unter mood.weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

MOODS: tuple[str, ...] = ("romance", "action", "sad", "funny", "calm")
SIGNALS: tuple[str, ...] = ("clip", "motion", "audio", "subtitles")


@dataclass(frozen=True)
class ClipSignals:
    """Rohwerte eines Clips, None = Signal fehlt (nicht gemessen oder nicht installiert)."""

    clip: dict[str, float] | None = None  # CLIP-Wahrscheinlichkeit pro Stimmung
    motion: float | None = None
    loudness: float | None = None  # dBFS
    speech: float | None = None  # Anteil Sprache 0-1
    dialog: dict[str, float] | None = None  # Untertitel: Wahrscheinlichkeit pro Stimmung


def percentile_ranks(values: Sequence[float | None]) -> list[float | None]:
    """Rang 0-1 innerhalb der Liste, gleiche Werte bekommen denselben Rang. None bleibt None."""
    known = [(v, i) for i, v in enumerate(values) if v is not None]
    ranks: list[float | None] = [None] * len(values)
    if not known:
        return ranks
    if len(known) == 1:
        ranks[known[0][1]] = 0.5
        return ranks
    known.sort()
    position = 0
    while position < len(known):
        end = position
        while end + 1 < len(known) and known[end + 1][0] == known[position][0]:
            end += 1
        rank = (position + end) / 2 / (len(known) - 1)
        for k in range(position, end + 1):
            ranks[known[k][1]] = rank
        position = end + 1
    return ranks


def signal_values(signals: Sequence[ClipSignals]) -> list[dict[str, dict[str, float]]]:
    """Pro Clip: Signal -> Stimmung -> Wert 0-1 (nur Signale, die für den Clip vorhanden sind)."""
    n = len(signals)
    clip_ranks = {m: percentile_ranks([s.clip.get(m) if s.clip else None for s in signals]) for m in MOODS}
    dialog_ranks = {m: percentile_ranks([s.dialog.get(m) if s.dialog else None for s in signals]) for m in MOODS}
    motion = percentile_ranks([s.motion for s in signals])
    loud = percentile_ranks([s.loudness for s in signals])

    result: list[dict[str, dict[str, float]]] = []
    for i in range(n):
        values: dict[str, dict[str, float]] = {}
        clip = {m: r for m in MOODS if (r := clip_ranks[m][i]) is not None}
        if clip:
            values["clip"] = clip
        m = motion[i]
        if m is not None:
            values["motion"] = {"action": m, "funny": m, "romance": 1 - m, "sad": 1 - m, "calm": 1 - m}
        level = loud[i]
        if level is not None:
            speech = signals[i].speech
            # Leise und ohne Sprache: der klassische Romance/Calm-Moment
            quiet = (1 - level) * (1 - speech if speech is not None else 1.0)
            values["audio"] = {"action": level, "funny": level, "romance": quiet, "sad": quiet, "calm": quiet}
        dialog = {m: r for m in MOODS if (r := dialog_ranks[m][i]) is not None}
        if dialog:
            values["subtitles"] = dialog
        result.append(values)
    return result


def combine(values: dict[str, dict[str, float]], weights: dict[str, dict[str, float]]) -> dict[str, float]:
    """Gewichteter Mittelwert pro Stimmung über die vorhandenen Signale. Ohne Signal: 0.5."""
    mood: dict[str, float] = {}
    for m in MOODS:
        total = weight_sum = 0.0
        for signal, per_mood in values.items():
            w = float(weights.get(signal, {}).get(m, 0.0))
            if w > 0 and m in per_mood:
                total += w * per_mood[m]
                weight_sum += w
        mood[m] = round(total / weight_sum, 3) if weight_sum > 0 else 0.5
    return mood


def mood_vectors(signals: Sequence[ClipSignals], weights: dict[str, dict[str, float]]) -> list[dict[str, float]]:
    unknown = sorted(set(weights) - set(SIGNALS))
    if unknown:
        raise ValueError(f"mood.weights: unbekanntes Signal {', '.join(unknown)} (erlaubt: {', '.join(SIGNALS)})")
    return [combine(v, weights) for v in signal_values(signals)]


def mood_match(mood: dict[str, float], target: dict[str, float]) -> float:
    """Wie gut passt ein Stimmungsvektor zum Ziel eines Stils? 0 = gar nicht, 1 = perfekt.

    Positive Ziele zählen, negative ziehen ab. Ein Clip mit romance 1 und action 0 passt zu
    {romance: 1, action: -0.6} perfekt, einer mit romance 0 und action 1 gar nicht.
    """
    positive = sum(v for v in target.values() if v > 0)
    negative = sum(-v for v in target.values() if v < 0)
    if positive + negative == 0:
        return 0.5
    raw = sum(v * mood.get(m, 0.5) for m, v in target.items())
    return float(np.clip((raw + negative) / (positive + negative), 0.0, 1.0))


def dominant(mood: dict[str, float]) -> str:
    """Die stärkste Stimmung eines Clips."""
    return max(MOODS, key=lambda m: mood.get(m, 0.0))
