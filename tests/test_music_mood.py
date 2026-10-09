"""Song-Stimmung: Messwerte, Arousal/Valenz, CLAP-Zero-Shot und das Mischen der Signale."""

from pathlib import Path

import librosa
import numpy as np
import pytest

from backend.analysis.music.clap import MusicPrompts, load_music_prompts, window_starts, zero_shot
from backend.analysis.music.mood import arousal_valence, combine, describe, feature_mood, measure
from backend.config import load_settings
from tests.synth_song import SR, make_ballad

CFG = load_settings().music_mood


def _features(path: Path, bpm: float):  # type: ignore[no-untyped-def]
    y, sr = librosa.load(str(path), sr=SR)
    return measure(y, sr, bpm)


def test_ballad_is_calmer_and_sadder_than_edm(tmp_path: Path, synth_song: Path) -> None:
    ballad = _features(make_ballad(tmp_path / "ballad.wav"), 72.0)
    edm = _features(synth_song, 128.0)
    assert ballad.key == "a-Moll" and ballad.major < 0.5
    assert ballad.onsets < edm.onsets and ballad.brightness < edm.brightness
    assert 0.0 <= ballad.percussive <= 1.0 and 0.0 <= edm.percussive <= 1.0

    a_ballad, v_ballad = arousal_valence(ballad, CFG)
    a_edm, v_edm = arousal_valence(edm, CFG)
    assert a_ballad < a_edm and v_ballad < v_edm
    mood_ballad, mood_edm = feature_mood(a_ballad, v_ballad, CFG), feature_mood(a_edm, v_edm, CFG)
    assert mood_ballad["sad"] > mood_ballad["action"]
    assert mood_edm["action"] - mood_edm["sad"] > mood_ballad["action"] - mood_ballad["sad"]


def test_feature_mood_is_one_at_the_prototype() -> None:
    for mood, (arousal, valence) in CFG.prototypes.items():
        values = feature_mood(arousal, valence, CFG)
        assert values[mood] == 1.0 and max(values, key=values.get) == mood  # type: ignore[arg-type]


def test_combine_weights_and_missing_signals() -> None:
    features = {"romance": 0.2, "action": 1.0, "sad": 0.0, "funny": 0.5, "calm": 0.5}
    clap = {"romance": 1.0, "action": 0.0, "sad": 0.0, "funny": 0.5, "calm": 0.5}
    mixed = combine({"features": features, "clap": clap}, {"features": 1.0, "clap": 3.0})
    assert mixed["romance"] == pytest.approx(0.8) and mixed["action"] == pytest.approx(0.25)
    assert combine({"features": features}, {"features": 0.4, "clap": 1.0}) == features  # ohne CLAP: nur Messwerte
    assert combine({}, {"features": 1.0})["calm"] == 0.5
    with pytest.raises(ValueError, match="unbekanntes Signal"):
        combine({"features": features}, {"features": 1.0, "spotify": 1.0})


def test_window_starts_skip_intro_and_outro() -> None:
    starts = window_starts(200.0, 6, 10.0)
    assert len(starts) == 6 and starts[0] == 20.0 and starts[-1] == 170.0
    assert window_starts(8.0, 6, 10.0) == [0.0]  # kürzer als ein Stück
    assert window_starts(200.0, 1, 10.0) == [95.0]
    assert window_starts(200.0, 0, 10.0) == []


def _prompts() -> MusicPrompts:
    return MusicPrompts(["love", "fight", "tears", "joke", "rest"], ["romance", "action", "sad", "funny", "calm"])


def test_zero_shot_finds_the_mood() -> None:
    texts = np.eye(5, dtype=np.float32)
    audio = np.array([[1.0, 0.1, 0.0, 0.0, 0.0], [0.9, 0.0, 0.0, 0.0, 0.2]], dtype=np.float32)
    tags = zero_shot(audio, texts, _prompts(), logit_scale=20.0)
    assert tags.top_prompt == "love" and tags.mood["romance"] == 1.0
    assert sum(tags.probs.values()) == pytest.approx(1.0, abs=1e-3)
    assert max(tags.mood.values()) == tags.mood["romance"] and tags.mood["sad"] < 0.5


def test_zero_shot_undecided_is_half() -> None:
    tags = zero_shot(np.ones((3, 5), dtype=np.float32), np.eye(5, dtype=np.float32), _prompts(), logit_scale=20.0)
    assert all(v == pytest.approx(0.5, abs=1e-3) for v in tags.mood.values())


def test_music_prompts_file_is_valid(tmp_path: Path) -> None:
    prompts = load_music_prompts(CFG.prompts)
    assert set(prompts.groups) == {"romance", "action", "sad", "funny", "calm"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("moods:\n  epic:\n    - 'epic music'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unbekannte Stimmung epic"):
        load_music_prompts(bad)


def test_describe() -> None:
    assert describe(0.1, 0.2) == "ruhig, eher traurig/düster"
    assert describe(0.9, 0.8) == "energiegeladen, eher fröhlich"
