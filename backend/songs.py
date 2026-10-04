"""Song-Analyse mit Cache in SQLite: Jeder Song wird nur einmal analysiert."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from backend.analysis.music.structure import ANALYSIS_VERSION, SongAnalysis, analyze_song
from backend.config.settings import MusicSettings
from backend.db.models import Song

log = logging.getLogger(__name__)

Analyzer = Callable[[Path, MusicSettings, str], SongAnalysis]


def settings_signature(cfg: MusicSettings) -> str:
    """Fingerabdruck aller Einstellungen, die das Ergebnis ändern (nicht analyzer, siehe load_song)."""
    values = {k: v for k, v in asdict(cfg).items() if k not in ("analyzer", "allin1_work_dir")}
    values["version"] = ANALYSIS_VERSION
    return hashlib.sha1(json.dumps(values, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def load_song(
    engine: Engine,
    path: Path,
    cfg: MusicSettings,
    analyzer: str | None = None,
    force: bool = False,
    analyze: Analyzer = analyze_song,
) -> SongAnalysis:
    """Holt die Analyse aus der Datenbank oder rechnet sie aus.

    analyzer=auto nimmt jedes gespeicherte Ergebnis. Wer nach dem Installieren von allin1 neu
    analysieren will, nimmt --analyzer allin1 (oder --force).
    """
    wanted = analyzer or cfg.analyzer
    key = str(path.resolve())
    stat = path.stat()
    signature = settings_signature(cfg)

    with Session(engine) as session:
        row = session.exec(select(Song).where(Song.path == key)).first()
        if (
            row is not None
            and not force
            and (row.file_size, row.file_mtime_ns) == (stat.st_size, stat.st_mtime_ns)
            and row.settings_signature == signature
            and (wanted == "auto" or row.analyzer == wanted)
        ):
            log.info("Song-Analyse aus der Datenbank: %s (%s)", path.name, row.analyzer)
            return SongAnalysis.from_dict(row.analysis)

        result = analyze(path, cfg, wanted)
        if row is None:
            row = Song(path=key, file_size=0, file_mtime_ns=0, settings_signature="", analyzer="", duration=0, bpm=0)
        row.file_size, row.file_mtime_ns = stat.st_size, stat.st_mtime_ns
        row.settings_signature = signature
        row.analyzer = result.analyzer
        row.duration, row.bpm = result.duration, result.bpm
        row.analysis = result.to_dict()
        row.analyzed_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        return result
