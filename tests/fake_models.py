"""Kleine Ersatz-Modelle für Tests: kein Download, keine GPU, vorhersagbare Ergebnisse.

FakeClip sieht nur Farben: Rot = romance, Blau = action, Grün = calm. Die Sätze aus
clip_prompts.yaml zeigen jeweils auf die Achse ihrer Gruppe. FakeFaceDetector hält kräftig farbige
Flächen für Gesichter (siehe synth_video.make_face_video). FakeClap hört nur, wie rau ein Song klingt.
"""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

from backend.analysis.audio.episode_audio import Segment
from backend.analysis.music.clap import load_music_prompts
from backend.analysis.video.clip_tags import load_prompts, normalize
from backend.analysis.video.faces import Face
from backend.config.settings import ClipModelSettings, MusicMoodSettings

AXES = {"romance": 0, "action": 1, "sad": 2, "funny": 3, "calm": 4, "neutral": 5}
QUALITY_AXIS = 6


class FakeClip:
    logit_scale = 20.0

    def __init__(self, cfg: ClipModelSettings) -> None:
        self.name = f"{cfg.model}/{cfg.pretrained}/{cfg.crop}"  # wie das echte Modell, damit Signaturen stabil sind
        prompts = load_prompts(cfg.prompts)
        self.group_of = dict(zip(prompts.texts, prompts.groups))
        self.images_seen = 0

    def embed_images(self, images: Sequence[np.ndarray]) -> np.ndarray:
        self.images_seen += len(images)
        rows = []
        for image in images:
            r, g, b = (image.reshape(-1, 3).mean(axis=0) / 255.0).tolist()
            rows.append([r, b, 0.0, 0.0, g, 0.35, 0.0])
        return normalize(np.asarray(rows, dtype=np.float32))

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        rows = np.zeros((len(texts), 7), dtype=np.float32)
        for i, text in enumerate(texts):
            rows[i, AXES.get(self.group_of.get(text, ""), QUALITY_AXIS)] = 1.0
        return rows


class FakeDialog:
    name = "fake-dialog"

    def classify(self, texts: Sequence[str]) -> list[dict[str, float]]:
        result = []
        for text in texts:
            if "liebe" in text.lower():
                result.append({"romance": 0.9, "action": 0.0, "sad": 0.0, "funny": 0.0, "neutral": 0.1})
            else:
                result.append({"romance": 0.0, "action": 0.0, "sad": 0.0, "funny": 0.0, "neutral": 1.0})
        return result


class FakeVad:
    name = "fake-vad"

    def __init__(self, segments: Sequence[Segment] = ()) -> None:
        self.segments = list(segments)

    def __call__(self, audio: np.ndarray, sample_rate: int) -> list[Segment]:
        return self.segments


class FakeFaceDetector:
    """Jede kräftig farbige Fläche (nicht grau, nicht weiß) ist ein "Gesicht"."""

    name = "fake-faces"

    def __init__(self) -> None:
        self.frames = 0

    def detect(self, image: np.ndarray) -> list[Face]:
        self.frames += 1
        pixels = image.astype(np.int16)
        mask = ((pixels.max(axis=2) - pixels.min(axis=2)) > 80).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        height, width = mask.shape
        faces = []
        for x, y, w, h, area in stats[1:count]:
            if area >= 0.01 * height * width:
                faces.append(Face((x / width, y / height, (x + w) / width, (y + h) / height), 0.9))
        return faces


class FakeClap:
    """Ersatz für CLAP: Stücke mit Hi-Hats und Becken (Energie über 2 kHz) sind action, alles andere romance.
    Die Sätze aus music_prompts.yaml zeigen jeweils auf die Achse ihrer Stimmung."""

    sample_rate = 16000
    window_seconds = 4.0
    logit_scale = 20.0

    def __init__(self, cfg: MusicMoodSettings) -> None:
        self.name = cfg.clap_model  # wie das echte Modell, damit Signaturen stabil sind
        prompts = load_music_prompts(cfg.prompts)
        self.group_of = dict(zip(prompts.texts, prompts.groups))
        self.windows_heard = 0

    @classmethod
    def bright(cls, window: np.ndarray) -> bool:
        power = np.abs(np.fft.rfft(window)) ** 2
        freqs = np.fft.rfftfreq(len(window), 1 / cls.sample_rate)
        return float(power[freqs > 2000].sum() / max(1e-12, float(power.sum()))) > 0.002

    def embed_audio(self, windows: Sequence[np.ndarray]) -> np.ndarray:
        self.windows_heard += len(windows)
        rows = [[0.0, 1.0, 0.0, 0.0, 0.0, 0.2] if self.bright(w) else [1.0, 0.0, 0.0, 0.0, 0.0, 0.2] for w in windows]
        return normalize(np.asarray(rows, dtype=np.float32))

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        rows = np.zeros((len(texts), 6), dtype=np.float32)
        for i, text in enumerate(texts):
            rows[i, AXES[self.group_of[text]]] = 1.0
        return rows
