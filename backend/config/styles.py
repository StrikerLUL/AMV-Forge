"""Stil-Profile aus backend/styles/*.yaml laden (Phase 4: Ziel-Stimmung und Score-Gewichte)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.analysis.mood import MOODS
from backend.config.settings import ScoreWeights

STYLES_DIR = Path(__file__).resolve().parent.parent / "styles"


@dataclass(frozen=True)
class StyleProfile:
    name: str
    description: str
    mood: dict[str, float]  # Ziel-Stimmung, -1 bis 1 pro Stimmung
    pool: float  # Anteil der Clips (nach Stimmung), die überhaupt in Frage kommen
    weights: ScoreWeights


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
    return StyleProfile(name=name, description=str(raw.get("description", "")), mood=mood, pool=pool,
                        weights=ScoreWeights(**weights))
