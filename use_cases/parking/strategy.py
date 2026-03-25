"""
Parking strategy — ticket machine confusion/frustration detection,
transaction flow tracking.
"""

from __future__ import annotations

import os
from snow_cv.strategies import UseCaseStrategy, register_strategy


PARKING_ZONE_PRIORITY = ["exit_vehicle", "ticket_machine", "gate_area", "approach_lane"]

PARKING_ROLE_MAP = {
    "exit_vehicle": "exited_vehicle",
    "ticket_machine": "at_machine",
    "gate_area": "at_gate",
    "approach_lane": "approaching",
}

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
        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "snow_cv", "defaults", "parking_event_rules.yaml")
        path = os.path.normpath(path)
        return path if os.path.exists(path) else None

    def classify_role(self, zone, track_state, tid, cx):
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

        if prev_role not in _MACHINE_ROLES and role in _MACHINE_ROLES:
            events.append(("machine_interaction_started",
                           {"from_role": prev_role}))
            dwell_starts[tid] = ts

        elif prev_role in _MACHINE_ROLES and role not in _MACHINE_ROLES:
            dwell = round(ts - dwell_starts.get(tid, ts), 3)
            events.append(("machine_interaction_ended",
                           {"to_role": role, "dwell_sec": dwell}))
            dwell_starts.pop(tid, None)
            prolonged_fired.discard(tid)

        if prev_role not in _EXIT_VEHICLE_ROLES and role in _EXIT_VEHICLE_ROLES:
            events.append(("driver_exited_vehicle",
                           {"from_role": prev_role}))
            if self.config.get("exit_vehicle_is_frustration", True):
                events.append(("confusion_detected",
                               {"reason": "driver_exited_vehicle"}))

        if prev_role not in _GATE_ROLES and role in _GATE_ROLES:
            events.append(("gate_approached", {"from_role": prev_role}))

        return events

    def eval_lost(self, tid, tinfo, ts, engine_state):
        events = []
        zones_visited = tinfo.get("zones_visited", set())
        last_role = tinfo.get("last_role", "unknown")

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


# Auto-register when imported
register_strategy("parking", ParkingStrategy)
