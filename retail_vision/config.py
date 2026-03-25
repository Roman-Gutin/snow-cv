"""
Store and feed configuration — single YAML defines everything for a deployment.

Supports:
  - Single-camera stores (simple case)
  - Multi-camera stores (N feeds with cross-feed links)
  - Loading from YAML file, dict, or SPCS environment variables
"""

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class FeedLink:
    """Cross-camera link: exit from one feed correlates with entry on another."""
    from_feed: str
    from_zone: str
    to_feed: str
    to_zone: str
    max_delay_sec: float = 10.0


@dataclass
class FeedConfig:
    """Configuration for a single camera feed."""
    name: str
    video_path: str = ""
    zones: dict[str, list[list[float]]] = field(default_factory=dict)
    counter_region: Optional[list[list[float]]] = None
    zone_priority: Optional[list[str]] = None
    role_map: Optional[dict[str, str]] = None
    # Processing params
    sample_fps: int = 1
    confidence_threshold: float = 0.3
    model_name: str = "yolov8n-seg.pt"

    @classmethod
    def from_dict(cls, d: dict) -> FeedConfig:
        return cls(
            name=d["name"],
            video_path=d.get("video_path", ""),
            zones=d.get("zones") or {},
            counter_region=d.get("counter_region"),
            zone_priority=d.get("zone_priority"),
            role_map=d.get("role_map"),
            sample_fps=d.get("sample_fps", 1),
            confidence_threshold=d.get("confidence_threshold", 0.3),
            model_name=d.get("model_name", "yolov8n-seg.pt"),
        )


# Example zones — NOT silently applied. Used only when customer explicitly
# opts out of vision detection or as documented examples.
# For production, zones should come from auto_detect_zones() (Florence-2).
EXAMPLE_ZONES = {
    "employee": [[0.02, 0.15], [0.28, 0.15], [0.28, 0.55], [0.02, 0.55]],
    "service":  [[0.28, 0.35], [0.46, 0.35], [0.46, 0.95], [0.28, 0.95]],
    "queue":    [[0.46, 0.15], [0.75, 0.15], [0.75, 0.95], [0.46, 0.95]],
    "entrance": [[0.75, 0.15], [0.98, 0.15], [0.98, 0.95], [0.75, 0.95]],
}
EXAMPLE_COUNTER_REGION = [[0.03, 0.55], [0.35, 0.55], [0.35, 0.95], [0.03, 0.95]]


