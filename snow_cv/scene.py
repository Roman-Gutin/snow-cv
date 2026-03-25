"""
Scene understanding — LVM spatial reasoning + SAM2 precise segmentation.

Provides the "brain" for zone detection: instead of geometric heuristics,
ask a vision-language model about the store layout, then use SAM2 for
pixel-perfect fixture masks.

Models:
  - Moondream2 (2B VLM): spatial Q&A about the scene
  - SAM2.1 (via ultralytics): bbox-prompted segmentation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded singletons (same pattern as Florence-2 in zones.py)
# ---------------------------------------------------------------------------

_lvm_model = None
_lvm_device = None

_sam_model = None


def _load_lvm():
    """Load Moondream2 for scene understanding (once, cached)."""
    global _lvm_model, _lvm_device
    if _lvm_model is not None:
        return

    import torch
    from transformers import AutoModelForCausalLM

    if torch.cuda.is_available():
        _lvm_device = "cuda"
    elif torch.backends.mps.is_available():
        _lvm_device = "mps"
    else:
        _lvm_device = "cpu"

    log.info("Loading Moondream2 on %s...", _lvm_device)
    _lvm_model = AutoModelForCausalLM.from_pretrained(
        "vikhyatk/moondream2",
        trust_remote_code=True,
        device_map={"": _lvm_device},
    )
    _lvm_model.eval()
    log.info("Moondream2 loaded.")


def _load_sam():
    """Load SAM2.1-b via ultralytics (once, cached)."""
    global _sam_model
    if _sam_model is not None:
        return

    from ultralytics import SAM

    log.info("Loading SAM2.1-b...")
    _sam_model = SAM("sam2.1_b.pt")
    log.info("SAM2.1-b loaded.")


# ---------------------------------------------------------------------------
# LVM scene understanding
# ---------------------------------------------------------------------------

# Targeted questions — short answers for reliable parsing.
_SCENE_QUESTIONS = [
    (
        "counter_side",
        "In this security camera image of a retail store, which side of "
        "the frame is the service counter, checkout area, or display case? "
        "Answer with one word: left, right, top, or bottom.",
    ),
    (
        "employee_direction",
        "Looking at the service counter or display case in this image, "
        "which direction from the counter do employees or staff stand? "
        "Answer with one word: left, right, above, or below.",
    ),
    (
        "customer_service_side",
        "Looking at the service counter or display case in this image, "
        "which direction from the counter do customers stand when they "
        "are being served or picking up their order? "
        "Answer with one word: left, right, above, or below.",
    ),
    (
        "entrance_side",
        "Where is the store entrance or exit door in this image? "
        "Answer with one word: left, right, top, or bottom.",
    ),
    (
        "layout_description",
        "Describe the spatial layout of this retail store from the "
        "camera's perspective in 2-3 sentences. Mention the counter, "
        "employee area, customer waiting area, and entrance if visible.",
    ),
]

_DIRECTION_KEYWORDS = {
    "left": "left",
    "right": "right",
    "top": "top",
    "upper": "top",
    "above": "top",
    "bottom": "bottom",
    "lower": "bottom",
    "below": "bottom",
}


def _parse_direction(answer: str) -> str | None:
    """Extract a direction keyword from an LVM answer."""
    answer_lower = answer.lower().strip()
    # Check single-word answer first
    if answer_lower in _DIRECTION_KEYWORDS:
        return _DIRECTION_KEYWORDS[answer_lower]
    # Scan for first direction keyword in a longer answer
    for word in answer_lower.split():
        clean = word.strip(".,;:!?")
        if clean in _DIRECTION_KEYWORDS:
            return _DIRECTION_KEYWORDS[clean]
    return None


def understand_scene(image) -> dict:
    """Ask Moondream2 targeted spatial questions about a store image.

    Args:
        image: PIL Image

    Returns:
        {
            "counter_side": "left" | "right" | "top" | "bottom" | None,
            "employee_direction": "left" | "right" | "top" | "bottom" | None,
            "customer_service_side": "left" | "right" | "top" | "bottom" | None,
            "entrance_side": "left" | "right" | "top" | "bottom" | None,
            "layout_description": str,
            "raw_answers": {key: raw_answer_str, ...},
            "confidence": float,  # 0-1, fraction of questions with parseable answers
        }
    """
    try:
        _load_lvm()
    except Exception as e:
        log.warning("Failed to load Moondream2: %s — LVM scene understanding unavailable", e)
        return {
            "counter_side": None,
            "employee_direction": None,
            "customer_service_side": None,
            "entrance_side": None,
            "layout_description": "",
            "raw_answers": {},
            "confidence": 0.0,
            "error": str(e),
        }

    raw_answers = {}
    parsed = {}

    for key, question in _SCENE_QUESTIONS:
        try:
            result = _lvm_model.query(image, question)
            answer = result["answer"] if isinstance(result, dict) else str(result)
            raw_answers[key] = answer
            log.info("LVM Q[%s]: %s", key, answer)

            if key == "layout_description":
                parsed[key] = answer
            else:
                parsed[key] = _parse_direction(answer)
        except Exception as e:
            log.warning("LVM query failed for %s: %s", key, e)
            raw_answers[key] = f"ERROR: {e}"
            parsed[key] = None if key != "layout_description" else ""

    # Confidence = fraction of directional questions that parsed successfully
    directional_keys = ["counter_side", "employee_direction", "customer_service_side", "entrance_side"]
    answered = sum(1 for k in directional_keys if parsed.get(k) is not None)
    confidence = answered / len(directional_keys)

    return {
        **parsed,
        "raw_answers": raw_answers,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# SAM2 precise segmentation
# ---------------------------------------------------------------------------

def segment_fixture(
    image_array: np.ndarray,
    bbox: list[float],
    simplify_points: int = 30,
) -> list[list[float]] | None:
    """Segment a fixture using SAM2 with a bbox prompt.

    Args:
        image_array: RGB numpy array (H, W, 3)
        bbox: [x1, y1, x2, y2] in pixel coordinates
        simplify_points: max polygon points after simplification

    Returns:
        Normalized polygon [[x, y], ...] or None if segmentation fails.
    """
    try:
        _load_sam()
    except Exception as e:
        log.warning("Failed to load SAM2: %s — falling back to bbox polygon", e)
        return None

    h, w = image_array.shape[:2]

    try:
        results = _sam_model(image_array, bboxes=[bbox], verbose=False)
        result = results[0]

        if result.masks is None or len(result.masks) == 0:
            log.warning("SAM2 returned no masks for bbox %s", bbox)
            return None

        # Get the mask with highest confidence
        if hasattr(result.masks, "xyn") and len(result.masks.xyn) > 0:
            # ultralytics already gives normalized xy polygon
            poly = result.masks.xyn[0]
            if len(poly) == 0:
                return None

            # Simplify if too many points
            if len(poly) > simplify_points:
                step = max(1, len(poly) // simplify_points)
                poly = poly[::step]

            return [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in poly]

        # Fallback: extract from binary mask
        mask = result.masks.data[0].cpu().numpy().astype(np.uint8)
        return _mask_to_polygon(mask, w, h, simplify_points)

    except Exception as e:
        log.warning("SAM2 segmentation failed: %s — falling back to bbox", e)
        return None


def _mask_to_polygon(
    mask: np.ndarray, img_w: int, img_h: int, max_points: int = 30,
) -> list[list[float]] | None:
    """Convert a binary mask to a simplified, normalized polygon."""
    try:
        import cv2
    except ImportError:
        log.warning("cv2 not available for mask-to-polygon conversion")
        return None

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Take the largest contour
    contour = max(contours, key=cv2.contourArea)

    # Simplify
    epsilon = 0.01 * cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon, True)

    if len(approx) > max_points:
        step = max(1, len(approx) // max_points)
        approx = approx[::step]

    # Normalize
    mask_h, mask_w = mask.shape[:2]
    polygon = []
    for pt in approx:
        x, y = pt[0]
        polygon.append([
            round(float(x) / mask_w, 4),
            round(float(y) / mask_h, 4),
        ])

    return polygon if len(polygon) >= 3 else None


# ---------------------------------------------------------------------------
# SAM2 "segment everything" — no prompt, returns all segments
# ---------------------------------------------------------------------------

@dataclass
class SegmentInfo:
    """Metadata for a single SAM2 segment."""
    polygon: list[list[float]]      # normalized [[x,y], ...]
    area_frac: float                 # fraction of frame area
    bbox_norm: tuple[float, float, float, float]  # (x1,y1,x2,y2) normalized
    centroid: tuple[float, float]    # normalized (cx, cy)
    touches_edge: set[str]          # {"left","right","top","bottom"}
    aspect_ratio: float             # width / height of bounding box
    mask: np.ndarray | None = None  # binary mask (H,W) if available


def segment_everything(
    image_array: np.ndarray,
    min_area_frac: float = 0.005,
    max_area_frac: float = 0.60,
    simplify_points: int = 30,
) -> list[SegmentInfo]:
    """Run SAM2 with no prompts to segment all objects in the image.

    Args:
        image_array: RGB numpy array (H, W, 3)
        min_area_frac: discard segments smaller than this fraction of frame
        max_area_frac: discard segments larger than this (floors, ceilings)
        simplify_points: max polygon vertices per segment

    Returns:
        List of SegmentInfo sorted by area (largest first).
    """
    try:
        _load_sam()
    except Exception as e:
        log.warning("Failed to load SAM2 for segment-everything: %s", e)
        return []

    h, w = image_array.shape[:2]
    frame_area = h * w
    edge_threshold = 0.03  # within 3% of edge counts as touching

    try:
        results = _sam_model(image_array, verbose=False)
        result = results[0]

        if result.masks is None or len(result.masks) == 0:
            log.warning("SAM2 segment-everything returned no masks")
            return []

        segments = []
        for i in range(len(result.masks)):
            # Get binary mask
            mask_data = result.masks.data[i].cpu().numpy().astype(np.uint8)
            mask_area = float(mask_data.sum())
            area_frac = mask_area / frame_area

            if area_frac < min_area_frac or area_frac > max_area_frac:
                continue

            # Get polygon
            poly = None
            if hasattr(result.masks, "xyn") and i < len(result.masks.xyn):
                raw_poly = result.masks.xyn[i]
                if len(raw_poly) > 0:
                    if len(raw_poly) > simplify_points:
                        step = max(1, len(raw_poly) // simplify_points)
                        raw_poly = raw_poly[::step]
                    poly = [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in raw_poly]

            if poly is None or len(poly) < 3:
                poly_from_mask = _mask_to_polygon(mask_data, w, h, simplify_points)
                if poly_from_mask is None:
                    continue
                poly = poly_from_mask

            # Compute bounding box from polygon
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            bw = max(x2 - x1, 0.001)
            bh = max(y2 - y1, 0.001)

            # Which frame edges does this segment touch?
            touches = set()
            if x1 < edge_threshold:
                touches.add("left")
            if x2 > 1.0 - edge_threshold:
                touches.add("right")
            if y1 < edge_threshold:
                touches.add("top")
            if y2 > 1.0 - edge_threshold:
                touches.add("bottom")

            segments.append(SegmentInfo(
                polygon=poly,
                area_frac=round(area_frac, 4),
                bbox_norm=(round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)),
                centroid=(round(cx, 4), round(cy, 4)),
                touches_edge=touches,
                aspect_ratio=round(bw / bh, 3),
                mask=mask_data,
            ))

        # Sort by area, largest first
        segments.sort(key=lambda s: s.area_frac, reverse=True)
        log.info("SAM2 segment-everything: %d segments after filtering", len(segments))
        return segments

    except Exception as e:
        log.warning("SAM2 segment-everything failed: %s", e)
        return []

def validate_zones_with_yolo(
    image_array: np.ndarray,
    zones: dict[str, list[list[float]]],
) -> dict[str, Any]:
    """Run YOLO person detection on the reference frame to validate zone placement.

    Checks that zones contain people where expected (e.g., employee zone
    should have at least one person if the store appears staffed).

    Args:
        image_array: RGB numpy array (H, W, 3)
        zones: {zone_name: [[x,y], ...]} polygon dict

    Returns:
        {
            "people_count": int,
            "zone_occupancy": {"employee": 2, "queue": 3, ...},
            "empty_zones": ["entrance"],
            "warnings": ["employee zone has 0 people — may be misplaced"],
            "confidence": float,
        }
    """
    from snow_cv.zones import ZoneMap

    zone_map = ZoneMap(zones=zones)

    try:
        from snow_cv.detector import PersonDetector
        detector = PersonDetector(confidence=0.25)
        detections = detector.detect(image_array)
    except Exception as e:
        log.warning("YOLO validation failed: %s", e)
        return {
            "people_count": 0,
            "zone_occupancy": {},
            "empty_zones": list(zones.keys()),
            "warnings": [f"YOLO detection failed: {e}"],
            "confidence": 0.0,
        }

    zone_occupancy: dict[str, int] = {z: 0 for z in zones}
    for det in detections:
        cx, cy = det.centroid
        zone = zone_map.zone_for_point(cx, cy)
        if zone and zone in zone_occupancy:
            zone_occupancy[zone] += 1

    warnings = []
    # Employee zone with 0 people is suspicious (most stores are staffed)
    if zone_occupancy.get("employee", 0) == 0 and len(detections) > 0:
        warnings.append(
            "Employee zone has 0 people — zone may be misplaced. "
            f"Detected {len(detections)} people total, none in employee area."
        )

    # If ALL people are in a single zone, zones are probably wrong
    occupied_zones = [z for z, c in zone_occupancy.items() if c > 0]
    if len(detections) > 2 and len(occupied_zones) <= 1:
        warnings.append(
            f"All {len(detections)} people fall in zone '{occupied_zones[0] if occupied_zones else 'none'}' "
            "— zone placement may be incorrect."
        )

    empty_zones = [z for z, c in zone_occupancy.items() if c == 0]

    # Confidence: higher when people are distributed across zones
    if len(detections) == 0:
        confidence = 0.5  # can't validate without people
    else:
        zone_spread = len(occupied_zones) / max(len(zones), 1)
        has_employee = 1.0 if zone_occupancy.get("employee", 0) > 0 else 0.0
        confidence = round(0.4 * zone_spread + 0.4 * has_employee + 0.2, 2)

    result = {
        "people_count": len(detections),
        "zone_occupancy": zone_occupancy,
        "empty_zones": empty_zones,
        "warnings": warnings,
        "confidence": confidence,
    }

    for w in warnings:
        log.warning("Zone validation: %s", w)
    log.info(
        "YOLO validation: %d people, occupancy=%s, confidence=%.2f",
        len(detections), zone_occupancy, confidence,
    )

    return result
