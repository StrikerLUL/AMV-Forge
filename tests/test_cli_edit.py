"""Ganzer Ablauf über die Kommandozeile: Staffel indexieren, Song analysieren, Edit rendern."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session

from backend.analysis.video import clip_tags
from backend.cli import main
from backend.db import get_engine
from backend.db.models import Season
from tests.fake_models import FakeClip
from tests.synth_song import BAR, DROP_TIME
from tests.synth_video import BLUES, REDS, make_color_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")

# Ohne CLIP, Silero, Satz-Modell und Gesichtsdetektor: Tests laden nie große Modelle herunter
LIGHT = 'clip:\n  enabled: false\nepisode_audio:\n  vad: none\nsubtitles:\n  model: ""\nfaces:\n  enabled: false\n'


def _episode(path: Path, source: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def test_edit_cuts_the_drop_faster_than_the_verse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synth_song: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "Testanime" / "S1"
    folder.mkdir(parents=True)
    _episode(folder / "Testanime - 01x01.mkv", "testsrc2=size=160x120:rate=24:duration=30")
    _episode(folder / "Testanime - 01x02.mkv", "testsrc=size=160x120:rate=24:duration=30")
    config = tmp_path / "light.yaml"
    config.write_text(LIGHT, encoding="utf-8")

    assert main(["index", "--source", "folder", "--path", str(folder), "--no-api", "--config", str(config)]) == 0
    assert main(["song", str(synth_song), "--analyzer", "librosa", "--config", str(config)]) == 0
    out = tmp_path / "edit.mp4"
    # Start bei Takt 12: 4 Takte Verse, 4 Takte Build-up, dann der Drop
    assert main(["edit", "--season", "1", "--song", str(synth_song), "--length", "20", "--preview",
                 "--song-start", str(12 * BAR), "--seed", "1", "--analyzer", "librosa", "--out", str(out),
                 "--config", str(config)]) == 0

    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True).stdout)
    assert duration == pytest.approx(20.0, abs=0.1)

    plan = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert plan["song_start"] < DROP_TIME < plan["song_start"] + 20
    lengths: dict[str, list[float]] = {}
    for clip in plan["clips"]:
        lengths.setdefault(clip["section"], []).append(clip["end"] - clip["start"])
    verse_avg = sum(lengths["verse"]) / len(lengths["verse"])
    drop_avg = sum(lengths["drop"]) / len(lengths["drop"])
    assert drop_avg < 0.5 * verse_avg  # der Drop ist sichtbar schneller geschnitten
    assert {clip["episode"] for clip in plan["clips"]} == {1, 2}
    assert all(clip["crowd"] >= 1 for clip in plan["clips"])  # Streuung ist an


def test_edit_asks_for_index_when_motion_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    synth_song: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["edit", "--season", "1", "--song", str(synth_song)]) == 1  # leere Datenbank: klare Fehlermeldung


def test_romance_and_hype_pick_different_scenes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                synth_song: Path) -> None:
    """Folge 1: ruhige rote Szenen (für FakeClip = Romance). Folge 2: blaue Szenen mit viel Bewegung."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(clip_tags, "is_installed", lambda: True)
    monkeypatch.setattr("backend.analysis.loader.OpenClipModel", lambda cfg: FakeClip(cfg))
    folder = tmp_path / "Testanime" / "S1"
    folder.mkdir(parents=True)
    make_color_video(folder / "Testanime - 01x01.mkv", REDS, moving=False)
    make_color_video(folder / "Testanime - 01x02.mkv", BLUES, moving=True, tone=880.0)
    config = tmp_path / "test.yaml"
    # Einfarbige Testszenen sind sonst "einfarbig" bzw. "unscharf" und flögen raus
    config.write_text(LIGHT.replace("clip:\n  enabled: false", "clip:\n  enabled: true")
                      + "quality:\n  flat: 0.0\n  blurry_ratio: 0.0\n", encoding="utf-8")
    common = ["--config", str(config)]

    assert main(["index", "--source", "folder", "--path", str(folder), "--no-api", *common]) == 0
    assert main(["moods", "--season", "1", "--style", "romance", "--top", "4", *common]) == 0
    assert (tmp_path / "data" / "renders" / "stimmung_s1_romance.jpg").is_file()

    episodes: dict[str, list[int]] = {}
    for style in ("romance", "hype"):
        out = tmp_path / f"{style}.mp4"
        assert main(["edit", "--season", "1", "--song", str(synth_song), "--length", "8", "--preview",
                     "--seed", "5", "--analyzer", "librosa", "--style", style, "--out", str(out), *common]) == 0
        plan = json.loads(out.with_suffix(".plan.json").read_text(encoding="utf-8"))
        assert plan["style"] == style and all(clip["mood"] for clip in plan["clips"])
        episodes[style] = [clip["episode"] for clip in plan["clips"]]

    def share(style: str, episode: int) -> float:
        return episodes[style].count(episode) / len(episodes[style])

    # Nur 8 Szenen pro Folge und höchstens 2 hintereinander aus derselben Folge, daher "deutlich mehr"
    assert share("romance", 1) > 0.6 and share("hype", 2) > 0.6


def test_style_needs_the_phase_4_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synth_song: Path) -> None:
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "Testanime" / "S1"
    folder.mkdir(parents=True)
    _episode(folder / "Testanime - 01x01.mkv", "testsrc=size=160x120:rate=24:duration=30")
    config = tmp_path / "light.yaml"
    config.write_text(LIGHT, encoding="utf-8")
    assert main(["index", "--source", "folder", "--path", str(folder), "--no-api", "--config", str(config)]) == 0
    engine = get_engine(tmp_path / "data" / "amv_forge.sqlite")
    with Session(engine) as session:  # so sieht eine Datenbank aus Phase 3 aus
        season = session.get(Season, 1)
        assert season is not None
        season.mood_signature = None
        session.add(season)
        session.commit()
    assert main(["edit", "--season", "1", "--song", str(synth_song), "--style", "romance",
                 "--config", str(config)]) == 1
