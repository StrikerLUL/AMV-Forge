"""Phase 7: Songs analysieren (Struktur aus Phase 3, dazu Stimmung und Tonart) und in SQLite merken.

Wie beim Staffel-Index merkt sich jeder Song einen Fingerabdruck (Signatur): Datei, Einstellungen,
CLAP-Modell und Sätze. Ein zweiter Lauf rechnet deshalb nichts neu. Ändern sich nur die Sätze in
music_prompts.yaml, wird nur neu verglichen (das CLAP-Embedding liegt in data/cache/clap_music/).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import librosa
import numpy as np
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from backend.analysis.loader import NONE, ModelLoader
from backend.analysis.music.clap import AudioTextEmbedder, load_music_prompts, window_starts, zero_shot
from backend.analysis.music.mood import (MUSIC_MOOD_VERSION, arousal_valence, combine, feature_mood, measure)
from backend.analysis.music.structure import SongAnalysis
from backend.analysis.video.clip_tags import file_hash
from backend.config.settings import MusicMoodSettings, Settings
from backend.db.models import Song
from backend.media import read_audio
from backend.signatures import file_signature, signature
from backend.songs import is_current, load_song, song_key
from backend.sources.music import LibraryTrack, with_tags

log = logging.getLogger(__name__)

SAMPLE_RATE = 22050


@dataclass
class MusicReport:
    found: int = 0  # Songs in der Quelle
    filtered: int = 0  # passen nicht zu --search/--genre
    wrong_length: int = 0  # zu kurz oder zu lang
    known: int = 0  # waren schon fertig in der Datenbank
    analyzed: int = 0  # in diesem Lauf (neu) analysiert
    structure: int = 0  # davon Song-Struktur neu (Phase 3)
    mood: int = 0  # davon Stimmung und Tonart neu
    downloads: int = 0
    failed: int = 0  # in diesem Lauf gescheitert
    failed_before: int = 0  # schon früher gescheitert, nicht nochmal probiert
    pending: int = 0  # wegen --limit noch nicht dran

    @property
    def computed_anything(self) -> bool:
        return bool(self.analyzed or self.downloads or self.failed)


def _mood_settings_values(cfg: MusicMoodSettings) -> dict[str, Any]:
    return {k: v for k, v in asdict(cfg).items() if k not in ("cache_dir", "device", "clap_enabled", "prompts")}


def mood_signature(path: Path, bpm: float, cfg: MusicMoodSettings, model_name: str) -> str:
    prompts = file_hash(cfg.prompts) if model_name != NONE else ""
    return signature(MUSIC_MOOD_VERSION, file_signature(path), bpm, json.dumps(_mood_settings_values(cfg),
                                                                              sort_keys=True, default=str),
                     model_name, prompts)


def _embeddings_file(cfg: MusicMoodSettings, path: Path, model: AudioTextEmbedder) -> Path:
    key = signature(file_signature(path), model.name, cfg.windows, model.window_seconds)
    return cfg.cache_dir / f"{hashlib.sha1(str(path.resolve()).encode('utf-8')).hexdigest()[:12]}_{key}.npy"


def clap_embeddings(path: Path, duration: float, model: AudioTextEmbedder, cfg: MusicMoodSettings) -> np.ndarray:
    """Ein CLAP-Embedding pro 10-s-Stück des Songs, gespeichert als .npy (neue Sätze brauchen kein neues Anhören)."""
    target = _embeddings_file(cfg, path, model)
    if target.exists():
        return np.load(target)
    starts = window_starts(duration, cfg.windows, model.window_seconds)
    windows = [read_audio(path, start, model.window_seconds, model.sample_rate) for start in starts]
    embeddings = model.embed_audio([w for w in windows if len(w)])
    target.parent.mkdir(parents=True, exist_ok=True)
    np.save(target, embeddings.astype(np.float32))
    return embeddings


def analyze_mood(path: Path, song: SongAnalysis, settings: Settings,
                 models: ModelLoader) -> tuple[dict[str, float], str, dict[str, Any], str]:
    """Stimmung, Tonart und Messwerte eines Songs. Gibt auch den Namen des CLAP-Modells zurück (oder none)."""
    cfg = settings.music_mood
    y, sr = librosa.load(str(path), sr=SAMPLE_RATE, mono=True)
    features = measure(y, sr, song.bpm)
    arousal, valence = arousal_valence(features, cfg)
    signals = {"features": feature_mood(arousal, valence, cfg)}
    extra: dict[str, Any] = {**features.to_dict(), "arousal": arousal, "valence": valence}

    model = models.clap() if models.clap_name() != NONE else None
    if model is not None:
        prompts = load_music_prompts(cfg.prompts)
        tags = zero_shot(clap_embeddings(path, song.duration, model, cfg), models.music_text_embeddings(model, prompts),
                         prompts, model.logit_scale)
        signals["clap"] = tags.mood
        extra["clap_top"] = tags.top_prompt
        extra["clap_probs"] = tags.probs
    extra["signals"] = signals
    return combine(signals, cfg.weights), features.key, extra, model.name if model else NONE


def _row(session: Session, path: Path) -> Song | None:
    return session.exec(select(Song).where(Song.path == song_key(path))).first()


def mood_is_current(row: Song | None, path: Path, settings: Settings, model_name: str) -> bool:
    return (row is not None and row.mood is not None and bool(row.analysis)
            and row.mood_signature == mood_signature(path, row.bpm, settings.music_mood, model_name))


def mood_is_known(row: Song | None, path: Path, settings: Settings, models: ModelLoader) -> bool:
    """Stimmung schon gerechnet? Ohne CLAP gerechnet zählt auch, solange CLAP weiter nicht lädt.

    Dann wird CLAP einmal pro Lauf probiert: Lädt es diesmal, rechnet der Lauf die Stimmung mit CLAP nach,
    sonst bleibt alles, wie es ist (statt bei jedem Lauf alle Songs umsonst neu zu messen).
    """
    wanted = models.clap_name()
    if mood_is_current(row, path, settings, wanted):
        return True
    if wanted == NONE or not mood_is_current(row, path, settings, NONE):
        return False
    return models.clap() is None


def analyze_one(engine: Engine, path: Path, settings: Settings, models: ModelLoader, analyzer: str | None = None,
                force: bool = False, track: LibraryTrack | None = None,
                report: MusicReport | None = None) -> Song:
    """Struktur (Phase 3) und Stimmung (Phase 7) eines Songs, jeweils nur, wenn noch nicht in der Datenbank."""
    report = report if report is not None else MusicReport()
    wanted = analyzer or settings.music.analyzer
    with Session(engine) as session:
        row = _row(session, path)
        structure_known = row is not None and not force and is_current(row, path, settings.music, wanted)
    song = load_song(engine, path, settings.music, analyzer, force)
    if not structure_known:
        report.structure += 1

    with Session(engine) as session:
        row = _row(session, path)
        assert row is not None
        if force or not mood_is_known(row, path, settings, models):
            mood, key, features, model_name = analyze_mood(path, song, settings, models)
            new_signature = mood_signature(path, song.bpm, settings.music_mood, model_name)
            if force or row.mood_signature != new_signature or row.mood is None:
                report.mood += 1
            row.mood, row.musical_key, row.features, row.mood_signature = mood, key, features, new_signature
        if track is not None:
            row.title, row.artist, row.album = track.title, track.artist, track.album
            row.genres, row.source, row.source_id = list(track.genres), track.source, track.source_id
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


class FailedSongs:
    """Songs, deren Analyse scheiterte (kaputte Datei, kein Rhythmus): werden nicht bei jedem Lauf neu probiert."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.entries: dict[str, str] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            self.entries = {}

    def reason(self, file: Path) -> str | None:
        return self.entries.get(file_signature(file))

    def add(self, file: Path, reason: str) -> None:
        self.entries[file_signature(file)] = reason
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=1, ensure_ascii=False), encoding="utf-8")


