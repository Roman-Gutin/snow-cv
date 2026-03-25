"""
Output writers — write detection, event, and trace data to destinations.

Supports:
  - CsvOutput: local CSV files (for testing / local iteration)
  - SnowflakeOutput: PUT to stage + COPY INTO tables
  - OutputWriter: abstract base
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class OutputWriter(ABC):
    """Base class for pipeline output."""

    @abstractmethod
    def write_detections(self, rows: list[tuple]):
        """Write detection rows."""

    @abstractmethod
    def write_events(self, rows: list[tuple]):
        """Write event rows."""

    @abstractmethod
    def write_traces(self, rows: list[tuple]):
        """Write inference trace rows."""

    @abstractmethod
    def write_metadata(self, row: tuple):
        """Write video metadata row."""

    @abstractmethod
    def flush(self):
        """Flush any buffered data."""


class CsvOutput(OutputWriter):
    """Write results to local CSV files. Good for local iteration with the studio."""

    def __init__(self, output_dir: str = ""):
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="rv_results_")
        os.makedirs(self.output_dir, exist_ok=True)
        self._det_path = os.path.join(self.output_dir, "detections.csv")
        self._evt_path = os.path.join(self.output_dir, "events.csv")
        self._trace_path = os.path.join(self.output_dir, "traces.csv")
        self._meta_path = os.path.join(self.output_dir, "metadata.csv")
        log.info("CsvOutput writing to %s", self.output_dir)

    def write_detections(self, rows: list[tuple]):
        self._append_csv(self._det_path, rows)

    def write_events(self, rows: list[tuple]):
        self._append_csv(self._evt_path, rows)

    def write_traces(self, rows: list[tuple]):
        self._append_csv(self._trace_path, rows)

    def write_metadata(self, row: tuple):
        self._append_csv(self._meta_path, [row])

    def flush(self):
        pass  # CSV writes are immediate

    @staticmethod
    def _append_csv(path: str, rows: list[tuple]):
        if not rows:
            return
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            for row in rows:
                w.writerow(row)


class SnowflakeOutput(OutputWriter):
    """Write results to Snowflake via stage PUT + COPY INTO.

    Buffers rows locally, flushes to stage on flush(), then COPY INTO tables.
    """

    def __init__(
        self,
        conn,
        results_stage: str = "@SNOW_CV_DB.SNOW_CV_SCHEMA.RAW_VIDEO/results",
        database: str = "SNOW_CV_DB",
        schema: str = "SNOW_CV_SCHEMA",
        video_id: str = "",
        feed_name: str = "",
        segment_id: str = "",
    ):
        self.conn = conn
        self.results_stage = results_stage
        self.database = database
        self.schema = schema
        self.video_id = video_id
        self.feed_name = feed_name
        self.segment_id = segment_id

        self._det_table = f"{database}.{schema}.PERSON_DETECTIONS"
        self._evt_table = f"{database}.{schema}.PERSON_EVENTS"
        self._meta_table = f"{database}.{schema}.VIDEO_METADATA"
        self._trace_table = f"{database}.{schema}.INFERENCE_TRACES"

        self._tmpdir = tempfile.mkdtemp(prefix="rv_stage_")
        self._det_rows: list[tuple] = []
        self._evt_rows: list[tuple] = []
        self._trace_rows: list[tuple] = []
        self._meta_row: tuple | None = None

    def write_detections(self, rows: list[tuple]):
        self._det_rows.extend(rows)

    def write_events(self, rows: list[tuple]):
        self._evt_rows.extend(rows)

    def write_traces(self, rows: list[tuple]):
        self._trace_rows.extend(rows)

    def write_metadata(self, row: tuple):
        self._meta_row = row

    def flush(self):
        """Write buffered data to stage, then COPY INTO tables."""
        stage_suffix = self.video_id
        if self.feed_name:
            stage_suffix = f"{stage_suffix}/{self.feed_name}"
        if self.segment_id:
            stage_suffix = f"{stage_suffix}/{self.segment_id}"
        stage_prefix = f"{self.results_stage}/{stage_suffix}"

        cur = self.conn.cursor()

        # Detections
        if self._det_rows:
            det_path = os.path.join(self._tmpdir, "detections.csv")
            self._write_csv(det_path, self._det_rows)
            cur.execute(f"PUT 'file://{det_path}' '{stage_prefix}/' AUTO_COMPRESS=TRUE OVERWRITE=TRUE")
            cur.fetchall()
            cur.execute(
                f"COPY INTO {self._det_table} "
                f"FROM '{stage_prefix}/' "
                f"FILE_FORMAT=(TYPE=CSV FIELD_OPTIONALLY_ENCLOSED_BY='\"' SKIP_HEADER=0) "
                f"PURGE=TRUE ON_ERROR='CONTINUE'"
            )
            cur.fetchall()
            log.info("Loaded %d detections into %s", len(self._det_rows), self._det_table)
            self._det_rows.clear()

        # Events
        if self._evt_rows:
            evt_path = os.path.join(self._tmpdir, "events.csv")
            self._write_csv(evt_path, self._evt_rows)
            cur.execute(f"PUT 'file://{evt_path}' '{stage_prefix}/' AUTO_COMPRESS=TRUE OVERWRITE=TRUE")
            cur.fetchall()
            cur.execute(
                f"COPY INTO {self._evt_table} "
                f"FROM '{stage_prefix}/' "
                f"FILE_FORMAT=(TYPE=CSV FIELD_OPTIONALLY_ENCLOSED_BY='\"' SKIP_HEADER=0) "
                f"PURGE=TRUE ON_ERROR='CONTINUE'"
            )
            cur.fetchall()
            log.info("Loaded %d events into %s", len(self._evt_rows), self._evt_table)
            self._evt_rows.clear()

        # Traces
        if self._trace_rows:
            trace_path = os.path.join(self._tmpdir, "traces.csv")
            self._write_csv(trace_path, self._trace_rows)
            cur.execute(f"PUT 'file://{trace_path}' '{stage_prefix}/' AUTO_COMPRESS=TRUE OVERWRITE=TRUE")
            cur.fetchall()
            cur.execute(
                f"COPY INTO {self._trace_table} "
                f"FROM '{stage_prefix}/' "
                f"FILE_FORMAT=(TYPE=CSV FIELD_OPTIONALLY_ENCLOSED_BY='\"' SKIP_HEADER=0) "
                f"PURGE=TRUE ON_ERROR='CONTINUE'"
            )
            cur.fetchall()
            log.info("Loaded %d traces into %s", len(self._trace_rows), self._trace_table)
            self._trace_rows.clear()

        # Metadata
        if self._meta_row:
            vals = self._meta_row
            cur.execute(
                f"INSERT INTO {self._meta_table} "
                f"(VIDEO_ID, VIDEO_FILENAME, DURATION_SEC, FPS, ZONE_CONFIG, FEED_NAME) "
                f"SELECT %s, %s, %s, %s, PARSE_JSON(%s), %s",
                vals,
            )
            log.info("Wrote metadata for video %s", vals[0])
            self._meta_row = None

        cur.close()

    @staticmethod
    def _write_csv(path: str, rows: list[tuple]):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            for row in rows:
                w.writerow(row)
