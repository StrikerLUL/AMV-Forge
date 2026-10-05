"""Phase 4 im Index: Bild, CLIP-Vergleich, Ton und Untertitel pro Folge, danach die Stimmung der Staffel.

Wie in Phase 2 merkt sich jede Folge mit einem Fingerabdruck (Signatur), was schon berechnet ist.
Ändert sich nur clip_prompts.yaml, wird nur neu verglichen. Ändern sich nur die Gewichte in
default.yaml, wird nur die Stimmung neu gemischt. Beides dauert Sekunden.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, TypeVar

import numpy as np
from sqlmodel import Session, select

from backend.analysis.audio import episode_audio
from backend.analysis.audio.episode_audio import SileroVad, SpeechDetector, analyze_clips
from backend.analysis.audio.subtitles import (
    DialogModel, SentenceDialogModel, average_tags, find_subtitles, lines_in, load_lines, sentence_model_installed,
)
from backend.analysis.mood import MOODS, ClipSignals, mood_vectors
from backend.analysis.video import clip_tags
from backend.analysis.video.clip_tags import ImageTextEmbedder, OpenClipModel, PromptSet, load_prompts, zero_shot
from backend.analysis.video.keyframes import iter_keyframes, save_jpeg
from backend.analysis.video.quality import ClipStats, FrameStats, clip_quality, combine_stats, frame_stats
from backend.config.settings import Settings
from backend.db.models import Clip, Episode, Season
from backend.media import read_audio

log = logging.getLogger(__name__)

MOOD_VERSION = 1  # erhöhen, wenn sich die Berechnung der Stimmung ändert
NONE = "none"
T = TypeVar("T")


class _Auto:
    """Platzhalter: Modell selbst laden (statt eines Test-Ersatzes oder None = aus)."""


AUTO = _Auto()


def signature(*parts: object) -> str:
    raw = json.dumps([str(p) for p in parts], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


class MoodModels:
    """CLIP, Silero VAD und das Satz-Modell werden erst geladen, wenn eine Folge sie braucht (einmal pro Lauf).

    Fehlt ein Paket oder scheitert das Laden (z. B. kein Internet beim ersten Download), läuft der
    Index ohne dieses Signal weiter. Beim nächsten Lauf wird es erneut versucht.
    """

    def __init__(
        self,
        settings: Settings,
        clip: ImageTextEmbedder | None | _Auto = AUTO,
        vad: SpeechDetector | None | _Auto = AUTO,
        dialog: DialogModel | None | _Auto = AUTO,
    ) -> None:
        self.settings = settings
        self._overrides: dict[str, object] = {"clip": clip, "vad": vad, "dialog": dialog}
        self._loaded: dict[str, object] = {}
        self._text_embeddings: dict[str, np.ndarray] = {}

    def _name(self, kind: str, wanted: bool, installed: Callable[[], bool], expected: str, package: str) -> str:
        override = self._overrides[kind]
        if not isinstance(override, _Auto):
            return getattr(override, "name") if override is not None else NONE
        if not wanted:
            return NONE
        if not installed():
            if f"warned-{kind}" not in self._loaded:
                self._loaded[f"warned-{kind}"] = True
                log.warning("%s ist nicht installiert, dieses Signal fehlt (Anleitung: README, Phase 4).", package)
            return NONE
        return expected

    def clip_name(self) -> str:
        c = self.settings.clip
        return self._name("clip", c.enabled, clip_tags.is_installed, f"{c.model}/{c.pretrained}/{c.crop}",
                          "open_clip_torch")

    def vad_name(self) -> str:
        a = self.settings.episode_audio
        return self._name("vad", a.vad == "silero", episode_audio.silero_installed, f"silero/{a.vad_threshold}",
                          "silero-vad")

    def dialog_name(self) -> str:
        s = self.settings.subtitles
        return self._name("dialog", bool(s.model), sentence_model_installed, s.model, "sentence-transformers")

    def _get(self, kind: str, name: str, factory: Callable[[], T]) -> T | None:
        override = self._overrides[kind]
        if not isinstance(override, _Auto):
            return override  # type: ignore[return-value]
        if name == NONE:
            return None
        if kind not in self._loaded:
            try:
                self._loaded[kind] = factory()
            except Exception as exc:  # Download, CUDA, kaputte Installation: ohne dieses Signal weiter
                log.warning("%s konnte nicht geladen werden (%s: %s). Weiter ohne dieses Signal.",
                            name, type(exc).__name__, exc)
                self._loaded[kind] = None
        return self._loaded[kind]  # type: ignore[return-value]

    def clip(self) -> ImageTextEmbedder | None:
        return self._get("clip", self.clip_name(), lambda: OpenClipModel(self.settings.clip))

    def vad(self) -> SpeechDetector | None:
        return self._get("vad", self.vad_name(), lambda: SileroVad(self.settings.episode_audio.vad_threshold))

    def dialog(self) -> DialogModel | None:
        return self._get("dialog", self.dialog_name(), lambda: SentenceDialogModel(self.settings.subtitles))

    def text_embeddings(self, model: ImageTextEmbedder, prompts: PromptSet) -> np.ndarray:
        key = signature(model.name, *prompts.texts)
        if key not in self._text_embeddings:
            self._text_embeddings[key] = model.embed_texts(prompts.texts)
        return self._text_embeddings[key]


@dataclass(frozen=True)
class MoodWork:
    """Eine Folge, deren Datei in diesem Lauf vorhanden ist."""

    episode_id: int
    number: int
    path: Path
    duration: float


@dataclass
class MoodReport:
    visual: int = 0
    tags: int = 0
    audio: int = 0
    subtitles: int = 0
    mood: bool = False

    @property
    def computed_anything(self) -> bool:
        return bool(self.visual or self.tags or self.audio or self.subtitles or self.mood)


def _clips(session: Session, episode_id: int) -> list[Clip]:
    return list(session.exec(select(Clip).where(Clip.episode_id == episode_id).order_by(Clip.start)).all())


def embeddings_file(settings: Settings, episode_id: int, visual_signature: str) -> Path:
    return settings.clip.cache_dir / f"ep{episode_id}_{visual_signature}.npz"


def _remove_old(folder: Path, pattern: str, keep: Path) -> None:
    """Alte Cache-Dateien dieser Folge löschen (von früheren Einstellungen)."""
    if not folder.is_dir():
        return
    for old in folder.glob(pattern):
        if old == keep:
            continue
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)


def _visual_signature(ep: Episode, settings: Settings, model_name: str) -> str:
    return signature(ep.scenes_signature, asdict(settings.keyframes), model_name)


def visual_pass(session: Session, settings: Settings, work: list[MoodWork], models: MoodModels, force: bool,
                report: MoodReport) -> None:
    """Standbilder pro Clip: Vorschaubild, Helligkeit/Kontrast/Schärfe und (mit CLIP) ein Embedding."""
    cfg = settings.keyframes
    for w in work:
        ep = session.get(Episode, w.episode_id)
        assert ep is not None
        expected = _visual_signature(ep, settings, models.clip_name())
        has_embeddings = models.clip_name() == NONE or embeddings_file(settings, w.episode_id, expected).exists()
        if ep.visual_signature == expected and has_embeddings and not force:
            continue
        model = models.clip() if models.clip_name() != NONE else None
        actual = _visual_signature(ep, settings, model.name if model else NONE)
        if ep.visual_signature == actual and model is None and not force:
            continue  # CLIP ließ sich nicht laden: Standbilder ohne CLIP sind schon da, nicht nochmal dekodieren
        clips = _clips(session, w.episode_id)
        log.info("Folge %d: Standbilder%s ...", w.number, " + CLIP" if model else "")
        started = time.monotonic()

        thumbs_dir = cfg.cache_dir / f"ep{w.episode_id}_{actual}"
        stats: list[list[FrameStats]] = [[] for _ in clips]
        sums: np.ndarray | None = None
        counts = np.zeros(len(clips), dtype=np.int32)
        batch: list[tuple[int, np.ndarray]] = []

        def flush() -> None:
            nonlocal sums
            if not batch or model is None:
                batch.clear()
                return
            features = model.embed_images([image for _, image in batch])
            if sums is None:
                sums = np.zeros((len(clips), features.shape[1]), dtype=np.float32)
            for (clip_index, _), feature in zip(batch, features):
                sums[clip_index] += feature
                counts[clip_index] += 1
            batch.clear()

        done = 0
        next_log = 0.25
        for frame in iter_keyframes(w.path, [(c.start, c.end) for c in clips], cfg, settings.motion.hwaccel):
            stats[frame.clip_index].append(frame_stats(frame.image))
            if frame.thumbnail:
                path = thumbs_dir / f"{clips[frame.clip_index].id}.jpg"
                save_jpeg(frame.image, path, cfg.jpeg_quality)
                clips[frame.clip_index].thumbnail = str(path)
                done += 1
                if clips and done / len(clips) >= next_log:
                    log.info("  Folge %d: %d %%", w.number, int(next_log * 100))
                    next_log += 0.25
            if model is not None:
                batch.append((frame.clip_index, frame.image.copy()))
                if len(batch) >= settings.clip.batch_size:
                    flush()
        flush()

        for clip, frames in zip(clips, stats):
            s = combine_stats(frames)
            clip.brightness_min = s.brightness_min if s else None
            clip.brightness_max = s.brightness_max if s else None
            clip.contrast = s.contrast if s else None
            clip.sharpness = s.sharpness if s else None
            session.add(clip)

        npz = embeddings_file(settings, w.episode_id, actual)
        if model is not None and sums is not None:
            have = counts > 0
            vectors = sums[have] / counts[have, None]
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
            npz.parent.mkdir(parents=True, exist_ok=True)
            np.savez(npz, clip_ids=np.asarray([c.id for c, h in zip(clips, have) if h], dtype=np.int64),
                     embeddings=vectors.astype(np.float16))
        _remove_old(settings.clip.cache_dir, f"ep{w.episode_id}_*.npz", npz)
        _remove_old(cfg.cache_dir, f"ep{w.episode_id}_*", thumbs_dir)

        ep.visual_signature = actual
        ep.tags_signature = None  # neue Embeddings: CLIP-Vergleich neu
        session.add(ep)
        session.commit()
        report.visual += 1
        log.info("Folge %d: %d Clips, %d Standbilder in %.0f s", w.number, len(clips),
                 sum(len(s) for s in stats), time.monotonic() - started)


def tags_pass(session: Session, settings: Settings, work: list[MoodWork], models: MoodModels, force: bool,
              report: MoodReport) -> None:
    """CLIP-Vergleich der gespeicherten Embeddings mit den Sätzen aus clip_prompts.yaml."""
    prompts_text = settings.clip.prompts.read_text(encoding="utf-8")
    prompts: PromptSet | None = None
    for w in work:
        ep = session.get(Episode, w.episode_id)
        assert ep is not None and ep.visual_signature is not None
        npz = embeddings_file(settings, w.episode_id, ep.visual_signature)
        expected = signature(ep.visual_signature, prompts_text if npz.exists() else NONE)
        if ep.tags_signature == expected and not force:
            continue
        clips = _clips(session, w.episode_id)
        if npz.exists():
            model = models.clip()
            if model is None:
                log.warning("Folge %d: CLIP fehlt, Vergleich mit den Prompts bleibt offen", w.number)
                continue
            prompts = prompts or load_prompts(settings.clip.prompts)
            with np.load(npz) as data:  # mit "with", sonst bleibt die Datei unter Windows gesperrt
                embeddings, clip_ids = data["embeddings"], data["clip_ids"].tolist()
            tags = zero_shot(embeddings, models.text_embeddings(model, prompts), prompts, model.logit_scale)
            by_id = dict(zip(clip_ids, tags))
            for clip in clips:
                hit = by_id.get(clip.id)
                clip.clip_tags = hit.probs if hit else None
                clip.clip_top = hit.top_prompt if hit else None
                session.add(clip)
        else:
            for clip in clips:
                clip.clip_tags, clip.clip_top = None, None
                session.add(clip)
        ep.tags_signature = expected
        session.add(ep)
        session.commit()
        report.tags += 1


def audio_pass(session: Session, settings: Settings, work: list[MoodWork], models: MoodModels, force: bool,
               report: MoodReport) -> None:
    """Lautstärke und Sprachanteil pro Clip aus dem Ton der Folge."""
    cfg = settings.episode_audio
    for w in work:
        ep = session.get(Episode, w.episode_id)
        assert ep is not None
        if ep.audio_signature == signature(ep.scenes_signature, cfg.sample_rate, models.vad_name()) and not force:
            continue
        vad = models.vad() if models.vad_name() != NONE else None
        if ep.audio_signature == signature(ep.scenes_signature, cfg.sample_rate, NONE) and vad is None and not force:
            continue  # Silero ließ sich nicht laden: Lautstärke ist schon da
        log.info("Folge %d: Ton%s ...", w.number, " + Sprache" if vad else "")
        clips = _clips(session, w.episode_id)
        try:
            audio = read_audio(w.path, 0.0, w.duration + 1.0, cfg.sample_rate)
        except subprocess.CalledProcessError:
            log.warning("Folge %d: keine lesbare Tonspur, Ton-Signal fehlt", w.number)
            audio = None
        if audio is not None and len(audio):
            speech = vad(audio, cfg.sample_rate) if vad else None
            results = analyze_clips(audio, cfg.sample_rate, [(c.start, c.end) for c in clips], speech)
            for clip, r in zip(clips, results):
                clip.loudness, clip.speech = r.loudness, r.speech
                session.add(clip)
        ep.audio_signature = signature(ep.scenes_signature, cfg.sample_rate, vad.name if vad else NONE)
        session.add(ep)
        session.commit()
        report.audio += 1


def subtitle_pass(session: Session, settings: Settings, work: list[MoodWork], models: MoodModels, force: bool,
                  report: MoodReport) -> None:
    """Untertitel pro Clip und (mit Satz-Modell) ihre Stimmung."""
    cfg = settings.subtitles
    prompts_text = cfg.prompts.read_text(encoding="utf-8") if cfg.prompts.is_file() else ""
    for w in work:
        ep = session.get(Episode, w.episode_id)
        assert ep is not None
        source = find_subtitles(w.path, cfg) if cfg.enabled else None
        dialog_name = models.dialog_name() if source else NONE

        def sig(name: str) -> str:
            return signature(ep.scenes_signature, file_signature(source.path) if source else NONE, cfg.skip_pattern,
                             cfg.min_overlap_seconds, name, prompts_text if name != NONE else "")

        if ep.subtitle_signature == sig(dialog_name) and not force:
            continue
        clips = _clips(session, w.episode_id)
        lines = load_lines(source.path, cfg.skip_pattern) if source else []
        dialog = models.dialog() if lines and dialog_name != NONE else None
        if lines and dialog_name != NONE and dialog is None:
            dialog_name = NONE  # Laden gescheitert: nächster Lauf versucht es erneut
        line_tags = dialog.classify([line.text for line in lines]) if dialog else None
        index = {id(line): i for i, line in enumerate(lines)}
        with_text = 0
        for clip in clips:
            hits = lines_in(lines, clip.start, clip.end, cfg.min_overlap_seconds)
            clip.subtitle = " / ".join(line.text for line, _ in hits)[:500] or None
            clip.dialog_tags = average_tags([line_tags[index[id(line)]] for line, _ in hits],
                                            [overlap for _, overlap in hits]) if line_tags and hits else None
            with_text += clip.subtitle is not None
            session.add(clip)
        ep.subtitle_source = source.label if source else "keine"
        ep.subtitle_signature = sig(dialog_name)
        session.add(ep)
        session.commit()
        report.subtitles += 1
        if source:
            log.info("Folge %d: Untertitel %s, %d Zeilen, %d Clips mit Text", w.number, source.label, len(lines),
                     with_text)


def _season_mood_signature(episodes: list[Episode], settings: Settings) -> str:
    parts: list[object] = [MOOD_VERSION, json.dumps(settings.mood.weights, sort_keys=True), asdict(settings.quality),
                           settings.episode_audio.dialog_min_share]
    for ep in sorted(episodes, key=lambda e: e.number):
        parts += [ep.id, ep.motion_signature, ep.visual_signature, ep.tags_signature, ep.audio_signature,
                  ep.subtitle_signature]
    return signature(*parts)


def mood_pass(session: Session, settings: Settings, season: Season, force: bool, report: MoodReport) -> None:
    """Stimmungsvektor, Qualität und Dialog für alle Clips der Staffel (braucht die ganze Staffel für die Ränge)."""
    episodes = list(session.exec(select(Episode).where(Episode.season_id == season.id)).all())
    expected = _season_mood_signature(episodes, settings)
    if season.mood_signature == expected and not force:
        return
    clips = list(session.exec(
        select(Clip).join(Episode, Clip.episode_id == Episode.id).where(Episode.season_id == season.id)
    ).all())
    if not clips:
        return
    signals = [
        ClipSignals(
            clip={m: c.clip_tags[m] for m in MOODS if m in c.clip_tags} if c.clip_tags else None,
            motion=c.motion,
            loudness=c.loudness,
            speech=c.speech,
            dialog={m: c.dialog_tags[m] for m in MOODS if m in c.dialog_tags} if c.dialog_tags else None,
        )
        for c in clips
    ]
    moods = mood_vectors(signals, settings.mood.weights)
    known = [c.sharpness for c in clips if c.sharpness is not None]
    typical_sharpness = float(np.median(known)) if known else None
    issues: Counter[str] = Counter()
    for clip, mood in zip(clips, moods):
        stats = None
        if clip.brightness_min is not None and clip.brightness_max is not None:
            stats = ClipStats(clip.brightness_min, clip.brightness_max, clip.contrast or 0.0, clip.sharpness or 0.0)
        quality_probs = {g: p for g, p in (clip.clip_tags or {}).items() if g not in MOODS and g != clip_tags.NEUTRAL}
        q = clip_quality(stats, typical_sharpness, quality_probs, settings.quality)
        clip.mood, clip.quality, clip.quality_issue = mood, q.score, q.issue
        if q.score < settings.quality.min_score:
            issues[q.issue or "niedrig"] += 1
        if clip.speech is None and clip.subtitle is None:
            clip.dialog = None
        else:
            clip.dialog = bool(clip.subtitle) or (clip.speech or 0.0) >= settings.episode_audio.dialog_min_share
        session.add(clip)
    season.mood_signature = expected
    session.add(season)
    session.commit()
    report.mood = True
    detail = ", ".join(f"{name} {n}" for name, n in issues.most_common()) or "keine"
    log.info("Stimmung für %d Clips berechnet, aussortiert: %d (%s)", len(clips), sum(issues.values()), detail)


def run_mood_passes(session: Session, settings: Settings, season: Season, work: list[MoodWork],
                    models: MoodModels, force: bool) -> MoodReport:
    report = MoodReport()
    visual_pass(session, settings, work, models, force, report)
    tags_pass(session, settings, work, models, force, report)
    audio_pass(session, settings, work, models, force, report)
    subtitle_pass(session, settings, work, models, force, report)
    mood_pass(session, settings, season, force, report)
    return report
