import random

import pytest

from backend.analysis.video.scenes import Scene
from backend.planner.assign import assign_random, usable_scenes
from backend.planner.slots import Slot
from backend.render.ffmpeg_graph import frame_counts


def test_clips_stay_inside_their_scene() -> None:
    scenes = [Scene(i * 3.0, i * 3.0 + 3.0) for i in range(50)]
    slots = [Slot(i * 1.0, i * 1.0 + 1.0) for i in range(30)]
    result = assign_random(slots, scenes, random.Random(1))
    for a in result:
        scene = next(s for s in scenes if s.start <= a.source_start < s.end)
        assert a.source_end <= scene.end + 1e-9


def test_no_repeats_while_enough_scenes() -> None:
    scenes = [Scene(i * 3.0, i * 3.0 + 3.0) for i in range(50)]
    slots = [Slot(i * 1.0, i * 1.0 + 1.0) for i in range(30)]
    result = assign_random(slots, scenes, random.Random(2))
    starts = [int(a.source_start // 3) for a in result]
    assert len(set(starts)) == len(starts)


def test_same_seed_same_edit() -> None:
    scenes = [Scene(i * 2.0, i * 2.0 + 2.0) for i in range(20)]
    slots = [Slot(i * 0.5, i * 0.5 + 0.5) for i in range(10)]
    a = assign_random(slots, scenes, random.Random(42))
    b = assign_random(slots, scenes, random.Random(42))
    assert a == b


def test_usable_scenes_respects_skip() -> None:
    scenes = [Scene(0, 60), Scene(60, 120), Scene(1300, 1440)]
    result = usable_scenes(scenes, 1440, skip_start=90, skip_end=90)
    assert result == [Scene(90, 120), Scene(1300, 1350)]


def test_no_scenes_raises() -> None:
    with pytest.raises(ValueError):
        assign_random([Slot(0, 1)], [], random.Random(0))


def test_frame_counts_sum_to_total_without_drift() -> None:
    scenes = [Scene(0, 100)]
    slots = [Slot(i * 0.4687, (i + 1) * 0.4687) for i in range(64)]
    result = assign_random(slots, scenes, random.Random(0))
    counts = frame_counts(result, 30)
    assert sum(counts) == round(64 * 0.4687 * 30)
