-- ============================================================
-- Parking Lot Ticket Machine Analytics — Setup & Views
-- Uses the same PERSON_DETECTIONS and PERSON_EVENTS tables
-- as the retail pipeline (schema is use-case-agnostic).
-- ============================================================

-- Parking-specific analytics view
-- Derives KPIs from PERSON_EVENTS for ticket machine confusion detection.
CREATE OR REPLACE VIEW PARKING_ANALYTICS AS
WITH interactions AS (
    -- Each machine interaction: start → end, with dwell time
    SELECT
        s.VIDEO_ID,
        s.TRACK_ID,
        s.FEED_NAME,
        s.TIMESTAMP_SEC AS interaction_start,
        MIN(e.TIMESTAMP_SEC) AS interaction_end,
        ROUND(MIN(e.TIMESTAMP_SEC) - s.TIMESTAMP_SEC, 2) AS interaction_duration_sec
    FROM PERSON_EVENTS s
    LEFT JOIN PERSON_EVENTS e
        ON s.VIDEO_ID = e.VIDEO_ID
        AND s.TRACK_ID = e.TRACK_ID
        AND s.FEED_NAME = e.FEED_NAME
        AND e.EVENT_TYPE = 'machine_interaction_ended'
        AND e.TIMESTAMP_SEC > s.TIMESTAMP_SEC
    WHERE s.EVENT_TYPE = 'machine_interaction_started'
    GROUP BY s.VIDEO_ID, s.TRACK_ID, s.FEED_NAME, s.TIMESTAMP_SEC
),
confusion_events AS (
    SELECT
        VIDEO_ID,
        COUNT(DISTINCT TRACK_ID) AS confused_people,
        COUNT(CASE WHEN DETAILS:"reason"::VARCHAR = 'driver_exited_vehicle' THEN 1 END) AS exited_vehicle_count,
        COUNT(CASE WHEN DETAILS:"reason"::VARCHAR = 'prolonged_dwell' THEN 1 END) AS prolonged_dwell_count
    FROM PERSON_EVENTS
    WHERE EVENT_TYPE = 'confusion_detected'
    GROUP BY VIDEO_ID
),
outcomes AS (
    SELECT
        VIDEO_ID,
        COUNT(CASE WHEN EVENT_TYPE = 'transaction_completed' THEN 1 END) AS completed_transactions,
        COUNT(CASE WHEN EVENT_TYPE = 'abandoned_transaction' THEN 1 END) AS abandoned_transactions,
        COUNT(CASE WHEN EVENT_TYPE = 'vehicle_arrived' THEN 1 END) AS total_arrivals,
        COUNT(CASE WHEN EVENT_TYPE = 'driver_exited_vehicle' THEN 1 END) AS drivers_exited_vehicle,
        COUNT(*) AS total_events
    FROM PERSON_EVENTS
    GROUP BY VIDEO_ID
)
SELECT
    m.VIDEO_ID,
    m.VIDEO_FILENAME,
    m.DURATION_SEC,
    m.FPS,
    m.FEED_NAME,
    COALESCE(o.total_arrivals, 0)           AS TOTAL_ARRIVALS,
    COALESCE(o.completed_transactions, 0)   AS COMPLETED_TRANSACTIONS,
    COALESCE(o.abandoned_transactions, 0)   AS ABANDONED_TRANSACTIONS,
    COALESCE(o.drivers_exited_vehicle, 0)   AS DRIVERS_EXITED_VEHICLE,
    COALESCE(c.confused_people, 0)          AS CONFUSED_PEOPLE,
    COALESCE(c.exited_vehicle_count, 0)     AS EXIT_VEHICLE_CONFUSION_COUNT,
    COALESCE(c.prolonged_dwell_count, 0)    AS PROLONGED_DWELL_CONFUSION_COUNT,
    ROUND(i.avg_duration, 2)                AS AVG_INTERACTION_SEC,
    ROUND(i.max_duration, 2)                AS MAX_INTERACTION_SEC,
    ROUND(i.min_duration, 2)                AS MIN_INTERACTION_SEC,
    ROUND(
        COALESCE(o.abandoned_transactions, 0) * 100.0
        / NULLIF(COALESCE(o.completed_transactions, 0) + COALESCE(o.abandoned_transactions, 0), 0),
        1
    ) AS ABANDONMENT_RATE_PCT,
    ROUND(
        COALESCE(c.confused_people, 0) * 100.0
        / NULLIF(COALESCE(o.total_arrivals, 0), 0),
        1
    ) AS CONFUSION_RATE_PCT,
    COALESCE(o.total_events, 0) AS TOTAL_EVENTS,
    m.ZONE_CONFIG,
    m.CREATED_AT
FROM VIDEO_METADATA m
LEFT JOIN (
    SELECT VIDEO_ID,
           AVG(interaction_duration_sec) AS avg_duration,
           MAX(interaction_duration_sec) AS max_duration,
           MIN(interaction_duration_sec) AS min_duration
    FROM interactions
    GROUP BY VIDEO_ID
) i ON m.VIDEO_ID = i.VIDEO_ID
LEFT JOIN confusion_events c ON m.VIDEO_ID = c.VIDEO_ID
LEFT JOIN outcomes o ON m.VIDEO_ID = o.VIDEO_ID;


-- ============================================================
-- Key Business Questions for Parking Lot Operations
-- ============================================================

