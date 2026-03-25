"""
Retail strategy — entrance direction, queue/service/employee zones,
counter-unstaffed detection, abandonment tracking.
"""

from __future__ import annotations

import os
from snow_cv.strategies import UseCaseStrategy, register_strategy


# Retail zone defaults — only applied when config explicitly uses retail strategy
RETAIL_ZONE_PRIORITY = ["employee", "service", "queue", "entrance"]

RETAIL_ROLE_MAP = {
    "employee": "employee",
    "service": "customer_being_served",
    "queue": "in_queue",
    "entrance": "at_entrance",
}

EXAMPLE_ZONES = {
    "employee": [[0.02, 0.15], [0.28, 0.15], [0.28, 0.55], [0.02, 0.55]],
    "service":  [[0.28, 0.35], [0.46, 0.35], [0.46, 0.95], [0.28, 0.95]],
    "queue":    [[0.46, 0.15], [0.75, 0.15], [0.75, 0.95], [0.46, 0.95]],
    "entrance": [[0.75, 0.15], [0.98, 0.15], [0.98, 0.95], [0.75, 0.95]],
}

EXAMPLE_COUNTER_REGION = [[0.03, 0.55], [0.35, 0.55], [0.35, 0.95], [0.03, 0.95]]


class RetailStrategy(UseCaseStrategy):
    """Retail store: entrance direction, queue, service, employee zones."""

    name = "retail"

    def zone_priority(self):
        return list(RETAIL_ZONE_PRIORITY)

    def role_map(self):
        return dict(RETAIL_ROLE_MAP)

    def special_roles(self):
        return {"staff_role": "employee", "queue_role": "in_queue"}

    def default_zones(self):
        return dict(EXAMPLE_ZONES)

    def default_counter(self):
        return list(EXAMPLE_COUNTER_REGION)

    def default_event_rules_path(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "snow_cv", "defaults", "event_rules.yaml")
        path = os.path.normpath(path)
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


# Auto-register when imported
register_strategy("retail", RetailStrategy)
