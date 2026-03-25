"""
Flask API backend for surveillance visualizer.

Runs inference on images/videos and returns annotated results as JSON + base64
frames for the React app to render.

Uses the snow_cv SDK for all detection, tracking, zone classification,
and event logic — no duplicated analytics code.

Usage:
    python server.py
    # Server starts on http://localhost:5001
"""

import io
import os
import json
import base64
import sys
import tempfile
from pathlib import Path

import av
import numpy as np
from PIL import Image
from flask import Flask, request, jsonify
from flask_cors import CORS

# Add parent dir so snow_cv SDK is importable
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from snow_cv.zones import ZoneMap
from snow_cv.detector import PersonDetector
from snow_cv.tracker import TrackState
from snow_cv.events import EventEngine
from snow_cv.strategies import get_strategy

app = Flask(__name__)
CORS(app)

# Paths — relative to customer_facing/
RAW_VIDEO_DIR = BASE_DIR / "videos"
ZONE_CONFIG_DIR = BASE_DIR / "configs"

# Load model once at startup
print("Loading YOLOv8n-seg...")
detector = PersonDetector(model_name="yolov8n-seg.pt", confidence=0.3)
print("Model loaded.")

# Last auto-detected counter region (set by /api/auto-zones, not hardcoded)
_last_auto_counter = None

# Per-video use_case cache (populated when loading config)
_use_case_cache = {}
_strategy_config_cache = {}


def frame_to_b64(pil_img, quality=85):
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parse_zones(req):
    """Parse zones from request. Returns client-provided zones, or empty dict.
    Never falls back to hardcoded examples — the client controls what zones are used."""
    data = req.get_json(silent=True) or {}
    if "zones" in data and data["zones"]:
        return dict(data["zones"])
    # FormData uploads may pass zones as a JSON string field
    if hasattr(req, "form") and "zones" in req.form:
        import json as _json
        parsed = _json.loads(req.form["zones"])
        if parsed:
            return parsed
    return {}


def parse_counter(req):
    """Parse counter region from request. Returns client-provided counter, or None."""
    data = req.get_json(silent=True) or {}
    if "counter" in data and data["counter"]:
        return data["counter"]
    if hasattr(req, "form") and "counter" in req.form:
        import json as _json
        parsed = _json.loads(req.form["counter"])
        if parsed:
            return parsed
    return _last_auto_counter


def _build_zone_map(zones, counter=None, use_case="retail", config=None):
    """Build a ZoneMap from a zones dict and optional counter region."""
    strategy = get_strategy(use_case, (config or {}).get("strategy_config", (config or {}).get("parking", {})))
    kwargs = {"zones": zones, "counter_region": counter}
    priority = (config or {}).get("zone_priority") or strategy.zone_priority()
    role_map = (config or {}).get("role_map") or strategy.role_map()
    if priority is not None:
        kwargs["priority"] = priority
    if role_map is not None:
        kwargs["role_map"] = role_map
    return ZoneMap(**kwargs)


def _build_event_engine(use_case="retail", config=None):
    """Build the right EventEngine for the use case."""
    scfg = (config or {}).get("strategy_config", (config or {}).get("parking", {}))
    strategy = get_strategy(use_case, scfg)
    return EventEngine.default(strategy=strategy)


def _resolve_use_case(filename):
    """Look up cached use_case and strategy config for a filename."""
    uc = _use_case_cache.get(filename, "generic")
    scfg = _strategy_config_cache.get(filename, {})
    return uc, scfg


