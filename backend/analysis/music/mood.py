"""Stimmung eines Songs (Phase 7): dieselben fünf Stimmungen wie bei den Clips (romance, action, sad, funny, calm).

Zwei Signale, wie in Phase 4 gemischt mit Gewichten aus default.yaml (music_mood.weights):

1. Messwerte ohne KI. Psychologen beschreiben Stimmungen gern in zwei Richtungen (Russell):
   Arousal = wie aufgeregt (ruhig ... energiegeladen) und Valenz = wie angenehm (traurig ... fröhlich).
   Arousal kommt aus Tempo, Anschlägen pro Sekunde, Anteil Schlagzeug, Helligkeit und Lautstärke,
   Valenz vor allem aus Dur/Moll. Jede Stimmung hat einen Platz in diesem Feld (music_mood.prototypes),
   je näher der Song daran liegt, desto stärker die Stimmung.
2. CLAP (clap.py), falls installiert: hört sich den Song an und vergleicht ihn mit Sätzen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import librosa
import numpy as np

from backend.analysis.mood import MOODS
from backend.analysis.music.key import estimate_key
from backend.config.settings import MusicMoodSettings

MUSIC_MOOD_VERSION = 1  # erhöhen, wenn sich die Berechnung ändert


@dataclass(frozen=True)
class SongFeatures:
    """Messwerte eines ganzen Songs."""

    tempo: float  # BPM aus der Song-Analyse
    onsets: float  # Anschläge (Töne, Schläge) pro Sekunde
    percussive: float  # Anteil von Schlagzeug/Perkussion an der Energie, 0-1
    brightness: float  # Schwerpunkt des Spektrums in Hz (hoch = hell: Becken, Synths)
    loudness: float  # Lautstärke der lauten Hälfte in dBFS
    major: float  # 0 = klar Moll, 1 = klar Dur
    key: str  # Tonart, z. B. "a-Moll"
    key_strength: float  # wie eindeutig die Tonart ist (Korrelation, über 0,6 = klar)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure(y: np.ndarray, sr: int, tempo: float) -> SongFeatures:
    """Alle Messwerte aus einem Mono-Signal. Ein Spektrogramm reicht für fast alles."""
    duration = max(1e-6, len(y) / sr)
    spec = np.abs(librosa.stft(y, n_fft=4096, hop_length=1024))
    power = spec ** 2

    # Harmonisch (Töne, die stehen bleiben) gegen perkussiv (kurze Schläge): HPSS trennt das Spektrogramm
    # in waagerechte Linien (Töne) und senkrechte (Schläge)
    harmonic, percussive = librosa.decompose.hpss(spec, kernel_size=17)
    perc_share = float((percussive ** 2).sum() / max(1e-12, (harmonic ** 2).sum() + (percussive ** 2).sum()))

    frame_db = 20.0 * np.log10(np.maximum(librosa.feature.rms(S=spec, frame_length=4096)[0], 1e-6))
    loud = frame_db >= np.median(frame_db)  # nur die lautere Hälfte (Stille und Intros zählen nicht)
    centroid = librosa.feature.spectral_centroid(S=spec, sr=sr)[0]

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)

    key = estimate_key(librosa.feature.chroma_stft(S=power, sr=sr, n_fft=4096).mean(axis=1))
    return SongFeatures(
        tempo=round(float(tempo), 2),
        onsets=round(len(onsets) / duration, 3),
        percussive=round(perc_share, 3),
        brightness=round(float(np.median(centroid[loud])) if loud.any() else 0.0, 1),
        loudness=round(float(np.mean(frame_db[loud])) if loud.any() else -90.0, 2),
        major=key.majorness,
        key=key.name,
        key_strength=key.strength,
    )


def _scaled(value: float, low: float, high: float) -> float:
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _weighted(features: SongFeatures, weights: dict[str, float], cfg: MusicMoodSettings) -> float:
    total = weight_sum = 0.0
    for name, weight in weights.items():
        if weight <= 0:
            continue
        if name == "major":
            value = features.major
        else:
            low, high = cfg.ranges[name]
            value = _scaled(float(getattr(features, name)), low, high)
        total += weight * value
        weight_sum += weight
    return total / weight_sum if weight_sum > 0 else 0.5


def arousal_valence(features: SongFeatures, cfg: MusicMoodSettings) -> tuple[float, float]:
    """(Arousal, Valenz), beide 0-1."""
    return round(_weighted(features, cfg.arousal, cfg), 3), round(_weighted(features, cfg.valence, cfg), 3)


def feature_mood(arousal: float, valence: float, cfg: MusicMoodSettings) -> dict[str, float]:
    """Stimmung aus der Lage im Feld Arousal x Valenz: 1 = genau am Platz der Stimmung, Richtung 0 = weit weg."""
    mood: dict[str, float] = {}
    for m in MOODS:
        a, v = cfg.prototypes.get(m, (0.5, 0.5))
        distance2 = (arousal - a) ** 2 + (valence - v) ** 2
        mood[m] = round(float(np.exp(-distance2 / (2 * cfg.spread ** 2))), 3)
    return mood


def combine(signals: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, float]:
    """Gewichteter Mittelwert pro Stimmung über die vorhandenen Signale (features, clap)."""
    unknown = sorted(set(weights) - {"features", "clap"})
    if unknown:
        raise ValueError(f"music_mood.weights: unbekanntes Signal {', '.join(unknown)} (erlaubt: features, clap)")
    mood: dict[str, float] = {}
    for m in MOODS:
        total = weight_sum = 0.0
        for signal, values in signals.items():
            w = float(weights.get(signal, 0.0))
            if w > 0 and m in values:
                total += w * values[m]
                weight_sum += w
        mood[m] = round(total / weight_sum, 3) if weight_sum > 0 else 0.5
    return mood


def describe(arousal: float, valence: float) -> str:
    """Kurze Beschreibung in Worten, z. B. "ruhig, eher fröhlich"."""
    energy = "ruhig" if arousal < 0.35 else ("energiegeladen" if arousal > 0.65 else "mittleres Tempo")
    mood = "eher traurig/düster" if valence < 0.4 else ("eher fröhlich" if valence > 0.6 else "neutral")
    return f"{energy}, {mood}"
