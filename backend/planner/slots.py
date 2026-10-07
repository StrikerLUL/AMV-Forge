"""Zerlegt einen Ausschnitt des Songs in Slots zwischen zwei Schnittpunkten."""

from __future__ import annotations

import bisect
from dataclasses import dataclass


@dataclass(frozen=True)
class Timing:
    """Wie schnell der Clip in einem Slot läuft (Phase 6: Slow-Mo und Speed-Ramps).

    pieces: (bis zu welchem Anteil des Slots, Tempo), z. B. ((1.0, 0.8),) = ganzer Slot in 0,8-facher
    Geschwindigkeit, ((0.4, 0.5), (1.0, 1.6)) = erst Zeitlupe, dann schneller. Vor dem Slot (Übergänge)
    gilt das erste Tempo, danach das letzte.
    """

    pieces: tuple[tuple[float, float], ...] = ((1.0, 1.0),)

    @property
    def is_normal(self) -> bool:
        return all(abs(speed - 1.0) < 1e-9 for _, speed in self.pieces)

    def _breaks(self, duration: float) -> list[tuple[float, float, float]]:
        """(Edit-Zeit, Clip-Zeit, Tempo) am Anfang jedes Stücks, Zeiten ab Slot-Anfang."""
        result: list[tuple[float, float, float]] = []
        t = s = 0.0
        for end, speed in self.pieces:
            result.append((t, s, speed))
            t_end = end * duration
            s += (t_end - t) * speed
            t = t_end
        return result

    def source_offset(self, t: float, duration: float) -> float:
        """So viele Sekunden des Clips sind bis zur Edit-Zeit t (ab Slot-Anfang) gelaufen."""
        breaks = self._breaks(duration)
        t0, s0, speed = breaks[0]
        for b in breaks[1:]:
            if t < b[0]:
                break
            t0, s0, speed = b
        return s0 + (t - t0) * speed

    def edit_offset(self, s: float, duration: float) -> float:
        """Umkehrung von source_offset: zu welcher Edit-Zeit läuft Sekunde s des Clips (ab Ausschnitt-Anfang)?"""
        breaks = self._breaks(duration)
        t0, s0, speed = breaks[0]
        for b in breaks[1:]:
            if s < b[1]:
                break
            t0, s0, speed = b
        return t0 + (s - s0) / speed


NORMAL = Timing()


@dataclass(frozen=True)
class Slot:
    """Zeitraum im fertigen Edit, Sekunden ab Edit-Anfang (0 = song_start)."""

    start: float
    end: float
    # Ab Phase 3: Abschnitt (intro, verse, chorus, bridge, outro, buildup, drop) und Ziel-Intensität 0-1
    section: str = ""
    intensity: float = 0.5
    # Zeitpunkte im Slot (ab Slot-Anfang), auf die ein Bewegungs-Peak passt, der beste zuerst.
    hits: tuple[float, ...] = (0.0,)
    # Ab Phase 6: Tempo des Clips (Slow-Mo, Speed-Ramp)
    timing: Timing = NORMAL

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def source_duration(self) -> float:
        """So viele Sekunden des Clips braucht der Slot (bei Slow-Mo weniger, als der Slot lang ist)."""
        return self.timing.source_offset(self.duration, self.duration)

    def source_hit(self, offset: float) -> float:
        """Ein Zeitpunkt im Slot (z. B. ein Beat aus hits) als Sekunden ab Anfang des Clip-Ausschnitts."""
        return self.timing.source_offset(offset, self.duration)


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
