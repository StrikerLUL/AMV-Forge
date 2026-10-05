"""Score-Formel aus CLAUDE.md: Wie gut passt ein Clip in einen Slot?

    Score = w_m·Stimmung + w_e·(1 − |I_Clip − I_Slot|) + w_c·Charakter + w_q·Qualität
            − w_r·Wiederholung − w_d·Dialogschnitt

Alle Teile liegen zwischen 0 und 1, die Gewichte kommen aus default.yaml (planner.weights) oder
aus dem Stil (backend/styles/*.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from backend.analysis.mood import mood_match
from backend.config.settings import ScoreWeights

# Nur Bewegung passend zur Song-Energie, wie in Phase 3 (Standard ohne Stimmung)
ENERGY_ONLY = ScoreWeights(mood=0.0, energy=1.0, character=0.0, quality=0.0, repeat=0.0, dialog=0.0)


class Scorable(Protocol):
    mood: dict[str, float] | None
    quality: float | None
    speech: float | None
    episode: int | None


@dataclass(frozen=True)
class Score:
    mood: float
    energy: float
    character: float
    quality: float
    repeat: float
    dialog: float
    total: float


def repeat_share(episode: int | None, recent: Sequence[int | None]) -> float:
    """Anteil der letzten Clips, die aus derselben Folge kamen (0 = keiner, 1 = alle)."""
    if episode is None or not recent:
        return 0.0
    return sum(1 for e in recent if e == episode) / len(recent)


def score_clip(
    cand: Scorable,
    motion_rank: float,
    slot_intensity: float,
    weights: ScoreWeights,
    target: dict[str, float] | None,
    recent_episodes: Sequence[int | None],
) -> Score:
    mood = mood_match(cand.mood, target) if target and cand.mood else 0.5
    energy = 1.0 - abs(motion_rank - slot_intensity)
    character = 0.0  # Phase 5: gewünschte Figuren im Bild
    quality = cand.quality if cand.quality is not None else 1.0
    repeat = repeat_share(cand.episode, recent_episodes)
    dialog = cand.speech if cand.speech is not None else 0.0
    total = (weights.mood * mood + weights.energy * energy + weights.character * character
             + weights.quality * quality - weights.repeat * repeat - weights.dialog * dialog)
    return Score(round(mood, 3), round(energy, 3), character, round(quality, 3), round(repeat, 3),
                 round(dialog, 3), round(total, 4))


def style_pool(candidates: Sequence[Scorable], target: dict[str, float], share: float) -> set[int]:
    """Indizes der Clips, die am besten zur Ziel-Stimmung passen (der beste Anteil 'share')."""
    matches = sorted(((mood_match(c.mood, target) if c.mood else 0.0, i) for i, c in enumerate(candidates)),
                     reverse=True)
    keep = max(1, int(round(len(matches) * share)))
    return {i for _, i in matches[:keep]}
