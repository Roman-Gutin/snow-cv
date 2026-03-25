"""
snow_cv — Computer vision SDK for Snowflake SPCS.

Provides composable building blocks for:
  - Zone detection and configuration
  - Person detection + multi-object tracking (YOLO + ByteTrack)
  - Declarative event rule engine
  - Pluggable use-case strategies (generic, retail, parking, custom)
  - Multi-camera feed management
  - Structured inference tracing / observability
  - Output to Snowflake stages and tables

Quick start (single camera):
    from snow_cv import Pipeline, StoreConfig

    config = StoreConfig.from_yaml("store_config.yaml")
    pipeline = Pipeline(config)
    pipeline.run("video.mp4")

Quick start (from SPCS container env vars):
    from snow_cv import Pipeline, StoreConfig

    config = StoreConfig.from_env()
    pipeline = Pipeline(config)
    pipeline.run(os.environ["VIDEO_PATH"])
"""

from snow_cv.config import StoreConfig, FeedConfig
from snow_cv.zones import ZoneMap, PARKING_ZONE_PRIORITY, PARKING_ROLE_MAP
from snow_cv.scene import understand_scene, segment_fixture, validate_zones_with_yolo
from snow_cv.detector import PersonDetector
from snow_cv.tracker import TrackState
from snow_cv.events import EventEngine, EventRule
from snow_cv.strategies import get_strategy, register_strategy, UseCaseStrategy, GenericStrategy
from snow_cv.feeds import MultiFeedManager
from snow_cv.trace import InferenceTracer
from snow_cv.output import OutputWriter, SnowflakeOutput, CsvOutput
from snow_cv.pipeline import Pipeline

# Auto-register bundled use-case strategies (retail, parking).
# Each use_case sub-package calls register_strategy() on import.
import use_cases  # noqa: F401

# Backward compatibility alias
RetailPipeline = Pipeline

__version__ = "0.1.0"

__all__ = [
    "StoreConfig", "FeedConfig",
    "ZoneMap", "PARKING_ZONE_PRIORITY", "PARKING_ROLE_MAP",
    "understand_scene", "segment_fixture", "validate_zones_with_yolo",
    "PersonDetector",
    "TrackState",
    "EventEngine", "EventRule",
    "get_strategy", "register_strategy", "UseCaseStrategy", "GenericStrategy",
    "MultiFeedManager",
    "InferenceTracer",
    "OutputWriter", "SnowflakeOutput", "CsvOutput",
    "Pipeline",
    "RetailPipeline",  # backward compat alias
]
