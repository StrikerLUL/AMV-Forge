from pathlib import Path

import httpx
import pytest

from backend.db import get_engine
from backend.sources import anilist, aniskip, jikan
from backend.sources.http import ApiClient
from backend.sources.jellyfin import JellyfinClient
from tests.fake_apis import MAL_ID, S1_ID, S2_ID, FakeApis


@pytest.fixture()
def fake() -> FakeApis:
    return FakeApis()


def _client(tmp_path: Path, fake: FakeApis, service: str, url: str) -> ApiClient:
    return ApiClient(get_engine(tmp_path / "db.sqlite"), service, url, 0.0, transport=fake.transport)


def test_aniskip_picks_closest_episode_length(tmp_path: Path, fake: FakeApis) -> None:
    client = _client(tmp_path, fake, "aniskip", "https://api.aniskip.com")
    result = aniskip.skip_times(client, MAL_ID, 1, episode_duration=20.5)
    assert [(s.kind, s.start, s.end) for s in result] == [("op", 2.0, 8.0), ("ed", 15.0, 19.0)]


def test_aniskip_unknown_episode_is_empty_and_cached(tmp_path: Path, fake: FakeApis) -> None:
    client = _client(tmp_path, fake, "aniskip", "https://api.aniskip.com")
    assert aniskip.skip_times(client, MAL_ID, 5) == []
    assert aniskip.skip_times(client, MAL_ID, 5) == []
    assert len(fake.calls) == 1
    assert client.cache_hits == 1


def test_cache_survives_new_client(tmp_path: Path, fake: FakeApis) -> None:
    first = _client(tmp_path, fake, "jikan", "https://api.jikan.moe/v4")
    jikan.episode_meta(first, MAL_ID)
    second = _client(tmp_path, fake, "jikan", "https://api.jikan.moe/v4")
    meta = jikan.episode_meta(second, MAL_ID)
    assert meta[2].filler and not meta[1].filler
    assert second.network_requests == 0


def test_anilist_follows_sequel_for_season_two(tmp_path: Path, fake: FakeApis) -> None:
    client = _client(tmp_path, fake, "anilist", "https://graphql.anilist.co")
    s1 = anilist.find_anime(client, "Horimiya", 1)
    s2 = anilist.find_anime(client, "Horimiya", 2)
    assert s1 is not None and s1.anilist_id == S1_ID and s1.mal_id == MAL_ID
    assert s2 is not None and s2.anilist_id == S2_ID
    assert [c.name for c in s1.characters] == ["Kyouko Hori", "Izumi Miyamura"]


def test_retry_on_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.sources.http.time.sleep", lambda _s: None)
    answers = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"ok": True})])
    transport = httpx.MockTransport(lambda _r: next(answers))
    client = ApiClient(get_engine(tmp_path / "db.sqlite"), "jikan", "https://api.jikan.moe/v4", 0.0,
                       transport=transport)
    assert client.get_json("/x") == (200, {"ok": True})
    assert client.network_requests == 2


def test_jellyfin_load_season(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert 'Token="geheim"' in request.headers["Authorization"]
        path, ids = request.url.path, request.url.params.get("ids")
        if path == "/Items" and ids == "season1":
            return httpx.Response(200, json={"Items": [
                {"Id": "season1", "Type": "Season", "IndexNumber": 1, "SeriesId": "series1", "ProviderIds": {}}
            ]})
        if path == "/Items" and ids == "series1":
            return httpx.Response(200, json={"Items": [
                {"Id": "series1", "Type": "Series", "Name": "Horimiya", "ProviderIds": {"AniList": "124080"}}
            ]})
        if path == "/Shows/series1/Episodes":
            return httpx.Response(200, json={"Items": [
                {"Id": "ep2", "IndexNumber": 2, "Name": "Zwei", "Path": "/media/Horimiya/E02.mkv"},
                {"Id": "ep1", "IndexNumber": 1, "Name": "Eins", "Path": "/media/Horimiya/E01.mkv"},
            ]})
        if path == "/Items/ep1/Download":
            return httpx.Response(200, content=b"video")
        return httpx.Response(404)

    client = JellyfinClient("https://jf.example", "geheim", transport=httpx.MockTransport(handle))
    season = client.load_season("season1", tmp_path)
    assert season.title == "Horimiya" and season.anilist_id == 124080
    assert {e.number: e.title for e in season.episodes} == {1: "Eins", 2: "Zwei"}
    ep1 = next(e for e in season.episodes if e.number == 1)
    assert ep1.local_path is None and ep1.fetch is not None
    downloaded = ep1.fetch()
    assert downloaded.read_bytes() == b"video" and downloaded.suffix == ".mkv"
