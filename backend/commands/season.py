"""CLI-Befehle für Phase 2: Staffel indexieren, Status anzeigen, in Jellyfin suchen."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from sqlmodel import Session, func, select

from backend.commands.common import fmt_time
from backend.config.settings import Settings
from backend.db import get_engine
from backend.db.models import Character, Clip, Episode, Season, SkipSegment
from backend.indexer import index_season, make_apis
from backend.media import require_ffmpeg
from backend.sources.folder import scan_folder
from backend.sources.jellyfin import JellyfinClient

log = logging.getLogger("amv_forge")


def add_season_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    index = sub.add_parser("index", help="Phase 2: ganze Staffel analysieren und in SQLite speichern")
    index.add_argument("--source", choices=["folder", "jellyfin"], default=None,
                       help="Videoquelle (Standard aus YAML: index.source)")
    index.add_argument("--path", type=Path, help="Ordner mit den Folgen (bei --source folder)")
    index.add_argument("--season", help="Jellyfin-ID der Staffel (bei --source jellyfin, siehe jellyfin-search)")
    index.add_argument("--title", help="Anime-Titel für die AniList-Suche (Standard: Ordner-/Serienname)")
    index.add_argument("--season-number", type=int, help="Staffelnummer, falls nicht aus dem Namen erkennbar")
    index.add_argument("--anilist-id", type=int, help="AniList-ID direkt angeben, wenn die Suche danebenliegt")
    index.add_argument("--no-api", action="store_true", help="Ohne AniList/Jikan/AniSkip, OP/ED nur per Audio-Vergleich")
    index.add_argument("--force", action="store_true", help="Alles neu berechnen")
    index.add_argument("--config", type=Path, default=None, help="Eigene YAML statt backend/config/default.yaml")
    index.set_defaults(handler=run_index)

    status = sub.add_parser("status", help="Zeigt, was in der Datenbank liegt")
    status.add_argument("--season", type=int, help="DB-ID einer Staffel für Details pro Folge")
    status.add_argument("--config", type=Path, default=None)
    status.set_defaults(handler=run_status)

    search = sub.add_parser("jellyfin-search", help="Serie in Jellyfin suchen und Staffel-IDs anzeigen")
    search.add_argument("term", help="Suchbegriff, z. B. Horimiya")
    search.set_defaults(handler=run_jellyfin_search)


def run_index(args: argparse.Namespace, settings: Settings) -> None:
    require_ffmpeg()
    source = args.source or settings.index.source
    if source == "folder":
        if args.path is None:
            raise ValueError("Bei --source folder brauchst du --path <Ordner mit den Folgen>.")
        src = scan_folder(args.path, settings.index.video_extensions, args.title, args.season_number)
    else:
        if not args.season:
            raise ValueError("Bei --source jellyfin brauchst du --season <ID>. Die ID zeigt dir 'jellyfin-search'.")
        src = JellyfinClient.from_env(settings.apis.timeout_seconds).load_season(
            args.season, settings.index.download_dir
        )
        if args.title:
            src.title = args.title
        if args.season_number:
            src.season_number = args.season_number
    log.info("%s, Staffel %d: %d Folgen gefunden (%s)", src.title, src.season_number, len(src.episodes), source)

    engine = get_engine(settings.database.path)
    apis = None if args.no_api else make_apis(engine, settings.apis)
    report = index_season(src, settings, engine, apis, force=args.force, anilist_override=args.anilist_id)

    log.info("-" * 60)
    log.info("Staffel '%s' (DB-ID %d): %d Folgen, %d Clips ohne OP/ED",
             report.title, report.season_id, report.episodes, report.clips_total)
    if not report.computed_anything:
        log.info("Nichts neu berechnet, alles kam aus der Datenbank.")
        return
    by_source = ", ".join(f"{k}: {v}" for k, v in sorted(report.skips_by_source.items())) or "-"
    log.info("Neu berechnet: OP/ED %d (%s), Szenen %d, Bewegung %d, Downloads %d, API-Anfragen %d",
             report.skips_computed, by_source, report.scenes_computed, report.motion_computed, report.downloads,
             report.api_requests)
    m = report.mood
    log.info("Phase 4: Standbilder %d, CLIP-Vergleich %d, Ton %d, Untertitel %d, Stimmung %s",
             m.visual, m.tags, m.audio, m.subtitles, "neu gemischt" if m.mood else "unverändert")
    c = report.characters
    log.info("Phase 5: Gesichter %d, Bilder von AniList %d, Figuren %s", c.faces, c.downloads,
             "neu zugeordnet" if c.matched else "unverändert")
    log.info("Details: python -m backend.cli status --season %d", report.season_id)
    log.info("Stimmung ansehen: python -m backend.cli moods --season %d", report.season_id)
    log.info("Figuren ansehen: python -m backend.cli characters --season %d", report.season_id)


def run_status(args: argparse.Namespace, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    with Session(engine) as session:
        if args.season is None:
            seasons = session.exec(select(Season)).all()
            if not seasons:
                log.info("Noch keine Staffel in der Datenbank. Starte mit 'index'.")
            for s in seasons:
                episodes = session.exec(select(func.count(Episode.id)).where(Episode.season_id == s.id)).one()
                clips = session.exec(
                    select(func.count(Clip.id)).join(Episode, Clip.episode_id == Episode.id)
                    .where(Episode.season_id == s.id)
                ).one()
                log.info("[%d] %s, Staffel %d (%s): %d Folgen, %d Clips, AniList %s, MAL %s",
                         s.id, s.anilist_title or s.title, s.season_number, s.source, episodes, clips,
                         s.anilist_id or "-", s.mal_id or "-")
            return

        season = session.get(Season, args.season)
        if season is None:
            raise ValueError(f"Keine Staffel mit DB-ID {args.season}. 'status' ohne --season zeigt alle.")
        log.info("%s, Staffel %d | Genres: %s", season.anilist_title or season.title, season.season_number,
                 ", ".join(season.genres) or "-")
        top_tags = sorted(season.tags, key=lambda t: -t.get("rank", 0))[:8]
        if top_tags:
            log.info("Tags: %s", ", ".join(f"{t['name']} ({t['rank']}%)" for t in top_tags))
        main_chars = session.exec(
            select(Character).where(Character.season_id == season.id, Character.role == "MAIN")
        ).all()
        if main_chars:
            log.info("Hauptfiguren: %s", ", ".join(c.name for c in main_chars))
        log.info("%-4s %-8s %-15s %-15s %-20s %6s %-9s %-9s %-10s %-16s %s", "Nr", "Länge", "OP", "ED", "Quelle",
                 "Clips", "Bewegung", "Stimmung", "Gesichter", "Untertitel", "Titel")
        for ep in session.exec(select(Episode).where(Episode.season_id == season.id).order_by(Episode.number)):
            skips = session.exec(select(SkipSegment).where(SkipSegment.episode_id == ep.id)).all()

            def span(kinds: set[str]) -> str:
                hit = next((s for s in skips if s.kind in kinds), None)
                return f"{fmt_time(hit.start)}-{fmt_time(hit.end)}" if hit else "-"

            clips = session.exec(select(func.count(Clip.id)).where(Clip.episode_id == ep.id)).one()
            flags = " [Filler]" if ep.filler else (" [Recap]" if ep.recap else "")
            mood_done = all((ep.visual_signature, ep.tags_signature, ep.audio_signature, ep.subtitle_signature))
            log.info("%-4d %-8s %-15s %-15s %-20s %6d %-9s %-9s %-10s %-16s %s%s", ep.number,
                     fmt_time(ep.duration) if ep.duration else "-", span({"op", "mixed-op"}),
                     span({"ed", "mixed-ed"}), ep.skips_source or "offen", clips,
                     "ja" if ep.motion_signature else "offen", "ja" if mood_done else "offen",
                     "ja" if ep.faces_signature else "offen", (ep.subtitle_source or "offen")[:16], ep.title or "",
                     flags)


def run_jellyfin_search(args: argparse.Namespace, settings: Settings) -> None:
    client = JellyfinClient.from_env(settings.apis.timeout_seconds)
    series_list = client.search_series(args.term)
    if not series_list:
        log.info("Keine Serie gefunden für '%s'", args.term)
    for series in series_list:
        log.info("%s (%s)", series.get("Name"), series.get("ProductionYear") or "?")
        for season in client.seasons(series["Id"]):
            log.info("    %-12s --season %s", season.get("Name"), season["Id"])