def _known_jellyfin_path(session: Session, track: LibraryTrack) -> Path | None:
    """Schon heruntergeladen und analysiert? Dann kein neuer Download."""
    if track.source_id is None:
        return None
    row = session.exec(select(Song).where(Song.source == "jellyfin", Song.source_id == track.source_id)).first()
    return Path(row.path) if row is not None and Path(row.path).exists() else None


def _fill_from_db(session: Session, track: LibraryTrack) -> LibraryTrack:
    """Titel und Tags eines Ordner-Songs aus der Datenbank, wenn die Datei sich nicht geändert hat (spart ffprobe)."""
    if track.tagged or track.local_path is None:
        return track
    row = _row(session, track.local_path)
    if row is None or row.title is None:
        return track
    stat = track.local_path.stat()
    if (row.file_size, row.file_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
        return track
    return replace(track, title=row.title, artist=row.artist, album=row.album, genres=list(row.genres or []),
                   duration=row.duration, tagged=True)


def index_library(
    tracks: list[LibraryTrack],
    settings: Settings,
    engine: Engine,
    models: ModelLoader,
    limit: int | None = None,
    search: str | None = None,
    genre: str | None = None,
    analyzer: str | None = None,
    force: bool = False,
) -> MusicReport:
    """Analysiert alle Songs, die noch fehlen. limit = höchstens so viele neue Songs in diesem Lauf."""
    cfg = settings.music_library
    report = MusicReport(found=len(tracks))
    failed = FailedSongs(cfg.download_dir / "failed.json")
    wanted = analyzer or settings.music.analyzer
    todo: list[LibraryTrack] = []

    for track in tracks:
        with Session(engine) as session:
            if track.source == "jellyfin" and track.local_path is None:
                known = _known_jellyfin_path(session, track)
                track = replace(track, local_path=known) if known else track
            track = _fill_from_db(session, track)
        if track.local_path is not None and not force and failed.reason(track.local_path) is not None:
            report.failed_before += 1
            continue
        try:
            track = with_tags(track)
        except (RuntimeError, ValueError) as exc:  # ffprobe kann die Datei nicht lesen
            log.warning("%s übersprungen (wird beim nächsten Lauf nicht nochmal probiert): %s", track.label, exc)
            report.failed += 1
            if track.local_path is not None:
                failed.add(track.local_path, str(exc))
            continue
        if not track.matches(search, genre):
            report.filtered += 1
            continue
        if track.duration is not None and not cfg.min_seconds <= track.duration <= cfg.max_minutes * 60:
            report.wrong_length += 1
            continue
        if track.local_path is not None and not force:
            with Session(engine) as session:
                row = _row(session, track.local_path)
                if (row is not None and is_current(row, track.local_path, settings.music, wanted)
                        and mood_is_known(row, track.local_path, settings, models)):
                    report.known += 1
                    if (row.title, row.source_id) != (track.title, track.source_id):  # Tags ergänzen, ohne neu zu rechnen
                        row.title, row.artist, row.album = track.title, track.artist, track.album
                        row.genres, row.source, row.source_id = list(track.genres), track.source, track.source_id
                        session.add(row)
                        session.commit()
                    continue
        todo.append(track)

    if limit is not None and len(todo) > limit:
        report.pending = len(todo) - limit
        todo = todo[:limit]
    for number, track in enumerate(todo, start=1):
        started = time.monotonic()
        log.info("Song %d/%d: %s", number, len(todo), track.label)
        try:
            path = track.local_path
            if path is None:
                assert track.fetch is not None
                path = track.fetch()
                report.downloads += 1
            if failed.reason(path) is not None and not force:
                report.failed_before += 1
                continue
            analyze_one(engine, path, settings, models, analyzer, force, track, report)
        except Exception as exc:  # kaputte Datei, kein Rhythmus, Download-Fehler: Rest der Bibliothek trotzdem
            log.warning("  gescheitert (%s: %s), wird beim nächsten Lauf übersprungen", type(exc).__name__, exc)
            report.failed += 1
            if path is not None and path.exists():  # ohne Datei (Download gescheitert) beim nächsten Mal neu probieren
                failed.add(path, f"{type(exc).__name__}: {exc}")
            continue
        report.analyzed += 1
        log.info("  fertig in %.1f s", time.monotonic() - started)
    return report
