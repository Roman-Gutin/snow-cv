"""
Zone geometry — polygon definitions, point-in-polygon tests, ZoneMap.

Supports:
  - Loading zones from config dict or env vars
  - Priority-based zone classification (employee > service > queue > entrance)

Zone *detection* is handled externally (e.g. the retail-zone-setup skill),
not in this module.  This file is pure geometry.
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


# Default zone classification priority (highest first).
# Customers can override by providing a custom priority list in config.
DEFAULT_ZONE_PRIORITY = ["employee", "service", "queue", "entrance"]

# Role mapping: zone name -> display role name
DEFAULT_ROLE_MAP = {
    "employee": "employee",
    "service": "customer_being_served",
    "queue": "in_queue",
    "entrance": "at_entrance",
}


@dataclass
class ZoneMap:
    """Named zone polygons with priority-based classification.

    Args:
        zones: {zone_name: [[x,y], ...]} polygon dict (normalized 0-1)
        counter_region: polygon for the counter fixture (optional)
        priority: zone names in classification priority order (first match wins)
        role_map: zone_name -> role string mapping
    """
    zones: dict[str, list[list[float]]] = field(default_factory=dict)
    counter_region: list[list[float]] | None = None
    priority: list[str] = field(default_factory=lambda: list(DEFAULT_ZONE_PRIORITY))
    role_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROLE_MAP))

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
            priority=d.get("priority", list(DEFAULT_ZONE_PRIORITY)),
            role_map=d.get("role_map", dict(DEFAULT_ROLE_MAP)),
        )
