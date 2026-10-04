"""AniList GraphQL: Titel, MAL-ID, Genres, Tags und Charaktere einer Staffel."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from backend.sources.http import ApiClient, ApiError

log = logging.getLogger(__name__)

SEASON_FORMATS = {"TV", "TV_SHORT", "ONA"}

MEDIA_FIELDS = """
  id
  idMal
  format
  episodes
  title { romaji english native }
  synonyms
  genres
  tags { name rank }
  relations { edges { relationType node { id format type } } }
  characters(sort: [ROLE, RELEVANCE], perPage: 25) {
    edges { role node { id name { full } image { large } } }
  }
"""

SEARCH_QUERY = (
    "query ($search: String) { Page(perPage: 10) { media(search: $search, type: ANIME, sort: SEARCH_MATCH) {"
    + MEDIA_FIELDS
    + "} } }"
)
BY_ID_QUERY = "query ($id: Int) { Media(id: $id, type: ANIME) {" + MEDIA_FIELDS + "} }"


@dataclass(frozen=True)
class CharacterInfo:
    anilist_id: int
    name: str
    role: str
    image_url: str | None


@dataclass(frozen=True)
class AnimeInfo:
    anilist_id: int
    mal_id: int | None
    title: str
    episodes: int | None
    genres: list[str] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    characters: list[CharacterInfo] = field(default_factory=list)
    sequel_id: int | None = None
    all_titles: list[str] = field(default_factory=list)


def _parse_media(media: dict[str, Any]) -> AnimeInfo:
    title = media["title"].get("english") or media["title"].get("romaji") or str(media["id"])
    sequel = next(
        (
            e["node"]["id"]
            for e in media.get("relations", {}).get("edges", [])
            if e["relationType"] == "SEQUEL"
            and e["node"].get("type") == "ANIME"
            and e["node"].get("format") in SEASON_FORMATS
        ),
        None,
    )
    characters = [
        CharacterInfo(
            anilist_id=e["node"]["id"],
            name=e["node"]["name"]["full"],
            role=e["role"],
            image_url=(e["node"].get("image") or {}).get("large"),
        )
        for e in media.get("characters", {}).get("edges", [])
    ]
    return AnimeInfo(
        anilist_id=media["id"],
        mal_id=media.get("idMal"),
        title=title,
        episodes=media.get("episodes"),
        genres=list(media.get("genres") or []),
        tags=[{"name": t["name"], "rank": t["rank"]} for t in media.get("tags") or []],
        characters=characters,
        sequel_id=sequel,
        all_titles=[t for t in (*media["title"].values(), *(media.get("synonyms") or [])) if t],
    )


def get_anime(client: ApiClient, anilist_id: int) -> AnimeInfo:
    status, body = client.post_json("/", {"query": BY_ID_QUERY, "variables": {"id": anilist_id}})
    if status != 200 or not body or not body.get("data", {}).get("Media"):
        raise ApiError(f"AniList kennt die ID {anilist_id} nicht.")
    return _parse_media(body["data"]["Media"])


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def title_score(query: str, titles: list[str]) -> float:
    """Wie gut passt der Suchbegriff zu einem der Titel? 1.0 = gleich, 0 = gar nicht."""
    q = _normalize(query)
    best = 0.0
    for title in titles:
        t = _normalize(title)
        if not q or not t:
            continue
        score = SequenceMatcher(None, q, t).ratio()
        # "Kaguya-sama" passt zu "Kaguya-sama: Love is War", aber "S1" nicht zu "Major S1"
        if len(q) >= 4 and set(q.split()) <= set(t.split()):
            score = max(score, 0.9)
        best = max(best, score)
    return best


def find_anime(client: ApiClient, title: str, season_number: int = 1, min_score: float = 0.75) -> AnimeInfo | None:
    """Sucht Staffel 1 per Titel und folgt dann den SEQUEL-Verknüpfungen bis zur gewünschten Staffel.

    Passt kein Treffer gut genug zum Suchbegriff, gibt es None statt eines falschen Animes.
    """
    status, body = client.post_json("/", {"query": SEARCH_QUERY, "variables": {"search": title}})
    results = (body or {}).get("data", {}).get("Page", {}).get("media", []) if status == 200 else []
    candidates = [_parse_media(m) for m in results if m.get("format") in SEASON_FORMATS]
    if not candidates:
        log.warning("AniList: nichts gefunden für '%s'", title)
        return None

    scored = sorted(((title_score(title, c.all_titles), i, c) for i, c in enumerate(candidates)),
                    key=lambda x: (-x[0], x[1]))
    score, _, info = scored[0]
    if score < min_score:
        log.warning("AniList: kein sicherer Treffer für '%s'. Kandidaten:", title)
        for cand_score, _, cand in scored[:5]:
            log.warning("    %s (AniList-ID %d, passt zu %d %%)", cand.title, cand.anilist_id, round(cand_score * 100))
        return None

    for step in range(1, season_number):
        if info.sequel_id is None:
            log.warning("AniList: '%s' hat keine Fortsetzung, Staffel %d nicht gefunden", info.title, step + 1)
            return None
        info = get_anime(client, info.sequel_id)
    return info
