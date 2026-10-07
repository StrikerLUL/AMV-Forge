"""Übergänge und Effekte aus den Stil-Profilen (Phase 6): Planung und fertig gerenderte Bilder."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from backend.analysis.video.faces import ClipFace
from backend.config import load_settings
from backend.config.styles import load_style
from backend.planner.assign import Assignment, Candidate
from backend.planner.slots import Slot, Timing
from backend.render.effects import (
    CUT,
    NO_FX,
    ClipFx,
    TransitionRules,
    Transition,
    fade_filters,
    parse_effects,
    parse_transitions,
    plan_effects,
    prepare_slots,
    zoom_filter,
)
from backend.render.ffmpeg_graph import Look, RenderOptions, Shot, frame_counts, render_edit
from backend.render.looks import resolve_look
from backend.render.reframe import Framing
from tests.synth_video import make_color_video

SETTINGS = load_settings()
FX = SETTINGS.fx
DEFAULTS = SETTINGS.planner.weights
FPS = 24
VIDEO = Path("folge.mkv")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


# ---------------------------------------------------------------- YAML


@pytest.mark.parametrize("raw, message", [
    ({"default": "wipe"}, "wipe"),
    ({"lenght": 1}, "lenght"),
    ({"beats": 0}, "beats"),
])
def test_bad_transitions_are_explained(raw: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_transitions(raw, "test.yaml")


@pytest.mark.parametrize("raw, message", [
    ({"blur": 1}, "blur"),
    ({"shake": {"zones": ["refrain"]}}, "refrain"),
    ({"shake": {"strenght": 0.1}}, "strenght"),
    ({"zoom_punch": {"at": "bars"}}, "zoom_punch.at"),
    ({"slowmo": {"speed": 10}}, "slowmo.speed"),
    ({"speed_ramp": {"split": 1.5}}, "speed_ramp"),
])
def test_bad_effects_are_explained(raw: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_effects(raw, "test.yaml")


def test_effects_with_defaults() -> None:
    rules = parse_effects({"slowmo": True, "shake": None, "look": "warm"}, "test.yaml")
    assert rules.slowmo is not None and rules.slowmo.speed == 0.8 and rules.slowmo.zones == ()
    assert rules.shake is None and rules.look == "warm"


def test_transition_priority() -> None:
    rules = TransitionRules("cut", downbeat="flash", section="whip", drop="dip_white")
    assert rules.pick(drop=True, section=True, downbeat=True) == "dip_white"
    assert rules.pick(drop=False, section=True, downbeat=True) == "whip"
    assert rules.pick(drop=False, section=False, downbeat=True) == "flash"
    assert rules.pick(drop=False, section=False, downbeat=False) == "cut"
    assert TransitionRules("crossfade").pick(drop=True, section=True, downbeat=True) == "crossfade"


# ---------------------------------------------------------------- vor der Clip-Wahl


def test_romance_slows_long_clips_down() -> None:
    romance = load_style("romance", DEFAULTS)
    long, short = prepare_slots([Slot(0.0, 2.0), Slot(2.0, 2.5)], romance.effects)
    assert long.timing == Timing(((1.0, 0.8),)) and long.source_duration == pytest.approx(1.6)
    assert short.timing.is_normal


def test_hype_ramps_outside_the_drop() -> None:
    hype = load_style("hype", DEFAULTS)
    verse, drop, tiny = prepare_slots([Slot(0.0, 1.0, "verse"), Slot(1.0, 2.0, "drop"), Slot(2.0, 2.3, "verse")],
                                      hype.effects)
    assert verse.timing == Timing(((0.4, 0.5), (1.0, 1.6)))
    assert verse.source_duration == pytest.approx(0.4 * 0.5 + 0.6 * 1.6)
    assert drop.timing.is_normal and tiny.timing.is_normal


def test_freeze_prefers_a_late_beat() -> None:
    funny = load_style("funny", DEFAULTS)
    (slot,) = prepare_slots([Slot(0.0, 2.0, hits=(0.0, 0.5, 1.0, 1.5))], funny.effects)
    assert slot.hits == (1.0, 1.5, 0.0, 0.5)


# ---------------------------------------------------------------- nach der Clip-Wahl


def _assign(*slots: Slot) -> list[Assignment]:
    return [Assignment(s, 10.0 * i, VIDEO) for i, s in enumerate(slots)]


def test_romance_crossfades_and_dips_at_new_sections() -> None:
    romance = load_style("romance", DEFAULTS)
    assignments = _assign(Slot(0, 2, "verse"), Slot(2, 4, "verse"), Slot(4, 6, "chorus"), Slot(6, 6.25, "chorus"),
                          Slot(6.25, 8, "drop"))
    counts = frame_counts(assignments, FPS)
    fxs = plan_effects(assignments, counts, romance.transitions, romance.effects, [0, 2, 4, 6], 0.5, FPS, FX)
    # 0,75 Beats bei 120 BPM = 9 Frames, also 4 Frames auf jeder Seite des Schnitts
    assert [fx.into for fx in fxs] == [CUT, Transition("crossfade", 4), Transition("dip_white", 4),
                                       Transition("crossfade", 1), Transition("dip_white", 1)]
    assert fxs[0].out == fxs[1].into and fxs[-1].out == CUT
    assert fxs[0].push_in == 0.05 and fxs[3].push_in == 0.0  # nur in langen Clips


def test_hype_flashes_on_the_one_and_whips_into_new_sections() -> None:
    hype = load_style("hype", DEFAULTS)
    slots = prepare_slots([Slot(0, 1, "verse"), Slot(1, 2, "verse"), Slot(2, 3, "verse"), Slot(3, 4, "buildup"),
                           Slot(4, 4.25, "drop"), Slot(4.25, 4.5, "drop")], hype.effects)
    assignments = _assign(*slots)
    fxs = plan_effects(assignments, frame_counts(assignments, FPS), hype.transitions, hype.effects, [0, 2, 4, 6],
                       0.5, FPS, FX)
    assert [fx.into.kind for fx in fxs] == ["cut", "cut", "flash", "whip", "flash", "cut"]
    assert fxs[2].into.frames == 4 and fxs[3].into.frames == 2
    assert fxs[4].into.frames == 3  # höchstens die Hälfte des 6 Frames kurzen Drop-Clips
    assert fxs[4].punches == (0.0,) and fxs[4].shake == 0.015
    assert fxs[0].speed == "0.5x>1.6x" and fxs[4].speed == ""
    assert "zoom_punch x1" in fxs[4].labels() and "shake" in fxs[4].labels()


def test_freeze_on_the_peak() -> None:
    funny = load_style("funny", DEFAULTS)
    (slot,) = prepare_slots([Slot(0.0, 2.0, "verse", hits=(0.0, 0.5, 1.0, 1.5))], funny.effects)
    cand = Candidate(VIDEO, 10.0, 14.0, motion=1.0, peak=12.0)
    a = Assignment(slot, 11.0, VIDEO, aligned=True, candidate=cand)  # Peak 1 s nach dem Schnitt
    (fx,) = plan_effects([a], [48], funny.transitions, funny.effects, [], 0.5, FPS, FX)
    assert fx.freeze_at == pytest.approx(1.0) and fx.freeze_zoom == 0.25
    early = replace(a, source_start=11.9)  # Peak direkt am Schnitt: nichts zum Einfrieren
    assert plan_effects([early], [48], funny.transitions, funny.effects, [], 0.5, FPS, FX)[0].freeze_at is None


def test_filters() -> None:
    assert zoom_filter(NO_FX, (0.5, 0.5), 0.0, 1.0, 90, 160, FPS, FX) is None
    zoom = zoom_filter(ClipFx(punches=(0.0,), punch_zoom=0.1, push_in=0.05), (0.3, 0.4), 0.0, 1.0, 90, 160, FPS, FX)
    assert zoom is not None and zoom.startswith("zoompan=") and "s=90x160" in zoom
    flash = ClipFx(into=Transition("flash", 4))
    assert fade_filters(flash, 0, 24, 24, FPS) == ["fade=t=in:st=0:d=0.16667:color=white"]
    assert fade_filters(flash, 6, 24, 24, FPS) == []  # Stück ohne den Schnitt
    dip = ClipFx(out=Transition("dip_white", 4))
    assert fade_filters(dip, 0, 24, 24, FPS) == ["fade=t=out:st=0.83333:d=0.16667:color=white"]


# ---------------------------------------------------------------- gerendert

OPTS = RenderOptions(width=90, height=160, fps=FPS, crf=18, preset="ultrafast", audio_bitrate="128k",
                     with_music=False, fx=FX)
RED, BLUE = "0xd02020", "0x2020d0"


def _frames(video: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, OPTS.height, OPTS.width, 3).astype(int)


def _color(frame: np.ndarray) -> tuple[int, int, int]:
    r, g, b = frame[60:100, 25:65].reshape(-1, 3).mean(axis=0)
    return int(r), int(g), int(b)


@pytest.fixture(scope="module")
def red_blue(tmp_path_factory: pytest.TempPathFactory) -> list[Assignment]:
    """Zwei Clips: 1 s Rot, dann 1 s Blau, der Schnitt liegt bei Frame 24."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg fehlt")
    video = make_color_video(tmp_path_factory.mktemp("fx") / "folge.mkv", [RED, BLUE], moving=False, seconds=2.0)
    return [Assignment(Slot(0.0, 1.0), 0.5, video, candidate=Candidate(video, 0.0, 2.0)),
            Assignment(Slot(1.0, 2.0), 2.5, video, candidate=Candidate(video, 2.0, 4.0))]


