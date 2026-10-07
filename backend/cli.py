"""Kommandozeile für AMV-Forge.

Beispiele:
    python -m backend.cli quick --video folge.mkv --song song.mp3 --length 30
    python -m backend.cli index --source folder --path "P:\\Anime\\Horimiya\\S1"
    python -m backend.cli status --season 1
    python -m backend.cli song "C:\\Musik\\song.mp3"
    python -m backend.cli edit --season 1 --song "C:\\Musik\\song.mp3" --length 30
    python -m backend.cli moods --season 1
    python -m backend.cli edit --season 1 --song "C:\\Musik\\song.mp3" --style romance
    python -m backend.cli characters --season 1 --show "Hori,Miyamura"
    python -m backend.cli edit --season 1 --song "C:\\Musik\\song.mp3" --characters "Hori,Miyamura"
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.commands.characters import add_character_commands
from backend.commands.edit import add_edit_commands
from backend.commands.moods import add_mood_commands
from backend.commands.season import add_season_commands
from backend.commands.song import add_song_commands
from backend.config import load_settings

log = logging.getLogger("amv_forge")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amv-forge", description="Beat-synchrone Anime-Edits.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Ausgaben anzeigen")
    sub = parser.add_subparsers(dest="command", required=True)
    add_edit_commands(sub)
    add_season_commands(sub)
    add_song_commands(sub)
    add_mood_commands(sub)
    add_character_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if not args.verbose:
        # httpx loggt sonst jede einzelne Anfrage, numba/matplotlib/huggingface reden auch gern
        for noisy in ("httpx", "numba", "matplotlib", "huggingface_hub", "sentence_transformers", "timm",
                      "onnxruntime"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        # open_clip loggt direkt über den Root-Logger (jeder Ladeschritt), wir selbst nie
        for handler in logging.getLogger().handlers:
            handler.addFilter(lambda record: record.name != "root" or record.levelno >= logging.WARNING)
    try:
        settings = load_settings(getattr(args, "config", None))
        args.handler(args, settings)
    except (RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
