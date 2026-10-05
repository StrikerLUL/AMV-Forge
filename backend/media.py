"""Kleine ffmpeg/ffprobe-Helfer, die mehrere Module brauchen."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np


def require_ffmpeg() -> None:
    """Bricht mit klarer Meldung ab, wenn ffmpeg/ffprobe nicht im PATH sind."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(f"Nicht im PATH gefunden: {', '.join(missing)}. Bitte ffmpeg installieren.")


def probe_duration(path: Path) -> float:
    """Länge einer Video- oder Audiodatei in Sekunden."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def read_audio(path: Path, start: float, duration: float, sr: int = 22050) -> np.ndarray:
    """Liest einen Audio-Ausschnitt als Mono-float32 direkt aus ffmpeg (ohne Zwischendatei)."""
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}", "-i", str(path),
            "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def probe_video_size(path: Path) -> tuple[int, int]:
    """Breite und Höhe, wie das Bild angezeigt wird (berücksichtigt nicht-quadratische Pixel)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,sample_aspect_ratio", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    streams = json.loads(out).get("streams") or []
    if not streams:
        raise RuntimeError(f"{path.name} hat keine Videospur.")
    width, height = int(streams[0]["width"]), int(streams[0]["height"])
    sar = str(streams[0].get("sample_aspect_ratio") or "1:1")
    try:
        num, den = (int(x) for x in sar.split(":"))
    except ValueError:
        num, den = 1, 1
    if num > 0 and den > 0 and num != den:
        width = round(width * num / den)
    return width, height
