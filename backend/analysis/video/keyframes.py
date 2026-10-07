"""Standbilder aus jedem Clip holen, mit einem einzigen ffmpeg-Durchlauf pro Folge.

ffmpeg tastet die Folge mit wenigen Bildern pro Sekunde in kleiner Auflösung ab. Von jedem Clip
behalten wir 1-3 Bilder (je nach Länge), mit etwas Abstand zu den Schnitten.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import cv2
import numpy as np

from backend.config.settings import KeyframeSettings
from backend.media import probe_video_size

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Keyframe:
    clip_index: int  # Position in der übergebenen Clip-Liste
    time: float  # Sekunden in der Folge
    thumbnail: bool  # mittleres Bild des Clips, wird als Vorschaubild gespeichert
    image: np.ndarray  # RGB, uint8, Höhe x Breite x 3


def keyframe_times(start: float, end: float, cfg: KeyframeSettings) -> list[float]:
    """Zeitpunkte der Standbilder eines Clips, gleichmäßig verteilt, nicht direkt am Schnitt."""
    inner_start, inner_end = start + cfg.edge_seconds, end - cfg.edge_seconds
    if inner_end - inner_start < 1.0 / cfg.fps:
        return [(start + end) / 2]
    count = min(cfg.max_per_clip, 1 + int((end - start) // cfg.seconds_per_frame))
    step = (inner_end - inner_start) / count
    return [inner_start + (i + 0.5) * step for i in range(count)]


def plan_frames(clips: Sequence[tuple[float, float]], cfg: KeyframeSettings) -> dict[int, list[tuple[int, bool]]]:
    """Bildnummer (bei cfg.fps) -> [(Clip-Index, Vorschaubild?)]. Doppelte Bilder pro Clip fallen weg."""
    plan: dict[int, list[tuple[int, bool]]] = {}
    for idx, (start, end) in enumerate(clips):
        frames: list[int] = []
        for t in keyframe_times(start, end, cfg):
            frame = int(round(t * cfg.fps))
            if frame not in frames:
                frames.append(frame)
        middle = frames[len(frames) // 2]
        for frame in frames:
            plan.setdefault(frame, []).append((idx, frame == middle))
    return plan


def output_size(video: Path, height: int) -> tuple[int, int]:
    """Bildgröße für die Standbilder: feste Höhe, Breite passend zum Seitenverhältnis (gerade Zahl)."""
    width, src_height = probe_video_size(video)
    return max(2, int(round(width * height / src_height / 2)) * 2), height


def iter_keyframes(
    video: Path,
    clips: Sequence[tuple[float, float]],
    cfg: KeyframeSettings,
    hwaccel: str = "",
) -> Iterator[Keyframe]:
    """Liefert die geplanten Standbilder in zeitlicher Reihenfolge. Nach dem letzten wird ffmpeg beendet."""
    plan = plan_frames(clips, cfg)
    if not plan:
        return
    last = max(plan)
    width, height = output_size(video, cfg.height)
    frame_size = width * height * 3
    cmd = ["ffmpeg", "-v", "error"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += [
        "-i", str(video), "-an", "-sn", "-dn",
        "-vf", f"fps={cfg.fps},scale={width}:{height}:flags=area,format=rgb24",
        "-f", "rawvideo", "-",
    ]
    # Fehlermeldungen in eine Datei statt in eine Pipe: eine volle Pipe würde ffmpeg unter Windows blockieren
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        assert proc.stdout is not None
        index = -1
        eof = False
        try:
            while index < last:
                raw = proc.stdout.read(frame_size)
                if len(raw) < frame_size:
                    eof = True
                    break
                index += 1
                for clip_index, thumb in plan.get(index, []):
                    image = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                    yield Keyframe(clip_index, index / cfg.fps, thumb, image)
        finally:
            # Alle Bilder da (oder Abbruch): ffmpeg muss den Rest der Folge nicht mehr dekodieren
            if not eof and proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            code = proc.wait()
        if eof and code != 0:
            errors.seek(0)
            message = errors.read().decode(errors="replace")[-1000:]
            raise RuntimeError(f"ffmpeg konnte {video.name} nicht lesen:\n{message}")
    missing = sum(1 for frame in plan if frame > index)
    if missing:
        log.debug("%s: %d Standbilder liegen hinter dem letzten Bild der Datei", video.name, missing)


def save_jpeg(image: np.ndarray, path: Path, quality: int) -> None:
    """Speichert ein RGB-Bild als JPG. Über imencode, weil cv2.imwrite unter Windows an Umlauten scheitert."""
    ok, data = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"Konnte {path.name} nicht als JPG kodieren")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.tobytes())


def load_image(path: Path) -> np.ndarray | None:
    """Liest ein JPG/PNG/WebP als RGB (None, wenn es fehlt oder kaputt ist). Ohne Pfad-Probleme unter Windows."""
    if not path.is_file():
        return None
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    return None if image is None else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def grab_frame(video: Path, time: float, height: int) -> np.ndarray | None:
    """Ein einzelnes Bild an Sekunde time, height Pixel hoch (RGB). None, wenn ffmpeg dort nichts findet."""
    width, height = output_size(video, height)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, time):.3f}", "-i", str(video), "-frames:v", "1", "-an", "-sn",
         "-vf", f"scale={width}:{height}:flags=area,format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True,
    )
    if result.returncode != 0 or len(result.stdout) < width * height * 3:
        return None
    return np.frombuffer(result.stdout[: width * height * 3], dtype=np.uint8).reshape(height, width, 3).copy()
