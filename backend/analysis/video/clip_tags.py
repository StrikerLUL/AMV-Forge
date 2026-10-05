"""CLIP-Zero-Shot: Was ist auf dem Bild zu sehen?

CLIP ist ein Modell, das Bilder und Texte in denselben Zahlenraum abbildet (ein "Embedding", hier 512
Zahlen pro Bild). Bild und passende Beschreibung landen nah beieinander. Wir vergleichen deshalb
jedes Standbild mit vielen Sätzen ("two anime characters blushing", "anime fight scene ...") und
nehmen die ähnlichsten. Zero-Shot heißt: Wir müssen nichts trainieren, die Sätze stehen in YAML.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
import yaml

from backend.analysis.device import torch_device
from backend.analysis.mood import MOODS
from backend.config.settings import ClipModelSettings

log = logging.getLogger(__name__)

NEUTRAL = "neutral"


class ImageTextEmbedder(Protocol):
    """Alles, was Bilder und Texte in Embeddings umrechnet (das echte CLIP oder ein Test-Ersatz)."""

    name: str
    logit_scale: float

    def embed_images(self, images: Sequence[np.ndarray]) -> np.ndarray: ...

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class PromptSet:
    texts: list[str]
    groups: list[str]  # Gruppe pro Satz: eine Stimmung, "neutral" oder eine Qualitätsgruppe
    quality_groups: tuple[str, ...]


@dataclass(frozen=True)
class ClipTags:
    probs: dict[str, float]  # Wahrscheinlichkeit pro Gruppe, alle Gruppen zusammen = 1
    top_prompt: str  # der Satz, der am besten passt


def is_installed() -> bool:
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("open_clip") is not None


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def load_prompts(path: Path) -> PromptSet:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    texts: list[str] = []
    groups: list[str] = []
    moods = raw.get("moods") or {}
    unknown = sorted(set(moods) - set(MOODS))
    if unknown:
        raise ValueError(f"{path.name}: unbekannte Stimmung {', '.join(unknown)} (erlaubt: {', '.join(MOODS)})")
    for mood, sentences in moods.items():
        for sentence in sentences or []:
            texts.append(str(sentence))
            groups.append(mood)
    for sentence in raw.get(NEUTRAL) or []:
        texts.append(str(sentence))
        groups.append(NEUTRAL)
    quality = raw.get("quality") or {}
    clash = sorted(set(quality) & (set(MOODS) | {NEUTRAL}))
    if clash:
        raise ValueError(f"{path.name}: Qualitätsgruppe heißt wie eine Stimmung: {', '.join(clash)}")
    for group, sentences in quality.items():
        for sentence in sentences or []:
            texts.append(str(sentence))
            groups.append(str(group))
    if not texts:
        raise ValueError(f"{path.name} enthält keine Sätze")
    return PromptSet(texts=texts, groups=groups, quality_groups=tuple(str(g) for g in quality))


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-8)


def zero_shot(
    image_embeddings: np.ndarray,
    text_embeddings: np.ndarray,
    prompts: PromptSet,
    logit_scale: float,
) -> list[ClipTags]:
    """Vergleicht jedes Bild-Embedding mit allen Sätzen und summiert die Wahrscheinlichkeiten pro Gruppe."""
    if len(image_embeddings) == 0:
        return []
    probs = softmax(logit_scale * normalize(image_embeddings.astype(np.float32)) @
                    normalize(text_embeddings.astype(np.float32)).T)
    names = sorted(set(prompts.groups))
    columns = {g: [i for i, group in enumerate(prompts.groups) if group == g] for g in names}
    result = []
    for row in probs:
        result.append(ClipTags(
            probs={g: round(float(row[columns[g]].sum()), 4) for g in names},
            top_prompt=prompts.texts[int(row.argmax())],
        ))
    return result


def crops(image: np.ndarray, mode: str) -> list[np.ndarray]:
    """Quadratische Ausschnitte für CLIP (das Modell sieht nur Quadrate).

    sides: linke und rechte Hälfte (überlappend), zusammen ist das ganze Bild drin.
    center: nur die Mitte. pad: ganzes Bild mit Balken oben und unten.
    """
    height, width = image.shape[:2]
    side = min(height, width)
    if mode == "center" or width == height:
        x, y = (width - side) // 2, (height - side) // 2
        return [image[y:y + side, x:x + side]]
    if mode == "pad":
        size = max(height, width)
        canvas = np.zeros((size, size, 3), dtype=image.dtype)
        y, x = (size - height) // 2, (size - width) // 2
        canvas[y:y + height, x:x + width] = image
        return [canvas]
    if mode != "sides":
        raise ValueError(f"clip.crop muss sides, center oder pad sein, nicht '{mode}'")
    if width > height:
        return [image[:, :side], image[:, width - side:]]
    return [image[:side, :], image[height - side:, :]]


class OpenClipModel:
    """Das echte CLIP über open_clip. Lädt das Modell beim Erzeugen (beim ersten Mal mit Download)."""

    def __init__(self, cfg: ClipModelSettings) -> None:
        import open_clip
        import torch

        self._torch = torch
        self.device = torch_device(cfg.device)
        self.crop = cfg.crop
        self.batch_size = max(1, cfg.batch_size)
        self.name = f"{cfg.model}/{cfg.pretrained}/{cfg.crop}"
        log.info("Lade CLIP %s (%s) ...", cfg.model, cfg.pretrained)
        model, _, preprocess = open_clip.create_model_and_transforms(cfg.model, pretrained=cfg.pretrained,
                                                                     device=self.device)
        model.eval()
        self.model = model
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(cfg.model)
        self.logit_scale = float(model.logit_scale.exp().item())

    def _no_grad(self) -> contextlib.AbstractContextManager[object]:
        """Ohne Gradienten (nur Vorhersage), auf der GPU zusätzlich mit halber Genauigkeit (schneller)."""
        stack = contextlib.ExitStack()
        stack.enter_context(self._torch.no_grad())
        if self.device == "cuda":
            stack.enter_context(self._torch.autocast(device_type="cuda"))
        return stack

    def _encode(self, batch: list[np.ndarray]) -> np.ndarray:
        from PIL import Image

        tensor = self._torch.stack([self.preprocess(Image.fromarray(np.ascontiguousarray(c))) for c in batch])
        with self._no_grad():
            features = self.model.encode_image(tensor.to(self.device))
        return features.float().cpu().numpy()

    def embed_images(self, images: Sequence[np.ndarray]) -> np.ndarray:
        """Ein normiertes Embedding pro Bild (Mittelwert über die Ausschnitte)."""
        pieces: list[np.ndarray] = []
        owner: list[int] = []
        for i, image in enumerate(images):
            for crop in crops(image, self.crop):
                pieces.append(crop)
                owner.append(i)
        features = np.concatenate([self._encode(pieces[k:k + self.batch_size])
                                   for k in range(0, len(pieces), self.batch_size)]) if pieces else np.zeros((0, 1))
        features = normalize(features)
        result = np.zeros((len(images), features.shape[1]), dtype=np.float32)
        np.add.at(result, np.asarray(owner), features)
        return normalize(result)

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        tokens = self.tokenizer(list(texts)).to(self.device)
        with self._no_grad():
            features = self.model.encode_text(tokens)
        return normalize(features.float().cpu().numpy())

