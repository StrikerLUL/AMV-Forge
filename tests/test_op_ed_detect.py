import numpy as np

from backend.analysis.audio.op_ed_detect import FEATURE_SR, Fingerprint, find_shared_segment, fingerprint

FPS = 5.0


def _random_unit(n: int, rng: np.random.Generator) -> np.ndarray:
    x = rng.normal(size=(n, 12)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def test_finds_shared_block_at_different_offsets() -> None:
    rng = np.random.default_rng(0)
    song = _random_unit(450, rng)  # 90 s "Opening"
    a = np.concatenate([_random_unit(100, rng), song, _random_unit(300, rng)])  # OP ab 20 s
    b = np.concatenate([_random_unit(250, rng), song, _random_unit(100, rng)])  # OP ab 50 s
    seg = find_shared_segment(Fingerprint(a, FPS, 0.0), Fingerprint(b, FPS, 0.0), 0.9, 40, 130, 2.0)
    assert seg is not None
    assert abs(seg.start_a - 20) < 0.5 and abs(seg.end_a - 110) < 0.5
    assert abs(seg.start_b - 50) < 0.5


def test_offset_is_added() -> None:
    rng = np.random.default_rng(1)
    song = _random_unit(300, rng)
    a = np.concatenate([_random_unit(50, rng), song])
    b = np.concatenate([song, _random_unit(50, rng)])
    seg = find_shared_segment(Fingerprint(a, FPS, 1000.0), Fingerprint(b, FPS, 0.0), 0.9, 40, 130, 2.0)
    assert seg is not None
    assert abs(seg.start_a - 1010) < 0.5


def test_no_match_without_shared_music() -> None:
    rng = np.random.default_rng(2)
    a, b = _random_unit(600, rng), _random_unit(600, rng)
    assert find_shared_segment(Fingerprint(a, FPS, 0.0), Fingerprint(b, FPS, 0.0), 0.9, 40, 130, 2.0) is None


def test_too_short_match_is_ignored() -> None:
    rng = np.random.default_rng(3)
    jingle = _random_unit(50, rng)  # nur 10 s gleich, z. B. ein Eyecatch
    a = np.concatenate([_random_unit(200, rng), jingle, _random_unit(200, rng)])
    b = np.concatenate([_random_unit(100, rng), jingle, _random_unit(300, rng)])
    assert find_shared_segment(Fingerprint(a, FPS, 0.0), Fingerprint(b, FPS, 0.0), 0.9, 40, 130, 2.0) is None


def _melody(seed: int, seconds: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    parts = []
    for _ in range(int(seconds * 2)):
        f = 220 * 2 ** (rng.integers(0, 24) / 12)
        t = np.arange(int(0.5 * FEATURE_SR)) / FEATURE_SR
        parts.append(0.3 * np.sin(2 * np.pi * f * t) * np.exp(-3 * t))
    return np.concatenate(parts).astype(np.float32)


def test_real_audio_with_noise_around_the_opening() -> None:
    noise = np.random.default_rng(9)
    op = _melody(100, 60)
    a = np.concatenate([noise.normal(0, 0.05, 15 * FEATURE_SR), op, noise.normal(0, 0.05, 20 * FEATURE_SR)])
    b = np.concatenate([noise.normal(0, 0.05, 30 * FEATURE_SR), op, noise.normal(0, 0.05, 10 * FEATURE_SR)])
    seg = find_shared_segment(
        fingerprint(a.astype(np.float32), 0.0, -45), fingerprint(b.astype(np.float32), 0.0, -45), 0.9, 40, 130, 2.0
    )
    assert seg is not None
    assert abs(seg.start_a - 15) < 1.5 and abs(seg.end_a - 75) < 1.5
