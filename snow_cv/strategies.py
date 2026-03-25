"""
Use-case strategy pattern — pluggable role classification and event logic.

Each use case (retail, parking, etc.) implements a strategy that defines:
  - How to classify a person's role from their zone
  - What events to emit on track appear / transition / loss / per-frame
  - What zone defaults to use

Adding a new use case = one new Strategy subclass + register it.
No changes to pipeline.py, events.py, or server.py.

Bundled strategies (retail, parking) live in use_cases/ and auto-register
on import. This file contains only the ABC, GenericStrategy, and registry.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

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

        Returns list of (event_type, track_id, details).
        Default: no frame-level events.
        """
        return []

    def reset_state(self, engine_state: dict):
        """Reset any per-video stateful tracking in engine_state."""
        pass


# ============================================================
# Generic strategy — the net-new / unbiased default
# ============================================================

class GenericStrategy(UseCaseStrategy):
    """Generic strategy for net-new use cases.

    Uses zone names as-is for roles. Emits generic events
    (track_appeared, zone_changed, track_lost) without any
    use-case-specific assumptions.

    This is the default when no use_case is specified — the
    onboarding flow should ask the user what they want to measure
    before selecting a specialized strategy.
    """

    name = "generic"

    def classify_role(self, zone, track_state, tid, cx):
        """Role = zone name. No mapping, no assumptions."""
        track_state.clear_direction(tid)
        return zone or "unknown"

    def is_entry_role(self, role):
        """In generic mode, any role could be an entry. Defer to config."""
        entry_roles = self.config.get("entry_roles", [])
        return role in entry_roles if entry_roles else False

    def eval_appeared(self, tid, role, ts, engine_state):
        return [("track_appeared", {"role": role})]

    def eval_transition(self, tid, prev_role, role, ts, engine_state):
        return [("zone_changed", {"from_role": prev_role, "to_role": role})]

    def eval_lost(self, tid, tinfo, ts, engine_state):
        return [("track_lost", {
            "zones_visited": sorted(tinfo.get("zones_visited", set())),
            "last_role": tinfo.get("last_role", "unknown"),
        })]


# ============================================================
# Registry
# ============================================================

_STRATEGY_REGISTRY: dict[str, type[UseCaseStrategy]] = {
    "generic": GenericStrategy,
}


def register_strategy(name: str, cls: type[UseCaseStrategy]):
    """Register a new use-case strategy."""
    _STRATEGY_REGISTRY[name] = cls


def get_strategy(use_case: str, config: dict | None = None) -> UseCaseStrategy:
    """Look up and instantiate a strategy by use_case name.

    Falls back to GenericStrategy if the use_case is not registered,
    logging a warning instead of raising.
    """
    cls = _STRATEGY_REGISTRY.get(use_case)
    if cls is None:
        available = ", ".join(sorted(_STRATEGY_REGISTRY.keys()))
        log.warning(
            "Unknown use_case '%s'. Registered: %s. Falling back to generic.",
            use_case, available,
        )
        cls = GenericStrategy
    return cls(config)
