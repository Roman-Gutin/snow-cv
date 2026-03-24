"""
YOLO-seg + ByteTrack Retail Video Analyzer (SPCS Container Job)

Thin entrypoint for SPCS containers. All detection, tracking, event, and
output logic lives in the retail_vision SDK package.

This file handles only:
  - SPCS Snowflake connection (OAuth token from /snowflake/session/token)
  - Video listing and download from @RAW_VIDEO stage
  - Building SDK config from SPCS environment variables or config file
  - Delegating to RetailPipeline.run() for each feed

Supports two config modes:
  1. Multi-feed config file (recommended):
       STORE_CONFIG_PATH — path to a JSON or YAML config file defining
       store_id, feeds[], feed_links[], and infrastructure settings.
       Each feed is processed sequentially against its video_path.

  2. Legacy single-feed env vars:
       STORE_ID, VIDEO_PATH, FEED_NAME, zone env vars, etc.
       Falls back to this when STORE_CONFIG_PATH is not set.

Environment variables (injected by SPCS):
  SNOWFLAKE_ACCOUNT, SNOWFLAKE_HOST — set automatically.
  STORE_CONFIG_PATH — path to multi-feed JSON/YAML config (preferred).
  VIDEO_PATH — filename filter on stage (single-feed mode).
  STORE_ID, SEGMENT_ID, START_SEC, END_SEC — segment-parallel processing.
  FEED_NAME — camera feed name (default: main, single-feed mode).
  EMPLOYEE_ZONE, SERVICE_ZONE, QUEUE_ZONE, ENTRANCE_ZONE, COUNTER_REGION —
    JSON polygon overrides (single-feed mode).
  SNOWFLAKE_WAREHOUSE — warehouse to use (default: SNOW_CV_WH).
  EVENT_RULES_PATH — optional path to custom event rules YAML.
"""

import os
import time
import logging
import tempfile

import snowflake.connector

from retail_vision import (
    StoreConfig, RetailPipeline, SnowflakeOutput, InferenceTracer,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "SNOW_CV_DB")
SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "SNOW_CV_SCHEMA")
RAW_VIDEO_STAGE = f"@{DATABASE}.{SCHEMA}.RAW_VIDEO"
RESULTS_STAGE = f"@{DATABASE}.{SCHEMA}.RAW_VIDEO/results"
WAREHOUSE = os.environ.get("SNOWFLAKE_WAREHOUSE", "SNOW_CV_WH")


def get_snowflake_connection(max_retries=5, base_delay=2.0, max_delay=30.0):
    """Connect to Snowflake using SPCS OAuth token."""
    token_path = "/snowflake/session/token"
    with open(token_path) as f:
        token = f.read().strip()

    for attempt in range(1, max_retries + 1):
        try:
            conn = snowflake.connector.connect(
                host=os.environ.get("SNOWFLAKE_HOST", ""),
                account=os.environ.get("SNOWFLAKE_ACCOUNT", ""),
                token=token,
                authenticator="oauth",
                warehouse=WAREHOUSE,
                database=DATABASE,
                schema=SCHEMA,
            )
            log.info("Connected to Snowflake (attempt %d)", attempt)
            return conn
        except Exception as e:
            if attempt == max_retries:
                log.error("Failed to connect after %d attempts: %s", max_retries, e)
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter = delay * 0.3 * (2 * __import__('random').random() - 1)
            sleep_time = delay + jitter
            log.warning("Connection attempt %d failed: %s. Retrying in %.1fs...",
                        attempt, e, sleep_time)
            time.sleep(sleep_time)


def list_videos(conn):
    """List video files on RAW_VIDEO stage."""
    cur = conn.cursor()
    cur.execute(f"LIST {RAW_VIDEO_STAGE}")
    rows = cur.fetchall()
    cur.close()
    videos = [row[0] for row in rows if row[0].lower().endswith((".mp4", ".mov", ".avi"))]
    log.info("Found %d video(s) on stage", len(videos))
    return videos


