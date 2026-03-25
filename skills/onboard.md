# snow-cv-onboard — Use Case Onboarding Skill

## When to Use

Use this skill when a user wants to onboard a new video for computer vision analysis.
They will provide a video file (or path to one). They may or may not know what they
want to measure yet — that's what this flow is designed to discover.

## Core Principle

**Video first, conversation second, code last.**

Do NOT assume a use case. Do NOT default to retail zones. The onboarding flow is a
conversation that starts with "what do I see?" and "what do you want to know?" —
then builds the right config from the answers.

## Onboarding Flow

### Step 1: Video Setup

- Copy/verify the video is in `videos/` directory
- Extract a reference frame:
  ```python
  from snow_cv.pipeline import Pipeline
  frame = Pipeline._extract_reference_frame("videos/<name>.mp4")
  ```
- **Look at the reference frame** using your vision capability

### Step 2: Scene Description (MANDATORY — do not skip)

Describe what you see to the user. Be specific:
- What kind of location is this? (store, street, warehouse, parking lot, office, etc.)
- What's happening? (people walking, vehicles moving, queuing, etc.)
- What are the distinct spatial areas visible?
- How many people/objects are present?

Then **ask the user**:

> "Here's what I see in your video: [description]. What do you want to measure or
> understand from this footage?"

**Wait for their answer.** Do not proceed until they tell you what matters to them.

### Step 3: Use Case Selection

Based on the user's answer, determine the right approach:

1. **Check existing strategies:**
   ```python
   from snow_cv.strategies import _STRATEGY_REGISTRY
   print(list(_STRATEGY_REGISTRY.keys()))
   ```

2. **If an existing strategy matches** (e.g., user wants queue abandonment → retail,
   user wants ticket machine confusion → parking):
   - Confirm with user: "This sounds like our [X] use case. Want to use it?"
   - If yes, use that strategy's expected zones and event logic

3. **If no existing strategy matches** (net new use case):
   - Use `"generic"` as the use_case
   - Name zones based on what the user described (e.g., "loading_dock", "break_room",
     "crosswalk", "waiting_area" — whatever fits their scene and goals)
   - The generic strategy emits `track_appeared`, `zone_changed`, `track_lost` events
   - Optionally create a new strategy class if the user needs custom event logic

### Step 4: Zone Identification

Based on Steps 2-3, identify zones that serve the user's stated metrics:

- **For existing use cases:** use that strategy's expected zone names
- **For net new:** name zones based on the scene and user's goals
- Generate normalized polygon coordinates (0-1 range) for each zone
- Explain each zone to the user and confirm the layout makes sense

### Step 5: Generate Config

Write a JSON config file to `configs/<video_stem>.json`:

```json
{
  "store_id": "<descriptive_id>",
  "use_case": "<strategy_name>",
  "feed_name": "<camera_name>",
  "sample_fps": 2,
  "confidence_threshold": 0.25,
  "zones": {
    "<zone_name>": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]],
    ...
  },
  "zone_priority": ["<highest_priority_zone>", ...],
  "role_map": {"<zone_name>": "<role_name>", ...}
}
```

For existing use cases, include strategy-specific config blocks
(e.g., `"parking": { "confusion_dwell_threshold_sec": 30 }`).

### Step 6: Push Config to Backend

```python
import requests, json
config = json.load(open("configs/<video_stem>.json"))
requests.post("http://localhost:5001/api/set-zones", json={
    "path": "<video_stem>.mp4",
    "zones": config["zones"],
    "counter": config.get("counter"),
    "save": True,
    **{k: v for k, v in config.items() if k not in ("zones", "counter")}
})
```

### Step 7: Validate Pipeline Locally

Run the pipeline against the video:

```python
from snow_cv import StoreConfig, Pipeline, CsvOutput
config = StoreConfig.from_dict(json.load(open("configs/<video_stem>.json")))
pipeline = Pipeline(config=config, output=CsvOutput("validation_output"))
summary = pipeline.run("videos/<video_stem>.mp4")
print(f"Detections: {summary['total_detections']}")
print(f"Events: {summary['total_events']}")
print(f"Event types: {summary['events_by_type']}")
```

Check:
- Are detections being assigned the right roles?
- Are the expected events firing?
- Do the event counts make sense for the video content?

### Step 8: Start React Preview

Start backend + frontend so the user can visually verify:

```bash
cd backend && python3 server.py &
cd frontend && npm run dev &
```

Tell the user to open the frontend URL. The walkthrough tab will auto-load the
video with zones, role colors, and events.

### Step 9: Generate SQL Analytics (if deploying to Snowflake)

Create a SQL view specific to this use case in `sql/<use_case>_analytics.sql`:

- The view should query `PERSON_DETECTIONS` and `PERSON_EVENTS`
- KPIs should match the business questions from the user's stated goals (Step 2)
- Include 5-10 example queries that answer the user's business questions

### Step 10: Deploy to SPCS (if requested)

Upload the config file to the Snowflake stage:

```sql
PUT file://configs/<video_stem>.json @RAW_VIDEO/configs AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

Then deploy with the job spec referencing `STORE_CONFIG_PATH`:

```yaml
env:
  STORE_CONFIG_PATH: /mnt/raw_video/configs/<video_stem>.json
  SNOWFLAKE_WAREHOUSE: SNOW_CV_WH
```

## Key Principles

1. **Ask before assuming** — never pick a use case or zone layout without asking the user
2. **Generic is the default** — if no existing strategy fits, use generic. Don't force-fit.
3. **Config over code** — zone definitions, role maps, and thresholds go in JSON config
4. **Strategy pattern** — only create a new strategy class if the user needs custom events
5. **Visual verification** — always show the React preview before deploying
6. **One session** — the entire flow from video to insights should complete in a single session
