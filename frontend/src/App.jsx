import { useState, useEffect, useCallback } from "react";
import FrameCanvas from "./components/FrameCanvas";
import MetricsPanel from "./components/MetricsPanel";
import VideoPlayer from "./components/VideoPlayer";
import PipelineWalkthrough from "./components/PipelineWalkthrough";
import "./App.css";

const API = "/api";

function App() {
  const [sampleFiles, setSampleFiles] = useState({ images: [], videos: [] });
  const [zones, setZones] = useState(null);
  const [counter, setCounter] = useState(null);
  const [showZones, setShowZones] = useState(true);
  const [showPersons, setShowPersons] = useState(true);
  const [showRoles, setShowRoles] = useState(true);
  const [showEvents, setShowEvents] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [confThreshold, setConfThreshold] = useState(0.3);

  // Single image mode
  const [imageData, setImageData] = useState(null);
  const [imageAnalysis, setImageAnalysis] = useState(null);

  // Video mode
  const [videoFrames, setVideoFrames] = useState(null);
  const [currentFrameIdx, setCurrentFrameIdx] = useState(0);

  const [mode, setMode] = useState(null); // "image" | "video"
  const [tab, setTab] = useState("walkthrough"); // "visualizer" | "walkthrough"
  const [activeFile, setActiveFile] = useState(null); // currently loaded filename
  const [zoneDetection, setZoneDetection] = useState(null); // result from auto-detect
  const [detectingZones, setDetectingZones] = useState(false);

  useEffect(() => {
    fetch(`${API}/sample-files`)
      .then((r) => r.json())
      .then(setSampleFiles)
      .catch(() => setError("Cannot connect to backend. Start server.py on port 5001."));

    fetch(`${API}/zones`)
      .then((r) => r.json())
      .then(setZones)
      .catch(() => {});

    fetch(`${API}/counter`)
      .then((r) => r.json())
      .then((data) => setCounter(data.counter))
      .catch(() => {});
  }, []);

  const analyzeImage = useCallback(async (filename) => {
    setLoading(true);
    setError(null);
    setMode("image");
    setVideoFrames(null);
    setActiveFile(filename);
    try {
      const res = await fetch(`${API}/analyze-image?conf=${confThreshold}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: filename }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setImageData(data.image);
      setImageAnalysis(data.analysis);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [confThreshold]);

  const analyzeVideo = useCallback(async (filename) => {
    setLoading(true);
    setError(null);
    setMode("video");
    setImageData(null);
    setImageAnalysis(null);
    setCurrentFrameIdx(0);
    setActiveFile(filename);
    try {
      const res = await fetch(`${API}/analyze-video?conf=${confThreshold}&fps=2`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: filename }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setVideoFrames(data.frames);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [confThreshold]);

  const handleUpload = useCallback(async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const isVideo = file.type.startsWith("video/");
    setLoading(true);
    setError(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const endpoint = isVideo ? "analyze-video" : "analyze-image";
      const res = await fetch(`${API}/${endpoint}?conf=${confThreshold}&fps=2`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      if (isVideo) {
        setMode("video");
        setImageData(null);
        setImageAnalysis(null);
        setVideoFrames(data.frames);
        setCurrentFrameIdx(0);
      } else {
        setMode("image");
        setVideoFrames(null);
        setImageData(data.image);
        setImageAnalysis(data.analysis);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [confThreshold]);

  const autoDetectZones = useCallback(async () => {
    if (!activeFile) return;
    setDetectingZones(true);
    setZoneDetection(null);
    try {
      const res = await fetch(`${API}/auto-zones`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: activeFile }),
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setZones(data.zones);
      if (data.counter) setCounter(data.counter);
      setZoneDetection({ detected: data.detected, fallback: data.fallback });
    } catch (e) {
      setError(`Zone detection failed: ${e.message}`);
    } finally {
      setDetectingZones(false);
    }
  }, [activeFile]);

  // Current frame data for display
  const currentFrame = videoFrames ? videoFrames[currentFrameIdx] : null;
  const displayImage = mode === "video" ? currentFrame?.image : imageData;
  const displayAnalysis = mode === "video" ? currentFrame?.analysis : imageAnalysis;
  const frameInfo = mode === "video" && currentFrame
    ? `${currentFrameIdx + 1}/${videoFrames.length} @ ${currentFrame.timestamp}s`
    : null;

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-top">
          <div className="header-brand">
            <div className="header-text">
              <h1>Customer Queue Analytics</h1>
              <p className="subtitle">
                {tab === "visualizer"
                  ? "Track wait times, rage quits, and service gaps in real time"
                  : "Step through the CV pipeline one layer at a time"}
              </p>
            </div>
          </div>
          <div className="tab-toggle">
            <button
              className={`tab-btn${tab === "visualizer" ? " active" : ""}`}
              onClick={() => setTab("visualizer")}
            >
              Visualizer
            </button>
            <button
              className={`tab-btn${tab === "walkthrough" ? " active" : ""}`}
              onClick={() => setTab("walkthrough")}
            >
              Pipeline Walkthrough
            </button>
          </div>
        </div>
      </header>

      {tab === "visualizer" && (
        <>
          <div className="toolbar">
            <div className="toolbar-section">
              <label className="toolbar-label">Sample Images</label>
              <div className="btn-group">
                {sampleFiles.images.map((f) => (
                  <button key={f} onClick={() => analyzeImage(f)} disabled={loading} className="sample-btn">
                    {f.replace("frame_bright_", "Frame ").replace(".png", "").replace("frame_", "Frame ")}
                  </button>
                ))}
              </div>
            </div>

            <div className="toolbar-section">
              <label className="toolbar-label">Videos</label>
              <div className="btn-group">
                {sampleFiles.videos.map((f) => (
                  <button key={f} onClick={() => analyzeVideo(f)} disabled={loading} className="sample-btn video-btn">
                    {f.replace(".mp4", "").replace(/_/g, " ")}
                  </button>
                ))}
              </div>
            </div>

            <div className="toolbar-section">
              <label className="toolbar-label">Upload</label>
              <input type="file" accept="image/*,video/*" onChange={handleUpload} disabled={loading} />
            </div>

            <div className="toolbar-section">
              <label className="toolbar-label">Confidence</label>
              <label className="conf-label">
                {confThreshold}
                <input
                  type="range" min="0.1" max="0.9" step="0.05"
                  value={confThreshold}
                  onChange={(e) => setConfThreshold(Number(e.target.value))}
                />
              </label>
            </div>

            <div className="toolbar-section">
              <label className="toolbar-label">Zones</label>
              <div className="btn-group">
                <button
                  className="sample-btn auto-zone-btn"
                  onClick={autoDetectZones}
                  disabled={!activeFile || detectingZones || loading}
                  title={!activeFile ? "Load a video or image first" : "Load zone config for this video"}
                >
                  {detectingZones ? "Detecting..." : "Auto-Detect Zones"}
                </button>
              </div>
            </div>

            <div className="toolbar-section">
              <label className="toolbar-label">CV Layers</label>
              <div className="layer-toggles">
                {[
                  { key: "zones", label: "Zones", color: "#5C88DA", state: showZones, set: setShowZones },
                  { key: "persons", label: "Persons", color: "#78D64B", state: showPersons, set: setShowPersons },
                  { key: "roles", label: "Roles", color: "#A77BCA", state: showRoles, set: setShowRoles },
                  { key: "events", label: "Events", color: "#EE2737", state: showEvents, set: setShowEvents },
                ].map(({ key, label, color, state, set }) => (
                  <button
                    key={key}
                    className={`layer-toggle${state ? " on" : ""}`}
                    onClick={() => set(!state)}
                    onDoubleClick={() => {
                      setShowZones(key === "zones");
                      setShowPersons(key === "persons");
                      setShowRoles(key === "roles");
                      setShowEvents(key === "events");
                    }}
                  >
                    <span className="lt-dot" style={{ background: color }} />{label}
                  </button>
                ))}
              </div>
            </div>
            {zoneDetection && (
              <div className="zone-detection-status">
                {zoneDetection.detected.length > 0 && (
                  <span className="zone-detected">
                    Detected: {zoneDetection.detected.join(", ")}
                  </span>
                )}
                {zoneDetection.fallback && zoneDetection.fallback.length > 0 && (
                  <span className="zone-fallback">
                    Fallback: {zoneDetection.fallback.join(", ")}
                  </span>
                )}
              </div>
            )}
          </div>

          {error && <div className="error-banner">{error}</div>}
          {loading && <div className="loading">Analyzing... (this may take a moment for videos)</div>}

          <div className="main-content">
            <div className="canvas-area">
              {displayImage ? (
                <FrameCanvas
                  imageSrc={displayImage}
                  analysis={displayAnalysis}
                  zones={zones}
                  counter={counter}
                  showZones={showZones}
                  showPersons={showPersons}
                  showRoles={showRoles}
                  showEvents={showEvents}
                  events={displayAnalysis?.events}
                />
              ) : (
                <div className="empty-canvas">
                  <p>Select a sample file or upload an image/video to begin</p>
                </div>
              )}

              {mode === "video" && videoFrames && (
                <VideoPlayer
                  frames={videoFrames}
                  currentIdx={currentFrameIdx}
                  onFrameChange={setCurrentFrameIdx}
                />
              )}
            </div>

            <div className="sidebar">
              <MetricsPanel analysis={displayAnalysis} frameInfo={frameInfo} />
            </div>
          </div>
        </>
      )}

      {tab === "walkthrough" && (
        <PipelineWalkthrough />
      )}
    </div>
  );
}

export default App;
