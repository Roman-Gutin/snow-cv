-- ============================================================
-- Retail Surveillance Video Analytics - Infrastructure Setup
-- Set YOUR_DB, YOUR_SCHEMA, YOUR_WH below before running.
-- ============================================================

USE ROLE ACCOUNTADMIN;
-- USE WAREHOUSE YOUR_WH;
-- USE DATABASE YOUR_DB;
-- CREATE SCHEMA IF NOT EXISTS YOUR_SCHEMA COMMENT = 'Retail surveillance video analytics';
-- USE SCHEMA YOUR_SCHEMA;

-- Stages (server-side encrypted, directory tables enabled for AI_COMPLETE multimodal)
CREATE OR REPLACE STAGE RAW_VIDEO
    DIRECTORY = (ENABLE = TRUE)
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
    COMMENT = 'Raw surveillance video files';

CREATE OR REPLACE STAGE EXTRACTED_FRAMES
    DIRECTORY = (ENABLE = TRUE)
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
    COMMENT = 'Extracted video frames as JPEG images for multimodal LLM analysis';

-- Per-person per-frame detections (flat relational — no JSON flattening needed)
CREATE TABLE IF NOT EXISTS PERSON_DETECTIONS (
    VIDEO_ID        VARCHAR   NOT NULL,
    FRAME_IDX       INT       NOT NULL,
    TIMESTAMP_SEC   FLOAT     NOT NULL,
    TRACK_ID        INT       NOT NULL,
    ROLE            VARCHAR   NOT NULL,   -- employee | customer_being_served | in_queue | entering | exiting | at_entrance | other
    CONFIDENCE      FLOAT     NOT NULL,
    BBOX_X_MIN      FLOAT     NOT NULL,
    BBOX_Y_MIN      FLOAT     NOT NULL,
    BBOX_X_MAX      FLOAT     NOT NULL,
    BBOX_Y_MAX      FLOAT     NOT NULL,
    CENTROID_X      FLOAT     NOT NULL,
    CENTROID_Y      FLOAT     NOT NULL,
    QUEUE_POSITION  INT,                  -- NULL if not in queue
    MASK_POLYGON    VARIANT,              -- array of [x,y] normalized seg-mask points
    FEED_NAME       VARCHAR   DEFAULT 'main',  -- camera feed name for multi-camera stores
    CREATED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (VIDEO_ID, FRAME_IDX, TRACK_ID, FEED_NAME)
) COMMENT = 'One row per person per frame — YOLO+ByteTrack detections with zone-based role classification';

-- Video metadata (one row per analyzed video — written by SPCS container)
-- Only stores facts the container knows from the file itself.
-- All analytics are derived via the VIDEO_ANALYTICS view below.
CREATE TABLE IF NOT EXISTS VIDEO_METADATA (
    VIDEO_ID        VARCHAR   NOT NULL PRIMARY KEY,
    VIDEO_FILENAME  VARCHAR,
    DURATION_SEC    FLOAT,
    FPS             FLOAT,
    ZONE_CONFIG     VARIANT,   -- zone polygons used for this run
    FEED_NAME       VARCHAR   DEFAULT 'main',  -- camera feed name
    CREATED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
) COMMENT = 'Per-video file metadata written by the SPCS container job';

