"""
Zone geometry — polygon definitions, point-in-polygon tests, ZoneMap.

Supports:
  - Loading zones from config dict or env vars
  - Priority-based zone classification (configurable per use case)

Zone *detection* is handled externally (e.g. the onboarding skill),
not in this module.  This file is pure geometry.

No use-case-specific defaults live here. Zone priority and role maps
are provided by the active strategy or config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


def point_in_polygon(x: float, y: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test. Coordinates are normalized 0-1."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ---- Backward-compat exports ----
# These constants are kept so existing code that imports them doesn't break,
# but they are NOT used as defaults anywhere in the core SDK.
# Use-case-specific constants now live in use_cases/<name>/strategy.py.

RETAIL_ZONE_PRIORITY = ["employee", "service", "queue", "entrance"]
RETAIL_ROLE_MAP = {
    "employee": "employee",
    "service": "customer_being_served",
    "queue": "in_queue",
    "entrance": "at_entrance",
}
DEFAULT_ZONE_PRIORITY = RETAIL_ZONE_PRIORITY
DEFAULT_ROLE_MAP = RETAIL_ROLE_MAP

PARKING_ZONE_PRIORITY = ["exit_vehicle", "ticket_machine", "gate_area", "approach_lane"]
PARKING_ROLE_MAP = {
    "exit_vehicle": "exited_vehicle",
    "ticket_machine": "at_machine",
    "gate_area": "at_gate",
    "approach_lane": "approaching",
}


@dataclass
class ZoneMap:
    """Named zone polygons with priority-based classification.

    Args:
        zones: {zone_name: [[x,y], ...]} polygon dict (normalized 0-1)
        counter_region: polygon for the counter fixture (optional)
        priority: zone names in classification priority order (first match wins).
                  Defaults to empty — the strategy or config must provide this.
        role_map: zone_name -> role string mapping.
                  Defaults to empty — zone names are used as-is.
    """
    zones: dict[str, list[list[float]]] = field(default_factory=dict)
    counter_region: list[list[float]] | None = None
    priority: list[str] = field(default_factory=list)
    role_map: dict[str, str] = field(default_factory=dict)

    def classify(self, cx: float, cy: float) -> str:
        """Classify a centroid into a role based on zone priority.

        Returns the role string (e.g., 'employee', 'in_queue') or 'other'.
        """
        for zone_name in self.priority:
            if zone_name in self.zones and point_in_polygon(cx, cy, self.zones[zone_name]):
                return self.role_map.get(zone_name, zone_name)
        return "other"

    def zone_for_point(self, cx: float, cy: float) -> str | None:
        """Return the zone name (not role) for a point, or None."""
        for zone_name in self.priority:
            if zone_name in self.zones and point_in_polygon(cx, cy, self.zones[zone_name]):
                return zone_name
        return None

    def to_dict(self) -> dict:
        """Serialize for JSON/YAML storage."""
        return {
            "zones": self.zones,
            "counter_region": self.counter_region,
            "priority": self.priority,
            "role_map": self.role_map,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ZoneMap:
        return cls(
            zones=d.get("zones", {}),
            counter_region=d.get("counter_region"),
            priority=d.get("priority", []),
            role_map=d.get("role_map", {}),
        )
