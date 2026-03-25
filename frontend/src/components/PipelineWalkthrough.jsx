import { useState, useCallback, useEffect } from "react";
import FrameCanvas from "./FrameCanvas";

const API = "http://localhost:5001/api";

export default function PipelineWalkthrough() {
  const [frames, setFrames] = useState(null);
  const [frameIdx, setFrameIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [videos, setVideos] = useState([]);
  const [activeVideo, setActiveVideo] = useState(null);
  const [zones, setZones] = useState(null);
  const [counter, setCounter] = useState(null);
  const [playing, setPlaying] = useState(false);

  const loadVideo = useCallback(async (filename) => {
    setLoading(true);
    setError(null);
    setFrameIdx(0);
    setFrames(null);
    setActiveVideo(filename);
    setPlaying(false);
    try {
      // Load zones for this video
      const zoneRes = await fetch(`${API}/auto-zones`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: filename }),
      });
      const zoneData = await zoneRes.json();
      if (zoneData.zones && Object.keys(zoneData.zones).length > 0) {
        setZones(zoneData.zones);
        setCounter(zoneData.counter || null);
      }

      // Run inference on all frames
      const res = await fetch(`${API}/walkthrough?conf=0.25&max_frames=60`, {
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

  // Fetch available videos and auto-load the first one
  useEffect(() => {
    fetch(`${API}/sample-files`)
      .then((r) => r.json())
      .then((data) => {
        const vids = data.videos || [];
        setVideos(vids);
        if (vids.length > 0) loadVideo(vids[0]);
      })
      .catch((e) => setError("Cannot connect to backend on port 5001."));
  }, [loadVideo]);

  // Auto-play: advance frame every 500ms
  useEffect(() => {
    if (!playing || !frames) return;
    const timer = setInterval(() => {
      setFrameIdx((i) => {
        if (i >= frames.length - 1) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, 500);
    return () => clearInterval(timer);
  }, [playing, frames]);

  const currentFrame = frames ? frames[frameIdx] : null;

  // Collect all events up to current frame
  const cumulativeEvents = frames
    ? frames.slice(0, frameIdx + 1).flatMap((f) => f.events || [])
    : [];

  return (
    <div className="walkthrough">
      {error && <div className="error-banner">{error}</div>}
      {loading && (
        <div className="loading">
          Running YOLO inference on all frames... this may take a minute.
        </div>
      )}

      {frames && (
        <>
          {/* Video selector */}
          {videos.length > 1 && (
            <div className="wt-top-panel">
              <div className="btn-group" style={{ justifyContent: "center" }}>
                {videos.map((v) => (
                  <button
                    key={v}
                    className={`sample-btn video-btn${activeVideo === v ? " active" : ""}`}
                    onClick={() => loadVideo(v)}
                    disabled={loading}
                    style={
                      activeVideo === v
                        ? { background: "var(--tru-green-muted)", borderColor: "var(--tru-green)" }
                        : {}
                    }
                  >
                    {v.replace(".mp4", "").replace(/_/g, " ")}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Canvas — show everything: zones, people, roles, events */}
          <div className="wt-canvas-area">
            {currentFrame && (
              <FrameCanvas
                imageSrc={currentFrame.image}
                analysis={currentFrame.analysis}
                zones={zones}
                counter={counter}
                showZones={true}
                showPersons={true}
                showRoles={true}
                showEvents={true}
                events={currentFrame.events}
              />
            )}
          </div>

          {/* Controls: play/pause + scrubber */}
          <div className="wt-controls-row">
            <div className="wt-frame-scrubber">
              <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
                <button
                  className="wt-nav-btn"
                  onClick={() => setPlaying(!playing)}
                  style={{ minWidth: 36, fontSize: 18 }}
                >
                  {playing ? "\u23F8" : "\u25B6"}
                </button>
                <label className="wt-scrub-label">
                  Frame {frameIdx + 1}/{frames.length}
                  {currentFrame && ` \u2014 ${currentFrame.timestamp}s`}
                </label>
              </div>
              <input
                type="range"
                min={0}
                max={frames.length - 1}
                value={frameIdx}
                onChange={(e) => {
                  setFrameIdx(Number(e.target.value));
                  setPlaying(false);
                }}
                className="wt-scrub-slider"
              />
            </div>

            {/* Event log */}
            {cumulativeEvents.length > 0 && (
              <div className="wt-events-summary">
                <h4>Events ({cumulativeEvents.length})</h4>
                <div className="wt-event-list">
                  {cumulativeEvents.map((evt, i) => (
                    <span key={i} className="wt-event-chip" data-type={evt.event_type}>
                      {evt.track_id !== 0 && `#${evt.track_id} `}
                      {evt.event_type.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
