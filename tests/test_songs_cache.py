"""Ein Song wird nur einmal analysiert, danach kommt alles aus der Datenbank."""

from dataclasses import replace
from pathlib import Path

from backend.analysis.music.energy import Drop
from backend.analysis.music.structure import Section, SongAnalysis
from backend.config import load_settings
from backend.config.settings import MusicSettings
from backend.db import get_engine
from backend.songs import load_song


class FakeAnalyzer:
    def __init__(self, name: str = "librosa") -> None:
        self.name = name
        self.calls = 0

    def __call__(self, path: Path, cfg: MusicSettings, analyzer: str) -> SongAnalysis:
        self.calls += 1
        return SongAnalysis(
            path=str(path), analyzer=self.name if analyzer == "auto" else analyzer, duration=60.0, bpm=120.0,
            beats=[i * 0.5 for i in range(120)], downbeats=[i * 2.0 for i in range(30)],
            sections=[Section(0.0, 60.0, "verse", 0.5)], drops=[Drop(30.0, 0.4)], energy=[0.5] * 600,
            energy_rate=10.0,
        )


def test_second_call_comes_from_the_database(tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x" * 100)
    engine = get_engine(tmp_path / "db.sqlite")
    cfg = load_settings().music
    fake = FakeAnalyzer()

    first = load_song(engine, song, cfg, analyze=fake)
    second = load_song(engine, song, cfg, analyze=fake)
    assert fake.calls == 1
    assert first == second and second.drops == [Drop(30.0, 0.4)]

    # Andere Einstellungen -> neu analysieren
    load_song(engine, song, replace(cfg, drop_min_jump=0.3), analyze=fake)
    assert fake.calls == 2
    # Datei geändert -> neu analysieren
    song.write_bytes(b"y" * 200)
    load_song(engine, song, replace(cfg, drop_min_jump=0.3), analyze=fake)
    assert fake.calls == 3
    # --force
    load_song(engine, song, replace(cfg, drop_min_jump=0.3), force=True, analyze=fake)
    assert fake.calls == 4


def test_explicit_analyzer_reanalyzes_if_stored_with_another(tmp_path: Path) -> None:
    song = tmp_path / "song.wav"
    song.write_bytes(b"x" * 10)
    engine = get_engine(tmp_path / "db.sqlite")
    cfg = load_settings().music
    fake = FakeAnalyzer("librosa")

    load_song(engine, song, cfg, "auto", analyze=fake)
    load_song(engine, song, cfg, "librosa", analyze=fake)
    assert fake.calls == 1
    result = load_song(engine, song, cfg, "allin1", analyze=fake)
    assert fake.calls == 2 and result.analyzer == "allin1"
    load_song(engine, song, cfg, "auto", analyze=fake)  # auto nimmt, was da ist
    assert fake.calls == 2
