"""CLI-Befehle, die ein Video schneiden: quick (1 Folge) und edit (ganze Staffel aus der Datenbank)."""

from __future__ import annotations

import argparse
import json
import logging
import random
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

from sqlmodel import Session, select

from backend.analysis.character_names import pick_characters
from backend.analysis.mood import MOODS, dominant, mood_match
from backend.analysis.music.energy import mean_energy
from backend.analysis.music.structure import SongAnalysis
from backend.analysis.video.motion import clip_motion, measure_motion
from backend.analysis.video.scenes import detect_scenes
from backend.commands.common import existing_file, fmt_time
from backend.commands.song import log_song
from backend.config.settings import Settings
from backend.config.styles import StyleProfile, available_styles, load_style
from backend.db import get_engine
from backend.character_index import season_characters
from backend.db.models import Character, Clip, Episode, Season
from backend.media import probe_duration, require_ffmpeg
from backend.planner.assign import Assignment, Candidate, assign_to_beats, usable_scenes
from backend.planner.scoring import character_tiers
from backend.planner.slots import Slot, build_slots, choose_song_start
from backend.planner.song_slots import build_song_slots, choose_edit_start, summarize
from backend.render.ffmpeg_graph import RenderOptions, render_edit
from backend.songs import load_song

log = logging.getLogger("amv_forge")

DATA_DIR = Path("data")


def _add_common(parser: argparse.ArgumentParser, default_out: Path) -> None:
    parser.add_argument("--song", type=existing_file, required=True, help="Song (MP3/WAV/FLAC)")
    parser.add_argument("--length", type=float, default=30.0, help="Länge des Edits in Sekunden")
    parser.add_argument("--out", type=Path, default=default_out, help="Ausgabedatei")
    parser.add_argument("--song-start", type=float, default=None,
                        help="Start im Song in Sekunden (Standard: so, dass der Drop bei 40 %% kommt)")
    parser.add_argument("--uniform", action="store_true",
                        help="Gleichmäßig schneiden wie in Phase 1, ohne Song-Struktur (zum Vergleich)")
    parser.add_argument("--beats-per-cut", type=int, default=None,
                        help="Mit --uniform: alle n Beats schneiden (Standard aus YAML: quick.beats_per_cut)")
    parser.add_argument("--analyzer", choices=["auto", "allin1", "librosa"], default=None,
                        help="Song-Analyse (Standard aus YAML: music.analyzer)")
    parser.add_argument("--seed", type=int, default=None, help="Zufalls-Seed für reproduzierbare Edits")
    parser.add_argument("--preview", action="store_true", help="Schnelle 480p-Vorschau statt 1080x1920")
    parser.add_argument("--no-music", action="store_true", help="Ohne eingebrannte Musik exportieren (für TikTok-Sounds)")
    parser.add_argument("--config", type=Path, default=None, help="Eigene YAML statt backend/config/default.yaml")


def add_edit_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    quick = sub.add_parser("quick", help="1 Folge + 1 Song, Schnitt folgt der Song-Struktur")
    quick.add_argument("--video", type=existing_file, required=True, help="Folge (MKV/MP4)")
    _add_common(quick, DATA_DIR / "renders" / "quick.mp4")
    quick.set_defaults(handler=run_quick)

    edit = sub.add_parser("edit", help="Edit aus einer indexierten Staffel (siehe 'index' und 'status')")
    edit.add_argument("--season", type=int, required=True, help="DB-ID der Staffel (zeigt 'status')")
    edit.add_argument("--style", choices=available_styles(), default=None,
                      help="Phase 4: Clips nach Stimmung wählen (backend/styles/<stil>.yaml)")
    edit.add_argument("--characters", default=None,
                      help='Phase 5: Szenen mit diesen Figuren, z. B. "Hori,Miyamura" (ein Teil des Namens reicht)')
    _add_common(edit, DATA_DIR / "renders" / "edit.mp4")
    edit.set_defaults(handler=run_edit)


def plan_slots(args: argparse.Namespace, settings: Settings, song: SongAnalysis) -> tuple[float, list[Slot]]:
    """Song-Ausschnitt und Schnittpunkte: nach Song-Struktur oder (mit --uniform) gleichmäßig."""
    if args.uniform or args.beats_per_cut:
        every = args.beats_per_cut or settings.quick.beats_per_cut
        start = choose_song_start(song.beats, song.duration, args.length, args.song_start)
        slots = build_slots(song.beats, start, args.length, every, settings.quick.min_slot_seconds)
        # Auch ohne Struktur soll die Bewegung zur Energie passen
        slots = [replace(s, intensity=round(mean_energy(song.energy, song.energy_rate, start + s.start,
                                                        start + s.end), 3)) for s in slots]
        log.info("Gleichmäßiger Schnitt: alle %d Beats", every)
        return start, slots
    start = choose_edit_start(song, args.length, args.song_start, settings.cuts.drop_position)
    return start, build_song_slots(song, start, args.length, settings.cuts, settings.quick.min_slot_seconds)