-- Analytics view — derives all KPIs from PERSON_DETECTIONS + PERSON_EVENTS + VIDEO_METADATA.
-- No container re-run needed to refresh analytics; just query this view.
CREATE OR REPLACE VIEW VIDEO_ANALYTICS AS
WITH people_count AS (
    SELECT VIDEO_ID, COUNT(DISTINCT TRACK_ID) AS total_people_detected
    FROM PERSON_DETECTIONS
    GROUP BY VIDEO_ID
),
frame_stats AS (
    SELECT
        VIDEO_ID,
        COUNT(DISTINCT FRAME_IDX)  AS total_frames_analyzed,
        ROUND(AVG(queue_len), 2)   AS avg_queue_length,
        MAX(queue_len)             AS max_queue_length
    FROM (
        SELECT VIDEO_ID, FRAME_IDX,
               COUNT(CASE WHEN ROLE = 'in_queue' THEN 1 END) AS queue_len
        FROM PERSON_DETECTIONS
        GROUP BY VIDEO_ID, FRAME_IDX
    )
    GROUP BY VIDEO_ID
),
employee_stats AS (
    SELECT
        VIDEO_ID,
        ROUND(100.0 * COUNT(DISTINCT CASE WHEN has_emp THEN FRAME_IDX END)
              / NULLIF(COUNT(DISTINCT FRAME_IDX), 0), 1) AS employee_present_pct,
        ROUND(100.0 * COUNT(DISTINCT CASE WHEN has_emp AND has_svc THEN FRAME_IDX END)
              / NULLIF(COUNT(DISTINCT FRAME_IDX), 0), 1) AS service_active_pct
    FROM (
        SELECT VIDEO_ID, FRAME_IDX,
               MAX(CASE WHEN ROLE = 'employee' THEN TRUE END)              AS has_emp,
               MAX(CASE WHEN ROLE = 'customer_being_served' THEN TRUE END) AS has_svc
        FROM PERSON_DETECTIONS
        GROUP BY VIDEO_ID, FRAME_IDX
    )
    GROUP BY VIDEO_ID
),
traffic AS (
    SELECT
        VIDEO_ID,
        COUNT(CASE WHEN EVENT_TYPE = 'entered_store'            THEN 1 END) AS total_entered,
        COUNT(CASE WHEN EVENT_TYPE = 'exited_store'             THEN 1 END) AS total_exited,
        COUNT(CASE WHEN EVENT_TYPE IN ('abandoned','unserviced') THEN 1 END) AS total_left_unserviced,
        COUNT(*) AS total_events
    FROM PERSON_EVENTS
    GROUP BY VIDEO_ID
),
wait_times AS (
    SELECT q.VIDEO_ID,
           ROUND(AVG(s.ts - q.ts), 2) AS avg_wait_time_sec
    FROM (
        SELECT VIDEO_ID, TRACK_ID, MIN(TIMESTAMP_SEC) AS ts
        FROM PERSON_EVENTS WHERE EVENT_TYPE = 'queue_entered'
        GROUP BY VIDEO_ID, TRACK_ID
    ) q
    JOIN (
        SELECT VIDEO_ID, TRACK_ID, MIN(TIMESTAMP_SEC) AS ts
        FROM PERSON_EVENTS WHERE EVENT_TYPE = 'service_started'
        GROUP BY VIDEO_ID, TRACK_ID
    ) s ON q.VIDEO_ID = s.VIDEO_ID AND q.TRACK_ID = s.TRACK_ID
    GROUP BY q.VIDEO_ID
),
service_times AS (
    SELECT ss.VIDEO_ID,
           ROUND(AVG(se.ts - ss.ts), 2) AS avg_service_time_sec
    FROM (
        SELECT VIDEO_ID, TRACK_ID, MIN(TIMESTAMP_SEC) AS ts
        FROM PERSON_EVENTS WHERE EVENT_TYPE = 'service_started'
        GROUP BY VIDEO_ID, TRACK_ID
    ) ss
    JOIN (
        SELECT VIDEO_ID, TRACK_ID, MAX(TIMESTAMP_SEC) AS ts
        FROM PERSON_EVENTS WHERE EVENT_TYPE = 'service_ended'
        GROUP BY VIDEO_ID, TRACK_ID
    ) se ON ss.VIDEO_ID = se.VIDEO_ID AND ss.TRACK_ID = se.TRACK_ID
    GROUP BY ss.VIDEO_ID
),
unstaffed AS (
    SELECT VIDEO_ID,
           ROUND(SUM(DETAILS:"duration_sec"::FLOAT), 2) AS total_unstaffed_sec,
           ROUND(MAX(DETAILS:"duration_sec"::FLOAT), 2) AS max_unstaffed_sec
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'counter_unstaffed_end'
    GROUP BY VIDEO_ID
)
SELECT
    m.VIDEO_ID,
    m.VIDEO_FILENAME,
    m.DURATION_SEC,
    m.FPS,
    f.total_frames_analyzed            AS TOTAL_FRAMES_ANALYZED,
    p.total_people_detected            AS TOTAL_PEOPLE_DETECTED,
    COALESCE(t.total_entered, 0)       AS TOTAL_ENTERED,
    COALESCE(t.total_exited, 0)        AS TOTAL_EXITED,
    COALESCE(t.total_left_unserviced, 0) AS TOTAL_LEFT_UNSERVICED,
    f.avg_queue_length                 AS AVG_QUEUE_LENGTH,
    f.max_queue_length                 AS MAX_QUEUE_LENGTH,
    e.employee_present_pct             AS EMPLOYEE_PRESENT_PCT,
    e.service_active_pct               AS SERVICE_ACTIVE_PCT,
    w.avg_wait_time_sec                AS AVG_WAIT_TIME_SEC,
    st.avg_service_time_sec            AS AVG_SERVICE_TIME_SEC,
    COALESCE(u.total_unstaffed_sec, 0) AS TOTAL_UNSTAFFED_SEC,
    COALESCE(u.max_unstaffed_sec, 0)   AS MAX_UNSTAFFED_SEC,
    COALESCE(t.total_events, 0)        AS TOTAL_EVENTS,
    m.ZONE_CONFIG,
    m.CREATED_AT
