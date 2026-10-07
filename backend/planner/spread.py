"""Streuung: nicht viele Clips aus derselben Szene oder demselben engen Zeitfenster einer Folge.

Regel: Aus jeder Spanne von window Sekunden einer Folge kommen höchstens max_clips Clips ins Edit (Standard:
2 pro Minute). Gibt es keinen passenden Clip mehr, der die Regel einhält (knapper Pool, z. B. nur 71 Paar-Szenen
für 81 Schnitte), steigt die Grenze um 1, bis einer passt. So geht nie ein Schnitt verloren.

Ein reiner Mindestabstand (z. B. 30 s zwischen zwei Clips) hätte denselben Zweck, sperrt aber um jeden Clip eine
ganze Minute. Bei einer Kampfszene bliebe dann nur ein Clip übrig, und Hype-Edits verlieren ihre besten Szenen.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


class Located(Protocol):
    """Was die Streuung von einem Clip braucht: in welcher Folge er wo anfängt."""

    @property
    def video(self) -> Path: ...

    @property
    def episode(self) -> int | None: ...

    @property
    def start(self) -> float: ...


def _key(clip: Located) -> tuple[Path, int | None]:
    return clip.video, clip.episode


def fullest_window(times: Sequence[float], at: float, window: float) -> int:
    """Wie viele der Zeitpunkte liegen in der vollsten Spanne von window Sekunden, die at enthält (at mitgezählt)?"""
    points = sorted([t for t in times if abs(t - at) <= window] + [at])
    best = 0
    last = 0
    for first, left in enumerate(points):
        if left > at:
            break
        if left < at - window:
            continue
        # Die Spanne beginnt bei einem Clip (weiter nach rechts schieben verliert keinen) und enthält at
        last = max(last, first)
        while last + 1 < len(points) and points[last + 1] <= left + window:
            last += 1
        best = max(best, last - first + 1)
    return best


class Occupied:
    """Die Stellen der Folgen, aus denen schon Clips im Edit sind."""

    def __init__(self) -> None:
        self._starts: dict[tuple[Path, int | None], list[float]] = defaultdict(list)

    def add(self, clip: Located) -> None:
        self._starts[_key(clip)].append(clip.start)

    def crowd(self, clip: Located, window: float) -> int:
        """So viele Clips kämen aus der vollsten Spanne von window Sekunden um diesen Clip (er mitgezählt)."""
        return fullest_window(self._starts.get(_key(clip), []), clip.start, window)


def spread_out(pool: list[int], clips: Sequence[Located], occupied: Occupied, max_clips: int,
               window: float) -> list[int]:
    """Behält die Clips aus pool (Indizes in clips), mit denen keine Stelle einer Folge zu voll wird.

    Erst mit max_clips pro window Sekunden, gibt es da keinen, mit max_clips + 1 usw. Leer wird der Pool nie.
    """
    if max_clips <= 0 or window <= 0 or not pool:
        return pool
    crowd = {i: occupied.crowd(clips[i], window) for i in pool}
    limit = max(max_clips, min(crowd.values()))
    return [i for i in pool if crowd[i] <= limit]


@dataclass(frozen=True)
class Stretch:
    """Die Stelle einer Folge, aus der die meisten Clips im Edit stammen."""

    episode: int | None
    count: int
    start: float
    end: float


def densest_stretch(clips: Sequence[Located], window: float) -> Stretch | None:
    """Wie viele Clips kommen höchstens aus window Sekunden einer Folge? (zum Vergleich vorher/nachher)"""
    starts: dict[tuple[Path, int | None], list[float]] = defaultdict(list)
    for clip in clips:
        starts[_key(clip)].append(clip.start)
    best: Stretch | None = None
    for (_, episode), times in starts.items():
        times.sort()
        first = 0
        for last, t in enumerate(times):
            while t - times[first] > window:
                first += 1
            count = last - first + 1
            if best is None or count > best.count:
                best = Stretch(episode, count, times[first], t)
    return best
