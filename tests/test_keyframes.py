"""Standbilder pro Clip: Zeitpunkte, ein ffmpeg-Durchlauf, Vorschaubilder auch mit Umlauten im Pfad."""

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from backend.analysis.video.keyframes import iter_keyframes, keyframe_times, load_jpeg, plan_frames, save_jpeg
from backend.config import load_settings
from tests.synth_video import REDS, make_color_video

CFG = load_settings().keyframes


def test_longer_clips_get_more_frames_but_not_at_the_cut() -> None:
    assert len(keyframe_times(0.0, 1.0, CFG)) == 1
    assert len(keyframe_times(0.0, 2.0, CFG)) == 2
    times = keyframe_times(10.0, 30.0, CFG)
    assert len(times) == CFG.max_per_clip
    assert all(10.0 + CFG.edge_seconds < t < 30.0 - CFG.edge_seconds for t in times)


def test_very_short_clip_uses_its_middle() -> None:
    assert keyframe_times(5.0, 5.3, CFG) == [pytest.approx(5.15)]


def test_each_clip_has_exactly_one_thumbnail() -> None:
    plan = plan_frames([(0.0, 3.0), (3.0, 4.0), (4.0, 9.0)], CFG)
    thumbs = [clip for entries in plan.values() for clip, thumb in entries if thumb]
    assert sorted(thumbs) == [0, 1, 2]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_frames_come_from_the_right_clip(tmp_path: Path) -> None:
    video = make_color_video(tmp_path / "farben.mkv", ["0xff0000", "0x0000ff", "0x00ff00"], moving=False)
    clips = [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]
    cfg = replace(CFG, height=72)
    frames = list(iter_keyframes(video, clips, cfg))
    assert {f.clip_index for f in frames} == {0, 1, 2}
    for f in frames:
        assert f.image.shape == (72, 128, 3)  # 16:9 bei fester Höhe
        r, g, b = f.image.reshape(-1, 3).mean(axis=0)
        expected = ["r", "b", "g"][f.clip_index]
        assert {"r": r, "g": g, "b": b}[expected] > 150


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_decoding_stops_after_the_last_needed_frame(tmp_path: Path) -> None:
    video = make_color_video(tmp_path / "lang.mkv", REDS, moving=False)
    frames = list(iter_keyframes(video, [(0.0, 3.0)], CFG))  # nur der erste Clip von 24 s
    assert frames and all(f.time < 3.0 for f in frames)


def test_jpeg_round_trip_with_umlauts(tmp_path: Path) -> None:
    image = np.zeros((36, 64, 3), dtype=np.uint8)
    image[:, :, 0] = 200
    path = tmp_path / "Dieser Sommer ist sehr heiß" / "1.jpg"
    save_jpeg(image, path, 90)
    back = load_jpeg(path)
    assert back is not None and back.shape == image.shape
    assert abs(int(back[..., 0].mean()) - 200) < 5
    assert load_jpeg(tmp_path / "fehlt.jpg") is None
