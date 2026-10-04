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
class DatabaseSettings:
    path: Path


@dataclass(frozen=True)
class IndexSettings:
    source: str
    video_extensions: tuple[str, ...]
    download_dir: Path
    min_clip_seconds: float


@dataclass(frozen=True)
class ApiSettings:
    anilist_interval: float
    jikan_interval: float
    aniskip_interval: float
    timeout_seconds: float
    connect_timeout_seconds: float
    connection_retries: int
    max_retries: int
    anilist_min_title_score: float
    aniskip_max_length_diff: float


@dataclass(frozen=True)
class OpEdSettings:
    head_seconds: float
    tail_seconds: float
    min_seconds: float
    max_seconds: float
    similarity: float
    max_gap_seconds: float
    silence_db: float
    min_overlap: float


@dataclass(frozen=True)
class Settings:
    quick: QuickSettings
    scenes: SceneSettings
    render: RenderSettings
    database: DatabaseSettings
    index: IndexSettings
    apis: ApiSettings
    op_ed: OpEdSettings


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Legt die eigenen Werte über die Standardwerte, auch in verschachtelten Abschnitten."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: Path | None = None) -> Settings:
    """Liest backend/config/default.yaml. Eine eigene YAML muss nur die Werte enthalten, die sie ändert."""
    raw: dict[str, Any] = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    if path is not None:
        raw = _merge(raw, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    index = raw["index"]
    return Settings(
        quick=QuickSettings(**raw["quick"]),
        scenes=SceneSettings(**raw["scenes"]),
        render=RenderSettings(**raw["render"]),
        database=DatabaseSettings(path=Path(raw["database"]["path"])),
        index=IndexSettings(
            source=index["source"],
            video_extensions=tuple(e.lower() for e in index["video_extensions"]),
            download_dir=Path(index["download_dir"]),
            min_clip_seconds=float(index["min_clip_seconds"]),
        ),
        apis=ApiSettings(**raw["apis"]),
        op_ed=OpEdSettings(**raw["op_ed"]),
    )
