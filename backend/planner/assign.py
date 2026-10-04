"""Phase 1: Jedem Slot einen zufälligen, passend langen Clip zuweisen."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from backend.analysis.video.scenes import Scene
from backend.planner.slots import Slot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Assignment:
    """Ein Slot im Edit und die Stelle im Video, die dort läuft."""

    slot: Slot
    source_start: float

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
