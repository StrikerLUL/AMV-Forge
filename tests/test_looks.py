"""Farblooks als .cube-LUT (Phase 6): Tabelle, Dateien und das Ergebnis im gerenderten Bild."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from backend.config import load_settings
from backend.planner.assign import Assignment, Candidate
from backend.planner.slots import Slot
from backend.render.ffmpeg_graph import Look, RenderOptions, Shot, render_edit
from backend.render.looks import apply_look, cube_text, filter_path, resolve_look
from tests.synth_video import make_color_video

FX = load_settings().fx
GRAY = np.array([0.5, 0.5, 0.5])


def test_neutral_look_changes_nothing() -> None:
    colors = np.random.default_rng(0).random((50, 3))
    assert apply_look(colors, {}) == pytest.approx(colors)


def test_looks_do_what_their_names_say() -> None:
    warm = apply_look(GRAY, FX.looks["warm"])
    assert warm[0] > warm[2]  # mehr Rot als Blau
    cold = apply_look(GRAY, FX.looks["cold"])
    assert cold[2] > cold[0]
    red = np.array([0.8, 0.2, 0.2])
    assert np.ptp(apply_look(red, FX.looks["cold"])) < np.ptp(red) < np.ptp(apply_look(red, FX.looks["punchy"]))
    with pytest.raises(ValueError, match="hue"):
        apply_look(GRAY, {"hue": 1.0})


def test_cube_file_format() -> None:
    lines = cube_text({}, 3, "neutral").splitlines()
    assert lines[:4] == ['TITLE "neutral"', "LUT_3D_SIZE 3", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    table = [tuple(float(v) for v in line.split()) for line in lines[4:]]
    assert len(table) == 27
    assert table[0] == (0, 0, 0) and table[1] == (0.5, 0, 0) and table[3] == (0, 0.5, 0)  # Rot zählt am schnellsten
    assert table[-1] == (1, 1, 1)


def test_resolve_look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    fx = replace(FX, cache_dir=tmp_path / "looks")
    for off in (None, "", "none", "aus"):
        assert resolve_look(off, fx) is None
    warm = resolve_look("warm", fx)
    assert warm is not None and warm.parent == fx.cache_dir and warm.read_text(encoding="utf-8").startswith("TITLE")
    assert resolve_look("warm", fx) == warm  # gleicher Look, gleiche Datei

    own = tmp_path / "Meine LUTs" / "Kino Look.cube"
    own.parent.mkdir()
    own.write_text(cube_text({"saturation": 0.5}, 5, "kino"), encoding="utf-8")
    copied = resolve_look(str(own), fx)
    assert copied is not None and copied.parent == fx.cache_dir and " " not in copied.name
    assert copied.read_bytes() == own.read_bytes()
    with pytest.raises(ValueError, match="warm"):
        resolve_look("pastell", fx)


def test_filter_path_works_with_windows_drives() -> None:
    assert filter_path(Path("C:/Users/x/looks/warm.cube")) == "'C\\:/Users/x/looks/warm.cube'"
    with pytest.raises(ValueError):
        filter_path(Path("data/it's.cube"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_look_and_vignette_in_the_render(tmp_path: Path) -> None:
    video = make_color_video(tmp_path / "grau.mkv", ["0x808080"], moving=False, seconds=1.0)
    a = Assignment(Slot(0.0, 0.5), 0.2, video, candidate=Candidate(video, 0.0, 1.0))
    base = RenderOptions(width=90, height=160, fps=24, crf=18, preset="ultrafast", audio_bitrate="128k",
                         with_music=False, fx=FX)

    def middle_frame(look: Look, name: str) -> np.ndarray:
        out = render_edit([Shot(a)], tmp_path / "song.wav", 0.0, tmp_path / name, replace(base, look=look))
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 160, 90, 3)[6].astype(int)

    plain = middle_frame(Look(), "neutral.mp4")
    warm = middle_frame(Look(resolve_look("warm", replace(FX, cache_dir=tmp_path / "looks"))), "warm.mp4")
    assert abs(plain[80, 45, 0] - plain[80, 45, 2]) < 6  # grau bleibt grau
    assert warm[80, 45, 0] - warm[80, 45, 2] > 8  # warm: mehr Rot als Blau ...
    expected = apply_look(plain[80, 45] / 255, FX.looks["warm"]) * 255  # ... genau wie in der Tabelle berechnet
    assert np.abs(warm[80, 45] - expected).max() < 6
    dark = middle_frame(Look(vignette=0.6), "vignette.mp4")
    assert dark[2, 2].mean() < dark[80, 45].mean() - 15  # Ecken dunkler als die Mitte
