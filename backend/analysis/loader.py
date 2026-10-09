"""Lädt die großen Modelle erst, wenn eine Folge (oder ein Song) sie braucht (einmal pro Lauf).

CLIP, Silero VAD, das Satz-Modell (Phase 4), der Gesichtsdetektor (Phase 5) und CLAP für Songs (Phase 7). Fehlt ein Paket oder
scheitert das Laden (z. B. kein Internet beim ersten Download), läuft der Index ohne dieses Signal
weiter. Beim nächsten Lauf wird es erneut versucht.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

import numpy as np

from backend.analysis.audio import episode_audio
from backend.analysis.audio.episode_audio import SileroVad, SpeechDetector
from backend.analysis.audio.subtitles import DialogModel, SentenceDialogModel, sentence_model_installed
from backend.analysis.music import clap as music_clap
from backend.analysis.music.clap import AudioTextEmbedder, ClapMusicModel, MusicPrompts
from backend.analysis.video import clip_tags, faces
from backend.analysis.video.clip_tags import ImageTextEmbedder, OpenClipModel, PromptSet
from backend.analysis.video.faces import FaceDetector, YoloFaceDetector
from backend.config.settings import Settings
from backend.signatures import signature

log = logging.getLogger(__name__)

NONE = "none"
T = TypeVar("T")


class _Auto:
    """Platzhalter: Modell selbst laden (statt eines Test-Ersatzes oder None = aus)."""


AUTO = _Auto()


class ModelLoader:
    def __init__(
        self,
        settings: Settings,
        clip: ImageTextEmbedder | None | _Auto = AUTO,
        vad: SpeechDetector | None | _Auto = AUTO,
        dialog: DialogModel | None | _Auto = AUTO,
        faces: FaceDetector | None | _Auto = AUTO,
        clap: AudioTextEmbedder | None | _Auto = AUTO,
    ) -> None:
        self.settings = settings
        self._overrides: dict[str, object] = {"clip": clip, "vad": vad, "dialog": dialog, "faces": faces,
                                              "clap": clap}
        self._loaded: dict[str, object] = {}
        self._text_embeddings: dict[str, np.ndarray] = {}

    def _name(self, kind: str, wanted: bool, installed: Callable[[], bool], expected: str, package: str,
              phase: int = 4) -> str:
        override = self._overrides[kind]
        if not isinstance(override, _Auto):
            return getattr(override, "name") if override is not None else NONE
        if not wanted:
            return NONE
        if not installed():
            if f"warned-{kind}" not in self._loaded:
                self._loaded[f"warned-{kind}"] = True
                log.warning("%s ist nicht installiert, dieses Signal fehlt (Anleitung: README, Phase %d).",
                            package, phase)
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

    def detector_name(self) -> str:
        f = self.settings.faces
        return self._name("faces", f.enabled, faces.is_installed, f"{f.repo}/{f.model}", "onnxruntime", phase=5)

    def clap_name(self) -> str:
        m = self.settings.music_mood
        return self._name("clap", m.clap_enabled, music_clap.is_installed, m.clap_model, "transformers (mit torch)",
                          phase=7)

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

    def face_detector(self) -> FaceDetector | None:
        return self._get("faces", self.detector_name(), lambda: YoloFaceDetector(self.settings.faces))

    def clap(self) -> AudioTextEmbedder | None:
        return self._get("clap", self.clap_name(), lambda: ClapMusicModel(self.settings.music_mood))

    def music_text_embeddings(self, model: AudioTextEmbedder, prompts: MusicPrompts) -> np.ndarray:
        key = signature("clap", model.name, *prompts.texts)
        if key not in self._text_embeddings:
            self._text_embeddings[key] = model.embed_texts(prompts.texts)
        return self._text_embeddings[key]

    def text_embeddings(self, model: ImageTextEmbedder, prompts: PromptSet) -> np.ndarray:
        key = signature(model.name, *prompts.texts)
        if key not in self._text_embeddings:
            self._text_embeddings[key] = model.embed_texts(prompts.texts)
        return self._text_embeddings[key]
