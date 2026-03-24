"""
Declarative event rule engine — YAML-driven event detection.

Replaces hardcoded if/else chains with configurable rules.
Customers edit YAML, not Python.

Rule types:
  - zone_transition: fires when a track moves between zones
  - track_appeared: fires when a new track is first seen
  - track_lost: fires when a track disappears (after grace period)
  - unstaffed: fires when no employee is present while queue > 0
  - cross_feed: fires on multi-camera handoff (evaluated by MultiFeedManager)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


@dataclass
class EventRule:
    """A single event rule definition."""
    name: str
    trigger: str  # zone_transition | track_appeared | track_lost | unstaffed | cross_feed
    from_zones: list[str] = field(default_factory=list)
    to_zones: list[str] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    # cross_feed fields
    from_feed: str = ""
    to_feed: str = ""
    max_delay_sec: float = 10.0

    @classmethod
    def from_dict(cls, d: dict) -> EventRule:
        return cls(
            name=d["name"],
            trigger=d.get("trigger", "zone_transition"),
            from_zones=d.get("from_zones", []),
            to_zones=d.get("to_zones", []),
            conditions=d.get("conditions", {}),
            from_feed=d.get("from_feed", ""),
            to_feed=d.get("to_feed", ""),
            max_delay_sec=d.get("max_delay_sec", 10.0),
        )


@dataclass
class Event:
    """An emitted event."""
    video_id: str
    track_id: int
    event_type: str
    timestamp_sec: float
    frame_idx: int
    details: dict[str, Any] = field(default_factory=dict)
    feed_name: str = ""
    journey_id: str = ""


# Convenience sets for role matching
_QUEUE_ROLES = {"in_queue"}
_SERVICE_ROLES = {"customer_being_served"}
_EMPLOYEE_ROLES = {"employee"}


class EventEngine:
    """Evaluates event rules against track state each frame.

    Usage:
        engine = EventEngine.from_yaml("event_rules.yaml")
        # or:
        engine = EventEngine.default()

        events = engine.evaluate_frame(...)
    """

    def __init__(self, rules: list[EventRule] | None = None):
        self.rules = rules or []
        self._unstaffed_since: float | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> EventEngine:
        import yaml
        with open(path) as f:
            d = yaml.safe_load(f)
        rules = [EventRule.from_dict(rd) for rd in d.get("rules", [])]
        return cls(rules)

    @classmethod
    def default(cls) -> EventEngine:
        """Load the built-in default rules matching current pipeline behavior."""
        default_path = Path(__file__).parent / "defaults" / "event_rules.yaml"
        if default_path.exists():
            return cls.from_yaml(default_path)
        return cls(_build_default_rules())

    def evaluate_frame(
        self,
        video_id: str,
        frame_idx: int,
        timestamp_sec: float,
        current_tracks: dict[int, dict],
        lost_tracks: dict[int, dict],
        frame_has_employee: bool,
        frame_queue_count: int,
        feed_name: str = "",
    ) -> list[Event]:
        """Evaluate all rules for one frame.

        Args:
            current_tracks: {tid: {"role": str, "prev_role": str|None, "zone": str|None,
                                    "is_new": bool, "observed_entry": bool,
                                    "zones_visited": set, ...}}
            lost_tracks: {tid: {"zones_visited": set, "last_role": str,
                                "observed_entry": bool}}
            frame_has_employee: whether any employee is in frame
            frame_queue_count: number of people in queue

        Returns:
            List of emitted Event objects.
        """
        events = []

        # Track-appeared and zone-transition rules
        for tid, tinfo in current_tracks.items():
            role = tinfo["role"]
            prev_role = tinfo.get("prev_role")

            if tinfo.get("is_new", False):
                events.extend(self._eval_appeared(
                    video_id, tid, role, timestamp_sec, frame_idx, feed_name))

            if prev_role is not None and prev_role != role:
                events.extend(self._eval_transitions(
                    video_id, tid, prev_role, role, timestamp_sec, frame_idx, feed_name))

        # Track-lost rules
        for tid, tinfo in lost_tracks.items():
            events.extend(self._eval_lost(
                video_id, tid, tinfo, timestamp_sec, frame_idx, feed_name))

        # Unstaffed detection
        events.extend(self._eval_unstaffed(
            video_id, frame_has_employee, frame_queue_count,
            timestamp_sec, frame_idx, feed_name))

        return events

    def _eval_appeared(self, video_id, tid, role, ts, fi, feed_name) -> list[Event]:
        """Evaluate track_appeared rules."""
        events = []

        # Built-in: classify as entered_store vs pre_existing
        is_entry = role in ("entering", "at_entrance")
        if is_entry:
            events.append(Event(video_id, tid, "entered_store", ts, fi,
                                {"role": role}, feed_name))
        else:
            events.append(Event(video_id, tid, "pre_existing", ts, fi,
                                {"role": role}, feed_name))

        # If first seen already in a significant role, emit that too
        if role in _QUEUE_ROLES:
            events.append(Event(video_id, tid, "queue_entered", ts, fi,
                                {"from_role": role}, feed_name))
        elif role in _SERVICE_ROLES:
            events.append(Event(video_id, tid, "service_started", ts, fi,
                                {"from_role": "new"}, feed_name))
        elif role in _EMPLOYEE_ROLES:
            events.append(Event(video_id, tid, "employee_arrived", ts, fi, {}, feed_name))

        # Custom track_appeared rules
        for rule in self.rules:
            if rule.trigger == "track_appeared":
                if not rule.to_zones or role in rule.to_zones:
                    events.append(Event(video_id, tid, rule.name, ts, fi,
                                        {"role": role}, feed_name))

        return events

    def _eval_transitions(self, video_id, tid, prev_role, role, ts, fi, feed_name) -> list[Event]:
        """Evaluate zone_transition rules."""
        events = []

        # Built-in transitions
        if prev_role not in _QUEUE_ROLES and role in _QUEUE_ROLES:
            events.append(Event(video_id, tid, "queue_entered", ts, fi,
                                {"from_role": prev_role}, feed_name))
        elif prev_role in _QUEUE_ROLES and role not in _QUEUE_ROLES:
            events.append(Event(video_id, tid, "queue_exited", ts, fi,
                                {"to_role": role}, feed_name))

        if prev_role not in _SERVICE_ROLES and role in _SERVICE_ROLES:
            events.append(Event(video_id, tid, "service_started", ts, fi,
                                {"from_role": prev_role}, feed_name))
        elif prev_role in _SERVICE_ROLES and role not in _SERVICE_ROLES:
            events.append(Event(video_id, tid, "service_ended", ts, fi,
                                {"to_role": role}, feed_name))

        if prev_role not in _EMPLOYEE_ROLES and role in _EMPLOYEE_ROLES:
            events.append(Event(video_id, tid, "employee_arrived", ts, fi, {}, feed_name))
        elif prev_role in _EMPLOYEE_ROLES and role not in _EMPLOYEE_ROLES:
            events.append(Event(video_id, tid, "employee_left", ts, fi,
                                {"to_role": role}, feed_name))

        # Custom zone_transition rules
        for rule in self.rules:
            if rule.trigger != "zone_transition":
                continue
            from_match = not rule.from_zones or prev_role in rule.from_zones
            to_match = not rule.to_zones or role in rule.to_zones
            if from_match and to_match:
                # Skip if this duplicates a built-in event name
                if rule.name not in ("queue_entered", "queue_exited", "service_started",
                                     "service_ended", "employee_arrived", "employee_left"):
                    events.append(Event(video_id, tid, rule.name, ts, fi,
                                        {"from_role": prev_role, "to_role": role}, feed_name))

        return events

    def _eval_lost(self, video_id, tid, tinfo, ts, fi, feed_name) -> list[Event]:
        """Evaluate track_lost rules."""
        events = []
        zones_visited = tinfo.get("zones_visited", set())
        last_role = tinfo.get("last_role", "unknown")
        observed_entry = tinfo.get("observed_entry", False)

        # Built-in: service_ended / employee_left on track loss
        if last_role in _SERVICE_ROLES:
            events.append(Event(video_id, tid, "service_ended", ts, fi,
                                {"reason": "track_lost"}, feed_name))
        if last_role in _EMPLOYEE_ROLES:
            events.append(Event(video_id, tid, "employee_left", ts, fi,
                                {"reason": "track_lost"}, feed_name))

        # Built-in: abandonment classification
        was_customer = zones_visited & {"in_queue", "at_entrance"}
        was_served = "service" in zones_visited or "customer_being_served" in zones_visited
        if was_customer and not was_served:
            if observed_entry:
                events.append(Event(video_id, tid, "abandoned", ts, fi,
                                    {"zones_visited": sorted(zones_visited),
                                     "last_role": last_role}, feed_name))
            else:
                events.append(Event(video_id, tid, "unserviced", ts, fi,
                                    {"zones_visited": sorted(zones_visited),
                                     "last_role": last_role,
                                     "reason": "entry_not_observed"}, feed_name))

        # Built-in: exited_store
        if last_role == "exiting" and observed_entry:
            events.append(Event(video_id, tid, "exited_store", ts, fi,
                                {"last_role": last_role}, feed_name))

        # Custom track_lost rules
        for rule in self.rules:
            if rule.trigger != "track_lost":
                continue
            conds = rule.conditions
            match = True
            if "visited_any" in conds:
                if not (zones_visited & set(conds["visited_any"])):
                    match = False
            if "not_visited" in conds:
                if zones_visited & set(conds["not_visited"]):
                    match = False
            if conds.get("observed_entry") and not observed_entry:
                match = False
            if match and rule.name not in ("abandoned", "unserviced", "exited_store"):
                events.append(Event(video_id, tid, rule.name, ts, fi,
                                    {"zones_visited": sorted(zones_visited),
                                     "last_role": last_role}, feed_name))

        return events

    def _eval_unstaffed(self, video_id, has_employee, queue_count, ts, fi, feed_name) -> list[Event]:
        """Detect counter-unstaffed-while-waiting periods."""
        events = []
        if not has_employee and queue_count > 0:
            if self._unstaffed_since is None:
                self._unstaffed_since = ts
                events.append(Event(video_id, 0, "counter_unstaffed_start", ts, fi,
                                    {"queue_length": queue_count}, feed_name))
        elif has_employee and self._unstaffed_since is not None:
            dur = round(ts - self._unstaffed_since, 3)
            events.append(Event(video_id, 0, "counter_unstaffed_end", ts, fi,
                                {"duration_sec": dur, "queue_length": queue_count}, feed_name))
            self._unstaffed_since = None
        elif queue_count == 0 and self._unstaffed_since is not None:
            dur = round(ts - self._unstaffed_since, 3)
            events.append(Event(video_id, 0, "counter_unstaffed_end", ts, fi,
                                {"duration_sec": dur, "queue_length": 0,
                                 "reason": "queue_emptied"}, feed_name))
            self._unstaffed_since = None
        return events

    def reset(self):
        """Reset stateful rule state (between videos/segments)."""
        self._unstaffed_since = None


def _build_default_rules() -> list[EventRule]:
    """Fallback default rules if YAML not found."""
    return []
