"""Gefälschte API-Antworten für Tests, damit nichts ins Internet geht."""

from __future__ import annotations

import json
from typing import Any

import httpx

MAL_ID = 42897
S1_ID, S2_ID = 124080, 163132


def _media(anilist_id: int, mal_id: int, title: str, sequel: int | None) -> dict[str, Any]:
    edges = [{"relationType": "SEQUEL", "node": {"id": sequel, "format": "TV", "type": "ANIME"}}] if sequel else []
    return {
        "id": anilist_id,
        "idMal": mal_id,
        "format": "TV",
        "episodes": 2,
        "title": {"romaji": title, "english": title},
        "genres": ["Romance", "Slice of Life"],
        "tags": [{"name": "Couples", "rank": 90}],
        "relations": {"edges": edges},
        "characters": {
            "edges": [
                {"role": "MAIN", "node": {"id": 1, "name": {"full": "Kyouko Hori"}, "image": {"large": "https://x/h.png"}}},
                {"role": "MAIN", "node": {"id": 2, "name": {"full": "Izumi Miyamura"}, "image": {"large": "https://x/m.png"}}},
            ]
        },
    }


class FakeApis:
    """Ein httpx-Transport, der AniList, Jikan und AniSkip spielt und Anfragen zählt."""

    def __init__(self, images: dict[str, bytes] | None = None) -> None:
        self.calls: list[str] = []
        self.urls: list[str] = []
        self.images = images or {}  # Bilder der Figuren (Host "x", z. B. "/h.png")
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.url.host}{request.url.path}")
        self.urls.append(str(request.url))
        host, path = request.url.host, request.url.path
        if host == "graphql.anilist.co":
            body = json.loads(request.content)
            if body["variables"].get("search") == "S1":
                # So passiert bei Striker: Ordner "S1" -> AniList findet "Major S1"
                return httpx.Response(200, json={"data": {"Page": {"media": [_media(627, 627, "Major S1", None)]}}})
            if "search" in body["variables"]:
                media = [_media(S1_ID, MAL_ID, "Horimiya", S2_ID)]
                return httpx.Response(200, json={"data": {"Page": {"media": media}}})
            return httpx.Response(200, json={"data": {"Media": _media(S2_ID, 54856, "Horimiya: piece", None)}})
        if host == "api.jikan.moe":
            data = [
                {"mal_id": 1, "title": "A Tiny Happenstance", "filler": False, "recap": False},
                {"mal_id": 2, "title": "Filler Folge", "filler": True, "recap": False},
            ]
            return httpx.Response(200, json={"pagination": {"has_next_page": False}, "data": data})
        if host == "api.aniskip.com":
            episode = int(path.rstrip("/").split("/")[-1])
            if episode == 1:
                results = [
                    {"interval": {"startTime": 2.0, "endTime": 8.0}, "skipType": "op", "episodeLength": 20.0},
                    {"interval": {"startTime": 15.0, "endTime": 19.0}, "skipType": "ed", "episodeLength": 20.0},
                    {"interval": {"startTime": 1.0, "endTime": 7.0}, "skipType": "op", "episodeLength": 300.0},
                ]
                return httpx.Response(200, json={"found": True, "results": results})
            return httpx.Response(404, json={"found": False, "results": []})
        if host == "x" and path in self.images:
            return httpx.Response(200, content=self.images[path], headers={"Content-Type": "image/png"})
        return httpx.Response(500)
