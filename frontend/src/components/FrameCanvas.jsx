import { useRef, useEffect } from "react";

const ROLE_COLORS = {
  employee: "#5C88DA",
  customer_being_served: "#78D64B",
  in_queue: "#FFC658",
  entering: "#A77BCA",
  exiting: "#FFA38B",
  at_entrance: "#A77BCA",
  other: "#CED9E5",
  // Parking roles
  at_machine: "#FFC658",
  exited_vehicle: "#EE2737",
  at_gate: "#78D64B",
  approaching: "#A77BCA",
};

const EVENT_COLORS = {
  entered_store: "#A77BCA",
  exited_store: "#FFA38B",
  pre_existing: "#CED9E5",
  queue_entered: "#FFC658",
  queue_exited: "#FFC658",
  service_started: "#78D64B",
  service_ended: "#8FE066",
  abandoned: "#EE2737",
  unserviced: "#E35205",
  employee_arrived: "#5C88DA",
  employee_left: "#5C88DA",
  counter_unstaffed_start: "#E35205",
  counter_unstaffed_end: "#78D64B",
  // Parking events
  vehicle_arrived: "#A77BCA",
  machine_interaction_started: "#FFC658",
  machine_interaction_ended: "#FFC658",
  machine_interaction_prolonged: "#E35205",
  driver_exited_vehicle: "#EE2737",
  confusion_detected: "#EE2737",
  gate_approached: "#78D64B",
  transaction_completed: "#78D64B",
  abandoned_transaction: "#EE2737",
  passed_without_interaction: "#CED9E5",
  person_detected: "#CED9E5",
  person_left: "#FFA38B",
};

const ZONE_COLORS = {
  employee: "rgba(92, 136, 218, 0.22)",
  service: "rgba(120, 214, 75, 0.28)",
  queue: "rgba(255, 198, 88, 0.28)",
  entrance: "rgba(167, 123, 202, 0.28)",
  counter: "rgba(75, 61, 42, 0.32)",
  // Parking zones
  ticket_machine: "rgba(255, 198, 88, 0.28)",
  exit_vehicle: "rgba(238, 39, 55, 0.22)",
  gate_area: "rgba(120, 214, 75, 0.28)",
  approach_lane: "rgba(167, 123, 202, 0.28)",
};

const ZONE_BORDERS = {
  employee: "rgba(92, 136, 218, 0.85)",
  service: "rgba(120, 214, 75, 0.85)",
  queue: "rgba(255, 198, 88, 0.85)",
  entrance: "rgba(167, 123, 202, 0.85)",
  counter: "rgba(75, 61, 42, 0.9)",
  // Parking zones
  ticket_machine: "rgba(255, 198, 88, 0.85)",
  exit_vehicle: "rgba(238, 39, 55, 0.85)",
  gate_area: "rgba(120, 214, 75, 0.85)",
  approach_lane: "rgba(167, 123, 202, 0.85)",
};

