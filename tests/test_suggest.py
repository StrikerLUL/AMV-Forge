"""Songvorschläge (Phase 7): Teilnoten, Reihenfolge, Begründungen."""

from pathlib import Path

import pytest

from backend.analysis.music.energy import Drop
from backend.analysis.music.structure import Section, SongAnalysis
from backend.config import load_settings
from backend.config.styles import load_style
from backend.planner.assign import Candidate
from backend.planner.suggest import (LibrarySong, SeasonProfile, profile_similarity, rate, season_profile, standout,
                                     suggest, tempo_fit)

SETTINGS = load_settings()
ROMANCE = load_style("romance", SETTINGS.planner.weights)
HYPE = load_style("hype", SETTINGS.planner.weights)
STORY = load_style("story", SETTINGS.planner.weights)

LOVE = {"romance": 0.9, "action": 0.1, "sad": 0.4, "funny": 0.4, "calm": 0.7}
FIGHT = {"romance": 0.1, "action": 0.95, "sad": 0.1, "funny": 0.5, "calm": 0.1}
TEARS = {"romance": 0.4, "action": 0.05, "sad": 0.95, "funny": 0.05, "calm": 0.6}


def _analysis(bpm: float, duration: float = 120.0, drop: float | None = None, strength: float = 0.6) -> SongAnalysis:
    beat = 60.0 / bpm
    beats = [round(i * beat, 4) for i in range(int(duration / beat))]
    sections = ([Section(0.0, duration, "verse", 0.5)] if drop is None else
                [Section(0.0, drop, "verse", 0.4), Section(drop, duration, "chorus", 0.9)])
    return SongAnalysis(path="song.mp3", analyzer="test", duration=duration, bpm=bpm, beats=beats,
                        downbeats=beats[::4], sections=sections, drops=[Drop(drop, strength)] if drop else [],
                        energy=[0.5] * int(duration * 2), energy_rate=2.0)


def _song(title: str, mood: dict[str, float], bpm: float, drop: float | None = None, artist: str | None = None,
          duration: float = 120.0) -> LibrarySong:
    return LibrarySong(path=f"{title}.mp3", title=title, artist=artist, analysis=_analysis(bpm, duration, drop),
                       mood=mood, key="a-Moll")


def _season() -> list[Candidate]:
    """20 ruhige Paar-Szenen (Figuren 1 und 2), 20 Kämpfe (nur Figur 1)."""
    love = [Candidate(Path("e1.mkv"), i, i + 2.0, mood=LOVE, characters={1: 0.9, 2: 0.8}) for i in range(20)]
    fight = [Candidate(Path("e2.mkv"), i, i + 2.0, mood=FIGHT, characters={1: 0.9}) for i in range(20)]
    return love + fight


def _profile(style_name: str = "romance", pool: int = 40) -> SeasonProfile:
    mood = LOVE if style_name == "romance" else FIGHT
    return SeasonProfile(title="Horimiya", clips=40, pool=pool, mood=mood)


def test_tempo_fit() -> None:
    assert tempo_fit(80, (70, 100), 0.4, 0.85) == (1.0, 1.0)
    assert tempo_fit(150, (70, 100), 0.4, 0.85) == (0.85, 2.0)  # im halben Tempo wie 75 BPM
    assert tempo_fit(110, (70, 100), 0.4, 0.85) == (0.75, 1.0)  # 10 % zu schnell
    assert tempo_fit(200, (140, 175), 0.4, 0.85)[0] < 1.0
    assert tempo_fit(120, None, 0.4, 0.85) == (1.0, 1.0)  # Stil ohne Tempo (story)


def test_profile_similarity_compares_shape_not_height() -> None:
    assert profile_similarity(LOVE, LOVE) == 1.0
    assert profile_similarity(LOVE, {m: v * 0.5 + 0.2 for m, v in LOVE.items()}) == pytest.approx(1.0)
    assert profile_similarity(LOVE, {m: 1 - v for m, v in LOVE.items()}) == pytest.approx(0.0)
    assert profile_similarity(LOVE, {}) == 0.5
    assert standout(LOVE) == ["romance", "calm"]


