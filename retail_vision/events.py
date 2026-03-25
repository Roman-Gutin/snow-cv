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
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from retail_vision.strategies import UseCaseStrategy

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


class EventEngine:
    """Evaluates event rules against track state each frame.

    Delegates use-case-specific logic to a UseCaseStrategy.

    Usage:
        from retail_vision.strategies import get_strategy
        strategy = get_strategy("parking", parking_config)
        engine = EventEngine.default(strategy=strategy)
        events = engine.evaluate_frame(...)
    """

    def __init__(self, rules: list[EventRule] | None = None,
                 strategy: UseCaseStrategy | None = None):
        self.rules = rules or []
        self._strategy = strategy
        # Mutable state dict — strategies read/write their own keys here
        self._state: dict[str, Any] = {}

    @property
    def strategy(self) -> UseCaseStrategy:
        if self._strategy is None:
            from retail_vision.strategies import RetailStrategy
            self._strategy = RetailStrategy()
        return self._strategy

    @strategy.setter
    def strategy(self, s: UseCaseStrategy):
        self._strategy = s

    # Backward-compat properties so existing code that reads these doesn't break
    @property
    def use_case(self) -> str:
        return self.strategy.name

    @use_case.setter
    def use_case(self, val: str):
        # Allow setting for backward compat — but prefer passing strategy
        pass

    @classmethod
    def from_yaml(cls, path: str | Path, strategy: UseCaseStrategy | None = None) -> EventEngine:
        import yaml
        with open(path) as f:
            d = yaml.safe_load(f)
        rules = [EventRule.from_dict(rd) for rd in d.get("rules", [])]
        return cls(rules, strategy=strategy)

    @classmethod
    def default(cls, strategy: UseCaseStrategy | None = None) -> EventEngine:
        """Load the built-in default rules matching current pipeline behavior."""
        default_path = Path(__file__).parent / "defaults" / "event_rules.yaml"
        if default_path.exists():
            return cls.from_yaml(default_path, strategy=strategy)
        return cls(_build_default_rules(), strategy=strategy)

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

        Delegates to self.strategy for use-case-specific event logic,
        then evaluates custom YAML rules on top.
        """
        events = []
        strat = self.strategy

        # Track-appeared and zone-transition
        for tid, tinfo in current_tracks.items():
            role = tinfo["role"]
            prev_role = tinfo.get("prev_role")

            if tinfo.get("is_new", False):
                for evt_type, details in strat.eval_appeared(
                        tid, role, timestamp_sec, self._state):
                    events.append(Event(video_id, tid, evt_type, timestamp_sec,
                                        frame_idx, details, feed_name))
                # Custom track_appeared rules
                for rule in self.rules:
                    if rule.trigger == "track_appeared":
                        if not rule.to_zones or role in rule.to_zones:
                            events.append(Event(video_id, tid, rule.name,
                                                timestamp_sec, frame_idx,
                                                {"role": role}, feed_name))

            if prev_role is not None and prev_role != role:
                for evt_type, details in strat.eval_transition(
                        tid, prev_role, role, timestamp_sec, self._state):
                    events.append(Event(video_id, tid, evt_type, timestamp_sec,
                                        frame_idx, details, feed_name))
                # Custom zone_transition rules
                for rule in self.rules:
                    if rule.trigger != "zone_transition":
                        continue
                    from_match = not rule.from_zones or prev_role in rule.from_zones
                    to_match = not rule.to_zones or role in rule.to_zones
                    if from_match and to_match:
                        events.append(Event(video_id, tid, rule.name,
                                            timestamp_sec, frame_idx,
                                            {"from_role": prev_role, "to_role": role},
                                            feed_name))

        # Track-lost
        for tid, tinfo in lost_tracks.items():
            for evt_type, details in strat.eval_lost(
                    tid, tinfo, timestamp_sec, self._state):
                events.append(Event(video_id, tid, evt_type, timestamp_sec,
                                    frame_idx, details, feed_name))
            # Custom track_lost rules
            zones_visited = tinfo.get("zones_visited", set())
            last_role = tinfo.get("last_role", "unknown")
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
                if conds.get("observed_entry") and not tinfo.get("observed_entry", False):
                    match = False
                if match:
                    events.append(Event(video_id, tid, rule.name,
                                        timestamp_sec, frame_idx,
                                        {"zones_visited": sorted(zones_visited),
                                         "last_role": last_role}, feed_name))

        # Frame-level events (unstaffed detection, dwell tracking, etc.)
        for item in strat.eval_frame_level(
                current_tracks, timestamp_sec, frame_has_employee,
                frame_queue_count, self._state):
            evt_type, tid, details = item
            events.append(Event(video_id, tid, evt_type, timestamp_sec,
                                frame_idx, details, feed_name))

        return events

    def reset(self):
        """Reset stateful rule state (between videos/segments)."""
        self.strategy.reset_state(self._state)
        self._state.clear()


def _build_default_rules() -> list[EventRule]:
    """Fallback default rules if YAML not found."""
    return []