// visibilityLevel controls progressive overlay rendering (for walkthrough mode):
//   1 = raw frame only
//   2 = + bounding boxes with confidence
//   3 = + segmentation masks
//   4 = + track ID labels
//   5 = + zone overlays + role labels
//   6 = + event annotations
//   undefined/null = show everything (backward compatible)
export default function FrameCanvas({ imageSrc, analysis, zones, counter, showZones, showPersons, showRoles: showRolesProp, showEvents: showEventsProp, width = 1280, height = 720, visibilityLevel, events }) {
  const canvasRef = useRef(null);
  const imgRef = useRef(null);

  // When visibilityLevel is set (walkthrough mode), derive flags from level
  // When null (visualizer mode), use individual toggle props
  const effectiveShowZones = visibilityLevel != null ? visibilityLevel >= 5 : (showZones !== false);
  const effectiveShowPersons = visibilityLevel != null ? visibilityLevel >= 2 : (showPersons !== false);
  const effectiveShowRoleLabels = visibilityLevel != null ? visibilityLevel >= 5 : (showRolesProp !== false);
  const effectiveShowEvents = visibilityLevel != null ? visibilityLevel >= 6 : (showEventsProp !== false);

  useEffect(() => {
    if (!imageSrc) return;

    const img = new window.Image();
    img.onload = () => {
      imgRef.current = img;
      draw();
    };
    img.src = imageSrc.startsWith("data:")
      ? imageSrc
      : `data:image/jpeg;base64,${imageSrc}`;
  }, [imageSrc, analysis, zones, counter, effectiveShowZones, effectiveShowPersons, effectiveShowRoleLabels, effectiveShowEvents, visibilityLevel, events]);

  function draw() {
    const canvas = canvasRef.current;
    if (!canvas || !imgRef.current) return;

    const ctx = canvas.getContext("2d");
    const img = imgRef.current;

    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;

    // Draw frame
    ctx.drawImage(img, 0, 0);

    const w = canvas.width;
    const h = canvas.height;

    // Draw zone overlays
    if (effectiveShowZones && zones) {
      for (const [name, polygon] of Object.entries(zones)) {
        if (name === "counter") continue; // counter drawn separately
        ctx.beginPath();
        polygon.forEach(([x, y], i) => {
          const px = x * w;
          const py = y * h;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
        ctx.fillStyle = ZONE_COLORS[name] || "rgba(128,128,128,0.2)";
        ctx.fill();
        ctx.strokeStyle = ZONE_BORDERS[name] || "rgba(128,128,128,0.6)";
        ctx.lineWidth = 3;
        ctx.setLineDash([8, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Zone label with background pill for readability
        const cx = polygon.reduce((s, p) => s + p[0], 0) / polygon.length * w;
        const cy = polygon.reduce((s, p) => s + p[1], 0) / polygon.length * h;
        const zoneText = name.toUpperCase() + " ZONE";
        ctx.font = "bold 16px monospace";
        const ztm = ctx.measureText(zoneText);
        const pillW = ztm.width + 16;
        const pillH = 24;
        ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
        ctx.fillRect(cx - pillW / 2, cy - pillH / 2 - 4, pillW, pillH);
        ctx.fillStyle = ZONE_BORDERS[name] || "#aaa";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(zoneText, cx, cy - 4);
        ctx.textBaseline = "alphabetic";
      }

      // Draw counter outline separately (subtle, no large center label)
      if (counter && counter.length >= 3) {
        ctx.beginPath();
        counter.forEach(([x, y], i) => {
          const px = x * w;
          const py = y * h;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
        ctx.fillStyle = ZONE_COLORS.counter;
        ctx.fill();
        ctx.strokeStyle = ZONE_BORDERS.counter;
        ctx.lineWidth = 3;
        ctx.setLineDash([8, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Counter label with background pill
        const maxX = Math.max(...counter.map((p) => p[0])) * w;
        const maxY = Math.max(...counter.map((p) => p[1])) * h;
        const cText = "COUNTER";
        ctx.font = "bold 13px monospace";
        const ctm = ctx.measureText(cText);
        ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
        ctx.fillRect(maxX - ctm.width - 18, maxY - 24, ctm.width + 12, 20);
        ctx.fillStyle = ZONE_BORDERS.counter;
        ctx.textAlign = "right";
        ctx.fillText(cText, maxX - 10, maxY - 10);
      }
    }

    // Draw person outlines and labels
    if (effectiveShowPersons && analysis && analysis.people) {
      for (const person of analysis.people) {
        const bb = person.bounding_box;
        const mask = person.mask;
        const hasMask = mask && mask.length >= 3;

        // Color: role-colored when Roles is on, uniform green otherwise
        const color = effectiveShowRoleLabels
          ? (ROLE_COLORS[person.role] || ROLE_COLORS.other)
          : "#78D64B";

        if (hasMask) {
          // Draw segmentation mask outline
          ctx.beginPath();
          mask.forEach(([x, y], i) => {
            const px = x * w;
            const py = y * h;
            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
          });
          ctx.closePath();

          // Semi-transparent fill inside silhouette
          ctx.fillStyle = hexToRgba(color, 0.15);
          ctx.fill();

          // Outline stroke
          ctx.strokeStyle = color;
          ctx.lineWidth = 2.5;
          ctx.stroke();
        } else {
          // No mask: draw bounding box
          const x = bb.x_min * w;
          const y = bb.y_min * h;
          const bw = (bb.x_max - bb.x_min) * w;
          const bh = (bb.y_max - bb.y_min) * h;

          ctx.strokeStyle = color;
          ctx.lineWidth = 2.5;
          ctx.strokeRect(x, y, bw, bh);
          ctx.fillStyle = hexToRgba(color, 0.08);
          ctx.fillRect(x, y, bw, bh);
        }

        // Label: always show track ID; append role when Roles toggle is on
        let label = `#${person.track_id}`;
        if (effectiveShowRoleLabels) {
          const roleLabel = person.role.replace(/_/g, " ");
          label += ` ${roleLabel}${person.queue_position ? " Q" + person.queue_position : ""} ${Math.round(person.confidence * 100)}%`;
        }

        if (label) {
          const x = bb.x_min * w;
          const y = bb.y_min * h;
          const bh = (bb.y_max - bb.y_min) * h;

          ctx.font = "bold 13px monospace";
          const tm = ctx.measureText(label);
          const labelH = 20;
          const labelY = y > labelH + 4 ? y - labelH - 2 : y + bh + 2;

          ctx.fillStyle = color;
          ctx.fillRect(x - 1, labelY, tm.width + 10, labelH);

          ctx.fillStyle = "#fff";
          ctx.textAlign = "left";
          ctx.textBaseline = "top";
          ctx.fillText(label, x + 4, labelY + 3);
        }

        // Centroid dot
        const cx = (bb.x_min + bb.x_max) / 2 * w;
        const cy = (bb.y_min + bb.y_max) / 2 * h;
        ctx.beginPath();
        ctx.arc(cx, cy, 4, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }

    // Draw event annotations (independent of person toggles)
    if (effectiveShowEvents) {
      const evtList = events || (analysis && analysis.events) || [];
      if (evtList.length > 0) {
        const eventY = 30;
        ctx.font = "bold 14px monospace";
        ctx.textAlign = "left";
        ctx.textBaseline = "top";

        evtList.forEach((evt, idx) => {
          const yPos = eventY + idx * 26;
          const evtColor = EVENT_COLORS[evt.event_type] || "#FFC658";
          const text = evt.track_id === 0
            ? `  ${evt.event_type}`
            : `  #${evt.track_id} ${evt.event_type}`;

          // Background pill
          const tm = ctx.measureText(text);
          ctx.fillStyle = "rgba(0, 0, 0, 0.7)";
          ctx.fillRect(8, yPos - 2, tm.width + 16, 22);
          ctx.fillStyle = evtColor;
          ctx.fillRect(8, yPos - 2, 4, 22);

          ctx.fillStyle = evtColor;
          ctx.fillText(text, 16, yPos);
        });
      }
    }
  }

  return (
    <canvas
      ref={canvasRef}
      style={{
        width: "100%",
        maxWidth: width,
        height: "auto",
        borderRadius: 8,
        border: "1px solid #333",
        background: "#111",
      }}
    />
  );
}

/** Convert hex color to rgba string */
function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