def _log_plan(song: SongAnalysis, start: float, slots: list[Slot], length: float) -> None:
    log.info("Ausschnitt im Song: %s bis %s", fmt_time(start), fmt_time(start + length))
    for drop in song.drops:
        if start <= drop.time < start + length:
            log.info("Drop im Edit bei %.1f s (im Song %s)", drop.time - start, fmt_time(drop.time))
    log.info("Schnitte nach Abschnitt:")
    for label, count, avg in summarize(slots):
        log.info("  %-8s %3d Clips, im Schnitt %.2f s lang", label or "gleich", count, avg)


def _finish(
    args: argparse.Namespace,
    settings: Settings,
    song: SongAnalysis,
    song_start: float,
    assignments: list[Assignment],
    seed: int,
    extra: dict[str, object],
    names: dict[int, str] | None = None,
) -> Path:
    aligned = sum(1 for a in assignments if a.aligned)
    log.info("Bewegungs-Peak genau auf dem Beat: %d von %d Clips", aligned, len(assignments))

    r = settings.render
    width, height = (r.preview_width, r.preview_height) if args.preview else (r.width, r.height)
    opts = RenderOptions(width=width, height=height, fps=r.fps, crf=r.crf, preset=r.preset,
                         audio_bitrate=r.audio_bitrate, with_music=not args.no_music)
    out = render_edit(assignments, args.song, song_start, args.out, opts)

    plan_file = out.with_suffix(".plan.json")
    plan = {
        **extra,
        "song": str(args.song),
        "analyzer": song.analyzer,
        "bpm": song.bpm,
        "song_start": song_start,
        "seed": seed,
        "style": getattr(args, "style", None),
        "sections": [asdict(s) for s in song.sections],
        "drops": [asdict(d) for d in song.drops],
        "clips": [
            {
                **{k: v for k, v in asdict(a.slot).items() if k != "hits"},
                "video": str(a.video) if a.video else None,
                "episode": a.episode,
                "source_start": round(a.source_start, 3),
                "peak_on_beat": a.aligned,
                "clip_id": a.candidate.clip_id if a.candidate else None,
                "mood": a.candidate.mood if a.candidate else None,
                "quality": a.candidate.quality if a.candidate else None,
                "speech": a.candidate.speech if a.candidate else None,
                "characters": _named(a.candidate.characters, names) if a.candidate else None,
                "score": a.score,
            }
            for a in assignments
        ],
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Schnittliste: %s", plan_file)
    return out


def _named(found: dict[int, float] | None, names: dict[int, str] | None) -> dict[str, float] | None:
    """{AniList-ID: Sicherheit} -> {Name: Sicherheit} für die Schnittliste."""
    if found is None:
        return None
    return {(names or {}).get(k, str(k)): v for k, v in sorted(found.items(), key=lambda kv: -kv[1])}


def _seed(args: argparse.Namespace) -> int:
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    log.info("Seed: %d (mit --seed %d bekommst du genau dieses Edit nochmal)", seed, seed)
    return seed


def run_quick(args: argparse.Namespace, settings: Settings) -> Path:
    require_ffmpeg()
    engine = get_engine(settings.database.path)
    song = load_song(engine, args.song, settings.music, args.analyzer)
    log_song(song)
    song_start, slots = plan_slots(args, settings, song)
    _log_plan(song, song_start, slots, args.length)

    video_duration = probe_duration(args.video)
    scenes = detect_scenes(args.video, DATA_DIR / "cache" / "scenes",
                           settings.scenes.adaptive_threshold, settings.scenes.min_scene_len_frames)
    scenes = usable_scenes(scenes, video_duration, settings.quick.skip_start_seconds, settings.quick.skip_end_seconds)
    curve = measure_motion(args.video, settings.motion, video_duration)
    candidates = []
    for s in scenes:
        stats = clip_motion(curve, s.start, s.end, settings.motion.edge_seconds, settings.motion.peak_smooth)
        candidates.append(Candidate(args.video, s.start, s.end,
                                    stats.motion if stats else None, stats.peak if stats else None))

    seed = _seed(args)
    p = settings.planner
    assignments = assign_to_beats(slots, candidates, random.Random(seed), p.weights, p.pick_from_top, 0,
                                  repeat_window=p.repeat_window)
    return _finish(args, settings, song, song_start, assignments, seed, {"video": str(args.video)})


def _index_hint(season: Season) -> str:
    if season.source == "folder":
        return f'python -m backend.cli index --source folder --path "{season.source_id}"'
    return f"python -m backend.cli index --source jellyfin --season {season.source_id}"


def _load_candidates(session: Session, season: Season, settings: Settings, need_mood: bool,
                     need_characters: bool = False) -> list[Candidate]:
    rows = session.exec(
        select(Clip, Episode).join(Episode, Clip.episode_id == Episode.id).where(Episode.season_id == season.id)
    ).all()
    if not rows:
        raise ValueError(f"Staffel {season.id} hat keine Clips. Erst 'index' laufen lassen.")

    no_motion = sorted({ep.number for _, ep in rows if ep.motion_signature is None})
    if no_motion:
        raise ValueError(f"Für Folge {', '.join(map(str, no_motion))} fehlt noch die Bewegung (neu in Phase 3). "
                         f"Einmal ausführen: {_index_hint(season)}")
    if need_mood and (season.mood_signature is None or any(clip.mood is None for clip, _ in rows)):
        raise ValueError(f"Die Stimmung der Clips fehlt noch (neu in Phase 4). Einmal ausführen: {_index_hint(season)}")
    if need_characters and season.characters_signature is None:
        raise ValueError(f"Die Figuren der Clips fehlen noch (neu in Phase 5). Einmal ausführen: {_index_hint(season)}")

    missing: set[int] = set()
    dropped: Counter[str] = Counter()
    candidates: list[Candidate] = []
    for clip, ep in rows:
        if not ep.path or not Path(ep.path).exists():
            missing.add(ep.number)
            continue
        if clip.quality is not None and clip.quality < settings.quality.min_score:
            dropped[clip.quality_issue or "niedrig"] += 1
            continue
        found = {int(k): float(v) for k, v in clip.characters.items()} if clip.characters is not None else None
        candidates.append(Candidate(Path(ep.path), clip.start, clip.end, clip.motion, clip.motion_peak, ep.number,
                                    clip_id=clip.id, mood=clip.mood, quality=clip.quality, speech=clip.speech,
                                    characters=found))
    if missing:
        log.warning("Datei fehlt für Folge %s, deren Clips werden übersprungen", ", ".join(map(str, sorted(missing))))
    if dropped:
        log.info("Wegen Bildqualität aussortiert: %d Clips (%s)", sum(dropped.values()),
                 ", ".join(f"{k} {v}" for k, v in dropped.most_common()))
    if not candidates:
        raise ValueError("Keine Folge der Staffel ist als Datei vorhanden.")
    return candidates


def _log_moods(assignments: list[Assignment], candidates: list[Candidate], style: StyleProfile | None) -> None:
    """Zeigt, welche Stimmung im fertigen Edit steckt (zum Vergleich von --style romance und hype)."""
    moods = [a.candidate.mood for a in assignments if a.candidate and a.candidate.mood]
    if not moods:
        return
    average = {m: sum(x[m] for x in moods) / len(moods) for m in MOODS}
    log.info("Stimmung im Edit (Durchschnitt 0-1): %s", ", ".join(f"{m} {average[m]:.2f}" for m in MOODS))
    counts = Counter(dominant(x) for x in moods)
    log.info("Stärkste Stimmung pro Clip: %s", ", ".join(f"{m} {n}" for m, n in counts.most_common()))
    if style is not None:
        season = [c.mood for c in candidates if c.mood]
        in_edit = sum(mood_match(x, style.mood) for x in moods) / len(moods)
        overall = sum(mood_match(x, style.mood) for x in season) / max(1, len(season))
        log.info("Passt zum Stil '%s': im Edit %.2f, Staffel-Durchschnitt %.2f (1 = perfekt)", style.name, in_edit,
                 overall)


def _resolve_characters(session: Session, season: Season, settings: Settings, text: str) -> list[Character]:
    """--characters "Hori,Miyamura" -> die passenden Figuren der Staffel (Teil des Namens reicht)."""
    known = season_characters(session, season, settings.characters.roles)
    if not known:
        raise ValueError("Für diese Staffel sind keine Figuren von AniList gespeichert (Lauf mit --no-api?). "
                         f"Einmal ausführen: {_index_hint(season)}")
    return pick_characters(text, known)


def _character_names(session: Session, season: Season) -> dict[int, str]:
    return {c.anilist_id: c.name for c in session.exec(select(Character).where(Character.season_id == season.id))}


def _log_available(candidates: list[Candidate], wanted: list[Character], slots: int) -> None:
    ids = [c.anilist_id for c in wanted]
    every, some = character_tiers(candidates, ids)
    together = " + ".join(c.name for c in wanted)
    if len(wanted) == 1:
        log.info("Clips mit %s: %d von %d", together, len(every), len(candidates))
    else:
        log.info("Clips mit %s zusammen: %d, mit mindestens einer davon: %d (von %d)", together, len(every),
                 len(some), len(candidates))
    if len(every) < slots:
        log.warning("Nur %d Clips mit %s für %d Schnitte: danach kommen %sWiederholungen", len(every), together,
                    slots, "Clips mit einer davon, dann " if len(some) > len(every) else "")
    unsearched = sum(1 for c in candidates if c.characters is None)
    if unsearched:
        log.warning("%d Clips ohne Gesichtersuche (Folge noch nicht fertig indexiert?)", unsearched)


def _log_characters(assignments: list[Assignment], wanted: list[Character], names: dict[int, str]) -> None:
    """Zeigt, wer im fertigen Edit zu sehen ist (zum Prüfen von --characters)."""
    sets = [a.candidate.character_ids if a.candidate else frozenset() for a in assignments]
    if wanted:
        ids = {c.anilist_id for c in wanted}
        parts = []
        if len(wanted) > 1:
            parts.append(f"{'beide' if len(wanted) == 2 else 'alle'} {sum(1 for s in sets if ids <= s)}")
            parts += [f"nur {c.name} {sum(1 for s in sets if s & ids == {c.anilist_id})}" for c in wanted]
        else:
            parts.append(f"mit {wanted[0].name} {sum(1 for s in sets if ids <= s)}")
        parts.append(f"ohne {sum(1 for s in sets if not s & ids)}")
        log.info("Figuren im Edit (%d Clips): %s", len(sets), ", ".join(parts))
        return
    counts = Counter(k for s in sets for k in s)
    if counts:
        log.info("Figuren im Edit: %s", ", ".join(f"{names.get(k, k)} {n}" for k, n in counts.most_common(6)))


def run_edit(args: argparse.Namespace, settings: Settings) -> Path:
    require_ffmpeg()
    style = load_style(args.style, settings.planner.weights) if args.style else None
    engine = get_engine(settings.database.path)
    with Session(engine) as session:
        season = session.get(Season, args.season)
        if season is None:
            raise ValueError(f"Keine Staffel mit DB-ID {args.season}. 'status' zeigt alle.")
        wanted = _resolve_characters(session, season, settings, args.characters) if args.characters else []
        candidates = _load_candidates(session, season, settings, need_mood=style is not None,
                                      need_characters=bool(wanted))
        names = _character_names(session, season)
        title = season.anilist_title or season.title
    log.info("%s: %d Clips aus %d Folgen", title, len(candidates), len({c.episode for c in candidates}))
    if style is not None:
        log.info("Stil %s: %s (die besten %.0f %% nach Stimmung kommen in Frage)", style.name, style.description,
                 style.pool * 100)

    song = load_song(engine, args.song, settings.music, args.analyzer)
    log_song(song)
    song_start, slots = plan_slots(args, settings, song)
    _log_plan(song, song_start, slots, args.length)
    if wanted:
        _log_available(candidates, wanted, len(slots))

    seed = _seed(args)
    p = settings.planner
    assignments = assign_to_beats(
        slots, candidates, random.Random(seed),
        weights=style.weights if style else p.weights,
        pick_from_top=p.pick_from_top,
        max_same_episode_in_row=p.max_same_episode_in_row,
        target=style.mood if style else None,
        pool_share=style.pool if style else 1.0,
        repeat_window=p.repeat_window,
        characters=[c.anilist_id for c in wanted],
        max_same_character_in_row=p.max_same_character_in_row,
    )
    episodes = [a.episode for a in assignments]
    log.info("Folgen im Edit: %s", ", ".join(f"{n}x Folge {e}" for e, n in
                                             sorted({e: episodes.count(e) for e in episodes}.items())))
    _log_moods(assignments, candidates, style)
    _log_characters(assignments, wanted, names)
    extra: dict[str, object] = {"season": args.season, "title": title, "characters": [c.name for c in wanted]}
    return _finish(args, settings, song, song_start, assignments, seed, extra, names)
