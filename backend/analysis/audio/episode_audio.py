"""Ton der Folge: Wie laut ist ein Clip, und wird darin geredet?

Lautstärke ist der RMS-Pegel in dBFS (0 = maximal laut, -60 = sehr leise). Ob geredet wird, sagt
Silero VAD (Voice Activity Detection): ein kleines neuronales Netz, das in 32-ms-Stücken entscheidet,
ob eine Stimme zu hören ist. Musik und Geräusche zählen nicht als Sprache.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

log = logging.getLogger(__name__)

Segment = tuple[float, float]


class SpeechDetector(Protocol):
    name: str

    def __call__(self, audio: np.ndarray, sample_rate: int) -> list[Segment]: ...


@dataclass(frozen=True)
class ClipAudio:
    loudness: float  # dBFS
    speech: float | None  # Anteil Sprache 0-1, None = keine Spracherkennung


def silero_installed() -> bool:
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("silero_vad") is not None


def rms_db(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
    return round(max(-100.0, 20 * np.log10(rms + 1e-10)), 2)


def overlap_share(start: float, end: float, segments: Sequence[Segment]) -> float:
    """Anteil von [start, end], der in den (sortierten) Segmenten liegt."""
    length = end - start
    if length <= 0:
        return 0.0
    covered = sum(max(0.0, min(end, b) - max(start, a)) for a, b in segments if b > start and a < end)
    return round(min(1.0, covered / length), 3)


def analyze_clips(
    audio: np.ndarray,
    sample_rate: int,
    clips: Sequence[Segment],
    speech: Sequence[Segment] | None,
) -> list[ClipAudio]:
    result = []
    for start, end in clips:
        a, b = int(start * sample_rate), int(end * sample_rate)
        share = overlap_share(start, end, speech) if speech is not None else None
        result.append(ClipAudio(loudness=rms_db(audio[a:b]), speech=share))
    return result


class SileroVad:
    """Silero VAD aus dem pip-Paket silero-vad (das Modell steckt im Paket, kein Download)."""

    def __init__(self, threshold: float) -> None:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad

        self._torch = torch
        self._detect = get_speech_timestamps
        self.model = load_silero_vad()
        self.threshold = threshold
        self.name = f"silero/{threshold}"

    def __call__(self, audio: np.ndarray, sample_rate: int) -> list[Segment]:
        if sample_rate not in (8000, 16000):
            raise ValueError("Silero VAD braucht 8000 oder 16000 Hz (episode_audio.sample_rate)")
        with self._torch.no_grad():
            found = self._detect(self._torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)), self.model,
                                 threshold=self.threshold, sampling_rate=sample_rate, return_seconds=True)
        return [(float(s["start"]), float(s["end"])) for s in found]
