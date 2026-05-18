const indicatorGroups = [
  "SMA",
  "EMA",
  "RSI",
  "MACD",
  "Bollinger Bands",
  "ATR",
  "ADX",
  "OBV",
  "Stochastic",
];

export function IndicatorPanel() {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Indicators</h2>
        <span className="panel-meta">Registry</span>
      </div>
      <div className="indicator-grid">
        {indicatorGroups.map((name) => (
          <span className="indicator-chip" key={name}>
            {name}
          </span>
        ))}
      </div>
    </section>
  );
}
