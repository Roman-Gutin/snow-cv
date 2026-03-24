"""
Structured inference tracing — per-frame observability for debugging.

Records detection pipeline stages, confidence distributions, anomalies,
and container identity. Written to INFERENCE_TRACES table for post-run
debugging of bad inferences.

Usage:
    tracer = InferenceTracer(store_id="store_001", container_id="SCALE_JOB_001")

    tracer.begin_frame(frame_idx=100, timestamp_sec=100.0)
    tracer.record_raw_detections(5)
    tracer.record_after_dedup(4)
    tracer.record_after_merge(4)
    tracer.record_confidences([0.85, 0.72, 0.91, 0.65])
    tracer.record_tracks(active=4, new=1, lost=0)
    tracer.record_events(["queue_entered", "entered_store"])
    tracer.end_frame()

    # After processing, get all traces
    traces = tracer.get_traces()

    # Or query: "which frames had warnings?"
    bad_frames = tracer.get_flagged_frames()
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class FrameTrace:
    """Trace record for a single frame."""
    frame_idx: int = 0
    timestamp_sec: float = 0.0
    raw_detections: int = 0
    after_dedup: int = 0
    after_merge: int = 0
    tracks_active: int = 0
    tracks_new: int = 0
    tracks_lost: int = 0
    confidence_min: float = 0.0
    confidence_max: float = 0.0
    confidence_mean: float = 0.0
    events_emitted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    processing_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "frame_idx": self.frame_idx,
            "timestamp_sec": self.timestamp_sec,
            "raw_detections": self.raw_detections,
            "after_dedup": self.after_dedup,
            "after_merge": self.after_merge,
            "tracks_active": self.tracks_active,
            "tracks_new": self.tracks_new,
            "tracks_lost": self.tracks_lost,
            "confidence_min": round(self.confidence_min, 3),
            "confidence_max": round(self.confidence_max, 3),
            "confidence_mean": round(self.confidence_mean, 3),
            "events_emitted": self.events_emitted,
            "warnings": self.warnings,
            "processing_ms": round(self.processing_ms, 2),
        }

    def to_row(self, video_id: str, store_id: str, feed_name: str,
               container_id: str) -> tuple:
        """Convert to a flat tuple for CSV/Snowflake output."""
        return (
            video_id, store_id, feed_name, container_id,
            self.frame_idx, self.timestamp_sec,
            self.raw_detections, self.after_dedup, self.after_merge,
            self.tracks_active, self.tracks_new, self.tracks_lost,
            round(self.confidence_min, 3),
            round(self.confidence_max, 3),
            round(self.confidence_mean, 3),
            json.dumps(self.events_emitted),
            json.dumps(self.warnings),
            round(self.processing_ms, 2),
        )


# Anomaly detection thresholds
TRACK_SPIKE_THRESHOLD = 5      # track count jump between frames
LOW_CONFIDENCE_THRESHOLD = 0.4  # all detections below this = warning
SLOW_FRAME_MULTIPLIER = 2.0     # frame > 2x avg processing time = warning


class InferenceTracer:
    """Collects per-frame trace data for observability.

    Args:
        store_id: store identifier for trace records
        feed_name: camera feed name
        container_id: SPCS job/container name for correlation
        enabled: set False to disable tracing (zero overhead)
        sample_rate: trace every Nth frame (1 = all frames, 10 = every 10th)
    """

    def __init__(
        self,
        store_id: str = "",
        feed_name: str = "",
        container_id: str = "",
        enabled: bool = True,
        sample_rate: int = 1,
    ):
        self.store_id = store_id
        self.feed_name = feed_name
        self.container_id = container_id
        self.enabled = enabled
        self.sample_rate = max(1, sample_rate)

        self._traces: list[FrameTrace] = []
        self._current: FrameTrace | None = None
        self._frame_start: float = 0
        self._frame_count: int = 0
        self._prev_track_count: int = 0
        self._total_processing_ms: float = 0
        self._processed_frames: int = 0

    def begin_frame(self, frame_idx: int, timestamp_sec: float):
        """Start tracing a new frame."""
        self._frame_count += 1
        if not self.enabled:
            return
        if self._frame_count % self.sample_rate != 0:
            self._current = None
            return
        self._current = FrameTrace(frame_idx=frame_idx, timestamp_sec=timestamp_sec)
        self._frame_start = time.time()

    def record_raw_detections(self, count: int):
        if self._current:
            self._current.raw_detections = count

    def record_after_dedup(self, count: int):
        if self._current:
            self._current.after_dedup = count

    def record_after_merge(self, count: int):
        if self._current:
            self._current.after_merge = count

    def record_confidences(self, confidences: list[float]):
        if not self._current or not confidences:
            return
        self._current.confidence_min = min(confidences)
        self._current.confidence_max = max(confidences)
        self._current.confidence_mean = sum(confidences) / len(confidences)

        # Low confidence warning
        if all(c < LOW_CONFIDENCE_THRESHOLD for c in confidences):
            self._current.warnings.append("low_confidence_frame")

    def record_tracks(self, active: int, new: int, lost: int):
        if not self._current:
            return
        self._current.tracks_active = active
        self._current.tracks_new = new
        self._current.tracks_lost = lost

        # Track count spike warning
        if self._prev_track_count > 0:
            delta = abs(active - self._prev_track_count)
            if delta > TRACK_SPIKE_THRESHOLD:
                self._current.warnings.append(
                    f"track_count_spike:{self._prev_track_count}->{active}")
        self._prev_track_count = active

    def record_events(self, event_types: list[str]):
        if self._current:
            self._current.events_emitted = list(event_types)

    def end_frame(self):
        """Finalize the current frame trace."""
        if not self._current:
            return

        elapsed_ms = (time.time() - self._frame_start) * 1000
        self._current.processing_ms = elapsed_ms

        # Slow frame warning
        self._processed_frames += 1
        self._total_processing_ms += elapsed_ms
        if self._processed_frames > 10:
            avg_ms = self._total_processing_ms / self._processed_frames
            if elapsed_ms > avg_ms * SLOW_FRAME_MULTIPLIER:
                self._current.warnings.append(
                    f"slow_frame:{elapsed_ms:.0f}ms_vs_avg_{avg_ms:.0f}ms")

        self._traces.append(self._current)
        self._current = None

    def get_traces(self) -> list[FrameTrace]:
        """Return all collected traces."""
        return list(self._traces)

    def get_flagged_frames(self) -> list[FrameTrace]:
        """Return only traces with warnings."""
        return [t for t in self._traces if t.warnings]

    def get_rows(self, video_id: str) -> list[tuple]:
        """Return all traces as flat tuples for CSV/Snowflake output."""
        return [t.to_row(video_id, self.store_id, self.feed_name, self.container_id)
                for t in self._traces]

    def summary(self) -> dict:
        """Return summary statistics for the run."""
        if not self._traces:
            return {"frames_traced": 0}

        flagged = self.get_flagged_frames()
        all_warnings = []
        for t in flagged:
            all_warnings.extend(t.warnings)

        warning_types = {}
        for w in all_warnings:
            wtype = w.split(":")[0]
            warning_types[wtype] = warning_types.get(wtype, 0) + 1

        return {
            "frames_traced": len(self._traces),
            "frames_flagged": len(flagged),
            "warning_counts": warning_types,
            "avg_processing_ms": round(self._total_processing_ms / max(1, self._processed_frames), 2),
            "total_events": sum(len(t.events_emitted) for t in self._traces),
        }

    def reset(self):
        """Clear all traces."""
        self._traces.clear()
        self._current = None
        self._frame_count = 0
        self._prev_track_count = 0
        self._total_processing_ms = 0
        self._processed_frames = 0
