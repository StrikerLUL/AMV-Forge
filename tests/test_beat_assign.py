"""Clip-Auswahl in Phase 3: Bewegungs-Peak auf den Beat, Bewegung passend zur Song-Energie."""

import random
from pathlib import Path

import pytest

from backend.planner.assign import Candidate, align_start, assign_to_beats
from backend.planner.slots import Slot

VIDEO = Path("folge.mkv")


def test_peak_lands_on_the_cut() -> None:
    cand = Candidate(VIDEO, 10.0, 14.0, motion=1.0, peak=11.2)
    start, aligned = align_start(cand, Slot(0.0, 1.0, hits=(0.0, 0.5)))
    assert aligned and start == pytest.approx(11.2)  # Peak = erstes Bild nach dem Schnitt


def test_late_peak_uses_a_beat_inside_the_slot() -> None:
    cand = Candidate(VIDEO, 10.0, 12.4, motion=1.0, peak=11.8)
    slot = Slot(0.0, 1.0, hits=(0.0, 0.5))
    start, aligned = align_start(cand, slot)
    # Ab 11,8 s passt 1 s nicht mehr rein, also liegt der Peak auf dem zweiten Beat (0,5 s im Slot)
    assert aligned and start == pytest.approx(11.3)
    assert start + slot.duration <= cand.end + 1e-9


def test_peak_that_fits_nowhere_is_not_aligned_but_stays_inside() -> None:
    cand = Candidate(VIDEO, 10.0, 11.05, motion=1.0, peak=11.0)
    start, aligned = align_start(cand, Slot(0.0, 1.0, hits=(0.0,)))
    assert not aligned
    assert 10.0 <= start <= 10.05 + 1e-9


def test_without_peak_the_middle_is_used() -> None:
    start, aligned = align_start(Candidate(VIDEO, 10.0, 14.0), Slot(0.0, 2.0))
    assert not aligned and start == pytest.approx(11.0)


def _clips(n: int) -> list[Candidate]:
    # Clip i hat Bewegung i: 0 = ganz ruhig, n-1 = wild
    return [Candidate(VIDEO, i * 5.0, i * 5.0 + 4.0, motion=float(i), peak=i * 5.0 + 1.0, episode=i % 3 + 1)
            for i in range(n)]


def test_loud_slots_get_wild_clips_and_calm_slots_calm_clips() -> None:
    clips = _clips(100)
    slots = [Slot(i * 0.5, i * 0.5 + 0.5, intensity=1.0 if i % 2 else 0.0) for i in range(20)]
    result = assign_to_beats(slots, clips, random.Random(0), pick_from_top=5)
    for a in result:
        motion = next(c.motion for c in clips if c.start <= a.source_start < c.end)
        if a.slot.intensity == 1.0:
            assert motion >= 85
        else:
            assert motion <= 14


def test_no_clip_twice_and_peaks_on_beats() -> None:
    clips = _clips(40)
    slots = [Slot(i * 1.0, i * 1.0 + 1.0, intensity=0.5) for i in range(30)]
    result = assign_to_beats(slots, clips, random.Random(1))
    starts = [int(a.source_start // 5) for a in result]
    assert len(set(starts)) == len(starts)
    assert all(a.aligned for a in result)
    assert all(a.video == VIDEO for a in result)


def test_not_too_many_clips_from_the_same_episode_in_a_row() -> None:
    clips = [Candidate(VIDEO, i * 5.0, i * 5.0 + 4.0, motion=1.0, peak=i * 5.0 + 1, episode=1) for i in range(30)]
    clips += [Candidate(VIDEO, 500 + i * 5.0, 504 + i * 5.0, motion=1.0, peak=501 + i * 5.0, episode=2) for i in range(5)]
    slots = [Slot(i * 1.0, i * 1.0 + 1.0) for i in range(12)]
    result = assign_to_beats(slots, clips, random.Random(3), max_same_episode_in_row=2)
    episodes = [a.episode for a in result]
    for i in range(len(episodes) - 2):
        if episodes[i] == episodes[i + 1] == episodes[i + 2]:
            # nur erlaubt, wenn Folge 2 schon aufgebraucht ist
            assert sum(1 for e in episodes[: i + 2] if e == 2) == 5


def test_same_seed_same_edit() -> None:
    clips = _clips(50)
    slots = [Slot(i * 0.5, i * 0.5 + 0.5, intensity=i / 20) for i in range(20)]
    assert assign_to_beats(slots, clips, random.Random(9)) == assign_to_beats(slots, clips, random.Random(9))


def test_no_candidates_raises() -> None:
    with pytest.raises(ValueError):
        assign_to_beats([Slot(0, 1)], [], random.Random(0))
