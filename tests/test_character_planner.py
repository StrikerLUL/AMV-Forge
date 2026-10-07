"""Planer mit Figuren: --characters "Hori,Miyamura" nimmt Szenen mit beiden, und nie 3x dieselbe Figur."""

import random
from pathlib import Path

import pytest

from backend.config import load_settings
from backend.config.styles import load_style
from backend.planner.assign import Candidate, assign_to_beats, repeated_characters
from backend.planner.scoring import character_match, character_tiers, score_clip
from backend.planner.slots import Slot

DEFAULTS = load_settings().planner.weights
VIDEO = Path("folge.mkv")
HORI, MIYAMURA, TORU, YUKI = 1, 2, 3, 4
ROMANCE = {"romance": 0.9, "action": 0.1, "sad": 0.3, "funny": 0.2, "calm": 0.8}
ACTION = {"romance": 0.1, "action": 0.9, "sad": 0.2, "funny": 0.4, "calm": 0.1}
CAST = [{HORI: 0.9, MIYAMURA: 0.8}, {HORI: 0.95}, {MIYAMURA: 0.9}, {TORU: 0.9}, {TORU: 0.8, YUKI: 0.9}, {}]


def _season(n: int = 60) -> list[Candidate]:
    """Clips reihum: Paar, nur Hori, nur Miyamura, Toru, Toru + Yuki, niemand. Abwechselnd 6 ruhige, 6 Action."""
    return [Candidate(VIDEO, i * 10.0, i * 10.0 + 4.0, motion=1.0 + i * 0.01, peak=i * 10.0 + 1.0, episode=i % 4 + 1,
                      clip_id=i, mood=ACTION if (i // 6) % 2 else ROMANCE, quality=1.0, speech=0.0,
                      characters=CAST[i % 6])
            for i in range(n)]


def _slots(n: int) -> list[Slot]:
    return [Slot(i * 1.0, i * 1.0 + 1.0, intensity=0.5) for i in range(n)]


def test_only_scenes_with_both_while_there_are_enough() -> None:
    result = assign_to_beats(_slots(10), _season(), random.Random(1), DEFAULTS, 8, 2,
                             characters=[HORI, MIYAMURA], max_same_character_in_row=2)
    assert all(a.candidate and {HORI, MIYAMURA} <= a.candidate.character_ids for a in result)


def test_then_scenes_with_one_of_them_before_anything_else() -> None:
    result = assign_to_beats(_slots(25), _season(), random.Random(2), DEFAULTS, 8, 2,
                             characters=[HORI, MIYAMURA])
    sets = [a.candidate.character_ids for a in result if a.candidate]
    assert len({a.candidate.clip_id for a in result if a.candidate}) == 25  # kein Clip doppelt
    assert sum(1 for s in sets if {HORI, MIYAMURA} <= s) == 10  # alle 10 Paar-Szenen
    assert sum(1 for s in sets if s & {HORI, MIYAMURA}) == 25  # Rest: Hori oder Miyamura allein


def test_style_and_characters_together() -> None:
    romance = load_style("romance", DEFAULTS)
    # Die ruhigsten 30 % werden unter den Clips mit Hori oder Miyamura gesucht, nicht in der ganzen Staffel
    result = assign_to_beats(_slots(3), _season(), random.Random(3), romance.weights, 8, 2, target=romance.mood,
                             pool_share=romance.pool, characters=[HORI, MIYAMURA])
    for a in result:
        assert a.candidate and {HORI, MIYAMURA} <= a.candidate.character_ids
        assert a.candidate.mood is ROMANCE  # unter den Paar-Szenen die ruhigen


def test_no_character_three_times_in_a_row() -> None:
    clips = _season()
    result = assign_to_beats(_slots(30), clips, random.Random(4), DEFAULTS, 8, 0, max_same_character_in_row=2)
    sets = [a.candidate.character_ids if a.candidate else frozenset() for a in result]
    for i in range(len(sets) - 2):
        assert not (sets[i] & sets[i + 1] & sets[i + 2]), i


def test_wanted_characters_may_appear_in_every_clip() -> None:
    result = assign_to_beats(_slots(8), _season(), random.Random(5), DEFAULTS, 8, 2,
                             characters=[HORI], max_same_character_in_row=2)
    assert all(a.candidate and HORI in a.candidate.character_ids for a in result)


def test_repeated_characters() -> None:
    clips = _season(12)
    picks = [clips[0], clips[1], clips[7]]  # Paar, Hori, Hori: Hori in allen dreien
    result = assign_to_beats(_slots(3), picks, random.Random(0), DEFAULTS, 1, 0)
    by_id = {a.candidate.clip_id: a for a in result if a.candidate}
    ordered = [by_id[0], by_id[1], by_id[7]]
    assert repeated_characters(ordered, 3, frozenset()) == {HORI}
    assert repeated_characters(ordered, 3, frozenset({HORI})) == frozenset()
    assert repeated_characters(ordered[:2], 3, frozenset()) == frozenset()


def test_character_score() -> None:
    assert character_match({HORI: 0.9, MIYAMURA: 0.8}, [HORI, MIYAMURA]) == pytest.approx(0.85)
    assert character_match({HORI: 0.9}, [HORI, MIYAMURA]) == pytest.approx(0.45)
    assert character_match(None, [HORI]) == 0.0 and character_match({HORI: 1.0}, []) == 0.0
    pair, single = _season(2)
    with_pair = score_clip(pair, 0.5, 0.5, DEFAULTS, None, [], [HORI, MIYAMURA])
    with_single = score_clip(single, 0.5, 0.5, DEFAULTS, None, [], [HORI, MIYAMURA])
    assert with_pair.character == pytest.approx(0.85) and with_pair.total > with_single.total
    every, some = character_tiers(_season(12), [HORI, MIYAMURA])
    assert every == {0, 6} and some == {0, 1, 2, 6, 7, 8}
