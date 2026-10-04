"""Phase 3: Schnittpunkte aus der Song-Struktur.

Jeder Beat bekommt eine "Zone" (Abschnitt, Build-up oder Drop) mit eigener Schnittrate.
An jedem Zonenwechsel wird geschnitten, innerhalb einer Zone alle n Beats. Im Drop sind auch
halbe Beats erlaubt, die Zeit dafür liegt genau in der Mitte zwischen zwei Beats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.analysis.music.energy import mean_energy
from backend.analysis.music.structure import SongAnalysis
from backend.config.settings import CutSettings
from backend.planner.slots import Slot

EPS = 1e-6


@dataclass(frozen=True)
class Zone:
    label: str
    beats_per_cut: float


def ramp(bars: int, start: float, end: float) -> list[float]:
    """Schnittrate pro Takt im Build-up, z. B. 4 Takte von 4 nach 1 -> [4, 2, 2, 1]."""
    if bars <= 0:
        return []
    if bars == 1 or start <= 0 or end <= 0:
        return [end] * bars
    rates = []
    for k in range(bars):
        value = start * (end / start) ** (k / (bars - 1))
        rates.append(float(2 ** round(math.log2(value))))
    return rates


def zones_per_beat(song: SongAnalysis, cfg: CutSettings) -> list[Zone]:
    """Für jeden Beat: in welcher Zone liegt er und wie oft wird dort geschnitten?"""
    tol = 0.25 * song.beat_seconds  # Beat gehört zur Zone, die bis zu 1/4 Beat nach ihm beginnt
    bar = song.bar_seconds
    windows: list[tuple[float, float, str, list[float]]] = []  # (start, ende, zone, Raten pro Takt)
    previous_end = 0.0
    for drop in sorted(song.drops, key=lambda d: d.time):
        section = song.section_at(drop.time + tol)
        drop_end = drop.time + cfg.drop_bars * bar
        if section is not None and section.end > drop.time + tol:
            drop_end = min(drop_end, section.end)
        build_bars = int(cfg.buildup_bars)
        build_start = max(previous_end, drop.time - build_bars * bar)
        if build_bars > 0 and drop.time - build_start > tol:
            windows.append((build_start, drop.time, "buildup", ramp(build_bars, cfg.buildup_from, cfg.buildup_to)))
        windows.append((drop.time, drop_end, "drop", [cfg.drop]))
        previous_end = drop_end

    zones: list[Zone] = []
    for beat in song.beats:
        t = beat + tol
        zone: Zone | None = None
        for start, end, label, rates in windows:
            if start <= t < end:
                if label == "buildup":
                    # Takte vom Drop rückwärts zählen, damit der letzte Takt immer die schnellste Rate hat
                    bars_before_drop = int((end - t) // bar)
                    rate = rates[max(0, len(rates) - 1 - bars_before_drop)]
                else:
                    rate = rates[0]
                zone = Zone(label, rate)
                break
        if zone is None:
            section = song.section_at(t)
            label = section.label if section else "verse"
            zone = Zone(label, float(cfg.beats_per_cut.get(label, 2)))
        zones.append(zone)
    return zones


def choose_edit_start(
    song: SongAnalysis,
    length: float,
    wanted_start: float | None = None,
    drop_position: float = 0.4,
) -> float:
    """Startpunkt im Song: auf einem Taktanfang, und der stärkste Drop landet bei drop_position des Edits.

    Mit wanted_start (z. B. --song-start) wird nur auf den nächsten Beat gerundet.
    """
    if length > song.duration:
        raise ValueError(f"Song ist nur {song.duration:.1f} s lang, gewünscht sind {length:.1f} s.")
    latest = song.duration - length
    grid = song.downbeats or song.beats
    if wanted_start is not None:
        target, candidates = wanted_start, song.beats
    elif song.drops:
        main = max(song.drops, key=lambda d: d.strength)
        target, candidates = main.time - drop_position * length, grid
    else:
        chorus = next((s for s in song.sections if s.label == "chorus"), None)
        target = chorus.start - drop_position * length if chorus else (grid[0] if grid else 0.0)
        candidates = grid
    fitting = [b for b in candidates if 0.0 <= b <= latest + EPS]
    if not fitting:
        return 0.0
    return min(fitting, key=lambda b: abs(b - target))


def _beat_time(beats: list[float], pos: float) -> float:
    i = int(math.floor(pos + EPS))
    frac = pos - i
    if frac < EPS or i + 1 >= len(beats):
        return beats[min(i, len(beats) - 1)]
    return beats[i] + frac * (beats[i + 1] - beats[i])


def build_song_slots(
    song: SongAnalysis,
    song_start: float,
    length: float,
    cfg: CutSettings,
    min_slot_seconds: float = 0.25,
) -> list[Slot]:
    """Slots für den Ausschnitt [song_start, song_start + length) mit Schnittrate je nach Zone."""
    beats = song.beats
    if len(beats) < 2:
        raise ValueError("Zu wenige Beats im Song.")
    zones = zones_per_beat(song, cfg)
    song_end = song_start + length
    first = min(range(len(beats)), key=lambda i: abs(beats[i] - song_start))

    # Nächster Zonenwechsel ab jedem Beat (dort wird immer geschnitten)
    next_change = [len(beats)] * len(beats)
    for i in range(len(beats) - 2, -1, -1):
        next_change[i] = i + 1 if zones[i + 1] != zones[i] else next_change[i + 1]

    positions = [float(first)]
    pos = float(first)
    while True:
        k = int(math.floor(pos + EPS))
        step = zones[k].beats_per_cut
        if step < 1 and k + 1 < len(beats) and (beats[k + 1] - beats[k]) * step < cfg.min_cut_seconds:
            step = 1.0
        nxt = min(pos + step, float(next_change[k]))
        if nxt > len(beats) - 1 + EPS or _beat_time(beats, nxt) >= song_end - min_slot_seconds:
            break
        positions.append(nxt)
        pos = nxt

    downbeats = song.downbeats
    tol = 0.25 * song.beat_seconds

    def is_downbeat(t: float) -> bool:
        return any(abs(t - d) < tol for d in downbeats)

    times = [_beat_time(beats, p) for p in positions] + [song_end]
    times[0] = song_start
    slots: list[Slot] = []
    for idx, (p, t0, t1) in enumerate(zip(positions, times, times[1:])):
        inner = [beats[j] - t0 for j in range(int(math.ceil(p - EPS)), len(beats)) if t0 + EPS < beats[j] < t1 - EPS]
        down = [o for o in inner if is_downbeat(t0 + o)]
        rest = [o for o in inner if o not in down]
        zone = zones[int(math.floor(p + EPS))]
        slots.append(
            Slot(
                start=t0 - song_start,
                end=(t1 - song_start) if idx < len(positions) - 1 else length,
                section=zone.label,
                intensity=round(mean_energy(song.energy, song.energy_rate, t0, t1), 3),
                hits=(0.0, *(round(o, 4) for o in down + rest)),
            )
        )
    return slots


def summarize(slots: list[Slot]) -> list[tuple[str, int, float]]:
    """(Zone, Anzahl Clips, durchschnittliche Clip-Länge) in der Reihenfolge des Edits."""
    order: list[str] = []
    stats: dict[str, list[float]] = {}
    for s in slots:
        if s.section not in stats:
            order.append(s.section)
            stats[s.section] = []
        stats[s.section].append(s.duration)
    return [(label, len(stats[label]), sum(stats[label]) / len(stats[label])) for label in order]
