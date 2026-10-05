"""Ganzer Index-Lauf mit zwei kleinen Testvideos und gefälschten APIs."""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.config import load_settings
from backend.db import get_engine
from backend.db.models import Character, Clip, Episode, Season
from backend.indexer import index_season, make_apis
from backend.mood_index import MoodModels
from backend.sources.folder import scan_folder
from tests.fake_apis import MAL_ID, S2_ID, FakeApis
from tests.fake_models import FakeClip, FakeDialog, FakeVad

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
    (folder / "Horimiya - 01.de.srt").write_text("1\n00:00:09,000 --> 00:00:10,500\nIch liebe dich.\n", encoding="utf-8")

    settings = load_settings()
    engine = get_engine(tmp_path / "test.sqlite")
    fake = FakeApis()
    source = scan_folder(folder, settings.index.video_extensions)
    fake_clip = FakeClip(settings.clip)
    models = MoodModels(settings, clip=fake_clip, vad=FakeVad([(8.5, 10.0)]), dialog=FakeDialog())

    first = index_season(source, settings, engine, make_apis(engine, settings.apis, fake.transport), models=models)
    assert first.scenes_computed == 2 and first.skips_computed == 2 and first.motion_computed == 2
    assert first.metadata_fetched and first.api_requests > 0
    m = first.mood
    assert (m.visual, m.tags, m.audio, m.subtitles, m.mood) == (2, 2, 2, 2, True)

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
        # Phase 3: jeder Clip hat Bewegung und einen Peak innerhalb des Clips
        for clip in session.exec(select(Clip)):
            assert clip.motion is not None and clip.motion_peak is not None
            assert clip.start <= clip.motion_peak <= clip.end
            # Phase 4: Vorschaubild, CLIP, Ton, Stimmung, Qualität
            assert clip.thumbnail is not None and Path(clip.thumbnail).is_file()
            assert clip.clip_tags is not None and clip.clip_top is not None
            assert clip.loudness is not None and clip.speech is not None
            assert clip.mood is not None and set(clip.mood) == {"romance", "action", "sad", "funny", "calm"}
            assert clip.quality is not None and clip.quality_issue is None  # testsrc ist bunt und scharf
        assert ep1.subtitle_source == "Horimiya - 01.de.srt" and ep2.subtitle_source == "keine"
        scene = session.exec(select(Clip).where(Clip.episode_id == ep1.id, Clip.start == 8.0)).one()
        assert scene.subtitle == "Ich liebe dich." and scene.dialog_tags and scene.dialog_tags["romance"] == 0.9
        assert scene.speech == round(1.5 / 7, 3) and scene.dialog is True

    fake.calls.clear()
    images = fake_clip.images_seen
    second = index_season(
        scan_folder(folder, settings.index.video_extensions), settings, engine,
        make_apis(engine, settings.apis, fake.transport), models=models,
    )
    assert not second.computed_anything
    assert fake.calls == []
    assert second.clips_total == first.clips_total

    # Neue Prompts: nur der CLIP-Vergleich und die Stimmung, die Folgen werden nicht neu dekodiert
    prompts = tmp_path / "prompts.yaml"
    prompts.write_text(settings.clip.prompts.read_text(encoding="utf-8").replace(
        '    - "an anime sunset"', '    - "an anime sunset"\n    - "an anime beach"'), encoding="utf-8")
    tuned = replace(settings, clip=replace(settings.clip, prompts=prompts))
    third = index_season(scan_folder(folder, settings.index.video_extensions), tuned, engine, None,
                         models=MoodModels(tuned, clip=fake_clip, vad=FakeVad([(8.5, 10.0)]), dialog=FakeDialog()))
    assert (third.mood.visual, third.mood.tags, third.mood.audio, third.mood.subtitles) == (0, 2, 0, 0)
    assert third.mood.mood and fake_clip.images_seen == images

    # Falsches Anime korrigiert: neue AniList-ID -> neue MAL-ID -> OP/ED wird neu gesucht
    fourth = index_season(
        scan_folder(folder, settings.index.video_extensions), settings, engine,
        make_apis(engine, settings.apis, fake.transport), anilist_override=S2_ID, models=models,
    )
    assert fourth.skips_computed == 2
    with Session(engine) as session:
        assert session.exec(select(Season)).one().mal_id == 54856
