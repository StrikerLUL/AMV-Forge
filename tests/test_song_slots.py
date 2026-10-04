"""Schnitt-Planer nach Song-Struktur mit einem handgebauten Song (120 BPM, Beat = 0,5 s, Takt = 2 s)."""

from dataclasses import replace

import pytest

from backend.analysis.music.energy import Drop
from backend.analysis.music.structure import Section, SongAnalysis
from backend.config import load_settings
from backend.planner.song_slots import build_song_slots, choose_edit_start, ramp, summarize, zones_per_beat

CUTS = load_settings().cuts


def make_song(bpm: float = 120.0, drops: tuple[float, ...] = (32.0,)) -> SongAnalysis:
    """160 Beats: Intro Takt 0-8, Verse 8-16, Chorus 16-32, Outro 32-40. drops in Sekunden bei 120 BPM."""
    beat = 60.0 / bpm
    bar = 4 * beat
    beats = [i * beat for i in range(160)]
    duration = 160 * beat
    sections = [
        Section(0.0, 8 * bar, "intro", 0.1),
        Section(8 * bar, 16 * bar, "verse", 0.5),
        Section(16 * bar, 32 * bar, "chorus", 0.9),
        Section(32 * bar, duration, "outro", 0.2),
    ]
    levels = [(8 * bar, 0.1), (8 * bar, 0.5), (16 * bar, 0.95), (8 * bar, 0.2)]
    energy = [e for length, e in levels for _ in range(int(round(length * 10)))]
    return SongAnalysis(
        path="test.wav", analyzer="test", duration=duration, bpm=bpm, beats=beats, downbeats=beats[::4],
        sections=sections, drops=[Drop(d * beat / 0.5, 0.5) for d in drops], energy=energy, energy_rate=10.0,
    )


def test_ramp() -> None:
    assert ramp(4, 4, 1) == [4.0, 2.0, 2.0, 1.0]
    assert ramp(2, 4, 1) == [4.0, 1.0]
    assert ramp(1, 4, 1) == [1.0]
    assert ramp(0, 4, 1) == []


def test_zones() -> None:
    song = make_song()
    zones = zones_per_beat(song, CUTS)
    assert zones[0].label == "intro" and zones[0].beats_per_cut == 4
    assert zones[40].label == "verse" and zones[40].beats_per_cut == 2
    assert zones[48].label == "buildup" and zones[48].beats_per_cut == 4  # 4 Takte vor dem Drop (Beat 64)
    assert zones[63].label == "buildup" and zones[63].beats_per_cut == 1
    assert zones[64].label == "drop" and zones[64].beats_per_cut == 0.5
    assert zones[79].label == "drop"  # Drop dauert 4 Takte = 16 Beats
    assert zones[80].label == "chorus" and zones[80].beats_per_cut == 1
    assert zones[130].label == "outro"


def test_slots_cover_the_edit_and_follow_the_structure() -> None:
    song = make_song()
    slots = build_song_slots(song, song_start=16.0, length=22.0, cfg=CUTS)
    assert slots[0].start == 0.0
    assert slots[-1].end == pytest.approx(22.0)
    for a, b in zip(slots, slots[1:]):
        assert a.end == pytest.approx(b.start)

    by_section: dict[str, list[float]] = {}
    for s in slots:
        by_section.setdefault(s.section, []).append(round(s.duration, 3))
    assert set(by_section["verse"]) == {1.0}  # alle 2 Beats
    assert by_section["buildup"] == [2.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.5]  # 4 -> 2 -> 2 -> 1 Beats
    assert set(by_section["drop"][:-1]) == {0.25}  # halbe Beats (der letzte endet beim Edit-Ende)
    # Drop beginnt exakt beim Drop: Song 32 s = Edit 16 s
    first_drop = next(s for s in slots if s.section == "drop")
    assert first_drop.start == pytest.approx(16.0)


def test_drop_intensity_is_higher_than_verse() -> None:
    slots = build_song_slots(make_song(), 16.0, 22.0, CUTS)
    verse = [s.intensity for s in slots if s.section == "verse"]
    drop = [s.intensity for s in slots if s.section == "drop"]
    assert min(drop) > max(verse)


def test_hits_start_with_the_cut_then_downbeats() -> None:
    song = make_song(drops=())
    slots = build_song_slots(song, 0.0, 16.0, CUTS)  # Intro: ein Clip pro Takt (4 Beats)
    assert slots[0].hits == (0.0, 0.5, 1.0, 1.5)
    assert all(s.hits[0] == 0.0 for s in slots)


def test_very_fast_song_does_not_cut_below_min_cut_seconds() -> None:
    song = make_song(bpm=200.0, drops=(32.0,))  # Beat 0,3 s, halber Beat 0,15 s < 0,18 s
    drop = song.drops[0].time
    slots = build_song_slots(song, drop - 3.0, 6.0, CUTS)
    assert min(s.duration for s in slots if s.section == "drop") == pytest.approx(0.3)


def test_summary() -> None:
    slots = build_song_slots(make_song(), 16.0, 22.0, CUTS)
    labels = [label for label, _, _ in summarize(slots)]
    assert labels == ["verse", "buildup", "drop"]


def test_edit_start_puts_drop_at_forty_percent() -> None:
    song = make_song()
    start = choose_edit_start(song, 30.0, None, 0.4)
    assert start in song.downbeats
    assert song.drops[0].time - start == pytest.approx(12.0, abs=1.0)


def test_edit_start_without_drop_uses_first_chorus() -> None:
    song = make_song(drops=())
    start = choose_edit_start(song, 10.0, None, 0.4)
    assert start == pytest.approx(32.0 - 4.0)  # erster Refrain bei 32 s


def test_edit_start_respects_wanted_start_and_song_end() -> None:
    song = make_song()
    assert choose_edit_start(song, 10.0, 10.2, 0.4) == 10.0
    late = choose_edit_start(song, 10.0, 79.0, 0.4)
    assert late + 10.0 <= song.duration
    with pytest.raises(ValueError):
        choose_edit_start(song, 100.0)


def test_section_rates_come_from_yaml() -> None:
    cfg = replace(CUTS, beats_per_cut={**CUTS.beats_per_cut, "verse": 4})
    slots = build_song_slots(make_song(drops=()), 16.0, 8.0, cfg)
    assert {round(s.duration, 3) for s in slots} == {2.0}
