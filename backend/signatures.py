"""Fingerabdrücke (Signaturen): Woran merkt der Index, dass etwas schon mit diesen Einstellungen berechnet ist?"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def signature(*parts: object) -> str:
    raw = json.dumps([str(p) for p in parts], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def content_signature(path: Path, chunk: int = 1 << 16) -> str:
    """Fingerabdruck des Inhalts (Größe, Anfang und Ende der Datei): erkennt Kopien unter anderem Namen."""
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode("utf-8"))
    with path.open("rb") as f:
        digest.update(f.read(chunk))
        if size > chunk:
            f.seek(max(chunk, size - chunk))
            digest.update(f.read(chunk))
    return digest.hexdigest()[:16]
