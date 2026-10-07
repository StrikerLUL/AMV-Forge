"""Gesichtsdetektor ohne echtes Modell: Letterbox, YOLO-Ausgabe lesen, NMS, Ausschnitte."""

from types import SimpleNamespace

import numpy as np
import pytest

from backend.analysis.video import faces
from backend.analysis.video.faces import (
    PAD_GRAY,
    Face,
    YoloFaceDetector,
    _input_size,
    crop_face,
    decode,
    keep_faces,
    letterbox,
    nms,
    top_square,
)
from backend.config import load_settings


def test_letterbox_keeps_aspect_ratio_and_pads_gray() -> None:
    image = np.full((100, 200, 3), 255, dtype=np.uint8)
    canvas, scale, left, top = letterbox(image, (64, 64))
    assert canvas.shape == (64, 64, 3)
    assert scale == pytest.approx(0.32) and (left, top) == (0, 16)
    assert (canvas[:16] == PAD_GRAY).all() and (canvas[48:] == PAD_GRAY).all()
    assert (canvas[16:48] == 255).all()


def test_nms_keeps_the_best_of_overlapping_boxes() -> None:
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.8, 0.9, 0.5], dtype=np.float32)
    assert nms(boxes, scores, 0.5) == [1, 2]


def _yolo_output(rows: list[tuple[float, float, float, float, float]]) -> np.ndarray:
    """So sieht die Ausgabe von YOLOv8 mit einer Klasse aus: [1, 5, N] (Mitte x, Mitte y, Breite, Höhe, Sicherheit)."""
    return np.asarray(rows, dtype=np.float32).T[None]


def test_decode_yolov8_output_back_to_the_original_image() -> None:
    # Original 200x100, Letterbox auf 64x64: Maßstab 0.32, oben 16 Pixel Rand
    output = _yolo_output([
        (16, 32, 16, 16, 0.9),   # Gesicht links: im Original x 25-75, y 25-75
        (17, 33, 16, 16, 0.6),   # fast derselbe Rahmen, weniger sicher -> fällt weg (NMS)
        (48, 32, 8, 8, 0.2),     # zu unsicher
    ])
    found = decode(output, 0.32, 0, 16, 200, 100, min_confidence=0.35, iou_threshold=0.5)
    assert len(found) == 1
    assert found[0].score == pytest.approx(0.9)
    assert found[0].box == pytest.approx((0.125, 0.25, 0.375, 0.75), abs=1e-3)


def test_decode_end_to_end_output() -> None:
    # Neuere Modelle: [1, N, 6] mit x0, y0, x1, y1, Sicherheit, Klasse
    output = np.array([[[8, 24, 24, 40, 0.8, 0], [0, 0, 1, 1, 0.1, 0], [40, 20, 56, 44, 0.7, 0]]], dtype=np.float32)
    found = decode(output, 0.32, 0, 16, 200, 100, min_confidence=0.35, iou_threshold=0.5)
    assert [f.score for f in found] == pytest.approx([0.8, 0.7])
    assert found[0].box == pytest.approx((0.125, 0.25, 0.375, 0.75), abs=1e-3)


def test_decode_without_faces() -> None:
    assert decode(np.zeros((1, 5, 0), dtype=np.float32), 1.0, 0, 0, 64, 64, 0.35, 0.5) == []
    assert decode(_yolo_output([(10, 10, 5, 5, 0.1)]), 1.0, 0, 0, 64, 64, 0.35, 0.5) == []


def test_keep_faces_drops_small_ones_and_sorts_by_size() -> None:
    small = Face((0.0, 0.0, 0.05, 0.05), 0.9)
    medium = Face((0.1, 0.1, 0.3, 0.3), 0.5)
    large = Face((0.5, 0.2, 0.9, 0.8), 0.6)
    assert keep_faces([small, medium, large], min_size=0.07, max_count=6) == [large, medium]
    assert keep_faces([small, medium, large], min_size=0.07, max_count=1) == [large]


def test_crop_face_is_square_bigger_and_stays_inside_the_image() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = crop_face(image, Face((0.4, 0.4, 0.5, 0.6), 0.9), 1.8)  # 20x20 Pixel
    assert crop.shape == (36, 36, 3)
    corner = crop_face(image, Face((0.0, 0.0, 0.1, 0.2), 0.9), 1.8)  # am Rand: verschoben, nicht abgeschnitten
    assert corner.shape == (36, 36, 3)
    huge = crop_face(image, Face((0.0, 0.0, 1.0, 1.0), 0.9), 1.8)  # größer als das Bild: höchstens Bildhöhe
    assert huge.shape == (100, 100, 3)


def test_crop_face_includes_the_hair_above() -> None:
    image = np.zeros((200, 200, 3), dtype=np.uint8)
    image[60:100, 80:120] = 255  # Gesicht
    image[40:60, 80:120] = 128  # Haare darüber
    crop = crop_face(image, Face((0.4, 0.3, 0.6, 0.5), 0.9), 1.8)
    assert (crop == 128).any() and (crop == 255).any()


def test_top_square_for_portraits() -> None:
    image = np.zeros((300, 200, 3), dtype=np.uint8)
    image[:200] = 255
    square = top_square(image)
    assert square.shape == (200, 200, 3) and (square == 255).all()


def _session(shape: list[object], meta: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        get_inputs=lambda: [SimpleNamespace(shape=shape, name="images")],
        get_modelmeta=lambda: SimpleNamespace(custom_metadata_map=meta or {}),
    )


def test_input_size_from_model_file() -> None:
    assert _input_size(_session([1, 3, 320, 480])) == (320, 480)
    assert _input_size(_session([1, 3, "height", "width"], {"imgsz": "[640, 640]"})) == (640, 640)
    assert _input_size(_session([1, 3, "h", "w"])) == (640, 640)


class FakeSession:
    """Spielt onnxruntime: merkt sich die Eingabe und liefert ein Gesicht in der Bildmitte."""

    def __init__(self) -> None:
        self.inputs: list[np.ndarray] = []

    def run(self, _outputs: object, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.inputs.append(feed["images"])
        return [_yolo_output([(32, 32, 16, 16, 0.95)])]


def test_detect_feeds_a_normalized_letterbox_tensor() -> None:
    detector = object.__new__(YoloFaceDetector)  # ohne Download: nur detect() prüfen
    detector.cfg = load_settings().faces
    detector.session = FakeSession()
    detector.input_name = "images"
    detector.size = (64, 64)
    found = detector.detect(np.full((100, 200, 3), 255, dtype=np.uint8))
    tensor = detector.session.inputs[0]
    assert tensor.shape == (1, 3, 64, 64) and tensor.dtype == np.float32
    assert tensor.max() == pytest.approx(1.0) and tensor[0, 0, 0, 0] == pytest.approx(PAD_GRAY / 255)
    assert len(found) == 1 and found[0].box == pytest.approx((0.375, 0.25, 0.625, 0.75), abs=1e-3)


def test_onnx_providers_prefers_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    ort = pytest.importorskip("onnxruntime")
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    assert faces.onnx_providers("auto") == ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert faces.onnx_providers("cpu") == ["CPUExecutionProvider"]
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    assert faces.onnx_providers("auto") == ["CPUExecutionProvider"]
