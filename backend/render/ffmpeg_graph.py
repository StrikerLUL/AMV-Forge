"""Rendert das Edit mit ffmpeg zu einem 9:16-MP4, ab Phase 6 mit Reframe, Übergängen und Effekten.

Jeder Clip wird als eigenes Stück frame-genau gerendert, am Ende hängt der concat-Demuxer alle Stücke ohne
neues Kodieren aneinander und legt den Song drunter.

Bei Überblendungen (crossfade, whip) sind zwei Clips gleichzeitig zu sehen. Dann gehört die Überblendung zum
Stück des neuen Clips: Der alte Clip läuft darin als zweite Eingabe noch eine halbe Übergangslänge über den
Schnitt hinaus, der neue fängt eine halbe Länge vor dem Schnitt an. Die Mitte der Überblendung liegt so genau
auf dem Beat, und die Gesamtlänge bleibt Frame für Frame gleich. Reicht die Szene dafür nicht (der Clip ist am
Szenenwechsel zu Ende), wird das erste bzw. letzte Bild der Szene gehalten, statt die Nachbarszene zu zeigen.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from backend.config.settings import FxSettings
from backend.planner.assign import Assignment
from backend.render.effects import NO_FX, ClipFx, fade_filters, shake_filter, vignette_filter, zoom_filter
from backend.render.looks import filter_path
from backend.render.reframe import Framing

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Look:
    """Was für das ganze Edit gilt: Farblook (LUT), Soft Glow und Vignette (je 0-1)."""

    lut: Path | None = None
    glow: float = 0.0
    vignette: float = 0.0


@dataclass(frozen=True)
class RenderOptions:
    width: int
    height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str
    with_music: bool = True
    look: Look = Look()
    fx: FxSettings | None = None  # Längen und Stärken der Effekte (fx: in default.yaml), nötig ab Phase 6


@dataclass(frozen=True)
class Shot:
    """Ein Clip im Edit mit allem, was der Renderer wissen muss."""

    assignment: Assignment
    framing: Framing | None = None  # None = Mitte wie bis Phase 5
    fx: ClipFx = NO_FX


def frame_counts(assignments: Sequence[Assignment], fps: int) -> list[int]:
    """Rechnet Slot-Grenzen auf das Frame-Raster um.

    Jede Grenze wird einmal global gerundet, dadurch summieren sich Rundungsfehler nicht auf:
    jeder Schnitt liegt höchstens einen halben Frame neben dem Beat.
    """
    bounds = [round(a.slot.start * fps) for a in assignments]
    bounds.append(round(assignments[-1].slot.end * fps))
    return [b - a for a, b in zip(bounds, bounds[1:])]


def _num(value: float) -> str:
    return f"{value:.4f}"


@dataclass(frozen=True)
class Window:
    """Welche Stelle der Folge ein Stück zeigt: Frames k0 bis k1 (ab Slot-Anfang, auch negativ oder hinter dem
    Slot-Ende, das braucht die Überblendung)."""

    seek: float  # -ss in der Folge
    length: float | None  # -t (None = bis zum Dateiende)
    pre: int  # so viele Frames am Anfang zeigen das erste Bild der Szene (das Stück fängt vor der Szene an)
    u0: float  # Sekunden ab assignment.source_start am Suchpunkt
    tau0: float  # Sekunden ab Slot-Anfang am Suchpunkt


def plan_window(shot: Shot, k0: int, k1: int, fps: int) -> Window:
    a = shot.assignment
    slot = a.slot

    def src(k: int) -> float:
        return a.source_start + slot.timing.source_offset(k / fps, slot.duration)

    cand = a.candidate
    lo = max(0.0, cand.start) if cand is not None else 0.0
    hi = cand.end if cand is not None else None
    if shot.fx.freeze_at is not None:  # ab hier bleibt das Bild stehen
        frozen = a.source_start + slot.source_hit(shot.fx.freeze_at)
        hi = frozen if hi is None else min(hi, frozen)
    pre = 0
    while k0 + pre < k1 - 1 and src(k0 + pre) < lo - 1e-6:
        pre += 1
    seek = max(src(k0 + pre), lo)
    if hi is not None and seek > hi - 0.1:
        seek = max(lo, hi - 0.1)  # mindestens ein paar echte Bilder, sonst gäbe es nichts zum Halten
    need = src(k1) + 0.2
    length = max(0.1, (min(hi, need) if hi is not None else need) - seek)
    u0 = seek - a.source_start
    return Window(seek, length, pre, u0, slot.timing.edit_offset(u0, slot.duration))


def speed_expr(shot: Shot, window: Window) -> str | None:
    """setpts für Slow-Mo und Speed-Ramps: Eingabezeit T (ab Suchpunkt) -> Zeit im Stück. None = normales Tempo."""
    slot = shot.assignment.slot
    timing = slot.timing
    if timing.is_normal:
        return None
    breaks = timing._breaks(slot.duration)
    u = f"({_num(window.u0)}+T)"
    t_last, s_last, v_last = breaks[-1]
    expr = f"{_num(t_last)}+({u}-{_num(s_last)})/{_num(v_last)}"
    for (t0, s0, v0), (_, s1, _) in reversed(list(zip(breaks, breaks[1:]))):
        expr = f"if(lt({u},{_num(s1)}),{_num(t0)}+({u}-{_num(s0)})/{_num(v0)},{expr})"
    return f"setpts='({expr}-{_num(window.tau0)})/TB'"


class Chain:
    """Baut eine Filterkette, die sich zwischendurch aufteilen kann (Glow, fit) und wieder zusammenkommt."""

    def __init__(self, source: str, prefix: str) -> None:
        self.lines: list[str] = []
        self.inputs = f"[{source}]"
        self.filters: list[str] = []
        self.prefix = prefix
        self.count = 0

    def add(self, *filters: str) -> None:
        self.filters.extend(f for f in filters if f)

    def label(self) -> str:
        self.count += 1
        return f"{self.prefix}{self.count}"

    def split(self) -> tuple[str, str]:
        """Beendet die Kette hier und liefert zwei Kopien des Bildes (für Filter mit zwei Eingängen)."""
        first, second = self.label(), self.label()
        self.lines.append(f"{self.inputs}{','.join(self.filters + ['split=2'])}[{first}][{second}]")
        self.filters = []
        return first, second

    def side(self, source: str, filters: str) -> str:
        out = self.label()
        self.lines.append(f"[{source}]{filters}[{out}]")
        return out

    def join(self, first: str, second: str, filter_: str) -> None:
        """Die nächste Kette beginnt mit einem Filter, der beide Kopien wieder zusammenführt."""
        self.inputs = f"[{first}][{second}]"
        self.filters = [filter_]

    def finish(self, out: str) -> str:
        self.lines.append(f"{self.inputs}{','.join(self.filters or ['null'])}[{out}]")
        return ";".join(self.lines)


def _frame(chain: Chain, framing: Framing | None, offset: float, opts: RenderOptions) -> None:
    w, h = opts.width, opts.height
    if framing is None or (framing.width >= 1.0 and framing.mode != "fit"):
        chain.add(f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}", "setsar=1")
    elif framing.mode == "fit":
        # ganzes Bild in die Mitte, oben und unten dasselbe Bild groß und unscharf
        back, front = chain.split()
        back = chain.side(back, f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                                f"scale={w // 8}:{h // 8},gblur=sigma=4,scale={w}:{h},eq=brightness=-0.08")
        front = chain.side(front, f"scale={w}:-2")
        chain.join(back, front, "overlay=(W-w)/2:(H-h)/2")
        chain.add("setsar=1")
    else:
        x = f"clip(iw*({framing.center_expr(offset)})-ow/2,0,iw-ow)"
        chain.add(f"{framing.crop_filter()}:x='{x}':y=0", f"scale={w}:{h}", "setsar=1")


def part_graph(shot: Shot, k0: int, k1: int, frames: int, opts: RenderOptions, source: str,
               prefix: str) -> tuple[list[str], str, str]:
    """ffmpeg-Eingabe und Filter für die Frames k0..k1 (ab Slot-Anfang) eines Clips.

    frames ist die Länge des Slots in Frames (für Übergänge am Ende des Clips).
    Gibt (Eingabe-Argumente, Filtergraph, Ausgabe-Label) zurück.
    """
    a = shot.assignment
    assert a.video is not None
    fps, w, h = opts.fps, opts.width, opts.height
    window = plan_window(shot, k0, k1, fps)
    inputs = ["-ss", f"{window.seek:.4f}"]
    if window.length is not None:
        inputs += ["-t", f"{window.length:.4f}"]
    inputs += ["-i", str(a.video)]

    chain = Chain(source, prefix)
    chain.add(speed_expr(shot, window) or "", f"fps={fps}")
    pad = f"tpad=start={window.pre}:start_mode=clone:" if window.pre else "tpad="
    chain.add(f"{pad}stop=-1:stop_mode=clone", f"trim=end_frame={k1 - k0}", f"setpts=N/({fps}*TB)")
    offset = k0 / fps  # Sekunden ab Slot-Anfang, bei denen das Stück beginnt
    _frame(chain, shot.framing, offset, opts)

    fx, cfg = shot.fx, opts.fx
    if cfg is not None:
        if fx.shake:
            chain.add(shake_filter(fx.shake, offset, w, h, cfg))
        focus = shot.framing.focus if shot.framing is not None else (0.5, 0.5)
        chain.add(zoom_filter(fx, focus, offset, a.slot.duration, w, h, fps, cfg) or "")
    look = opts.look
    if look.lut is not None or look.glow > 0:
        chain.add("format=gbrp")  # LUT und Glow rechnen in RGB, einmal umwandeln statt pro Filter
    if look.lut is not None:
        chain.add(f"lut3d=file={filter_path(look.lut)}:interp=tetrahedral")
    if look.glow > 0:
        # Soft Glow: unscharfe Kopie hell darüberlegen (screen), in RGB, damit die Farben stimmen
        blur = max(1.0, (cfg.glow_blur if cfg is not None else 0.02) * h / 4)
        base, glow = chain.split()
        glow = chain.side(glow, f"scale={w // 4}:{h // 4},gblur=sigma={blur:.2f},scale={w}:{h}")
        chain.join(base, glow, f"blend=all_mode=screen:all_opacity={min(look.glow, 1.0):.3f}")
    if look.vignette > 0:
        chain.add(vignette_filter(look.vignette))
    chain.add(*fade_filters(fx, k0, k1, frames, fps))
    chain.add("format=yuv420p")
    out = f"{prefix}out"
    return inputs, chain.finish(out), out


def _run(cmd: list[str]) -> None:
    log.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg ist fehlgeschlagen:\n{result.stderr[-2000:]}")


def segment_command(shots: Sequence[Shot], counts: Sequence[int], i: int, opts: RenderOptions,
                    out: Path) -> tuple[list[str], int]:
    """ffmpeg-Befehl für das Stück von Clip i (mit der Überblendung aus Clip i-1, falls es eine gibt)."""
    shot = shots[i]
    frames = counts[i]
    into = shot.fx.into.overlap if i > 0 else 0
    out_overlap = shots[i + 1].fx.into.overlap if i + 1 < len(shots) else 0
    start, end = -into, frames - out_overlap
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if into:
        prev_inputs, prev_graph, prev_out = part_graph(shots[i - 1], counts[i - 1] - into, counts[i - 1] + into,
                                                       counts[i - 1], opts, "0:v", "a")
        inputs, graph, own = part_graph(shot, start, end, frames, opts, "1:v", "b")
        seconds = 2 * into / opts.fps
        whip = shot.fx.into.kind == "whip"
        graph = (f"{prev_graph};{graph};[{prev_out}][{own}]xfade=transition={'slideleft' if whip else 'fade'}:"
                 f"duration={seconds:.4f}:offset=0")
        if whip:
            # xfade malt beim ersten Bild von slideleft einen grünen Streifen an den linken Rand (ffmpeg-Fehler),
            # fillborders überdeckt ihn mit der Nachbarspalte
            graph += f",fillborders=left=2:mode=smear:enable='lt(t,{seconds:.4f})'"
            if opts.fx is not None:  # Wisch-Schwenk: dazu Bewegungsunschärfe quer
                blur = max(1, min(1024, round(opts.fx.whip_blur * opts.width)))
                graph += f",avgblur=sizeX={blur}:sizeY=1:enable='lt(t,{seconds:.4f})'"
        graph += "[v]"
        cmd += prev_inputs + inputs
    else:
        inputs, graph, own = part_graph(shot, start, end, frames, opts, "0:v", "b")
        graph += f";[{own}]null[v]"
        cmd += inputs
    cmd += ["-filter_complex", graph, "-map", "[v]", "-an", "-frames:v", str(end - start),
            "-c:v", "libx264", "-preset", opts.preset, "-crf", str(opts.crf),
            "-pix_fmt", "yuv420p", "-video_track_timescale", "15360", str(out)]
    return cmd, end - start


def render_edit(
    shots: Sequence[Shot | Assignment],
    song: Path,
    song_start: float,
    out: Path,
    opts: RenderOptions,
    video: Path | None = None,
) -> Path:
    """Rendert jeden Clip als eigenes Stück (frame-genau) und hängt alles mit der Musik zusammen.

    Statt Shots gehen auch reine Zuweisungen (Mitte, ohne Effekte, wie bis Phase 5). Jede Zuweisung bringt
    ihre Folge mit (assignment.video), video ist nur der Standard dafür.
    """
    items = [s if isinstance(s, Shot) else Shot(s) for s in shots]
    if video is not None:
        items = [s if s.assignment.video else Shot(Assignment(s.assignment.slot, s.assignment.source_start, video,
                                                              s.assignment.episode), s.framing, s.fx) for s in items]
    if any(s.assignment.video is None for s in items):
        raise ValueError("Zuweisung ohne Video: render_edit braucht video=... oder assignment.video.")
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = frame_counts([s.assignment for s in items], opts.fps)
    total_seconds = sum(counts) / opts.fps

    with tempfile.TemporaryDirectory(prefix="amv_forge_") as tmp:
        tmp_dir = Path(tmp)
        segment_files: list[Path] = []
        for i, shot in enumerate(items):
            if counts[i] <= 0:
                continue
            seg = tmp_dir / f"seg_{i:04d}.mp4"
            cmd, length = segment_command(items, counts, i, opts, seg)
            if length <= 0:
                continue
            _run(cmd)
            segment_files.append(seg)
            a = shot.assignment
            extras = [shot.fx.into.kind] if shot.fx.into.kind != "cut" else []
            extras += shot.fx.labels()
            log.info("Clip %d/%d: %s ab %.2f s, %d Frames%s%s", i + 1, len(items), a.video.name if a.video else "?",
                     a.source_start, counts[i], f" [{a.slot.section}]" if a.slot.section else "",
                     f" ({', '.join(extras)})" if extras else "")

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
