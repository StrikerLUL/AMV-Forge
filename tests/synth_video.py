"""Testvideos aus farbigen Szenen, jede Szene ist eine Einstellung (harter Schnitt dazwischen).

Mit moving=True fliegt in jeder Szene ein weißes Quadrat durchs Bild (viel Bewegung).
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
