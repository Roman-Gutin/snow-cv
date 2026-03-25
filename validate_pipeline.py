"""Local pipeline validation — runs RetailPipeline on the synthetic video with our zone config."""

import json
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from snow_cv import RetailPipeline, StoreConfig, CsvOutput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "examples", "configs", "synthetic_retail_queue.json")
VIDEO_PATH = os.path.join(os.path.dirname(__file__), "videos", "synthetic_retail_queue.mp4")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "validation_output")

def main():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    config = StoreConfig.from_dict(cfg)
    print(f"Store ID: {config.store_id}")
    print(f"Feeds: {[f.name for f in config.feeds]}")
    for feed in config.feeds:
        print(f"  Feed '{feed.name}': zones={list(feed.zones.keys())}, sample_fps={feed.sample_fps}")

    output = CsvOutput(output_dir=OUTPUT_DIR)
    pipeline = RetailPipeline(config=config, output=output)

    summary = pipeline.run(VIDEO_PATH)

    print("\n===== PIPELINE SUMMARY =====")
    print(f"Video ID:          {summary['video_id']}")
    print(f"Frames processed:  {summary['frames_processed']}")
    print(f"Total detections:  {summary['total_detections']}")
    print(f"Total events:      {summary['total_events']}")
    print(f"Elapsed:           {summary['elapsed_sec']}s")
    print(f"Processing FPS:    {summary['fps']}")
    print(f"\nEvents by type:")
    for evt_type, count in sorted(summary.get("events_by_type", {}).items()):
        print(f"  {evt_type}: {count}")

    # Quick check: read events CSV and look for wait-time-relevant events
    evt_path = os.path.join(OUTPUT_DIR, "events.csv")
    if os.path.exists(evt_path):
        import csv
        with open(evt_path) as f:
            reader = csv.reader(f)
            rows = list(reader)
        print(f"\n===== EVENT LOG ({len(rows)} rows) =====")
        queue_entered = [r for r in rows if r[2] == "queue_entered"]
        service_started = [r for r in rows if r[2] == "service_started"]
        entered_store = [r for r in rows if r[2] == "entered_store"]
        abandoned = [r for r in rows if r[2] == "abandoned"]
        print(f"  entered_store:   {len(entered_store)}")
        print(f"  queue_entered:   {len(queue_entered)}")
        print(f"  service_started: {len(service_started)}")
        print(f"  abandoned:       {len(abandoned)}")

        # Compute wait times where possible
        if queue_entered and service_started:
            print("\n===== WAIT TIME ANALYSIS =====")
            qe_by_track = {}
            for r in queue_entered:
                tid = r[1]
                ts = float(r[3])
                if tid not in qe_by_track:
                    qe_by_track[tid] = ts
            ss_by_track = {}
            for r in service_started:
                tid = r[1]
                ts = float(r[3])
                if tid not in ss_by_track:
                    ss_by_track[tid] = ts
            wait_times = []
            for tid in qe_by_track:
                if tid in ss_by_track:
                    wait = ss_by_track[tid] - qe_by_track[tid]
                    wait_times.append((tid, wait))
                    print(f"  Track {tid}: queued at {qe_by_track[tid]:.1f}s, served at {ss_by_track[tid]:.1f}s, wait = {wait:.1f}s")
            if wait_times:
                avg = sum(w for _, w in wait_times) / len(wait_times)
                print(f"\n  Average wait time: {avg:.1f}s")
            else:
                print("  No tracks with both queue_entered and service_started events.")
        else:
            print("\n  Not enough events to compute wait times.")

    print(f"\nOutput files in: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
