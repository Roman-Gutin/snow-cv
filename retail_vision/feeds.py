"""
Multi-feed manager — coordinates N camera feeds per store.

Handles:
  - Running independent trackers per camera
  - Cross-feed temporal correlation (exit cam A → enter cam B)
  - Shared journey IDs across feeds
  - Merged event streams

For SPCS batch: each feed runs in its own container. Cross-feed
correlation is done in SQL post-processing using temporal joins.

For local studio iteration: feeds run sequentially or in parallel
threads, with in-memory cross-feed correlation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from retail_vision.config import FeedConfig, FeedLink

log = logging.getLogger(__name__)


@dataclass
class FeedExitEvent:
    """Records a person exiting a feed, for cross-feed matching."""
    feed_name: str
    zone: str
    track_id: int
    timestamp_sec: float
    journey_id: str


class MultiFeedManager:
    """Manages cross-feed correlation for multi-camera stores.

    In local/studio mode, this runs in-memory and can correlate exits
    from one camera with entrances on another in real-time.

    In SPCS batch mode, each feed runs independently. Cross-feed
    correlation is done via SQL temporal joins after all containers finish.

    Args:
        feed_links: list of FeedLink definitions from config
    """

    def __init__(self, feed_links: list[FeedLink] | None = None):
        self.feed_links = feed_links or []
        self._pending_exits: list[FeedExitEvent] = []
        self._journey_map: dict[tuple[str, int], str] = {}  # (feed_name, track_id) -> journey_id

    def record_exit(self, feed_name: str, zone: str, track_id: int, timestamp_sec: float):
        """Record a person exiting a feed zone (potential cross-feed handoff)."""
        # Check if any feed_link matches this exit
        for link in self.feed_links:
            if link.from_feed == feed_name and link.from_zone == zone:
                journey_id = self._get_journey_id(feed_name, track_id)
                self._pending_exits.append(FeedExitEvent(
                    feed_name=feed_name,
                    zone=zone,
                    track_id=track_id,
                    timestamp_sec=timestamp_sec,
                    journey_id=journey_id,
                ))
                log.debug("Recorded exit: feed=%s zone=%s tid=%d ts=%.1f journey=%s",
                          feed_name, zone, track_id, timestamp_sec, journey_id)

    def try_match_entrance(
        self,
        feed_name: str,
        zone: str,
        track_id: int,
        timestamp_sec: float,
    ) -> str | None:
        """Try to match a new track entering a feed with a pending exit.

        Returns journey_id if matched, None otherwise.
        """
        for link in self.feed_links:
            if link.to_feed != feed_name or link.to_zone != zone:
                continue

            # Look for a matching pending exit
            best_exit = None
            best_dt = link.max_delay_sec
            for i, exit_evt in enumerate(self._pending_exits):
                if exit_evt.feed_name != link.from_feed:
                    continue
                if exit_evt.zone != link.from_zone:
                    continue
                dt = timestamp_sec - exit_evt.timestamp_sec
                if 0 <= dt <= best_dt:
                    best_dt = dt
                    best_exit = i

            if best_exit is not None:
                exit_evt = self._pending_exits.pop(best_exit)
                # Link the new track to the same journey
                self._journey_map[(feed_name, track_id)] = exit_evt.journey_id
                log.info("Cross-feed match: %s/%s tid=%d -> %s/%s tid=%d "
                         "journey=%s dt=%.1fs",
                         exit_evt.feed_name, exit_evt.zone, exit_evt.track_id,
                         feed_name, zone, track_id, exit_evt.journey_id, best_dt)
                return exit_evt.journey_id

        return None

    def _get_journey_id(self, feed_name: str, track_id: int) -> str:
        """Get or create a journey ID for a track."""
        key = (feed_name, track_id)
        if key not in self._journey_map:
            self._journey_map[key] = uuid.uuid4().hex[:12]
        return self._journey_map[key]

    def prune_stale(self, current_time: float):
        """Remove pending exits that are too old to match."""
        if not self.feed_links:
            return
        max_delay = max(link.max_delay_sec for link in self.feed_links)
        cutoff = current_time - max_delay * 2
        self._pending_exits = [e for e in self._pending_exits if e.timestamp_sec > cutoff]

    def get_journey_id(self, feed_name: str, track_id: int) -> str:
        """Get the journey ID for a track (empty string if none)."""
        return self._journey_map.get((feed_name, track_id), "")

    def reset(self):
        """Clear all state."""
        self._pending_exits.clear()
        self._journey_map.clear()

    @staticmethod
    def sql_cross_feed_correlation() -> str:
        """Return SQL for post-hoc cross-feed correlation.

        Use this after batch processing to link tracks across cameras.
        """
        return """
-- Cross-feed correlation: match exits from one camera with entrances on another.
-- Run this after all feed containers have finished writing to PERSON_EVENTS.
WITH exits AS (
    SELECT VIDEO_ID, FEED_NAME, TRACK_ID, TIMESTAMP_SEC,
           DETAILS:"last_role"::VARCHAR AS last_role
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'exited_store'
),
entrances AS (
    SELECT VIDEO_ID, FEED_NAME, TRACK_ID, TIMESTAMP_SEC,
           DETAILS:"role"::VARCHAR AS entry_role
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'entered_store'
)
SELECT
    e.VIDEO_ID,
    e.FEED_NAME AS exit_feed,
    e.TRACK_ID AS exit_track,
    e.TIMESTAMP_SEC AS exit_time,
    n.FEED_NAME AS entry_feed,
    n.TRACK_ID AS entry_track,
    n.TIMESTAMP_SEC AS entry_time,
    ROUND(n.TIMESTAMP_SEC - e.TIMESTAMP_SEC, 2) AS handoff_delay_sec
FROM exits e
JOIN entrances n
    ON e.VIDEO_ID = n.VIDEO_ID
    AND e.FEED_NAME != n.FEED_NAME
    AND n.TIMESTAMP_SEC BETWEEN e.TIMESTAMP_SEC AND e.TIMESTAMP_SEC + 10
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY n.VIDEO_ID, n.FEED_NAME, n.TRACK_ID
    ORDER BY ABS(n.TIMESTAMP_SEC - e.TIMESTAMP_SEC)
) = 1
ORDER BY e.TIMESTAMP_SEC;
"""
