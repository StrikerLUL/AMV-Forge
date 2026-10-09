"""Stil-Profile in backend/styles/ sind gültig und unterscheiden sich wirklich."""

from pathlib import Path

import pytest

from backend.config import load_settings
from backend.config import styles
from backend.config.styles import available_styles, load_style

DEFAULTS = load_settings().planner.weights


def test_all_styles_load() -> None:
    assert {"romance", "hype", "sad", "funny", "story"} <= set(available_styles())
    for name in available_styles():
        profile = load_style(name, DEFAULTS)
        assert profile.weights.mood > 0 and 0 < profile.pool <= 1
        if name != "story":  # story will starke Momente jeder Art
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


def test_phase_6_profiles_look_different() -> None:
    romance, hype = load_style("romance", DEFAULTS), load_style("hype", DEFAULTS)
    assert romance.bpm == (70, 100) and hype.bpm == (140, 175)
    assert romance.cuts["beats_per_cut"]["chorus"] > hype.cuts["beats_per_cut"]["chorus"]
    assert romance.transitions.default == "crossfade" and hype.transitions.default == "cut"
    assert romance.effects.slowmo is not None and hype.effects.slowmo is None
    assert hype.effects.zoom_punch is not None and hype.effects.shake is not None
    assert romance.effects.look != hype.effects.look
    assert load_style("story", DEFAULTS).order == "chronological" and romance.order == "score"


def test_bad_phase_6_entries_are_explained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(styles, "STYLES_DIR", tmp_path)
    bad = {
        "tempo": "bpm: 90\n",
        "abschnitt": "cuts:\n  beats_per_cut: {refrain: 2}\n",
        "schnitt": "cuts:\n  dorp: 2\n",
        "uebergang": "transitions:\n  default: wischen\n",
        "effekt": "effects:\n  blitz: true\n",
        "reihenfolge": "order: random\n",
        "eintrag": "farben: warm\n",
    }
    for name, text in bad.items():
        (tmp_path / f"{name}.yaml").write_text("mood:\n  romance: 1\n" + text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_style(name, DEFAULTS)
