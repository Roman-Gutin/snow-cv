# Snow CV — Computer Vision on Snowflake

## What Is It

Snow CV is a fast path from raw camera footage to structured Snowflake data. It combines Cortex Code skills, a Python SDK, GPU containers on SPCS, and pre-built SQL analytics so you can ship a computer vision use case in a single session — not weeks.

## Why It Matters

CV projects fail for predictable reasons. The abstractions are unclear — teams blur the line between detection, tracking, and business logic until everything is coupled. Labeling is expensive and slow. Training custom models takes months, often doesn't generalize, and locks you into a maintenance cycle. Most teams bite off more than they can chew and ship nothing.

Snow CV sidesteps this entirely. Instead of training custom models, it pairs open-source models that already work (YOLOv8 for detection, ByteTrack for tracking, Florence-2 for scene understanding) with an agent that reasons about your video and your use case together. The agent looks at your footage, identifies the zones that matter, wires up the event logic, and deploys the pipeline — all in one session. The models are off the shelf. The intelligence is in the orchestration.

Over time, this repo will catalog which open-source models work best for which tasks. Customers won't need to shop for the best model — the toolkit will already know.

## How to Onboard Your Use Case

### 1. Drop your video

Place your `.mp4` file in the `videos/` folder.

### 2. Open Cortex Code in this repo

```bash
cortex
```

### 3. Tell the agent what you want to measure

```
onboard videos/my_store.mp4 for customer wait time analysis
```

The skill runs this workflow automatically:

1. **Intake** — asks what you want to measure (wait times, abandonment, staffing gaps, etc.)
2. **Frame extraction** — pulls a reference frame from your video
3. **Vision analysis** — the agent looks at the frame and identifies zone polygons (entrance, queue, service area, employee area, counter)
4. **Config generation** — saves zone config to `configs/`
5. **React preview** — zones overlaid on video frames with real-time detection and events
6. **Job spec generation** — creates a `job_spec.yaml` with your zones baked in as env vars
7. **SPCS deployment** — runs `EXECUTE JOB SERVICE` on Snowflake GPU compute
8. **SQL verification** — runs business queries against the resulting event data

### 4. Set your Snowflake coordinates

Before deploying, configure your target database in one of two ways:

**Option A: Config file** (recommended for multi-camera)
```json
{
  "store_id": "my_store",
  "database": "MY_DB",
  "schema": "MY_SCHEMA",
  "warehouse": "MY_WH"
}
```

**Option B: Environment variables** (SPCS single-camera)
```
SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE
```

For SQL setup, edit the `USE DATABASE` / `USE SCHEMA` lines at the top of `sql/setup.sql` before running.

## Known Use Cases