def _render(tmp_path: Path, shots: list[Shot], name: str = "edit.mp4", opts: RenderOptions = OPTS) -> np.ndarray:
    return _frames(render_edit(shots, tmp_path / "song.wav", 0.0, tmp_path / name, opts))


def test_crossfade_is_centered_on_the_beat(tmp_path: Path, red_blue: list[Assignment]) -> None:
    fade = Transition("crossfade", 4)
    frames = _render(tmp_path, [Shot(red_blue[0], fx=ClipFx(out=fade)), Shot(red_blue[1], fx=ClipFx(into=fade))])
    assert len(frames) == 48  # Länge Frame für Frame wie ohne Übergang
    r, _, b = _color(frames[18])
    assert r > 170 and b < 70
    r, _, b = _color(frames[24])  # genau auf dem Schnitt: halb und halb
    assert 70 < r < 160 and 70 < b < 160
    r, _, b = _color(frames[30])
    assert b > 170 and r < 70


def test_flash_and_dip_to_white(tmp_path: Path, red_blue: list[Assignment]) -> None:
    flash = _render(tmp_path, [Shot(red_blue[0]), Shot(red_blue[1], fx=ClipFx(into=Transition("flash", 4)))])
    assert len(flash) == 48
    assert min(_color(flash[24])) > 230 and _color(flash[23])[0] > 170 > _color(flash[23])[2]
    assert _color(flash[30])[2] > 170

    dip = Transition("dip_white", 4)
    frames = _render(tmp_path, [Shot(red_blue[0], fx=ClipFx(out=dip)), Shot(red_blue[1], fx=ClipFx(into=dip))],
                     "dip.mp4")
    assert len(frames) == 48
    assert min(_color(frames[23])) > 170 and min(_color(frames[24])) > 230  # erst ins Weiß, dann heraus
    assert _color(frames[18])[2] < 70 and _color(frames[30])[0] < 70


