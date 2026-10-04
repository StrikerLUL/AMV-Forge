"""Bewegung in einer Folge messen (Optical Flow) und den stärksten Moment jedes Clips finden.

Optical Flow vergleicht zwei aufeinanderfolgende Bilder und schätzt für jeden Pixel, wohin er sich
bewegt hat. Der Durchschnitt dieser Verschiebungen ist unser Bewegungswert: Faustschlag oder
schneller Schwenk = hoch, zwei Figuren, die sich anschauen = niedrig. Wir rechnen auf einer
160x90-Mini-Version mit 12 Bildern pro Sekunde, das reicht und geht schnell.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from backend.config.settings import MotionSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MotionCurve:
    """values[i] = Bewegung zwischen Bild i-1 und Bild i, Bild i liegt bei i / fps Sekunden."""

    fps: float
    values: np.ndarray

    def time(self, index: int) -> float:
        return index / self.fps


@dataclass(frozen=True)
class ClipMotion:
    motion: float  # durchschnittliche Bewegung (Pixel pro Bild in der 160-px-Version)
    peak: float  # Zeitpunkt der stärksten Bewegung in der Folge (Sekunden)


def _cache_file(video: Path, cfg: MotionSettings) -> Path:
    stat = video.stat()
    raw = f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{cfg.fps}|{cfg.width}x{cfg.height}"
    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return cfg.cache_dir / f"{video.stem}_{key}.npy"


def flow_magnitude(prev: np.ndarray, frame: np.ndarray) -> float:
    """Durchschnittliche Pixel-Verschiebung zwischen zwei Graustufenbildern (Farneback-Verfahren)."""
    flow = cv2.calcOpticalFlowFarneback(prev, frame, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    return float(np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2).mean())


def _ffmpeg_cmd(video: Path, cfg: MotionSettings) -> list[str]:
    cmd = ["ffmpeg", "-v", "error"]
    if cfg.hwaccel:
        cmd += ["-hwaccel", cfg.hwaccel]
    return cmd + [
        "-i", str(video), "-an", "-sn", "-dn",
        "-vf", f"fps={cfg.fps},scale={cfg.width}:{cfg.height}:flags=area,format=gray",
        "-f", "rawvideo", "-",
    ]


def measure_motion(
    video: Path, cfg: MotionSettings, duration: float | None = None, use_cache: bool = True
) -> MotionCurve:
    """Bewegungskurve der ganzen Folge. Wird als .npy gecacht, ein zweiter Aufruf liest nur die Datei."""
    cache = _cache_file(video, cfg)
    if use_cache and cache.exists():
        return MotionCurve(cfg.fps, np.load(cache))

    log.info("Messe Bewegung in %s ...", video.name)
    started = time.monotonic()
    frame_size = cfg.width * cfg.height
    values: list[float] = [0.0]
    prev: np.ndarray | None = None
    expected = int(duration * cfg.fps) if duration else 0
    next_log = 0.25
    # Fehlermeldungen in eine Datei statt in eine Pipe: eine volle Pipe würde ffmpeg unter Windows blockieren
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(_ffmpeg_cmd(video, cfg), stdout=subprocess.PIPE, stderr=errors)
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(cfg.height, cfg.width)
            if prev is not None:
                values.append(flow_magnitude(prev, frame))
            prev = frame
            if expected and len(values) / expected >= next_log:
                log.info("  %s: %d %%", video.name, int(next_log * 100))
                next_log += 0.25
        proc.stdout.close()
        if proc.wait() != 0:
            errors.seek(0)
            message = errors.read().decode(errors="replace")[-1000:]
            raise RuntimeError(f"ffmpeg konnte {video.name} nicht lesen:\n{message}")

    curve = np.asarray(values, dtype=np.float32)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, curve)
    log.info("Bewegung gemessen: %d Bilder in %.0f s", len(curve), time.monotonic() - started)
    return MotionCurve(cfg.fps, curve)


def clip_motion(curve: MotionCurve, start: float, end: float, edge_seconds: float, smooth: int) -> ClipMotion | None:
    """Durchschnitt und Peak der Bewegung zwischen start und end.

    Die ersten/letzten edge_seconds werden ignoriert, weil der Schnitt selbst wie ein riesiger
    Sprung aussieht. Ist der Clip dafür zu kurz, zählt der ganze Clip.
    """
    values = curve.values
    first = int(np.ceil(start * curve.fps))
    last = min(len(values), int(np.floor(end * curve.fps)) + 1)
    if last - first < 1:
        return None
    inner_first = int(np.ceil((start + edge_seconds) * curve.fps))
    inner_last = min(last, int(np.floor((end - edge_seconds) * curve.fps)) + 1)
    if inner_last - inner_first >= 2:
        first, last = inner_first, inner_last

    window = values[first:last].astype(float)
    if smooth > 1 and len(window) >= smooth:
        smoothed = np.convolve(window, np.ones(smooth) / smooth, mode="same")
    else:
        smoothed = window
    peak_index = first + int(np.argmax(smoothed))
    return ClipMotion(motion=round(float(window.mean()), 4), peak=round(curve.time(peak_index), 3))
