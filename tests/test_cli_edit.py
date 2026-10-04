"""Ganzer Ablauf über die Kommandozeile: Staffel indexieren, Song analysieren, Edit rendern."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.cli import main
from tests.synth_song import BAR, DROP_TIME

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


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

    assert main(["index", "--source", "folder", "--path", str(folder), "--no-api"]) == 0
    assert main(["song", str(synth_song), "--analyzer", "librosa"]) == 0
    out = tmp_path / "edit.mp4"
    # Start bei Takt 12: 4 Takte Verse, 4 Takte Build-up, dann der Drop
    assert main(["edit", "--season", "1", "--song", str(synth_song), "--length", "20", "--preview",
                 "--song-start", str(12 * BAR), "--seed", "1", "--analyzer", "librosa", "--out", str(out)]) == 0

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


def test_edit_asks_for_index_when_motion_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    synth_song: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["edit", "--season", "1", "--song", str(synth_song)]) == 1  # leere Datenbank: klare Fehlermeldung
