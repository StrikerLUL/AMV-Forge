"""Ganzer Ablauf von Phase 7 über die Kommandozeile: Musik analysieren, Songs vorschlagen."""

import logging
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session

from backend.analysis.music import clap as music_clap
from backend.cli import main
from backend.db import get_engine
from backend.db.models import Character, Clip, Episode, Season
from tests.fake_models import FakeClap
from tests.synth_song import make_ballad

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")

# Wie in test_cli_edit: keine großen Modelle. CLAP ist der Ersatz aus fake_models.py.
LIGHT = 'clip:\n  enabled: false\nepisode_audio:\n  vad: none\nsubtitles:\n  model: ""\nfaces:\n  enabled: false\n'
LOVE = {"romance": 0.9, "action": 0.1, "sad": 0.3, "funny": 0.4, "calm": 0.7}
FIGHT = {"romance": 0.1, "action": 0.95, "sad": 0.1, "funny": 0.5, "calm": 0.1}


def _season() -> None:
    """Eine fertig analysierte Staffel (Phase 4 und 5): 30 ruhige Szenen mit Hori und Miyamura, 30 Kämpfe."""
    with Session(get_engine(Path("data") / "amv_forge.sqlite")) as session:
        season = Season(key="folder:Horimiya", source="folder", source_id="P:/Anime/Horimiya/S1", title="S1",
                        anilist_title="Horimiya", mood_signature="m", characters_signature="c")
        session.add(season)
        session.commit()
        assert season.id is not None
        session.add(Character(season_id=season.id, anilist_id=1, name="Kyouko Hori", role="MAIN"))
        session.add(Character(season_id=season.id, anilist_id=2, name="Izumi Miyamura", role="MAIN"))
        for number, mood, found in ((1, LOVE, {"1": 0.9, "2": 0.8}), (2, FIGHT, {"1": 0.9})):
            episode = Episode(season_id=season.id, number=number, path=f"Horimiya - 01x0{number}.mp4")
            session.add(episode)
            session.commit()
            assert episode.id is not None
            for i in range(30):
                session.add(Clip(episode_id=episode.id, start=2.0 * i, end=2.0 * i + 2.0, motion=0.5, mood=mood,
                                 quality=0.9, characters=found))
        session.commit()


def _first_song(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("1. "))


def test_music_and_suggest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synth_song: Path,
                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(music_clap, "is_installed", lambda: True)
    monkeypatch.setattr("backend.analysis.loader.ClapMusicModel", FakeClap)
    music = tmp_path / "Musik"
    music.mkdir()
    for source, name in ((make_ballad(tmp_path / "ballad.wav"), "Duo - Slow Love.flac"),
                         (synth_song, "DJ Test - Drop Song.flac")):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-c:a", "flac", str(music / name)],
                       check=True)
    config = tmp_path / "light.yaml"
    config.write_text(LIGHT, encoding="utf-8")
    _season()

    analyze = ["music", "--source", "folder", "--path", str(music), "--analyzer", "librosa", "--config", str(config)]
    assert main(analyze) == 0
    assert "2 neu analysiert" in caplog.text
    caplog.clear()
    assert main(analyze) == 0
    assert "Nichts neu berechnet" in caplog.text
    assert main(["music", "--list", "--config", str(config)]) == 0
    assert "a-Moll" in caplog.text

    # Derselbe Song noch einmal als song.flac, einzeln mit 'song' analysiert: zählt nur einmal
    copy = tmp_path / "song.flac"
    shutil.copyfile(music / "DJ Test - Drop Song.flac", copy)
    assert main(["song", str(copy), "--analyzer", "librosa", "--config", str(config)]) == 0

    assert main(["suggest", "--season", "1", "--style", "romance", "--config", str(config)]) == 0
    romance = (tmp_path / "data" / "renders" / "vorschlaege_s1_romance.txt").read_text(encoding="utf-8")
    assert _first_song(romance).startswith("1. Duo - Slow Love")
    assert "Warum: klingt romantisch" in romance
    assert f'python -m backend.cli edit --season 1 --style romance --song "{music / "Duo - Slow Love.flac"}"' in \
        romance

    assert main(["suggest", "--season", "1", "--style", "hype", "--length", "20", "--config", str(config)]) == 0
    hype = (tmp_path / "data" / "renders" / "vorschlaege_s1_hype.txt").read_text(encoding="utf-8")
    assert _first_song(hype).startswith("1. DJ Test - Drop Song")
    assert "Drop bei" in hype and "--length 20" in hype
    assert "song.flac ist dieselbe Datei wie DJ Test - Drop Song" in caplog.text and "2. song " not in hype
    assert "Achtung" not in hype  # alle Songs mit CLAP eingeordnet

    assert main(["suggest", "--season", "1", "--style", "romance", "--characters", "Hori,Miyamura",
                 "--config", str(config)]) == 0
    assert "18 von 60 Clips mit Kyouko Hori + Izumi Miyamura kommen in Frage" in caplog.text  # wie bei edit
    assert main(["suggest", "--season", "7", "--style", "romance", "--config", str(config)]) == 1


def test_suggest_says_when_clap_was_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.chdir(tmp_path)
    music = tmp_path / "Musik"
    music.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(make_ballad(tmp_path / "ballad.wav")), "-c:a", "flac",
                    str(music / "Duo - Slow Love.flac")], check=True)
    config = tmp_path / "light.yaml"
    config.write_text(LIGHT + "music_mood:\n  clap_enabled: false\n", encoding="utf-8")
    _season()
    assert main(["music", "--source", "folder", "--path", str(music), "--analyzer", "librosa",
                 "--config", str(config)]) == 0
    assert "Achtung: 1 von 1 Songs sind ohne CLAP nur nach Messwerten eingeordnet" in caplog.text
    assert main(["suggest", "--season", "1", "--style", "romance", "--config", str(config)]) == 0
    text = (tmp_path / "data" / "renders" / "vorschlaege_s1_romance.txt").read_text(encoding="utf-8")
    assert text.splitlines()[1].startswith("Achtung: 1 von 1 Songs") and "music_mood.clap_enabled" in text


def test_suggest_without_songs_explains_what_to_do(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.chdir(tmp_path)
    _season()
    assert main(["suggest", "--season", "1", "--style", "sad"]) == 1
    assert "Erst die Musik analysieren" in caplog.text
