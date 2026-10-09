"""Anime-Gesichter finden (Phase 5).

Ein YOLOv8-Modell, das auf Anime-Gesichter trainiert ist, sucht in jedem Standbild Rahmen um
Gesichter. YOLO ("You Only Look Once") schaut das Bild einmal komplett an und gibt für mehrere
tausend mögliche Rahmen gleichzeitig an, wie sicher dort ein Gesicht ist. Viele dieser Rahmen
liegen fast übereinander. Die Non-Maximum Suppression (NMS) behält davon nur den sichersten.

Das Modell liegt als ONNX-Datei auf Hugging Face und läuft mit onnxruntime. ONNX ist ein
Austauschformat für neuronale Netze, dafür braucht es kein PyTorch.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from backend.config.settings import FaceSettings

log = logging.getLogger(__name__)

PAD_GRAY = 114  # Füllfarbe beim Letterboxing, wie beim Training von YOLO


@dataclass(frozen=True)
class Face:
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 als Anteil von Bildbreite und -höhe (0-1)
    score: float  # wie sicher der Detektor ist, 0-1

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


@dataclass(frozen=True)
class ClipFace:
    """Ein gefundenes Gesicht aus der Datenbank (Clip.faces): wann, wo und wer. Braucht der Reframe (Phase 6)."""

    time: float  # Sekunden in der Folge
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 als Anteil von Bildbreite und -höhe (0-1)
    character: int | None = None  # AniList-ID oder None (unbekannt)
    probability: float = 0.0

    @property
    def center_x(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def center_y(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @staticmethod
    def from_db(rows: list | None) -> tuple["ClipFace", ...] | None:
        """[{"t", "box", "char", "p", ...}] aus Clip.faces. None bleibt None (noch nicht gesucht)."""
        if rows is None:
            return None
        return tuple(
            ClipFace(float(r["t"]), (float(r["box"][0]), float(r["box"][1]), float(r["box"][2]), float(r["box"][3])),
                     int(r["char"]) if r.get("char") is not None else None, float(r.get("p") or 0.0))
            for r in rows
        )


class FaceDetector(Protocol):
    """Alles, was in einem RGB-Bild Gesichter findet (das echte YOLO oder ein Test-Ersatz)."""

    name: str

    def detect(self, image: np.ndarray) -> list[Face]: ...


def is_installed() -> bool:
    return importlib.util.find_spec("onnxruntime") is not None and importlib.util.find_spec("huggingface_hub") is not None


def letterbox(image: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, int, int]:
    """Verkleinert das Bild auf size (Höhe, Breite), ohne es zu verzerren, und füllt den Rest grau auf.

    Gibt das neue Bild, den Maßstab und den Rand links/oben zurück (zum Zurückrechnen der Rahmen).
    """
    height, width = image.shape[:2]
    target_h, target_w = size
    scale = min(target_w / width, target_h / height)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas = np.full((target_h, target_w, 3), PAD_GRAY, dtype=np.uint8)
    left, top = (target_w - new_w) // 2, (target_h - new_h) // 2
    canvas[top:top + new_h, left:left + new_w] = resized
    return canvas, scale, left, top


def iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    """Überlappung (Intersection over Union) eines Rahmens mit vielen anderen, 0 = gar nicht, 1 = gleich."""
    x0 = np.maximum(box[0], others[:, 0])
    y0 = np.maximum(box[1], others[:, 1])
    x1 = np.minimum(box[2], others[:, 2])
    y1 = np.minimum(box[3], others[:, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area = (box[2] - box[0]) * (box[3] - box[1])
    areas = (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1])
    return inter / np.maximum(area + areas - inter, 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Non-Maximum Suppression: vom sichersten Rahmen abwärts, stark überlappende fallen weg."""
    order = list(np.argsort(-scores))
    keep: list[int] = []
    while order:
        best = order.pop(0)
        keep.append(int(best))
        if order:
            overlap = iou(boxes[best], boxes[order])
            order = [i for i, o in zip(order, overlap) if o <= threshold]
    return keep


def decode(output: np.ndarray, scale: float, left: int, top: int, width: int, height: int,
           min_confidence: float, iou_threshold: float) -> list[Face]:
    """Rechnet die Rohausgabe von YOLO in Gesichtsrahmen um.

    YOLOv8/YOLO11 liefert [4 + Klassen, N]: Mitte x, Mitte y, Breite, Höhe und eine Sicherheit pro
    Klasse, in Pixeln des Letterbox-Bildes. Neuere End-to-End-Modelle liefern [N, 6] mit
    x0, y0, x1, y1, Sicherheit, Klasse.
    """
    out = np.asarray(output, dtype=np.float32)
    if out.ndim == 3:
        out = out[0]
    if out.ndim != 2 or out.size == 0:
        return []
    if out.shape[1] == 6 and out.shape[0] != 6:
        boxes, scores = out[:, :4].copy(), out[:, 4]
    else:
        rows = out.T
        cx, cy, w, h = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        scores = rows[:, 4:].max(axis=1)
    keep = scores >= min_confidence
    boxes, scores = boxes[keep], scores[keep]
    if not len(scores):
        return []
    picked = nms(boxes, scores, iou_threshold)
    boxes, scores = boxes[picked], scores[picked]
    # vom Letterbox-Bild zurück aufs Original, dann als Anteil von Breite und Höhe
    boxes[:, [0, 2]] = np.clip((boxes[:, [0, 2]] - left) / scale / width, 0.0, 1.0)
    boxes[:, [1, 3]] = np.clip((boxes[:, [1, 3]] - top) / scale / height, 0.0, 1.0)
    return [Face((round(float(b[0]), 4), round(float(b[1]), 4), round(float(b[2]), 4), round(float(b[3]), 4)),
                 round(float(s), 3)) for b, s in zip(boxes, scores)]


