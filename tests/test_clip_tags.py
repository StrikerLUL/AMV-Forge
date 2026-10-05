"""CLIP-Zero-Shot: Prompts laden, Ausschnitte, Wahrscheinlichkeiten pro Gruppe."""

from pathlib import Path

import numpy as np
import pytest

from backend.analysis.mood import MOODS
from backend.analysis.video.clip_tags import NEUTRAL, crops, load_prompts, zero_shot
from backend.config import load_settings
from tests.fake_models import FakeClip

SETTINGS = load_settings()


def test_prompt_file_covers_every_mood() -> None:
    prompts = load_prompts(SETTINGS.clip.prompts)
    assert set(MOODS) <= set(prompts.groups)
    assert NEUTRAL in prompts.groups
    assert set(prompts.quality_groups) == {"text", "blurry"}
    assert len(prompts.texts) == len(prompts.groups) == len(set(prompts.texts))


def test_unknown_mood_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "prompts.yaml"
    bad.write_text("moods:\n  kitsch:\n    - pink hearts\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kitsch"):
        load_prompts(bad)


def test_sides_crop_covers_the_whole_wide_frame() -> None:
    frame = np.zeros((224, 398, 3), dtype=np.uint8)
    frame[:, :10] = 255  # Figur ganz links
    frame[:, -10:] = 128  # Figur ganz rechts
    left, right = crops(frame, "sides")
    assert left.shape == right.shape == (224, 224, 3)
    assert left[:, :10].mean() == 255 and right[:, -10:].mean() == 128
    (center,) = crops(frame, "center")
    assert center.shape == (224, 224, 3) and center.max() == 0
    (pad,) = crops(frame, "pad")
    assert pad.shape == (398, 398, 3)
    with pytest.raises(ValueError):
        crops(frame, "zoom")


def test_zero_shot_groups_sum_to_one_and_pick_the_right_mood() -> None:
    model = FakeClip(SETTINGS.clip)
    prompts = load_prompts(SETTINGS.clip.prompts)
    red = np.zeros((90, 160, 3), dtype=np.uint8)
    red[..., 0] = 220
    blue = np.zeros((90, 160, 3), dtype=np.uint8)
    blue[..., 2] = 220
    tags = zero_shot(model.embed_images([red, blue]), model.embed_texts(prompts.texts), prompts, model.logit_scale)
    assert sum(tags[0].probs.values()) == pytest.approx(1.0, abs=1e-3)
    assert max(tags[0].probs, key=tags[0].probs.get) == "romance"
    assert max(tags[1].probs, key=tags[1].probs.get) == "action"
    assert tags[0].top_prompt in prompts.texts
    assert zero_shot(np.zeros((0, 7)), model.embed_texts(prompts.texts), prompts, 20.0) == []