FROM VIDEO_METADATA m
LEFT JOIN people_count p   ON m.VIDEO_ID = p.VIDEO_ID
LEFT JOIN frame_stats f    ON m.VIDEO_ID = f.VIDEO_ID
LEFT JOIN employee_stats e ON m.VIDEO_ID = e.VIDEO_ID
LEFT JOIN traffic t        ON m.VIDEO_ID = t.VIDEO_ID
LEFT JOIN wait_times w     ON m.VIDEO_ID = w.VIDEO_ID
LEFT JOIN service_times st ON m.VIDEO_ID = st.VIDEO_ID
LEFT JOIN unstaffed u      ON m.VIDEO_ID = u.VIDEO_ID;

-- Event stream: state transitions per person + system-level events (unstaffed counter)
CREATE TABLE IF NOT EXISTS PERSON_EVENTS (
    VIDEO_ID        VARCHAR   NOT NULL,
    TRACK_ID        INT       NOT NULL,   -- 0 for system-level events (counter_unstaffed_*)
    EVENT_TYPE      VARCHAR   NOT NULL,   -- entered_store | exited_store | pre_existing
                                          -- | queue_entered | queue_exited
                                          -- | service_started | service_ended
                                          -- | abandoned (full lifecycle observed)
                                          -- | unserviced (entry not observed, can't confirm abandonment)
                                          -- | employee_arrived | employee_left
                                          -- | counter_unstaffed_start | counter_unstaffed_end
    TIMESTAMP_SEC   FLOAT     NOT NULL,
    FRAME_IDX       INT       NOT NULL,
    DETAILS         VARIANT,              -- contextual JSON (from_role, to_role, duration_sec, etc.)
    FEED_NAME       VARCHAR   DEFAULT 'main',  -- camera feed name for multi-camera stores
    JOURNEY_ID      VARCHAR,              -- shared ID for cross-feed person tracking
    CREATED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (VIDEO_ID, FRAME_IDX, TRACK_ID, EVENT_TYPE, FEED_NAME)
) COMMENT = 'State-transition events: role changes, employee presence, unstaffed periods, abandonment';

-- Inference traces: per-frame observability for debugging bad inferences
CREATE TABLE IF NOT EXISTS INFERENCE_TRACES (
    VIDEO_ID        VARCHAR   NOT NULL,
    STORE_ID        VARCHAR,
    FEED_NAME       VARCHAR   DEFAULT 'main',
    CONTAINER_ID    VARCHAR,              -- SPCS job name for container-level correlation
    FRAME_IDX       INT       NOT NULL,
    TIMESTAMP_SEC   FLOAT     NOT NULL,
    RAW_DETECTIONS  INT,                  -- count before dedup
    AFTER_DEDUP     INT,                  -- count after centroid dedup
    AFTER_MERGE     INT,                  -- count after cross-frame ID merge
    TRACKS_ACTIVE   INT,
    TRACKS_NEW      INT,
    TRACKS_LOST     INT,
    CONFIDENCE_MIN  FLOAT,
    CONFIDENCE_MAX  FLOAT,
    CONFIDENCE_MEAN FLOAT,
    EVENTS_EMITTED  VARIANT,              -- array of event type strings
    WARNINGS        VARIANT,              -- array of anomaly strings (track_count_spike, low_confidence, etc.)
    PROCESSING_MS   FLOAT,
    CREATED_AT      TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (VIDEO_ID, FRAME_IDX, FEED_NAME)
) COMMENT = 'Per-frame inference trace data for observability — query WHERE ARRAY_SIZE(WARNINGS) > 0 to find bad frames';

-- ============================================================
-- Cross-feed journey correlation (multi-camera stores)
-- Matches exits from one camera with entrances on another
-- using temporal proximity (within 10 seconds).
-- ============================================================
CREATE OR REPLACE VIEW CROSS_FEED_JOURNEYS AS
WITH exits AS (
    SELECT VIDEO_ID, FEED_NAME, TRACK_ID, TIMESTAMP_SEC,
           DETAILS:"last_role"::VARCHAR AS last_role,
           JOURNEY_ID
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'exited_store'
),
entrances AS (
    SELECT VIDEO_ID, FEED_NAME, TRACK_ID, TIMESTAMP_SEC,
           DETAILS:"role"::VARCHAR AS entry_role,
           JOURNEY_ID
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'entered_store'
)
SELECT
    e.VIDEO_ID,
    e.FEED_NAME AS exit_feed,
    e.TRACK_ID AS exit_track,
    e.TIMESTAMP_SEC AS exit_time,
    n.FEED_NAME AS entry_feed,
    n.TRACK_ID AS entry_track,
    n.TIMESTAMP_SEC AS entry_time,
    ROUND(n.TIMESTAMP_SEC - e.TIMESTAMP_SEC, 2) AS handoff_delay_sec,
    COALESCE(e.JOURNEY_ID, n.JOURNEY_ID) AS journey_id
FROM exits e
JOIN entrances n
    ON e.VIDEO_ID = n.VIDEO_ID
    AND e.FEED_NAME != n.FEED_NAME
    AND n.TIMESTAMP_SEC BETWEEN e.TIMESTAMP_SEC AND e.TIMESTAMP_SEC + 10
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY n.VIDEO_ID, n.FEED_NAME, n.TRACK_ID
    ORDER BY ABS(n.TIMESTAMP_SEC - e.TIMESTAMP_SEC)
) = 1
ORDER BY e.TIMESTAMP_SEC;