-- 1. How many drivers experienced confusion at the ticket machine?
--    (exited vehicle OR prolonged dwell)
SELECT
    COUNT(DISTINCT TRACK_ID) AS confused_drivers,
    COUNT(CASE WHEN DETAILS:"reason"::VARCHAR = 'driver_exited_vehicle' THEN 1 END) AS exited_vehicle,
    COUNT(CASE WHEN DETAILS:"reason"::VARCHAR = 'prolonged_dwell' THEN 1 END) AS prolonged_dwell
FROM PERSON_EVENTS
WHERE EVENT_TYPE = 'confusion_detected'
-- AND VIDEO_ID = '<VIDEO_ID>'
;


-- 2. What is the average time to complete a ticket machine transaction?
SELECT
    s.TRACK_ID,
    s.TIMESTAMP_SEC AS interaction_start,
    e.TIMESTAMP_SEC AS interaction_end,
    ROUND(e.TIMESTAMP_SEC - s.TIMESTAMP_SEC, 1) AS duration_sec
FROM PERSON_EVENTS s
JOIN PERSON_EVENTS e
    ON s.VIDEO_ID = e.VIDEO_ID
    AND s.TRACK_ID = e.TRACK_ID
    AND e.EVENT_TYPE = 'machine_interaction_ended'
    AND e.TIMESTAMP_SEC > s.TIMESTAMP_SEC
WHERE s.EVENT_TYPE = 'machine_interaction_started'
-- AND s.VIDEO_ID = '<VIDEO_ID>'
ORDER BY duration_sec DESC;


-- 3. What percentage of transactions were abandoned?
WITH outcomes AS (
    SELECT
        COUNT(CASE WHEN EVENT_TYPE = 'transaction_completed' THEN 1 END) AS completed,
        COUNT(CASE WHEN EVENT_TYPE = 'abandoned_transaction' THEN 1 END) AS abandoned
    FROM PERSON_EVENTS
    -- WHERE VIDEO_ID = '<VIDEO_ID>'
)
SELECT
    completed,
    abandoned,
    completed + abandoned AS total,
    ROUND(abandoned * 100.0 / NULLIF(completed + abandoned, 0), 1) AS abandonment_rate_pct
FROM outcomes;


-- 4. Full event timeline — see everything that happened in order
SELECT
    TRACK_ID,
    EVENT_TYPE,
    TIMESTAMP_SEC,
    FRAME_IDX,
    DETAILS
FROM PERSON_EVENTS
-- WHERE VIDEO_ID = '<VIDEO_ID>'
ORDER BY TIMESTAMP_SEC, TRACK_ID;


-- 5. Role distribution — time spent in each zone
SELECT
    ROLE,
    COUNT(DISTINCT TRACK_ID) AS unique_people,
    COUNT(*) AS total_detections,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM PERSON_DETECTIONS
-- WHERE VIDEO_ID = '<VIDEO_ID>'
GROUP BY ROLE
ORDER BY total_detections DESC;


-- 6. Confusion incidents with details
SELECT
    TRACK_ID,
    EVENT_TYPE,
    TIMESTAMP_SEC,
    DETAILS:"reason"::VARCHAR AS confusion_reason,
    DETAILS:"dwell_sec"::FLOAT AS dwell_at_machine_sec,
    DETAILS
FROM PERSON_EVENTS
WHERE EVENT_TYPE = 'confusion_detected'
-- AND VIDEO_ID = '<VIDEO_ID>'
ORDER BY TIMESTAMP_SEC;


-- 7. Abandoned transactions — who gave up and why?
SELECT
    TRACK_ID,
    TIMESTAMP_SEC,
    DETAILS:"zones_visited" AS zones_visited,
    DETAILS:"last_role"::VARCHAR AS last_role,
    DETAILS:"machine_dwell_sec"::FLOAT AS machine_dwell_sec
FROM PERSON_EVENTS
WHERE EVENT_TYPE = 'abandoned_transaction'
-- AND VIDEO_ID = '<VIDEO_ID>'
ORDER BY TIMESTAMP_SEC;


-- 8. Drivers who exited their vehicle (strongest frustration signal)
SELECT
    TRACK_ID,
    EVENT_TYPE,
    TIMESTAMP_SEC,
    DETAILS:"from_role"::VARCHAR AS was_doing,
    DETAILS
FROM PERSON_EVENTS
WHERE EVENT_TYPE = 'driver_exited_vehicle'
-- AND VIDEO_ID = '<VIDEO_ID>'
ORDER BY TIMESTAMP_SEC;


-- 9. Gate throughput — vehicles per hour
SELECT
    DATE_TRUNC('hour', CREATED_AT) AS hour_bucket,
    COUNT(CASE WHEN EVENT_TYPE = 'vehicle_arrived' THEN 1 END) AS arrivals,
    COUNT(CASE WHEN EVENT_TYPE = 'transaction_completed' THEN 1 END) AS completed,
    COUNT(CASE WHEN EVENT_TYPE = 'confusion_detected' THEN 1 END) AS confusion_incidents
FROM PERSON_EVENTS
-- WHERE VIDEO_ID = '<VIDEO_ID>'
GROUP BY hour_bucket
ORDER BY hour_bucket;


-- 10. Summary dashboard view
SELECT * FROM PARKING_ANALYTICS
ORDER BY CREATED_AT DESC;
