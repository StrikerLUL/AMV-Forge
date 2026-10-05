"""Kontaktbogen: viele Vorschaubilder als Raster in einem JPG, jedes mit kurzer Beschriftung."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from backend.analysis.video.keyframes import load_jpeg, save_jpeg

LABEL_HEIGHT = 22


@dataclass(frozen=True)
class Tile:
    image: Path | None
    label: str  # nur ASCII, cv2 kann keine Umlaute zeichnen


def contact_sheet(tiles: Sequence[Tile], out: Path, columns: int = 4, tile_height: int = 180) -> Path:
    tile_width = int(round(tile_height * 16 / 9))
    rows = max(1, -(-len(tiles) // columns))
    sheet = np.full((rows * (tile_height + LABEL_HEIGHT), columns * tile_width, 3), 24, dtype=np.uint8)
    for i, tile in enumerate(tiles):
        y, x = (i // columns) * (tile_height + LABEL_HEIGHT), (i % columns) * tile_width
        image = load_jpeg(tile.image) if tile.image else None
        if image is not None:
            sheet[y:y + tile_height, x:x + tile_width] = cv2.resize(image, (tile_width, tile_height),
                                                                    interpolation=cv2.INTER_AREA)
        cv2.putText(sheet, tile.label.encode("ascii", "replace").decode(), (x + 4, y + tile_height + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (235, 235, 235), 1, cv2.LINE_AA)
    save_jpeg(sheet, out, 85)
    return out
