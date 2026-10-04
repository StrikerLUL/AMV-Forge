"""Phase 2: Eine ganze Staffel einmal analysieren und in SQLite ablegen.

Jeder Schritt merkt sich in der Datenbank, dass er fertig ist. Ein zweiter Lauf
rechnet deshalb nur, was neu ist oder sich geändert hat.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.engine import Engine
from sqlalchemy import delete, func
from sqlmodel import Session, select

from backend.analysis.audio.op_ed_detect import Fingerprint, find_shared_segment, fingerprint
from backend.analysis.intervals import subtract
from backend.analysis.video.scenes import detect_scenes
from backend.config.settings import ApiSettings, Settings
from backend.db.models import Character, Clip, Episode, Season, SkipSegment
from backend.media import probe_duration, read_audio
from backend.sources import anilist, aniskip, jikan
from backend.sources.base import SourceEpisode, SourceSeason
from backend.sources.http import ApiClient, ApiError

log = logging.getLogger(__name__)

OP_KINDS = {"op", "mixed-op"}
ED_KINDS = {"ed", "mixed-ed"}


@dataclass
class Apis:
    anilist: ApiClient
    jikan: ApiClient
    aniskip: ApiClient

    @property
    def network_requests(self) -> int:
        return self.anilist.network_requests + self.jikan.network_requests + self.aniskip.network_requests


def make_apis(engine: Engine, cfg: ApiSettings, transport: httpx.BaseTransport | None = None) -> Apis:
    def client(service: str, url: str, interval: float) -> ApiClient:
        return ApiClient(engine, service, url, interval, cfg.timeout_seconds, cfg.max_retries, transport)

    return Apis(
        anilist=client("anilist", "https://graphql.anilist.co", cfg.anilist_interval),
        jikan=client("jikan", "https://api.jikan.moe/v4", cfg.jikan_interval),
        aniskip=client("aniskip", "https://api.aniskip.com", cfg.aniskip_interval),
    )


@dataclass
class IndexReport:
    season_id: int = 0
    title: str = ""
    episodes: int = 0
    clips_total: int = 0
    downloads: int = 0
    metadata_fetched: bool = False
    skips_computed: int = 0
    skips_by_source: dict[str, int] = field(default_factory=dict)
    scenes_computed: int = 0
    api_requests: int = 0

    @property
    def computed_anything(self) -> bool:
        return bool(
            self.downloads or self.metadata_fetched or self.skips_computed or self.scenes_computed or self.api_requests
        )


@dataclass
class _Work:
    """Eine Folge, für die in diesem Lauf etwas zu tun ist."""

    episode_id: int
    number: int
    path: Path
    duration: float
    needs_skips: bool
    op_ed_missing: set[str] = field(default_factory=set)


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _scenes_signature(path: Path, settings: Settings, skips: list[SkipSegment]) -> str:
    size, mtime = _file_signature(path)
    parts = [
        str(size), str(mtime),
        str(settings.scenes.adaptive_threshold), str(settings.scenes.min_scene_len_frames),
        str(settings.index.min_clip_seconds),
        *sorted(f"{s.kind}:{s.start:.2f}-{s.end:.2f}" for s in skips),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _upsert_season(session: Session, src: SourceSeason, anilist_override: int | None) -> Season:
    season = session.exec(select(Season).where(Season.key == src.key)).first()
    if season is None:
        season = Season(key=src.key, source=src.source, source_id=src.source_id, title=src.title,
                        season_number=src.season_number)
        log.info("Neue Staffel in der Datenbank: %s (Staffel %d)", src.title, src.season_number)
    season.title = src.title
    season.season_number = src.season_number
    wanted = anilist_override or src.anilist_id
    if wanted and season.anilist_id != wanted:
        season.anilist_id = wanted
        season.metadata_done = False
    if src.mal_id and not season.mal_id:
        season.mal_id = src.mal_id
    session.add(season)
    session.commit()
    session.refresh(season)
    return season


def _fetch_metadata(session: Session, season: Season, apis: Apis) -> bool:
    """AniList: IDs, Genres, Tags, Charaktere. Gibt True zurück, wenn etwas geladen wurde."""
    try:
        if season.anilist_id:
            info = anilist.get_anime(apis.anilist, season.anilist_id)
        else:
            info = anilist.find_anime(apis.anilist, season.title, season.season_number)
    except ApiError as exc:
        log.warning("AniList nicht erreichbar (%s), mache ohne Metadaten weiter", exc)
        return False
    if info is None:
        log.warning("Keine AniList-Daten. Tipp: --anilist-id angeben (Zahl aus der AniList-URL).")
        return False

    new_mal_id = info.mal_id or season.mal_id
    if season.mal_id and new_mal_id != season.mal_id:
        # Anderes Anime als beim letzten Lauf: AniSkip-Zeiten gehörten zum falschen Anime.
        log.info("MAL-ID geändert (%s -> %s), OP/ED wird neu gesucht", season.mal_id, new_mal_id)
        for ep in session.exec(select(Episode).where(Episode.season_id == season.id)):
            ep.skips_source = None
            session.add(ep)
    season.anilist_id = info.anilist_id
    season.mal_id = new_mal_id
    season.anilist_title = info.title
    season.genres = info.genres
    season.tags = info.tags
    season.metadata_done = True
    session.execute(delete(Character).where(Character.season_id == season.id))
    for ch in info.characters:
        session.add(Character(season_id=season.id, anilist_id=ch.anilist_id, name=ch.name, role=ch.role,
                              image_url=ch.image_url))
    session.add(season)
    session.commit()
    log.info(
        "AniList: %s (AniList %d, MAL %s), Genres: %s, %d Charaktere",
        info.title, info.anilist_id, info.mal_id or "?", ", ".join(info.genres) or "-", len(info.characters),
    )
    if season.season_number > 1 or not season.anilist_title:
        log.info("Falscher Treffer? Mit --anilist-id <ID> korrigieren.")
    return True


def _prepare_episode(
    session: Session,
    season: Season,
    src: SourceEpisode,
    meta: dict[int, jikan.EpisodeMeta],
    force: bool,
    report: IndexReport,
) -> _Work | None:
    ep = session.exec(
        select(Episode).where(Episode.season_id == season.id, Episode.number == src.number)
    ).first()
    if ep is None:
        ep = Episode(season_id=season.id, number=src.number)
    info = meta.get(src.number)
    ep.title = src.title or (info.title if info else None) or ep.title
    if info:
        ep.filler, ep.recap = info.filler, info.recap
    ep.source_item_id = src.source_item_id or ep.source_item_id

    complete = ep.skips_source is not None and ep.scenes_signature is not None
    path = src.local_path
    if path is None and complete and not force:
        # Fertig analysiert und Datei nicht lokal: nichts herunterladen.
        session.add(ep)
        session.commit()
        return None
    if path is None and src.fetch is not None:
        path = src.fetch()
        report.downloads += 1
    if path is None or not path.exists():
        log.warning("Folge %d: keine Datei gefunden, übersprungen", src.number)
        return None

    size, mtime = _file_signature(path)
    if ep.file_size is not None and (ep.file_size, ep.file_mtime_ns) != (size, mtime):
        log.info("Folge %d: Datei hat sich geändert, analysiere neu", src.number)
        ep.skips_source, ep.scenes_signature, ep.duration = None, None, None
    if force:
        ep.skips_source = None
    ep.path, ep.file_size, ep.file_mtime_ns = str(path), size, mtime
    if ep.duration is None:
        ep.duration = probe_duration(path)
    session.add(ep)
    session.commit()
    session.refresh(ep)
    assert ep.id is not None and ep.duration is not None
    return _Work(ep.id, ep.number, path, ep.duration, needs_skips=ep.skips_source is None)


def _aniskip_pass(session: Session, season: Season, work: list[_Work], apis: Apis | None) -> None:
    for w in work:
        if not w.needs_skips:
            continue
        # Erst die API fragen, dann schreiben: Der API-Cache nutzt eine eigene Verbindung,
        # und SQLite erlaubt nur einen Schreiber gleichzeitig.
        found: list[aniskip.SkipTime] = []
        if apis and season.mal_id:
            try:
                found = aniskip.skip_times(apis.aniskip, season.mal_id, w.number, w.duration)
            except ApiError as exc:
                log.warning("AniSkip Folge %d: %s", w.number, exc)
        session.execute(delete(SkipSegment).where(SkipSegment.episode_id == w.episode_id))
        for s in found:
            session.add(SkipSegment(episode_id=w.episode_id, kind=s.kind, start=s.start, end=s.end, source="aniskip"))
        kinds = {s.kind for s in found}
        w.op_ed_missing = set()
        if not kinds & OP_KINDS:
            w.op_ed_missing.add("op")
        if not kinds & ED_KINDS:
            w.op_ed_missing.add("ed")
        if found:
            log.info("Folge %d: AniSkip kennt %s", w.number, ", ".join(sorted(kinds)))
        session.commit()


def _fingerprint_pass(session: Session, settings: Settings, work: list[_Work], all_files: list[_Work]) -> None:
    """Sucht fehlende OP/EDs, indem die Folge mit ihren Nachbarfolgen verglichen wird."""
    cfg = settings.op_ed
    cache: dict[tuple[int, str], Fingerprint] = {}

    def fp(w: _Work, region: str) -> Fingerprint:
        key = (w.episode_id, region)
        if key not in cache:
            if region == "op":
                start, length = 0.0, min(cfg.head_seconds, w.duration)
            else:
                start = max(0.0, w.duration - cfg.tail_seconds)
                length = w.duration - start
            cache[key] = fingerprint(read_audio(w.path, start, length), start, cfg.silence_db)
        return cache[key]

    for w in work:
        if not w.needs_skips or not w.op_ed_missing:
            continue
        partners = sorted((p for p in all_files if p.episode_id != w.episode_id), key=lambda p: abs(p.number - w.number))
        for region in sorted(w.op_ed_missing):
            for partner in partners[:3]:
                seg = find_shared_segment(
                    fp(w, region), fp(partner, region),
                    cfg.similarity, cfg.min_seconds, cfg.max_seconds, cfg.max_gap_seconds,
                )
                if seg is not None:
                    session.add(SkipSegment(episode_id=w.episode_id, kind=region, start=seg.start_a,
                                            end=seg.end_a, source="fingerprint"))
                    w.op_ed_missing.discard(region)
                    log.info(
                        "Folge %d: %s per Audio-Vergleich mit Folge %d gefunden (%.0f-%.0f s)",
                        w.number, region.upper(), partner.number, seg.start_a, seg.end_a,
                    )
                    break
    session.commit()


def _finish_skips(session: Session, work: list[_Work], report: IndexReport) -> None:
    for w in work:
        if not w.needs_skips:
            continue
        sources = set(session.exec(select(SkipSegment.source).where(SkipSegment.episode_id == w.episode_id)).all())
        label = "+".join(sorted(sources)) if sources else "none"
        if w.op_ed_missing:
            log.warning("Folge %d: %s nicht gefunden, dieser Teil bleibt drin", w.number,
                        " und ".join(sorted(k.upper() for k in w.op_ed_missing)))
        ep = session.get(Episode, w.episode_id)
        assert ep is not None
        ep.skips_source = label
        session.add(ep)
        report.skips_computed += 1
        report.skips_by_source[label] = report.skips_by_source.get(label, 0) + 1
    session.commit()


def _scene_pass(session: Session, settings: Settings, work: list[_Work], force: bool, report: IndexReport) -> None:
    scene_cache = None if force else Path("data/cache/scenes")
    for w in work:
        ep = session.get(Episode, w.episode_id)
        assert ep is not None
        skips = list(session.exec(select(SkipSegment).where(SkipSegment.episode_id == w.episode_id)).all())
        signature = _scenes_signature(w.path, settings, skips)
        if ep.scenes_signature == signature and not force:
            continue
        log.info("Folge %d: Szenen ...", w.number)
        raw = detect_scenes(w.path, scene_cache, settings.scenes.adaptive_threshold,
                            settings.scenes.min_scene_len_frames)
        pieces = subtract([(s.start, s.end) for s in raw], [(s.start, s.end) for s in skips],
                          settings.index.min_clip_seconds)
        session.execute(delete(Clip).where(Clip.episode_id == w.episode_id))
        session.add_all(Clip(episode_id=w.episode_id, start=a, end=b) for a, b in pieces)
        ep.scenes_signature = signature
        session.add(ep)
        session.commit()
        report.scenes_computed += 1
        log.info("Folge %d: %d Szenen, %d Clips nach OP/ED-Filter", w.number, len(raw), len(pieces))


def index_season(
    src: SourceSeason,
    settings: Settings,
    engine: Engine,
    apis: Apis | None,
    force: bool = False,
    anilist_override: int | None = None,
) -> IndexReport:
    report = IndexReport(title=src.title, episodes=len(src.episodes))
    requests_before = apis.network_requests if apis else 0

    with Session(engine) as session:
        season = _upsert_season(session, src, anilist_override)
        assert season.id is not None
        report.season_id = season.id

        if apis and (force or not season.metadata_done):
            report.metadata_fetched = _fetch_metadata(session, season, apis)

        meta: dict[int, jikan.EpisodeMeta] = {}
        if apis and season.mal_id:
            try:
                meta = jikan.episode_meta(apis.jikan, season.mal_id)
            except ApiError as exc:
                log.warning("Jikan nicht erreichbar (%s), keine Filler-Infos", exc)

        work = [w for e in src.episodes if (w := _prepare_episode(session, season, e, meta, force, report))]

        if any(w.needs_skips for w in work):
            _aniskip_pass(session, season, work, apis)
            _fingerprint_pass(session, settings, work, work)
            _finish_skips(session, work, report)

        _scene_pass(session, settings, work, force, report)

        season.indexed_at = datetime.now(timezone.utc)
        session.add(season)
        session.commit()
        report.clips_total = session.exec(
            select(func.count(Clip.id)).join(Episode, Clip.episode_id == Episode.id).where(Episode.season_id == season.id)
        ).one()

    report.api_requests = (apis.network_requests if apis else 0) - requests_before
    return report
