const ROLE_COLORS = {
  employee: "#5C88DA",
  customer_being_served: "#78D64B",
  in_queue: "#FFC658",
  entering: "#A77BCA",
  exiting: "#FFA38B",
  at_entrance: "#A77BCA",
  other: "#CED9E5",
};

export default function MetricsPanel({ analysis, frameInfo }) {
  if (!analysis) {
    return (
      <div className="metrics-panel">
        <h3>Metrics</h3>
        <p className="muted">Load an image or video to see metrics</p>
      </div>
    );
  }

  const { people, queue_metrics, service_point } = analysis;
  const traffic = analysis.cumulative_traffic || analysis.traffic_metrics || {};

  return (
    <div className="metrics-panel">
      <h3>Frame Metrics</h3>

      {frameInfo && (
        <div className="metric-row">
          <span className="metric-label">Frame</span>
          <span className="metric-value">{frameInfo}</span>
        </div>
      )}

      <div className="metric-row">
        <span className="metric-label">People Detected</span>
        <span className="metric-value big">{people.length}</span>
      </div>

      <div className="metric-row">
        <span className="metric-label">Queue Length</span>
        <span className="metric-value big" style={{ color: "#FFC658" }}>
          {queue_metrics.queue_length}
        </span>
      </div>

      <div className="metric-row">
        <span className="metric-label">Service Active</span>
        <span className={`metric-value ${service_point.active ? "active" : "inactive"}`}>
          {service_point.active ? "YES" : "NO"}
        </span>
      </div>

      <div className="metric-row">
        <span className="metric-label">Employee Present</span>
        <span className={`metric-value ${service_point.employee_present ? "active" : "inactive"}`}>
          {service_point.employee_present ? "YES" : "NO"}
        </span>
      </div>

      {(traffic.entered != null || traffic.exited != null) && (
        <>
          <h4>Traffic</h4>
          <div className="metric-row">
            <span className="metric-label">Entered</span>
            <span className="metric-value" style={{ color: "#A77BCA" }}>
              {traffic.entered || 0}
            </span>
          </div>
          <div className="metric-row">
            <span className="metric-label">Exited</span>
            <span className="metric-value" style={{ color: "#FFA38B" }}>
              {traffic.exited || 0}
            </span>
          </div>
          <div className="metric-row">
            <span className="metric-label">Left Unserviced</span>
            <span className="metric-value" style={{ color: traffic.left_unserviced > 0 ? "#EE2737" : "#666" }}>
              {traffic.left_unserviced || 0}
            </span>
          </div>
        </>
      )}

      <h4>People</h4>
      <div className="people-list">
        {people.map((p) => (
          <div key={p.track_id} className="person-card" style={{ borderLeftColor: ROLE_COLORS[p.role] || "#888" }}>
            <div className="person-header">
              <span className="track-id">#{p.track_id}</span>
              <span className="role-badge" style={{ background: ROLE_COLORS[p.role] || "#888" }}>
                {p.role.replace(/_/g, " ")}
              </span>
            </div>
            <div className="person-details">
              <span>Confidence: {Math.round(p.confidence * 100)}%</span>
              {p.queue_position && <span> | Queue pos: {p.queue_position}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
