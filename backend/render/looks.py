"""Farblooks als 3D-LUT (.cube), angewendet mit dem ffmpeg-Filter lut3d (Phase 6).

Eine 3D-LUT (Look-Up-Table) ist eine Tabelle: Für ein Raster von Farben (hier 17x17x17) steht drin, welche
Farbe daraus werden soll. ffmpeg rechnet für jeden Pixel zwischen den nächsten Rasterpunkten. So passt jeder
Look in einen einzigen Filter, egal wie viele Schritte (Wärme, Sättigung, Kontrast ...) er hat. Dasselbe
Format (.cube) nutzt auch DaVinci Resolve, eigene LUTs von dort funktionieren deshalb auch.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import numpy as np

from backend.config.settings import FxSettings, project_file

PARAMS = ("warmth", "tint", "saturation", "contrast", "brightness", "lift")
NO_LOOK = ("", "none", "aus")


def apply_look(rgb: np.ndarray, params: dict[str, float]) -> np.ndarray:
    """Wendet einen Look auf Farben an (Werte 0-1, letzte Achse = R, G, B)."""
    unknown = sorted(set(params) - set(PARAMS))
    if unknown:
        raise ValueError(f"Unbekannter Look-Parameter {', '.join(unknown)} (erlaubt: {', '.join(PARAMS)})")
    warmth, tint = params.get("warmth", 0.0), params.get("tint", 0.0)
    out = rgb.astype(np.float64) * np.array([1 + 0.15 * warmth + 0.05 * tint, 1 + 0.03 * warmth - 0.1 * tint,
                                             1 - 0.15 * warmth + 0.05 * tint])
    luma = out @ np.array([0.2126, 0.7152, 0.0722])
    out = luma[..., None] + (out - luma[..., None]) * params.get("saturation", 1.0)
    out = 0.5 + (out - 0.5) * params.get("contrast", 1.0) + params.get("brightness", 0.0)
    lift = params.get("lift", 0.0)
    out = lift + np.clip(out, 0.0, 1.0) * (1.0 - lift)
    return np.clip(out, 0.0, 1.0)


def cube_text(params: dict[str, float], size: int, title: str) -> str:
    """Die LUT im .cube-Format: Rot ändert sich am schnellsten, dann Grün, dann Blau."""
    grid = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    colors = apply_look(np.stack([r, g, b], axis=-1).reshape(-1, 3), params)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{c[0]:.5f} {c[1]:.5f} {c[2]:.5f}" for c in colors]
    return "\n".join(lines) + "\n"


def _safe(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_") or "look"


def resolve_look(look: str | None, cfg: FxSettings) -> Path | None:
    """Name aus fx.looks oder eigene .cube-Datei -> .cube-Datei in cfg.cache_dir (None = kein Look).

    Eigene Dateien werden dorthin kopiert, damit der Pfad für ffmpeg nie Sonderzeichen enthält.
    """
    if look is None or look.strip().lower() in NO_LOOK:
        return None
    if look in cfg.looks:
        params = cfg.looks[look]
        text = cube_text(params, cfg.lut_size, look)
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        path = cfg.cache_dir / f"{_safe(look)}_{key}.cube"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return path
    source = project_file(look)
    if source.suffix.lower() == ".cube" and source.is_file():
        data = source.read_bytes()
        path = cfg.cache_dir / f"{_safe(source.stem)}_{hashlib.sha1(data).hexdigest()[:10]}.cube"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        return path
    raise ValueError(f"Look '{look}' gibt es nicht. Vorhanden: {', '.join(sorted(cfg.looks))} "
                     "oder der Pfad zu einer .cube-Datei")


def filter_path(path: Path) -> str:
    """Pfad so geschrieben, dass er in einem ffmpeg-Filter funktioniert (auch C:/... unter Windows)."""
    text = path.as_posix()
    if "'" in text:
        raise ValueError(f"Pfade mit ' gehen in ffmpeg-Filtern nicht: {text}")
    return "'" + text.replace(":", "\\:") + "'"