def analyze_frame_sdk(arr, zone_map, track_state, event_engine,
                      frame_idx=0, timestamp_sec=0.0, use_case="retail"):
    """Run detection + tracking + events on a single frame using the SDK.

    Returns a generic dict with people, events, and role/event summaries.
    """
    strategy = event_engine.strategy

    detections = detector.detect(arr)

    if not detections:
        return {
            "scene_description": "No people tracked in frame",
            "people": [],
            "events": [],
            "role_counts": {},
            "event_counts": {},
            "traffic_metrics": {"entered": 0, "exited": 0, "left_unserviced": 0},
        }

    # Dedup + merge
    centroids = [d.centroid for d in detections]
    confidences = [d.confidence for d in detections]
    suppressed = track_state.deduplicate(centroids, confidences)
    track_ids = [d.track_id for d in detections]
    remap = track_state.merge_ids(track_ids, centroids, suppressed)
    track_ids = track_state.apply_remap(track_ids, remap)

    people = []
    current_tids = set()
    current_tracks = {}

    for j, det in enumerate(detections):
        if j in suppressed:
            continue

        tid = track_ids[j]
        cx, cy = det.centroid
        current_tids.add(tid)

        info = track_state.get_or_create(tid, timestamp_sec)
        zone = zone_map.zone_for_point(cx, cy)

        # Strategy-driven role classification
        role = strategy.classify_role(zone, track_state, tid, cx)

        info.zones_visited.add(zone or "other")

        is_new = info.prev_role is None
        if is_new and strategy.is_entry_role(role):
            info.observed_entry = True

        current_tracks[tid] = {
            "role": role,
            "prev_role": info.prev_role,
            "zone": zone,
            "is_new": is_new,
            "observed_entry": info.observed_entry,
            "zones_visited": info.zones_visited,
        }

        track_state.update_centroid(tid, cx, cy)
        info.prev_role = role

        people.append({
            "person_id": f"P{len(people)+1}",
            "track_id": int(tid),
            "confidence": det.confidence,
            "bounding_box": {
                "x_min": det.bbox[0], "y_min": det.bbox[1],
                "x_max": det.bbox[2], "y_max": det.bbox[3],
            },
            "mask": det.mask_points,
            "role": role,
            "queue_position": None,
            "first_seen_sec": info.first_seen_sec,
        })

    # Queue positions — strategy declares which role is "queue"
    special = strategy.special_roles()
    queue_role = special.get("queue_role")
    if queue_role:
        queue_people = [p for p in people if p["role"] == queue_role]
        queue_people.sort(key=lambda p: (p["bounding_box"]["x_min"] + p["bounding_box"]["x_max"]) / 2)
        for pos, p in enumerate(queue_people, 1):
            p["queue_position"] = pos

    # Track loss
    truly_lost = track_state.process_missing(current_tids)
    lost_tracks = {}
    for tid in truly_lost:
        info = track_state.remove_track(tid)
        if info:
            lost_tracks[tid] = {
                "zones_visited": info.zones_visited,
                "last_role": info.prev_role or "unknown",
                "observed_entry": info.observed_entry,
            }

    # Events
    frame_events = event_engine.evaluate_frame(
        video_id="studio",
        frame_idx=frame_idx,
        timestamp_sec=timestamp_sec,
        current_tracks=current_tracks,
        lost_tracks=lost_tracks,
    )

    event_dicts = [{"track_id": e.track_id, "event_type": e.event_type,
                    "details": e.details} for e in frame_events]

    # Generic role and event counts
    from collections import Counter
    role_counts = Counter(p["role"] for p in people)
    event_type_counts = Counter(e.event_type for e in frame_events)

    desc = f"{strategy.name.title()} surveillance frame with {len(people)} people detected"

    return {
        "scene_description": desc,
        "people": people,
        "events": event_dicts,
        "role_counts": dict(role_counts),
        "event_counts": dict(event_type_counts),
        "traffic_metrics": {
            "entered": sum(1 for e in frame_events if "arrived" in e.event_type or "entered" in e.event_type),
            "exited": sum(1 for e in frame_events if "exited" in e.event_type or "completed" in e.event_type),
            "left_unserviced": sum(1 for e in frame_events if "abandoned" in e.event_type or "unserviced" in e.event_type),
        },
    }


# ---------- API Routes ----------

@app.route("/api/sample-files", methods=["GET"])
def sample_files():
    """List available sample images and videos."""
    images = sorted([f.name for f in RAW_VIDEO_DIR.glob("*.png")])
    videos = sorted([f.name for f in RAW_VIDEO_DIR.glob("*.mp4")])
    return jsonify({"images": images, "videos": videos})


