-- ============================================================
-- Key Business Questions — Run After SPCS Pipeline Completes
-- ============================================================
-- These queries answer the questions that matter most to store
-- operations teams. They work against the PERSON_EVENTS and
-- PERSON_DETECTIONS tables written by the SPCS pipeline.
--
-- Replace <VIDEO_ID> with the actual video ID, or remove the
-- WHERE clause to query across all videos.
-- ============================================================

-- 1. How many customers entered the store and did NOT get served?
--    (The #1 question for retail ops)
--    NOTE: Excludes employees — a track with 'employee_arrived' is staff, not a customer.
WITH customers AS (
    SELECT DISTINCT track_id
    FROM PERSON_EVENTS
    WHERE event_type IN ('entered_store', 'pre_existing')
      -- WHERE video_id = '<VIDEO_ID>'
      AND track_id NOT IN (
          SELECT track_id FROM PERSON_EVENTS
          WHERE event_type = 'employee_arrived'
          -- AND video_id = '<VIDEO_ID>'
      )
),
served AS (
    SELECT DISTINCT track_id
    FROM PERSON_EVENTS
    WHERE event_type = 'service_started'
      AND track_id IN (SELECT track_id FROM customers)
      -- AND video_id = '<VIDEO_ID>'
)
SELECT
    (SELECT COUNT(*) FROM customers) AS total_customers,
    (SELECT COUNT(*) FROM served) AS served,
    (SELECT COUNT(*) FROM customers) - (SELECT COUNT(*) FROM served) AS not_served,
    ROUND(
        ((SELECT COUNT(*) FROM customers) - (SELECT COUNT(*) FROM served))
        / NULLIF((SELECT COUNT(*) FROM customers), 0) * 100, 1
    ) AS pct_not_served;


-- 2. What is the average wait time from queue entry to service start?
SELECT
    q.track_id,
    q.timestamp_sec AS queue_entered_at,
    s.timestamp_sec AS service_started_at,
    ROUND(s.timestamp_sec - q.timestamp_sec, 1) AS wait_seconds
FROM PERSON_EVENTS q
JOIN PERSON_EVENTS s
    ON q.track_id = s.track_id
    AND q.video_id = s.video_id
    AND q.event_type = 'queue_entered'
    AND s.event_type = 'service_started'
-- WHERE q.video_id = '<VIDEO_ID>'
ORDER BY wait_seconds DESC;


-- 3. When was the counter unstaffed while customers were in the store?
SELECT
    e.timestamp_sec AS unstaffed_start,
    COALESCE(
        (SELECT MIN(e2.timestamp_sec)
         FROM PERSON_EVENTS e2
         WHERE e2.video_id = e.video_id
           AND e2.event_type = 'counter_unstaffed_end'
           AND e2.timestamp_sec > e.timestamp_sec),
        999
    ) AS unstaffed_end,
    COALESCE(
        (SELECT MIN(e2.timestamp_sec)
         FROM PERSON_EVENTS e2
         WHERE e2.video_id = e.video_id
           AND e2.event_type = 'counter_unstaffed_end'
           AND e2.timestamp_sec > e.timestamp_sec),
        999
    ) - e.timestamp_sec AS gap_seconds
FROM PERSON_EVENTS e
WHERE e.event_type = 'counter_unstaffed_start'
-- AND e.video_id = '<VIDEO_ID>'
ORDER BY e.timestamp_sec;


-- 4. What percentage of time was an employee present at the counter?
SELECT
    m.duration_sec AS video_duration,
    COALESCE(SUM(staffed_duration), 0) AS staffed_seconds,
    ROUND(COALESCE(SUM(staffed_duration), 0) / m.duration_sec * 100, 1) AS pct_staffed
FROM VIDEO_METADATA m
LEFT JOIN (
    SELECT
        e.video_id,
        COALESCE(
            (SELECT MIN(e2.timestamp_sec)
             FROM PERSON_EVENTS e2
             WHERE e2.video_id = e.video_id
               AND e2.event_type = 'employee_left'
               AND e2.timestamp_sec > e.timestamp_sec),
            m2.duration_sec
        ) - e.timestamp_sec AS staffed_duration
    FROM PERSON_EVENTS e
    JOIN VIDEO_METADATA m2 ON e.video_id = m2.video_id
    WHERE e.event_type = 'employee_arrived'
) staffed ON m.video_id = staffed.video_id
-- WHERE m.video_id = '<VIDEO_ID>'
GROUP BY m.video_id, m.duration_sec;


-- 5. Full event timeline — see everything that happened in order
SELECT
    track_id,
    event_type,
    timestamp_sec,
    frame_idx,
    details
FROM PERSON_EVENTS
-- WHERE video_id = '<VIDEO_ID>'
ORDER BY timestamp_sec, track_id;


-- 6. Queue length over time (sampled per frame)
SELECT
    d.timestamp_sec,
    COUNT(DISTINCT CASE WHEN d.role = 'in_queue' THEN d.track_id END) AS queue_length,
    COUNT(DISTINCT CASE WHEN d.role = 'employee' THEN d.track_id END) AS employees,
    COUNT(DISTINCT CASE WHEN d.role = 'customer_being_served' THEN d.track_id END) AS being_served,
    COUNT(DISTINCT d.track_id) AS total_people
FROM PERSON_DETECTIONS d
-- WHERE d.video_id = '<VIDEO_ID>'
GROUP BY d.timestamp_sec
ORDER BY d.timestamp_sec;


-- 7. Which customers were in the queue but never got served?
SELECT DISTINCT q.track_id
FROM PERSON_EVENTS q
WHERE q.event_type = 'queue_entered'
  AND q.track_id NOT IN (
    SELECT s.track_id
    FROM PERSON_EVENTS s
    WHERE s.event_type = 'service_started'
      AND s.video_id = q.video_id
  )
-- AND q.video_id = '<VIDEO_ID>'
;


-- 8. Role distribution summary — how many unique people in each role?
SELECT
    role,
    COUNT(DISTINCT track_id) AS unique_people,
    COUNT(*) AS total_detections
FROM PERSON_DETECTIONS
-- WHERE video_id = '<VIDEO_ID>'
GROUP BY role
ORDER BY unique_people DESC;


-- 9. Cross-camera wait time — how long did a person wait from lobby queue
--    to being served inside the store? (requires multi-camera with feed_links)
--    Uses JOURNEY_ID to link the same person across cameras.
SELECT
    person_key,
    queue_feed,
    queue_track,
    queue_entered_at,
    service_feed,
    service_track,
    service_started_at,
    wait_time_sec,
    JOURNEY_ID
FROM JOURNEY_WAIT_TIMES
-- WHERE VIDEO_ID = '<VIDEO_ID>'
ORDER BY wait_time_sec DESC;


-- 10. Cross-camera handoffs — which exits on one camera matched entrances
--     on another? Shows the temporal correlation between cameras.
SELECT *
FROM CROSS_FEED_JOURNEYS
-- WHERE VIDEO_ID = '<VIDEO_ID>'
ORDER BY exit_time;
