"""
RetailPipeline — orchestrates detection, tracking, events, and output.

Wires together all SDK components into a single run() call.
Supports both local iteration (CsvOutput) and SPCS batch (SnowflakeOutput).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from collections import Counter

import av
import numpy as np

from retail_vision.config import StoreConfig, FeedConfig, EXAMPLE_ZONES, EXAMPLE_COUNTER_REGION
from retail_vision.zones import ZoneMap
from retail_vision.detector import PersonDetector, Detection
from retail_vision.tracker import TrackState
from retail_vision.events import EventEngine, Event
from retail_vision.strategies import get_strategy
from retail_vision.feeds import MultiFeedManager
from retail_vision.trace import InferenceTracer
from retail_vision.output import OutputWriter, CsvOutput

log = logging.getLogger(__name__)

FLUSH_EVERY_FRAMES = 3600


def generate_video_id(filename: str, store_id: str = "", segment_id: str = "",
                       feed_name: str = "") -> str:
    """Generate deterministic video ID from identifiers."""
    key = filename
    if store_id:
        key = f"{store_id}_{key}"
    if feed_name:
        key = f"{key}_{feed_name}"
    if segment_id:
        key = f"{key}_{segment_id}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


class RetailPipeline:
    """Main pipeline: video in → structured event data out.

    Args:
        config: store/feed configuration
        output: where to write results (default: CsvOutput to temp dir)
        tracer: inference tracer (default: enabled)
        event_engine: event rule engine (default: built-in rules)
        feed_manager: multi-feed manager (default: from config)
        detector: person detector (default: YOLOv8n-seg)
    """

    def __init__(
        self,
        config: StoreConfig | None = None,
        output: OutputWriter | None = None,
        tracer: InferenceTracer | None = None,
        event_engine: EventEngine | None = None,
        feed_manager: MultiFeedManager | None = None,
        detector: PersonDetector | None = None,
    ):
        self.config = config or StoreConfig(store_id="default")
        self.strategy = get_strategy(
            self.config.use_case, self.config.parking)
        self.output = output or CsvOutput()
        self.tracer = tracer or InferenceTracer(
            store_id=self.config.store_id, enabled=True)
        self.event_engine = event_engine or self._load_event_engine()
        self.feed_manager = feed_manager or MultiFeedManager(self.config.feed_links)
        self._detector = detector

    def _load_event_engine(self) -> EventEngine:
        if self.config.event_rules_path and os.path.exists(self.config.event_rules_path):
            engine = EventEngine.from_yaml(
                self.config.event_rules_path, strategy=self.strategy)
        elif self.config.use_case == "parking":
            default_path = os.path.join(
                os.path.dirname(__file__), "defaults", "parking_event_rules.yaml")
            if os.path.exists(default_path):
                engine = EventEngine.from_yaml(default_path, strategy=self.strategy)
            else:
                engine = EventEngine.default(strategy=self.strategy)
        else:
            engine = EventEngine.default(strategy=self.strategy)
        return engine

    def _get_detector(self, feed: FeedConfig) -> PersonDetector:
        if self._detector is None:
            self._detector = PersonDetector(
                model_name=feed.model_name,
                confidence=feed.confidence_threshold,
            )
        return self._detector

    @staticmethod
    def _extract_reference_frame(video_path: str) -> np.ndarray | None:
        """Extract a usable reference frame from the first few seconds of video."""
        container = av.open(video_path)
        ref_frame = None
        for i, frame in enumerate(container.decode(video=0)):
            arr = np.array(frame.to_image())
            if float(arr.mean()) > 30:
                ref_frame = arr
                break
            if i > 60:
                break
        container.close()
        return ref_frame

    def run(
        self,
        video_path: str,
        feed_name: str = "",
        start_sec: float = 0,
        end_sec: float = 0,
        segment_id: str = "",
    ) -> dict:
        """Process a single video feed.

        Args:
            video_path: local path to video file
            feed_name: which feed config to use (default: first feed)
            start_sec: segment start time in seconds
            end_sec: segment end time (0 = end of video)
            segment_id: unique segment ID for parallel processing

        Returns:
            Summary dict with counts and timing.
        """
        # Resolve feed config
        if feed_name:
            feed = self.config.get_feed(feed_name)
        elif self.config.feeds:
            feed = self.config.feeds[0]
            feed_name = feed.name
        else:
            feed = FeedConfig(name="main")
            feed_name = "main"

        # Fall back to example zones if none configured.
        # Zone detection is handled externally by the retail-zone-setup skill.
        if not feed.zones:
            log.warning("No zones configured — using example zones as fallback. "
                        "Run the retail-zone-setup skill to define real zones for %s", video_path)
            feed.zones = dict(EXAMPLE_ZONES)
            feed.counter_region = list(EXAMPLE_COUNTER_REGION)

        # Set up zone map from feed config, with strategy-driven defaults
        priority = feed.zone_priority or self.strategy.zone_priority()
        role_map = feed.role_map or self.strategy.role_map()

        zone_kwargs = {"zones": feed.zones, "counter_region": feed.counter_region}
        if priority is not None:
            zone_kwargs["priority"] = priority
        if role_map is not None:
            zone_kwargs["role_map"] = role_map
        zone_map = ZoneMap(**zone_kwargs)
        detector = self._get_detector(feed)
        detector.reset_tracker()

        track_state = TrackState()
        self.event_engine.reset()
        self.tracer.feed_name = feed_name
        self.tracer.reset()

        video_id = generate_video_id(
            os.path.basename(video_path),
            store_id=self.config.store_id,
            segment_id=segment_id,
            feed_name=feed_name,
        )

        # Probe video
        probe = av.open(video_path)
        probe_stream = probe.streams.video[0]
        fps = float(probe_stream.average_rate)
        probe_tb = float(probe_stream.time_base)
        video_duration = float(probe_stream.duration * probe_tb) if probe_stream.duration else 0
        probe.close()

        if end_sec <= 0:
            end_sec = video_duration
        segment_duration = end_sec - start_sec
        is_short = video_duration < segment_duration

        log.info("Processing %s feed=%s segment=%.0f-%.0fs (%.1fs of %.1fs) fps=%.1f",
                 video_path, feed_name, start_sec, end_sec, segment_duration, video_duration, fps)

        # Open video for seek-based extraction
        container = av.open(video_path)
        stream = container.streams.video[0]
        tb = float(stream.time_base)
        sample_fps = feed.sample_fps

        # Generate target timestamps
        target_times = []
        t = start_sec
        interval = 1.0 / sample_fps
        while t < end_sec:
            target_times.append(t)
            t += interval
        total_targets = len(target_times)

        log.info("Will process %d frames (sample_fps=%d)", total_targets, sample_fps)

        # Accumulators
        det_rows = []
        evt_rows = []
        total_det = 0
        total_evt = 0
        event_counts = Counter()
        processed = 0
        t0 = time.time()

        for target_sec in target_times:
            actual_seek = target_sec % video_duration if is_short else target_sec
            seek_pts = int(actual_seek / tb)
            container.seek(seek_pts, backward=True, any_frame=False, stream=stream)

            frame = None
            for candidate in container.decode(video=0):
                candidate_ts = float(candidate.pts * tb) if candidate.pts else 0
                if candidate_ts >= actual_seek - 0.01:
                    frame = candidate
                    break

            if frame is None:
                processed += 1
                continue

            arr = np.array(frame.to_image())
            if float(arr.mean()) < 10:
                processed += 1
                continue

            ts = round(target_sec, 3)
            fi = int(round(target_sec * sample_fps))

            # --- Trace: begin ---
            self.tracer.begin_frame(fi, ts)

            # --- Detect ---
            detections = detector.detect(arr)
            self.tracer.record_raw_detections(len(detections))

            if not detections:
                self.tracer.record_after_dedup(0)
                self.tracer.record_after_merge(0)
                self.tracer.record_tracks(active=0, new=0, lost=0)
                self.tracer.record_events([])
                self.tracer.end_frame()
                processed += 1
                continue

            # --- Dedup ---
            centroids = [d.centroid for d in detections]
            confidences = [d.confidence for d in detections]
            suppressed = track_state.deduplicate(centroids, confidences)
            self.tracer.record_after_dedup(len(detections) - len(suppressed))

            # --- ID Merge ---
            track_ids = [d.track_id for d in detections]
            remap = track_state.merge_ids(track_ids, centroids, suppressed)
            track_ids = track_state.apply_remap(track_ids, remap)
            self.tracer.record_after_merge(len(detections) - len(suppressed))

            # --- Classify and track ---
            current_tids = set()
            frame_has_employee = False
            queue_people = []
            current_tracks = {}
            new_track_count = 0

            for j, det in enumerate(detections):
                if j in suppressed:
                    continue

                tid = track_ids[j]
                cx, cy = det.centroid
                current_tids.add(tid)

                info = track_state.get_or_create(tid, ts)
                zone = zone_map.zone_for_point(cx, cy)

                # Role classification — strategy-driven
                role = self.strategy.classify_role(zone, track_state, tid, cx)

                info.zones_visited.add(zone or "other")

                is_new = info.prev_role is None
                if is_new:
                    new_track_count += 1
                    if self.strategy.is_entry_role(role):
                        info.observed_entry = True
                        # Cross-feed matching: check if this entrance
                        # correlates with an exit on another camera
                        journey_id = self.feed_manager.try_match_entrance(
                            feed_name, zone or "entrance", tid, ts)
                        if journey_id:
                            log.debug("Cross-feed match: feed=%s tid=%d journey=%s",
                                      feed_name, tid, journey_id)

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

                if role == "employee":
                    frame_has_employee = True
                if role == "in_queue":
                    queue_people.append((cx, tid, len(det_rows)))

                det_rows.append((
                    video_id, fi, ts, int(tid), role,
                    det.confidence,
                    det.bbox[0], det.bbox[1], det.bbox[2], det.bbox[3],
                    round(cx, 4), round(cy, 4),
                    None,  # queue_position (set below)
                    json.dumps(det.mask_points) if det.mask_points else None,
                    feed_name,
                ))

            # Queue positions
            queue_people.sort(key=lambda t: t[0])
            for pos, (_, _, row_idx) in enumerate(queue_people, 1):
                row = list(det_rows[row_idx])
                row[12] = pos
                det_rows[row_idx] = tuple(row)

            # --- Track loss ---
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
                    # Record exit for cross-feed
                    if info.prev_role == "exiting" and info.observed_entry:
                        last_zone = None
                        for z in info.zones_visited:
                            last_zone = z
                        self.feed_manager.record_exit(
                            feed_name, last_zone or "entrance", tid, ts)

            self.tracer.record_tracks(
                active=len(current_tids),
                new=new_track_count,
                lost=len(truly_lost),
            )
            self.tracer.record_confidences(
                [d.confidence for j, d in enumerate(detections) if j not in suppressed])

            # --- Events ---
            frame_events = self.event_engine.evaluate_frame(
                video_id=video_id,
                frame_idx=fi,
                timestamp_sec=ts,
                current_tracks=current_tracks,
                lost_tracks=lost_tracks,
                frame_has_employee=frame_has_employee,
                frame_queue_count=len(queue_people),
                feed_name=feed_name,
            )

            for evt in frame_events:
                # Stamp journey_id from cross-feed manager if available
                if not evt.journey_id and evt.track_id:
                    evt.journey_id = self.feed_manager.get_journey_id(
                        feed_name, evt.track_id)
                evt_rows.append((
                    evt.video_id, int(evt.track_id), evt.event_type,
                    evt.timestamp_sec, evt.frame_idx,
                    json.dumps(evt.details) if evt.details else None,
                    evt.feed_name, evt.journey_id,
                ))
                event_counts[evt.event_type] += 1

            self.tracer.record_events([e.event_type for e in frame_events])
            self.tracer.end_frame()

            processed += 1

            # Periodic flush
            if processed % FLUSH_EVERY_FRAMES == 0:
                self.output.write_detections(det_rows)
                self.output.write_events(evt_rows)
                total_det += len(det_rows)
                total_evt += len(evt_rows)
                det_rows = []
                evt_rows = []
                self.feed_manager.prune_stale(ts)
                elapsed = time.time() - t0
                rate = processed / elapsed if elapsed > 0 else 0
                log.info("Flushed at frame %d/%d: %d det, %d evt, %.1f fps",
                         processed, total_targets, total_det, total_evt, rate)

            # Progress log
            if processed % 100 == 0:
                elapsed = time.time() - t0
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total_targets - processed) / rate if rate > 0 else 0
                log.info("Frame %d/%d @%.0fs: %d people, queue=%d, rate=%.1ffps, ETA=%.0fs",
                         processed, total_targets, ts, len(current_tids),
                         len(queue_people), rate, eta)

        container.close()

        # Final flush
        self.output.write_detections(det_rows)
        self.output.write_events(evt_rows)
        total_det += len(det_rows)
        total_evt += len(evt_rows)

        # Traces
        trace_rows = self.tracer.get_rows(video_id)
        self.output.write_traces(trace_rows)

        # Metadata
        zone_config_json = json.dumps(zone_map.to_dict())
        self.output.write_metadata((
            video_id, os.path.basename(video_path), video_duration,
            fps, zone_config_json, feed_name,
        ))

        self.output.flush()

        elapsed = time.time() - t0
        summary = {
            "video_id": video_id,
            "store_id": self.config.store_id,
            "feed_name": feed_name,
            "frames_processed": processed,
            "total_detections": total_det,
            "total_events": total_evt,
            "events_by_type": dict(event_counts),
            "elapsed_sec": round(elapsed, 1),
            "fps": round(processed / elapsed, 2) if elapsed > 0 else 0,
            "trace_summary": self.tracer.summary(),
        }

        log.info("Complete: %d frames, %d det, %d evt in %.1fs (%.1f fps)",
                 processed, total_det, total_evt, elapsed,
                 processed / elapsed if elapsed > 0 else 0)

        return summary
