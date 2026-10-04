"""Zerlegt einen Ausschnitt des Songs in Slots zwischen zwei Schnittpunkten."""

from __future__ import annotations

import bisect
from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    """Zeitraum im fertigen Edit, Sekunden ab Edit-Anfang (0 = song_start)."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def choose_song_start(
    beats: list[float],
    song_duration: float,
    length: float,
    wanted_start: float | None = None,
) -> float:
    """Wählt den Startpunkt im Song: immer auf einem Beat, und der Ausschnitt muss reinpassen."""
    if not beats:
        raise ValueError("Keine Beats gefunden, der Song ist zu leise oder zu kurz.")
    if length > song_duration:
        raise ValueError(f"Song ist nur {song_duration:.1f} s lang, gewünscht sind {length:.1f} s.")

    if wanted_start is None:
        start = beats[0]
    else:
        start = min(beats, key=lambda b: abs(b - wanted_start))

    if start + length > song_duration:
        fitting = [b for b in beats if b + length <= song_duration]
        start = fitting[-1] if fitting else 0.0
    return start


def build_slots(
    beats: list[float],
    song_start: float,
    length: float,
    beats_per_cut: int,
    min_slot_seconds: float = 0.25,
) -> list[Slot]:
    """Schneidet auf jedem n-ten Beat ab song_start. Der letzte Slot endet genau bei length."""
    if beats_per_cut < 1:
        raise ValueError("beats_per_cut muss mindestens 1 sein.")

    song_end = song_start + length
    first = bisect.bisect_left(beats, song_start - 1e-6)
    cut_points = [
        b - song_start
        for b in beats[first::beats_per_cut]
        if song_start <= b < song_end
    ]
    if not cut_points or cut_points[0] > 1e-6:
        cut_points.insert(0, 0.0)
    cut_points[0] = 0.0
    cut_points.append(length)

    slots = [Slot(a, b) for a, b in zip(cut_points, cut_points[1:]) if b - a > 1e-9]

    # Zu kurzen letzten Slot an den vorherigen hängen, damit kein Mini-Flackern am Ende entsteht.
    if len(slots) > 1 and slots[-1].duration < min_slot_seconds:
        last = slots.pop()
        slots[-1] = Slot(slots[-1].start, last.end)
    return slots
