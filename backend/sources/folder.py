"""Videoquelle "folder": ein lokaler Ordner mit den Folgen einer Staffel."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.sources.base import SourceEpisode, SourceSeason

log = logging.getLogger(__name__)

# Reihenfolge ist wichtig: genaue Muster zuerst.
EPISODE_PATTERNS = [
    re.compile(r"S\d{1,2}\s*E(\d{1,4})", re.IGNORECASE),  # S01E03
    re.compile(r"\b\d{1,2}x(\d{1,4})\b", re.IGNORECASE),  # 01x03 (Staffel x Folge)
    re.compile(r"\b(?:Episode|Folge|Ep)\.?\s*(\d{1,4})\b", re.IGNORECASE),  # Episode 3, Ep03, Folge 3
    re.compile(r"\bE(\d{1,4})\b", re.IGNORECASE),  # E03
    re.compile(r"\s-\s(\d{1,4})(?:v\d)?\b"),  # [Gruppe] Titel - 03 [1080p]
]
SEASON_PATTERNS = [
    re.compile(r"\bS(\d{1,2})\s*E\d", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})x\d{1,4}\b", re.IGNORECASE),
    re.compile(r"\b(?:Season|Staffel)\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\bS(\d{1,2})\b", re.IGNORECASE),
]
# Zahlen, die fast immer Auflösung oder Codec sind und keine Folgennummer.
NOT_EPISODE = {480, 576, 720, 1080, 2160, 264, 265}


def _strip_tags(name: str) -> str:
    """Entfernt [Gruppe], (Jahr), [CRC] usw."""
    return re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name)


def parse_episode_number(filename: str) -> int | None:
    stem = _strip_tags(Path(filename).stem)
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(stem)
        if match:
            return int(match.group(1))
    numbers = [int(n) for n in re.findall(r"(?<![\dx])(\d{1,3})(?![\dp])", stem)]
    numbers = [n for n in numbers if n not in NOT_EPISODE]
    return numbers[-1] if numbers else None


def parse_season_number(name: str) -> int | None:
    for pattern in SEASON_PATTERNS:
        match = pattern.search(name)
        if match:
            return int(match.group(1))
    return None


def clean_title(name: str) -> str:
    """Macht aus einem Ordnernamen einen Suchbegriff: "[Grp] Horimiya S01 (2021)" -> "Horimiya".

    Bleibt nichts übrig (Ordner heißt nur "S1" oder "Season 2"), kommt ein leerer String zurück.
    """
    text = _strip_tags(name).replace("_", " ").replace(".", " ")
    text = re.sub(r"\b(?:Season|Staffel)\s*\d{1,2}\b|\bS\d{1,2}\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" -")


def guess_title(folder: Path, files: list[Path]) -> str:
    """Titel für die AniList-Suche: Ordnername, sonst Elternordner, sonst Anfang des Dateinamens."""
    for candidate in (folder.name, folder.resolve().parent.name):
        title = clean_title(candidate)
        if title:
            return title
    # "Horimiya - 01x02 - Titel.mkv" -> "Horimiya"
    first = re.split(r"\s-\s|\bS\d{1,2}E\d|\b\d{1,2}x\d", _strip_tags(files[0].stem), maxsplit=1)[0]
    return clean_title(first) or folder.name


def scan_folder(
    folder: Path,
    extensions: tuple[str, ...],
    title: str | None = None,
    season_number: int | None = None,
) -> SourceSeason:
    """Findet alle Videodateien im Ordner und ordnet ihnen Folgennummern zu."""
    if not folder.is_dir():
        raise ValueError(f"Ordner nicht gefunden: {folder}")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions)
    if not files:
        raise ValueError(f"Keine Videodateien ({', '.join(extensions)}) in {folder}")

    episodes: list[SourceEpisode] = []
    used: set[int] = set()
    for index, path in enumerate(files, start=1):
        number = parse_episode_number(path.name)
        if number is None or number in used:
            log.warning("Folgennummer für '%s' unklar, nehme Position %d", path.name, index)
            number = index
            while number in used:
                number += 1
        used.add(number)
        episodes.append(SourceEpisode(number=number, title=None, local_path=path))
    episodes.sort(key=lambda e: e.number)

    resolved = folder.resolve()
    season = season_number or parse_season_number(folder.name) or parse_season_number(files[0].name) or 1
    return SourceSeason(
        key=f"folder:{resolved.as_posix().lower()}",
        source="folder",
        source_id=str(resolved),
        title=title or guess_title(folder, files),
        season_number=season,
        episodes=episodes,
    )
