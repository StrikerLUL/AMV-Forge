"""Song-Struktur: Beats, Taktanfänge, Abschnitte, Energiekurve und Drops in einem Ergebnis."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from backend.analysis.music import allin1_backend
from backend.analysis.music.beats import BeatGrid, bpm_from_beats, track_beats
from backend.analysis.music.energy import Drop, EnergyWeights, detect_drops, energy_curve, mean_energy
from backend.analysis.music.segments import SegmentSettings, segment_song
from backend.config.settings import MusicSettings

log = logging.getLogger(__name__)

SECTION_LABELS = ("intro", "verse", "chorus", "bridge", "outro")
# Hochzählen, wenn sich die Analyse ändert: gespeicherte Ergebnisse werden dann neu berechnet.
ANALYSIS_VERSION = 1


@dataclass(frozen=True)
class Section:
    start: float
    end: float
    label: str  # intro, verse, chorus, bridge, outro
    energy: float  # 0-1
    raw_label: str = ""  # Name, den der Analysator vergeben hat


@dataclass(frozen=True)
class SongAnalysis:
    """Alles, was der Schnitt-Planer über einen Song wissen muss. Zeiten in Sekunden."""

    path: str
    analyzer: str  # "allin1" oder "librosa"
    duration: float
    bpm: float
    beats: list[float]
    downbeats: list[float]
    sections: list[Section]
    drops: list[Drop]
    energy: list[float]
    energy_rate: float
    beats_per_bar: int = 4
    notes: list[str] = field(default_factory=list)

    @property
    def beat_seconds(self) -> float:
        return 60.0 / self.bpm if self.bpm > 0 else 0.5

    @property
    def bar_seconds(self) -> float:
        return self.beat_seconds * self.beats_per_bar

    def section_at(self, t: float) -> Section | None:
        for s in self.sections:
            if s.start <= t < s.end:
                return s
        return self.sections[-1] if self.sections and t >= self.sections[-1].end else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SongAnalysis":
        return SongAnalysis(
            **{
                **data,
                "sections": [Section(**s) for s in data["sections"]],
                "drops": [Drop(**d) for d in data["drops"]],
            }
        )


def resolve_analyzer(wanted: str) -> str:
    """auto -> allin1, wenn installiert, sonst librosa."""
    if wanted not in ("auto", "allin1", "librosa"):
        raise ValueError(f"Unbekannter Analysator '{wanted}'. Erlaubt: auto, allin1, librosa.")
    if wanted == "auto":
        return "allin1" if allin1_backend.is_installed() else "librosa"
    if wanted == "allin1" and not allin1_backend.is_installed():
        raise RuntimeError("allin1 ist nicht installiert. Anleitung in der README oder --analyzer librosa nehmen.")
    return wanted


def _sections_with_energy(
    raw: list[tuple[float, float, str, str]], curve: np.ndarray, rate: float, duration: float
) -> list[Section]:
    sections: list[Section] = []
    for start, end, label, raw_label in raw:
        start, end = max(0.0, start), min(duration, end)
        if end - start <= 0:
            continue
        sections.append(Section(round(start, 3), round(end, 3), label,
                                round(mean_energy(curve, rate, start, end), 3), raw_label))
    return sections


def analyze_song(path: Path, cfg: MusicSettings, analyzer: str = "auto") -> SongAnalysis:
    """Analysiert einen Song. analyzer: auto, allin1 oder librosa."""
    chosen = resolve_analyzer(analyzer)
    log.info("Analysiere %s (%s) ...", path.name, chosen)
    y, sr = librosa.load(str(path), sr=cfg.sample_rate, mono=True)
    duration = float(len(y) / sr)
    if duration < 5:
        raise ValueError(f"{path.name} ist nur {duration:.1f} s lang, zu kurz für ein Edit.")

    weights = EnergyWeights(**cfg.energy_weights)
    curve = energy_curve(y, sr, cfg.energy_rate, weights, cfg.energy_smooth_seconds)
    notes: list[str] = []

    raw_sections: list[tuple[float, float, str, str]] | None = None
    grid: BeatGrid | None = None
    if chosen == "allin1":
        try:
            result = allin1_backend.analyze(path, cfg.allin1_work_dir)
            grid = BeatGrid(result.bpm, result.beats, result.downbeats)
            raw_sections = result.sections
        except allin1_backend.Allin1Unavailable as exc:
            if analyzer == "allin1":
                raise RuntimeError(str(exc)) from exc
            log.warning("%s. Nehme den librosa-Fallback.", exc)
            notes.append(str(exc))
            chosen = "librosa"

    if grid is None:
        grid = track_beats(y, sr, cfg.beats_per_bar)
    if len(grid.beats) < 8:
        raise ValueError(f"In {path.name} wurden kaum Beats gefunden. Ist der Song sehr leise oder ohne Rhythmus?")
    downbeats = grid.downbeats or grid.beats[:: cfg.beats_per_bar]
    bpm = grid.bpm or bpm_from_beats(grid.beats)

    if raw_sections is None:
        seg_cfg = SegmentSettings(cfg.min_section_bars, cfg.novelty_bars, cfg.novelty_threshold,
                                  cfg.chorus_energy, cfg.calm_energy)
        raw_sections = [(a, b, label, label) for a, b, label in
                        segment_song(y, sr, downbeats, duration, curve, cfg.energy_rate, seg_cfg)]
    sections = _sections_with_energy(raw_sections, curve, cfg.energy_rate, duration)

    bar = 60.0 / bpm * cfg.beats_per_bar
    drops = detect_drops(
        curve, cfg.energy_rate, downbeats,
        window_seconds=cfg.drop_window_bars * bar,
        min_jump=cfg.drop_min_jump,
        min_level=cfg.drop_min_level,
        min_distance_seconds=cfg.drop_min_distance_bars * bar,
        max_drops=cfg.max_drops,
    )
    return SongAnalysis(
        path=str(path),
        analyzer=chosen,
        duration=round(duration, 3),
        bpm=round(bpm, 2),
        beats=[round(b, 4) for b in grid.beats],
        downbeats=[round(b, 4) for b in downbeats],
        sections=sections,
        drops=drops,
        energy=[round(float(v), 3) for v in curve],
        energy_rate=cfg.energy_rate,
        beats_per_bar=cfg.beats_per_bar,
        notes=notes,
    )
