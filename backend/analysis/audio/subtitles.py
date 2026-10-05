"""Untertitel finden, lesen und einordnen (Geständnis, Streit, Witz ...).

Untertitel kommen entweder als Datei neben der Folge ("Folge.de.ass", "Folge.srt") oder als Spur in
der MKV/MP4. Schilder und Liedtexte fliegen raus. Jede Dialogzeile rechnet ein mehrsprachiges
Satz-Modell in ein Embedding um, das wir mit den Beispielsätzen in dialog_prompts.yaml vergleichen.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
import yaml

from backend.analysis.device import torch_device
from backend.analysis.mood import MOODS
from backend.config.settings import SubtitleSettings

log = logging.getLogger(__name__)

SIDECAR_EXTENSIONS = (".ass", ".ssa", ".srt", ".vtt")
TEXT_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "text"}
NEUTRAL = "neutral"


@dataclass(frozen=True)
class SubtitleLine:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SubtitleFile:
    path: Path
    label: str  # für Logs und 'status', z. B. "Folge.de.ass" oder "Spur 2 (ger)"


class DialogModel(Protocol):
    name: str

    def classify(self, texts: Sequence[str]) -> list[dict[str, float]]: ...


def _language_rank(tags: Sequence[str], languages: Sequence[str]) -> int:
    ranks = [languages.index(t) for t in tags if t in languages]
    return min(ranks) if ranks else len(languages)


def find_sidecar(video: Path, cfg: SubtitleSettings) -> Path | None:
    """Untertitel-Datei neben der Folge: gleicher Name, optional mit Sprache ("Folge.de.ass")."""
    skip = re.compile(cfg.skip_pattern)
    found: list[tuple[int, int, str, Path]] = []
    for path in video.parent.iterdir():
        suffix = path.suffix.lower()
        if suffix not in SIDECAR_EXTENSIONS or not path.name.startswith(video.stem) or not path.is_file():
            continue
        middle = path.name[len(video.stem):-len(suffix)]
        if middle and not middle.startswith("."):
            continue  # "Folge 10.srt" gehört nicht zu "Folge 1"
        tags = [t.lower() for t in middle.split(".") if t]
        if any(skip.search(t) for t in tags):
            continue
        found.append((_language_rank(tags, cfg.languages), SIDECAR_EXTENSIONS.index(suffix), path.name, path))
    return min(found)[3] if found else None


def embedded_streams(video: Path) -> list[dict]:
    """Text-Untertitelspuren der Datei (Bild-Untertitel wie PGS lassen sich nicht lesen)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream_disposition=forced",
         "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    ).stdout
    return [s for s in json.loads(out).get("streams") or [] if s.get("codec_name") in TEXT_CODECS]


def pick_stream(streams: Sequence[dict], cfg: SubtitleSettings) -> dict | None:
    skip = re.compile(cfg.skip_pattern)
    usable = []
    for order, s in enumerate(streams):
        tags = s.get("tags") or {}
        if (s.get("disposition") or {}).get("forced") or skip.search(str(tags.get("title", ""))):
            continue
        lang = str(tags.get("language", "")).lower()
        usable.append((_language_rank([lang], cfg.languages), order, s))
    return min(usable, key=lambda u: u[:2])[2] if usable else None


def extract_stream(video: Path, stream: dict, cache_dir: Path) -> Path:
    """Holt eine Untertitelspur einmal als Datei aus der Folge (danach aus dem Cache)."""
    stat = video.stat()
    key = hashlib.sha1(f"{video.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{stream['index']}".encode()).hexdigest()
    is_ass = stream.get("codec_name") in ("ass", "ssa")
    out = cache_dir / f"{video.stem}_{key[:12]}{'.ass' if is_ass else '.srt'}"
    if out.is_file():
        return out
    cache_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-map", f"0:{stream['index']}",
         "-c:s", "copy" if is_ass else "srt", str(out)],
        check=True, capture_output=True,
    )
    return out


def find_subtitles(video: Path, cfg: SubtitleSettings) -> SubtitleFile | None:
    sidecar = find_sidecar(video, cfg)
    if sidecar is not None:
        return SubtitleFile(sidecar, sidecar.name)
    stream = pick_stream(embedded_streams(video), cfg)
    if stream is None:
        return None
    lang = (stream.get("tags") or {}).get("language", "?")
    return SubtitleFile(extract_stream(video, stream, cfg.cache_dir), f"Spur {stream['index']} ({lang})")