def test_freeze_holds_the_frame(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg fehlt")
    video = make_color_video(tmp_path / "bewegt.mkv", ["0x303030"], moving=True, seconds=3.0)
    a = Assignment(Slot(0.0, 1.0), 0.5, video, candidate=Candidate(video, 0.0, 3.0))
    frames = _render(tmp_path, [Shot(a, fx=ClipFx(freeze_at=0.5))])
    assert len(frames) == 24
    assert any(np.abs(frames[k] - frames[0]).max() > 100 for k in range(1, 12))  # vorher bewegt sich das Quadrat
    assert all(np.abs(frames[k] - frames[14]).max() < 12 for k in range(14, 24))  # danach steht das Bild


def test_all_effects_together_keep_the_length(tmp_path: Path) -> None:
    """Alles auf einmal (Tempo, Reframe mit Schwenk, Zooms, Shake, Freeze, Look, Glow, Vignette, Übergänge):
    ffmpeg muss jeden Filter annehmen, und die Länge muss Frame für Frame stimmen."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg fehlt")
    video = make_color_video(tmp_path / "folge.mkv", [RED, BLUE, "0x20a020"], moving=True, seconds=3.0,
                             size="320x180")
    faces = (ClipFace(1.0, (0.1, 0.2, 0.25, 0.6)), ClipFace(1.0, (0.7, 0.2, 0.85, 0.6)))
    slots = [Slot(0.0, 1.0, timing=Timing(((1.0, 0.8),))), Slot(1.0, 2.0, "drop", timing=Timing(((0.4, 0.5), (1.0, 1.6)))),
             Slot(2.0, 3.0)]
    cands = [Candidate(video, 3.0 * i, 3.0 * i + 3.0, faces=faces) for i in range(3)]
    assignments = [Assignment(s, c.start + 0.5, video, candidate=c) for s, c in zip(slots, cands)]
    pan = Framing("pan", 0.316, ((0.2, 0.2), (0.8, 0.8)), focus=(0.5, 0.4))
    face = Framing("face", 0.316, ((0.0, 0.7),), focus=(0.5, 0.4))
    fade, whip = Transition("crossfade", 4), Transition("whip", 2)
    shots = [Shot(assignments[0], pan, ClipFx(out=fade, push_in=0.05)),
             Shot(assignments[1], face, ClipFx(into=fade, out=whip, shake=0.02, punches=(0.0, 0.5), punch_zoom=0.12)),
             Shot(assignments[2], Framing("fit", 1.0), ClipFx(into=whip, freeze_at=0.5, freeze_zoom=0.2))]
    fx = replace(FX, cache_dir=tmp_path / "looks")
    opts = replace(OPTS, look=Look(resolve_look("warm", fx), glow=0.3, vignette=0.4))
    assert len(_render(tmp_path, shots, opts=opts)) == 72
