"""Gesichter den Figuren zuordnen, mit künstlichen Embeddings statt CLIP."""

from dataclasses import replace

import numpy as np
import pytest

from backend.analysis.characters import UNKNOWN, clip_characters, confidence, first_seeds, identify
from backend.config import load_settings

DIM = 256


def _unit(rng: np.random.Generator, *shape: int) -> np.ndarray:
    v = rng.normal(size=(*shape, DIM))
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _season(seed: int, counts: list[int], unknown: list[int], spread: float = 0.8):
    """Wie CLIP-Embeddings von Anime-Gesichtern: alle ähneln sich stark (gemeinsamer Anteil), jede Figur hat
    ihre eigene Richtung (Haare, Augen), dazu Rauschen. Die AniList-Bilder sind anders gezeichnet (Stil-Versatz).
    """
    rng = np.random.default_rng(seed)
    common, style = _unit(rng), _unit(rng)
    centers = _unit(rng, len(counts) + len(unknown))
    faces, labels = [], []
    for k, n in enumerate(counts + unknown):
        faces.append(3.0 * common + spread * centers[k] + rng.normal(size=(n, DIM)) / np.sqrt(DIM))
        labels += [100 + k if k < len(counts) else UNKNOWN] * n
    refs = {100 + k: (3.0 * common + 1.5 * style + 0.7 * spread * centers[k]
                      + 0.3 * rng.normal(size=DIM) / np.sqrt(DIM))[None].astype(np.float32)
            for k in range(len(counts))}
    order = rng.permutation(len(labels))
    return np.vstack(faces).astype(np.float32)[order], np.asarray(labels)[order], refs


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_identify_finds_the_characters_despite_a_different_drawing_style(seed: int) -> None:
    faces, labels, refs = _season(seed, counts=[600, 500, 150, 60, 12], unknown=[200, 120])
    result = identify(faces, refs, load_settings().characters)
    for character in refs:
        mine = labels == character
        assert (result.character[mine] == character).mean() > 0.95, character
    for main in (100, 101):  # wer als Hauptfigur erkannt wird, ist es auch (wichtig für --characters)
        assert (labels[result.character == main] == main).mean() > 0.95
    unknown = labels == UNKNOWN
    assert (result.character[unknown] == UNKNOWN).mean() > 0.75  # Figuren ohne AniList-Eintrag: meist unbekannt
    known = result.character != UNKNOWN
    assert (result.probability[known] >= 0.6).all() and (result.probability[~known] == 0).all()
    assert set(result.seeds) == set(refs)


def test_identify_also_works_when_characters_look_alike() -> None:
    faces, labels, refs = _season(3, counts=[600, 500, 150], unknown=[200], spread=0.5)
    result = identify(faces, refs, load_settings().characters)
    for character in refs:
        assert (result.character[labels == character] == character).mean() > 0.9


def test_identify_without_faces_or_references() -> None:
    cfg = load_settings().characters
    empty = identify(np.zeros((0, DIM), dtype=np.float32), {1: np.ones((1, DIM))}, cfg)
    assert len(empty.character) == 0
    faces, _, _ = _season(0, counts=[20], unknown=[])
    nobody = identify(faces, {}, cfg)
    assert (nobody.character == UNKNOWN).all() and (nobody.probability == 0).all()


def test_stricter_min_probability_leaves_more_faces_unknown() -> None:
    faces, _, refs = _season(4, counts=[300, 300], unknown=[100], spread=0.5)
    cfg = load_settings().characters
    normal = identify(faces, refs, cfg)
    strict = identify(faces, refs, replace(cfg, min_probability=0.99))
    assert (strict.character != UNKNOWN).sum() < (normal.character != UNKNOWN).sum()


def test_first_seeds_ignore_how_similar_a_reference_is_to_everything() -> None:
    # Vorbild 0 ist allen Gesichtern ähnlich (z. B. heller Hintergrund), trotzdem gehört Gesicht 2 zu Vorbild 1
    faces = np.array([[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.6, 0.0, 0.8]], dtype=np.float32)
    faces /= np.linalg.norm(faces, axis=1, keepdims=True)
    refs = np.array([[0.95, 0.31, 0.0], [0.8, 0.0, 0.6]], dtype=np.float32)
    seeds = first_seeds(faces, refs, count=5)
    assert 2 in seeds[1] and 2 not in seeds[0]


def test_confidence_is_the_gap_to_the_second_best() -> None:
    sims = np.array([[0.9, 0.9, 0.1], [0.95, 0.80, 0.1]], dtype=np.float32)
    best, p = confidence(sims, scale=50)
    assert p[0] == pytest.approx(0.5) and p[1] > 0.99
    assert best[1] == 0


def test_clip_characters_takes_the_most_certain_face() -> None:
    assert clip_characters([1, 2, 1, UNKNOWN], [0.7, 0.9, 0.85, 0.0]) == {1: 0.85, 2: 0.9}
    assert clip_characters([], []) == {}
