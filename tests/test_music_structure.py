"""Song-Analyse mit dem librosa-Fallback auf einem künstlichen Song mit bekannter Struktur."""

from pathlib import Path

import pytest

from backend.analysis.music import allin1_backend
from backend.analysis.music.beats import bpm_from_beats, pick_downbeat_phase
from backend.analysis.music.structure import SongAnalysis, analyze_song, resolve_analyzer
from backend.config import load_settings
from tests.synth_song import BAR, BEAT, BPM, DROP_TIME


@pytest.fixture(scope="module")
def analysis(synth_song: Path) -> SongAnalysis:
    return analyze_song(synth_song, load_settings().music, "librosa")


def test_tempo_and_beats(analysis: SongAnalysis) -> None:
    assert analysis.analyzer == "librosa"
    assert analysis.bpm == pytest.approx(BPM, abs=1.5)
    # Beats liegen höchstens 60 ms neben dem echten Raster
    off_grid = [abs(b / BEAT - round(b / BEAT)) * BEAT for b in analysis.beats]
    assert max(off_grid) < 0.06


def test_downbeats_are_bar_starts(analysis: SongAnalysis) -> None:
    on_bar = [abs(d / BAR - round(d / BAR)) < 0.1 for d in analysis.downbeats]
    assert sum(on_bar) >= 0.9 * len(on_bar)


def test_drop_is_found(analysis: SongAnalysis) -> None:
    assert analysis.drops, "kein Drop gefunden"
    main = max(analysis.drops, key=lambda d: d.strength)
    assert main.time == pytest.approx(DROP_TIME, abs=BEAT)


def test_sections(analysis: SongAnalysis) -> None:
    assert analysis.sections[0].label == "intro"
    in_drop = analysis.section_at(DROP_TIME + 2 * BAR)
    assert in_drop is not None and in_drop.label == "chorus"
    assert in_drop.start == pytest.approx(DROP_TIME, abs=BEAT)
    verse = analysis.section_at(12 * BAR)
    assert verse is not None and verse.label == "verse"
    assert analysis.sections[-1].end == pytest.approx(analysis.duration)
    for a, b in zip(analysis.sections, analysis.sections[1:]):
        assert a.end == pytest.approx(b.start)


def test_roundtrip_through_dict(analysis: SongAnalysis) -> None:
    assert SongAnalysis.from_dict(analysis.to_dict()) == analysis


def test_resolve_analyzer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(allin1_backend, "is_installed", lambda: False)
    assert resolve_analyzer("auto") == "librosa"
    assert resolve_analyzer("librosa") == "librosa"
    with pytest.raises(RuntimeError):
        resolve_analyzer("allin1")
    with pytest.raises(ValueError):
        resolve_analyzer("spotify")
    monkeypatch.setattr(allin1_backend, "is_installed", lambda: True)
    assert resolve_analyzer("auto") == "allin1"


def test_broken_allin1_falls_back_in_auto_mode(synth_song: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> None:
        raise allin1_backend.Allin1Unavailable("allin1 lässt sich nicht laden: natten fehlt")

    monkeypatch.setattr(allin1_backend, "is_installed", lambda: True)
    monkeypatch.setattr(allin1_backend, "analyze", broken)
    result = analyze_song(synth_song, load_settings().music, "auto")
    assert result.analyzer == "librosa" and "natten" in result.notes[0]
    with pytest.raises(RuntimeError):
        analyze_song(synth_song, load_settings().music, "allin1")


def test_bpm_from_beats_and_phase() -> None:
    assert bpm_from_beats([i * 0.5 for i in range(20)]) == pytest.approx(120.0)
    import numpy as np

    change = np.array([0.0, 0.0, 1.0, 0.0] * 8)  # Akkordwechsel immer auf dem dritten Beat
    bass = np.array([0.1, 0.0, 0.9, 0.0] * 8)
    assert pick_downbeat_phase(change, bass, 4) == 2