def load_lines(path: Path, skip_pattern: str) -> list[SubtitleLine]:
    """Dialogzeilen ohne Schilder, Liedtexte und Formatierung."""
    import pysubs2

    try:
        subs = pysubs2.load(str(path), encoding="utf-8")
    except UnicodeDecodeError:
        subs = pysubs2.load(str(path), encoding="cp1252")  # ältere deutsche SRTs
    skip = re.compile(skip_pattern)
    lines: list[SubtitleLine] = []
    for event in subs:
        if event.is_comment or event.is_drawing or skip.search(event.style or ""):
            continue
        if "\\pos(" in event.text or "\\move(" in event.text:
            continue  # fest platzierter Text ist fast immer ein Schild
        text = " ".join(event.plaintext.split())
        if not text or text.startswith(("♪", "♫")) or re.fullmatch(r"[\[(].*[\])]", text):
            continue  # Liedtext oder Geräusch-Beschreibung wie [Musik]
        lines.append(SubtitleLine(event.start / 1000.0, event.end / 1000.0, text))
    return sorted(lines, key=lambda line: line.start)


def lines_in(lines: Sequence[SubtitleLine], start: float, end: float, min_overlap: float) -> list[tuple[SubtitleLine, float]]:
    """Zeilen, die sich mindestens min_overlap Sekunden (oder zur Hälfte) mit dem Clip überschneiden."""
    result = []
    for line in lines:
        if line.start >= end:
            break
        overlap = min(end, line.end) - max(start, line.start)
        if overlap > 0 and (overlap >= min_overlap or overlap >= 0.5 * (line.end - line.start)):
            result.append((line, overlap))
    return result


def average_tags(tags: Sequence[dict[str, float]], weights: Sequence[float]) -> dict[str, float] | None:
    if not tags or sum(weights) <= 0:
        return None
    total = sum(weights)
    groups = sorted({g for t in tags for g in t})
    return {g: round(sum(w * t.get(g, 0.0) for t, w in zip(tags, weights)) / total, 4) for g in groups}


def load_dialog_prompts(path: Path) -> tuple[list[str], list[str], float]:
    """(Sätze, Gruppe pro Satz, Temperatur) aus dialog_prompts.yaml."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    groups = raw.get("groups") or {}
    unknown = sorted(set(groups) - set(MOODS) - {NEUTRAL})
    if unknown:
        raise ValueError(f"{path.name}: unbekannte Gruppe {', '.join(unknown)} (erlaubt: {', '.join(MOODS)}, neutral)")
    texts, names = [], []
    for group, sentences in groups.items():
        for sentence in sentences or []:
            texts.append(str(sentence))
            names.append(str(group))
    if not texts:
        raise ValueError(f"{path.name} enthält keine Beispielsätze")
    return texts, names, float(raw.get("temperature", 0.05))


def classify_embeddings(
    lines: np.ndarray, examples: np.ndarray, groups: Sequence[str], temperature: float
) -> list[dict[str, float]]:
    """Pro Zeile: Ähnlichkeit zum besten Beispiel jeder Gruppe, per Softmax in Wahrscheinlichkeiten."""
    if len(lines) == 0:
        return []
    names = sorted(set(groups))
    sims = lines @ examples.T  # beide normiert -> Kosinus-Ähnlichkeit
    best = np.stack([sims[:, [i for i, g in enumerate(groups) if g == name]].max(axis=1) for name in names], axis=1)
    logits = best / max(temperature, 1e-6)
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(axis=1, keepdims=True)
    return [{name: round(float(p), 4) for name, p in zip(names, row)} for row in probs]


def sentence_model_installed() -> bool:
    return importlib.util.find_spec("sentence_transformers") is not None


class SentenceDialogModel:
    """Mehrsprachiges Satz-Modell (sentence-transformers), lädt beim ersten Mal ca. 470 MB."""

    def __init__(self, cfg: SubtitleSettings) -> None:
        from sentence_transformers import SentenceTransformer

        texts, self.groups, self.temperature = load_dialog_prompts(cfg.prompts)
        log.info("Lade Satz-Modell %s ...", cfg.model)
        self.model = SentenceTransformer(cfg.model, device=torch_device(cfg.device))
        self.examples = self._encode(texts)
        self.name = cfg.model

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self.model.encode(list(texts), batch_size=64, convert_to_numpy=True,
                                            normalize_embeddings=True, show_progress_bar=False))

    def classify(self, texts: Sequence[str]) -> list[dict[str, float]]:
        if not texts:
            return []
        return classify_embeddings(self._encode(texts), self.examples, self.groups, self.temperature)
