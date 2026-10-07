"""Welche Figur ist das? Gesichter aus den Folgen mit den Bildern von AniList vergleichen (Phase 5).

Jedes Gesicht wird mit CLIP in ein Embedding umgerechnet, genau wie die Standbilder in Phase 4.
Gesichter derselben Figur liegen dort nah beieinander: gleiche Haarfarbe, Frisur, Augen, Brille.

1. Start: Für jede Figur gibt es ein Vorbild, das Embedding ihres AniList-Bildes. Das Bild ist oft
   anders gezeichnet als die Folgen, deshalb zählt hier nur, welchem Vorbild ein Gesicht ähnlicher
   ist als die anderen Gesichter der Staffel (z-Wert statt absoluter Ähnlichkeit). Die eindeutigsten
   Treffer pro Figur sind der Startpunkt.
2. Verbessern: Der Durchschnitt dieser Treffer wird das neue Vorbild, jetzt im Zeichenstil der Folgen.
   Mit den neuen Vorbildern kommen neue sichere Treffer dazu. Das wiederholt sich ein paar Runden.
3. Am Ende gehört jedes Gesicht zur ähnlichsten Figur, wenn die Sicherheit über min_probability liegt.
   Sicherheit heißt hier: Wie deutlich liegt die ähnlichste Figur vor der zweitähnlichsten? Gleich
   ähnlich = 0,5, klarer Abstand = fast 1. Gesichter von Figuren, die nicht auf AniList stehen, sind
   meist keiner Figur deutlich näher und bleiben "unbekannt".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from backend.analysis.video.clip_tags import normalize
from backend.config.settings import CharacterSettings

UNKNOWN = -1


@dataclass(frozen=True)
class Identification:
    character: np.ndarray  # pro Gesicht: AniList-ID der Figur oder UNKNOWN
    probability: np.ndarray  # pro Gesicht: Sicherheit 0-1, dass es diese Figur ist
    seeds: dict[int, int] = field(default_factory=dict)  # pro Figur: aus so vielen Gesichtern ist das Vorbild


def first_seeds(faces: np.ndarray, refs: np.ndarray, count: int) -> list[np.ndarray]:
    """Runde 1: Pro Figur die Gesichter, die ihrem AniList-Bild am eindeutigsten ähneln.

    z-Wert: Wie viel ähnlicher ist das Gesicht dem Vorbild als die Gesichter im Schnitt? So stört es
    nicht, dass ein AniList-Bild allen Gesichtern ähnlich (oder unähnlich) sieht.
    """
    sims = faces @ refs.T
    z = (sims - sims.mean(axis=0)) / (sims.std(axis=0) + 1e-6)
    best = z.argmax(axis=1)
    if refs.shape[0] > 1:
        margin = z[np.arange(len(z)), best] - np.partition(z, -2, axis=1)[:, -2]
    else:
        margin = z[:, 0]
    seeds = []
    for k in range(refs.shape[0]):
        idx = np.where((best == k) & (margin > 0))[0]
        seeds.append(idx[np.argsort(-margin[idx], kind="stable")][:count])
    return seeds


def prototypes(faces: np.ndarray, refs: np.ndarray, seeds: Sequence[np.ndarray], ref_weight: float) -> np.ndarray:
    """Neues Vorbild pro Figur: Durchschnitt der Treffer, mit etwas AniList-Bild gegen Abdriften."""
    protos = refs.copy()
    for k, idx in enumerate(seeds):
        if len(idx):
            mean = normalize(faces[idx].mean(axis=0))
            protos[k] = normalize(ref_weight * refs[k] + (1.0 - ref_weight) * mean)
    return protos


def confidence(similarities: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Pro Zeile: Index des ähnlichsten Vorbilds und die Sicherheit 0,5-1 (Abstand zum zweitähnlichsten)."""
    best = similarities.argmax(axis=1)
    if similarities.shape[1] < 2:
        return best, np.ones(len(similarities), dtype=np.float32)
    top2 = np.partition(similarities, -2, axis=1)
    gap = top2[:, -1] - top2[:, -2]
    return best, (1.0 / (1.0 + np.exp(-scale * gap))).astype(np.float32)


def _assign(faces: np.ndarray, protos: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Ähnlichste Figur und Sicherheit pro Gesicht. Bei nur einer Figur ist der Vergleich "alle Gesichter im Schnitt"."""
    if len(protos) == 1:
        protos = np.vstack([protos, normalize(faces.mean(axis=0))])
    return confidence(faces @ protos.T, scale)


def identify(faces: np.ndarray, references: Mapping[int, np.ndarray], cfg: CharacterSettings) -> Identification:
    """Ordnet jedes Gesicht (Zeile in faces) einer Figur zu. references: AniList-ID -> Embeddings ihrer Bilder."""
    n = len(faces)
    ids = [c for c, r in references.items() if len(r)]
    if n == 0 or not ids:
        return Identification(np.full(n, UNKNOWN, dtype=np.int64), np.zeros(n, dtype=np.float32))
    faces = normalize(np.asarray(faces, dtype=np.float32))
    refs = normalize(np.stack([normalize(np.asarray(references[c], dtype=np.float32)).mean(axis=0) for c in ids]))
    count = len(ids)

    seeds = first_seeds(faces, refs, cfg.seed_faces)
    best, p = np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.float32)
    for _ in range(max(1, cfg.rounds)):
        best, p = _assign(faces, prototypes(faces, refs, seeds, cfg.reference_weight), cfg.scale)
        new_seeds = []
        for k in range(count):
            idx = np.where((best == k) & (p >= cfg.seed_probability))[0]
            idx = idx[np.argsort(-p[idx], kind="stable")][:cfg.max_seed_faces]
            new_seeds.append(idx if len(idx) else seeds[k])  # keine sicheren Treffer: altes Vorbild behalten
        seeds = new_seeds

    known = (best < count) & (p >= cfg.min_probability)
    character = np.where(known, np.asarray(ids, dtype=np.int64)[np.minimum(best, count - 1)], UNKNOWN)
    return Identification(character, np.where(known, p, 0.0).astype(np.float32),
                          {c: int(len(s)) for c, s in zip(ids, seeds)})


def clip_characters(character: Sequence[int], probability: Sequence[float]) -> dict[int, float]:
    """Figuren eines Clips aus seinen Gesichtern: pro Figur die höchste Sicherheit."""
    result: dict[int, float] = {}
    for c, p in zip(character, probability):
        if int(c) != UNKNOWN:
            result[int(c)] = max(result.get(int(c), 0.0), round(float(p), 3))
    return result
