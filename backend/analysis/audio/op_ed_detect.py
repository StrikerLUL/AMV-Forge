"""OP/ED-Fallback per Audio-Fingerprint.

Idee: Das Opening ist in jeder Folge derselbe Song. Wir wandeln den Ton in "Chroma"-Vektoren um
(pro Moment: wie stark klingt jeder der 12 Halbtöne C, C#, D ...). Läuft in zwei Folgen dieselbe
Musik, ergeben sich über ~90 Sekunden fast identische Chroma-Folgen. Diese lange gemeinsame
Strecke suchen wir auf den Diagonalen einer Ähnlichkeitsmatrix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import librosa
import numpy as np

from backend.analysis.intervals import overlap_ratio

log = logging.getLogger(__name__)

FEATURE_SR = 22050
HOP = 4096  # ~5,4 Vektoren pro Sekunde; muss ein Vielfaches von 64 sein (CQT-Bedingung)


@dataclass(frozen=True)
class Fingerprint:
    chroma: np.ndarray  # (Frames, 12), jede Zeile Länge 1 oder 0 bei Stille
    fps: float
    offset: float  # Sekunde in der Folge, bei der das Fingerprint-Fenster beginnt


@dataclass(frozen=True)
class SharedSegment:
    start_a: float
    end_a: float
    start_b: float
    end_b: float
    coverage: float

    @property
    def duration(self) -> float:
        return self.end_a - self.start_a


def fingerprint(y: np.ndarray, offset: float, silence_db: float, sr: int = FEATURE_SR) -> Fingerprint:
    """Chroma-CENS pro Zeitschritt, Stille wird auf 0 gesetzt (Stille ist in jeder Folge gleich).

    Jeder Vektor wird um seinen Mittelwert verschoben: Rauschen und Sprache haben ein flaches
    Chroma (alle Töne gleich stark) und würden sonst in jeder Folge "gleich" aussehen.
    """
    if len(y) < HOP * 4:
        return Fingerprint(np.zeros((0, 12), dtype=np.float32), sr / HOP, offset)
    chroma = librosa.feature.chroma_cens(y=y, sr=sr, hop_length=HOP, win_len_smooth=9).T.astype(np.float32)
    rms = librosa.feature.rms(y=y, frame_length=HOP * 2, hop_length=HOP)[0]
    n = min(len(chroma), len(rms))
    chroma, rms = chroma[:n], rms[:n]
    loud = librosa.amplitude_to_db(rms, ref=1.0) >= silence_db
    chroma = chroma - chroma.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(chroma, axis=1, keepdims=True)
    chroma = np.where(norms > 1e-6, chroma / np.maximum(norms, 1e-9), 0.0).astype(np.float32)
    chroma[~loud] = 0.0
    return Fingerprint(chroma, sr / HOP, offset)


def _longest_run(mask: np.ndarray, max_gap: int) -> tuple[int, int, int]:
    """Längste Strecke mit True, Lücken bis max_gap werden überbrückt. Gibt (start, ende, treffer) zurück."""
    hits = np.flatnonzero(mask)
    if len(hits) == 0:
        return 0, 0, 0
    breaks = np.flatnonzero(np.diff(hits) > max_gap + 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(hits) - 1]))
    lengths = hits[ends] - hits[starts] + 1
    best = int(np.argmax(lengths))
    return int(hits[starts[best]]), int(hits[ends[best]]) + 1, int(ends[best] - starts[best] + 1)


def find_shared_segment(
    a: Fingerprint,
    b: Fingerprint,
    similarity: float,
    min_seconds: float,
    max_seconds: float,
    max_gap_seconds: float,
    min_coverage: float = 0.6,
) -> SharedSegment | None:
    """Sucht die längste Strecke, in der a und b dieselbe Musik enthalten (bei beliebigem Versatz)."""
    if len(a.chroma) == 0 or len(b.chroma) == 0:
        return None
    fps = a.fps
    sim = a.chroma @ b.chroma.T  # Kosinus-Ähnlichkeit, da alle Zeilen Länge 1 haben
    mask = sim >= similarity
    min_frames, max_frames = int(min_seconds * fps), int(max_seconds * fps)
    max_gap = int(round(max_gap_seconds * fps))

    best: tuple[int, int, int, int] | None = None  # (länge, k, start, treffer)
    for k in range(-(len(a.chroma) - 1), len(b.chroma)):
        diag = np.diagonal(mask, offset=k)
        if diag.sum() < min_frames * min_coverage:
            continue
        start, end, hits = _longest_run(diag, max_gap)
        length = end - start
        if length < min_frames or length > max_frames or hits / length < min_coverage:
            continue
        if best is None or length > best[0]:
            best = (length, k, start, hits)

    if best is None:
        return None
    length, k, start, hits = best
    # Diagonale k: Element i gehört zu a[i + max(0,-k)] und b[i + max(0,k)]
    ia, ib = start + max(0, -k), start + max(0, k)
    return SharedSegment(
        start_a=a.offset + ia / fps,
        end_a=a.offset + (ia + length) / fps,
        start_b=b.offset + ib / fps,
        end_b=b.offset + (ib + length) / fps,
        coverage=hits / length,
    )


def reconcile(
    aniskip: tuple[float, float] | None,
    audio: tuple[float, float] | None,
    min_overlap: float,
) -> tuple[tuple[float, float], str] | None:
    """Entscheidet zwischen AniSkip und Audio-Vergleich für ein OP oder ED.

    - nur einer hat etwas gefunden: den nehmen
    - beide überlappen genug: beide zusammen (lieber 2 s zu viel raus als ein Stück Opening drin)
    - beide widersprechen sich: Audio-Vergleich, denn der ist auf deiner Datei gemessen
    """
    if aniskip is None and audio is None:
        return None
    if audio is None:
        return aniskip, "aniskip"  # type: ignore[return-value]
    if aniskip is None:
        return audio, "fingerprint"
    if overlap_ratio(aniskip, audio) >= min_overlap:
        return (min(aniskip[0], audio[0]), max(aniskip[1], audio[1])), "aniskip+fingerprint"
    return audio, "fingerprint"
