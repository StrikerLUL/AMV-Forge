"""CLI-Befehl für Phase 3: Song analysieren und Struktur anzeigen."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from backend.analysis.music.structure import SongAnalysis
from backend.commands.common import existing_file, fmt_time
from backend.config.settings import Settings
from backend.db import get_engine
from backend.planner.song_slots import choose_edit_start
from backend.songs import load_song

log = logging.getLogger("amv_forge")


def add_song_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    song = sub.add_parser("song", help="Phase 3: Song analysieren (Tempo, Abschnitte, Drops)")
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


def run_song(args: argparse.Namespace, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    song = load_song(engine, args.path, settings.music, args.analyzer, args.force)
    log_song(song)
    if args.length <= song.duration:
        start = choose_edit_start(song, args.length, None, settings.cuts.drop_position)
        log.info("Ein %.0f-s-Edit würde bei %s starten (änderbar mit --song-start).", args.length, fmt_time(start))
