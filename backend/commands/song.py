"""CLI-Befehl für Phase 3: Song analysieren und Struktur anzeigen (ab Phase 7 auch Stimmung und Tonart)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from backend.analysis.loader import ModelLoader
from backend.analysis.mood import MOODS
from backend.analysis.music.mood import describe
from backend.analysis.music.structure import SongAnalysis
from backend.commands.common import existing_file, fmt_time
from backend.config.settings import Settings
from backend.db import get_engine
from backend.db.models import Song
from backend.music_index import analyze_one
from backend.planner.song_slots import choose_edit_start

log = logging.getLogger("amv_forge")


def add_song_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    song = sub.add_parser("song", help="Song analysieren: Tempo, Abschnitte, Drops (Phase 3), Stimmung und Tonart (Phase 7)")
    song.add_argument("path", type=existing_file, help="Song (MP3/WAV/FLAC)")
    song.add_argument("--analyzer", choices=["auto", "allin1", "librosa"], default=None,
                      help="Standard aus YAML (music.analyzer): allin1, wenn installiert, sonst librosa")
    song.add_argument("--length", type=float, default=30.0, help="Für den Vorschlag, wo das Edit startet")
    song.add_argument("--force", action="store_true", help="Neu analysieren, auch wenn es schon in der DB liegt")
    song.add_argument("--config", type=Path, default=None)
    song.set_defaults(handler=run_song)


def _bar(value: float, width: int = 10) -> str:
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "#" * filled + "." * (width - filled)


def log_song(song: SongAnalysis) -> None:
    log.info("%s | %.1f BPM | %s | Analyse: %s | %d Beats, %d Takte",
             Path(song.path).name, song.bpm, fmt_time(song.duration), song.analyzer,
             len(song.beats), len(song.downbeats))
    log.info("Abschnitte:")
    for s in song.sections:
        extra = f" ({s.raw_label})" if s.raw_label and s.raw_label != s.label else ""
        log.info("  %s-%s  %-7s Energie %s %.2f%s", fmt_time(s.start), fmt_time(s.end), s.label,
                 _bar(s.energy), s.energy, extra)
    if song.drops:
        log.info("Drops: %s", ", ".join(f"{fmt_time(d.time)} (Sprung +{d.strength:.2f})" for d in song.drops))
    else:
        log.info("Drops: keine gefunden. Das Edit wird dann am ersten Refrain ausgerichtet.")


def short_mood(mood: dict[str, float] | None, count: int = 2) -> str:
    if not mood:
        return "-"
    return ", ".join(f"{m} {mood[m]:.2f}" for m in sorted(MOODS, key=lambda m: -mood.get(m, 0.0))[:count])


def log_song_mood(row: Song) -> None:
    """Tonart, Stimmung und woraus sie kommt (für 'song' und 'music --list')."""
    if row.mood is None:
        return
    f = row.features or {}
    log.info("Tonart: %s | Stimmung: %s", row.musical_key or "-",
             ", ".join(f"{m} {row.mood[m]:.2f}" for m in sorted(MOODS, key=lambda m: -row.mood[m])))  # type: ignore[index]
    if "arousal" in f:
        log.info("Messwerte: %.1f Anschläge/s, Schlagzeug %.0f %%, Helligkeit %.0f Hz, Lautstärke %.1f dBFS, Dur %.2f "
                 "-> Arousal %.2f, Valenz %.2f (%s)", f.get("onsets", 0), 100 * f.get("percussive", 0),
                 f.get("brightness", 0), f.get("loudness", 0), f.get("major", 0.5), f["arousal"], f["valence"],
                 describe(f["arousal"], f["valence"]))
    if f.get("clap_top"):
        log.info('CLAP: am ähnlichsten "%s" (%s)', f["clap_top"], short_mood(f.get("signals", {}).get("clap"), 3))
    else:
        log.info("CLAP: nicht dabei, Stimmung nur aus den Messwerten")


def run_song(args: argparse.Namespace, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    row = analyze_one(engine, args.path, settings, ModelLoader(settings), args.analyzer, args.force)
    song = SongAnalysis.from_dict(row.analysis)
    log_song(song)
    log_song_mood(row)
    if args.length <= song.duration:
        start = choose_edit_start(song, args.length, None, settings.cuts.drop_position)
        log.info("Ein %.0f-s-Edit würde bei %s starten (änderbar mit --song-start).", args.length, fmt_time(start))