| Domain | Use Case | Description |
|--------|----------|-------------|
| Retail | [Customer Lines and Abandonment](#retail-customer-lines-and-abandonment) | Measure customer wait times, detect queue abandonment, track staffing gaps at service counters |

*This repo will grow with more use cases over time.*

---

### Retail: Customer Lines and Abandonment

The first use case built on Snow CV. Given footage of a retail store with a queue area and service counter, the pipeline:

- Detects every person in frame (YOLOv8n-seg)
- Tracks them persistently across frames (ByteTrack)
- Fires events when they enter the store, join a queue, reach the service counter, or leave without being served
- Computes per-person wait times, abandonment rates, and staffing gap duration
- Supports multi-camera setups where a person walks from one camera's view to another (cross-camera journey correlation via `JOURNEY_ID`)

**Events generated:**

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

**Example configs:** `examples/configs/synthetic_retail_queue.json`, `examples/configs/multi_camera_example.json`

**SQL queries:** `sql/analytics.sql`, `sql/key_questions.sql`

---

## Multi-Camera Support

The SDK supports multiple cameras per store with cross-camera person tracking:

- Define multiple feeds in a single config file (see `examples/configs/multi_camera_example.json`)
- `feed_links` declare which exit zone on camera A correlates with which entrance zone on camera B
- `MultiFeedManager` matches exits with entrances within a configurable time window
- `JOURNEY_ID` propagates through events, enabling cross-camera wait time calculation
- SQL views (`CROSS_FEED_JOURNEYS`, `JOURNEY_WAIT_TIMES`) handle both single and multi-camera gracefully

## Models Used and Why

Snow CV uses open-source models chosen for the best tradeoff between accuracy, speed, and zero labeling cost. As the repo grows, this catalog will expand — the goal is that customers never need to evaluate models themselves.

| Model | Task | Why This One |
|-------|------|-------------|
| **YOLOv8n-seg** | Person detection + segmentation | Nano variant runs on a single GPU at real-time speeds. Segmentation masks give precise boundaries for zone containment, not just bounding boxes. Pre-trained on COCO — no custom labels needed. |
| **ByteTrack** | Multi-object tracking | Assigns persistent IDs across frames so you can track a specific person from entrance to service counter. Works on top of any detector's output with no additional training. Handles occlusion and re-identification well in fixed-camera scenarios. |
| **Cortex Code (multimodal agent)** | Scene understanding / zone detection | The agent itself looks at a reference frame from your video and reasons about the spatial layout — where the counter is, where people queue, where the entrance is. This replaces manual zone annotation entirely. No vision model we tested (including Florence-2) could reliably identify business-relevant zones from a single frame. The coding agent's native visual reasoning handles it. |

**Design principle:** No model in this stack requires custom training or labeled data. Detection and tracking use pre-trained weights. Scene understanding is handled by the agent's own visual reasoning at onboarding time — not a separate vision model. If a future use case needs a specialized model (e.g., action recognition, anomaly detection), it gets added to this table with the same bar: open-source, pre-trained, no labeling required.

## Prerequisites

- Python 3.11+
- Node 18+
- Snowflake account with SPCS enabled (GPU compute pool)
- [Cortex Code CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code)

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
- `PERSON_EVENTS` — zone transition events
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
│   └── job_spec_template.yaml
├── sql/
│   ├── setup.sql          ← CREATE TABLE/VIEW DDL
│   ├── analytics.sql      ← Pre-built analytics query patterns
│   └── key_questions.sql  ← Business questions as SQL
├── backend/
│   └── server.py          ← Flask API for the React preview app
├── frontend/              ← React 19 + Vite preview app
├── examples/              ← Reference configs and job specs
├── videos/                ← Drop your .mp4 files here
└── validate_pipeline.py   ← Local end-to-end validation script
```

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

## Contributing

Want to add a new use case or improve an existing one? Here's how:

### Adding a New Use Case

1. **Fork this repo** and create a branch: `git checkout -b use-case/my-new-case`
2. **Build your pipeline** using the `retail_vision` SDK as a reference. The core components (detector, tracker, zones, events, output) are reusable across use cases.
3. **Add example configs** in `examples/configs/` showing the zone layout and parameters for your use case
4. **Add SQL queries** in `sql/` with the analytics patterns relevant to your use case
5. **Add a section** to this README under "Known Use Cases" with a link anchor and description
6. **Include a sample video** in `videos/` (keep it small — under 5MB) or document how to obtain test footage
7. **Validate locally**: run `python validate_pipeline.py` or create a use-case-specific validation script
8. **Open a PR** with:
   - What the use case measures
   - What zones/events it uses
   - Sample SQL output
   - A screenshot or summary from the React preview (optional but helpful)

### Improving the SDK

- Bug fixes and performance improvements to `retail_vision/` are welcome
- If you add a new event type, update `retail_vision/defaults/event_rules.yaml` and document it
- If you add a new output writer, follow the `OutputWriter` abstract base class pattern in `output.py`
- Keep configs genericized — use `SNOW_CV_DB` / `SNOW_CV_SCHEMA` / `SNOW_CV_WH` as placeholder defaults

### Style

- Keep it simple. The goal is rapid onboarding, not framework perfection.
- Every use case should be deployable in a single Cortex Code session.
- Configs over code — zone definitions, event rules, and Snowflake coordinates belong in config files, not hardcoded.