@app.route("/api/analyze-image", methods=["POST"])
def analyze_image():
    """Analyze a single image."""
    zones = parse_zones(request)
    counter = parse_counter(request)
    conf = float(request.args.get("conf", 0.3))
    detector.confidence = conf
    detector.reset_tracker()

    # Resolve use_case from filename if available
    data_peek = request.get_json(silent=True) or {}
    uc_key = data_peek.get("path", "")
    use_case, pcfg = _resolve_use_case(uc_key)

    zone_map = _build_zone_map(zones, counter, use_case=use_case, config=pcfg)
    track_state = TrackState()
    event_engine = _build_event_engine(use_case=use_case, config=pcfg)

    if "file" in request.files:
        file = request.files["file"]
        img = Image.open(file.stream).convert("RGB")
    else:
        data = request.get_json(silent=True) or {}
        filename = data.get("path", "")
        filepath = RAW_VIDEO_DIR / filename
        if not filepath.exists():
            return jsonify({"error": f"File not found: {filename}"}), 404
        img = Image.open(filepath).convert("RGB")

    arr = np.array(img)
    analysis = analyze_frame_sdk(arr, zone_map, track_state, event_engine,
                                 use_case=use_case)

    return jsonify({
        "width": img.width,
        "height": img.height,
        "image": frame_to_b64(img),
        "analysis": analysis,
        "zones_used": zones if zones else None,
        "counter_used": counter,
    })


@app.route("/api/analyze-video", methods=["POST"])
def analyze_video():
    """Analyze a video file frame by frame with persistent tracking."""
    zones = parse_zones(request)
    counter = parse_counter(request)
    conf = float(request.args.get("conf", 0.3))
    sample_fps = int(request.args.get("fps", 1))

    # Fall back to cached zones when request doesn't include them
    if not zones:
        data_pre = request.get_json(silent=True) or {}
        cache_key = data_pre.get("path", "")
        if cache_key in _zone_cache:
            cached = _zone_cache[cache_key]
            zones = cached.get("zones", {})
            if not counter and cached.get("counter"):
                counter = cached["counter"]

    detector.confidence = conf
    detector.reset_tracker()

    # Resolve use_case from the filename (set when auto-zones loads config)
    data_uc = request.get_json(silent=True) or {}
    uc_key = data_uc.get("path", "")
    use_case, pcfg = _resolve_use_case(uc_key)

    zone_map = _build_zone_map(zones, counter, use_case=use_case, config=pcfg)
    track_state = TrackState()
    event_engine = _build_event_engine(use_case=use_case, config=pcfg)

    if "file" in request.files:
        file = request.files["file"]
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        file.save(tmp.name)
        video_path = tmp.name
        cleanup = True
    else:
        data = request.get_json(silent=True) or {}
        filename = data.get("path", "")
        filepath = RAW_VIDEO_DIR / filename
        if not filepath.exists():
            return jsonify({"error": f"File not found: {filename}"}), 404
        video_path = str(filepath)
        cleanup = False

    try:
        container = av.open(video_path)
        stream = container.streams.video[0]
        fps = float(stream.average_rate)
        frame_interval = max(1, int(fps / sample_fps))
        time_base = float(stream.time_base)

        frames = []
        cumulative_entered = 0
        cumulative_exited = 0
        cumulative_unserviced = 0

        for i, frame in enumerate(container.decode(video=0)):
            if i % frame_interval != 0:
                continue

            img = frame.to_image()
            arr = np.array(img)
            if float(arr.mean()) < 10:
                continue

            ts = round(frame.pts * time_base if frame.pts else i / fps, 3)
            analysis = analyze_frame_sdk(arr, zone_map, track_state, event_engine,
                                         frame_idx=i, timestamp_sec=ts,
                                         use_case=use_case)

            tm = analysis.get("traffic_metrics", {})
            cumulative_entered += tm.get("entered", 0)
            cumulative_exited += tm.get("exited", 0)
            cumulative_unserviced += tm.get("left_unserviced", 0)

            analysis["cumulative_traffic"] = {
                "entered": cumulative_entered,
                "exited": cumulative_exited,
                "left_unserviced": cumulative_unserviced,
            }

            frames.append({
                "frame_idx": i,
                "timestamp": ts,
                "image": frame_to_b64(img),
                "analysis": analysis,
            })

        container.close()

        return jsonify({
            "total_frames": len(frames),
            "fps": fps,
            "sample_fps": sample_fps,
            "frames": frames,
            "zones_used": zones if zones else None,
            "counter_used": counter,
            "cumulative_traffic": {
                "entered": cumulative_entered,
                "exited": cumulative_exited,
                "left_unserviced": cumulative_unserviced,
            },
        })
    finally:
        if cleanup:
            os.unlink(video_path)


