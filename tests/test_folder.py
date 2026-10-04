from pathlib import Path

import pytest

from backend.sources.folder import clean_title, parse_episode_number, parse_season_number, scan_folder


@pytest.mark.parametrize(
    ("name", "number"),
    [
        ("[SubsPlease] Horimiya - 03 (1080p) [ABCD1234].mkv", 3),
        ("Horimiya.S01E07.1080p.WEB.x264.mkv", 7),
        ("Horimiya Episode 12.mp4", 12),
        ("Horimiya_Folge_5.mkv", 5),
        ("Horimiya E09 [720p].mkv", 9),
        ("[Grp] Show - 11v2 [1080p].mkv", 11),
        ("04.mkv", 4),
        ("Horimiya - 01x02 - Unterschiedliche Gesichter.mp4", 2),
        ("Horimiya - 01x13 - Wenigstens diesen weiten Himmel ....mp4", 13),
        ("[Grp] Video 1920x1080 - 05.mkv", 5),
    ],
)
def test_parse_episode_number(name: str, number: int) -> None:
    assert parse_episode_number(name) == number


def test_parse_season_number() -> None:
    assert parse_season_number("Horimiya Season 2") == 2
    assert parse_season_number("Show.S03E01.mkv") == 3
    assert parse_season_number("Horimiya") is None
    assert parse_season_number("Horimiya - 02x05 - Titel.mp4") == 2
    assert parse_season_number("S1") == 1


def test_clean_title() -> None:
    assert clean_title("[Grp] Horimiya S01 (2021) [1080p]") == "Horimiya"
    assert clean_title("Kaguya-sama Season 2") == "Kaguya-sama"
    assert clean_title("S1") == ""


def test_season_folder_takes_title_from_parent(tmp_path: Path) -> None:
    folder = tmp_path / "Horimiya" / "S1"
    folder.mkdir(parents=True)
    for n in (1, 2):
        (folder / f"Horimiya - 01x{n:02d} - Titel {n}.mp4").write_bytes(b"x")
    season = scan_folder(folder, (".mp4",))
    assert season.title == "Horimiya"
    assert season.season_number == 1
    assert [e.number for e in season.episodes] == [1, 2]


def test_title_from_filename_when_folders_say_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "Staffel 2" / "Season 1"
    folder.mkdir(parents=True)
    (folder / "Horimiya - 01x01 - Titel.mp4").write_bytes(b"x")
    assert scan_folder(folder, (".mp4",)).title == "Horimiya"


def test_scan_folder(tmp_path: Path) -> None:
    for name in ["[G] Show - 02 [1080p].mkv", "[G] Show - 01 [1080p].mkv", "notes.txt"]:
        (tmp_path / name).write_bytes(b"x")
    season = scan_folder(tmp_path, (".mkv",), title="Show")
    assert [e.number for e in season.episodes] == [1, 2]
    assert season.title == "Show"
    assert season.key.startswith("folder:")


def test_scan_folder_without_videos(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        scan_folder(tmp_path, (".mkv",))
