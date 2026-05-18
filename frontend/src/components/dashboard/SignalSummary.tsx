const placeholderSignals = [
  { label: "Trend", value: "Pending" },
  { label: "Momentum", value: "Pending" },
  { label: "Targets", value: "Pending" },
];

export function SignalSummary() {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Signals</h2>
        <span className="panel-meta">Research</span>
      </div>
      <div className="signal-stack">
        {placeholderSignals.map((signal) => (
          <div className="signal-row" key={signal.label}>
            <span>{signal.label}</span>
            <strong>{signal.value}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}
