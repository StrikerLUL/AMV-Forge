"""Rendert die Zuweisungen mit ffmpeg zu einem 9:16-MP4."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.planner.assign import Assignment

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderOptions:
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str
    with_music: bool = True


def frame_counts(assignments: list[Assignment], fps: int) -> list[int]:
    """Rechnet Slot-Grenzen auf das Frame-Raster um.

    Jede Grenze wird einmal global gerundet, dadurch summieren sich Rundungsfehler nicht auf:
    jeder Schnitt liegt höchstens einen halben Frame neben dem Beat.
    """
    bounds = [round(a.slot.start * fps) for a in assignments]
    bounds.append(round(assignments[-1].slot.end * fps))
    return [b - a for a, b in zip(bounds, bounds[1:])]


def _video_filter(opts: RenderOptions) -> str:
    # 16:9 auf volle Höhe skalieren, Mitte als 9:16 ausschneiden (Smart Reframe kommt in Phase 6).
    # tpad verlängert notfalls den letzten Frame, falls die Quelle vorher endet.
    return (
        f"fps={opts.fps},"
        f"scale={opts.width}:{opts.height}:force_original_aspect_ratio=increase,"
        f"crop={opts.width}:{opts.height},setsar=1,"
        f"tpad=stop_mode=clone:stop_duration=1"
    )


def _run(cmd: list[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg ist fehlgeschlagen:\n{result.stderr[-2000:]}")


def render_edit(
    assignments: list[Assignment],
    song: Path,
    song_start: float,
    out: Path,
    opts: RenderOptions,
    video: Path | None = None,
) -> Path:
    """Schneidet jeden Slot als eigenes Stück (frame-genau) und hängt alles mit der Musik zusammen.

    Jede Zuweisung bringt ihre Folge mit (assignment.video), video ist nur der Standard dafür.
    """
    if any(a.video is None for a in assignments) and video is None:
        raise ValueError("Zuweisung ohne Video: render_edit braucht video=... oder assignment.video.")
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = frame_counts(assignments, opts.fps)
    total_seconds = sum(counts) / opts.fps

    with tempfile.TemporaryDirectory(prefix="amv_forge_") as tmp:
        tmp_dir = Path(tmp)
        segment_files: list[Path] = []
        for i, (a, frames) in enumerate(zip(assignments, counts)):
            if frames <= 0:
                continue
            seg = tmp_dir / f"seg_{i:04d}.mp4"
            source = a.video or video
            assert source is not None
            _run([
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{a.source_start:.3f}", "-i", str(source),
                "-an", "-vf", _video_filter(opts),
                "-frames:v", str(frames),
                "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
                "-pix_fmt", "yuv420p", "-video_track_timescale", "15360",
                str(seg),
            ])
            segment_files.append(seg)
            log.info("Clip %d/%d: %s ab %.2f s, %d Frames%s", i + 1, len(assignments), source.name,
                     a.source_start, frames, f" [{a.slot.section}]" if a.slot.section else "")

        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in segment_files), encoding="utf-8"
        )

        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list)]
        if opts.with_music:
            cmd += ["-ss", f"{song_start:.3f}", "-t", f"{total_seconds:.3f}", "-i", str(song)]
            cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", opts.audio_bitrate]
        else:
            cmd += ["-map", "0:v"]
        cmd += ["-c:v", "copy", "-t", f"{total_seconds:.3f}", "-movflags", "+faststart", str(out)]
        _run(cmd)

    log.info("Fertig: %s (%.2f s)", out, total_seconds)
    return out
