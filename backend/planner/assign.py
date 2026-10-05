"""Jedem Slot einen Clip zuweisen.

Phase 1: zufällig (assign_random). Phase 3: Bewegung passend zur Song-Energie, und der stärkste
Bewegungsmoment des Clips landet genau auf einem Beat (assign_to_beats). Phase 4: Auswahl nach der
Score-Formel (scoring.py) mit Stimmung, Qualität, Wiederholung und Dialog.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from backend.analysis.video.scenes import Scene
from backend.config.settings import ScoreWeights
from backend.planner.scoring import ENERGY_ONLY, score_clip, style_pool
from backend.planner.slots import Slot

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

    @property
    def duration(self) -> float:
        return self.end - self.start


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

    @property
    def source_end(self) -> float:
        return self.source_start + self.slot.duration


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
    den ersten, bei dem der Ausschnitt noch komplett im Clip liegt.
    """
    latest = cand.end - slot.duration
    if cand.peak is not None:
        for offset in slot.hits:
            start = cand.peak - offset
            if cand.start - 1e-6 <= start <= latest + 1e-6:
                return min(max(start, cand.start), max(cand.start, latest)), True
        return min(max(cand.peak - slot.hits[0], cand.start), max(cand.start, latest)), False
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
    latest = cand.end - slot.duration
    return any(cand.start - 1e-6 <= cand.peak - o <= latest + 1e-6 for o in slot.hits)


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
) -> list[Assignment]:
    """Greedy, Slot für Slot: passend lange Clips, möglichst mit Peak auf dem Beat, nach Score sortiert.

    Mit target (Ziel-Stimmung eines Stils) kommen nur die pool_share besten Clips nach Stimmung in
    Frage. Aus den pick_from_top besten nach Score wird zufällig gewählt, damit nicht jedes Edit gleich
    aussieht. Kein Clip doppelt und nicht zu oft dieselbe Folge hintereinander.
    """
    if not candidates:
        raise ValueError("Keine Clips zur Auswahl.")
    ranks = _motion_ranks(candidates)
    in_style = style_pool(candidates, target, pool_share) if target else None
    used: set[int] = set()
    result: list[Assignment] = []

    for slot in slots:
        pool = [i for i, c in enumerate(candidates) if c.duration >= slot.duration - 1e-6]
        fresh = [i for i in pool if i not in used]
        if fresh:
            pool = fresh
        elif pool:
            log.debug("Alle passenden Clips schon benutzt, nehme einen doppelt.")
        else:
            longest = max(range(len(candidates)), key=lambda i: candidates[i].duration)
            log.warning("Kein Clip ist %.2f s lang, nehme den längsten.", slot.duration)
            pool = [longest]

        if in_style is not None:
            pool = [i for i in pool if i in in_style] or pool

        recent = [a.episode for a in result[-max_same_episode_in_row:]] if max_same_episode_in_row > 0 else []
        if len(recent) == max_same_episode_in_row and recent and len(set(recent)) == 1 and recent[0] is not None:
            other = [i for i in pool if candidates[i].episode != recent[0]]
            pool = other or pool

        alignable = [i for i in pool if _can_align(candidates[i], slot)]
        pool = alignable or pool
        window = [a.episode for a in result[-repeat_window:]] if repeat_window > 0 else []
        scores = {i: score_clip(candidates[i], ranks[i], slot.intensity, weights, target, window).total for i in pool}
        best = sorted(pool, key=lambda i: (-scores[i], i))[: max(1, pick_from_top)]
        idx = rng.choice(best)
        used.add(idx)
        cand = candidates[idx]
        start, aligned = align_start(cand, slot)
        result.append(Assignment(slot=slot, source_start=start, video=cand.video, episode=cand.episode,
                                 aligned=aligned, candidate=cand, score=scores[idx]))
    return result