@dataclass
class StoreConfig:
    """Top-level config for a store/site deployment."""
    store_id: str
    use_case: str = "retail"  # "retail" | "parking" — controls zone/event defaults
    feeds: list[FeedConfig] = field(default_factory=list)
    feed_links: list[FeedLink] = field(default_factory=list)
    event_rules_path: Optional[str] = None
    parking: dict = field(default_factory=dict)  # parking-specific thresholds

    # SPCS / infrastructure
    database: str = "SNOW_CV_DB"
    schema: str = "SNOW_CV_SCHEMA"
    warehouse: str = "SNOW_CV_WH"
    raw_video_stage: str = "@SNOW_CV_DB.SNOW_CV_SCHEMA.RAW_VIDEO"
    results_stage: str = "@SNOW_CV_DB.SNOW_CV_SCHEMA.RAW_VIDEO/results"

    @classmethod
    def from_yaml(cls, path: str | Path) -> StoreConfig:
        """Load store config from a YAML file."""
        import yaml
        with open(path) as f:
            d = yaml.safe_load(f)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> StoreConfig:
        """Load store config from a dict."""
        feeds = []
        for fd in d.get("feeds", []):
            feeds.append(FeedConfig.from_dict(fd))

        # Top-level zone_priority / role_map (propagated to single-camera shorthand)
        zone_priority = d.get("zone_priority")
        role_map = d.get("role_map")

        # Single-camera shorthand: if no feeds defined, create one from top-level zones
        if not feeds:
            zones = d.get("zones") or {}
            feeds.append(FeedConfig(
                name=d.get("feed_name", "main"),
                video_path=d.get("video_path", ""),
                zones=zones,
                counter_region=d.get("counter_region"),
                zone_priority=zone_priority,
                role_map=role_map,
                sample_fps=d.get("sample_fps", 1),
                confidence_threshold=d.get("confidence_threshold", 0.3),
            ))

        links = []
        for ld in d.get("feed_links", []):
            links.append(FeedLink(
                from_feed=ld["from_feed"],
                from_zone=ld["from_zone"],
                to_feed=ld["to_feed"],
                to_zone=ld["to_zone"],
                max_delay_sec=ld.get("max_delay_sec", 10.0),
            ))

        return cls(
            store_id=d.get("store_id", "unknown"),
            use_case=d.get("use_case", "retail"),
            feeds=feeds,
            feed_links=links,
            event_rules_path=d.get("event_rules_path"),
            parking=d.get("parking", {}),
            database=d.get("database", "SNOW_CV_DB"),
            schema=d.get("schema", "SNOW_CV_SCHEMA"),
            warehouse=d.get("warehouse", "SNOW_CV_WH"),
            raw_video_stage=d.get("raw_video_stage", "@SNOW_CV_DB.SNOW_CV_SCHEMA.RAW_VIDEO"),
            results_stage=d.get("results_stage", "@SNOW_CV_DB.SNOW_CV_SCHEMA.RAW_VIDEO/results"),
        )

    @classmethod
    def from_env(cls) -> StoreConfig:
        """Build config from SPCS container environment variables.

        Reads: STORE_ID, VIDEO_PATH, FEED_NAME, zone env vars (EMPLOYEE_ZONE, etc.),
        SNOWFLAKE_WAREHOUSE, EVENT_RULES_PATH, plus multi-feed FEED_LINKS JSON.
        """
        store_id = os.environ.get("STORE_ID", "unknown")
        video_path = os.environ.get("VIDEO_PATH", "")
        feed_name = os.environ.get("FEED_NAME", "main")
        warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE", "SNOW_CV_DB")

        zones = {}
        for zone_name, env_var, default in [
            ("employee", "EMPLOYEE_ZONE", EXAMPLE_ZONES["employee"]),
            ("service", "SERVICE_ZONE", EXAMPLE_ZONES["service"]),
            ("queue", "QUEUE_ZONE", EXAMPLE_ZONES["queue"]),
            ("entrance", "ENTRANCE_ZONE", EXAMPLE_ZONES["entrance"]),
        ]:
            raw = os.environ.get(env_var, "")
            if raw:
                try:
                    zones[zone_name] = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Invalid JSON in %s, using default", env_var)
                    zones[zone_name] = default
            else:
                zones[zone_name] = default

        counter_raw = os.environ.get("COUNTER_REGION", "")
        counter = EXAMPLE_COUNTER_REGION
        if counter_raw:
            try:
                counter = json.loads(counter_raw)
            except json.JSONDecodeError:
                pass

        feed = FeedConfig(
            name=feed_name,
            video_path=video_path,
            zones=zones,
            counter_region=counter,
        )

        # Multi-feed links from JSON env var (optional)
        links = []
        links_raw = os.environ.get("FEED_LINKS", "")
        if links_raw:
            try:
                for ld in json.loads(links_raw):
                    links.append(FeedLink(**ld))
            except (json.JSONDecodeError, TypeError):
                log.warning("Invalid JSON in FEED_LINKS, ignoring")

        return cls(
            store_id=store_id,
            feeds=[feed],
            feed_links=links,
            event_rules_path=os.environ.get("EVENT_RULES_PATH"),
            warehouse=warehouse,
        )

    def get_feed(self, name: str) -> FeedConfig:
        """Get a feed by name."""
        for f in self.feeds:
            if f.name == name:
                return f
        raise KeyError(f"Feed '{name}' not found in store config")

    @classmethod
    def from_video(cls, store_id: str, video_path: str,
                   feed_name: str = "main", **kwargs) -> StoreConfig:
        """Build config by running vision-based zone detection on the video.

        This is the primary onboarding path — look at the actual store
        video to determine where the queue, service, and entrance zones are.

        Pipeline: Moondream2 (scene Q&A) → Florence-2 (grounding) →
        SAM2 (segmentation) → YOLO (cross-validation).
        Falls back gracefully when models are unavailable.

        Returns:
            StoreConfig with vision-detected zones, plus a `detection_result`
            attribute with raw detection metadata, scene_info, and validation.
        """
        import av
        import numpy as np
        from retail_vision.config import EXAMPLE_ZONES as _EX_ZONES, EXAMPLE_COUNTER_REGION as _EX_COUNTER

        log.info("Extracting reference frame from %s...", video_path)
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

        if ref_frame is None:
            log.warning("Could not extract usable frame from %s", video_path)
            config = cls.from_dict({"store_id": store_id, **kwargs})
            config.detection_result = None
            return config

        # Zone detection is now handled externally (retail-zone-setup skill).
        # Use example zones as placeholder.
        log.warning("StoreConfig.from_video: auto zone detection removed. "
                     "Using example zones. Run the retail-zone-setup skill for real zones.")
        result = {
            "zones": dict(_EX_ZONES),
            "counter": list(_EX_COUNTER),
            "detected": [],
        }

        feed = FeedConfig(
            name=feed_name,
            video_path=video_path,
            zones=result["zones"],
            counter_region=result.get("counter"),
        )

        config = cls(
            store_id=store_id,
            feeds=[feed],
            **kwargs,
        )
        config.detection_result = result
