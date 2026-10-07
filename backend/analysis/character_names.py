"""Figuren per Name finden: "Hori" -> "Kyouko Hori" (für --characters und eigene Vorbild-Ordner)."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Protocol, Sequence, TypeVar

log = logging.getLogger(__name__)

ROLE_ORDER = {"MAIN": 0, "SUPPORTING": 1, "BACKGROUND": 2}


class Named(Protocol):
    name: str
    role: str


N = TypeVar("N", bound=Named)


def normalize_name(text: str) -> str:
    """Kleinbuchstaben, ohne Akzente und Sonderzeichen: 'Kyōko  Hori!' -> 'kyoko hori'."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


def name_score(query: str, name: str) -> int:
    """3 = gleicher Name, 2 = alle Wörter kommen vor ("Hori" in "Kyouko Hori"), 1 = Teil eines Wortes, 0 = nein."""
    q, n = normalize_name(query), normalize_name(name)
    if not q:
        return 0
    if q == n:
        return 3
    if set(q.split()) <= set(n.split()):
        return 2
    return 1 if q in n else 0


def find_characters(query: str, characters: Sequence[N]) -> list[N]:
    """Alle passenden Figuren, die beste zuerst: besserer Name, dann Hauptfigur vor Nebenfigur, dann AniList-Reihenfolge."""
    scored = [(name_score(query, c.name), i, c) for i, c in enumerate(characters)]
    hits = [(s, i, c) for s, i, c in scored if s > 0]
    hits.sort(key=lambda t: (-t[0], ROLE_ORDER.get(t[2].role, 9), t[1]))
    return [c for _, _, c in hits]


def split_names(text: str) -> list[str]:
    """'Hori, Miyamura' -> ['Hori', 'Miyamura']"""
    return [part.strip() for part in text.split(",") if part.strip()]


def pick_characters(text: str, characters: Sequence[N]) -> list[N]:
    """'Hori,Miyamura' -> die beste passende Figur pro Name. ValueError, wenn ein Name nicht passt."""
    queries = split_names(text)
    if not queries:
        raise ValueError('Keine Namen angegeben. Beispiel: "Hori,Miyamura"')
    chosen: list[N] = []
    for query in queries:
        hits = find_characters(query, characters)
        if not hits:
            names = ", ".join(c.name for c in characters[:15]) + (" ..." if len(characters) > 15 else "")
            raise ValueError(f'Keine Figur "{query}" in dieser Staffel. Zur Auswahl: {names}')
        best = hits[0]
        if any(c is best for c in chosen):
            raise ValueError(f'"{query}" ist wieder {best.name}. Bitte genauer schreiben, z. B. den vollen Namen.')
        others = [h.name for h in hits[1:4]]
        log.info('Figur "%s" = %s%s', query, best.name, f" (auch möglich: {', '.join(others)})" if others else "")
        chosen.append(best)
    return chosen


def slug(name: str) -> str:
    """'Kyouko Hori' -> 'kyouko_hori' (für Dateinamen)."""
    return normalize_name(name).replace(" ", "_") or "figur"