@app.route("/api/zones", methods=["GET"])
def get_zones():
    return jsonify({})


@app.route("/api/counter", methods=["GET"])
def get_counter():
    """Return the counter region polygon."""
    return jsonify({"counter": _last_auto_counter})


# ---------- Auto Zone Detection ----------

_zone_cache = {}


def _extract_reference_frame(video_path):
    """Extract first bright frame from a video."""
    container = av.open(video_path)
    for i, frame in enumerate(container.decode(video=0)):
        arr = np.array(frame.to_image())
        if float(arr.mean()) > 30:
            container.close()
            return arr
        if i > 30:
            break
    container.close()
    return None


@app.route("/api/auto-zones", methods=["POST"])
def auto_zones():
    """Load zone config for a video.

    Zone configs are created by the retail-zone-setup skill
    and stored as JSON files in the configs/ directory,
    or set via POST /api/set-zones.
    """
    global _last_auto_counter

    data = request.get_json(silent=True) or {}
    filename = data.get("path", "")

    # Return cached zones if available
    if filename in _zone_cache:
        return jsonify(_zone_cache[filename])

    # Try loading from config file
    config_name = Path(filename).stem + ".json"
    config_path = ZONE_CONFIG_DIR / config_name
    if config_path.exists():
        import json as _json
        with open(config_path) as f:
            config = _json.load(f)
        counter_val = config.get("counter") or config.get("counter_region")
        result = {
            "zones": config.get("zones", {}),
            "counter": counter_val,
            "detected": list(config.get("zones", {}).keys()),
            "method": "config",
        }
        if counter_val:
            result["detected"].append("counter")
            _last_auto_counter = counter_val

        # Cache use_case and strategy config for downstream endpoints
        uc = config.get("use_case", "generic")
        _use_case_cache[filename] = uc
        _strategy_config_cache[filename] = {
            "strategy_config": config.get("strategy_config", config.get("parking", {})),
            "zone_priority": config.get("zone_priority"),
            "role_map": config.get("role_map"),
        }
        result["use_case"] = uc

        # Add reference frame
        filepath = RAW_VIDEO_DIR / filename
        if filepath.exists():
            ref = _extract_reference_frame(str(filepath))
            if ref is not None:
                result["reference_frame"] = frame_to_b64(Image.fromarray(ref), quality=70)
        _zone_cache[filename] = result
        return jsonify(result)

    # No config found — return reference frame with empty zones
    filepath = RAW_VIDEO_DIR / filename
    if not filepath.exists():
        return jsonify({"error": f"File not found: {filename}"}), 404

    result = {
        "zones": {},
        "counter": None,
        "detected": [],
        "method": "none",
        "message": "No zone config found. Use the onboarding skill to configure zones.",
    }

    if filename.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
        ref = _extract_reference_frame(str(filepath))
        if ref is not None:
            result["reference_frame"] = frame_to_b64(Image.fromarray(ref), quality=70)
    else:
        img = Image.open(filepath).convert("RGB")
        result["reference_frame"] = frame_to_b64(img, quality=70)

    return jsonify(result)


