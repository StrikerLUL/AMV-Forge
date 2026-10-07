"""Phase 4 im Index, wenn ein Modell fehlt oder nicht lädt: weiter ohne dieses Signal, nichts doppelt rechnen."""

import shutil
from pathlib import Path

import pytest
from sqlmodel import Session, select

from backend.analysis.video import clip_tags
from backend.config import load_settings
from backend.db import get_engine
from backend.db.models import Clip
from backend.indexer import index_season
from backend.analysis.loader import ModelLoader
from backend.sources.folder import scan_folder
from tests.synth_video import BLUES, make_color_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def test_failed_clip_download_is_retried_without_decoding_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(clip_tags, "is_installed", lambda: True)
    attempts: list[int] = []

    def offline(cfg: object) -> object:
        attempts.append(1)
        raise RuntimeError("Failed to download weights (kein Internet)")

    monkeypatch.setattr("backend.analysis.loader.OpenClipModel", offline)
    folder = tmp_path / "Testanime"
    folder.mkdir()
    make_color_video(folder / "Testanime - 01.mkv", BLUES[:3], moving=True)
    settings = load_settings()
    engine = get_engine(tmp_path / "test.sqlite")

    first = index_season(scan_folder(folder, settings.index.video_extensions), settings, engine, None,
                         models=ModelLoader(settings, vad=None, dialog=None))
    assert first.mood.visual == 1 and first.mood.mood
    assert "konnte nicht geladen werden" in caplog.text
    with Session(engine) as session:
        clips = session.exec(select(Clip)).all()
        assert clips and all(c.thumbnail and c.clip_tags is None and c.mood for c in clips)

    second = index_season(scan_folder(folder, settings.index.video_extensions), settings, engine, None,
                          models=ModelLoader(settings, vad=None, dialog=None))
    assert len(attempts) == 2  # beim nächsten Lauf wird der Download erneut versucht ...
    assert not second.computed_anything  # ... aber die Folge nicht nochmal dekodiert
