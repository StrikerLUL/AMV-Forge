"""Vorbilder für die Gesichtserkennung: Bilder der Figuren von AniList plus eigene Screenshots."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

import httpx

from backend.analysis.character_names import find_characters
from backend.config.settings import CharacterSettings
from backend.db.models import Character

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
USER_AGENT = "AMV-Forge/0.5 (github.com/StrikerLUL/amv-forge)"


def cached_image(cfg: CharacterSettings, anilist_id: int) -> Path | None:
    for suffix in IMAGE_SUFFIXES:
        path = cfg.image_dir / f"{anilist_id}{suffix}"
        if path.is_file():
            return path
    return None


def _usable_url(url: str | None) -> bool:
    # AniList zeigt für Figuren ohne Bild einen grauen Platzhalter ("default.jpg"), der hilft nicht
    return bool(url) and not str(url).rsplit("/", 1)[-1].startswith("default.")


def download_images(characters: Sequence[Character], cfg: CharacterSettings,
                    transport: httpx.BaseTransport | None = None, timeout: float = 30.0) -> int:
    """Lädt fehlende Bilder einmal nach cfg.image_dir. Gibt die Anzahl neuer Downloads zurück."""
    missing = [c for c in characters if _usable_url(c.image_url) and cached_image(cfg, c.anilist_id) is None]
    if not missing:
        return 0
    log.info("Lade %d Bilder von Figuren (AniList) ...", len(missing))
    cfg.image_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT}) as client:
        for ch in missing:
            url = str(ch.image_url)
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                log.warning("Bilder von AniList nicht erreichbar (%s), weiter mit denen, die schon da sind", exc)
                break
            if response.status_code != 200 or not response.content:
                log.warning("Bild von %s nicht geladen (HTTP %d)", ch.name, response.status_code)
                continue
            suffix = Path(url.split("?")[0]).suffix.lower()
            target = cfg.image_dir / f"{ch.anilist_id}{suffix if suffix in IMAGE_SUFFIXES else '.jpg'}"
            target.write_bytes(response.content)
            done += 1
            time.sleep(cfg.download_interval)
    return done


def extra_images(characters: Sequence[Character], cfg: CharacterSettings) -> dict[int, list[Path]]:
    """Eigene Vorbilder aus extra_dir/<Name>/. Der Ordnername wird wie bei --characters gesucht."""
    result: dict[int, list[Path]] = {}
    if not cfg.extra_dir.is_dir():
        return result
    for folder in sorted(p for p in cfg.extra_dir.iterdir() if p.is_dir()):
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not files:
            continue
        hits = find_characters(folder.name, characters)
        if not hits:
            log.warning("Ordner %s passt zu keiner Figur dieser Staffel, wird ignoriert", folder)
            continue
        result.setdefault(hits[0].anilist_id, []).extend(files)
    return result


def reference_files(characters: Sequence[Character], cfg: CharacterSettings) -> dict[int, list[Path]]:
    """Alle Vorbild-Bilder pro Figur (AniList-ID): erst das AniList-Bild, dann eigene."""
    extra = extra_images(characters, cfg)
    result: dict[int, list[Path]] = {}
    for ch in characters:
        files = [p for p in [cached_image(cfg, ch.anilist_id)] if p is not None] + extra.get(ch.anilist_id, [])
        if files:
            result[ch.anilist_id] = files
    return result
