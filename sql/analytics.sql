-- ============================================================
-- Retail Queue Intelligence - Telemetry Queries
-- Reads from PERSON_DETECTIONS, PERSON_EVENTS, VIDEO_ANALYTICS (view)
-- PERSON_DETECTIONS and PERSON_EVENTS are populated by the SPCS container job.
-- VIDEO_ANALYTICS is a SQL view that derives all KPIs from those two tables.
-- ============================================================

USE DATABASE <YOUR_DB>;
USE SCHEMA <YOUR_SCHEMA>;
USE WAREHOUSE <YOUR_WH>;

-- ============================================================
-- 1. VIDEO SUMMARY: High-level KPIs per video
-- ============================================================
SELECT
    VIDEO_ID,
    VIDEO_FILENAME,
    TOTAL_FRAMES_ANALYZED,
    TOTAL_PEOPLE_DETECTED,
    TOTAL_ENTERED,
    TOTAL_EXITED,
    TOTAL_LEFT_UNSERVICED,
    ROUND(AVG_QUEUE_LENGTH, 2)  AS avg_queue_length,
    MAX_QUEUE_LENGTH,
    EMPLOYEE_PRESENT_PCT,
    SERVICE_ACTIVE_PCT,
    AVG_WAIT_TIME_SEC,
    AVG_SERVICE_TIME_SEC,
    ROUND(TOTAL_UNSTAFFED_SEC, 1) AS total_unstaffed_sec,
    TOTAL_EVENTS,
    CREATED_AT
FROM VIDEO_ANALYTICS
ORDER BY CREATED_AT DESC;

-- ============================================================
-- 2. QUEUE LENGTH OVER TIME: Per-frame queue depth
-- ============================================================
SELECT
    TIMESTAMP_SEC,
    COUNT(CASE WHEN ROLE = 'in_queue' THEN 1 END) AS queue_length,
    COUNT(CASE WHEN ROLE = 'employee' THEN 1 END) AS employees,
    COUNT(CASE WHEN ROLE = 'customer_being_served' THEN 1 END) AS being_served,
    COUNT(*) AS total_people
FROM PERSON_DETECTIONS
WHERE VIDEO_ID = :video_id
GROUP BY TIMESTAMP_SEC
ORDER BY TIMESTAMP_SEC;

-- ============================================================
-- 3. PERSON TIMELINE: Track each person across frames
-- ============================================================
SELECT
    TRACK_ID,
    MIN(TIMESTAMP_SEC) AS first_seen,
    MAX(TIMESTAMP_SEC) AS last_seen,
    ROUND(MAX(TIMESTAMP_SEC) - MIN(TIMESTAMP_SEC), 1) AS duration_sec,
    ARRAY_AGG(DISTINCT ROLE) WITHIN GROUP (ORDER BY ROLE) AS roles_observed,
    COUNT(DISTINCT FRAME_IDX) AS frames_visible,
    MAX(QUEUE_POSITION) AS max_queue_position
FROM PERSON_DETECTIONS
WHERE VIDEO_ID = :video_id
GROUP BY TRACK_ID
ORDER BY first_seen;

-- ============================================================
-- 4. EVENT STREAM: All state transitions
-- ============================================================
SELECT
    TRACK_ID,
    EVENT_TYPE,
    TIMESTAMP_SEC,
    FRAME_IDX,
    DETAILS,
    CREATED_AT
FROM PERSON_EVENTS
WHERE VIDEO_ID = :video_id
ORDER BY TIMESTAMP_SEC, TRACK_ID;

-- ============================================================
-- 5. WAIT TIME ANALYSIS: Queue-to-service per person
-- ============================================================
WITH queue_enter AS (
    SELECT TRACK_ID, MIN(TIMESTAMP_SEC) AS entered_queue_at
    FROM PERSON_EVENTS
    WHERE VIDEO_ID = :video_id AND EVENT_TYPE = 'queue_entered'
    GROUP BY TRACK_ID
),
service_start AS (
    SELECT TRACK_ID, MIN(TIMESTAMP_SEC) AS service_started_at
    FROM PERSON_EVENTS
    WHERE VIDEO_ID = :video_id AND EVENT_TYPE = 'service_started'
    GROUP BY TRACK_ID
)
SELECT
    q.TRACK_ID,
    q.entered_queue_at,
    s.service_started_at,
    ROUND(s.service_started_at - q.entered_queue_at, 1) AS wait_time_sec
FROM queue_enter q
JOIN service_start s ON q.TRACK_ID = s.TRACK_ID
ORDER BY wait_time_sec DESC;

-- ============================================================
-- 6. ABANDONMENT / UNSERVICED: People who left without service
-- ============================================================
SELECT
    TRACK_ID,
    EVENT_TYPE,
    TIMESTAMP_SEC,
    DETAILS:zones_visited AS zones_visited,
    DETAILS:last_role::VARCHAR AS last_role,
    DETAILS:reason::VARCHAR AS reason
FROM PERSON_EVENTS
WHERE VIDEO_ID = :video_id
  AND EVENT_TYPE IN ('abandoned', 'unserviced')
ORDER BY TIMESTAMP_SEC;

-- ============================================================
-- 7. UNSTAFFED PERIODS: Counter unmanned while queue exists
-- ============================================================
SELECT
    TIMESTAMP_SEC,
    EVENT_TYPE,
    DETAILS:duration_sec::FLOAT AS duration_sec,
    DETAILS:queue_length::INT AS queue_length_at_time,
    DETAILS:reason::VARCHAR AS end_reason
FROM PERSON_EVENTS
WHERE VIDEO_ID = :video_id
  AND EVENT_TYPE IN ('counter_unstaffed_start', 'counter_unstaffed_end')
ORDER BY TIMESTAMP_SEC;

-- ============================================================
-- 8. ROLE DISTRIBUTION: What % of person-frames are each role
-- ============================================================
SELECT
    ROLE,
    COUNT(*) AS person_frames,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM PERSON_DETECTIONS
WHERE VIDEO_ID = :video_id
GROUP BY ROLE
ORDER BY person_frames DESC;

-- ============================================================
-- 9. CROSS-CAMERA JOURNEY TIMELINE (multi-camera stores)
--    Shows a person's full path across cameras via JOURNEY_ID.
-- ============================================================
SELECT
    COALESCE(NULLIF(JOURNEY_ID, ''), FEED_NAME || ':' || TRACK_ID) AS person_key,
    FEED_NAME,
    TRACK_ID,
    EVENT_TYPE,
    TIMESTAMP_SEC,
    DETAILS,
    JOURNEY_ID
FROM PERSON_EVENTS
WHERE VIDEO_ID = :video_id
  AND JOURNEY_ID IS NOT NULL AND JOURNEY_ID != ''
ORDER BY JOURNEY_ID, TIMESTAMP_SEC;

-- ============================================================
-- 10. JOURNEY-AWARE WAIT TIMES (works for single + multi-camera)
--     Uses JOURNEY_WAIT_TIMES view which falls back gracefully
--     to TRACK_ID when no cross-feed journey exists.
-- ============================================================
SELECT * FROM JOURNEY_WAIT_TIMES
WHERE VIDEO_ID = :video_id
ORDER BY wait_time_sec DESC;
