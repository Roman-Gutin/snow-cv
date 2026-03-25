# snow-cv-onboard — Use Case Onboarding Skill

## When to Use

Use this skill when a user wants to onboard a new computer vision use case. They will provide:
1. A video file (or path to one)
2. A description of what they want to measure

## Onboarding Flow

Execute these steps in order. Each step must complete before moving to the next.

### Step 1: Video Setup

- Copy/verify the video is in `videos/` directory
- Extract a reference frame using the SDK:
  ```python
  from snow_cv.pipeline import Pipeline
  frame = Pipeline._extract_reference_frame("videos/<name>.mp4")
  ```
- **Look at the reference frame** using your vision capability

### Step 2: Zone Identification

Based on the video frame AND the user's use case description, identify the zones that matter.

DO NOT hardcode to retail zones. Reason about what spatial regions are relevant:
- For **retail**: entrance, queue, service, employee areas
- For **parking**: approach lane, ticket machine, exit vehicle area, gate
- For **warehouse**: loading dock, staging area, restricted zone, walkway
- For **any new use case**: reason about what regions drive the business metrics the user cares about

Generate normalized polygon coordinates (0-1 range) for each zone.

### Step 3: Check Strategy Registry

```python
from snow_cv.strategies import get_strategy, _STRATEGY_REGISTRY
print(list(_STRATEGY_REGISTRY.keys()))  # See what's available
```

- If a matching strategy exists (e.g., "retail", "parking"), use it
- If not, you need to **create a new strategy class** in `snow_cv/strategies.py`:
  1. Subclass `UseCaseStrategy`
  2. Implement `classify_role()`, `eval_appeared()`, `eval_transition()`, `eval_lost()`
  3. Call `register_strategy("new_name", NewStrategy)`
  4. The zone_priority and role_map should reflect the zones you identified in Step 2

### Step 4: Generate Config

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
  "role_map": {"<zone_name>": "<role_name>", ...},
  "<use_case>": {
    ... use-case-specific thresholds ...
  }
}
```

### Step 5: Push Config to Backend

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

### Step 6: Validate Pipeline Locally

Run the pipeline against the video with the generated config:

```python
from snow_cv import StoreConfig, Pipeline
config = StoreConfig.from_dict(json.load(open("configs/<video_stem>.json")))
pipeline = Pipeline(config=config)
summary = pipeline.run("videos/<video_stem>.mp4")
print(f"Detections: {summary['total_detections']}")
print(f"Events: {summary['total_events']}")
print(f"Event types: {summary['events_by_type']}")
```

Check:
- Are detections being assigned the right roles?
- Are the expected events firing?
- Do the event counts make sense for the video content?

### Step 7: Start React Preview

Start backend + frontend so the user can visually verify:

```bash
cd backend && python3 server.py &
cd frontend && npm run dev &
```

Tell the user to open the frontend URL. The walkthrough tab will auto-load the video with zones, role colors, and events.

### Step 8: Generate SQL Analytics

Create a SQL view specific to this use case in `sql/<use_case>_analytics.sql`:

- The view should query `PERSON_DETECTIONS` and `PERSON_EVENTS` (these tables are use-case-generic)
- KPIs should match the business questions from the user's original description
- Include 5-10 example queries that answer the user's business questions

### Step 9: Deploy to SPCS

Upload the config file to the Snowflake stage alongside the video:

```sql
PUT file://configs/<video_stem>.json @RAW_VIDEO/configs AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
```

Then use the `deploy-to-spcs` skill with the job spec referencing `STORE_CONFIG_PATH`:

```yaml
env:
  STORE_CONFIG_PATH: /mnt/raw_video/configs/<video_stem>.json
  SNOWFLAKE_WAREHOUSE: SNOW_CV_WH
```

### Step 10: Verify SQL Output

After the container job completes, run the analytics view queries to verify business insights:

```sql
SELECT * FROM <USE_CASE>_ANALYTICS LIMIT 10;
```

Report the results to the user with a summary of what was detected and the key metrics.

## Key Principles

1. **Config over code** — zone definitions, role maps, and thresholds go in JSON config files, not hardcoded
2. **Strategy pattern** — use-case-specific behavior lives in strategy classes, not if/elif chains
3. **Generic data layer** — PERSON_DETECTIONS and PERSON_EVENTS accept any role/event strings; only the SQL views are use-case-specific
4. **Visual verification** — always show the user the React preview before deploying to SPCS
5. **One session** — the entire flow from video to SQL insights should complete in a single session
