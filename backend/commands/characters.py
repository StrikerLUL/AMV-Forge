"""CLI-Befehl für Phase 5: Welche Figuren hat die Gesichtserkennung gefunden? Mit Kontaktbögen zum Prüfen."""

from __future__ import annotations

import argparse
import itertools
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TypeVar

import numpy as np
from sqlmodel import Session, select

from backend.analysis.character_names import pick_characters, slug
from backend.analysis.video.faces import Face, crop_face
from backend.analysis.video.keyframes import grab_frame
from backend.character_index import reference_file, season_characters
from backend.commands.common import fmt_time
from backend.config.settings import Settings
from backend.db import get_engine
from backend.db.models import Character, Clip, Episode, Season
from backend.render.contact_sheet import Tile, contact_sheet
from backend.sources.character_images import reference_files

log = logging.getLogger("amv_forge")

T = TypeVar("T")

ROLE_LABEL = {"MAIN": "Haupt", "SUPPORTING": "Neben", "BACKGROUND": "Hintergr."}


def add_character_commands(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    chars = sub.add_parser("characters", help="Phase 5: gefundene Figuren anzeigen (mit Kontaktbögen der Gesichter)")
    chars.add_argument("--season", type=int, required=True, help="DB-ID der Staffel (zeigt 'status')")
    chars.add_argument("--show", default=None,
                       help='Kontaktbögen für diese Figuren, z. B. "Hori,Miyamura" (Standard: die Hauptfiguren)')
    chars.add_argument("--top", type=int, default=24, help="So viele Gesichter bzw. Szenen pro Kontaktbogen")
    chars.add_argument("--no-sheet", action="store_true", help="Keine Kontaktbögen (JPG) schreiben")
    chars.add_argument("--out-dir", type=Path, default=Path("data") / "renders", help="Ordner für die Kontaktbögen")
    chars.add_argument("--config", type=Path, default=None)
    chars.set_defaults(handler=run_characters)


@dataclass(frozen=True)
class FoundFace:
    clip: Clip
    episode: Episode
    time: float
    box: tuple[float, float, float, float]
    score: float
    character: int | None
    probability: float


def spread(items: Sequence[T], count: int) -> list[T]:
    """count Einträge gleichmäßig verteilt (erster und letzter sind dabei), damit man alle Bereiche sieht."""
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    picks = np.unique(np.linspace(0, len(items) - 1, count).round().astype(int))
    return [items[int(i)] for i in picks]


def _faces(rows: Sequence[tuple[Clip, Episode]]) -> list[FoundFace]:
    return [FoundFace(clip, ep, float(f["t"]), tuple(f["box"]), float(f.get("score", 0.0)),  # type: ignore[arg-type]
                      f.get("char"), float(f.get("p", 0.0)))
            for clip, ep in rows for f in (clip.faces or [])]


class _Frames:
    """Holt Standbilder aus den Folgen, jedes nur einmal (mehrere Gesichter liegen oft im selben Bild)."""

    def __init__(self, height: int) -> None:
        self.height = height
        self.cache: dict[tuple[str, float], np.ndarray | None] = {}

    def get(self, ep: Episode, time: float) -> np.ndarray | None:
        if not ep.path or not Path(ep.path).is_file():
            return None
        key = (ep.path, round(time, 2))
        if key not in self.cache:
            self.cache[key] = grab_frame(Path(ep.path), time, self.height)
        return self.cache[key]


def _face_tiles(faces: Sequence[FoundFace], frames: _Frames, crop_scale: float, label_p: bool) -> list[Tile]:
    tiles = []
    for f in faces:
        image = frames.get(f.episode, f.time)
        crop = crop_face(image, Face(f.box, f.score), crop_scale) if image is not None else None
        detail = f"p{f.probability:.2f}" if label_p else f"{f.box[3] - f.box[1]:.0%}"
        tiles.append(Tile(crop, f"F{f.episode.number} {fmt_time(f.time)} {detail}"))
    return tiles


def _log_overview(characters: Sequence[Character], faces: Sequence[FoundFace], clips: Sequence[Clip],
                  refs: dict[int, list[Path]]) -> None:
    by_char: dict[int, list[FoundFace]] = defaultdict(list)
    for f in faces:
        if f.character is not None:
            by_char[f.character].append(f)
    clip_count = Counter(int(k) for c in clips for k in (c.characters or {}))
    log.info("")
    log.info("  %-26s %-9s %6s %10s %11s %9s", "Figur", "Rolle", "Clips", "Gesichter", "Sicherheit", "Vorbilder")
    missing = []
    for ch in sorted(characters, key=lambda c: -clip_count.get(c.anilist_id, 0)):
        found = by_char.get(ch.anilist_id, [])
        if not found:
            missing.append(ch.name)
            continue
        avg = sum(f.probability for f in found) / len(found)
        log.info("  %-26s %-9s %6d %10d %11.2f %9d", ch.name, ROLE_LABEL.get(ch.role, ch.role),
                 clip_count.get(ch.anilist_id, 0), len(found), avg, len(refs.get(ch.anilist_id, [])))
    if missing:
        log.info("  Nicht gefunden: %s", ", ".join(missing))


def _together(clips: Sequence[Clip], chosen: Sequence[Character]) -> list[Clip]:
    ids = [str(c.anilist_id) for c in chosen]
    return [c for c in clips if all(k in (c.characters or {}) for k in ids)]


def run_characters(args: argparse.Namespace, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    with Session(engine) as session:
        season = session.get(Season, args.season)
        if season is None:
            raise ValueError(f"Keine Staffel mit DB-ID {args.season}. 'status' zeigt alle.")
        rows = list(session.exec(
            select(Clip, Episode).join(Episode, Clip.episode_id == Episode.id).where(Episode.season_id == season.id)
        ).all())
        characters = season_characters(session, season, settings.characters.roles)
    searched = [(c, ep) for c, ep in rows if c.faces is not None]
    if not searched:
        raise ValueError("Für diese Staffel wurden noch keine Gesichter gesucht. Erst 'index' laufen lassen (Phase 5).")

    faces = _faces(searched)
    clips = [c for c, _ in searched]
    n_episodes = len({ep.number for _, ep in rows})
    log.info("%s: %d Gesichter in %d von %d Clips (gesucht in %d von %d Folgen)", season.anilist_title or season.title,
             len(faces), sum(1 for c in clips if c.faces), len(rows), len({ep.number for _, ep in searched}),
             n_episodes)
    if season.characters_signature is None:
        log.warning("Die Gesichter sind noch keiner Figur zugeordnet. Die Meldungen von 'index' sagen, warum "
                    "(keine Figuren von AniList, keine Bilder oder kein CLIP).")
    known = sum(1 for f in faces if f.character is not None)
    log.info("Einer Figur zugeordnet: %d von %d Gesichtern (%d %%), der Rest gilt als unbekannt", known, len(faces),
             round(100 * known / max(1, len(faces))))
    refs = reference_files(characters, settings.characters)
    if characters:
        _log_overview(characters, faces, clips, refs)

    if args.show:
        shown = pick_characters(args.show, characters)
    else:
        shown = [c for c in characters if c.role == "MAIN"]
    if len(shown) > 1:
        log.info("")
        for size in range(2, len(shown) + 1):
            for group in itertools.combinations(shown, size):
                log.info("%s zusammen im Bild: %d Clips", " + ".join(c.name for c in group),
                         len(_together(clips, group)))
    if args.no_sheet:
        return

    log.info("")
    log.info("Kontaktbögen (Standbilder werden aus den Folgen geholt, das dauert etwas) ...")
    frames = _Frames(settings.faces.height)
    crop_scale = settings.faces.crop_scale
    for ch in shown:
        own = sorted((f for f in faces if f.character == ch.anilist_id), key=lambda f: -f.probability)
        references = [p for p in (reference_file(settings, ch.anilist_id, i) for i in range(6)) if p.is_file()]
        tiles = [Tile(p, f"Vorbild {i + 1}") for i, p in enumerate(references)]
        # gleichmäßig von "ganz sicher" bis "gerade noch": sind auch die unsicheren noch die richtige Figur?
        tiles += _face_tiles(spread(own, max(0, args.top - len(tiles))), frames, crop_scale, label_p=True)
        out = contact_sheet(tiles, args.out_dir / f"figur_s{season.id}_{slug(ch.name)}.jpg", columns=6,
                            tile_height=150, aspect=1.0)
        log.info("  %s: %s", ch.name, out)

    unknown = sorted((f for f in faces if f.character is None), key=lambda f: -(f.box[3] - f.box[1]))
    if unknown:
        # die größten unbekannten Gesichter: sind da Hauptfiguren dabei, die nicht erkannt wurden?
        tiles = _face_tiles(unknown[: args.top], frames, crop_scale, label_p=False)
        out = contact_sheet(tiles, args.out_dir / f"figur_s{season.id}_unbekannt.jpg", columns=6, tile_height=150,
                            aspect=1.0)
        log.info("  unbekannt (die größten Gesichter): %s", out)

    if len(shown) > 1:
        both = _together(clips, shown)
        both.sort(key=lambda c: -min((c.characters or {})[str(ch.anilist_id)] for ch in shown))
        episode_of = {c.id: ep for c, ep in searched}
        tiles = [Tile(Path(c.thumbnail) if c.thumbnail else None,
                      f"F{episode_of[c.id].number} {fmt_time(c.start)} "
                      f"{min((c.characters or {})[str(ch.anilist_id)] for ch in shown):.2f}")
                 for c in spread(both, args.top)]
        name = "_".join(slug(ch.name).split("_")[-1] for ch in shown)
        out = contact_sheet(tiles, args.out_dir / f"figuren_s{season.id}_{name}.jpg")
        log.info("  zusammen: %s", out)
