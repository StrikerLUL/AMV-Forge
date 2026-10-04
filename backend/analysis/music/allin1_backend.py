"""Optionale Song-Analyse mit allin1 (KI-Modell für Beats, Taktanfänge und Abschnitte).

allin1 trennt den Song zuerst in Gesang, Bass, Drums und Rest (Demucs) und erkennt dann mit einem
neuronalen Netz, wo Beats, Takte und Abschnitte (Verse, Chorus, ...) liegen. Das ist deutlich
genauer als der librosa-Fallback, braucht aber PyTorch und NATTEN, die unter Windows schwer zu
installieren sind. Fehlt es, nimmt AMV-Forge automatisch den Fallback.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# allin1 kennt diese Namen (Harmonix-Datensatz). Wir bilden sie auf unsere fünf ab.
LABEL_MAP = {
    "start": "intro",
    "intro": "intro",
    "verse": "verse",
    "chorus": "chorus",
    "bridge": "bridge",
    "break": "bridge",
    "inst": "verse",
    "solo": "verse",
    "outro": "outro",
    "end": "outro",
}


class Allin1Unavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Allin1Result:
    bpm: float
    beats: list[float]
    downbeats: list[float]
    sections: list[tuple[float, float, str, str]]  # (start, ende, unser Name, allin1-Name)


def is_installed() -> bool:
    """Schneller Check ohne Import (allin1 lädt beim Import PyTorch, das dauert)."""
    return importlib.util.find_spec("allin1") is not None


def analyze(song: Path, work_dir: Path) -> Allin1Result:
    try:
        import allin1
        import torch
    except Exception as exc:  # ImportError, aber auch DLL-Fehler unter Windows
        raise Allin1Unavailable(f"allin1 lässt sich nicht laden: {exc}") from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("allin1 analysiert %s auf %s (CUDA verfügbar: %s) ...", song.name, device.upper(),
             torch.cuda.is_available())
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = allin1.analyze(
            str(song),
            device=device,
            demix_dir=str(work_dir / "demix"),
            spec_dir=str(work_dir / "spec"),
            keep_byproducts=False,
            multiprocess=False,
        )
    except Exception as exc:  # z. B. Modell-Download fehlgeschlagen, CUDA-Fehler, kaputte Datei
        raise Allin1Unavailable(f"allin1 ist abgebrochen: {exc}") from exc
    sections = [
        (float(s.start), float(s.end), LABEL_MAP.get(s.label, "verse"), str(s.label))
        for s in result.segments
        if s.end - s.start > 0.05
    ]
    return Allin1Result(
        bpm=float(result.bpm),
        beats=[float(b) for b in result.beats],
        downbeats=[float(b) for b in result.downbeats],
        sections=sections,
    )