-- ============================================================
-- Journey-aware wait times (multi-camera)
-- Uses JOURNEY_ID to track a person across cameras.
-- Falls back to single-feed TRACK_ID when no journey exists.
-- ============================================================
CREATE OR REPLACE VIEW JOURNEY_WAIT_TIMES AS
WITH queue_events AS (
    SELECT
        VIDEO_ID,
        COALESCE(NULLIF(JOURNEY_ID, ''), FEED_NAME || ':' || TRACK_ID) AS person_key,
        TRACK_ID, FEED_NAME, JOURNEY_ID,
        MIN(TIMESTAMP_SEC) AS queue_entered_at
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'queue_entered'
    GROUP BY VIDEO_ID, person_key, TRACK_ID, FEED_NAME, JOURNEY_ID
),
service_events AS (
    SELECT
        VIDEO_ID,
        COALESCE(NULLIF(JOURNEY_ID, ''), FEED_NAME || ':' || TRACK_ID) AS person_key,
        TRACK_ID, FEED_NAME, JOURNEY_ID,
        MIN(TIMESTAMP_SEC) AS service_started_at
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'service_started'
    GROUP BY VIDEO_ID, person_key, TRACK_ID, FEED_NAME, JOURNEY_ID
)
SELECT
    q.VIDEO_ID,
    q.person_key,
    q.FEED_NAME AS queue_feed,
    q.TRACK_ID AS queue_track,
    q.queue_entered_at,
    s.FEED_NAME AS service_feed,
    s.TRACK_ID AS service_track,
    s.service_started_at,
    ROUND(s.service_started_at - q.queue_entered_at, 2) AS wait_time_sec,
    q.JOURNEY_ID
FROM queue_events q
JOIN service_events s
    ON q.VIDEO_ID = s.VIDEO_ID
    AND q.person_key = s.person_key
WHERE s.service_started_at >= q.queue_entered_at
ORDER BY wait_time_sec DESC;

-- Upload video (run from local machine):
-- PUT 'file:///path/to/synthetic_retail_queue.mp4' @RAW_VIDEO AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
