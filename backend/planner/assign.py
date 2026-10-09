"""Jedem Slot einen Clip zuweisen.

Phase 1: zufällig (assign_random). Phase 3: Bewegung passend zur Song-Energie, und der stärkste
Bewegungsmoment des Clips landet genau auf einem Beat (assign_to_beats). Phase 4: Auswahl nach der
Score-Formel (scoring.py) mit Stimmung, Qualität, Wiederholung und Dialog. Phase 5: gewünschte
Figuren (--characters) und nicht zu oft dieselbe Figur hintereinander. Streuung (spread.py): höchstens
2 Clips pro Minute einer Folge, damit ein Edit nicht eine Szene nacherzählt. Phase 6: Slow-Mo und
Speed-Ramps (Slot.timing) ändern, wie viel vom Clip ein Slot braucht, und der Stil "story" nimmt die
Clips in der Reihenfolge der Staffel.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from backend.analysis.video.faces import ClipFace
from backend.analysis.video.scenes import Scene
from backend.config.settings import ScoreWeights
from backend.planner.scoring import ENERGY_ONLY, character_tiers, score_clip, style_pool
from backend.planner.slots import Slot
from backend.planner.spread import Occupied, spread_out

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    """Ein Clip, der ins Edit darf (Szene ohne OP/ED)."""

    video: Path
    start: float
    end: float
    motion: float | None = None
    peak: float | None = None  # Sekunden in der Folge
    episode: int | None = None
    # Ab Phase 4
    clip_id: int | None = None
    mood: dict[str, float] | None = None
    quality: float | None = None
    speech: float | None = None
    # Ab Phase 5: AniList-ID der Figur -> Sicherheit 0-1
    characters: dict[int, float] | None = None
    # Ab Phase 6 für den Reframe: alle Gesichter im Clip (None = nicht gesucht)
    faces: tuple[ClipFace, ...] | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def character_ids(self) -> frozenset[int]:
        return frozenset(self.characters or {})


@dataclass(frozen=True)
class Assignment:
    """Ein Slot im Edit und die Stelle im Video, die dort läuft."""

    slot: Slot
    source_start: float
    video: Path | None = None
    episode: int | None = None
    # True, wenn der Bewegungs-Peak des Clips genau auf einem Beat liegt
    aligned: bool = False
    candidate: Candidate | None = None
    score: float | None = None
    # So viele Clips des Edits kamen bei der Wahl aus der vollsten Spanne seiner Folge um ihn herum
    # (er mitgezählt, 1 = allein an seiner Stelle, None = Streuung aus)
    crowd: int | None = None

    @property
    def source_end(self) -> float:
        return self.source_start + self.slot.source_duration


def usable_scenes(
    scenes: list[Scene],
    video_duration: float,
    skip_start: float = 0.0,
    skip_end: float = 0.0,
) -> list[Scene]:
    """Schneidet Szenen auf den erlaubten Bereich (ohne Anfang/Ende der Folge) zu."""
    lo, hi = skip_start, video_duration - skip_end
    result: list[Scene] = []
    for s in scenes:
        start, end = max(s.start, lo), min(s.end, hi)
        if end - start > 0:
            result.append(Scene(start, end))
    return result


def assign_random(
    slots: list[Slot],
    scenes: list[Scene],
    rng: random.Random,
) -> list[Assignment]:
    """Wählt pro Slot eine zufällige Szene, die lang genug ist, möglichst ohne Wiederholung.

    Ein Clip bleibt so immer innerhalb einer Einstellung, und jeder Schnitt im Edit liegt auf dem Beat.
    """
    if not scenes:
        raise ValueError("Keine nutzbaren Szenen im Video.")

    used: set[int] = set()
    assignments: list[Assignment] = []
    for slot in slots:
        fitting = [i for i, s in enumerate(scenes) if s.duration >= slot.duration]
        fresh = [i for i in fitting if i not in used]

        if fresh:
            idx = rng.choice(fresh)
        elif fitting:
            idx = rng.choice(fitting)
            log.debug("Alle passenden Szenen schon benutzt, nehme eine doppelt.")
        else:
            # Keine Szene ist lang genug: längste nehmen, der Clip läuft dann über den Szenenwechsel.
            idx = max(range(len(scenes)), key=lambda i: scenes[i].duration)
            log.warning("Keine Szene ist %.2f s lang, Clip läuft über einen Szenenwechsel.", slot.duration)

        scene = scenes[idx]
        slack = max(0.0, scene.duration - slot.duration)
        source_start = scene.start + rng.uniform(0.0, slack)
        used.add(idx)
        assignments.append(Assignment(slot=slot, source_start=source_start))
    return assignments


def align_start(cand: Candidate, slot: Slot) -> tuple[float, bool]:
    """Wo im Clip beginnt der Ausschnitt? Wenn möglich so, dass der Bewegungs-Peak auf einem Beat liegt.

    slot.hits sind die Beats im Slot (ab Slot-Anfang), der Schnitt selbst (0.0) zuerst. Wir nehmen
    den ersten, bei dem der Ausschnitt noch komplett im Clip liegt. Läuft der Clip langsamer oder
    schneller (Phase 6), wird mit der Clip-Zeit gerechnet (slot.source_hit, slot.source_duration).
    """
    latest = cand.end - slot.source_duration
    if cand.peak is not None:
        for offset in slot.hits:
            start = cand.peak - slot.source_hit(offset)
            if cand.start - 1e-6 <= start <= latest + 1e-6:
                return min(max(start, cand.start), max(cand.start, latest)), True
        return min(max(cand.peak - slot.source_hit(slot.hits[0]), cand.start), max(cand.start, latest)), False
    middle = cand.start + max(0.0, latest - cand.start) / 2
    return middle, False


def _motion_ranks(candidates: list[Candidate]) -> list[float]:
    """Bewegung als Rang 0-1 (0 = ruhigster Clip, 1 = wildester). Ohne Messung: 0.5."""
    measured = sorted((c.motion, i) for i, c in enumerate(candidates) if c.motion is not None)
    ranks = [0.5] * len(candidates)
    if len(measured) > 1:
        for rank, (_, i) in enumerate(measured):
            ranks[i] = rank / (len(measured) - 1)
    return ranks


def _can_align(cand: Candidate, slot: Slot) -> bool:
    if cand.peak is None:
        return False
    latest = cand.end - slot.source_duration
    return any(cand.start - 1e-6 <= cand.peak - slot.source_hit(o) <= latest + 1e-6 for o in slot.hits)


def repeated_characters(result: Sequence[Assignment], count: int, exempt: frozenset[int]) -> frozenset[int]:
    """Figuren, die in jedem der letzten count Clips vorkamen (ohne die ausdrücklich gewünschten)."""
    if count <= 0 or len(result) < count:
        return frozenset()
    sets = [a.candidate.character_ids if a.candidate else frozenset() for a in result[-count:]]
    return frozenset.intersection(*sets) - exempt


def chronological_order(candidates: Sequence[Candidate]) -> list[int]:
    """Indizes der Clips in der Reihenfolge der Staffel: Folge für Folge, in jeder Folge nach Zeit."""
    return sorted(range(len(candidates)),
                  key=lambda i: (candidates[i].episode or 0, str(candidates[i].video), candidates[i].start))


def in_story_order(pool: list[int], position: dict[int, int], last: int, slot: int, slots: int) -> list[int]:
    """Story-Stil: nur Clips nach dem zuletzt gewählten, und nicht weit vor dem Stück der Staffel, das zu
    diesem Slot gehört (Slot 10 von 40 bekommt etwa das zweite Viertel). So läuft das Edit einmal durch
    die ganze Staffel. Hinter dem gewählten Clip müssen noch genug Clips für die restlichen Slots frei sein,
    sonst müsste das Edit am Ende zurückspringen. Ist nichts davon frei, irgendein späterer Clip, sonst wie
    gehabt."""
    total = len(position)
    limit = (slot + 2) * total / max(1, slots)  # ein Stück Spielraum nach vorn
    later = sorted((i for i in pool if position[i] > last), key=lambda i: position[i])
    room = later[: max(1, len(later) - (slots - slot - 1))]  # Platz für die restlichen Slots lassen
    near = [i for i in room if position[i] < limit]
    return near or room or pool


def assign_to_beats(
    slots: list[Slot],
    candidates: list[Candidate],
    rng: random.Random,
    weights: ScoreWeights = ENERGY_ONLY,
    pick_from_top: int = 8,
    max_same_episode_in_row: int = 2,
    target: dict[str, float] | None = None,
    pool_share: float = 1.0,
    repeat_window: int = 6,
    characters: Sequence[int] = (),
    max_same_character_in_row: int = 0,
    spread_max_clips: int = 0,
    spread_window: float = 60.0,
    chronological: bool = False,
) -> list[Assignment]:
    """Greedy, Slot für Slot: passend lange Clips, möglichst mit Peak auf dem Beat, nach Score sortiert.

    Mit characters (AniList-IDs) kommen zuerst Clips mit allen diesen Figuren dran, sind die aufgebraucht,
    Clips mit mindestens einer. Mit target (Ziel-Stimmung eines Stils) kommen davon nur die pool_share
    besten nach Stimmung in Frage. Darunter nur Clips, mit denen aus keiner Spanne von spread_window Sekunden
    einer Folge mehr als spread_max_clips Clips kommen (bei knappem Pool gelockert, siehe spread.py). Aus den
    pick_from_top besten nach Score wird zufällig gewählt, damit nicht jedes Edit gleich aussieht. Kein Clip
    doppelt, nicht zu oft dieselbe Folge oder Figur hintereinander.

    Reihenfolge, wenn sich Wünsche widersprechen: Figuren vor Stil vor Streuung vor Abwechslung vor Beat.
    Mit chronological (Stil "story") kommen die Clips in der Reihenfolge der Staffel, das geht allem vor,
    und "nicht zu oft dieselbe Folge hintereinander" entfällt.
    """
    if not candidates:
        raise ValueError("Keine Clips zur Auswahl.")
    ranks = _motion_ranks(candidates)
    wanted = frozenset(characters)
    tiers = character_tiers(candidates, list(wanted)) if wanted else []
    among = sorted(tiers[-1]) if tiers and tiers[-1] else None
    in_style = style_pool(candidates, target, pool_share, among) if target else None
    episode_count = len({c.episode for c in candidates if c.episode is not None})
    used: set[int] = set()
    occupied = Occupied()
    in_edit: Counter[int | None] = Counter()
    result: list[Assignment] = []
    position = {i: k for k, i in enumerate(chronological_order(candidates))} if chronological else {}
    last = -1

    for number, slot in enumerate(slots):
        pool = [i for i, c in enumerate(candidates) if c.duration >= slot.source_duration - 1e-6]
        fresh = [i for i in pool if i not in used]
        if fresh:
            pool = fresh
        elif pool:
            log.debug("Alle passenden Clips schon benutzt, nehme einen doppelt.")
        else:
            longest = max(range(len(candidates)), key=lambda i: candidates[i].duration)
            log.warning("Kein Clip ist %.2f s lang, nehme den längsten.", slot.source_duration)
            pool = [longest]
        if chronological:
            pool = in_story_order(pool, position, last, number, len(slots))

        for tier in tiers:  # erst alle gewünschten Figuren, dann mindestens eine
            narrowed = [i for i in pool if i in tier]
            if narrowed:
                pool = narrowed
                break
        if in_style is not None:
            pool = [i for i in pool if i in in_style] or pool
        pool = spread_out(pool, candidates, occupied, spread_max_clips, spread_window)

        in_row = 0 if chronological else max_same_episode_in_row
        recent = [a.episode for a in result[-in_row:]] if in_row > 0 else []
        if len(recent) == in_row and recent and len(set(recent)) == 1 and recent[0] is not None:
            other = [i for i in pool if candidates[i].episode != recent[0]]
            pool = other or pool

        repeated = repeated_characters(result, max_same_character_in_row, wanted)
        if repeated:
            pool = [i for i in pool if not candidates[i].character_ids & repeated] or pool

        alignable = [i for i in pool if _can_align(candidates[i], slot)]
        pool = alignable or pool
        window = [a.episode for a in result[-repeat_window:]] if repeat_window > 0 else []
        scores = {i: score_clip(candidates[i], ranks[i], slot.intensity, weights, target, window, list(wanted),
                                in_edit, episode_count).total
                  for i in pool}
        best = sorted(pool, key=lambda i: (-scores[i], i))[: max(1, pick_from_top)]
        idx = rng.choice(best)
        used.add(idx)
        if chronological:
            last = position[idx]
        cand = candidates[idx]
        crowd = occupied.crowd(cand, spread_window) if spread_max_clips > 0 else None
        occupied.add(cand)
        in_edit[cand.episode] += 1
        start, aligned = align_start(cand, slot)
        result.append(Assignment(slot=slot, source_start=start, video=cand.video, episode=cand.episode,
                                 aligned=aligned, candidate=cand, score=scores[idx], crowd=crowd))
    if spread_max_clips > 0:
        over = [a.crowd for a in result if a.crowd is not None and a.crowd > spread_max_clips]
        log.info("Streuung (höchstens %d Clips aus %.0f s einer Folge): eingehalten bei %d von %d Clips",
                 spread_max_clips, spread_window, len(result) - len(over), len(result))
        if over:
            log.info("  bei %d Clips gelockert, bis zu %d aus derselben Stelle (an anderen Stellen gab es keinen "
                     "passenden Clip mehr)", len(over), max(over))
    return result