def test_season_profile_uses_the_style_pool() -> None:
    clips = _season()
    romance = season_profile(clips, ROMANCE, "Horimiya")
    hype = season_profile(clips, HYPE, "Horimiya")
    assert romance.pool == hype.pool == 12  # pool: 0.3 der 40 Clips
    assert romance.mood["romance"] == LOVE["romance"] and hype.mood["action"] == FIGHT["action"]
    couple = season_profile(clips, HYPE, "Horimiya", characters=[1, 2])
    assert couple.pool == 0  # Kämpfe gibt es nur ohne Figur 2
    assert season_profile(clips, ROMANCE, "Horimiya", characters=[1, 2]).pool == 12
    assert season_profile(clips, ROMANCE, "Horimiya", characters=[3]).pool == 0


def test_romance_and_hype_pick_different_songs() -> None:
    songs = [_song("Slow Love", LOVE, 82), _song("Battle Cry", FIGHT, 160, drop=60.0), _song("Goodbye", TEARS, 66)]
    romance = suggest(songs, ROMANCE, _profile("romance"), SETTINGS, 30.0, top=3)
    hype = suggest(songs, HYPE, _profile("hype"), SETTINGS, 30.0, top=3)
    assert romance[0].song.title == "Slow Love" and romance[-1].song.title == "Battle Cry"
    assert hype[0].song.title == "Battle Cry" and hype[-1].song.title != "Battle Cry"
    assert all(0.0 <= s.score <= 1.0 for s in romance + hype)

    best = romance[0]
    assert best.reasons[0] == "klingt romantisch und ruhig wie die romance-Szenen von Horimiya"
    assert "82 BPM passt zu romance (70-100)" in best.reasons
    assert best.factor == 1.0 and best.drop is None and best.cuts > 5
    battle = hype[0]
    assert battle.drop is not None and battle.window_start < 60.0 < battle.window_start + 30
    assert "Drop bei 1:00 im Ausschnitt" in battle.reasons and battle.parts["drop"] == 1.0


def test_concerns_name_what_does_not_fit() -> None:
    loud_love = rate(_song("Loud Love", LOVE, 82, drop=60.0), ROMANCE, _profile("romance"), SETTINGS, 30.0)
    assert loud_love.concerns == ["harter Drop bei 1:00"] and loud_love.parts["drop"] == 0.0
    no_drop = rate(_song("Flat", FIGHT, 160), HYPE, _profile("hype"), SETTINGS, 30.0)
    assert "kein Drop im Ausschnitt" in no_drop.concerns
    halftime = rate(_song("Fast Love", LOVE, 150), ROMANCE, _profile("romance"), SETTINGS, 30.0)
    assert halftime.factor == 2.0 and "150 BPM passt zu romance (70-100), im halben Tempo gezählt (wie 75)" in \
        halftime.reasons
    few = rate(_song("Slow Love", LOVE, 82), ROMANCE, _profile("romance", pool=3), SETTINGS, 30.0)
    assert any(c.startswith("nur 3 passende Clips für") for c in few.concerns)
    assert few.score < rate(_song("Slow Love", LOVE, 82), ROMANCE, _profile("romance"), SETTINGS, 30.0).score


def test_story_ignores_tempo() -> None:
    result = rate(_song("Any", TEARS, 200, drop=60.0), STORY, _profile("romance"), SETTINGS, 30.0)
    assert result.weights["tempo"] == 0.0 and not any("BPM" in r for r in result.reasons + result.concerns)


def test_suggest_limits_artists_and_skips_short_songs() -> None:
    songs = [_song(f"Love {i}", LOVE, 80 + i, artist="Duo") for i in range(3)]
    songs += [_song("Other", LOVE, 75, artist="Solo"), _song("Short", LOVE, 80, duration=20.0)]
    result = suggest(songs, ROMANCE, _profile("romance"), SETTINGS, 30.0, top=5)
    titles = [s.song.title for s in result]
    assert len(titles) == 3 and "Other" in titles and "Short" not in titles
    assert sum(s.song.artist == "Duo" for s in result) == 2
