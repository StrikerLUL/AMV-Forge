from backend.analysis.intervals import merge, subtract


def test_merge_overlapping() -> None:
    assert merge([(5, 10), (0, 3), (9, 12)]) == [(0, 3), (5, 12)]


def test_subtract_cuts_out_opening() -> None:
    scenes = [(0, 30), (30, 80), (80, 100), (100, 200)]
    opening = [(90, 180)]
    assert subtract(scenes, opening) == [(0, 30), (30, 80), (80, 90), (180, 200)]


def test_subtract_splits_scene_in_middle() -> None:
    assert subtract([(0, 100)], [(40, 50)]) == [(0, 40), (50, 100)]


def test_subtract_drops_short_rests() -> None:
    assert subtract([(0, 10), (10, 20)], [(0.3, 19.8)], min_length=0.5) == []


def test_subtract_without_cuts() -> None:
    assert subtract([(0, 5)], []) == [(0, 5)]
