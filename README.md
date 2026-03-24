# Snow CV — Computer Vision on Snowflake

## What Is It

Snow CV is a fast path from raw camera footage to structured Snowflake data. It combines Cortex Code skills, a Python SDK, GPU containers on SPCS, and pre-built SQL analytics so you can ship a computer vision use case in a single session — not weeks.

## Why It Matters

CV projects fail for predictable reasons. The abstractions are unclear — teams blur the line between detection, tracking, and business logic until everything is coupled. Labeling is expensive and slow. Training custom models takes months, often doesn't generalize, and locks you into a maintenance cycle. Most teams bite off more than they can chew and ship nothing.

Snow CV sidesteps this entirely. Instead of training custom models, it pairs open-source models that already work (YOLOv8 for detection, ByteTrack for tracking) with a coding agent that reasons about your video and your use case together. The agent looks at your footage, identifies the zones that matter, wires up the event logic, and deploys the pipeline — all in one session. The models are off the shelf. The intelligence is in the orchestration.

Over time, this repo will catalog which open-source models work best for which tasks. Customers won't need to shop for the best model — the toolkit will already know.

## Get Started

### 1. Drop your video

Place your `.mp4` in the `videos/` folder.

### 2. Tell the agent what you see

Open Cortex Code in this repo and describe your use case. Here's the prompt that shipped the retail queue use case:

```
I have a video of a retail store at videos/synthetic_retail_queue.mp4.
Customers walk in, wait in a queue, and get served at a counter.
I want to measure wait times, detect when people abandon the line,
and know when the counter is unstaffed. Help me build a pipeline
to get this data into Snowflake.
```

That's it. The agent takes it from there — it looks at your footage, identifies the zones, builds the config, previews the detections locally, deploys to SPCS, and runs the SQL to verify.

### 3. Verify in the React app

The agent starts a local preview app where you can see zones overlaid on your video with real-time detection, role classification, and events. This is your visual confirmation that the pipeline is generating the right data before it hits Snowflake.

## Known Use Cases

| Domain | Use Case | Status |
|--------|----------|--------|
| Retail | [Customer Lines and Abandonment](docs/use-cases/retail-queue-abandonment.md) | Shipped |

*See [Contributing](#contributing) to add yours.*

## What Cortex Code Skills Do For You

The heavy lifting happens through two skills that the agent invokes automatically. You don't call them directly — you describe your use case and the agent picks the right skill.

**`retail-zone-setup`** — Camera onboarding

The agent extracts a reference frame from your video, looks at it, and identifies the spatial layout: where the entrance is, where people queue, where the service counter is, where employees stand. It generates a zone config, pushes it to a local Flask server, and opens the React preview so you can confirm the zones are right. Then it builds a job spec with your zones baked in as environment variables.

This is the step that replaces weeks of manual annotation. The agent's visual reasoning identifies business-relevant zones from a single frame — something no off-the-shelf vision model (including Florence-2) could do reliably.

**`deploy-to-spcs`** — Container deployment

Once the zones are confirmed, the agent builds the Docker image, pushes it to your Snowflake image repository, and runs `EXECUTE JOB SERVICE` on a GPU compute pool. The container connects via OAuth, downloads your video from a Snowflake stage, runs YOLO + ByteTrack frame-by-frame, and writes structured detections and events to Snowflake tables. One-shot job — it runs and exits.

**The pattern:** You describe what you want to measure. The agent reasons about the video, picks the right models and zones, configures the pipeline, deploys it, and verifies the SQL output. The skills encode the operational knowledge so you don't have to.

## Models Used and Why

Snow CV uses open-source models chosen for the best tradeoff between accuracy, speed, and zero labeling cost. As the repo grows, this catalog will expand — the goal is that customers never need to evaluate models themselves.

| Model | Task | Why This One |
|-------|------|-------------|
| **YOLOv8n-seg** | Person detection + segmentation | Nano variant runs on a single GPU at real-time speeds. Segmentation masks give precise boundaries for zone containment, not just bounding boxes. Pre-trained on COCO — no custom labels needed. |
| **ByteTrack** | Multi-object tracking | Assigns persistent IDs across frames so you can track a specific person from entrance to service counter. Works on top of any detector's output with no additional training. Handles occlusion and re-identification well in fixed-camera scenarios. |
| **Cortex Code (multimodal agent)** | Scene understanding / zone detection | The agent itself looks at a reference frame from your video and reasons about the spatial layout — where the counter is, where people queue, where the entrance is. This replaces manual zone annotation entirely. No vision model we tested (including Florence-2) could reliably identify business-relevant zones from a single frame. The coding agent's native visual reasoning handles it. |

**Design principle:** No model in this stack requires custom training or labeled data. Detection and tracking use pre-trained weights. Scene understanding is handled by the agent's own visual reasoning at onboarding time — not a separate vision model. If a future use case needs a specialized model (e.g., action recognition, anomaly detection), it gets added to this table with the same bar: open-source, pre-trained, no labeling required.

## Multi-Camera Support

The SDK supports multiple cameras per store with cross-camera person tracking:

- Define multiple feeds in a single config file (see `examples/configs/multi_camera_example.json`)
- `feed_links` declare which exit zone on camera A correlates with which entrance zone on camera B
- `MultiFeedManager` matches exits with entrances within a configurable time window
- `JOURNEY_ID` propagates through events, enabling cross-camera wait time calculation
- SQL views (`CROSS_FEED_JOURNEYS`, `JOURNEY_WAIT_TIMES`) handle both single and multi-camera gracefully

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
├── docs/use-cases/        ← Detailed write-ups per use case
├── videos/                ← Drop your .mp4 files here
└── validate_pipeline.py   ← Local end-to-end validation script
```

## Prerequisites

- Python 3.11+
- Node 18+
- Snowflake account with SPCS enabled (GPU compute pool)
- [Cortex Code CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code)

## Contributing

Want to add a new use case or improve an existing one? Here's how:

### Adding a New Use Case

1. **Fork this repo** and create a branch: `git checkout -b use-case/my-new-case`
2. **Build your pipeline** using the `retail_vision` SDK as a reference. The core components (detector, tracker, zones, events, output) are reusable across use cases.
3. **Add example configs** in `examples/configs/` showing the zone layout and parameters for your use case
4. **Add SQL queries** in `sql/` with the analytics patterns relevant to your use case
5. **Create a use case doc** in `docs/use-cases/` and add a row to the Known Use Cases table in this README
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
