# Snow CV — CV 101 with Auto SPCS Deployment.

## What Is It

Snow CV is a get-started kit for building computer vision use cases on Snowflake compute. Drop a video, describe what you want to measure, and a Cortex Code skill handles the rest — zone identification, event logic, GPU container deployment, and SQL analytics.

The repo includes a pluggable Python SDK, a GPU container for SPCS, pre-built SQL analytics, and a skill prompt that orchestrates the full onboarding flow. Two use cases are included (retail queue analytics and parking lot confusion detection). Adding a new one means writing one strategy class and one config file.

## Why It Matters

CV projects fail because teams couple detection, tracking, and business logic into one tangled codebase. Labeling is expensive. Custom model training takes months, rarely generalizes, and locks you into maintenance.

Snow CV decouples the stack:

- **Detection and tracking** use off-the-shelf models (YOLOv8, ByteTrack). No training, no labeling.
- **Business logic** lives in strategy classes — pluggable Python that maps zones to roles to events. One class per use case.
- **Scene understanding** is handled by the coding agent's own visual reasoning at onboarding time. It looks at your video and identifies the zones that matter. No annotation tool needed.
- **Deployment** is a config file uploaded to a Snowflake stage. The GPU container reads it and writes structured data to Snowflake tables.

The intelligence is in the orchestration, not the models.

## Get Started

### 1. Drop your video

Place your `.mp4` in the `videos/` folder.

### 2. Tell the agent what you see

Open Cortex Code in this repo and describe your use case:

```
I have a video of a parking garage at videos/gate1.mp4.
Customers pull up, use a ticket machine, and drive through a gate.
I want to detect when people are confused or frustrated at the
ticket machine — long dwell times, exiting the vehicle, or
abandoning the transaction. Get this data into Snowflake.
```

The agent takes it from there.

### 3. What the skill does

```
┌─────────────────────────────────────────────────────────────┐
│                     YOU DESCRIBE THE USE CASE                │
│  "parking lot, detect confusion at ticket machines"          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  1. EXTRACT REFERENCE FRAME                                  │
│     Pull a frame from the video for visual analysis          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  2. IDENTIFY ZONES (agent vision)                            │
│     Agent looks at the frame + your description              │
│     Maps spatial regions to business-relevant zones          │
│                                                              │
│     "I see an approach lane, a ticket kiosk area,            │
│      a vehicle exit lane, and a gate barrier"                │
│                                                              │
│     ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│     │ approach │ │  ticket  │ │   exit   │ │   gate   │    │
│     │  lane    │ │ machine  │ │ vehicle  │ │   area   │    │
│     └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  3. BUILD STRATEGY (or select existing one)                  │
│     zones → roles → events → business metrics                │
│                                                              │
│     Zone "ticket_machine" → role "at_machine"                │
│     Dwell > 30s at machine → event "confusion_detected"      │
│     Was at machine + never reached gate → "abandoned"        │
│                                                              │
│     One Python class. ~100 lines.                            │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  4. GENERATE CONFIG                                          │
│     JSON file with zone polygons, role map, thresholds       │
│     Uploaded to Snowflake stage alongside the video          │
│                                                              │
│     configs/gate1.json → @RAW_VIDEO/configs/gate1.json       │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  5. VALIDATE LOCALLY                                         │
│     Run pipeline against video with generated config         │
│     Check: roles correct? events firing? counts make sense?  │
│                                                              │
│     "64 detections, 93.8% at_machine, 4 events"             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  6. DEPLOY TO SPCS                                           │
│     Docker container + GPU compute pool                      │
│     Container reads config from stage, runs YOLO+ByteTrack   │
│     Writes PERSON_DETECTIONS + PERSON_EVENTS to Snowflake    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  7. SQL ANALYTICS                                            │
│     Use-case-specific views on top of generic tables         │
│                                                              │
│     SELECT confusion_rate, avg_dwell_sec, abandonment_pct    │
│     FROM PARKING_ANALYTICS                                   │
│                                                              │
│     Business insight, queryable in Snowflake.                │
└─────────────────────────────────────────────────────────────┘
```

The skill prompt that drives this flow is in `skills/onboard.md`. The agent follows it automatically when you describe a use case.

## Known Use Cases

| Domain | Use Case | Strategy |
|--------|----------|----------|
| Retail | [Customer Lines and Abandonment](docs/use-cases/retail-queue-abandonment.md) | `RetailStrategy` |
| Parking | [Ticket Machine Confusion Detection](docs/use-cases/parking-ticket-confusion.md) | `ParkingStrategy` |

## How a Use Case Works

Every use case follows the same structure:

```
video → zones → role map → strategy class → SQL view
```

**Zones** define WHERE things happen (spatial regions in the camera frame).

**Role map** defines WHAT someone is doing (zone name → role name).

