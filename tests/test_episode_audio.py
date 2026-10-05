"""Ton der Folge: Lautstärke und Sprachanteil pro Clip."""

import numpy as np
import pytest

from backend.analysis.audio.episode_audio import analyze_clips, overlap_share, rms_db, silero_installed

SR = 16000


def test_loudness_in_dbfs() -> None:
    assert rms_db(np.zeros(SR, dtype=np.float32)) <= -99
    full_sine = np.sin(np.linspace(0, 2 * np.pi * 440, SR)).astype(np.float32)
    assert rms_db(full_sine) == pytest.approx(-3.0, abs=0.1)
    assert rms_db(np.array([], dtype=np.float32)) == -100.0


def test_speech_share_per_clip() -> None:
    speech = [(1.0, 2.0), (5.0, 9.0)]
    assert overlap_share(0.0, 4.0, speech) == 0.25
    assert overlap_share(6.0, 8.0, speech) == 1.0
    assert overlap_share(2.0, 5.0, speech) == 0.0


def test_quiet_and_loud_clips() -> None:
    audio = np.zeros(SR * 4, dtype=np.float32)
    audio[SR * 2:] = 0.5 * np.sin(np.linspace(0, 2 * np.pi * 880, SR * 2))
    quiet, loud = analyze_clips(audio, SR, [(0.0, 2.0), (2.0, 4.0)], [(2.5, 3.5)])
    assert quiet.loudness < -90 and loud.loudness > -10
    assert quiet.speech == 0.0 and loud.speech == 0.5
    (no_vad,) = analyze_clips(audio, SR, [(0.0, 4.0)], None)
    assert no_vad.speech is None


@pytest.mark.skipif(not silero_installed(), reason="silero-vad nicht installiert")
def test_silero_hears_no_speech_in_a_beep() -> None:
    from backend.analysis.audio.episode_audio import SileroVad

    beep = (0.3 * np.sin(np.linspace(0, 2 * np.pi * 1000 * 5, SR * 5))).astype(np.float32)
    assert SileroVad(0.5)(beep, SR) == []
