"""Score-Formel aus CLAUDE.md: Wie gut passt ein Clip in einen Slot?

    Score = w_m·Stimmung + w_e·(1 − |I_Clip − I_Slot|) + w_c·Charakter + w_q·Qualität
            − w_r·Wiederholung − w_d·Dialogschnitt

Alle Teile liegen zwischen 0 und 1, die Gewichte kommen aus default.yaml (planner.weights) oder
aus dem Stil (backend/styles/*.yaml). Charakter (Phase 5): Wie sicher sind die Figuren aus
--characters im Clip? Bei zwei gewünschten Figuren und nur einer im Bild ist das höchstens 0,5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from backend.analysis.mood import mood_match
from backend.config.settings import ScoreWeights

# Nur Bewegung passend zur Song-Energie, wie in Phase 3 (Standard ohne Stimmung)
ENERGY_ONLY = ScoreWeights(mood=0.0, energy=1.0, character=0.0, quality=0.0, repeat=0.0, dialog=0.0)


class Scorable(Protocol):
    """Was der Score von einem Clip braucht (nur lesen, passt so auch zu eingefrorenen Dataclasses)."""

    @property
    def mood(self) -> dict[str, float] | None: ...

    @property
    def quality(self) -> float | None: ...

    @property
    def speech(self) -> float | None: ...

    @property
    def episode(self) -> int | None: ...

    @property
    def characters(self) -> dict[int, float] | None: ...


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


def character_match(found: dict[int, float] | None, wanted: Sequence[int]) -> float:
    """Durchschnittliche Sicherheit der gewünschten Figuren im Clip (fehlende zählen 0)."""
    if not wanted:
        return 0.0
    return sum((found or {}).get(c, 0.0) for c in wanted) / len(wanted)


def score_clip(
    cand: Scorable,
    motion_rank: float,
    slot_intensity: float,
    weights: ScoreWeights,
    target: dict[str, float] | None,
    recent_episodes: Sequence[int | None],
    characters: Sequence[int] = (),
) -> Score:
    mood = mood_match(cand.mood, target) if target and cand.mood else 0.5
    energy = 1.0 - abs(motion_rank - slot_intensity)
    character = character_match(cand.characters, characters)
    quality = cand.quality if cand.quality is not None else 1.0
    repeat = repeat_share(cand.episode, recent_episodes)
    dialog = cand.speech if cand.speech is not None else 0.0
    total = (weights.mood * mood + weights.energy * energy + weights.character * character
             + weights.quality * quality - weights.repeat * repeat - weights.dialog * dialog)
    return Score(round(mood, 3), round(energy, 3), round(character, 3), round(quality, 3), round(repeat, 3),
                 round(dialog, 3), round(total, 4))


def style_pool(candidates: Sequence[Scorable], target: dict[str, float], share: float,
               among: Sequence[int] | None = None) -> set[int]:
    """Indizes der Clips, die am besten zur Ziel-Stimmung passen (der beste Anteil 'share').

    Mit among wird nur unter diesen Clips gesucht (z. B. nur Clips mit den gewünschten Figuren).
    """
    indices = range(len(candidates)) if among is None else among
    moods = [(candidates[i].mood, i) for i in indices]
    matches = sorted(((mood_match(mood, target) if mood else 0.0, i) for mood, i in moods), reverse=True)
    keep = max(1, int(round(len(matches) * share)))
    return {i for _, i in matches[:keep]}


def character_tiers(candidates: Sequence[Scorable], wanted: Sequence[int]) -> list[set[int]]:
    """Clips mit allen gewünschten Figuren, dann Clips mit mindestens einer davon (Indizes)."""
    need = set(wanted)
    every = {i for i, c in enumerate(candidates) if need <= set(c.characters or {})}
    some = {i for i, c in enumerate(candidates) if need & set(c.characters or {})}
    return [every, some]
