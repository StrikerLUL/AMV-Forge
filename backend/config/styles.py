"""Stil-Profile aus backend/styles/*.yaml laden.

Phase 4: Ziel-Stimmung, Pool und Score-Gewichte. Phase 6: Tempo (BPM-Bereich), Schnittrate pro Abschnitt,
Übergänge, Effekte und Reihenfolge (story = chronologisch).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from backend.analysis.mood import MOODS
from backend.config.settings import CutSettings, ScoreWeights
from backend.render.effects import (NO_EFFECTS, NO_TRANSITIONS, ZONES, EffectRules, TransitionRules, parse_effects,
                                    parse_transitions)

STYLES_DIR = Path(__file__).resolve().parent.parent / "styles"


@dataclass(frozen=True)
class StyleProfile:
    name: str
    description: str
    mood: dict[str, float]  # Ziel-Stimmung, -1 bis 1 pro Stimmung
    pool: float  # Anteil der Clips (nach Stimmung), die überhaupt in Frage kommen
    weights: ScoreWeights
    # Ab Phase 6
    bpm: tuple[float, float] | None = None  # Tempo, für das die Schnittraten gedacht sind
    cuts: dict[str, Any] = field(default_factory=dict)  # überschreibt cuts: aus default.yaml
    transitions: TransitionRules = NO_TRANSITIONS
    effects: EffectRules = NO_EFFECTS
    order: str = "score"  # score = beste Clips, chronological = in der Reihenfolge der Staffel (story)


def available_styles() -> list[str]:
    return sorted(p.stem for p in STYLES_DIR.glob("*.yaml"))


def load_style(name: str, defaults: ScoreWeights) -> StyleProfile:
    """Lädt backend/styles/<name>.yaml. Fehlende Gewichte kommen aus planner.weights."""
    path = STYLES_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ValueError(f"Stil '{name}' gibt es nicht. Vorhanden: {', '.join(available_styles())}")
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    mood = {str(k): float(v) for k, v in (raw.get("mood") or {}).items()}
    unknown = sorted(set(mood) - set(MOODS))
    if unknown:
        raise ValueError(f"{path.name}: unbekannte Stimmung {', '.join(unknown)} (erlaubt: {', '.join(MOODS)})")
    if not any(v > 0 for v in mood.values()):
        raise ValueError(f"{path.name}: unter 'mood' muss mindestens eine Stimmung positiv sein")

    pool = float(raw.get("pool", 1.0))
    if not 0.0 < pool <= 1.0:
        raise ValueError(f"{path.name}: 'pool' muss zwischen 0 und 1 liegen, ist {pool}")

    weights = {**asdict(defaults), **{str(k): float(v) for k, v in (raw.get("weights") or {}).items()}}
    unknown = sorted(set(weights) - set(asdict(defaults)))
    if unknown:
        raise ValueError(f"{path.name}: unbekanntes Gewicht {', '.join(unknown)}")

    allowed = {"description", "mood", "pool", "weights", "bpm", "cuts", "transitions", "effects", "order"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{path.name}: unbekannter Eintrag {', '.join(unknown)} (erlaubt: {', '.join(sorted(allowed))})")
    order = str(raw.get("order", "score"))
    if order not in ("score", "chronological"):
        raise ValueError(f"{path.name}: 'order' muss score oder chronological sein, ist {order}")
    return StyleProfile(name=name, description=str(raw.get("description", "")), mood=mood, pool=pool,
                        weights=ScoreWeights(**weights), bpm=_bpm(raw.get("bpm"), path.name),
                        cuts=_cuts(raw.get("cuts"), path.name),
                        transitions=parse_transitions(raw.get("transitions"), path.name),
                        effects=parse_effects(raw.get("effects"), path.name), order=order)


def _bpm(raw: Any, where: str) -> tuple[float, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2 or not 0 < float(raw[0]) <= float(raw[1]):
        raise ValueError(f"{where}: 'bpm' muss so aussehen: [70, 100]")
    return float(raw[0]), float(raw[1])


def _cuts(raw: Any, where: str) -> dict[str, Any]:
    """Schnittraten des Stils, gleiche Namen wie unter cuts: in default.yaml."""
    cuts = dict(raw or {})
    allowed = {f.name for f in fields(CutSettings)}
    unknown = sorted(set(cuts) - allowed)
    if unknown:
        raise ValueError(f"{where}: unbekannte Schnitt-Einstellung {', '.join(unknown)} (erlaubt: {', '.join(sorted(allowed))})")
    per_section = {str(k): float(v) for k, v in (cuts.pop("beats_per_cut", None) or {}).items()}
    bad = sorted(set(per_section) - set(ZONES))
    if bad or any(v <= 0 for v in per_section.values()):
        raise ValueError(f"{where}: cuts.beats_per_cut braucht Abschnitte ({', '.join(ZONES[:5])}) mit Werten über 0")
    result: dict[str, Any] = {k: float(v) for k, v in cuts.items()}
    if per_section:
        result["beats_per_cut"] = per_section
    return result