**Strategy class** defines WHAT IT MEANS:
- `classify_role(zone)` — assign a role from a zone
- `eval_appeared(role)` — what events fire when someone shows up
- `eval_transition(old_role, new_role)` — what events fire when someone moves
- `eval_lost(track_info)` — what events fire when someone disappears
- `eval_frame_level(all_tracks)` — what to check every frame (dwell times, staffing gaps)

**SQL view** turns events into business metrics.

### Adding a New Use Case

1. Write a strategy class in `snow_cv/strategies.py` (~100 lines)
2. Register it: `register_strategy("airport", AirportStrategy)`
3. Create a config JSON in `configs/` with zone polygons and `"use_case": "airport"`
4. Create a SQL view in `sql/` for the business queries
5. Add a doc in `docs/use-cases/` and a row to the table above

No changes to the pipeline, event engine, container, or deployment. The strategy is the only code you write.

## Architecture

```
snow-cv/
├── snow_cv/                 ← Core Python SDK
│   ├── strategies.py        ← USE-CASE LOGIC LIVES HERE
│   │                          RetailStrategy, ParkingStrategy, ...
│   │                          register_strategy() to add your own
│   ├── config.py            ← StoreConfig / FeedConfig
│   ├── detector.py          ← YOLOv8n-seg person detection
│   ├── tracker.py           ← ByteTrack ID persistence + dedup
│   ├── zones.py             ← ZoneMap with ray-casting point-in-polygon
│   ├── events.py            ← Event engine (delegates to strategy)
│   ├── pipeline.py          ← Pipeline orchestrator (uses strategy)
│   ├── feeds.py             ← MultiFeedManager (cross-camera)
│   ├── output.py            ← CsvOutput (local) / SnowflakeOutput (SPCS)
│   ├── trace.py             ← Inference quality tracing
│   └── scene.py             ← Scene understanding utilities
├── container/
│   ├── Dockerfile           ← SPCS container (python:3.11 + CUDA + YOLO)
│   ├── analyze_frames.py    ← SPCS entrypoint (config-driven, any use case)
│   └── job_spec_template.yaml  ← Uses STORE_CONFIG_PATH (not hardcoded zones)
├── skills/
│   └── onboard.md           ← Skill prompt: the full onboarding flow
├── sql/
│   ├── setup.sql            ← Generic tables (PERSON_DETECTIONS, PERSON_EVENTS)
│   ├── analytics.sql        ← Retail analytics patterns
│   ├── parking_analytics.sql ← Parking analytics patterns
│   └── key_questions.sql    ← Business questions as SQL
├── configs/                 ← Zone configs per video (JSON)
├── docs/use-cases/          ← Write-up per use case
├── videos/                  ← Drop your .mp4 files here
└── validate_pipeline.py     ← Local end-to-end validation
```

## Models Used and Why

| Model | Task | Why This One |
|-------|------|-------------|
| **YOLOv8n-seg** | Person detection + segmentation | Nano variant runs on a single GPU at real-time speeds. Segmentation masks give precise boundaries for zone containment. Pre-trained on COCO — no labels needed. |
| **ByteTrack** | Multi-object tracking | Persistent IDs across frames. Works on top of any detector. No additional training. Handles occlusion well in fixed-camera scenarios. |
| **Cortex Code (agent)** | Scene understanding / zone detection | The agent looks at a reference frame and reasons about spatial layout. Replaces manual zone annotation. No vision model we tested (including Florence-2) could reliably identify business-relevant zones from a single frame. |

**Design principle:** No model requires custom training or labeled data. Detection and tracking use pre-trained weights. Scene understanding is the agent's visual reasoning at onboarding time. If a future use case needs a specialized model (action recognition, anomaly detection), it gets added to this table with the same bar: open-source, pre-trained, no labeling.

## Data Model

The Snowflake tables are use-case-generic. Any strategy writes to the same tables.

**`PERSON_DETECTIONS`** — one row per person per frame: track ID, role, bounding box, centroid, confidence, feed name.

**`PERSON_EVENTS`** — one row per event: track ID, event type, timestamp, details (JSON), feed name, journey ID.

**`VIDEO_METADATA`** — one row per video: duration, FPS, zone config.

**`INFERENCE_TRACES`** — per-frame pipeline telemetry for quality monitoring.

Use-case-specific SQL views sit on top:
- `VIDEO_ANALYTICS` — retail KPIs (queue length, wait time, staffing gaps)
- `PARKING_ANALYTICS` — parking KPIs (confusion rate, dwell time, abandonment)
- Your view here.

## Multi-Camera Support

- Define multiple feeds in a single config file
- `feed_links` declare which exit zone on camera A correlates with which entrance zone on camera B
- `MultiFeedManager` matches exits with entrances within a configurable time window
- `JOURNEY_ID` propagates through events, enabling cross-camera analytics

## Prerequisites

- Python 3.11+
- Snowflake account with SPCS enabled (GPU compute pool)
- [Cortex Code CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code)
