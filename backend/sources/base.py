"""Gemeinsame Datentypen für alle Videoquellen (Ordner, Jellyfin)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class SourceEpisode:
    number: int
    title: str | None
    # Lokale Datei. Bei Jellyfin erst nach dem Download vorhanden.
    local_path: Path | None
    source_item_id: str | None = None
    # Lädt die Datei herunter und gibt den lokalen Pfad zurück (nur Jellyfin).
    fetch: Optional[Callable[[], Path]] = None


@dataclass
class SourceSeason:
    key: str
    source: str
    source_id: str
    title: str
    season_number: int
    episodes: list[SourceEpisode] = field(default_factory=list)
    # Falls die Quelle die IDs schon kennt (z. B. Jellyfin-AniList-Plugin).
    anilist_id: int | None = None
    mal_id: int | None = None
