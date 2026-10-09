"""Gemeinsame Test-Bausteine."""

from pathlib import Path

import pytest

from tests.synth_song import make_song


@pytest.fixture(scope="session")
def synth_song(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Künstlicher EDM-Song mit bekannter Struktur (siehe tests/synth_song.py), einmal pro Testlauf."""
    return make_song(tmp_path_factory.mktemp("song") / "synth.wav")


@pytest.fixture(autouse=True)
def no_model_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests laden nie echte Modelle aus dem Internet. Wer eins braucht, setzt einen Ersatz aus fake_models.py ein."""

    def blocked(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("In Tests werden keine Modelle heruntergeladen")

    for name in ("OpenClipModel", "SentenceDialogModel", "YoloFaceDetector", "ClapMusicModel"):
        monkeypatch.setattr(f"backend.analysis.loader.{name}", blocked)
