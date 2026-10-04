"""Kleine Helfer, die mehrere CLI-Befehle brauchen."""

from __future__ import annotations

import argparse
from pathlib import Path


def existing_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Datei nicht gefunden: {path}")
    return path


def fmt_time(seconds: float) -> str:
    """125.4 -> '02:05.4'"""
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"
