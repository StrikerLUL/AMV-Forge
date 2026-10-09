"""Tonart: Profil-Vergleich (Krumhansl-Schmuckler) und echte Akkordfolgen."""

from pathlib import Path

import librosa
import numpy as np

from backend.analysis.music.key import MAJOR_PROFILE, MINOR_PROFILE, Key, estimate_key, song_key
from tests.synth_song import SR, make_ballad


def test_profiles_are_found_in_every_key() -> None:
    g_major = estimate_key(np.roll(MAJOR_PROFILE, 7))
    assert (g_major.name, g_major.major) == ("G-Dur", True) and g_major.majorness > 0.5
    fis_minor = estimate_key(np.roll(MINOR_PROFILE, 6))
    assert (fis_minor.name, fis_minor.major) == ("fis-Moll", False) and fis_minor.majorness < 0.5


def test_german_names() -> None:
    assert Key(10, True, 0.8, 1.0).name == "B-Dur"  # englisch Bb
    assert Key(11, False, 0.8, 0.0).name == "h-Moll"  # englisch B minor


def test_silence_has_no_key() -> None:
    assert estimate_key(np.zeros(12)).majorness == 0.5


def _progression(chords: list[tuple[float, ...]], seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    parts = [sum(np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) for f in chord) for chord in chords]
    return np.concatenate(parts * 3).astype(np.float32)


def test_c_major_chords() -> None:
    c, f, g = (261.63, 329.63, 392.0), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66)
    key = song_key(_progression([c, f, g, c]), SR)
    assert key.name == "C-Dur" and key.majorness > 0.6


def test_ballad_is_a_minor(tmp_path: Path) -> None:
    y, sr = librosa.load(str(make_ballad(tmp_path / "ballad.wav")), sr=SR)
    key = song_key(y, sr)
    assert key.name == "a-Moll" and key.majorness < 0.4
