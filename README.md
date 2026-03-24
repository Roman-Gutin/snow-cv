# snow-cv — Computer Vision on Snowflake

Turn any store camera feed into structured Snowflake data: who entered, who waited, who got served, who left without service. Built as a reusable toolkit with Cortex Code skills for rapid onboarding of increasingly complex computer vision workloads.

## What It Does

A Cortex Code skill looks at a frame from your video, identifies store zones (counter, queue, entrance, etc.), and generates everything needed to run a YOLO+ByteTrack pipeline on Snowflake SPCS. You get a React preview app, SPCS container deployment, and SQL queries against the resulting event data.

## Prerequisites

- Python 3.11+
- Node 18+
- Snowflake account with SPCS enabled (GPU compute pool)
- [Cortex Code CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code)

## Quick Start

```
1. Drop your .mp4 into the videos/ folder
2. Open Cortex Code in THIS folder:  cortex
3. Say:  onboard videos/my_store.mp4 for retail surveillance
```

That's it. The skill handles everything else.

## Onboarding Workflow

When you say "onboard" the skill runs this workflow:

1. **Intake** — asks what you want to measure (wait times, abandonment, staffing gaps)
2. **Frame extraction** — pulls a reference frame from your video
3. **Vision analysis** — the agent looks at the frame and identifies zone polygons (entrance, queue, service area, employee area, counter)
4. **Zone push** — POSTs zones to the local Flask server, saves config to `configs/`
5. **React preview** — you see zones overlaid on video frames with real-time detection, role classification, and events
6. **Job spec generation** — creates a `job_spec.yaml` with your zones baked in as env vars
7. **SPCS deployment** — runs `EXECUTE JOB SERVICE` on Snowflake GPU compute
8. **SQL verification** — runs key business queries against the event data

## Customization

Before deploying, set your Snowflake coordinates in one of two ways:

**Option A: Config file** (recommended for multi-camera)
```json
{
  "store_id": "my_store",
  "database": "MY_DB",
  "schema": "MY_SCHEMA",
  "warehouse": "MY_WH",
  ...
}
```

**Option B: Environment variables** (SPCS single-camera)
```
SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE
```

For SQL setup, edit the `USE DATABASE` / `USE SCHEMA` lines at the top of `sql/setup.sql` before running.

## Multi-Camera Support

The SDK supports multiple cameras per store with cross-camera person tracking:

- Define multiple feeds in a single config file (see `examples/configs/multi_camera_example.json`)
- `feed_links` declare which exit zone on camera A correlates with which entrance zone on camera B
- `MultiFeedManager` matches exits with entrances within a configurable time window
- `JOURNEY_ID` propagates through events, enabling cross-camera wait time calculation
- SQL views (`CROSS_FEED_JOURNEYS`, `JOURNEY_WAIT_TIMES`) handle both single and multi-camera gracefully

## Manual Setup (without the skill)

```bash
# Terminal 1: Backend
pip install -r requirements.txt
python backend/server.py
# Starts on http://localhost:5001

# Terminal 2: Frontend
cd frontend
npm install
npm run dev
# Opens on http://localhost:5173 (proxies /api to :5001)
```

## Snowflake Setup

Before running the SPCS job, create the tables and views:

```sql
-- Edit the database/schema at the top of setup.sql first
@sql/setup.sql
```

This creates:
- `PERSON_DETECTIONS` — per-frame bounding boxes, roles, positions
- `PERSON_EVENTS` — zone transition events (entered_store, queue_entered, service_started, etc.)
- `VIDEO_METADATA` — video processing summary with zone config
- `INFERENCE_TRACES` — debug traces from the pipeline
- `VIDEO_ANALYTICS` — view for single-feed KPIs
- `CROSS_FEED_JOURNEYS` — view for multi-camera handoff correlation
- `JOURNEY_WAIT_TIMES` — view for journey-aware wait times

## Architecture

```
snow-cv/
├── retail_vision/         ← Core Python SDK
│   ├── config.py          ← StoreConfig / FeedConfig (YAML, dict, env vars)
│   ├── detector.py        ← YOLOv8n-seg person detection
│   ├── tracker.py         ← ByteTrack ID persistence + dedup
│   ├── zones.py           ← ZoneMap with ray-casting point-in-polygon
│   ├── events.py          ← Declarative event rule engine
│   ├── feeds.py           ← MultiFeedManager (cross-camera correlation)
│   ├── pipeline.py        ← Full pipeline orchestrator
│   ├── output.py          ← CsvOutput (local) / SnowflakeOutput (SPCS)
│   ├── trace.py           ← Inference quality tracing
│   └── scene.py           ← Scene understanding utilities
├── container/
│   ├── Dockerfile         ← SPCS container (python:3.11 + CUDA + YOLO)
│   ├── analyze_frames.py  ← SPCS entrypoint (supports single + multi-feed)
│   ├── job_spec_template.yaml            ← Single-camera template
│   └── job_spec_multi_camera_template.yaml ← Multi-camera template
├── sql/
│   ├── setup.sql          ← CREATE TABLE/VIEW DDL
│   ├── analytics.sql      ← Pre-built analytics query patterns
│   └── key_questions.sql  ← Business questions as SQL
├── backend/
│   └── server.py          ← Flask API for the React preview app
├── frontend/              ← React 19 + Vite preview app
├── configs/               ← Generated zone configs (per deployment)
├── videos/                ← Drop your .mp4 files here
├── examples/              ← Reference configs and job specs
└── validate_pipeline.py   ← Local end-to-end validation script
```

## Events Generated

| Event | Meaning |
|-------|---------|
| `entered_store` | Person first detected in entrance zone |
| `pre_existing` | Person already in frame when video starts |
| `queue_entered` | Person moved into the queue zone |
| `service_started` | Person moved from queue to service zone |
| `service_ended` | Person left the service zone |
| `employee_arrived` | Staff detected in employee zone |
| `employee_left` | Staff left employee zone |
| `counter_unstaffed_start` | No employee at counter while customers present |
| `counter_unstaffed_end` | Employee returned to counter |
| `abandoned` | Person left queue without being served |

## Skills Used

This toolkit is built around two Cortex Code skills:

- **`retail-zone-setup`** — Camera onboarding: frame extraction, vision-based zone detection, config generation, React preview, SPCS job spec creation
- **`deploy-to-spcs`** — Container deployment: Docker build, image push, `EXECUTE JOB SERVICE`

## Container Image

```bash
cd container
docker build -t yolo-tracker-analyzer .
# Tag and push to your Snowflake image repo
```

The container:
1. Connects to Snowflake via OAuth (automatic in SPCS)
2. Loads config from file (`STORE_CONFIG_PATH`) or env vars
3. Downloads video(s) from `@RAW_VIDEO` stage
4. Runs YOLO + ByteTrack frame-by-frame per feed
5. Writes detections, events, and metadata to Snowflake tables
6. Exits (one-shot job pattern)
