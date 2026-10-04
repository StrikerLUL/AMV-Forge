"""Gemeinsame Test-Bausteine."""

from pathlib import Path

import pytest

from tests.synth_song import make_song


@pytest.fixture(scope="session")
def synth_song(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Künstlicher EDM-Song mit bekannter Struktur (siehe tests/synth_song.py), einmal pro Testlauf."""
    return make_song(tmp_path_factory.mktemp("song") / "synth.wav")
