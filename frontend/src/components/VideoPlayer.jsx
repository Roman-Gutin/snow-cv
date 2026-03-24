import { useState, useEffect, useRef } from "react";

const SPEED_OPTIONS = [0.25, 0.5, 1, 2, 4];

export default function VideoPlayer({ frames, currentIdx, onFrameChange }) {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (playing && frames && frames.length > 0) {
      intervalRef.current = setInterval(() => {
        onFrameChange((prev) => {
          if (prev >= frames.length - 1) {
            setPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 500 / speed);
    }
    return () => clearInterval(intervalRef.current);
  }, [playing, frames, speed]);

  if (!frames || frames.length === 0) return null;

  const frame = frames[currentIdx];

  return (
    <div className="video-player">
      <div className="player-controls">
        <button
          className="play-btn"
          onClick={() => {
            if (currentIdx >= frames.length - 1) {
              onFrameChange(0);
            }
            setPlaying(!playing);
          }}
        >
          {playing ? "⏸ Pause" : "▶ Play"}
        </button>

        <button
          className="step-btn"
          onClick={() => onFrameChange(Math.max(0, currentIdx - 1))}
          disabled={currentIdx === 0}
        >
          ◀ Prev
        </button>

        <button
          className="step-btn"
          onClick={() => onFrameChange(Math.min(frames.length - 1, currentIdx + 1))}
          disabled={currentIdx >= frames.length - 1}
        >
          Next ▶
        </button>

        <span className="frame-counter">
          Frame {currentIdx + 1} / {frames.length}
          {frame && ` (${frame.timestamp}s)`}
        </span>

        <div className="speed-controls">
          {SPEED_OPTIONS.map((s) => (
            <button
              key={s}
              className={`speed-btn${speed === s ? " active" : ""}`}
              onClick={() => setSpeed(s)}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>

      <input
        type="range"
        className="frame-slider"
        min={0}
        max={frames.length - 1}
        value={currentIdx}
        onChange={(e) => {
          setPlaying(false);
          onFrameChange(Number(e.target.value));
        }}
      />

      <div className="timeline-ticks">
        {frames.map((f, i) => {
          const ql = f.analysis?.queue_metrics?.queue_length || 0;
          const maxQ = Math.max(1, ...frames.map(fr => fr.analysis?.queue_metrics?.queue_length || 0));
          const heightPct = (ql / maxQ) * 100;
          return (
            <div
              key={i}
              className={`timeline-bar ${i === currentIdx ? "active" : ""}`}
              style={{ height: `${Math.max(4, heightPct)}%` }}
              title={`Frame ${i + 1}: queue=${ql}, t=${f.timestamp}s`}
              onClick={() => { setPlaying(false); onFrameChange(i); }}
            />
          );
        })}
      </div>
      <div className="timeline-label">Queue length over time (click to jump)</div>
    </div>
  );
}
