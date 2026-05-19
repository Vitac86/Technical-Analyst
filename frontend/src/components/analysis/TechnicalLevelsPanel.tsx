import type { LevelKind, TechnicalLevel, TechnicalLevelsResponse } from "../../types/levels";

// ---------------------------------------------------------------------------
// Metadata maps
// ---------------------------------------------------------------------------

const KIND_LABEL: Record<LevelKind, string> = {
  support: "Support",
  resistance: "Resistance",
  target_up: "Target ↑",
  target_down: "Target ↓",
  stop_zone: "Stop Zone",
  info: "Info",
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function LevelBadge({ kind }: { kind: LevelKind }) {
  return (
    <span className={`lv-badge lv-badge-${kind.replace("_", "-")}`}>
      {KIND_LABEL[kind]}
    </span>
  );
}

function LevelRow({ level }: { level: TechnicalLevel }) {
  const distSign =
    level.distance_percent !== null && level.distance_percent > 0 ? "+" : "";
  const distClass =
    level.distance_percent === null
      ? ""
      : level.distance_percent >= 0
        ? "lv-dist-pos"
        : "lv-dist-neg";

  return (
    <div className="lv-row">
      <div className="lv-row-top">
        <div className="lv-row-left">
          <LevelBadge kind={level.kind} />
          <span className="lv-label">{level.label}</span>
        </div>
        <div className="lv-row-right">
          {level.price !== null ? (
            <span className="lv-price">{level.price.toFixed(2)}</span>
          ) : null}
          {level.distance_percent !== null ? (
            <span className={`lv-dist ${distClass}`}>
              {distSign}{level.distance_percent.toFixed(2)}%
            </span>
          ) : null}
        </div>
      </div>
      <p className="lv-reason">{level.reason}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

type Props = {
  data: TechnicalLevelsResponse | null;
  loading: boolean;
  error: string | null;
};

export function TechnicalLevelsPanel({ data, loading, error }: Props) {
  const empty = !loading && !error && data === null;
  const noLevels =
    !loading && !error && data !== null && data.levels.length === 0;

  return (
    <section className="panel lv-panel">
      <div className="panel-header">
        <h2>Levels &amp; Targets</h2>
        {data ? (
          <span className="panel-meta">
            {new Date(data.generated_at).toLocaleTimeString("ru-RU")}
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="chart-state">Calculating levels…</div>
      ) : null}

      {error ? (
        <div className="chart-state chart-state-error">{error}</div>
      ) : null}

      {empty ? (
        <div className="chart-state">
          Load instrument data to see Levels &amp; Targets.
        </div>
      ) : null}

      {noLevels ? (
        <div className="chart-state">
          {data?.message ?? "No level data available. Load candles first."}
        </div>
      ) : null}

      {data && !loading && !error && data.levels.length > 0 ? (
        <>
          {/* Summary row */}
          <div className="lv-summary-row">
            <div className="lv-meta-grid">
              {data.last_close !== null ? (
                <div className="lv-meta-item">
                  <span className="lv-meta-label">Last Close</span>
                  <span className="lv-meta-value">{data.last_close.toFixed(2)}</span>
                </div>
              ) : null}
              {data.atr !== null ? (
                <div className="lv-meta-item">
                  <span className="lv-meta-label">ATR 14</span>
                  <span className="lv-meta-value">{data.atr.toFixed(4)}</span>
                </div>
              ) : null}
              {data.atr_percent !== null ? (
                <div className="lv-meta-item">
                  <span className="lv-meta-label">ATR %</span>
                  <span className="lv-meta-value">{data.atr_percent.toFixed(2)}%</span>
                </div>
              ) : null}
              <div className="lv-meta-item">
                <span className="lv-meta-label">Lookback</span>
                <span className="lv-meta-value">{data.lookback} candles</span>
              </div>
            </div>
            <p className="lv-summary">{data.summary}</p>
          </div>

          {/* Level rows */}
          <div className="lv-list">
            {data.levels.map((level, i) => (
              <LevelRow key={`${level.kind}-${i}`} level={level} />
            ))}
          </div>

          {data.message ? (
            <p className="lv-notice">{data.message}</p>
          ) : null}

          <p className="lv-disclaimer">
            Levels &amp; Targets are estimated from recent candles and ATR. These
            are technical research levels, not financial advice or guaranteed
            price predictions.
          </p>
        </>
      ) : null}
    </section>
  );
}
