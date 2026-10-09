"""CLAP-Zero-Shot für Musik: Wie klingt ein Song?

CLAP ("Contrastive Language-Audio Pretraining") ist das, was CLIP für Bilder ist, nur für Ton: Ein
Modell rechnet Audio und Sätze in denselben Zahlenraum um (ein Embedding). Ein Song und eine
passende Beschreibung ("a sad piano ballad") landen nah beieinander. Wir hören uns einige
10-Sekunden-Stücke jedes Songs an und vergleichen sie mit den Sätzen aus music_prompts.yaml.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
import yaml

from backend.analysis.device import torch_device
from backend.analysis.mood import MOODS
from backend.analysis.video.clip_tags import normalize, softmax
from backend.config.settings import MusicMoodSettings

log = logging.getLogger(__name__)


class AudioTextEmbedder(Protocol):
    """Alles, was Audio und Texte in Embeddings umrechnet (das echte CLAP oder ein Test-Ersatz)."""

    name: str
    sample_rate: int  # Abtastrate, die das Modell erwartet (CLAP: 48000)
    window_seconds: float  # so lang ist ein Stück, das das Modell auf einmal hört (CLAP: 10 s)
    logit_scale: float

    def embed_audio(self, windows: Sequence[np.ndarray]) -> np.ndarray: ...

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class MusicPrompts:
    texts: list[str]
    groups: list[str]  # Stimmung pro Satz


@dataclass(frozen=True)
class MusicTags:
    mood: dict[str, float]  # 0-1 pro Stimmung, 0.5 = so wahrscheinlich wie jede andere
    probs: dict[str, float]  # Wahrscheinlichkeit pro Stimmung, zusammen 1
    top_prompt: str  # der Satz, der am besten passt


def is_installed() -> bool:
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("transformers") is not None


def load_music_prompts(path: Path) -> MusicPrompts:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    moods = raw.get("moods") or {}
    unknown = sorted(set(moods) - set(MOODS))
    if unknown:
        raise ValueError(f"{path.name}: unbekannte Stimmung {', '.join(unknown)} (erlaubt: {', '.join(MOODS)})")
    texts: list[str] = []
    groups: list[str] = []
    for mood, sentences in moods.items():
        for sentence in sentences or []:
            texts.append(str(sentence))
            groups.append(str(mood))
    if not texts:
        raise ValueError(f"{path.name} enthält keine Sätze")
    return MusicPrompts(texts, groups)


def window_starts(duration: float, count: int, length: float) -> list[float]:
    """Startzeiten von count Stücken, gleichmäßig verteilt, ohne die ersten und letzten 10 % (Intro, Outro)."""
    if count <= 0 or duration <= 0:
        return []
    if duration <= length:
        return [0.0]
    first, last = 0.1 * duration, 0.9 * duration - length
    if last <= first:
        return [max(0.0, (duration - length) / 2)]
    if count == 1:
        return [round((first + last) / 2, 3)]
    step = (last - first) / (count - 1)
    return [round(first + k * step, 3) for k in range(count)]


def zero_shot(audio_embeddings: np.ndarray, text_embeddings: np.ndarray, prompts: MusicPrompts,
              logit_scale: float) -> MusicTags:
    """Vergleicht jedes Stück mit allen Sätzen und mittelt über die Stücke.

    Pro Stimmung zählt die durchschnittliche Wahrscheinlichkeit ihrer Sätze (dann ist egal, wie viele Sätze
    eine Stimmung hat). Der Wert 0-1 ist so skaliert wie bei den Clips: 0.5 = so wahrscheinlich wie im
    Schnitt jede Stimmung, 1 = doppelt so wahrscheinlich oder mehr.
    """
    probs = softmax(logit_scale * normalize(audio_embeddings.astype(np.float32)) @
                    normalize(text_embeddings.astype(np.float32)).T).mean(axis=0)
    names = [m for m in MOODS if m in prompts.groups]
    per_group = {g: float(np.mean([p for p, group in zip(probs, prompts.groups) if group == g])) for g in names}
    total = sum(per_group.values()) or 1.0
    shares = {g: v / total for g, v in per_group.items()}
    mood = {g: round(float(min(1.0, shares[g] * len(names) / 2)), 3) for g in names}
    return MusicTags(mood=mood, probs={g: round(v, 4) for g, v in shares.items()},
                     top_prompt=prompts.texts[int(probs.argmax())])


class ClapMusicModel:
    """Das echte CLAP über transformers. Lädt das Modell beim Erzeugen (beim ersten Mal mit Download)."""

    def __init__(self, cfg: MusicMoodSettings) -> None:
        import torch
        from transformers import ClapModel, ClapProcessor

        self._torch = torch
        self.device = torch_device(cfg.device)
        self.name = cfg.clap_model
        log.info("Lade CLAP %s ...", cfg.clap_model)
        self.model = ClapModel.from_pretrained(cfg.clap_model).to(self.device).eval()
        processor = ClapProcessor.from_pretrained(cfg.clap_model)
        self.features = processor.feature_extractor
        self.tokenizer = processor.tokenizer
        self.sample_rate = int(self.features.sampling_rate)
        self.window_seconds = float(self.features.nb_max_samples) / self.sample_rate
        self.logit_scale = float(self.model.logit_scale_a.exp().item())

    def _vectors(self, output: object) -> np.ndarray:
        # Ältere transformers geben direkt die Embeddings zurück, neuere (ab 5) ein Objekt mit pooler_output
        tensor = getattr(output, "pooler_output", output)
        return normalize(tensor.float().cpu().numpy())  # type: ignore[union-attr]

    def embed_audio(self, windows: Sequence[np.ndarray]) -> np.ndarray:
        # Nie länger als ein Fenster: Längeres würde CLAP an zufälligen Stellen kürzen (jeder Lauf anders)
        most = int(round(self.window_seconds * self.sample_rate))
        inputs = self.features([np.asarray(w[:most], dtype=np.float32) for w in windows],
                               sampling_rate=self.sample_rate, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self._torch.no_grad():
            return self._vectors(self.model.get_audio_features(**inputs))

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        tokens = self.tokenizer(list(texts), padding=True, return_tensors="pt")
        tokens = {k: v.to(self.device) for k, v in tokens.items() if k in ("input_ids", "attention_mask")}
        with self._torch.no_grad():
            return self._vectors(self.model.get_text_features(**tokens))
