"""Musikbibliothek (Phase 7): Ordner mit Tags, Jellyfin-Musik, zweiter Lauf ohne Neuberechnung."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from backend.analysis.loader import ModelLoader
from backend.config import load_settings
from backend.config.settings import Settings
from backend.db import get_engine
from backend.db.models import Song
from backend import music_index
from backend.music_index import index_library
from backend.sources.jellyfin import JellyfinClient, JellyfinError
from backend.sources.music import safe_filename, scan_music_folder, split_genres, title_from_filename, with_tags
from tests.fake_models import FakeClap
from tests.synth_song import make_ballad

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def _flac(source: Path, target: Path, **tags: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for key, value in tags.items() for arg in ("-metadata", f"{key}={value}")]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source), *meta, "-c:a", "flac", str(target)], check=True)
    return target


@pytest.fixture
def library(tmp_path: Path, synth_song: Path) -> Path:
    """Musik/Balladen/01 - slow.flac (mit Tags), Musik/Club/DJ Test - Drop Song.flac (ohne Tags),
    Musik/intro.flac (5 s, zu kurz), Musik/kaputt.mp3 (keine Audiodatei), Musik/cover.jpg."""
    folder = tmp_path / "Musik"
    (tmp_path / "src").mkdir()
    ballad = make_ballad(tmp_path / "src" / "ballad.wav")
    _flac(ballad, folder / "Balladen" / "01 - slow.flac", title="Slow Love", artist="Duo", genre="J-Pop; Ballad")
    _flac(synth_song, folder / "Club" / "DJ Test - Drop Song.flac")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    str(folder / "intro.flac")], check=True)
    (folder / "kaputt.mp3").write_bytes(b"das ist kein mp3")
    (folder / "cover.jpg").write_bytes(b"jpg")
    return folder


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.chdir(tmp_path)  # data/ (Datenbank, Cache) landet im Testordner
    return load_settings()


def _songs(settings: Settings) -> dict[str, Song]:
    with Session(get_engine(settings.database.path)) as session:
        return {row.title or "": row for row in session.exec(select(Song)).all()}


def test_title_from_filename() -> None:
    assert title_from_filename(Path("LiSA - Gurenge.mp3")) == ("Gurenge", "LiSA")
    assert title_from_filename(Path("03 Gurenge.mp3")) == ("Gurenge", None)
    assert title_from_filename(Path("07. Unravel.flac")) == ("Unravel", None)
    assert title_from_filename(Path("LiSA_-_Gurenge.mp3")) == ("Gurenge", "LiSA")
    assert title_from_filename(Path("2049.mp3")) == ("2049", None)


def test_genres_and_filenames() -> None:
    assert split_genres("J-Pop; Anime/Soundtrack") == ["J-Pop", "Anime", "Soundtrack"]
    assert split_genres(None) == []
    assert safe_filename('AC/DC: Back in Black?') == "AC_DC_ Back in Black_"
    assert safe_filename("...") == "song"


def test_scan_finds_songs_in_subfolders(library: Path) -> None:
    tracks = scan_music_folder(library, (".flac", ".mp3"))
    assert sorted(t.local_path.name for t in tracks if t.local_path) == [
        "01 - slow.flac", "DJ Test - Drop Song.flac", "intro.flac", "kaputt.mp3"]
    ballad = with_tags(next(t for t in tracks if t.title == "slow"))
    assert (ballad.title, ballad.artist, ballad.genres) == ("Slow Love", "Duo", ["J-Pop", "Ballad"])
    assert 14 * 4 * 60 / 72 <= ballad.duration <= 14 * 4 * 60 / 72 + 1.5  # 14 Takte plus Ausklang
    drop = with_tags(next(t for t in tracks if t.title == "Drop Song"))
    assert (drop.title, drop.artist) == ("Drop Song", "DJ Test")  # ohne Tags zählt der Dateiname
    with pytest.raises(ValueError, match="Keine Songs"):
        scan_music_folder(library / "Balladen", (".ogg",))


def test_library_is_analyzed_once(library: Path, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    clap = FakeClap(settings.music_mood)
    tracks = scan_music_folder(library, settings.music_library.audio_extensions)

    first = index_library(tracks, settings, engine, ModelLoader(settings, clap=clap), limit=1, analyzer="librosa")
    assert (first.found, first.analyzed, first.pending) == (4, 1, 1)
    assert (first.wrong_length, first.failed) == (1, 1)  # intro.flac zu kurz, kaputt.mp3 nicht lesbar

    second = index_library(tracks, settings, engine, ModelLoader(settings, clap=clap), analyzer="librosa")
    assert (second.analyzed, second.known, second.pending, second.failed_before) == (1, 1, 0, 1)

    songs = _songs(settings)
    ballad, drop = songs["Slow Love"], songs["Drop Song"]
    assert (ballad.artist, ballad.genres, ballad.source) == ("Duo", ["J-Pop", "Ballad"], "folder")
    assert ballad.musical_key == "a-Moll" and drop.artist == "DJ Test"
    assert ballad.mood["romance"] > ballad.mood["action"] and drop.mood["action"] > drop.mood["romance"]
    assert set(ballad.features["signals"]) == {"features", "clap"}
    assert ballad.features["clap_top"] in clap.group_of and ballad.features["arousal"] < drop.features["arousal"]
    assert drop.analysis["drops"]  # Struktur aus Phase 3 ist mit dabei

    heard = clap.windows_heard
    third = index_library(scan_music_folder(library, settings.music_library.audio_extensions), settings, engine,
                          ModelLoader(settings, clap=FakeClap(settings.music_mood)), analyzer="librosa")
    assert (third.known, third.analyzed, third.failed_before, third.wrong_length) == (2, 0, 1, 1)
    assert not third.computed_anything and clap.windows_heard == heard


def test_new_clap_model_only_recomputes_mood(library: Path, settings: Settings) -> None:
    engine = get_engine(settings.database.path)
    tracks = [t for t in scan_music_folder(library, (".flac",)) if t.title == "slow"]
    index_library(tracks, settings, engine, ModelLoader(settings, clap=None), analyzer="librosa")
    assert set(_songs(settings)["Slow Love"].features["signals"]) == {"features"}

    report = index_library(tracks, settings, engine, ModelLoader(settings, clap=FakeClap(settings.music_mood)),
                           analyzer="librosa")
    assert (report.analyzed, report.structure, report.mood) == (1, 0, 1)
    assert set(_songs(settings)["Slow Love"].features["signals"]) == {"features", "clap"}


def test_filters_and_failures_are_remembered(library: Path, settings: Settings,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    engine = get_engine(settings.database.path)
    models = ModelLoader(settings, clap=None)
    tracks = scan_music_folder(library, settings.music_library.audio_extensions)
    assert index_library(tracks, settings, engine, models, limit=0, search="nichts davon").filtered == 3
    by_genre = index_library(tracks, settings, engine, models, limit=0, genre="pop")  # nur "J-Pop; Ballad" passt
    assert (by_genre.filtered, by_genre.pending, by_genre.analyzed) == (2, 1, 0)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kein Rhythmus gefunden")

    original = music_index.analyze_one
    monkeypatch.setattr(music_index, "analyze_one", broken)
    report = index_library(tracks, settings, engine, models)
    assert (report.failed, report.analyzed) == (2, 0)
    assert len(json.loads((settings.music_library.download_dir / "failed.json").read_text(encoding="utf-8"))) == 3
    monkeypatch.setattr(music_index, "analyze_one", original)
    again = index_library(tracks, settings, engine, models)
    assert (again.failed_before, again.analyzed, again.computed_anything) == (3, 0, False)


def _jellyfin(song_bytes: bytes, requests: list[str]) -> JellyfinClient:
    items = [
        {"Id": "aaaa1111bbbb", "Name": "Slow Love", "AlbumArtist": "Duo", "Album": "Liebe", "Genres": ["J-Pop"],
         "RunTimeTicks": 466_700_000, "Path": "/music/Duo/slow.FLAC", "Container": "flac"},
        {"Id": "cccc2222dddd", "Name": "Jingle", "Artists": ["Sender"], "RunTimeTicks": 50_000_000,
         "Container": "mp3"},
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        assert 'Token="geheim"' in request.headers["Authorization"]
        path, params = request.url.path, request.url.params
        requests.append(path)
        if path == "/Library/MediaFolders":
            return httpx.Response(200, json={"Items": [{"Id": "lib1", "Name": "Musik", "CollectionType": "music"},
                                                       {"Id": "lib2", "Name": "Anime", "CollectionType": "tvshows"}]})
        if path == "/Items" and params.get("includeItemTypes") == "Audio":
            assert params.get("parentId") == "lib1" and params.get("recursive") == "true"
            start, limit = int(params["startIndex"]), int(params["limit"])
            return httpx.Response(200, json={"Items": items[start:start + limit], "TotalRecordCount": len(items)})
        if path == "/Items/aaaa1111bbbb/Download":
            return httpx.Response(200, content=song_bytes)
        return httpx.Response(404)

    return JellyfinClient("https://jf.example", "geheim", transport=httpx.MockTransport(handle))


def test_jellyfin_music_is_downloaded_once(tmp_path: Path, settings: Settings) -> None:
    (tmp_path / "src").mkdir()
    song = _flac(make_ballad(tmp_path / "src" / "ballad.wav"), tmp_path / "src" / "slow.flac")
    requests: list[str] = []
    client = _jellyfin(song.read_bytes(), requests)
    download_dir = settings.music_library.download_dir

    assert len(client.audio_items("lib1", page=1)) == 2  # seitenweise: zwei Anfragen mit je einem Song
    with pytest.raises(JellyfinError, match="Vorhanden: Musik"):
        client.music_tracks(download_dir, library="Hörbücher")
    tracks = client.music_tracks(download_dir, library="musik")
    slow = tracks[0]
    assert (slow.label, slow.album, slow.genres, slow.source_id) == ("Duo - Slow Love", "Liebe", ["J-Pop"],
                                                                     "aaaa1111bbbb")
    assert slow.duration == pytest.approx(46.67) and slow.local_path is None and slow.tagged
    assert tracks[1].label == "Sender - Jingle"

    engine = get_engine(settings.database.path)
    report = index_library(tracks, settings, engine, ModelLoader(settings, clap=None), analyzer="librosa")
    assert (report.downloads, report.analyzed, report.wrong_length) == (1, 1, 1)
    row = _songs(settings)["Slow Love"]
    assert (row.source, row.source_id, row.artist) == ("jellyfin", "aaaa1111bbbb", "Duo")
    assert Path(row.path).name == "Duo - Slow Love_aaaa1111.flac" and Path(row.path).parent == download_dir.resolve()

    again = index_library(client.music_tracks(download_dir, library="Musik"), settings, engine,
                          ModelLoader(settings, clap=None), analyzer="librosa")
    assert (again.known, again.downloads, again.analyzed) == (1, 0, 0)
    assert requests.count("/Items/aaaa1111bbbb/Download") == 1
