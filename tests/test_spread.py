"""Streuung: nicht viele Clips aus derselben Szene, und keine Folge viel öfter als die anderen."""

import logging
import random
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from backend.config import load_settings
from backend.config.styles import load_style
from backend.planner.assign import Assignment, Candidate, assign_to_beats
from backend.planner.scoring import overuse_share, score_clip
from backend.planner.slots import Slot
from backend.planner.spread import Occupied, densest_stretch, fullest_window, spread_out

SETTINGS = load_settings().planner
ACTION = {"romance": 0.0, "action": 1.0, "sad": 0.0, "funny": 0.3, "calm": 0.0}
WILD = {"romance": 0.1, "action": 0.8, "sad": 0.1, "funny": 0.3, "calm": 0.1}
CALM = {"romance": 0.5, "action": 0.0, "sad": 0.2, "funny": 0.2, "calm": 1.0}


def _clip(episode: int, start: float, mood: dict[str, float], length: float = 2.0, clip_id: int = 0) -> Candidate:
    return Candidate(Path(f"folge{episode}.mkv"), start, start + length, motion=5.0, peak=start + 0.5,
                     episode=episode, clip_id=clip_id, mood=mood, quality=1.0, speech=0.0)


def _season() -> list[Candidate]:
    """Wie Horimiya beim hype-Edit: Folge 1 hat einen Kampf von 618 bis 650 s mit 16 kurzen Clips voller Action.

    Dazu in 6 Folgen alle 90 s ein Clip mit etwas weniger Action und viele ruhige Clips.
    """
    clips = [_clip(1, 618 + i * 2.0, ACTION) for i in range(16)]
    clips += [_clip(ep, 100 + k * 90.0, WILD) for ep in range(1, 7) for k in range(12)]
    clips += [_clip(ep, 105 + k * 30.0, CALM) for ep in range(1, 7) for k in range(36)]
    return [Candidate(c.video, c.start, c.end, c.motion, c.peak, c.episode, clip_id=i, mood=c.mood,
                      quality=c.quality, speech=c.speech) for i, c in enumerate(clips)]


def _slots(n: int) -> list[Slot]:
    return [Slot(i * 0.4, i * 0.4 + 0.4, intensity=0.8, hits=(0.0,)) for i in range(n)]


def _hype(clips: list[Candidate], seed: int, spread: int, overuse: float = 0.3) -> list[Assignment]:
    style = load_style("hype", SETTINGS.weights)
    return assign_to_beats(_slots(30), clips, random.Random(seed), replace(style.weights, overuse=overuse), 8, 2,
                           target=style.mood, pool_share=style.pool, spread_max_clips=spread, spread_window=60.0)


def _from_fight(result: list[Assignment]) -> int:
    return sum(1 for a in result if a.episode == 1 and a.candidate and 618 <= a.candidate.start <= 650)


def test_fullest_window() -> None:
    assert fullest_window([], 5.0, 60) == 1
    assert fullest_window([100.0], 0.0, 60) == 1
    assert fullest_window([10.0, 50.0], 30.0, 60) == 3
    # 0 und 70 passen nicht zusammen in 60 s, mit 30 sind es höchstens zwei
    assert fullest_window([0.0, 70.0], 30.0, 60) == 2
    assert fullest_window([0.0, 10.0, 50.0, 70.0], 30.0, 60) == 4
    assert fullest_window([25.0, 25.0], 25.0, 60) == 3  # derselbe Clip doppelt zählt doppelt


def test_spread_out_keeps_free_spots_and_never_empties_the_pool() -> None:
    clips = [_clip(1, 0, ACTION), _clip(1, 20, ACTION), _clip(1, 40, ACTION), _clip(1, 300, ACTION),
             _clip(2, 30, ACTION)]
    occupied = Occupied()
    occupied.add(clips[0])
    occupied.add(clips[1])
    # Folge 1 hat schon 2 Clips um 0-20 s: 40 s wäre der dritte in einer Minute, 300 s und Folge 2 sind frei
    assert spread_out([2, 3, 4], clips, occupied, 2, 60) == [3, 4]
    # Nur noch Clips an der vollen Stelle übrig: die Grenze steigt, statt einen Schnitt zu verlieren
    assert spread_out([2], clips, occupied, 2, 60) == [2]
    assert spread_out([2, 3], clips, occupied, 0, 60) == [2, 3]  # 0 = aus


