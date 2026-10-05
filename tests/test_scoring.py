"""Score-Formel und Auswahl nach Stil: Romance nimmt ruhige Paar-Clips, Hype nimmt Action."""

import random
from pathlib import Path

import pytest

from backend.config import load_settings
from backend.config.settings import ScoreWeights
from backend.config.styles import load_style
from backend.planner.assign import Candidate, assign_to_beats
from backend.planner.scoring import repeat_share, score_clip, style_pool
from backend.planner.slots import Slot

DEFAULTS = load_settings().planner.weights
VIDEO = Path("folge.mkv")
ROMANCE = {"romance": 0.9, "action": 0.1, "sad": 0.4, "funny": 0.3, "calm": 0.8}
ACTION = {"romance": 0.1, "action": 0.9, "sad": 0.2, "funny": 0.4, "calm": 0.1}
TALK = {"romance": 0.4, "action": 0.4, "sad": 0.4, "funny": 0.5, "calm": 0.5}


def _season() -> list[Candidate]:
    """60 Clips: 20 ruhige Paar-Szenen, 20 Action, 20 Gespräche, verteilt auf 4 Folgen."""
    clips = []
    for i in range(60):
        kind = (ROMANCE, ACTION, TALK)[i % 3]
        motion = {id(ROMANCE): 0.3, id(ACTION): 5.0, id(TALK): 1.0}[id(kind)] + i * 0.001
        clips.append(Candidate(VIDEO, i * 10.0, i * 10.0 + 4.0, motion=motion, peak=i * 10.0 + 1.0,
                               episode=i % 4 + 1, clip_id=i, mood=kind, quality=1.0,
                               speech=0.8 if kind is TALK else 0.0))
    return clips


def _slots(n: int = 24) -> list[Slot]:
    return [Slot(i * 1.0, i * 1.0 + 1.0, intensity=0.9 if i % 2 else 0.4) for i in range(n)]


@pytest.mark.parametrize("style, wanted", [("romance", ROMANCE), ("hype", ACTION)])
def test_style_picks_matching_clips(style: str, wanted: dict) -> None:
    profile = load_style(style, DEFAULTS)
    # pool 0.3 von 60 Clips = 18 Clips im Stil, genau so viele Slots
    result = assign_to_beats(_slots(18), _season(), random.Random(1), profile.weights, 8, 2,
                             target=profile.mood, pool_share=profile.pool)
    assert all(a.candidate is not None and a.candidate.mood is wanted for a in result)
    assert len({a.candidate.clip_id for a in result if a.candidate}) == 18
    assert all(a.score is not None for a in result)


def test_when_the_style_pool_is_used_up_the_next_best_fresh_clips_follow() -> None:
    profile = load_style("romance", DEFAULTS)
    result = assign_to_beats(_slots(30), _season(), random.Random(4), profile.weights, 8, 2,
                             target=profile.mood, pool_share=profile.pool)
    ids = [a.candidate.clip_id for a in result if a.candidate]
    assert len(set(ids)) == 30  # lieber ein frischer Clip als einer doppelt
    assert sum(1 for a in result if a.candidate and a.candidate.mood is ROMANCE) >= 18  # der ganze Pool


def test_without_style_motion_follows_the_song() -> None:
    result = assign_to_beats(_slots(), _season(), random.Random(2), DEFAULTS, 8, 2)
    loud = [a.candidate.motion for a in result if a.slot.intensity > 0.5 and a.candidate]
    calm = [a.candidate.motion for a in result if a.slot.intensity < 0.5 and a.candidate]
    assert sum(loud) / len(loud) > sum(calm) / len(calm)


def test_score_parts() -> None:
    weights = ScoreWeights(mood=1.0, energy=0.5, character=0.0, quality=0.3, repeat=0.3, dialog=0.4)
    target = {"romance": 1.0, "action": -0.5}
    cand = Candidate(VIDEO, 0, 4, mood=ROMANCE, quality=0.8, speech=0.5, episode=3)
    s = score_clip(cand, motion_rank=0.2, slot_intensity=0.4, weights=weights, target=target,
                   recent_episodes=[3, 1, 3, 2])
    assert s.energy == pytest.approx(0.8)
    assert s.repeat == 0.5 and s.dialog == 0.5 and s.quality == 0.8
    expected = 1.0 * s.mood + 0.5 * 0.8 + 0.3 * 0.8 - 0.3 * 0.5 - 0.4 * 0.5
    assert s.total == pytest.approx(expected, abs=1e-3)


def test_unmeasured_clip_is_neutral() -> None:
    s = score_clip(Candidate(VIDEO, 0, 4), 0.5, 0.5, DEFAULTS, {"romance": 1.0}, [])
    assert s.mood == 0.5 and s.quality == 1.0 and s.dialog == 0.0 and s.repeat == 0.0


def test_repeat_share_and_pool() -> None:
    assert repeat_share(1, [1, 1, 2, 3]) == 0.5
    assert repeat_share(None, [1]) == 0.0 and repeat_share(1, []) == 0.0
    pool = style_pool(_season(), {"romance": 1.0, "action": -0.5}, 0.3)
    assert len(pool) == 18 and all(i % 3 == 0 for i in pool)
