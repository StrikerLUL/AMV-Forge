"""Beat-Erkennung mit librosa."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BeatInfo:
    """Ergebnis der Beat-Analyse, alle Zeiten in Sekunden ab Songanfang."""

    tempo: float
    beats: list[float]
    duration: float


def analyze_beats(song: Path) -> BeatInfo:
    """Lädt den Song in Mono und schätzt Tempo und Beat-Zeitpunkte."""
    log.info("Analysiere Beats in %s ...", song.name)
    y, sr = librosa.load(str(song), sr=22050, mono=True)
    tempo, beat_times = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo_value = float(np.atleast_1d(tempo)[0])
    beats = [float(t) for t in beat_times]
    duration = float(librosa.get_duration(y=y, sr=sr))
    log.info("Tempo: %.1f BPM, %d Beats, Länge %.1f s", tempo_value, len(beats), duration)
    return BeatInfo(tempo=tempo_value, beats=beats, duration=duration)
