"""Videoquelle "jellyfin": Staffeln und Folgen über die Jellyfin-REST-API."""

from __future__ import annotations

import logging
import os
from functools import partial
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from backend.sources.base import SourceEpisode, SourceSeason
from backend.sources.music import LibraryTrack, safe_filename

log = logging.getLogger(__name__)


class JellyfinError(RuntimeError):
    pass


def _provider_id(item: dict[str, Any], *names: str) -> int | None:
    """Liest z. B. die AniList-ID aus ProviderIds, egal wie das Plugin den Schlüssel schreibt."""
    ids = {k.lower(): v for k, v in (item.get("ProviderIds") or {}).items()}
    for name in names:
        value = ids.get(name.lower())
        if value and str(value).isdigit():
            return int(value)
    return None


class JellyfinClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        user_id: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.user_id = user_id
        auth = (
            'MediaBrowser Client="AMV-Forge", Device="PC", DeviceId="amv-forge", '
            f'Version="0.2.0", Token="{api_key}"'
        )
        self._http = httpx.Client(
            base_url=url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": auth},
            follow_redirects=True,
        )

    @classmethod
    def from_env(cls, timeout: float = 30.0) -> "JellyfinClient":
        """Liest JELLYFIN_URL, JELLYFIN_API_KEY (und optional JELLYFIN_USER_ID) aus der .env."""
        load_dotenv()
        url, key = os.getenv("JELLYFIN_URL"), os.getenv("JELLYFIN_API_KEY")
        if not url or not key:
            raise JellyfinError("JELLYFIN_URL und JELLYFIN_API_KEY fehlen. Trag sie in .env ein (Vorlage: .env.example).")
        return cls(url, key, os.getenv("JELLYFIN_USER_ID") or None, timeout)

    def _get(self, path: str, **params: Any) -> Any:
        if self.user_id:
            params.setdefault("userId", self.user_id)
        try:
            response = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
        except httpx.TransportError as exc:
            raise JellyfinError(f"Jellyfin nicht erreichbar: {exc}") from exc
        if response.status_code == 401:
            raise JellyfinError("Jellyfin sagt 401: API-Key falsch oder abgelaufen.")
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin HTTP {response.status_code} für {path}")
        return response.json()

    def item(self, item_id: str) -> dict[str, Any]:
        items = self._get("/Items", ids=item_id, fields="ProviderIds").get("Items", [])
        if not items:
            raise JellyfinError(f"Kein Jellyfin-Eintrag mit der ID {item_id}")
        return items[0]

    def search_series(self, term: str) -> list[dict[str, Any]]:
        data = self._get(
            "/Items", searchTerm=term, includeItemTypes="Series", recursive="true", fields="ProviderIds"
        )
        return data.get("Items", [])

    def seasons(self, series_id: str) -> list[dict[str, Any]]:
        return self._get(f"/Shows/{series_id}/Seasons", fields="ProviderIds").get("Items", [])

    def episodes(self, series_id: str, season_id: str) -> list[dict[str, Any]]:
        data = self._get(
            f"/Shows/{series_id}/Episodes", seasonId=season_id, fields="Path,MediaSources,ProviderIds"
        )
        return data.get("Items", [])

    def download(self, item_id: str, target: Path, progress: bool = True) -> Path:
        """Lädt die Originaldatei einmal herunter. Existiert sie schon, passiert nichts."""
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(target.suffix + ".part")
        if progress:
            log.info("Lade %s herunter ...", target.name)
        try:
            with self._http.stream("GET", f"/Items/{item_id}/Download", timeout=None) as response:
                if response.status_code == 403:
                    raise JellyfinError("Download verboten: Gib dem Jellyfin-Benutzer die Download-Berechtigung.")
                if response.status_code >= 400:
                    raise JellyfinError(f"Download fehlgeschlagen: HTTP {response.status_code}")
                total = int(response.headers.get("Content-Length") or 0)
                done, next_log = 0, 0.1
                with part.open("wb") as fh:
                    for chunk in response.iter_bytes(chunk_size=1 << 20):
                        fh.write(chunk)
                        done += len(chunk)
                        if progress and total and done / total >= next_log:
                            log.info("  %s: %d %%", target.name, int(done / total * 100))
                            next_log += 0.1
        except httpx.TransportError as exc:
            raise JellyfinError(f"Download abgebrochen: {exc}") from exc
        part.replace(target)
        return target

    def load_season(self, season_id: str, download_dir: Path) -> SourceSeason:
        """Baut eine SourceSeason. Dateien werden erst beim ersten Bedarf heruntergeladen."""
        season = self.item(season_id)
        if season.get("Type") != "Season":
            raise JellyfinError(f"{season_id} ist keine Staffel, sondern {season.get('Type')}. Nimm die ID aus 'jellyfin-search'.")
        series_id = season["SeriesId"]
        series = self.item(series_id)
        number = int(season.get("IndexNumber") or 1)

        episodes: list[SourceEpisode] = []
        for index, ep in enumerate(self.episodes(series_id, season_id), start=1):
            ep_number = int(ep.get("IndexNumber") or index)
            container = (ep.get("MediaSources") or [{}])[0].get("Container") or ""
            suffix = Path(ep.get("Path") or "").suffix or (f".{container.split(',')[0]}" if container else ".mkv")
            target = download_dir / season_id / f"E{ep_number:03d}_{ep['Id']}{suffix}"
            episodes.append(
                SourceEpisode(
                    number=ep_number,
                    title=ep.get("Name"),
                    local_path=target if target.exists() else None,
                    source_item_id=ep["Id"],
                    fetch=partial(self.download, ep["Id"], target),
                )
            )

        # Serien-IDs gelten nur für Staffel 1, spätere Staffeln sind bei AniList eigene Einträge.
        anilist_id = _provider_id(season, "AniList") or (_provider_id(series, "AniList") if number == 1 else None)
        mal_id = _provider_id(season, "MyAnimeList", "Mal") or (
            _provider_id(series, "MyAnimeList", "Mal") if number == 1 else None
        )
        return SourceSeason(
            key=f"jellyfin:{season_id}",
            source="jellyfin",
            source_id=season_id,
            title=series.get("Name") or season.get("SeriesName") or season_id,
            season_number=number,
            episodes=episodes,
            anilist_id=anilist_id,
            mal_id=mal_id,
        )

    # ------------------------------------------------------------ Phase 7: Musikbibliothek

    def music_libraries(self) -> list[dict[str, Any]]:
        """Bibliotheken vom Typ Musik (Dashboard > Bibliotheken)."""
        items = self._get("/Library/MediaFolders").get("Items", [])
        return [i for i in items if str(i.get("CollectionType") or "").lower() == "music"]

    def audio_items(self, parent_id: str | None = None, search: str | None = None, genre: str | None = None,
                    page: int = 500) -> list[dict[str, Any]]:
        """Alle Songs (Typ Audio), seitenweise abgeholt."""
        items: list[dict[str, Any]] = []
        while True:
            data = self._get("/Items", includeItemTypes="Audio", recursive="true", parentId=parent_id,
                             searchTerm=search, genres=genre, fields="Path,MediaSources,Genres",
                             sortBy="SortName", startIndex=len(items), limit=page)
            batch = data.get("Items", [])
            items += batch
            if not batch or len(items) >= int(data.get("TotalRecordCount") or 0):
                return items

    def music_tracks(self, download_dir: Path, library: str | None = None, search: str | None = None,
                     genre: str | None = None) -> list[LibraryTrack]:
        """Songs aus Jellyfin. Die Dateien werden erst geladen, wenn ein Song analysiert wird."""
        parent_id = None
        if library:
            libraries = self.music_libraries()
            found = [lib for lib in libraries if library.lower() in (str(lib.get("Name", "")).lower(), lib["Id"])]
            if not found:
                names = ", ".join(str(lib.get("Name")) for lib in libraries) or "keine"
                raise JellyfinError(f"Keine Musikbibliothek '{library}' in Jellyfin. Vorhanden: {names}")
            parent_id = found[0]["Id"]
        tracks = []
        for item in self.audio_items(parent_id, search, genre):
            artist = item.get("AlbumArtist") or ", ".join(item.get("Artists") or []) or None
            title = item.get("Name") or item["Id"]
            container = str(item.get("Container") or "").split(",")[0]
            suffix = Path(item.get("Path") or "").suffix or (f".{container}" if container else ".mp3")
            name = safe_filename(f"{artist} - {title}" if artist else title)
            target = download_dir / f"{name}_{item['Id'][:8]}{suffix.lower()}"
            ticks = item.get("RunTimeTicks")
            tracks.append(LibraryTrack(
                title=title, artist=artist, album=item.get("Album"), genres=list(item.get("Genres") or []),
                duration=ticks / 10_000_000 if ticks else None,  # Jellyfin zählt in 100-ns-Schritten
                local_path=target if target.exists() else None, source="jellyfin", source_id=item["Id"],
                fetch=partial(self.download, item["Id"], target, False), tagged=True,
            ))
        return tracks
