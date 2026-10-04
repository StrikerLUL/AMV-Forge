"""Ganzer Index-Lauf mit zwei kleinen Testvideos und gefälschten APIs."""

import shutil
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.config import load_settings
from backend.db import get_engine
from backend.db.models import Character, Clip, Episode, Season
from backend.indexer import index_season, make_apis
from backend.sources.folder import scan_folder
from tests.fake_apis import MAL_ID, S2_ID, FakeApis

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def _make_episode(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=12:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def test_index_twice_computes_nothing_the_second_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # Szenen-Cache landet im Testordner
    folder = tmp_path / "Horimiya"
    folder.mkdir()
    for n in (1, 2):
        _make_episode(folder / f"Horimiya - {n:02d}.mkv")

    settings = load_settings()
    engine = get_engine(tmp_path / "test.sqlite")
    fake = FakeApis()
    source = scan_folder(folder, settings.index.video_extensions)

    first = index_season(source, settings, engine, make_apis(engine, settings.apis, fake.transport))
    assert first.scenes_computed == 2 and first.skips_computed == 2
    assert first.metadata_fetched and first.api_requests > 0

    with Session(engine) as session:
        season = session.exec(select(Season)).one()
        assert season.mal_id == MAL_ID and "Romance" in season.genres
        assert len(session.exec(select(Character)).all()) == 2
        ep1 = session.exec(select(Episode).where(Episode.number == 1)).one()
        ep2 = session.exec(select(Episode).where(Episode.number == 2)).one()
        assert ep1.skips_source == "aniskip" and ep2.filler
        clips = [(round(c.start, 1), round(c.end, 1))
                 for c in session.exec(select(Clip).where(Clip.episode_id == ep1.id).order_by(Clip.start))]
        # testsrc hat keinen Schnitt: eine Szene 0-20 s, OP 2-8 s und ED 15-19 s fliegen raus
        assert clips == [(0.0, 2.0), (8.0, 15.0), (19.0, 20.0)]

    fake.calls.clear()
    second = index_season(
        scan_folder(folder, settings.index.video_extensions), settings, engine,
        make_apis(engine, settings.apis, fake.transport),
    )
    assert not second.computed_anything
    assert fake.calls == []
    assert second.clips_total == first.clips_total

    # Falsches Anime korrigiert: neue AniList-ID -> neue MAL-ID -> OP/ED wird neu gesucht
    third = index_season(
        scan_folder(folder, settings.index.video_extensions), settings, engine,
        make_apis(engine, settings.apis, fake.transport), anilist_override=S2_ID,
    )
    assert third.skips_computed == 2
    with Session(engine) as session:
        assert session.exec(select(Season)).one().mal_id == 54856
