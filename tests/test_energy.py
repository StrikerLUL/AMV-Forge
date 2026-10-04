import numpy as np
import pytest

from backend.analysis.music.energy import detect_drops, mean_energy

RATE = 10.0


def _curve(levels: list[tuple[float, float]]) -> np.ndarray:
    """[(Dauer, Energie), ...] -> Kurve mit RATE Werten pro Sekunde."""
    return np.concatenate([np.full(int(d * RATE), e) for d, e in levels])


def test_mean_energy() -> None:
    curve = _curve([(10, 0.2), (10, 0.8)])
    assert mean_energy(curve, RATE, 0, 10) == pytest.approx(0.2)
    assert mean_energy(curve, RATE, 10, 20) == pytest.approx(0.8)
    assert mean_energy(curve, RATE, 5, 15) == pytest.approx(0.5)
    assert mean_energy(curve, RATE, 19.95, 19.96) == pytest.approx(0.8)  # ganz kurzer Slot: ein Wert


def test_drop_is_the_big_jump_to_a_loud_part() -> None:
    curve = _curve([(16, 0.1), (16, 0.6), (8, 0.3), (16, 1.0), (8, 0.2)])
    downbeats = [i * 2.0 for i in range(32)]
    drops = detect_drops(curve, RATE, downbeats, window_seconds=4, min_jump=0.2, min_level=0.75,
                         min_distance_seconds=16, max_drops=4)
    # 16 s: Sprung auf 0.6 ist nicht laut genug. 40 s: Sprung von 0.3 auf 1.0 ist der Drop.
    assert [d.time for d in drops] == [40.0]
    assert drops[0].strength == pytest.approx(0.7)


def test_close_candidates_keep_only_the_strongest() -> None:
    curve = _curve([(10, 0.1), (2, 0.5), (20, 1.0)])
    downbeats = [i * 2.0 for i in range(16)]
    drops = detect_drops(curve, RATE, downbeats, 4, 0.2, 0.75, 16, 4)
    assert len(drops) == 1


def test_no_drop_in_a_flat_song() -> None:
    curve = _curve([(60, 0.7)])
    assert detect_drops(curve, RATE, [i * 2.0 for i in range(30)], 4, 0.2, 0.75, 16, 4) == []
