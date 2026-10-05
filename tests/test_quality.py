"""Bildqualität: Schwarz, Weiß, einfarbig, Text, Unschärfe."""

import numpy as np

from backend.analysis.video.quality import ClipStats, clip_quality, combine_stats, frame_stats
from backend.config import load_settings

CFG = load_settings().quality
GOOD = ClipStats(brightness_min=0.3, brightness_max=0.6, contrast=0.2, sharpness=150.0)


def _image(value: int, noise: bool = False) -> np.ndarray:
    image = np.full((90, 160, 3), value, dtype=np.uint8)
    if noise:
        rng = np.random.default_rng(0)
        image = rng.integers(0, 255, size=image.shape, dtype=np.uint8)
    return image


def test_frame_stats() -> None:
    black = frame_stats(_image(0))
    assert black.brightness == 0.0 and black.contrast == 0.0 and black.sharpness == 0.0
    busy = frame_stats(_image(0, noise=True))
    assert 0.4 < busy.brightness < 0.6 and busy.contrast > 0.1 and busy.sharpness > 1000


def test_clip_is_only_black_if_every_frame_is_dark() -> None:
    fade_in = combine_stats([frame_stats(_image(0)), frame_stats(_image(0, noise=True))])
    assert fade_in is not None and clip_quality(fade_in, fade_in.sharpness, {}, CFG).issue is None
    black = combine_stats([frame_stats(_image(3)), frame_stats(_image(5))])
    assert clip_quality(black, 100.0, {}, CFG).issue == "schwarz"


def test_white_and_flat_frames() -> None:
    assert clip_quality(combine_stats([frame_stats(_image(252))]), 100.0, {}, CFG).issue == "weiß"
    assert clip_quality(combine_stats([frame_stats(_image(120))]), 100.0, {}, CFG).issue == "einfarbig"


def test_clip_text_and_blur() -> None:
    assert clip_quality(GOOD, 150.0, {"text": 0.8, "blurry": 0.1}, CFG).issue == "text"
    assert clip_quality(GOOD, 150.0, {"blurry": 0.7}, CFG).issue == "blurry"
    assert clip_quality(GOOD, 1000.0, {}, CFG).issue == "unscharf"  # viel weicher als der Rest der Staffel
    good = clip_quality(GOOD, 150.0, {"text": 0.05}, CFG)
    assert good.issue is None and CFG.min_score < good.score < 1.0
    assert clip_quality(GOOD, 150.0, {}, CFG).score == 1.0


def test_unknown_stats_are_fine() -> None:
    assert clip_quality(None, None, {}, CFG).score == 1.0
