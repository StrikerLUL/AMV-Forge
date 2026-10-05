"""Stimmungsvektor: Ränge innerhalb der Staffel, gewichtete Mischung, fehlende Signale."""

import pytest

from backend.analysis.mood import MOODS, ClipSignals, dominant, mood_match, mood_vectors, percentile_ranks
from backend.config import load_settings

WEIGHTS = load_settings().mood.weights


def test_percentile_ranks() -> None:
    assert percentile_ranks([3.0, 1.0, None, 2.0]) == [1.0, 0.0, None, 0.5]
    assert percentile_ranks([5.0, 5.0, 1.0]) == [0.75, 0.75, 0.0]  # gleiche Werte, gleicher Rang
    assert percentile_ranks([7.0]) == [0.5]
    assert percentile_ranks([None, None]) == [None, None]


def _clip(romance: float, action: float, motion: float, loud: float, speech: float) -> ClipSignals:
    clip = {m: 0.05 for m in MOODS} | {"romance": romance, "action": action}
    return ClipSignals(clip=clip, motion=motion, loudness=loud, speech=speech)


def test_quiet_couple_is_romance_and_loud_fight_is_action() -> None:
    signals = [
        _clip(romance=0.8, action=0.0, motion=0.2, loud=-35, speech=0.0),  # ruhige Paar-Szene
        _clip(romance=0.0, action=0.7, motion=6.0, loud=-8, speech=0.3),  # Kampf
        _clip(romance=0.1, action=0.1, motion=1.0, loud=-20, speech=0.9),  # Gespräch
    ]
    couple, fight, talk = mood_vectors(signals, WEIGHTS)
    assert couple["romance"] > talk["romance"] > fight["romance"]
    assert fight["action"] > talk["action"] > couple["action"]
    assert dominant(fight) == "action"
    assert all(0.0 <= v <= 1.0 for v in couple.values())


def test_missing_signals_are_left_out_not_counted_as_zero() -> None:
    only_motion = [ClipSignals(motion=0.1), ClipSignals(motion=5.0)]
    calm, wild = mood_vectors(only_motion, WEIGHTS)
    assert calm["calm"] == 1.0 and wild["action"] == 1.0
    assert mood_vectors([ClipSignals()], WEIGHTS) == [{m: 0.5 for m in MOODS}]


def test_unknown_signal_in_weights_is_an_error() -> None:
    with pytest.raises(ValueError, match="gefuehl"):
        mood_vectors([ClipSignals(motion=1.0)], {"gefuehl": {"romance": 1.0}})


def test_mood_match() -> None:
    target = {"romance": 1.0, "action": -0.6}
    assert mood_match({"romance": 1.0, "action": 0.0}, target) == 1.0
    assert mood_match({"romance": 0.0, "action": 1.0}, target) == 0.0
    assert 0.0 < mood_match({"romance": 0.5, "action": 0.5}, target) < 1.0
    assert mood_match({"romance": 1.0}, {}) == 0.5
