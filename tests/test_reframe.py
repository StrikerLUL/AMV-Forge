"""Smart Reframe 16:9 -> 9:16 (Phase 6): Der Ausschnitt folgt Gesichtern bzw. Bewegung, nicht stumpf der Mitte."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from backend.analysis.video.faces import ClipFace
from backend.config import load_settings
from backend.planner.assign import Assignment, Candidate
from backend.planner.slots import Slot
from backend.render.ffmpeg_graph import RenderOptions, Shot, render_edit
from backend.render.reframe import (
    Framing,
    centered,
    frame_faces,
    frame_shot,
    motion_center,
    reframe_sheet,
    summarize,
    window_width,
)
from tests.synth_video import make_face_video

SETTINGS = load_settings()
CFG = SETTINGS.reframe
ASPECT = 16 / 9
W = window_width(ASPECT)
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def face(x0: float, x1: float, t: float = 10.5, char: int | None = None, y0: float = 0.2, y1: float = 0.6,
         p: float = 0.9) -> ClipFace:
    return ClipFace(t, (x0, y0, x1, y1), char, p)


def frame(faces: list[ClipFace], cfg=CFG, wanted: frozenset[int] = frozenset(), duration: float = 2.0,
          window: tuple[float, float] = (10.0, 12.0)) -> Framing | None:
    return frame_faces(faces, window, lambda t: t - window[0], duration, ASPECT, wanted, cfg)


def test_window_is_a_third_of_a_16_9_frame() -> None:
    assert W == pytest.approx(0.3164, abs=1e-3)
    assert window_width(9 / 16) == 1.0  # Hochkant-Quelle: nichts zu schneiden
    assert frame_faces([face(0.7, 0.8)], (10, 12), lambda t: t, 2.0, 9 / 16, frozenset(), CFG) is None
    assert frame([]) is None


def test_single_face_on_the_right_is_in_the_middle_of_the_window() -> None:
    f = frame([face(0.7, 0.85)])
    assert f is not None and f.mode == "face"
    assert f.center_at(0.0) == pytest.approx(0.775)
    assert (f.faces, f.inside, f.main_inside) == (1, 1, True)
    assert f.center_inside == 0  # aus der Mitte geschnitten wäre das Gesicht abgeschnitten
    assert f.focus[0] == pytest.approx(0.5)  # Zooms gehen auf das Gesicht, das jetzt in der Mitte steht


def test_face_at_the_edge_keeps_the_window_inside_the_image() -> None:
    f = frame([face(0.9, 0.98)])
    assert f is not None and f.center_at(0.0) == pytest.approx(1 - W / 2)
    assert f.inside == 1


def test_close_pair_fits_together() -> None:
    f = frame([face(0.3, 0.42), face(0.48, 0.6)])
    assert f is not None and f.mode == "face"
    assert f.center_at(0.0) == pytest.approx(0.45)
    assert (f.faces, f.inside) == (2, 2)


def _wide_pair() -> list[ClipFace]:
    # Links ein großes Gesicht (das wichtigste), rechts ein etwas kleineres, zusammen viel zu breit
    return [face(0.1, 0.25, y0=0.2, y1=0.7), face(0.7, 0.85, y0=0.2, y1=0.6)]


def test_wide_pair_pans_from_the_main_face_to_the_other() -> None:
    f = frame(_wide_pair(), duration=2.0)
    assert f is not None and f.mode == "pan"
    assert [t for t, _ in f.keys] == pytest.approx([0.4, 1.6])
    assert f.center_at(0.0) == pytest.approx(0.175) and f.center_at(2.0) == pytest.approx(0.775)
    assert 0.175 < f.center_at(1.0) < 0.775
    assert (f.faces, f.inside, f.main_inside) == (2, 2, True)
    assert f.focus == pytest.approx((0.5, 0.45))


def test_wide_pair_in_a_short_clip_stays_on_the_main_face() -> None:
    f = frame(_wide_pair(), duration=1.0)
    assert f is not None and f.mode == "main"
    assert f.center_at(0.5) == pytest.approx(0.175)
    assert (f.inside, f.main_inside) == (1, True)


def test_wide_pair_other_settings() -> None:
    fit = frame(_wide_pair(), cfg=replace(CFG, too_wide="fit"))
    assert fit is not None and fit.mode == "fit" and fit.width == 1.0 and not fit.portrait
    assert fit.inside == 2
    main = frame(_wide_pair(), cfg=replace(CFG, too_wide="main"))
    assert main is not None and main.mode == "main"


def test_window_follows_faces_that_move() -> None:
    f = frame([face(0.1, 0.25, t=10.2), face(0.7, 0.85, t=11.6)])
    assert f is not None and f.mode == "face"
    assert [v for key in f.keys for v in key] == pytest.approx([0.2, 0.175, 1.6, 0.775])
    assert f.inside == 2  # jedes Gesicht ist zu seiner Zeit ganz im Bild


def test_small_background_faces_do_not_count() -> None:
    f = frame([face(0.6, 0.8, y0=0.1, y1=0.7), face(0.02, 0.06, y0=0.1, y1=0.2)])
    assert f is not None and f.mode == "face" and f.faces == 1
    assert f.center_at(0.0) == pytest.approx(0.7)


def test_wanted_character_wins_over_a_bigger_face() -> None:
    faces = [face(0.1, 0.3, char=5, y0=0.1, y1=0.7), face(0.7, 0.8, char=7, y0=0.2, y1=0.35)]
    assert frame(faces).center_at(0.0) == pytest.approx(0.2)  # type: ignore[union-attr]
    f = frame(faces, wanted=frozenset({7}))
    assert f is not None and f.center_at(0.0) == pytest.approx(0.75) and f.faces == 1


def test_faces_far_from_the_window_are_only_a_fallback() -> None:
    near_and_far = [face(0.7, 0.8, t=10.5), face(0.1, 0.2, t=30.0)]
    assert frame(near_and_far).center_at(0.0) == pytest.approx(0.75)  # type: ignore[union-attr]
    only_far = frame([face(0.1, 0.2, t=30.0)])
    assert only_far is not None and only_far.center_at(0.0) == pytest.approx(W / 2)


def _eval(expr: str, t: float) -> float:
    """Wertet einen ffmpeg-Ausdruck (if, lt, clip) in Python aus."""
    code = expr.replace("clip(", "_clip(").replace("if(", "_if(").replace("lt(", "_lt(")
    env = {"t": t, "_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: 1 if a < b else 0,
           "_clip": lambda v, lo, hi: min(max(v, lo), hi)}
    return float(eval(code, {"__builtins__": {}}, env))


def test_ffmpeg_expression_matches_python() -> None:
    f = frame(_wide_pair(), duration=2.0)
    assert f is not None
    for offset in (0.0, -0.2, 0.5):  # das Stück beginnt bei offset im Slot (negativ bei Überblendungen)
        for t in np.linspace(0.0, 2.2, 23):
            assert _eval(f.center_expr(offset), float(t)) == pytest.approx(f.center_at(float(t) + offset), abs=1e-3)


def test_summary_compares_with_the_old_center_crop() -> None:
    framings = [frame([face(0.7, 0.85)]), frame(_wide_pair()), centered(ASPECT)]
    stats = summarize([f for f in framings if f is not None])
    assert stats.modes == {"face": 1, "pan": 1, "center": 1}
    assert (stats.faces, stats.inside, stats.center_inside) == (3, 3, 0)
    assert (stats.clips_with_faces, stats.main_inside) == (2, 2)


def test_frame_shot_falls_back_to_the_center() -> None:
    slot = Slot(0.0, 2.0)
    plain = Assignment(slot, 10.0)
    assert frame_shot(plain, ASPECT, frozenset(), CFG, SETTINGS.motion).mode == "center"
    cand = Candidate(Path("folge.mkv"), 9.0, 13.0, faces=(face(0.7, 0.85),))
    with_face = Assignment(slot, 10.0, candidate=cand)
    assert frame_shot(with_face, ASPECT, frozenset(), CFG, SETTINGS.motion).mode == "face"
    assert frame_shot(with_face, ASPECT, frozenset(), replace(CFG, mode="center"), SETTINGS.motion).mode == "center"


def _square_video(path: Path, moving: bool) -> Path:
    square = "overlay=x=260:y='mod(t*240,150)'" if moving else "overlay=x=260:y=60"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x404040:s=320x180:r=24:d=2",
         "-f", "lavfi", "-i", "color=c=white:s=30x30:r=24:d=2", "-filter_complex", f"[0:v][1:v]{square}[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True,
    )
    return path


@needs_ffmpeg
def test_motion_center_finds_the_moving_part(tmp_path: Path) -> None:
    moving = _square_video(tmp_path / "moving.mp4", moving=True)
    x = motion_center(moving, 0.2, 1.8, SETTINGS.motion, CFG.motion_min)
    assert x is not None and x == pytest.approx(275 / 320, abs=0.06)
    still = _square_video(tmp_path / "still.mp4", moving=False)
    assert motion_center(still, 0.2, 1.8, SETTINGS.motion, CFG.motion_min) is None

    a = Assignment(Slot(0.0, 1.5), 0.2, video=moving, candidate=Candidate(moving, 0.0, 2.0))
    f = frame_shot(a, ASPECT, frozenset(), CFG, SETTINGS.motion)
    assert f.mode == "motion" and f.center_at(0.0) == pytest.approx(1 - W / 2, abs=0.06)


def _frame_at(video: Path, index: int, width: int, height: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 3)
    return frames[index]


@needs_ffmpeg
def test_render_keeps_the_face_in_the_9_16_frame(tmp_path: Path) -> None:
    """Hori links (rot), Miyamura rechts (blau). Mit --characters Miyamura muss das Blau in die Bildmitte."""
    video = make_face_video(tmp_path / "folge.mkv", [["hori", "miyamura"]], seconds=2.0)
    faces = (ClipFace(1.0, (0.125, 0.25, 0.375, 0.694), 1, 0.9), ClipFace(1.0, (0.625, 0.25, 0.875, 0.694), 2, 0.9))
    a = Assignment(Slot(0.0, 1.0), 0.5, video=video, candidate=Candidate(video, 0.0, 2.0, faces=faces))
    framing = frame_shot(a, ASPECT, frozenset({2}), CFG, SETTINGS.motion)
    assert framing.mode == "face" and framing.main_inside

    opts = RenderOptions(width=90, height=160, fps=24, crf=18, preset="ultrafast", audio_bitrate="128k",
                         with_music=False)
    smart = render_edit([Shot(a, framing)], tmp_path / "song.wav", 0.0, tmp_path / "smart.mp4", opts)
    middle = render_edit([Shot(a, centered(ASPECT))], tmp_path / "song.wav", 0.0, tmp_path / "mitte.mp4", opts)
    r, g, b = _frame_at(smart, 12, 90, 160)[80, 45].astype(int)
    assert b > 150 and r < 100  # Miyamura (blau) mitten im Bild
    assert _frame_at(middle, 12, 90, 160)[80, 45].min() > 200  # aus der Mitte: nur grauer Hintergrund

    sheet = reframe_sheet([a, a], [framing, centered(ASPECT)], tmp_path / "reframe.jpg")
    assert sheet.is_file()