def download_video(conn, stage_path):
    """Download a video from stage to a local temp file."""
    filename = stage_path.split("/")[-1]
    tmpdir = tempfile.mkdtemp()
    cur = conn.cursor()
    cur.execute(f"GET {RAW_VIDEO_STAGE}/{filename} 'file://{tmpdir}'")
    cur.fetchall()
    cur.close()
    local_path = os.path.join(tmpdir, filename)
    log.info("Downloaded %s (%d bytes)", filename, os.path.getsize(local_path))
    return local_path


def load_config() -> StoreConfig:
    """Load store config from file (multi-feed) or env vars (single-feed)."""
    config_path = os.environ.get("STORE_CONFIG_PATH", "").strip()
    if config_path:
        log.info("Loading multi-feed config from %s", config_path)
        if config_path.endswith((".yaml", ".yml")):
            config = StoreConfig.from_yaml(config_path)
        else:
            import json as _json
            with open(config_path) as f:
                config = StoreConfig.from_dict(_json.load(f))
        log.info("Store: %s, Feeds: %s, Links: %d",
                 config.store_id,
                 [f.name for f in config.feeds],
                 len(config.feed_links))
        return config

    log.info("No STORE_CONFIG_PATH set, using env var config (single-feed)")
    config = StoreConfig.from_env()
    log.info("Store: %s, Feed: %s, Zones: %s",
             config.store_id, config.feeds[0].name,
             list(config.feeds[0].zones.keys()))
    return config


def process_feed(conn, config, feed, segment_id, start_sec, end_sec):
    """Download and process all videos for a single feed."""
    feed_name = feed.name
    video_filter = feed.video_path or os.environ.get("VIDEO_PATH", "").strip()

    videos = list_videos(conn)
    if video_filter:
        videos = [v for v in videos if v.split("/")[-1] == video_filter
                  or v.endswith("/" + video_filter)]
        log.info("Feed '%s' video filter: '%s' -> %d match(es)",
                 feed_name, video_filter, len(videos))

    if not videos:
        log.warning("No videos found for feed '%s'", feed_name)
        return 0

    processed = 0
    for stage_path in videos:
        filename = stage_path.split("/")[-1]
        log.info("Processing %s (store=%s, feed=%s, segment=%s, range=%.0f-%.0fs)",
                 filename, config.store_id, feed_name, segment_id or "full",
                 start_sec, end_sec)

        local_path = download_video(conn, stage_path)

        output = SnowflakeOutput(
            conn=conn,
            results_stage=RESULTS_STAGE,
            database=DATABASE,
            schema=SCHEMA,
            video_id="",  # pipeline generates this
            feed_name=feed_name,
            segment_id=segment_id,
        )
        tracer = InferenceTracer(
            store_id=config.store_id,
            feed_name=feed_name,
            container_id=os.environ.get("JOB_NAME", segment_id or "local"),
        )

        pipeline = RetailPipeline(
            config=config,
            output=output,
            tracer=tracer,
        )

        summary = pipeline.run(
            video_path=local_path,
            feed_name=feed_name,
            start_sec=start_sec,
            end_sec=end_sec,
            segment_id=segment_id,
        )

        os.unlink(local_path)
        log.info("Finished %s: %d detections, %d events in %.1fs",
                 filename, summary["total_detections"],
                 summary["total_events"], summary["elapsed_sec"])

        if summary.get("trace_summary", {}).get("frames_flagged", 0) > 0:
            log.warning("Trace warnings: %s", summary["trace_summary"]["warning_counts"])
        processed += 1

    return processed


def main():
    log.info("=== YOLO-seg + ByteTrack Retail Analyzer Starting ===")

    config = load_config()
    conn = get_snowflake_connection()

    segment_id = os.environ.get("SEGMENT_ID", "").strip() or ""
    start_sec = float(os.environ.get("START_SEC", "0"))
    end_sec = float(os.environ.get("END_SEC", "0"))

    if segment_id:
        log.info("SEGMENT_ID: %s, START_SEC: %s, END_SEC: %s", segment_id, start_sec, end_sec)

    total_processed = 0
    for feed in config.feeds:
        log.info("--- Processing feed: %s ---", feed.name)
        total_processed += process_feed(
            conn, config, feed, segment_id, start_sec, end_sec)

    log.info("=== Done: %d feed(s), %d video(s) processed ===",
             len(config.feeds), total_processed)
    conn.close()


if __name__ == "__main__":
    main()
