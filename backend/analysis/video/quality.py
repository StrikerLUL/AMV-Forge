"""Bildqualität: Schwarzbilder, Weißblitze, einfarbige Flächen, Unschärfe, Text und Abspann aussortieren.

Helligkeit und Kontrast kommen direkt aus den Pixeln. Schärfe misst die Laplace-Varianz: Kanten
ergeben große Werte, verwischte Bilder kleine. Text, Abspann und Logos erkennt CLIP (Gruppen unter
"quality" in clip_prompts.yaml).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from backend.config.settings import QualitySettings


@dataclass(frozen=True)
class FrameStats:
    brightness: float  # mittlere Helligkeit 0-1
    contrast: float  # Standardabweichung der Helligkeit 0-1
    sharpness: float  # Varianz des Laplace-Filters (größer = schärfer)


@dataclass(frozen=True)
class ClipStats:
    """Zusammengefasst über alle Standbilder eines Clips."""

    brightness_min: float
    brightness_max: float
    contrast: float  # höchster Kontrast der Standbilder
    sharpness: float  # Median der Schärfe


@dataclass(frozen=True)
class Quality:
    score: float  # 0-1
    issue: str | None  # Grund, wenn der Clip aussortiert wird


def frame_stats(image: np.ndarray) -> FrameStats:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    laplace = cv2.Laplacian(gray, cv2.CV_64F)
    return FrameStats(
        brightness=float(gray.mean() / 255.0),
        contrast=float(gray.std() / 255.0),
        sharpness=float(laplace.var()),
    )


def combine_stats(frames: Sequence[FrameStats]) -> ClipStats | None:
    if not frames:
        return None
    return ClipStats(
        brightness_min=round(min(f.brightness for f in frames), 4),
        brightness_max=round(max(f.brightness for f in frames), 4),
        contrast=round(max(f.contrast for f in frames), 4),
        sharpness=round(float(np.median([f.sharpness for f in frames])), 2),
    )


def clip_quality(
    stats: ClipStats | None,
    typical_sharpness: float | None,
    quality_probs: dict[str, float],
    cfg: QualitySettings,
) -> Quality:
    """Qualität 0-1. Harte Fehler (schwarz, weiß, einfarbig, Text ...) ergeben 0 und einen Grund.

    typical_sharpness: Median der Schärfe in der Staffel. quality_probs: CLIP-Wahrscheinlichkeit pro
    Qualitätsgruppe (z. B. "text", "blurry").
    """
    if stats is not None:
        if stats.brightness_max < cfg.dark:
            return Quality(0.0, "schwarz")
        if stats.brightness_min > cfg.bright:
            return Quality(0.0, "weiß")
        if stats.contrast < cfg.flat:
            return Quality(0.0, "einfarbig")
    for group, prob in sorted(quality_probs.items(), key=lambda kv: -kv[1]):
        if prob >= cfg.prompt_threshold:
            return Quality(0.0, group)
    if stats is not None and typical_sharpness and stats.sharpness < cfg.blurry_ratio * typical_sharpness:
        return Quality(0.2, "unscharf")
    # Kein harter Fehler: leichter Abzug, je mehr CLIP schon in Richtung Text/Unschärfe tendiert
    penalty = sum(quality_probs.values()) / max(cfg.prompt_threshold, 1e-6)
    return Quality(round(max(0.5, 1.0 - 0.5 * min(1.0, penalty)), 3), None)
