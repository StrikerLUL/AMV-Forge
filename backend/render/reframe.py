"""Smart Reframe 16:9 -> 9:16 (Phase 6): Der Ausschnitt folgt den Gesichtern, nicht stumpf der Mitte.

Aus einem 16:9-Bild passt nur ein knappes Drittel der Breite ins 9:16-Format. Wo dieses Drittel liegt,
entscheidet pro Clip:
1. Gesichter aus Phase 5 (Clip.faces, 1-3 Standbilder pro Clip): Mit --characters zählen die gewünschten
   Figuren, sonst alle großen Gesichter. Passen sie zusammen rein, kommt der Ausschnitt in ihre Mitte.
   Bewegen sie sich zwischen den Standbildern, wandert er mit.
2. Passen sie nicht zusammen rein (Paar steht weit auseinander): langsamer Schwenk vom einen zum anderen
   (reframe.too_wide: pan), nur das wichtigste Gesicht (main) oder das ganze Bild mit unscharfem Rand (fit).
3. Ohne Gesichter der Bewegungsschwerpunkt: Optical Flow wie in Phase 3, aber ohne die Kamerabewegung
   (der Median des Flusses wird abgezogen), dann der Mittelwert der x-Positionen, gewichtet mit der Bewegung.
4. Sonst die Mitte.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

from backend.analysis.video.faces import ClipFace
from backend.analysis.video.keyframes import grab_frame
from backend.config.settings import MotionSettings, ReframeSettings
from backend.planner.assign import Assignment
from backend.render.contact_sheet import Tile, contact_sheet
from backend.render.effects import smoothstep

log = logging.getLogger(__name__)

PORTRAIT = 9 / 16
EPS = 0.005  # so viel darf ein Gesicht über den Rand ragen und gilt trotzdem als "im Bild"


@dataclass(frozen=True)
class Framing:
    """Wo der 9:16-Ausschnitt in einem Clip liegt."""

    mode: str  # face, pan, main, fit, motion, center
    width: float  # Breite des Ausschnitts als Anteil der Bildbreite (1 = ganzes Bild)
    keys: tuple[tuple[float, float], ...] = ((0.0, 0.5),)  # (Sekunden ab Slot-Anfang, Mitte des Ausschnitts 0-1)
    focus: tuple[float, float] = (0.5, 0.5)  # Ziel für Zooms im 9:16-Bild (x, y je 0-1), z. B. das Gesicht
    faces: int = 0  # wichtige Gesichter in den Standbildern des Clips
    inside: int = 0  # davon ganz im 9:16-Bild (irgendwann während des Clips)
    main_inside: bool | None = None  # das wichtigste Gesicht ganz im Bild? None = keine Gesichter
    targets: tuple[ClipFace, ...] = ()  # die wichtigen Gesichter (für den Kontaktbogen)
    center_inside: int = 0  # so viele davon wären mit dem Ausschnitt aus der Mitte (bis Phase 5) ganz im Bild

    def center_at(self, t: float) -> float:
        """Mitte des Ausschnitts zur Zeit t (Sekunden ab Slot-Anfang), zwischen den Stützpunkten sanft."""
        keys = self.keys
        if t <= keys[0][0] or len(keys) == 1:
            return keys[0][1]
        for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
            if t < t1:
                u = (t - t0) / max(t1 - t0, 1e-9)
                return x0 + (x1 - x0) * u * u * (3 - 2 * u)
        return keys[-1][1]

    def center_expr(self, offset: float) -> str:
        """center_at als ffmpeg-Ausdruck mit t = Sekunden ab Anfang des Stücks, das bei offset im Slot beginnt."""
        t = f"(t+{offset:.4f})"
        expr = f"{self.keys[-1][1]:.4f}"
        for (t0, x0), (t1, x1) in reversed(list(zip(self.keys, self.keys[1:]))):
            u = f"clip(({t}-{t0:.4f})/{max(t1 - t0, 1e-3):.4f},0,1)"
            expr = f"if(lt({t},{t1:.4f}),{x0:.4f}+({x1 - x0:.4f})*{smoothstep(u)},{expr})"
        return expr

    def crop_filter(self) -> str:
        """Nur die Breite, die x-Position kommt pro Stück dazu (siehe ffmpeg_graph)."""
        return f"crop=w=iw*{self.width:.5f}:h=ih"

    @property
    def portrait(self) -> bool:
        """True, wenn ein 9:16-Streifen ausgeschnitten wird (bei Hochkant-Quellen oder fit nicht)."""
        return self.width < 1.0 and self.mode != "fit"


def window_width(aspect: float) -> float:
    """Breite des 9:16-Ausschnitts als Anteil der Bildbreite, z. B. 0,316 bei 16:9."""
    return min(1.0, PORTRAIT / aspect) if aspect > 0 else 1.0


def centered(aspect: float) -> Framing:
    return Framing("center", window_width(aspect))


def _clamp(center: float, width: float) -> float:
    return min(max(center, width / 2), 1 - width / 2)


def _inside(face: ClipFace, center: float, width: float) -> bool:
    return face.box[0] >= center - width / 2 - EPS and face.box[2] <= center + width / 2 + EPS


def frame_faces(
    faces: Sequence[ClipFace],
    window: tuple[float, float],
    to_slot: Callable[[float], float],
    duration: float,
    aspect: float,
    wanted: frozenset[int],
    cfg: ReframeSettings,
) -> Framing | None:
    """Ausschnitt nach Gesichtern. window: Anfang und Ende des Ausschnitts in der Folge (Sekunden).

    None, wenn es keine brauchbaren Gesichter gibt (dann Bewegung oder Mitte).
    """
    width = window_width(aspect)
    if width >= 1.0 or not faces:
        return None
    start, end = window
    near = [f for f in faces if start - 0.3 <= f.time <= end + 0.3] or list(faces)
    if wanted and any(f.character in wanted for f in near):
        relevant = [f for f in near if f.character in wanted]
    else:
        biggest = max(f.height for f in near)
        relevant = [f for f in near if f.height >= cfg.min_face_share * biggest]
    if not relevant:
        return None
    main = max(relevant, key=lambda f: (f.character in wanted, f.height, f.probability))
    inner = width * (1 - 2 * cfg.margin)

    def at(face: ClipFace) -> float:
        return min(max(to_slot(face.time), 0.0), duration)

    lo, hi = min(f.box[0] for f in relevant), max(f.box[2] for f in relevant)
    if hi - lo <= inner:
        framing = Framing("face", width, ((0.0, _clamp((lo + hi) / 2, width)),))
    else:
        moments = sorted({at(f) for f in relevant})
        spans = [(t, [f for f in relevant if at(f) == t]) for t in moments]
        if len(spans) > 1 and all(max(f.box[2] for f in g) - min(f.box[0] for f in g) <= inner for _, g in spans):
            # Die Gesichter bewegen sich: der Ausschnitt wandert von Standbild zu Standbild mit
            keys = tuple((t, _clamp((min(f.box[0] for f in g) + max(f.box[2] for f in g)) / 2, width))
                         for t, g in spans)
            framing = Framing("face", width, keys)
        elif cfg.too_wide == "fit":
            framing = Framing("fit", 1.0)
        elif cfg.too_wide == "pan" and duration >= cfg.pan_min_seconds:
            other = max(relevant, key=lambda f: abs(f.center_x - main.center_x))
            keys = ((0.2 * duration, _clamp(main.center_x, width)), (0.8 * duration, _clamp(other.center_x, width)))
            framing = Framing("pan", width, keys)
        else:
            framing = Framing("main", width, ((0.0, _clamp(main.center_x, width)),))

    if framing.mode == "fit":
        inside = len(relevant)
        main_ok = True
        focus = (0.5, 0.5)
    else:
        positions = [c for _, c in framing.keys]

        def visible(face: ClipFace) -> bool:
            if framing.mode == "pan":
                return any(_inside(face, c, width) for c in positions)
            return _inside(face, framing.center_at(at(face)), width)

        inside = sum(1 for f in relevant if visible(f))
        main_ok = visible(main)
        center = framing.center_at(at(main))
        focus = ((main.center_x - (center - width / 2)) / width, main.center_y)
        if framing.mode == "pan":  # beim Schwenk wandert das Gesicht durchs Bild, Zooms gehen auf die Mitte
            focus = (0.5, main.center_y)
    center_inside = sum(1 for f in relevant if _inside(f, 0.5, width))
    return Framing(framing.mode, framing.width, framing.keys, focus, len(relevant), inside, main_ok, tuple(relevant),
                   center_inside)


class SlotClock:
    """Rechnet Sekunden in der Folge in Sekunden ab Slot-Anfang um (Slow-Mo und Speed-Ramps eingerechnet)."""

    def __init__(self, assignment: Assignment) -> None:
        self.assignment = assignment

    def __call__(self, episode_time: float) -> float:
        a = self.assignment
        return a.slot.timing.edit_offset(episode_time - a.source_start, a.slot.duration)


def motion_center(video: Path, start: float, end: float, cfg: MotionSettings, minimum: float) -> float | None:
    """Bewegungsschwerpunkt (x, 0-1) zwischen start und end, ohne Kameraschwenk. None = kaum Bewegung."""
    length = max(end - start, 3.0 / cfg.fps)
    size = cfg.width * cfg.height
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{length:.3f}", "-i", str(video),
           "-an", "-sn", "-dn", "-vf", f"fps={cfg.fps},scale={cfg.width}:{cfg.height}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        log.debug("Bewegungsschwerpunkt: ffmpeg-Fehler bei %s: %s", video.name, result.stderr[-300:])
        return None
    count = len(result.stdout) // size
    if count < 2:
        return None
    frames = np.frombuffer(result.stdout[: count * size], dtype=np.uint8).reshape(count, cfg.height, cfg.width)
    xs = (np.arange(cfg.width) + 0.5) / cfg.width
    weighted = total = moved = 0.0
    for prev, frame in zip(frames, frames[1:]):
        flow = cv2.calcOpticalFlowFarneback(prev, frame, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        own = flow - np.median(flow.reshape(-1, 2), axis=0)  # Kameraschwenk raus, übrig bleibt, was sich selbst bewegt
        magnitude = np.hypot(own[..., 0], own[..., 1])
        moved += float(magnitude.mean())
        energy = magnitude ** 2  # starke Bewegung zählt mehr als Rauschen
        weighted += float((energy.sum(axis=0) * xs).sum())
        total += float(energy.sum())
    if moved / (count - 1) < minimum or total <= 0:
        return None
    return weighted / total


def frame_shot(
    assignment: Assignment,
    aspect: float,
    wanted: frozenset[int],
    cfg: ReframeSettings,
    motion: MotionSettings,
) -> Framing:
    """Framing für einen Clip im Edit: Gesichter, sonst Bewegung, sonst Mitte."""
    width = window_width(aspect)
    if cfg.mode == "center" or width >= 1.0:
        return centered(aspect)
    a = assignment
    window = (a.source_start, a.source_end)
    if a.candidate is not None and a.candidate.faces:
        framing = frame_faces(a.candidate.faces, window, SlotClock(a), a.slot.duration, aspect, wanted, cfg)
        if framing is not None:
            return framing
    if a.video is not None:
        x = motion_center(a.video, window[0], window[1], motion, cfg.motion_min)
        if x is not None:
            return Framing("motion", width, ((0.0, _clamp(x, width)),), focus=(0.5, 0.5))
    return centered(aspect)


@dataclass(frozen=True)
class FramingStats:
    modes: dict[str, int]  # Clips pro Art (face, pan, motion ...)
    faces: int  # wichtige Gesichter in den Standbildern aller Clips
    inside: int  # davon ganz im 9:16-Bild
    center_inside: int  # davon ganz im Bild, wenn wie bis Phase 5 die Mitte ausgeschnitten würde
    clips_with_faces: int
    main_inside: int  # Clips, in denen das wichtigste Gesicht ganz im Bild ist


def summarize(framings: Sequence[Framing]) -> FramingStats:
    modes: dict[str, int] = {}
    for f in framings:
        modes[f.mode] = modes.get(f.mode, 0) + 1
    with_faces = [f for f in framings if f.main_inside is not None]
    return FramingStats(modes, sum(f.faces for f in framings), sum(f.inside for f in framings),
                        sum(f.center_inside for f in framings), len(with_faces),
                        sum(1 for f in with_faces if f.main_inside))


def _draw(image: np.ndarray, framing: Framing, faces: Sequence[ClipFace], t: float) -> np.ndarray:
    """Dunkelt alles außerhalb des 9:16-Ausschnitts ab und malt die wichtigen Gesichter ein (grün = ganz im Bild,
    rot = nicht). Gesichter, die für den Ausschnitt nicht zählen (Hintergrund), sind grau."""
    out = image.copy()
    h, w = out.shape[:2]
    if framing.portrait:
        center = framing.center_at(t)
        left, right = int(round((center - framing.width / 2) * w)), int(round((center + framing.width / 2) * w))
        mask = np.ones(w, dtype=bool)
        mask[max(0, left):min(w, right)] = False
        out[:, mask] = (out[:, mask] * 0.3).astype(np.uint8)
        cv2.rectangle(out, (left, 0), (right - 1, h - 1), (255, 255, 255), 1)
    for face in faces:
        x0, y0, x1, y1 = (int(round(v * s)) for v, s in zip(face.box, (w, h, w, h)))
        if face not in framing.targets:
            color = (150, 150, 150)
        elif framing.mode == "pan":  # Schwenk: zählt, wenn das Gesicht am Anfang oder am Ende ganz drin ist
            color = (60, 220, 60) if any(_inside(face, c, framing.width) for _, c in framing.keys) else (230, 40, 40)
        else:
            ok = not framing.portrait or _inside(face, framing.center_at(t), framing.width)
            color = (60, 220, 60) if ok else (230, 40, 40)
        cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)
    return out


def reframe_sheet(assignments: Sequence[Assignment], framings: Sequence[Framing], out: Path,
                  tile_height: int = 180) -> Path:
    """Kontaktbogen: pro Clip das 16:9-Bild aus der Mitte des Clips mit dem 9:16-Ausschnitt (hell) und Gesichtern."""
    tiles = []
    for i, (a, framing) in enumerate(zip(assignments, framings)):
        mid = a.slot.duration / 2
        source = a.source_start + a.slot.source_hit(mid)
        image = grab_frame(a.video, source, tile_height) if a.video else None
        faces: list[ClipFace] = []
        if image is not None and a.candidate is not None and a.candidate.faces:
            nearest = min(a.candidate.faces, key=lambda f: abs(f.time - source)).time
            faces = [f for f in a.candidate.faces if f.time == nearest]
        if image is not None:
            image = _draw(image, framing, faces, mid)
        label = f"{i + 1} F{a.episode if a.episode is not None else '-'} {int(source // 60):02d}:{source % 60:04.1f} {framing.mode}"
        if framing.faces:
            label += f" {framing.inside}/{framing.faces}"
        tiles.append(Tile(image, label))
    return contact_sheet(tiles, out, columns=6, tile_height=tile_height)
