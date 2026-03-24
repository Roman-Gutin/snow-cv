"""
Track state management — maintains per-track history across frames.

Handles:
  - Centroid history (last N positions for direction detection)
  - Zone visitation history
  - Previous role for transition detection
  - Missing-frame grace period before declaring track loss
  - Observed entry tracking for abandonment classification
  - Cross-frame ID merge (re-links broken ByteTrack IDs)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    """State for a single tracked person."""
    track_id: int
    positions: list[float] = field(default_factory=list)  # recent centroid x values
    direction: str | None = None  # "entering" | "exiting" | None
    zones_visited: set[str] = field(default_factory=set)
    prev_role: str | None = None
    observed_entry: bool = False
    last_centroid: tuple[float, float] | None = None
    missing_frames: int = 0
    first_seen_sec: float = 0.0


class TrackState:
    """Manages state for all tracked persons across frames.

    Args:
        dedup_dist: normalized distance threshold for centroid deduplication
        merge_dist: normalized distance threshold for cross-frame ID merging
        missing_grace: consecutive absent frames before declaring track lost
        max_history: max centroid positions to keep per track
    """

    def __init__(
        self,
        dedup_dist: float = 0.04,
        merge_dist: float = 0.06,
        missing_grace: int = 3,
        max_history: int = 5,
    ):
        self.dedup_dist = dedup_dist
        self.merge_dist = merge_dist
        self.missing_grace = missing_grace
        self.max_history = max_history
        self.tracks: dict[int, TrackInfo] = {}

    def get_or_create(self, tid: int, timestamp_sec: float = 0.0) -> TrackInfo:
        """Get existing track or create a new one."""
        if tid not in self.tracks:
            self.tracks[tid] = TrackInfo(track_id=tid, first_seen_sec=timestamp_sec)
        return self.tracks[tid]

    def deduplicate(
        self,
        centroids: list[tuple[float, float]],
        confidences: list[float],
    ) -> set[int]:
        """Suppress overlapping detections. Returns set of suppressed indices."""
        suppressed = set()
        for a in range(len(centroids)):
            if a in suppressed:
                continue
            for b in range(a + 1, len(centroids)):
                if b in suppressed:
                    continue
                dx = centroids[a][0] - centroids[b][0]
                dy = centroids[a][1] - centroids[b][1]
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < self.dedup_dist:
                    if confidences[a] >= confidences[b]:
                        suppressed.add(b)
                    else:
                        suppressed.add(a)
        return suppressed

    def merge_ids(
        self,
        track_ids: list[int],
        centroids: list[tuple[float, float]],
        suppressed: set[int],
    ) -> dict[int, int]:
        """Merge new IDs back to recently-lost tracks by centroid proximity.

        Returns {new_tid: old_tid} remap dict.
        """
        active_raw = set()
        for idx, tid in enumerate(track_ids):
            if idx not in suppressed:
                active_raw.add(tid)

        remap = {}
        for idx, tid in enumerate(track_ids):
            if idx in suppressed:
                continue
            if tid in self.tracks:
                continue  # already known, no merge needed
            cx, cy = centroids[idx]
            best_old = None
            best_dist = self.merge_dist
            for old_tid, info in self.tracks.items():
                if old_tid in active_raw:
                    continue
                if info.last_centroid is not None:
                    ocx, ocy = info.last_centroid
                    d = ((cx - ocx) ** 2 + (cy - ocy) ** 2) ** 0.5
                    if d < best_dist:
                        best_dist = d
                        best_old = old_tid
            if best_old is not None:
                remap[tid] = best_old
        return remap

    def apply_remap(self, track_ids: list[int], remap: dict[int, int]) -> list[int]:
        """Apply ID remap in-place and reset missing counters for merged tracks."""
        result = list(track_ids)
        for i, tid in enumerate(result):
            if tid in remap:
                result[i] = remap[tid]
                self.tracks[remap[tid]].missing_frames = 0
        return result

    def update_centroid(self, tid: int, cx: float, cy: float):
        """Update centroid history for a track."""
        info = self.tracks.get(tid)
        if info is None:
            return
        info.positions.append(cx)
        if len(info.positions) > self.max_history:
            info.positions = info.positions[-self.max_history:]
        info.last_centroid = (cx, cy)

    def detect_direction(self, tid: int, cx: float) -> str | None:
        """Detect enter/exit direction for entrance zone occupants.

        Returns 'entering', 'exiting', or None if undetermined.
        Direction is sticky: once set, it persists while in entrance zone.
        """
        info = self.tracks.get(tid)
        if info is None:
            return None

        if info.direction in ("entering", "exiting"):
            return info.direction

        if len(info.positions) >= 2:
            dx = cx - info.positions[-1]
            if dx < -0.005:
                info.direction = "entering"
                return "entering"
            elif dx > 0.005:
                info.direction = "exiting"
                return "exiting"
        return None

    def clear_direction(self, tid: int):
        """Clear sticky direction when person leaves entrance zone."""
        info = self.tracks.get(tid)
        if info is not None:
            info.direction = None

    def process_missing(self, current_tids: set[int]) -> set[int]:
        """Process track absence. Returns set of truly lost track IDs.

        Tracks must be absent for `missing_grace` consecutive frames
        before they are declared lost.
        """
        # Reset counter for present tracks
        for tid in current_tids:
            if tid in self.tracks:
                self.tracks[tid].missing_frames = 0

        truly_lost = set()
        for tid, info in list(self.tracks.items()):
            if tid not in current_tids and info.prev_role is not None:
                info.missing_frames += 1
                if info.missing_frames >= self.missing_grace:
                    truly_lost.add(tid)

        return truly_lost

    def remove_track(self, tid: int) -> TrackInfo | None:
        """Remove a track and return its final state."""
        return self.tracks.pop(tid, None)

    def reset(self):
        """Clear all tracking state (e.g., between segments or videos)."""
        self.tracks.clear()
