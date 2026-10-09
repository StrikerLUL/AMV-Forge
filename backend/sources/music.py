"""Songs für die Vorschläge (Phase 7): aus einem lokalen Ordner. Jellyfin steht in jellyfin.py."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class LibraryTrack:
    """Ein Song aus der Bibliothek, bevor er analysiert ist."""

    title: str
    artist: str | None = None
    album: str | None = None
    genres: list[str] = field(default_factory=list)
    duration: float | None = None  # Sekunden, None = noch unbekannt
    local_path: Path | None = None  # bei Jellyfin erst nach dem Download vorhanden
    source: str = "folder"
    source_id: str | None = None  # Jellyfin-Item-ID
    fetch: Optional[Callable[[], Path]] = None  # lädt die Datei herunter (nur Jellyfin)
    tagged: bool = False  # Titel, Interpret, Genre schon gelesen?

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    def matches(self, search: str | None, genre: str | None) -> bool:
        """--search: Teil von Interpret, Titel, Album oder Dateiname. --genre: Teil eines Genres."""
        if search:
            text = " ".join(str(x) for x in (self.artist, self.title, self.album,
                                             self.local_path.name if self.local_path else "") if x)
            if search.lower() not in text.lower():
                return False
        if genre and not any(genre.lower() in g.lower() for g in self.genres):
            return False
        return True


def split_genres(value: str | None) -> list[str]:
    """ "J-Pop; Anime/Soundtrack" -> ["J-Pop", "Anime", "Soundtrack"]"""
    if not value:
        return []
    return [g.strip() for g in re.split(r"[;/,|]", value) if g.strip()]


def title_from_filename(path: Path) -> tuple[str, str | None]:
    """ "LiSA - Gurenge.mp3" -> ("Gurenge", "LiSA"), "03 Gurenge.mp3" -> ("Gurenge", None)"""
    stem = path.stem if " " in path.stem else path.stem.replace("_", " ")  # "LiSA_-_Gurenge" wie "LiSA - Gurenge"
    stem = re.sub(r"^\d{1,3}[\s._-]+", "", stem).strip() or stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip() or None
    return stem, None


def read_tags(path: Path) -> dict[str, Any]:
    """Länge und Tags (title, artist, album, genre) mit ffprobe, Schlüssel klein geschrieben."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe kann {path.name} nicht lesen: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout or "{}").get("format") or {}
    tags = {str(k).lower(): str(v) for k, v in (data.get("tags") or {}).items()}
    if data.get("duration") not in (None, "N/A"):
        tags["duration"] = float(data["duration"])
    return tags


def with_tags(track: LibraryTrack) -> LibraryTrack:
    """Liest Titel, Interpret, Album, Genre und Länge aus der Datei. Fehlt ein Titel, zählt der Dateiname."""
    if track.tagged or track.local_path is None:
        return track
    tags = read_tags(track.local_path)
    title, artist = title_from_filename(track.local_path)
    return replace(
        track,
        title=tags.get("title") or title,
        artist=tags.get("artist") or tags.get("album_artist") or artist,
        album=tags.get("album") or None,
        genres=split_genres(tags.get("genre")),
        duration=tags.get("duration"),
        tagged=True,
    )


def scan_music_folder(folder: Path, extensions: tuple[str, ...]) -> list[LibraryTrack]:
    """Alle Songs im Ordner und seinen Unterordnern (die Tags werden erst bei Bedarf gelesen)."""
    if not folder.is_dir():
        raise ValueError(f"Ordner nicht gefunden: {folder}")
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in extensions)
    if not files:
        raise ValueError(f"Keine Songs ({', '.join(extensions)}) in {folder}")
    tracks = []
    for path in files:
        title, artist = title_from_filename(path)
        tracks.append(LibraryTrack(title=title, artist=artist, local_path=path, source="folder"))
    return tracks


def safe_filename(text: str, limit: int = 80) -> str:
    """Macht aus "AC/DC: Back in Black?" einen Dateinamen, den auch Windows mag."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return (cleaned[:limit].rstrip(" .") or "song")