@app.route("/api/set-zones", methods=["POST"])
def set_zones():
    """Accept a zone config from the skill and cache it.

    Expects JSON: {path, zones, counter, save (optional, default true)}
    Stores in memory cache and optionally writes to configs/ directory.
    """
    global _last_auto_counter

    data = request.get_json(silent=True) or {}
    filename = data.get("path", "")
    zones = data.get("zones", {})
    counter = data.get("counter")
    save = data.get("save", True)

    if not filename:
        return jsonify({"error": "path is required"}), 400

    result = {
        "zones": zones,
        "counter": counter,
        "detected": list(zones.keys()) + (["counter"] if counter else []),
        "method": "manual",
    }

    # Add reference frame
    filepath = RAW_VIDEO_DIR / filename
    if filepath.exists() and filename.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
        ref = _extract_reference_frame(str(filepath))
        if ref is not None:
            result["reference_frame"] = frame_to_b64(Image.fromarray(ref), quality=70)

    if counter:
        _last_auto_counter = counter

    _zone_cache[filename] = result

    # Persist to config file
    if save:
        import json as _json
        ZONE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config_path = ZONE_CONFIG_DIR / (Path(filename).stem + ".json")
        with open(config_path, "w") as f:
            _json.dump({"zones": zones, "counter": counter, "source_video": filename}, f, indent=2)

    return jsonify(result)


@app.route("/api/walkthrough", methods=["POST"])
def walkthrough():
    """Analyze a video for the pipeline walkthrough.

    Returns a smaller set of frames with full analysis + events,
    designed for stepping through the pipeline stages in the studio.
    """
    zones = parse_zones(request)
    counter = parse_counter(request)
    conf = float(request.args.get("conf", 0.3))
    max_frames = int(request.args.get("max_frames", 10))

    data = request.get_json(silent=True) or {}
    filename = data.get("path", "")

    # Fall back to cached zones when request doesn't include them
    if not zones and filename in _zone_cache:
        cached = _zone_cache[filename]
        zones = cached.get("zones", {})
        if not counter and cached.get("counter"):
            counter = cached["counter"]

    detector.confidence = conf
    detector.reset_tracker()

    use_case, pcfg = _resolve_use_case(filename)
    zone_map = _build_zone_map(zones, counter, use_case=use_case, config=pcfg)
    track_state = TrackState()
    event_engine = _build_event_engine(use_case=use_case, config=pcfg)
    filepath = RAW_VIDEO_DIR / filename
    if not filepath.exists():
        return jsonify({"error": f"File not found: {filename}"}), 404
    video_path = str(filepath)

    container = av.open(video_path)
    stream = container.streams.video[0]
    fps = float(stream.average_rate)
    time_base = float(stream.time_base)

    sample_fps = 1
    frame_interval = max(1, int(fps / sample_fps))

    frames = []
    all_events = []

    for i, frame in enumerate(container.decode(video=0)):
        if i % frame_interval != 0:
            continue

        img = frame.to_image()
        arr = np.array(img)
        if float(arr.mean()) < 10:
            continue

        ts = round(frame.pts * time_base if frame.pts else i / fps, 3)
        analysis = analyze_frame_sdk(arr, zone_map, track_state, event_engine,
                                     frame_idx=i, timestamp_sec=ts,
                                     use_case=use_case)

        frame_events = analysis.get("events", [])
        all_events.extend(frame_events)

        frames.append({
            "frame_idx": i,
            "timestamp": ts,
            "image": frame_to_b64(img),
            "analysis": analysis,
            "events": frame_events,
            "cumulative_events": list(all_events),
        })

    container.close()

    if len(frames) > max_frames:
        step = len(frames) / max_frames
        indices = [int(i * step) for i in range(max_frames)]
        if len(frames) - 1 not in indices:
            indices.append(len(frames) - 1)
        frames = [frames[i] for i in indices]

    return jsonify({
        "frames": frames,
        "all_events": all_events,
        "total_frames_analyzed": len(frames),
        "zones_used": zones if zones else None,
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
