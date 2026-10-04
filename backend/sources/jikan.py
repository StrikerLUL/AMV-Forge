"""Jikan (MyAnimeList): Episodentitel und Filler/Recap-Markierung."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.sources.http import ApiClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EpisodeMeta:
    number: int
    title: str | None
    filler: bool
    recap: bool


def episode_meta(client: ApiClient, mal_id: int, max_pages: int = 20) -> dict[int, EpisodeMeta]:
    """Holt alle Episoden-Infos (Jikan liefert 100 pro Seite)."""
    result: dict[int, EpisodeMeta] = {}
    for page in range(1, max_pages + 1):
        status, body = client.get_json(f"/anime/{mal_id}/episodes", params=[("page", str(page))])
        if status != 200 or not body:
            log.warning("Jikan: keine Episodenliste für MAL-ID %d", mal_id)
            break
        for ep in body.get("data", []):
            number = int(ep["mal_id"])
            result[number] = EpisodeMeta(
                number=number,
                title=ep.get("title"),
                filler=bool(ep.get("filler")),
                recap=bool(ep.get("recap")),
            )
        if not body.get("pagination", {}).get("has_next_page"):
            break
    return result
