"""Local pipeline validation — runs Pipeline in parking mode on a gate camera video."""

import json
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from snow_cv import RetailPipeline, StoreConfig, CsvOutput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "configs", "metropolis_gate1.json")
VIDEO_PATH = os.environ.get(
    "PARKING_VIDEO",
    os.path.join(os.path.expanduser("~/Downloads"),
                 "ElevenLabs_video_veo-3-1-fast_CCTV camera ..._2026-03-24T19_22_33.mp4"),
)
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "parking_validation_output")


def main():
    if not os.path.exists(VIDEO_PATH):
        print(f"ERROR: Video not found at {VIDEO_PATH}")
        print("Set PARKING_VIDEO env var to the path of your parking gate video.")
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    config = StoreConfig.from_dict(cfg)
    print(f"Store ID:  {config.store_id}")
    print(f"Use case:  {config.use_case}")
    print(f"Strategy:  {config.strategy_config}")
    print(f"Feeds:     {[f.name for f in config.feeds]}")
    for feed in config.feeds:
        print(f"  Feed '{feed.name}': zones={list(feed.zones.keys())}, "
              f"priority={feed.zone_priority}, sample_fps={feed.sample_fps}")

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

    # Analyze parking-specific events
    evt_path = os.path.join(OUTPUT_DIR, "events.csv")
    if os.path.exists(evt_path):
        import csv
        with open(evt_path) as f:
            reader = csv.reader(f)
            rows = list(reader)
        print(f"\n===== EVENT LOG ({len(rows)} rows) =====")

        confusion = [r for r in rows if r[2] == "confusion_detected"]
        machine_start = [r for r in rows if r[2] == "machine_interaction_started"]
        machine_end = [r for r in rows if r[2] == "machine_interaction_ended"]
        prolonged = [r for r in rows if r[2] == "machine_interaction_prolonged"]
        exited = [r for r in rows if r[2] == "driver_exited_vehicle"]
        abandoned = [r for r in rows if r[2] == "abandoned_transaction"]
        completed = [r for r in rows if r[2] == "transaction_completed"]
        arrived = [r for r in rows if r[2] == "vehicle_arrived"]

        print(f"  vehicle_arrived:                {len(arrived)}")
        print(f"  machine_interaction_started:    {len(machine_start)}")
        print(f"  machine_interaction_ended:      {len(machine_end)}")
        print(f"  machine_interaction_prolonged:  {len(prolonged)}")
        print(f"  driver_exited_vehicle:          {len(exited)}")
        print(f"  confusion_detected:             {len(confusion)}")
        print(f"  transaction_completed:          {len(completed)}")
        print(f"  abandoned_transaction:          {len(abandoned)}")

        # Compute interaction durations
        if machine_start and machine_end:
            print("\n===== INTERACTION DURATION ANALYSIS =====")
            start_by_track = {}
            for r in machine_start:
                tid = r[1]
                ts = float(r[3])
                if tid not in start_by_track:
                    start_by_track[tid] = ts
            end_by_track = {}
            for r in machine_end:
                tid = r[1]
                ts = float(r[3])
                if tid not in end_by_track:
                    end_by_track[tid] = ts
            durations = []
            for tid in start_by_track:
                if tid in end_by_track:
                    dur = end_by_track[tid] - start_by_track[tid]
                    durations.append((tid, dur))
                    print(f"  Track {tid}: started at {start_by_track[tid]:.1f}s, "
                          f"ended at {end_by_track[tid]:.1f}s, duration = {dur:.1f}s")
            if durations:
                avg = sum(d for _, d in durations) / len(durations)
                print(f"\n  Average interaction time: {avg:.1f}s")

        if confusion:
            print("\n===== CONFUSION EVENTS =====")
            for r in confusion:
                print(f"  Track {r[1]} at {float(r[3]):.1f}s: {r[5]}")

    # Role distribution
    det_path = os.path.join(OUTPUT_DIR, "detections.csv")
    if os.path.exists(det_path):
        import csv
        from collections import Counter
        with open(det_path) as f:
            reader = csv.reader(f)
            det_rows = list(reader)
        roles = Counter(r[4] for r in det_rows)
        print(f"\n===== ROLE DISTRIBUTION ({len(det_rows)} detections) =====")
        for role, count in roles.most_common():
            print(f"  {role}: {count} ({100*count/len(det_rows):.1f}%)")

    print(f"\nOutput files in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
