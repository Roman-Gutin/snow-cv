"""
retail_vision — SDK for retail surveillance video analytics on Snowflake SPCS.

Provides composable building blocks for:
  - Zone detection and configuration
  - Person detection + multi-object tracking (YOLO + ByteTrack)
  - Declarative event rule engine
  - Multi-camera feed management
  - Structured inference tracing / observability
  - Output to Snowflake stages and tables

Quick start (single camera):
    from retail_vision import RetailPipeline, StoreConfig

    config = StoreConfig.from_yaml("store_config.yaml")
    pipeline = RetailPipeline(config)
    pipeline.run("store_001_12h.mp4")

Quick start (from SPCS container env vars):
    from retail_vision import RetailPipeline, StoreConfig

    config = StoreConfig.from_env()
    pipeline = RetailPipeline(config)
    pipeline.run(os.environ["VIDEO_PATH"])
"""

from retail_vision.config import StoreConfig, FeedConfig
from retail_vision.zones import ZoneMap, PARKING_ZONE_PRIORITY, PARKING_ROLE_MAP
from retail_vision.scene import understand_scene, segment_fixture, validate_zones_with_yolo
from retail_vision.detector import PersonDetector
from retail_vision.tracker import TrackState
from retail_vision.events import EventEngine, EventRule
from retail_vision.strategies import get_strategy, register_strategy, UseCaseStrategy
from retail_vision.feeds import MultiFeedManager
from retail_vision.trace import InferenceTracer
from retail_vision.output import OutputWriter, SnowflakeOutput, CsvOutput
from retail_vision.pipeline import RetailPipeline

__version__ = "0.1.0"

__all__ = [
    "StoreConfig", "FeedConfig",
    "ZoneMap", "PARKING_ZONE_PRIORITY", "PARKING_ROLE_MAP",
    "understand_scene", "segment_fixture", "validate_zones_with_yolo",
    "PersonDetector",
    "TrackState",
    "EventEngine", "EventRule",
    "get_strategy", "register_strategy", "UseCaseStrategy",
    "MultiFeedManager",
    "InferenceTracer",
    "OutputWriter", "SnowflakeOutput", "CsvOutput",
    "RetailPipeline",
]
