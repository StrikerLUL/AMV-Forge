"""Testvideos aus farbigen Szenen, jede Szene ist eine Einstellung (harter Schnitt dazwischen).

Mit moving=True fliegt in jeder Szene ein weißes Quadrat durchs Bild (viel Bewegung).
make_face_video malt farbige Quadrate als "Gesichter" (für FakeFaceDetector in fake_models.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


def make_color_video(path: Path, colors: Sequence[str], moving: bool, seconds: float = 3.0,
                     size: str = "160x90", tone: float = 440.0) -> Path:
    inputs: list[str] = []
    filters: list[str] = []
    k = 0
    for i, color in enumerate(colors):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}"]
        if moving:
            inputs += ["-f", "lavfi", "-i", f"color=c=white:s=30x30:r=24:d={seconds}"]
            filters.append(f"[{k}:v][{k + 1}:v]overlay=x='mod(t*300,130)':y='mod(t*170,60)'[v{i}]")
            k += 2
        else:
            filters.append(f"[{k}:v]null[v{i}]")
            k += 1
    filters.append("".join(f"[v{i}]" for i in range(len(colors))) + f"concat=n={len(colors)}:v=1:a=0[out]")
    total = seconds * len(colors)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs,
         "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={total}",
         "-filter_complex", ";".join(filters), "-map", "[out]", "-map", f"{k}:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


REDS = ["0x500000", "0xd02020", "0x802040", "0xf06060", "0x600010", "0xc04040", "0x900000", "0xe08080"]
BLUES = ["0x000050", "0x2020d0", "0x204080", "0x6060f0", "0x100060", "0x4040c0", "0x000090", "0x8080e0"]


# "Gesichter" für FakeFaceDetector: Hori ist rot, Miyamura blau, eine Figur ohne AniList-Eintrag grün
FACE_COLORS = {"hori": "0xe02020", "miyamura": "0x2020e0", "fremd": "0x20c020"}
BACKGROUNDS = ["0xf0f0f0", "0x909090", "0xe0e0e0", "0x808080"]


def make_face_video(path: Path, scenes: Sequence[Sequence[str]], seconds: float = 3.0) -> Path:
    """Pro Szene ein grauer Hintergrund (jede Szene anders hell) mit bis zu zwei Quadraten (links, rechts)."""
    inputs: list[str] = []
    filters: list[str] = []
    for i, people in enumerate(scenes):
        inputs += ["-f", "lavfi", "-i", f"color=c={BACKGROUNDS[i % len(BACKGROUNDS)]}:s=320x180:r=24:d={seconds}"]
        boxes = [f"drawbox=x={40 + 160 * k}:y=45:w=80:h=80:color={FACE_COLORS[who]}:t=fill"
                 for k, who in enumerate(people)]
        filters.append(f"[{i}:v]{','.join(boxes) or 'null'}[v{i}]")
    filters.append("".join(f"[v{i}]" for i in range(len(scenes))) + f"concat=n={len(scenes)}:v=1:a=0[out]")
    total = seconds * len(scenes)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs, "-f", "lavfi", "-i", f"sine=frequency=330:duration={total}",
         "-filter_complex", ";".join(filters), "-map", "[out]", "-map", f"{len(scenes)}:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


def portrait_png(color: str) -> bytes:
    """Figurenbild wie auf AniList: Hochformat, weißer Hintergrund, Gesicht oben."""
    import cv2
    import numpy as np

    rgb = tuple(int(color[i:i + 2], 16) for i in (2, 4, 6))
    image = np.full((300, 200, 3), 255, dtype=np.uint8)
    image[30:110, 60:140] = rgb
    ok, data = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    assert ok
    return data.tobytes()
