"""Untertitel finden (neben der Folge oder in der MKV), Schilder rausfiltern, Zeilen den Clips zuordnen."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from backend.analysis.audio.subtitles import (
    SubtitleLine, average_tags, classify_embeddings, find_sidecar, find_subtitles, lines_in, load_dialog_prompts,
    load_lines,
)
from backend.config import load_settings

CFG = load_settings().subtitles

ASS = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1
Style: Signs,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\i1}Ich liebe dich,{\\i0}\\NMiyamura.
Dialogue: 0,0:00:02.00,0:00:04.00,Signs,,0,0,0,,Katakuri-Konditorei
Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\pos(100,50)}Klasse 3-1
Comment: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,Notiz vom Übersetzer
Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,♪ Liedtext ♪
Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,Bis morgen!
"""


def test_ass_without_signs_songs_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "Folge.ass"
    path.write_text(ASS, encoding="utf-8")
    lines = load_lines(path, CFG.skip_pattern)
    assert [line.text for line in lines] == ["Ich liebe dich, Miyamura.", "Bis morgen!"]
    assert lines[0].start == 1.0 and lines[0].end == 3.0


def test_old_srt_in_windows_encoding(tmp_path: Path) -> None:
    path = tmp_path / "Folge.srt"
    path.write_bytes("1\r\n00:00:01,000 --> 00:00:02,000\r\nSchön, dich zu sehen.\r\n\r\n2\r\n00:00:03,000 --> "
                     "00:00:04,000\r\n[Musik]\r\n".encode("cp1252"))
    assert [line.text for line in load_lines(path, CFG.skip_pattern)] == ["Schön, dich zu sehen."]


def test_sidecar_prefers_german_and_skips_forced(tmp_path: Path) -> None:
    video = tmp_path / "Horimiya - 01x01.mp4"
    video.write_bytes(b"")
    for name in ("Horimiya - 01x01.en.srt", "Horimiya - 01x01.de.forced.ass", "Horimiya - 01x01.de.srt",
                 "Horimiya - 01x010.de.ass"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert find_sidecar(video, CFG) == tmp_path / "Horimiya - 01x01.de.srt"
    (tmp_path / "Horimiya - 01x01.de.srt").unlink()
    assert find_sidecar(video, CFG) == tmp_path / "Horimiya - 01x01.en.srt"


def test_lines_are_matched_to_clips() -> None:
    lines = [SubtitleLine(1.0, 3.0, "a"), SubtitleLine(3.9, 6.0, "b"), SubtitleLine(8.0, 9.0, "c")]
    assert [line.text for line, _ in lines_in(lines, 0.0, 4.0, 0.2)] == ["a"]  # "b" ragt nur 0,1 s hinein
    assert [line.text for line, _ in lines_in(lines, 2.5, 8.5, 0.2)] == ["a", "b", "c"]
    assert average_tags([{"romance": 1.0}, {"romance": 0.0}], [3.0, 1.0]) == {"romance": 0.75}
    assert average_tags([], []) is None


def test_dialog_prompts_and_classification() -> None:
    texts, groups, temperature = load_dialog_prompts(CFG.prompts)
    assert {"romance", "action", "sad", "funny", "neutral"} <= set(groups) and len(texts) == len(groups)
    examples = np.eye(3, dtype=np.float32)
    lines = np.array([[0.9, 0.1, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
    probs = classify_embeddings(lines, examples, ["romance", "funny", "neutral"], temperature)
    assert max(probs[0], key=probs[0].get) == "romance"
    assert max(probs[1], key=probs[1].get) == "neutral"
    assert sum(probs[0].values()) == pytest.approx(1.0, abs=1e-3)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_embedded_track_is_extracted_once(tmp_path: Path) -> None:
    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:01,500\nIch liebe dich.\n", encoding="utf-8")
    video = tmp_path / "Folge.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x36:d=2", "-i", str(srt),
         "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast", "-c:s", "srt",
         "-metadata:s:s:0", "language=ger", str(video)],
        check=True,
    )
    cfg = replace(CFG, cache_dir=tmp_path / "cache")
    found = find_subtitles(video, cfg)
    assert found is not None and found.label == "Spur 1 (ger)"
    assert [line.text for line in load_lines(found.path, cfg.skip_pattern)] == ["Ich liebe dich."]
    assert find_subtitles(video, cfg) == found  # zweites Mal aus dem Cache
