"""Phase 5 im Index: Gesichter pro Folge finden, danach alle Gesichter der Staffel den Figuren zuordnen.

Wie in Phase 4 merkt sich jede Folge mit einer Signatur, was schon berechnet ist. Die Gesichter
(Rahmen + CLIP-Embedding) liegen pro Folge in data/cache/faces/. Ändern sich nur die Vorbilder (neues
Bild in data/characters/) oder die Einstellungen unter characters:, wird nur neu zugeordnet. Das
dauert Sekunden.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np
from sqlmodel import Session, col, select

from backend.analysis.characters import UNKNOWN, clip_characters, identify
from backend.analysis.loader import NONE, ModelLoader
from backend.analysis.video.faces import Face, FaceDetector, crop_face, top_square
from backend.analysis.video.keyframes import iter_keyframes, load_image, save_jpeg
from backend.config.settings import Settings
from backend.db.models import Character, Clip, Episode, Season
from backend.mood_index import MoodWork, _clips, _remove_old
from backend.signatures import file_signature, signature
from backend.sources.character_images import download_images, reference_files

if TYPE_CHECKING:
    from backend.indexer import Apis

log = logging.getLogger(__name__)

MATCH_VERSION = 1  # erhöhen, wenn sich die Zuordnung ändert


@dataclass
class CharacterReport:
    faces: int = 0  # Folgen, in denen Gesichter gesucht wurden
    downloads: int = 0  # neu geladene Bilder von AniList
    matched: bool = False  # Gesichter neu den Figuren zugeordnet

    @property
    def computed_anything(self) -> bool:
        return bool(self.faces or self.downloads or self.matched)


@dataclass(frozen=True)
class _Found:
    clip_index: int
    time: float
    face: Face


@dataclass(frozen=True)
class SeasonFaces:
    """Alle Gesichter der Staffel aus den .npz-Dateien, eine Zeile pro Gesicht."""

    clip_ids: np.ndarray
    times: np.ndarray
    boxes: np.ndarray
    scores: np.ndarray
    embeddings: np.ndarray


def faces_file(settings: Settings, episode_id: int, faces_signature: str) -> Path:
    return settings.faces.cache_dir / f"ep{episode_id}_{faces_signature}.npz"


def reference_file(settings: Settings, anilist_id: int, index: int) -> Path:
    """Ausschnitt aus dem Vorbild-Bild, so wie CLIP ihn sieht (für den Kontaktbogen in 'characters')."""
    return settings.characters.image_dir / "vorbilder" / f"{anilist_id}_{index}.jpg"


def _faces_signature(ep: Episode, settings: Settings, detector: str, clip: str) -> str:
    k, f = settings.keyframes, settings.faces
    return signature(ep.scenes_signature, k.fps, k.max_per_clip, k.seconds_per_frame, k.edge_seconds, f.height,
                     f.min_confidence, f.iou, f.min_size, f.max_per_frame, f.crop_scale, detector, clip)


def faces_pass(session: Session, settings: Settings, work: list[MoodWork], models: ModelLoader, force: bool,
               report: CharacterReport) -> None:
    """Pro Folge: Gesichter in den Standbildern suchen und jedes mit CLIP in ein Embedding umrechnen."""
    if models.detector_name() == NONE:
        return
    cfg = settings.faces
    frames_cfg = replace(settings.keyframes, height=cfg.height)
    for w in work:
        ep = session.get(Episode, w.episode_id)
        assert ep is not None and ep.id is not None
        expected = _faces_signature(ep, settings, models.detector_name(), models.clip_name())
        if ep.faces_signature == expected and faces_file(settings, ep.id, expected).exists() and not force:
            continue
        detector = models.face_detector()
        if detector is None:
            return  # Laden gescheitert (die Warnung kam schon), der nächste Lauf versucht es erneut
        clip_model = models.clip() if models.clip_name() != NONE else None
        actual = _faces_signature(ep, settings, detector.name, clip_model.name if clip_model else NONE)
        npz = faces_file(settings, ep.id, actual)
        if ep.faces_signature == actual and npz.exists() and not force:
            continue  # CLIP ließ sich nicht laden: Gesichter ohne CLIP sind schon da, nicht nochmal suchen
        clips = _clips(session, w.episode_id)
        log.info("Folge %d: Gesichter%s ...", w.number, " + CLIP" if clip_model else "")
        started = time.monotonic()

        found: list[_Found] = []
        crops: list[np.ndarray] = []
        vectors: list[np.ndarray] = []

        def flush() -> None:
            if crops and clip_model is not None:
                vectors.append(clip_model.embed_images(crops))
            crops.clear()

        next_log = 0.25
        for frame in iter_keyframes(w.path, [(c.start, c.end) for c in clips], frames_cfg, settings.motion.hwaccel):
            for face in detector.detect(frame.image):
                found.append(_Found(frame.clip_index, frame.time, face))
                if clip_model is not None:
                    crops.append(crop_face(frame.image, face, cfg.crop_scale).copy())
                    if len(crops) >= settings.clip.batch_size:
                        flush()
            if clips and (frame.clip_index + 1) / len(clips) >= next_log:
                log.info("  Folge %d: %d %%", w.number, int(next_log * 100))
                next_log += 0.25
        flush()

        embeddings = np.concatenate(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)
        npz.parent.mkdir(parents=True, exist_ok=True)
        np.savez(npz,
                 clip_ids=np.asarray([clips[f.clip_index].id for f in found], dtype=np.int64),
                 times=np.asarray([f.time for f in found], dtype=np.float32),
                 boxes=np.asarray([f.face.box for f in found], dtype=np.float32).reshape(-1, 4),
                 scores=np.asarray([f.face.score for f in found], dtype=np.float32),
                 embeddings=embeddings.astype(np.float16))
        _remove_old(cfg.cache_dir, f"ep{w.episode_id}_*.npz", npz)

        per_clip: dict[int, list[dict]] = defaultdict(list)
        for f in found:
            per_clip[f.clip_index].append({"t": round(f.time, 2), "box": list(f.face.box), "score": f.face.score,
                                           "char": None, "p": 0.0})
        for i, clip in enumerate(clips):
            clip.faces = per_clip.get(i, [])
            clip.characters = None  # wird gleich in match_pass neu zugeordnet
            session.add(clip)
        ep.faces_signature = actual
        session.add(ep)
        session.commit()
        report.faces += 1
        log.info("Folge %d: %d Gesichter in %d von %d Clips, %.0f s", w.number, len(found), len(per_clip),
                 len(clips), time.monotonic() - started)


def load_season_faces(settings: Settings, episodes: Sequence[Episode]) -> SeasonFaces:
    parts: dict[str, list[np.ndarray]] = defaultdict(list)
    for ep in episodes:
        if ep.id is None or not ep.faces_signature:
            continue
        path = faces_file(settings, ep.id, ep.faces_signature)
        if not path.exists():
            continue
        with np.load(path) as data:  # mit "with", sonst bleibt die Datei unter Windows gesperrt
            if len(data["clip_ids"]) == 0 or data["embeddings"].shape[0] != len(data["clip_ids"]):
                continue  # keine Gesichter oder ohne CLIP gesucht
            for key in ("clip_ids", "times", "boxes", "scores", "embeddings"):
                parts[key].append(data[key])
    if not parts:
        empty = np.zeros(0)
        return SeasonFaces(empty.astype(np.int64), empty, np.zeros((0, 4)), empty, np.zeros((0, 0), np.float32))
    return SeasonFaces(np.concatenate(parts["clip_ids"]), np.concatenate(parts["times"]),
                       np.concatenate(parts["boxes"]), np.concatenate(parts["scores"]),
                       np.concatenate(parts["embeddings"]).astype(np.float32))


def season_characters(session: Session, season: Season, roles: Sequence[str]) -> list[Character]:
    """Die Figuren der Staffel in AniList-Reihenfolge (Hauptfiguren zuerst), nur die gewünschten Rollen."""
    rows = session.exec(select(Character).where(Character.season_id == season.id).order_by(Character.id)).all()
    return [c for c in rows if c.role in roles]


def reference_crop(path: Path, detector: FaceDetector | None, crop_scale: float) -> np.ndarray | None:
    """Gesicht auf dem Vorbild-Bild finden und genauso ausschneiden wie in den Folgen."""
    image = load_image(path)
    if image is None:
        log.warning("Bild %s lässt sich nicht lesen, wird ignoriert", path)
        return None
    found = detector.detect(image) if detector is not None else []
    if found:
        return crop_face(image, max(found, key=lambda f: f.score), crop_scale)
    return top_square(image)  # Porträt: das Gesicht ist meist oben


def _match_signature(settings: Settings, models: ModelLoader, episodes: Sequence[Episode],
                     characters: Sequence[Character], files: dict[int, list[Path]]) -> str:
    cfg = settings.characters
    parts: list[object] = [MATCH_VERSION, cfg.rounds, cfg.seed_faces, cfg.max_seed_faces, cfg.seed_probability,
                           cfg.reference_weight, cfg.scale, cfg.min_probability, settings.faces.crop_scale,
                           models.clip_name(), models.detector_name()]
    parts += [(ep.id, ep.faces_signature) for ep in sorted(episodes, key=lambda e: e.number)]
    parts += [(c.anilist_id, c.name, [file_signature(p) for p in files.get(c.anilist_id, [])]) for c in characters]
    return signature(*parts)


def match_pass(session: Session, settings: Settings, season: Season, models: ModelLoader, apis: Apis | None,
               force: bool, report: CharacterReport) -> None:
    """Alle Gesichter der Staffel den Figuren von AniList zuordnen (braucht die ganze Staffel)."""
    cfg = settings.characters
    episodes = list(session.exec(select(Episode).where(Episode.season_id == season.id)).all())
    with_faces = [ep for ep in episodes
                  if ep.id is not None and ep.faces_signature and faces_file(settings, ep.id, ep.faces_signature).exists()]
    if not with_faces:
        return
    characters = season_characters(session, season, cfg.roles)
    if apis is not None and characters:
        report.downloads += download_images(characters, cfg, apis.transport, settings.apis.timeout_seconds)
    files = reference_files(characters, cfg)
    expected = _match_signature(settings, models, episodes, characters, files)
    if season.characters_signature == expected and not force:
        return

    faces = load_season_faces(settings, with_faces)
    if len(faces.embeddings) == 0:
        log.warning("Keine Gesichter mit CLIP-Embedding, Figuren können nicht zugeordnet werden")
        return
    clip_model = models.clip()
    if clip_model is None:
        log.warning("CLIP fehlt, Figuren können nicht zugeordnet werden")
        return
    if not characters:
        log.warning("Keine Figuren von AniList für diese Staffel (Lauf mit --no-api oder AniList-Treffer fehlt). "
                    "Ohne Figuren gibt es keine Zuordnung, nur die Gesichtsrahmen.")
    elif not files:
        log.warning("Keine Bilder der Figuren (AniList nicht erreichbar?). Eigene Bilder gehen auch: %s\\<Name>\\",
                    cfg.extra_dir)

    detector = models.face_detector()
    references: dict[int, np.ndarray] = {}
    for ch in characters:
        crops = [c for c in (reference_crop(p, detector, settings.faces.crop_scale)
                             for p in files.get(ch.anilist_id, [])) if c is not None and c.size]
        for old in reference_file(settings, ch.anilist_id, 0).parent.glob(f"{ch.anilist_id}_*.jpg"):
            old.unlink(missing_ok=True)
        for i, crop in enumerate(crops):
            save_jpeg(np.ascontiguousarray(crop), reference_file(settings, ch.anilist_id, i), 90)
        if crops:
            references[ch.anilist_id] = clip_model.embed_images(crops)

    result = identify(faces.embeddings, references, cfg)
    by_clip: dict[int, list[int]] = defaultdict(list)
    for i, clip_id in enumerate(faces.clip_ids.tolist()):
        by_clip[int(clip_id)].append(i)
    episode_ids = {ep.id for ep in with_faces}
    clips = session.exec(select(Clip).where(col(Clip.episode_id).in_(episode_ids))).all()
    for clip in clips:
        idx = by_clip.get(clip.id or -1, [])
        clip.faces = [{
            "t": round(float(faces.times[i]), 2),
            "box": [round(float(v), 4) for v in faces.boxes[i]],
            "score": round(float(faces.scores[i]), 3),
            "char": int(result.character[i]) if result.character[i] != UNKNOWN else None,
            "p": round(float(result.probability[i]), 3),
        } for i in idx]
        found = clip_characters(result.character[idx].tolist(), result.probability[idx].tolist()) if idx else {}
        clip.characters = {str(k): v for k, v in found.items()}
        session.add(clip)
    season.characters_signature = expected
    session.add(season)
    session.commit()
    report.matched = True
    _log_matches(characters, clips, result.character, result.seeds)


def _log_matches(characters: Sequence[Character], clips: Sequence[Clip], assigned: np.ndarray,
                 seeds: dict[int, int]) -> None:
    known = int((assigned != UNKNOWN).sum())
    log.info("Figuren zugeordnet: %d von %d Gesichtern (%d %%)", known, len(assigned),
             round(100 * known / max(1, len(assigned))))
    per_char = Counter(int(k) for clip in clips for k in (clip.characters or {}))
    for ch in characters:
        if per_char.get(ch.anilist_id):
            log.info("  %-24s %5d Clips (Vorbild aus %d Gesichtern)", ch.name, per_char[ch.anilist_id],
                     seeds.get(ch.anilist_id, 0))
    mains = [c for c in characters if c.role == "MAIN"]
    for a, b in itertools.combinations(mains, 2):
        both = sum(1 for clip in clips
                   if str(a.anilist_id) in (clip.characters or {}) and str(b.anilist_id) in (clip.characters or {}))
        log.info("  %s + %s zusammen im Bild: %d Clips", a.name, b.name, both)


def run_character_passes(session: Session, settings: Settings, season: Season, work: list[MoodWork],
                         models: ModelLoader, apis: Apis | None, force: bool) -> CharacterReport:
    report = CharacterReport()
    faces_pass(session, settings, work, models, force, report)
    match_pass(session, settings, season, models, apis, force, report)
    return report
