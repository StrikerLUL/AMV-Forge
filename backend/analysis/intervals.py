"""Rechnen mit Zeitbereichen (Start, Ende) in Sekunden."""

from __future__ import annotations


def merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Fasst überlappende Bereiche zusammen."""
    result: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def subtract(
    segments: list[tuple[float, float]],
    cuts: list[tuple[float, float]],
    min_length: float = 0.0,
) -> list[tuple[float, float]]:
    """Schneidet alle cuts aus den segments heraus. Reste kürzer als min_length fallen weg.

    Beispiel: Szene 80-100 s, Opening 90-180 s -> übrig bleibt 80-90 s.
    """
    merged = merge(cuts)
    result: list[tuple[float, float]] = []
    for start, end in segments:
        pieces = [(start, end)]
        for cut_start, cut_end in merged:
            next_pieces: list[tuple[float, float]] = []
            for p_start, p_end in pieces:
                if cut_end <= p_start or cut_start >= p_end:
                    next_pieces.append((p_start, p_end))
                    continue
                if cut_start > p_start:
                    next_pieces.append((p_start, cut_start))
                if cut_end < p_end:
                    next_pieces.append((cut_end, p_end))
            pieces = next_pieces
        result.extend(p for p in pieces if p[1] - p[0] >= min_length)
    return result
