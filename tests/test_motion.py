"""Bewegungsmessung mit Optical Flow auf einem Testvideo mit bekanntem Bewegungsmoment."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from backend.analysis.video import motion as motion_module
from backend.analysis.video.motion import MotionCurve, clip_motion, measure_motion
from backend.config import load_settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def _moving_box_video(path: Path) -> Path:
    """12 s graues Bild, nur zwischen 4,0 und 4,6 s saust ein weißes Kästchen nach rechts."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=24:duration=12",
         "-f", "lavfi", "-i", "color=c=white:size=40x40:rate=24:duration=12",
         "-filter_complex", "[0][1]overlay=x='if(between(t,4,4.6),40+(t-4)*400,if(gt(t,4.6),280,40))':y=60",
         "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True,
    )
    return path


@needs_ffmpeg
def test_peak_is_where_the_box_moves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _moving_box_video(tmp_path / "box.mp4")
    cfg = replace(load_settings().motion, cache_dir=tmp_path / "motion")
    curve = measure_motion(video, cfg, 12.0)
    assert len(curve.values) == pytest.approx(12 * cfg.fps, abs=2)

    whole = clip_motion(curve, 0.0, 12.0, cfg.edge_seconds, cfg.peak_smooth)
    assert whole is not None and 4.0 <= whole.peak <= 4.7
    calm = clip_motion(curve, 6.0, 12.0, cfg.edge_seconds, cfg.peak_smooth)
    assert calm is not None and calm.motion < 0.01 < whole.motion

    # Zweiter Aufruf liest nur den Cache, ffmpeg wird nicht mehr gestartet
    def no_ffmpeg(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ffmpeg sollte nicht laufen")

    monkeypatch.setattr(motion_module.subprocess, "Popen", no_ffmpeg)
    again = measure_motion(video, cfg, 12.0)
    assert np.array_equal(again.values, curve.values)


def test_motion_at_the_cut_is_ignored() -> None:
    values = np.zeros(120, dtype=np.float32)
    values[24] = 50.0  # Schnitt bei 2,0 s sieht aus wie riesige Bewegung
    values[40] = 3.0  # echte Bewegung bei 3,33 s
    curve = MotionCurve(12.0, values)
    stats = clip_motion(curve, 2.0, 5.0, edge_seconds=0.2, smooth=1)
    assert stats is not None and stats.peak == pytest.approx(40 / 12, abs=1e-3)


def test_very_short_clip_still_gets_a_value() -> None:
    curve = MotionCurve(12.0, np.arange(120, dtype=np.float32))
    stats = clip_motion(curve, 3.0, 3.3, edge_seconds=0.2, smooth=3)
    assert stats is not None and 3.0 <= stats.peak <= 3.3
    assert clip_motion(curve, 20.0, 21.0, 0.2, 3) is None  # hinter dem Ende der Kurve
