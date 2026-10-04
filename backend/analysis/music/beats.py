"""Beats und Taktanfänge (Downbeats) mit librosa.

Downbeat = die "Eins" im Takt. librosa findet nur Beats, deshalb raten wir die Eins:
Akkordwechsel und Bass-Schläge passieren fast immer auf der Eins. Wir probieren alle
vier möglichen Lagen aus und nehmen die, bei der diese Signale am stärksten sind.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import librosa
import numpy as np

log = logging.getLogger(__name__)

HOP = 512


@dataclass(frozen=True)
class BeatGrid:
    """Alle Zeiten in Sekunden ab Songanfang."""

    bpm: float
    beats: list[float]
    downbeats: list[float]


def _zscore(x: np.ndarray) -> np.ndarray:
    std = float(np.std(x))
    return (x - float(np.mean(x))) / std if std > 1e-9 else np.zeros_like(x)


def bpm_from_beats(beats: list[float]) -> float:
    """Tempo aus dem mittleren Beat-Abstand (Ausgleichsgerade, genauer als das Frame-Raster)."""
    if len(beats) < 2:
        return 0.0
    slope = float(np.polyfit(np.arange(len(beats)), np.asarray(beats), 1)[0])
    return 60.0 / slope if slope > 0 else 0.0


def pick_downbeat_phase(chroma_change: np.ndarray, bass_onset: np.ndarray, beats_per_bar: int) -> int:
    """Welcher der ersten beats_per_bar Beats ist eine Eins? Werte sind pro Beat gegeben."""
    score = _zscore(chroma_change) + _zscore(bass_onset)
    best, best_value = 0, -np.inf
    for phase in range(min(beats_per_bar, len(score))):
        value = float(np.mean(score[phase::beats_per_bar]))
        if value > best_value:
            best, best_value = phase, value
    return best


def track_beats(y: np.ndarray, sr: int, beats_per_bar: int = 4) -> BeatGrid:
    """Beats mit librosa, Downbeats per Akkordwechsel + Bass auf der Eins."""
    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=HOP, n_mels=64, fmax=8000)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    onset = librosa.onset.onset_strength(S=mel_db, sr=sr, hop_length=HOP)
    # trim=False: auch leise Intro-/Outro-Beats behalten, das Edit darf überall im Song starten
    _, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP, trim=False)
    beat_frames = np.asarray(beat_frames, dtype=int)
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP)
    if len(beats) < beats_per_bar * 2:
        return BeatGrid(bpm_from_beats(list(beats)), [float(b) for b in beats], [float(b) for b in beats[:1]])

    # Bass-Onsets: nur die tiefsten Mel-Bänder (bis ~150 Hz)
    mel_freqs = librosa.mel_frequencies(n_mels=64, fmax=8000)
    low = mel_freqs <= 150
    bass_onset = librosa.onset.onset_strength(S=mel_db[low], sr=sr, hop_length=HOP)

    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=HOP)
    # Akkord pro Beat (Median bis zum nächsten Beat), Wechsel = Abstand zum vorherigen Beat
    step = int(np.median(np.diff(beat_frames)))
    bounds = [*beat_frames, beat_frames[-1] + step]
    beat_chroma = np.stack(
        [np.median(chroma[:, a: max(a + 1, b)], axis=1) for a, b in zip(bounds, bounds[1:])], axis=1
    )
    beat_chroma = beat_chroma / (np.linalg.norm(beat_chroma, axis=0, keepdims=True) + 1e-9)
    change = np.zeros(len(beat_frames))
    change[1:] = 1.0 - np.sum(beat_chroma[:, 1:] * beat_chroma[:, :-1], axis=0)

    # Bass-Schlag kurz um den Beat herum
    width = 2
    bass_at = np.array([bass_onset[max(0, f - width): f + width + 1].max() for f in beat_frames])

    phase = pick_downbeat_phase(change, bass_at, beats_per_bar)
    beat_list = [float(b) for b in beats]
    return BeatGrid(bpm_from_beats(beat_list), beat_list, beat_list[phase::beats_per_bar])
