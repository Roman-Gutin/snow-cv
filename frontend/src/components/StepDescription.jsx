const STEPS = [
  {
    level: 1,
    title: "Raw Camera Frame",
    subtitle: "What the camera sees",
    description:
      "The unprocessed video frame from a fixed overhead CCTV camera. This is the input to our pipeline — a single RGB frame at the camera's native resolution. No inference has been performed yet.",
    details: [
      "Input: 1920×1080 or similar resolution JPEG frame",
      "Source: Fixed-position store surveillance camera",
      "Frame rate: Typically 2 fps for analysis (down-sampled from 15-30 fps)",
    ],
    accent: "#607D6B",
  },
  {
    level: 2,
    title: "YOLO Person Detection",
    subtitle: "Bounding boxes + confidence scores",
    description:
      "YOLOv8n-seg (nano segmentation variant) runs single-pass inference on each frame. It detects all people and outputs axis-aligned bounding boxes with confidence scores. At this stage we only know where people are — not who they are or what they're doing.",
    details: [
      "Model: YOLOv8n-seg (6.3M params, ~8ms/frame on GPU)",
      "Class filter: 'person' only (class 0)",
      "Confidence threshold: configurable (default 0.30)",
      "Output: bounding box coordinates + confidence per detection",
    ],
    accent: "#78D64B",
  },
  {
    level: 3,
    title: "Instance Segmentation",
    subtitle: "Pixel-precise silhouettes",
    description:
      "The segmentation head of YOLOv8n-seg outputs a binary mask for each detection, refined to pixel-level polygon contours. This gives us the exact silhouette of each person rather than a rectangular approximation — critical for accurate zone classification when people overlap.",
    details: [
      "Mask output: per-instance binary mask → polygon contour",
      "Simplification: cv2.approxPolyDP reduces vertex count",
      "Coordinates: normalized 0-1 relative to frame dimensions",
      "Benefit: precise centroid calculation vs. bounding box center",
    ],
    accent: "#49C5B1",
  },
  {
    level: 4,
    title: "Multi-Object Tracking",
    subtitle: "Persistent identity across frames",
    description:
      "ByteTrack assigns each detection a persistent track ID that follows the person across frames. It uses a Kalman filter for motion prediction and IoU-based matching, handling occlusions and brief disappearances. This is what turns isolated detections into continuous person trajectories.",
    details: [
      "Algorithm: ByteTrack (via ultralytics persist=True)",
      "Track ID: integer assigned on first appearance, maintained across frames",
      "Handles: partial occlusion, brief exits, overlapping paths",
      "Lost track threshold: configurable frames before ID is retired",
    ],
    accent: "#5C88DA",
  },
  {
    level: 5,
    title: "Zone Classification",
    subtitle: "Spatial role assignment via Florence-2 auto-detection",
    description:
      "Four named zones (Service, Queue, Entrance, Counter) are defined as normalized polygons. These can be auto-detected using Florence-2, a 230M-parameter vision-language model that identifies retail fixtures (shelves, counters, checkout areas, doors) from a reference frame and maps them to zone polygons. Each tracked person's centroid is tested against these zones using point-in-polygon checks.",
    details: [
      "Auto-detect: Florence-2-base with CAPTION_TO_PHRASE_GROUNDING task",
      "Zones: Service, Queue, Entrance (polygon regions) + Counter sub-region",
      "Role logic: centroid in counter → employee; in service → customer_being_served",
      "Fallback: default polygon definitions when auto-detection misses a zone",
      "Queue ordering: sorted by x-position within the queue zone",
    ],
    accent: "#A77BCA",
  },
  {
    level: 6,
    title: "Event Detection",
    subtitle: "Lifecycle and transition events",
    description:
      "By comparing each person's current role to their previous role, and tracking when IDs appear or disappear, we generate semantic events. A person first seen in the entrance zone triggers 'entered_store'; one first seen elsewhere is 'pre_existing'. When a track is lost, 'abandoned' means we saw them enter, 'unserviced' means we didn't.",
    details: [
      "Entry events: entered_store (via entrance) vs. pre_existing (elsewhere)",
      "Exit events: abandoned (entry observed) vs. unserviced (entry not observed)",
      "Role transitions: queue_entered, queue_exited, service_started, service_ended",
      "Staff events: employee_arrived, employee_left, counter_unstaffed_start/end",
    ],
    accent: "#EE2737",
  },
];

export default function StepDescription({ level }) {
  const step = STEPS[level - 1];
  if (!step) return null;

  return (
    <div className="step-description">
      <div className="step-badge" style={{ background: step.accent }}>
        Step {step.level} of 6
      </div>
      <h2 className="step-title">{step.title}</h2>
      <p className="step-subtitle">{step.subtitle}</p>
      <p className="step-body">{step.description}</p>
      <ul className="step-details">
        {step.details.map((d, i) => (
          <li key={i}>{d}</li>
        ))}
      </ul>
    </div>
  );
}
