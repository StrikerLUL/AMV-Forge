import pytest

from backend.planner.slots import build_slots, choose_song_start

BEATS = [0.5 + i * 0.5 for i in range(200)]  # 120 BPM, erster Beat bei 0.5 s


def test_song_start_is_first_beat_by_default() -> None:
    assert choose_song_start(BEATS, 100.0, 30.0) == 0.5


def test_song_start_snaps_to_nearest_beat() -> None:
    assert choose_song_start(BEATS, 100.0, 30.0, wanted_start=10.2) == 10.0


def test_song_start_moves_back_if_too_late() -> None:
    start = choose_song_start(BEATS, 100.0, 30.0, wanted_start=90.0)
    assert start + 30.0 <= 100.0
    assert start in BEATS


def test_song_too_short_raises() -> None:
    with pytest.raises(ValueError):
        choose_song_start(BEATS, 20.0, 30.0)


def test_slots_cover_length_exactly_and_cut_on_beats() -> None:
    slots = build_slots(BEATS, song_start=0.5, length=30.0, beats_per_cut=2)
    assert slots[0].start == 0.0
    assert slots[-1].end == pytest.approx(30.0)
    for a, b in zip(slots, slots[1:]):
        assert a.end == b.start
    for s in slots[1:]:
        assert (s.start + 0.5) in BEATS
    assert all(s.duration == pytest.approx(1.0) for s in slots)


def test_beats_per_cut_changes_cut_rate() -> None:
    every_beat = build_slots(BEATS, 0.5, 10.0, beats_per_cut=1)
    every_bar = build_slots(BEATS, 0.5, 10.0, beats_per_cut=4)
    assert len(every_beat) == 20
    assert len(every_bar) == 5


def test_short_last_slot_is_merged() -> None:
    slots = build_slots(BEATS, 0.5, 10.1, beats_per_cut=2, min_slot_seconds=0.25)
    assert slots[-1].end == pytest.approx(10.1)
    assert slots[-1].duration == pytest.approx(1.1)


def test_invalid_beats_per_cut() -> None:
    with pytest.raises(ValueError):
        build_slots(BEATS, 0.5, 10.0, beats_per_cut=0)
