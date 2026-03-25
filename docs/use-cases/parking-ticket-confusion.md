# Parking: Ticket Machine Confusion Detection

The second use case built on Snow CV. Given footage of a parking lot gate area with a ticket machine, the pipeline detects customer confusion and frustration — long dwell times at the kiosk, drivers exiting their vehicle, and abandoned transactions.

## What It Does

- Detects every person in frame (YOLOv8n-seg)
- Tracks them persistently across frames (ByteTrack)
- Classifies role based on zone: `approaching`, `at_machine`, `exited_vehicle`, `at_gate`
- Fires events when dwell time at the ticket machine exceeds a threshold (default 30s)
- Distinguishes completed transactions (reached gate) from abandoned ones (left without passing gate)
- Flags driver-exited-vehicle as a frustration signal

## Zones

| Zone | What It Represents |
|------|--------------------|
| `approach_lane` | Where vehicles arrive in the camera's view |
| `ticket_machine` | The kiosk area where drivers interact with the machine |
| `exit_vehicle` | Area where a driver has stepped out of the vehicle (frustration signal) |
| `gate_area` | The barrier gate — reaching here means the transaction completed |

## Events Generated

| Event | Meaning |
|-------|---------|
| `vehicle_arrived` | Person first detected in approach lane |
| `machine_interaction_started` | Person entered the ticket machine zone |
| `machine_interaction_ended` | Person left the ticket machine zone (includes dwell time) |
| `machine_interaction_prolonged` | Dwell time at machine exceeded threshold |
| `confusion_detected` | Prolonged dwell OR driver exited vehicle |
| `driver_exited_vehicle` | Person detected in exit vehicle zone |
| `gate_approached` | Person moved into gate area |
| `transaction_completed` | Person was at machine AND reached gate (track lost) |
| `abandoned_transaction` | Person was at machine AND never reached gate (track lost) |

## Business Questions Answered

- What percentage of customers experience confusion at the ticket machine?
- What is the average interaction time at the machine?
- How many transactions are abandoned vs completed?
- What times of day have the highest confusion rates?
- Does confusion correlate with specific machines or gate positions?

## Strategy

`ParkingStrategy` in `snow_cv/strategies.py`. Key logic:

- `classify_role`: zone name → role via role_map (e.g., `ticket_machine` → `at_machine`)
- `eval_appeared`: arriving as `approaching` → `vehicle_arrived`; arriving as `at_machine` → `machine_interaction_started` + start dwell timer
- `eval_transition`: entering machine zone starts interaction; leaving ends it with dwell time; entering exit_vehicle zone triggers frustration flag
- `eval_frame_level`: checks dwell time every frame; fires `confusion_detected` when threshold exceeded
- `eval_lost`: was at machine + reached gate = `transaction_completed`; was at machine + no gate = `abandoned_transaction`

## Configs

- `configs/metropolis_gate1.json` — zone polygons for split-screen CCTV footage

## SQL

- `sql/parking_analytics.sql` — `PARKING_ANALYTICS` view + 10 business queries

## Sample Video

`videos/metropolis_gate1.mp4` — parking garage CCTV footage with approach lane, ticket machine, exit area, and gate.
