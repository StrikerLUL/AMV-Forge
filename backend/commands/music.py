"""CLI-Befehle für Phase 7: Musikbibliothek analysieren (music) und Songs vorschlagen (suggest)."""

from __future__ import annotations

import argparse
import logging
from collections import Counter
from pathlib import Path

from sqlmodel import Session, select

from backend.analysis.loader import ModelLoader
from backend.analysis.mood import MOODS, dominant
from backend.analysis.music.structure import SongAnalysis
from backend.analysis.video.faces import ClipFace
from backend.commands.common import fmt_time
from backend.commands.edit import DATA_DIR, index_hint, resolve_characters
from backend.commands.song import log_song_mood, short_mood
from backend.config.settings import Settings
from backend.config.styles import available_styles, load_style
from backend.db import get_engine
from backend.db.models import Clip, Episode, Season, Song
from backend.media import require_ffmpeg
from backend.music_index import MusicReport, index_library
from backend.planner.assign import Candidate
from backend.planner.suggest import LibrarySong, SeasonProfile, Suggestion, season_profile, suggest
from backend.sources.jellyfin import JellyfinClient
from backend.sources.music import scan_music_folder

log = logging.getLogger("amv_forge")


def add_music_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    music = sub.add_parser("music", help="Phase 7: Musikbibliothek analysieren (Tempo, Abschnitte, Stimmung, Tonart)")
    music.add_argument("--source", choices=["folder", "jellyfin"], default=None,
                       help="Woher die Songs kommen (Standard aus YAML: music_library.source)")
    music.add_argument("--path", type=Path, help="Ordner mit Songs, Unterordner zählen mit (bei --source folder)")
    music.add_argument("--library", help="Name der Jellyfin-Musikbibliothek (Standard: alle Songs in Jellyfin)")
    music.add_argument("--search", help="Nur Songs, deren Interpret, Titel, Album oder Dateiname das enthält")
    music.add_argument("--genre", help="Nur Songs mit diesem Genre (ein Teil reicht, z. B. Pop)")
    music.add_argument("--limit", type=int, default=None,
                       help="Höchstens so viele neue Songs in diesem Lauf analysieren (der Rest beim nächsten Mal)")
    music.add_argument("--analyzer", choices=["auto", "allin1", "librosa"], default=None,
                       help="Song-Struktur (Standard aus YAML: music.analyzer)")
    music.add_argument("--force", action="store_true", help="Alles neu analysieren")
    music.add_argument("--list", action="store_true", help="Nur anzeigen, welche Songs schon analysiert sind")
    music.add_argument("--config", type=Path, default=None, help="Eigene YAML statt backend/config/default.yaml")
    music.set_defaults(handler=run_music)

    sug = sub.add_parser("suggest", help="Phase 7: Die besten Songs für eine Staffel und einen Stil, mit Begründung")
    sug.add_argument("--season", type=int, required=True, help="DB-ID der Staffel (zeigt 'status')")
    sug.add_argument("--style", choices=available_styles(), required=True, help="Stil aus backend/styles/")
    sug.add_argument("--top", type=int, default=None, help="So viele Songs (Standard aus YAML: suggest.top)")
    sug.add_argument("--length", type=float, default=30.0, help="Länge des geplanten Edits in Sekunden")
    sug.add_argument("--characters", default=None,
                     help='Edit mit diesen Figuren geplant, z. B. "Hori,Miyamura" (zählt, ob genug Clips da sind)')
    sug.add_argument("--config", type=Path, default=None)
    sug.set_defaults(handler=run_suggest)


def _log_report(report: MusicReport) -> None:
    log.info("-" * 60)
    log.info("%d Songs gefunden: %d neu analysiert, %d schon in der Datenbank", report.found, report.analyzed,
             report.known)
    skipped = [f"{n} {what}" for n, what in ((report.filtered, "passen nicht zu --search/--genre"),
                                             (report.wrong_length, "zu kurz oder zu lang"),
                                             (report.failed, "gescheitert"),
                                             (report.failed_before, "schon früher gescheitert")) if n]
    if skipped:
        log.info("Übersprungen: %s", ", ".join(skipped))
    if report.pending:
        log.info("Noch offen (--limit): %d Songs. Einfach nochmal starten, dann geht es dort weiter.", report.pending)
    if report.computed_anything:
        log.info("Neu berechnet: Struktur %d, Stimmung %d, Downloads %d", report.structure, report.mood,
                 report.downloads)
    else:
        log.info("Nichts neu berechnet, alles kam aus der Datenbank.")


