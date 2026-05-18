const placeholderBars = [42, 58, 51, 64, 72, 68, 77, 83, 75, 88, 91, 86];

export function PriceChart() {
  return (
    <section className="panel chart-panel" aria-label="Price chart">
      <div className="panel-header">
        <h2>Price</h2>
        <span className="panel-meta">Daily</span>
      </div>
      <div className="chart-placeholder" role="img" aria-label="Placeholder price chart">
        {placeholderBars.map((height, index) => (
          <span
            className="chart-bar"
            key={index}
            style={{ height: `${height}%` }}
          />
        ))}
      </div>
    </section>
  );
}
