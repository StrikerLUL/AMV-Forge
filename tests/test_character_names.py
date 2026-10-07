"""--characters "Hori,Miyamura" findet die richtigen Figuren."""

from dataclasses import dataclass

import pytest

from backend.analysis.character_names import (
    find_characters,
    name_score,
    normalize_name,
    pick_characters,
    slug,
    split_names,
)


@dataclass
class Person:
    name: str
    role: str


CAST = [
    Person("Kyouko Hori", "MAIN"),
    Person("Izumi Miyamura", "MAIN"),
    Person("Souta Hori", "SUPPORTING"),
    Person("Kyousuke Hori", "SUPPORTING"),
    Person("Yuki Yoshikawa", "SUPPORTING"),
    Person("Tōru Ishikawa", "SUPPORTING"),
]


def test_normalize_name() -> None:
    assert normalize_name("  Tōru  ISHIKAWA!") == "toru ishikawa"
    assert normalize_name("Kyōko-Hori") == "kyoko hori"


def test_name_score() -> None:
    assert name_score("kyouko hori", "Kyouko Hori") == 3
    assert name_score("Hori", "Kyouko Hori") == 2
    assert name_score("Miya", "Izumi Miyamura") == 1
    assert name_score("Toru", "Tōru Ishikawa") == 2
    assert name_score("Sakura", "Izumi Miyamura") == 0


def test_main_character_wins_when_the_family_name_is_shared() -> None:
    hits = find_characters("Hori", CAST)
    assert [h.name for h in hits] == ["Kyouko Hori", "Souta Hori", "Kyousuke Hori"]
    assert find_characters("Souta", CAST)[0].name == "Souta Hori"


def test_pick_characters() -> None:
    picked = pick_characters("Hori, Miyamura", CAST)
    assert [p.name for p in picked] == ["Kyouko Hori", "Izumi Miyamura"]
    with pytest.raises(ValueError, match="Keine Figur \"Sakura\""):
        pick_characters("Hori,Sakura", CAST)
    with pytest.raises(ValueError, match="wieder Kyouko Hori"):
        pick_characters("Hori,Kyouko", CAST)
    with pytest.raises(ValueError, match="Keine Namen"):
        pick_characters(" , ", CAST)


def test_split_names_and_slug() -> None:
    assert split_names("Hori,  Miyamura ,") == ["Hori", "Miyamura"]
    assert slug("Kyōko Hori") == "kyoko_hori"
    assert slug("???") == "figur"
