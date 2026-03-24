# Retail: Customer Lines and Abandonment

The first use case built on Snow CV. Given footage of a retail store with a queue area and service counter, the pipeline measures customer wait times, detects queue abandonment, and tracks staffing gaps.

## What It Does

- Detects every person in frame (YOLOv8n-seg)
- Tracks them persistently across frames (ByteTrack)
- Fires events when they enter the store, join a queue, reach the service counter, or leave without being served
- Computes per-person wait times, abandonment rates, and staffing gap duration
- Supports multi-camera setups where a person walks from one camera's view to another (cross-camera journey correlation via `JOURNEY_ID`)

## Zones

The agent identifies these zones from a reference frame during onboarding:

| Zone | What It Represents |
|------|--------------------|
| `entrance` | Where customers enter the camera's field of view |
| `queue` | Where customers wait in line |
| `service` | Where customers are being served |
| `employee` | Where staff are positioned |
| `counter` | The service counter region (used for staffing gap detection) |

## Events Generated

| Event | Meaning |
|-------|---------|
| `entered_store` | Person first detected in entrance zone |
| `queue_entered` | Person moved into the queue zone |
| `service_started` | Person moved from queue to service zone |
| `service_ended` | Person left the service zone |
| `abandoned` | Person left queue without being served |
| `employee_arrived` | Staff detected in employee zone |
| `employee_left` | Staff left employee zone |
| `counter_unstaffed_start` | No employee at counter while customers present |
| `counter_unstaffed_end` | Employee returned to counter |

## Business Questions Answered

- What is the average customer wait time?
- What percentage of customers abandon the queue?
- How long are staffing gaps at the counter?
- What are the peak queue times?
- How does wait time correlate with abandonment?

## Configs

- Single camera: `examples/configs/synthetic_retail_queue.json`
- Multi-camera: `examples/configs/multi_camera_example.json`

## SQL

- Table/view DDL: `sql/setup.sql`
- Analytics patterns: `sql/analytics.sql`
- Business questions: `sql/key_questions.sql`

## Sample Video

`videos/synthetic_retail_queue.mp4` — a 12-second synthetic retail clip with customers entering, queuing, and being served. Used for local validation.
