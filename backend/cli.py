"""Kommandozeile für AMV-Forge.

Beispiel:
    python -m backend.cli quick --video folge.mkv --song song.mp3 --length 30
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import asdict
from pathlib import Path

from backend.analysis.music.beats import analyze_beats
from backend.analysis.video.scenes import detect_scenes
from backend.config import load_settings
from backend.planner.assign import assign_random, usable_scenes
from backend.planner.slots import build_slots, choose_song_start
from backend.render.ffmpeg_graph import RenderOptions, probe_duration, render_edit, require_ffmpeg

log = logging.getLogger("amv_forge")

DATA_DIR = Path("data")


def _existing_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Datei nicht gefunden: {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amv-forge", description="Beat-synchrone Anime-Edits.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Ausgaben anzeigen")
    sub = parser.add_subparsers(dest="command", required=True)

    quick = sub.add_parser("quick", help="Phase 1: 1 Folge + 1 Song, zufällige Clips im Takt")
    quick.add_argument("--video", type=_existing_file, required=True, help="Folge (MKV/MP4)")
    quick.add_argument("--song", type=_existing_file, required=True, help="Song (MP3/WAV/FLAC)")
    quick.add_argument("--length", type=float, default=30.0, help="Länge des Edits in Sekunden")
    quick.add_argument("--out", type=Path, default=DATA_DIR / "renders" / "quick.mp4", help="Ausgabedatei")
    quick.add_argument("--beats-per-cut", type=int, default=None, help="Alle n Beats schneiden (Standard aus YAML)")
    quick.add_argument("--song-start", type=float, default=None, help="Start im Song in Sekunden (wird auf Beat gerundet)")
    quick.add_argument("--seed", type=int, default=None, help="Zufalls-Seed für reproduzierbare Edits")
    quick.add_argument("--preview", action="store_true", help="Schnelle 480p-Vorschau statt 1080x1920")
    quick.add_argument("--no-music", action="store_true", help="Ohne eingebrannte Musik exportieren (für TikTok-Sounds)")
    quick.add_argument("--config", type=Path, default=None, help="Eigene YAML statt backend/config/default.yaml")
    return parser


def run_quick(args: argparse.Namespace) -> Path:
    settings = load_settings(args.config)
    require_ffmpeg()

    beat_info = analyze_beats(args.song)
    song_start = choose_song_start(beat_info.beats, beat_info.duration, args.length, args.song_start)
    beats_per_cut = args.beats_per_cut or settings.quick.beats_per_cut
    slots = build_slots(
        beat_info.beats, song_start, args.length, beats_per_cut, settings.quick.min_slot_seconds
    )
    log.info("Song-Start bei %.2f s, %d Schnitte (alle %d Beats)", song_start, len(slots), beats_per_cut)

    video_duration = probe_duration(args.video)
    scenes = detect_scenes(
        args.video,
        DATA_DIR / "cache" / "scenes",
        settings.scenes.adaptive_threshold,
        settings.scenes.min_scene_len_frames,
    )
    scenes = usable_scenes(
        scenes, video_duration, settings.quick.skip_start_seconds, settings.quick.skip_end_seconds
    )

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    log.info("Seed: %d (mit --seed %d bekommst du genau dieses Edit nochmal)", seed, seed)
    assignments = assign_random(slots, scenes, random.Random(seed))

    r = settings.render
    width, height = (r.preview_width, r.preview_height) if args.preview else (r.width, r.height)
    opts = RenderOptions(
        width=width,
        height=height,
        fps=r.fps,
        crf=r.crf,
        preset=r.preset,
        audio_bitrate=r.audio_bitrate,
        with_music=not args.no_music,
    )
    out = render_edit(assignments, args.video, args.song, song_start, args.out, opts)

    plan_file = out.with_suffix(".plan.json")
    plan_file.write_text(
        json.dumps(
            {
                "video": str(args.video),
                "song": str(args.song),
                "tempo": beat_info.tempo,
                "song_start": song_start,
                "seed": seed,
                "clips": [
                    {**asdict(a.slot), "source_start": a.source_start} for a in assignments
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    log.info("Schnittliste: %s", plan_file)
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        if args.command == "quick":
            run_quick(args)
    except (RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
