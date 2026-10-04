"""Szenenerkennung mit PySceneDetect, Ergebnis wird als JSON gecacht."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from scenedetect import AdaptiveDetector, detect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scene:
    """Eine Einstellung im Video, Zeiten in Sekunden."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _cache_key(video: Path, threshold: float, min_len: int) -> str:
    stat = video.stat()
    raw = f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{threshold}|{min_len}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def detect_scenes(
    video: Path,
    cache_dir: Path,
    adaptive_threshold: float = 3.0,
    min_scene_len_frames: int = 12,
) -> list[Scene]:
    """Findet alle Schnitte im Video. Ein zweiter Aufruf mit derselben Datei liest nur den Cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{video.stem}_{_cache_key(video, adaptive_threshold, min_scene_len_frames)}.json"

    if cache_file.exists():
        scenes = [Scene(**s) for s in json.loads(cache_file.read_text(encoding="utf-8"))]
        log.info("Szenen aus Cache geladen: %d (%s)", len(scenes), cache_file.name)
        return scenes

    log.info("Suche Szenenwechsel in %s (kann ein paar Minuten dauern) ...", video.name)
    detector = AdaptiveDetector(
        adaptive_threshold=adaptive_threshold,
        min_scene_len=min_scene_len_frames,
    )
    scene_list = detect(str(video), detector, show_progress=True)
    scenes = [Scene(start=s.get_seconds(), end=e.get_seconds()) for s, e in scene_list]

    cache_file.write_text(json.dumps([asdict(s) for s in scenes]), encoding="utf-8")
    log.info("%d Szenen gefunden, Cache: %s", len(scenes), cache_file)
    return scenes
