"""Lädt die YAML-Einstellungen in typisierte Dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).with_name("default.yaml")


@dataclass(frozen=True)
class QuickSettings:
    beats_per_cut: int
    min_slot_seconds: float
    skip_start_seconds: float
    skip_end_seconds: float


@dataclass(frozen=True)
class SceneSettings:
    adaptive_threshold: float
    min_scene_len_frames: int


@dataclass(frozen=True)
class RenderSettings:
    width: int
    height: int
    preview_width: int
    preview_height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str


@dataclass(frozen=True)
class Settings:
    quick: QuickSettings
    scenes: SceneSettings
    render: RenderSettings


def load_settings(path: Path | None = None) -> Settings:
    """Liest die YAML-Datei (Standard: backend/config/default.yaml)."""
    raw: dict[str, Any] = yaml.safe_load((path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    return Settings(
        quick=QuickSettings(**raw["quick"]),
        scenes=SceneSettings(**raw["scenes"]),
        render=RenderSettings(**raw["render"]),
    )
