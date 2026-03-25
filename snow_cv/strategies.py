"""
Use-case strategy pattern — pluggable role classification and event logic.

Each use case (retail, parking, etc.) implements a strategy that defines:
  - How to classify a person's role from their zone
  - What events to emit on track appear / transition / loss / per-frame
  - What zone defaults to use

Adding a new use case = one new Strategy subclass + register it.
No changes to pipeline.py, events.py, or server.py.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from snow_cv.zones import (
    ZoneMap,
    PARKING_ZONE_PRIORITY,
    PARKING_ROLE_MAP,
)

import os

log = logging.getLogger(__name__)


# ============================================================
# Abstract base
# ============================================================

class UseCaseStrategy(ABC):
    """Base class for use-case-specific pipeline behavior."""

    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # --- Zone defaults ---

    def zone_priority(self) -> list[str] | None:
        """Return default zone priority list, or None for ZoneMap defaults."""
        return None

    def role_map(self) -> dict[str, str] | None:
        """Return default zone->role mapping, or None for ZoneMap defaults."""
        return None

    def special_roles(self) -> dict[str, str]:
        """Declare roles with special pipeline semantics.

        Supported keys:
          - "staff_role": pipeline tracks whether this role is present per frame
          - "queue_role": pipeline computes queue position ordering for this role

        Return {} if your use case has no staff/queue concept.
        """
        return {}

    def default_zones(self) -> dict[str, list[list[float]]] | None:
        """Return fallback zones when none are configured, or None to require config."""
        return None

    def default_counter(self) -> list[list[float]] | None:
        """Return fallback counter region, or None."""
        return None

    def default_event_rules_path(self) -> str | None:
        """Return path to default YAML event rules for this strategy, or None."""
        return None

    # --- Role classification ---

    @abstractmethod
    def classify_role(self, zone: str | None, track_state, tid: int, cx: float) -> str:
        """Assign a role to a detection given its zone.

        Args:
            zone: zone name from ZoneMap.zone_for_point(), or None
            track_state: TrackState instance (for direction detection etc.)
            tid: track ID
            cx: centroid x coordinate

        Returns:
            Role string (e.g. "in_queue", "at_machine", "employee")
        """

    def is_entry_role(self, role: str) -> bool:
        """Return True if this role counts as an observed entry."""
        return False

    # --- Event evaluation ---
    # Each method returns a list of (event_type, details) tuples.
    # The EventEngine wraps them into Event objects.

    @abstractmethod
    def eval_appeared(self, tid: int, role: str, ts: float,
                      engine_state: dict) -> list[tuple[str, dict]]:
        """Events when a new track first appears."""

    @abstractmethod
    def eval_transition(self, tid: int, prev_role: str, role: str, ts: float,
                        engine_state: dict) -> list[tuple[str, dict]]:
        """Events when a track changes role."""

    @abstractmethod
    def eval_lost(self, tid: int, tinfo: dict, ts: float,
                  engine_state: dict) -> list[tuple[str, dict]]:
        """Events when a track disappears."""

    def eval_frame_level(self, current_tracks: dict, ts: float,
                         engine_state: dict) -> list[tuple[str, int, dict]]:
        """Per-frame events not tied to a single track transition.

        Strategies compute any needed aggregates (staff presence, queue count)
        from current_tracks directly.

        Returns list of (event_type, track_id, details).
        Default: no frame-level events.
        """
        return []

    def reset_state(self, engine_state: dict):
        """Reset any per-video stateful tracking in engine_state."""
        pass


# ============================================================
# Retail strategy
# ============================================================

class RetailStrategy(UseCaseStrategy):
    """Retail store: entrance direction, queue, service, employee zones."""

    name = "retail"

    def special_roles(self):
        return {"staff_role": "employee", "queue_role": "in_queue"}

    def default_zones(self):
        from snow_cv.config import EXAMPLE_ZONES
        return dict(EXAMPLE_ZONES)

    def default_counter(self):
        from snow_cv.config import EXAMPLE_COUNTER_REGION
        return list(EXAMPLE_COUNTER_REGION)

    def default_event_rules_path(self):
        path = os.path.join(os.path.dirname(__file__), "defaults", "event_rules.yaml")
        return path if os.path.exists(path) else None

    def classify_role(self, zone, track_state, tid, cx):
        if zone == "entrance":
            direction = track_state.detect_direction(tid, cx)
            if direction == "exiting":
                return "exiting"
            elif direction == "entering":
                return "entering"
            else:
                return "at_entrance"
        elif zone == "employee":
            track_state.clear_direction(tid)
            return "employee"
        elif zone == "service":
            track_state.clear_direction(tid)
            return "customer_being_served"
        elif zone == "queue":
            track_state.clear_direction(tid)
            return "in_queue"
        else:
            track_state.clear_direction(tid)
            return zone or "other"

    def is_entry_role(self, role):
        return role in ("entering", "at_entrance")

    def eval_appeared(self, tid, role, ts, engine_state):
        events = []
        if role in ("entering", "at_entrance"):
            events.append(("entered_store", {"role": role}))
        else:
            events.append(("pre_existing", {"role": role}))

        if role == "in_queue":
            events.append(("queue_entered", {"from_role": role}))
        elif role == "customer_being_served":
            events.append(("service_started", {"from_role": "new"}))
        elif role == "employee":
            events.append(("employee_arrived", {}))

        return events

    def eval_transition(self, tid, prev_role, role, ts, engine_state):
        events = []
        q = {"in_queue"}
        s = {"customer_being_served"}
        e = {"employee"}

        if prev_role not in q and role in q:
            events.append(("queue_entered", {"from_role": prev_role}))
        elif prev_role in q and role not in q:
            events.append(("queue_exited", {"to_role": role}))

        if prev_role not in s and role in s:
            events.append(("service_started", {"from_role": prev_role}))
        elif prev_role in s and role not in s:
            events.append(("service_ended", {"to_role": role}))

        if prev_role not in e and role in e:
            events.append(("employee_arrived", {}))
        elif prev_role in e and role not in e:
            events.append(("employee_left", {"to_role": role}))

        return events

    def eval_lost(self, tid, tinfo, ts, engine_state):
        events = []
        zones_visited = tinfo.get("zones_visited", set())
        last_role = tinfo.get("last_role", "unknown")
        observed_entry = tinfo.get("observed_entry", False)

        if last_role == "customer_being_served":
            events.append(("service_ended", {"reason": "track_lost"}))
        if last_role == "employee":
            events.append(("employee_left", {"reason": "track_lost"}))

        was_customer = zones_visited & {"in_queue", "at_entrance"}
        was_served = "service" in zones_visited or "customer_being_served" in zones_visited
        if was_customer and not was_served:
            if observed_entry:
                events.append(("abandoned", {
                    "zones_visited": sorted(zones_visited),
                    "last_role": last_role}))
            else:
                events.append(("unserviced", {
                    "zones_visited": sorted(zones_visited),
                    "last_role": last_role,
                    "reason": "entry_not_observed"}))

        if last_role == "exiting" and observed_entry:
            events.append(("exited_store", {"last_role": last_role}))

        return events

    def eval_frame_level(self, current_tracks, ts, engine_state):
        """Detect counter-unstaffed-while-waiting periods."""
        events = []
        unstaffed_since = engine_state.get("_unstaffed_since")

        # Compute aggregates from current_tracks
        has_employee = any(t["role"] == "employee" for t in current_tracks.values())
        queue_count = sum(1 for t in current_tracks.values() if t["role"] == "in_queue")

        if not has_employee and queue_count > 0:
            if unstaffed_since is None:
                engine_state["_unstaffed_since"] = ts
                events.append(("counter_unstaffed_start", 0,
                               {"queue_length": queue_count}))
        elif has_employee and unstaffed_since is not None:
            dur = round(ts - unstaffed_since, 3)
            events.append(("counter_unstaffed_end", 0,
                           {"duration_sec": dur, "queue_length": queue_count}))
            engine_state["_unstaffed_since"] = None
        elif queue_count == 0 and unstaffed_since is not None:
            dur = round(ts - unstaffed_since, 3)
            events.append(("counter_unstaffed_end", 0,
                           {"duration_sec": dur, "queue_length": 0,
                            "reason": "queue_emptied"}))
            engine_state["_unstaffed_since"] = None

        return events

    def reset_state(self, engine_state):
        engine_state.pop("_unstaffed_since", None)


# ============================================================
# Parking strategy
# ============================================================

_MACHINE_ROLES = {"at_machine"}
_EXIT_VEHICLE_ROLES = {"exited_vehicle"}
_GATE_ROLES = {"at_gate"}
_APPROACH_ROLES = {"approaching"}


class ParkingStrategy(UseCaseStrategy):
    """Parking lot: ticket machine confusion/frustration detection."""

    name = "parking"

    def zone_priority(self):
        return list(PARKING_ZONE_PRIORITY)

    def role_map(self):
        return dict(PARKING_ROLE_MAP)

    def default_event_rules_path(self):
        path = os.path.join(os.path.dirname(__file__), "defaults", "parking_event_rules.yaml")
        return path if os.path.exists(path) else None

    def classify_role(self, zone, track_state, tid, cx):
        # Parking uses ZoneMap.classify() which applies priority + role_map.
        # But classify_role receives the raw zone name, so we apply the
        # role_map here as a fallback. The pipeline should prefer
        # zone_map.classify() directly.
        track_state.clear_direction(tid)
        rm = self.role_map()
        if zone and zone in rm:
            return rm[zone]
        return zone or "other"

    def is_entry_role(self, role):
        return role in ("approaching",)

    def eval_appeared(self, tid, role, ts, engine_state):
        events = []
        dwell_starts = engine_state.setdefault("_machine_dwell_start", {})

        if role in _APPROACH_ROLES:
            events.append(("vehicle_arrived", {"role": role}))
        elif role in _MACHINE_ROLES:
            events.append(("machine_interaction_started", {"role": role}))
            dwell_starts[tid] = ts
        elif role in _EXIT_VEHICLE_ROLES:
            events.append(("driver_exited_vehicle", {"role": role}))
            if self.config.get("exit_vehicle_is_frustration", True):
                events.append(("confusion_detected",
                               {"reason": "driver_exited_vehicle"}))
        elif role in _GATE_ROLES:
            events.append(("at_gate", {"role": role}))
        else:
            events.append(("person_detected", {"role": role}))

        return events

    def eval_transition(self, tid, prev_role, role, ts, engine_state):
        events = []
        dwell_starts = engine_state.setdefault("_machine_dwell_start", {})
        prolonged_fired = engine_state.setdefault("_machine_prolonged_fired", set())

        # Entered ticket machine zone
        if prev_role not in _MACHINE_ROLES and role in _MACHINE_ROLES:
            events.append(("machine_interaction_started",
                           {"from_role": prev_role}))
            dwell_starts[tid] = ts

        # Left ticket machine zone
        elif prev_role in _MACHINE_ROLES and role not in _MACHINE_ROLES:
            dwell = round(ts - dwell_starts.get(tid, ts), 3)
            events.append(("machine_interaction_ended",
                           {"to_role": role, "dwell_sec": dwell}))
            dwell_starts.pop(tid, None)
            prolonged_fired.discard(tid)

        # Exited vehicle (frustration signal)
        if prev_role not in _EXIT_VEHICLE_ROLES and role in _EXIT_VEHICLE_ROLES:
            events.append(("driver_exited_vehicle",
                           {"from_role": prev_role}))
            if self.config.get("exit_vehicle_is_frustration", True):
                events.append(("confusion_detected",
                               {"reason": "driver_exited_vehicle"}))

        # Reached gate area
        if prev_role not in _GATE_ROLES and role in _GATE_ROLES:
            events.append(("gate_approached", {"from_role": prev_role}))

        return events

    def eval_lost(self, tid, tinfo, ts, engine_state):
        events = []
        zones_visited = tinfo.get("zones_visited", set())
        last_role = tinfo.get("last_role", "unknown")

        # Clean up dwell tracking
        dwell_starts = engine_state.get("_machine_dwell_start", {})
        prolonged_fired = engine_state.get("_machine_prolonged_fired", set())
        dwell = 0
        if tid in dwell_starts:
            dwell = round(ts - dwell_starts.pop(tid), 3)
        prolonged_fired.discard(tid)

        was_at_machine = zones_visited & {"at_machine", "ticket_machine"}
        reached_gate = zones_visited & {"at_gate", "gate_area"}

        if was_at_machine and reached_gate:
            events.append(("transaction_completed", {
                "zones_visited": sorted(zones_visited),
                "machine_dwell_sec": dwell}))
        elif was_at_machine and not reached_gate:
            events.append(("abandoned_transaction", {
                "zones_visited": sorted(zones_visited),
                "last_role": last_role,
                "machine_dwell_sec": dwell}))
        elif last_role in ("approaching",) and not was_at_machine:
            events.append(("passed_without_interaction", {
                "zones_visited": sorted(zones_visited)}))
        else:
            events.append(("person_left", {
                "zones_visited": sorted(zones_visited),
                "last_role": last_role}))

        return events

    def eval_frame_level(self, current_tracks, ts, engine_state):
        """Check dwell time at ticket machine — fires prolonged/confusion."""
        events = []
        dwell_starts = engine_state.get("_machine_dwell_start", {})
        prolonged_fired = engine_state.setdefault("_machine_prolonged_fired", set())
        threshold = self.config.get("confusion_dwell_threshold_sec", 30.0)

        for tid, tinfo in current_tracks.items():
            role = tinfo["role"]
            if role in _MACHINE_ROLES and tid in dwell_starts:
                dwell = ts - dwell_starts[tid]
                if dwell >= threshold and tid not in prolonged_fired:
                    prolonged_fired.add(tid)
                    events.append(("machine_interaction_prolonged", tid, {
                        "dwell_sec": round(dwell, 3),
                        "threshold_sec": threshold}))
                    events.append(("confusion_detected", tid, {
                        "reason": "prolonged_dwell",
                        "dwell_sec": round(dwell, 3)}))

        return events

    def reset_state(self, engine_state):
        engine_state.pop("_machine_dwell_start", None)
        engine_state.pop("_machine_prolonged_fired", None)


# ============================================================
# Registry
# ============================================================

_STRATEGY_REGISTRY: dict[str, type[UseCaseStrategy]] = {
    "retail": RetailStrategy,
    "parking": ParkingStrategy,
}


def register_strategy(name: str, cls: type[UseCaseStrategy]):
    """Register a new use-case strategy."""
    _STRATEGY_REGISTRY[name] = cls


def get_strategy(use_case: str, config: dict | None = None) -> UseCaseStrategy:
    """Look up and instantiate a strategy by use_case name.

    Raises KeyError if the use_case is not registered.
    """
    cls = _STRATEGY_REGISTRY.get(use_case)
    if cls is None:
        available = ", ".join(sorted(_STRATEGY_REGISTRY.keys()))
        raise KeyError(
            f"Unknown use_case '{use_case}'. "
            f"Registered strategies: {available}. "
            f"Register a new one with register_strategy()."
        )
    return cls(config)
