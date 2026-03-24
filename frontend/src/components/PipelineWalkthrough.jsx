import { useState, useCallback, useEffect } from "react";
import FrameCanvas from "./FrameCanvas";
import StepDescription from "./StepDescription";

const API = "http://localhost:5001/api";

const STEP_NAMES = [
  "Raw Frame",
  "Detection",
  "Segmentation",
  "Tracking",
  "Zone Classification",
  "Event Detection",
];

export default function PipelineWalkthrough({ zones, counter }) {
  const [step, setStep] = useState(1); // 1-6
  const [frames, setFrames] = useState(null);
  const [frameIdx, setFrameIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadVideo = useCallback(async (filename) => {
    setLoading(true);
    setError(null);
    setFrameIdx(0);
    try {
      const res = await fetch(`${API}/walkthrough?conf=0.3&max_frames=30`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: filename }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setFrames(data.frames);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Auto-load the synthetic retail video on mount
  useEffect(() => {
    loadVideo("synthetic_retail_queue.mp4");
  }, [loadVideo]);

  const currentFrame = frames ? frames[frameIdx] : null;

  const prevStep = () => setStep((s) => Math.max(1, s - 1));
  const nextStep = () => setStep((s) => Math.min(6, s + 1));

  return (
    <div className="walkthrough">
      {error && <div className="error-banner">{error}</div>}
      {loading && <div className="loading">Running YOLO inference on all frames... this may take a minute.</div>}

      {frames && (
        <>
          {/* Step nav + description on top */}
          <div className="wt-top-panel">
            <div className="wt-step-nav">
              <button onClick={prevStep} disabled={step <= 1} className="wt-nav-btn">
                &#8592;
              </button>
              <span className="wt-step-label">
                <span className="wt-step-num">{step}</span>/6 — {STEP_NAMES[step - 1]}
              </span>
              <button onClick={nextStep} disabled={step >= 6} className="wt-nav-btn">
                &#8594;
              </button>
            </div>
            <StepDescription level={step} />
          </div>

          {/* Canvas + scrubber below */}
          <div className="wt-canvas-area">
            {currentFrame && (
              <FrameCanvas
                imageSrc={currentFrame.image}
                analysis={currentFrame.analysis}
                zones={zones}
                counter={counter}
                showZones={false}
                visibilityLevel={step}
                events={currentFrame.events}
              />
            )}
          </div>

          <div className="wt-controls-row">
            {/* Frame scrubber */}
            <div className="wt-frame-scrubber">
              <label className="wt-scrub-label">
                Frame {frameIdx + 1}/{frames.length}
                {currentFrame && ` — ${currentFrame.timestamp}s`}
              </label>
              <input
                type="range"
                min={0}
                max={frames.length - 1}
                value={frameIdx}
                onChange={(e) => setFrameIdx(Number(e.target.value))}
                className="wt-scrub-slider"
              />
            </div>

            {/* Cumulative events at step 6 */}
            {step >= 6 && currentFrame && currentFrame.cumulative_events && (
              <div className="wt-events-summary">
                <h4>Events</h4>
                <div className="wt-event-list">
                  {currentFrame.cumulative_events.map((evt, i) => (
                    <span key={i} className="wt-event-chip" data-type={evt.event_type}>
                      {evt.track_id !== 0 && `#${evt.track_id} `}{evt.event_type.replace(/_/g, " ")}
                    </span>
                  ))}
                  {currentFrame.cumulative_events.length === 0 && (
                    <span className="muted">No events detected yet</span>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
