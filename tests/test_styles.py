"""Stil-Profile in backend/styles/ sind gültig und unterscheiden sich wirklich."""

from pathlib import Path

import pytest

from backend.config import load_settings
from backend.config import styles
from backend.config.styles import available_styles, load_style

DEFAULTS = load_settings().planner.weights


def test_all_styles_load() -> None:
    assert {"romance", "hype", "sad", "funny"} <= set(available_styles())
    for name in available_styles():
        profile = load_style(name, DEFAULTS)
        assert profile.weights.mood > 0 and 0 < profile.pool <= 1
        assert max(profile.mood, key=profile.mood.get) == {"hype": "action"}.get(name, name)


def test_romance_and_hype_want_opposite_things() -> None:
    romance, hype = load_style("romance", DEFAULTS), load_style("hype", DEFAULTS)
    assert romance.mood["action"] < 0 < hype.mood["action"]
    assert hype.mood["romance"] < 0 < romance.mood["romance"]


def test_unknown_style_and_bad_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="romance"):
        load_style("kitsch", DEFAULTS)
    monkeypatch.setattr(styles, "STYLES_DIR", tmp_path)
    (tmp_path / "leer.yaml").write_text("mood:\n  action: -1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="positiv"):
        load_style("leer", DEFAULTS)
    (tmp_path / "tippfehler.yaml").write_text("mood:\n  romance: 1\nweights:\n  mod: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mod"):
        load_style("tippfehler", DEFAULTS)