def _list_songs(session: Session) -> list[Song]:
    return list(session.exec(select(Song).order_by(Song.artist, Song.title, Song.path)).all())


def run_music(args: argparse.Namespace, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    if not args.list:
        require_ffmpeg()
        source = args.source or settings.music_library.source
        if source == "folder":
            if args.path is None:
                raise ValueError("Bei --source folder brauchst du --path <Ordner mit Songs>.")
            tracks = scan_music_folder(args.path, settings.music_library.audio_extensions)
        else:
            client = JellyfinClient.from_env(settings.apis.timeout_seconds)
            # Suche und Genre filtert schon Jellyfin, dann wird weniger übertragen
            tracks = client.music_tracks(settings.music_library.download_dir, args.library, args.search, args.genre)
        log.info("%d Songs in der Quelle (%s)", len(tracks), source)
        report = index_library(tracks, settings, engine, ModelLoader(settings), args.limit, args.search, args.genre,
                               args.analyzer, args.force)
        _log_report(report)

    with Session(engine) as session:
        rows = _list_songs(session)
    with_mood = [r for r in rows if r.mood is not None]
    if args.list:
        for r in with_mood:
            log.info("%-45s %6.1f BPM  %-8s %-6s %s", _label(r)[:45], r.bpm, r.musical_key or "-",
                     fmt_time(r.duration), short_mood(r.mood))
    counts = Counter(dominant(r.mood or {}) for r in with_mood)
    log.info("In der Datenbank: %d Songs mit Stimmung (stärkste Stimmung: %s)", len(with_mood),
             ", ".join(f"{m} {counts.get(m, 0)}" for m in MOODS))
    if len(rows) > len(with_mood):
        log.info("%d Songs ohne Stimmung (einzeln mit 'song' oder 'edit' analysiert): 'music' mit ihrem Ordner "
                 "oder 'song <Datei>' holt sie nach", len(rows) - len(with_mood))
    log.info("Vorschläge: python -m backend.cli suggest --season <ID> --style romance")


def _label(row: Song) -> str:
    title = row.title or Path(row.path).stem
    return f"{row.artist} - {title}" if row.artist else title


def _season_candidates(session: Session, season: Season, settings: Settings) -> list[Candidate]:
    """Alle Clips der Staffel mit Stimmung (ohne aussortierte). Die Dateien müssen dafür nicht da sein."""
    rows = session.exec(
        select(Clip, Episode).join(Episode, Clip.episode_id == Episode.id).where(Episode.season_id == season.id)
    ).all()
    if not rows:
        raise ValueError(f"Staffel {season.id} hat keine Clips. Erst 'index' laufen lassen.")
    if season.mood_signature is None or any(clip.mood is None for clip, _ in rows):
        raise ValueError(f"Die Stimmung der Clips fehlt noch (Phase 4). Einmal ausführen: {index_hint(season)}")
    result = []
    for clip, ep in rows:
        if clip.quality is not None and clip.quality < settings.quality.min_score:
            continue
        found = {int(k): float(v) for k, v in clip.characters.items()} if clip.characters is not None else None
        result.append(Candidate(Path(ep.path or ""), clip.start, clip.end, clip.motion, clip.motion_peak, ep.number,
                                clip_id=clip.id, mood=clip.mood, quality=clip.quality, speech=clip.speech,
                                characters=found, faces=ClipFace.from_db(clip.faces)))
    return result


def _library_songs(session: Session) -> tuple[list[LibrarySong], int, int]:
    """Analysierte Songs mit Stimmung, deren Datei noch da ist. Dazu: wie viele ohne Stimmung, wie viele ohne Datei."""
    songs: list[LibrarySong] = []
    no_mood = missing = 0
    for row in _list_songs(session):
        if row.mood is None or not row.analysis:
            no_mood += 1
            continue
        if not Path(row.path).exists():
            missing += 1
            continue
        songs.append(LibrarySong(path=row.path, title=row.title or Path(row.path).stem, artist=row.artist,
                                 analysis=SongAnalysis.from_dict(row.analysis), mood=row.mood, key=row.musical_key,
                                 features=row.features or {}))
    return songs, no_mood, missing


def _no_reason(s: Suggestion) -> str:
    if s.concerns:
        return "nichts passt besonders gut, mit mehr analysierten Songs gibt es bessere Vorschläge"
    return "nichts sticht heraus, aber nichts spricht dagegen"


def suggestion_lines(number: int, s: Suggestion, args: argparse.Namespace, length: float) -> list[str]:
    """Die Zeilen für einen Vorschlag (für die Ausgabe und die Textdatei)."""
    song = s.song
    lines = [f"{number}. {song.label} ({fmt_time(song.analysis.duration)}) | {song.analysis.bpm:.0f} BPM, "
             f"{song.key or '?'} | {short_mood(song.mood)} | passt zu {100 * s.score:.0f} %",
             f"   Warum: {'; '.join(s.reasons) or _no_reason(s)}"]
    if s.concerns:
        lines.append(f"   Aber: {'; '.join(s.concerns)}")
    lines.append(f"   Ausschnitt {fmt_time(s.window_start)} bis {fmt_time(s.window_start + length)}, {s.cuts} Schnitte")
    extra = f" --length {length:g}" if length != 30.0 else ""
    if args.characters:
        extra += f' --characters "{args.characters}"'
    lines.append(f'   python -m backend.cli edit --season {args.season} --style {args.style} --song "{song.path}"{extra}')
    return lines


def run_suggest(args: argparse.Namespace, settings: Settings) -> Path:
    style = load_style(args.style, settings.planner.weights)
    engine = get_engine(settings.database.path)
    with Session(engine) as session:
        season = session.get(Season, args.season)
        if season is None:
            raise ValueError(f"Keine Staffel mit DB-ID {args.season}. 'status' zeigt alle.")
        title = season.anilist_title or season.title
        wanted = resolve_characters(session, season, settings, args.characters) if args.characters else []
        if wanted and season.characters_signature is None:
            raise ValueError(f"Die Figuren der Clips fehlen noch (Phase 5). Einmal ausführen: {index_hint(season)}")
        candidates = _season_candidates(session, season, settings)
        songs, no_mood, missing = _library_songs(session)

    profile: SeasonProfile = season_profile(candidates, style, title, [c.anilist_id for c in wanted])
    who = f" mit {' + '.join(c.name for c in wanted)}" if wanted else ""
    log.info("%s, Stil %s: %d von %d Clips%s kommen in Frage", title, style.name, profile.pool, profile.clips, who)
    if profile.mood:
        log.info("Stimmung dieser Clips: %s", ", ".join(f"{m} {profile.mood[m]:.2f}" for m in
                                                        sorted(MOODS, key=lambda m: -profile.mood[m])))
    long_enough = [s for s in songs if s.analysis.duration >= args.length]
    log.info("%d Songs mit Stimmung in der Datenbank, %d davon mindestens %.0f s lang", len(songs), len(long_enough),
             args.length)
    if no_mood:
        log.info("%d Songs ohne Stimmung zählen nicht ('music' oder 'song <Datei>' holt sie nach)", no_mood)
    if missing:
        log.warning("%d Songs fehlen auf der Platte (gelöscht oder verschoben?) und zählen nicht", missing)
    if not long_enough:
        raise ValueError("Keine passenden Songs. Erst die Musik analysieren, z. B.: "
                         'python -m backend.cli music --source folder --path "C:\\Musik"')

    top = args.top or settings.suggest.top
    result = suggest(long_enough, style, profile, settings, args.length, top)
    lines = [f"Songvorschläge für {title}, Stil {style.name}{who} ({args.length:g}-s-Edit):", ""]
    for number, s in enumerate(result, start=1):
        lines += suggestion_lines(number, s, args, args.length) + [""]
    if len(result) < top:
        lines.append(f"Nur {len(result)} Vorschläge: mehr Songs analysieren ('music'), dann gibt es mehr Auswahl.")
    for line in lines:
        log.info("%s", line)

    out = DATA_DIR / "renders" / f"vorschlaege_s{args.season}_{style.name}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Zum Kopieren gespeichert: %s", out)
    return out
