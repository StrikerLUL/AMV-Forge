"""Tonart eines Songs mit dem Krumhansl-Schmuckler-Verfahren.

Chroma = wie viel Energie auf jedem der 12 Halbtöne liegt (C, Cis, D ... H), egal in welcher Oktave.
Über den ganzen Song gemittelt ergibt das ein Profil. Jede Tonart hat ein typisches Profil: In C-Dur
klingen C, E und G am häufigsten, Cis und Fis kaum. Wir vergleichen das Profil des Songs mit denen
aller 24 Tonarten (12 Dur, 12 Moll) und nehmen die ähnlichste.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

# Wie stark jeder Ton in einer Dur- bzw. Moll-Tonart gefühlt "dazugehört" (Krumhansl & Kessler, 1982),
# ab dem Grundton gezählt
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Deutsche Tonnamen: B ist das englische Bb, H das englische B
NAMES = ("C", "Cis", "D", "Es", "E", "F", "Fis", "G", "As", "A", "B", "H")
HOP = 2048


@dataclass(frozen=True)
class Key:
    tonic: int  # Grundton, 0 = C, 9 = A
    major: bool
    strength: float  # Korrelation mit dem besten Profil, -1 bis 1 (über 0,6 = klare Tonart)
    majorness: float  # 0 = klar Moll, 0.5 = unklar, 1 = klar Dur

    @property
    def name(self) -> str:
        """z. B. "A-Dur" oder "a-Moll" (Moll klein geschrieben, wie im Deutschen üblich)."""
        root = NAMES[self.tonic]
        return f"{root}-Dur" if self.major else f"{root.lower()}-Moll"


def _correlations(profile: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Korrelation des Song-Profils mit der Vorlage für jeden der 12 Grundtöne."""
    return np.array([np.corrcoef(profile, np.roll(template, tonic))[0, 1] for tonic in range(12)])


def estimate_key(chroma_profile: np.ndarray) -> Key:
    """Tonart aus einem 12-Werte-Profil (Energie pro Halbton, C zuerst)."""
    profile = np.asarray(chroma_profile, dtype=float)
    if profile.shape != (12,) or float(np.std(profile)) < 1e-9:
        return Key(0, True, 0.0, 0.5)
    major = np.nan_to_num(_correlations(profile, MAJOR_PROFILE))
    minor = np.nan_to_num(_correlations(profile, MINOR_PROFILE))
    best_major, best_minor = int(major.argmax()), int(minor.argmax())
    is_major = major[best_major] >= minor[best_minor]
    # Dur und die parallele Moll-Tonart (C-Dur/a-Moll) teilen sich alle Töne, deshalb ist der Abstand
    # meist klein: 0,1 Unterschied in der Korrelation zählt schon als klar
    majorness = float(np.clip(0.5 + 5.0 * (major[best_major] - minor[best_minor]), 0.0, 1.0))
    tonic = best_major if is_major else best_minor
    strength = float(major[best_major] if is_major else minor[best_minor])
    return Key(tonic, bool(is_major), round(strength, 3), round(majorness, 3))


def song_key(y: np.ndarray, sr: int) -> Key:
    """Tonart eines ganzen Songs (Mono-Audio)."""
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, n_fft=8192, hop_length=HOP)
    return estimate_key(chroma.mean(axis=1))