def test_densest_stretch() -> None:
    clips = [_clip(1, 618 + i * 4.0, ACTION) for i in range(9)] + [_clip(2, 10, ACTION), _clip(1, 900, ACTION)]
    stretch = densest_stretch(clips, 60)
    assert stretch is not None and (stretch.episode, stretch.count) == (1, 9)
    assert stretch.start == 618 and stretch.end == 650
    assert densest_stretch([], 60) is None


@pytest.mark.parametrize("seed", [7, 8, 9])
def test_hype_edit_takes_at_most_two_clips_from_the_fight(seed: int) -> None:
    clips = _season()
    before = _hype(clips, seed, spread=0, overuse=0.0)
    after = _hype(clips, seed, spread=2)
    assert _from_fight(before) >= 6  # ohne Streuung erzählt das Edit den Kampf nach
    assert _from_fight(after) <= 2
    assert densest_stretch([a.candidate for a in after], 60).count <= 2
    # Statt des Kampfs kommen die anderen Action-Clips, keine ruhigen
    assert all(a.candidate.mood is not CALM for a in after)
    assert len({a.candidate.clip_id for a in after}) == 30


def test_scarce_pool_relaxes_instead_of_losing_cuts(caplog: pytest.LogCaptureFixture) -> None:
    # Nur 10 Clips, alle aus derselben Minute: jeder Slot bekommt trotzdem einen Clip, keiner doppelt
    clips = [_clip(6, 300 + i * 5.0, ACTION, clip_id=i) for i in range(10)]
    with caplog.at_level(logging.INFO, logger="backend.planner.assign"):
        result = assign_to_beats(_slots(10), clips, random.Random(1), SETTINGS.weights, 8, 2,
                                 spread_max_clips=2, spread_window=60.0)
    assert len(result) == 10 and len({a.candidate.clip_id for a in result}) == 10
    assert [a.crowd for a in result] == list(range(1, 11))
    assert "gelockert" in caplog.text


def test_same_seed_same_edit_with_spread() -> None:
    clips = _season()
    assert _hype(clips, 5, spread=2) == _hype(clips, 5, spread=2)


def test_without_spread_nothing_changes() -> None:
    """spread_max_clips 0 und overuse 0 = genau das Edit von vorher (für den Vergleich vorher/nachher)."""
    clips = _season()
    style = load_style("hype", SETTINGS.weights)
    plain = assign_to_beats(_slots(30), clips, random.Random(3), replace(style.weights, overuse=0.0), 8, 2,
                            target=style.mood, pool_share=style.pool)
    off = _hype(clips, 3, spread=0, overuse=0.0)
    picks = [(a.candidate.clip_id, a.source_start) for a in plain if a.candidate]
    assert picks == [(a.candidate.clip_id, a.source_start) for a in off if a.candidate]
    assert all(a.crowd is None for a in off)


def test_overuse_share() -> None:
    # 26 Clips aus 13 Folgen = Durchschnitt 2
    counts = Counter({1: 5, 2: 4, 3: 3, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 9: 1, 10: 1, 11: 1, 12: 1})
    assert sum(counts.values()) == 26
    assert overuse_share(4, counts, 13) == 0.0
    assert overuse_share(3, counts, 13) == pytest.approx(1 / 3)
    assert overuse_share(2, counts, 13) == pytest.approx(2 / 3)
    assert overuse_share(1, counts, 13) == 1.0
    assert overuse_share(13, counts, 13) == 0.0  # noch gar nicht dran
    assert overuse_share(None, counts, 13) == 0.0 and overuse_share(1, Counter(), 13) == 0.0
    assert overuse_share(1, Counter({1: 9}), 1) == 0.0  # quick: nur eine Folge


def test_overused_episode_scores_lower() -> None:
    weights = SETTINGS.weights
    clip = _clip(1, 0, ACTION)
    fresh = score_clip(clip, 0.5, 0.5, weights, None, [], edit_episodes=Counter({2: 3, 3: 3}), episode_count=3)
    crowded = score_clip(clip, 0.5, 0.5, weights, None, [], edit_episodes=Counter({1: 6, 2: 1}), episode_count=3)
    assert fresh.overuse == 0.0 and crowded.overuse > 0.5
    assert crowded.total == pytest.approx(fresh.total - weights.overuse * crowded.overuse, abs=1e-3)


def test_settings_and_styles_have_spread() -> None:
    assert SETTINGS.spread_max_clips == 2 and SETTINGS.spread_window_seconds == 60
    assert SETTINGS.weights.overuse == 0.3
    assert load_style("romance", SETTINGS.weights).weights.overuse == 0.3  # Stile erben das Gewicht
