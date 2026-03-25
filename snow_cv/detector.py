"""
Person detection — YOLO + ByteTrack wrapper with post-processing.

Handles model loading, inference, and returns structured detection results.
Delegates dedup/merge to TrackState.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    """A single person detection in one frame."""
    track_id: int
    confidence: float
    bbox: tuple[float, float, float, float]  # x_min, y_min, x_max, y_max (normalized)
    centroid: tuple[float, float]  # cx, cy (normalized)
    mask_points: list[list[float]] | None = None


class PersonDetector:
    """YOLO-seg + ByteTrack person detector.

    Loads the model once; call `detect()` per frame with `persist=True`
    to maintain ByteTrack state across frames.

    Args:
        model_name: YOLO model file (default: yolov8n-seg.pt)
        confidence: minimum detection confidence
    """

    def __init__(self, model_name: str = "yolov8n-seg.pt", confidence: float = 0.3):
        self.model_name = model_name
        self.confidence = confidence
        self._model = None

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO
            log.info("Loading %s...", self.model_name)
            self._model = YOLO(self.model_name)
            log.info("Model loaded.")

    def reset_tracker(self):
        """Reset ByteTrack state (between videos/segments)."""
        if self._model is not None:
            self._model.predictor = None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run YOLO-seg + ByteTrack on a single frame.

        Args:
            frame: RGB numpy array (H, W, 3)

        Returns:
            List of Detection objects (before dedup/merge).
        """
        self._load()

        results = self._model.track(
            frame, persist=True, tracker="bytetrack.yaml",
            classes=[0], conf=self.confidence, verbose=False,
        )
        result = results[0]

        if result.boxes is None or result.boxes.id is None:
            return []

        boxes = result.boxes
        xyxyn = boxes.xyxyn.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        track_ids = boxes.id.int().cpu().tolist()

        masks_xyn = []
        if result.masks is not None and hasattr(result.masks, "xyn"):
            masks_xyn = result.masks.xyn

        detections = []
        for j, (box, conf, tid) in enumerate(zip(xyxyn, confs, track_ids)):
            x_min, y_min, x_max, y_max = box
            cx = float((x_min + x_max) / 2)
            cy = float((y_min + y_max) / 2)

            mask_points = None
            if j < len(masks_xyn):
                poly = masks_xyn[j]
                if len(poly) > 0:
                    step = max(1, len(poly) // 40)
                    mask_points = [[round(float(p[0]), 4), round(float(p[1]), 4)]
                                   for p in poly[::step]]

            detections.append(Detection(
                track_id=tid,
                confidence=round(float(conf), 3),
                bbox=(round(float(x_min), 4), round(float(y_min), 4),
                      round(float(x_max), 4), round(float(y_max), 4)),
                centroid=(cx, cy),
                mask_points=mask_points,
            ))

        return detections