def keep_faces(faces: list[Face], min_size: float, max_count: int) -> list[Face]:
    """Zu kleine Gesichter weg, von den übrigen die größten max_count."""
    big = [f for f in faces if f.height >= min_size and f.width > 0]
    return sorted(big, key=lambda f: -(f.width * f.height))[:max_count]


def crop_face(image: np.ndarray, face: Face, crop_scale: float) -> np.ndarray:
    """Quadratischer Ausschnitt um das Gesicht, crop_scale-mal so groß, damit die Haare mit drauf sind.

    Etwas nach oben verschoben (Frisur). Am Bildrand wird der Ausschnitt verschoben statt abgeschnitten.
    """
    height, width = image.shape[:2]
    x0, y0, x1, y1 = face.box[0] * width, face.box[1] * height, face.box[2] * width, face.box[3] * height
    side = int(round(min(max(x1 - x0, y1 - y0) * crop_scale, width, height)))
    side = max(side, 1)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - 0.1 * (y1 - y0)
    left = int(round(min(max(cx - side / 2, 0), width - side)))
    top = int(round(min(max(cy - side / 2, 0), height - side)))
    return image[top:top + side, left:left + side]


def top_square(image: np.ndarray) -> np.ndarray:
    """Ersatz-Ausschnitt, wenn auf einem Charakterbild kein Gesicht gefunden wird: oberes Quadrat."""
    height, width = image.shape[:2]
    side = min(height, width)
    left = (width - side) // 2
    return image[:side, left:left + side]


def onnx_providers(device: str) -> list[str]:
    """GPU, wenn onnxruntime eine kennt (CUDA, DirectML unter Windows) und nicht 'cpu' eingestellt ist."""
    import onnxruntime as ort

    available = ort.get_available_providers()
    if device.lower() != "cpu":
        for gpu in ("CUDAExecutionProvider", "DmlExecutionProvider"):
            if gpu in available:
                return [gpu, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _input_size(session: object) -> tuple[int, int]:
    """Bildgröße (Höhe, Breite), die das Modell erwartet. Aus der Modell-Datei, sonst 640x640."""
    shape = session.get_inputs()[0].shape  # type: ignore[attr-defined]
    if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
        return int(shape[2]), int(shape[3])
    meta = session.get_modelmeta().custom_metadata_map  # type: ignore[attr-defined]
    if "imgsz" in meta:
        size = json.loads(meta["imgsz"])
        if isinstance(size, list) and len(size) == 2:
            return int(size[0]), int(size[1])
    return 640, 640


class YoloFaceDetector:
    """Das echte YOLO-Modell. Lädt es beim Erzeugen (beim ersten Mal von Hugging Face)."""

    def __init__(self, cfg: FaceSettings) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        self.cfg = cfg
        self.name = f"{cfg.repo}/{cfg.model}"
        log.info("Lade Gesichtsdetektor %s ...", self.name)
        path = hf_hub_download(repo_id=cfg.repo, filename=cfg.model)
        options = ort.SessionOptions()
        options.log_severity_level = 3  # nur echte Fehler, keine Hinweise
        providers = onnx_providers(cfg.device)
        if "DmlExecutionProvider" in providers:  # DirectML verlangt das so (Doku von onnxruntime)
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(path, options, providers=providers)
        provider = self.session.get_providers()[0]
        if provider == "CPUExecutionProvider":
            log.info("Gesichtsdetektor rechnet auf der CPU (onnxruntime ohne GPU, siehe README Phase 5)")
        else:
            log.info("Gesichtsdetektor rechnet auf der GPU (%s)", provider)
        self.input_name = self.session.get_inputs()[0].name
        self.size = _input_size(self.session)

    def detect(self, image: np.ndarray) -> list[Face]:
        height, width = image.shape[:2]
        canvas, scale, left, top = letterbox(image, self.size)
        tensor = (canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
        output = self.session.run(None, {self.input_name: tensor})[0]
        faces = decode(output, scale, left, top, width, height, self.cfg.min_confidence, self.cfg.iou)
        return keep_faces(faces, self.cfg.min_size, self.cfg.max_per_frame)
