import type {
  AggregateSignal,
  SignalDirection,
  TechnicalSignalItem,
  TechnicalSignalResponse,
} from "../../types/analysis";

// ---------------------------------------------------------------------------
// Signal metadata
// ---------------------------------------------------------------------------

const SIGNAL_LABEL: Record<SignalDirection, string> = {
  buy: "Buy",
  sell: "Sell",
  neutral: "Neutral",
  caution: "Caution",
  info: "Info",
};

const AGGREGATE_LABEL: Record<AggregateSignal, string> = {
  strong_buy: "Strong Buy",
  buy: "Buy",
  neutral: "Neutral",
  sell: "Sell",
  strong_sell: "Strong Sell",
  caution: "Caution",
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SignalBadge({ signal }: { signal: SignalDirection }) {
  return (
    <span className={`ts-badge ts-badge-${signal}`}>
      {SIGNAL_LABEL[signal]}
    </span>
  );
}

function AggregateBadge({ signal }: { signal: AggregateSignal }) {
  const directionClass =
    signal === "strong_buy" || signal === "buy"
      ? "ts-badge-buy"
      : signal === "strong_sell" || signal === "sell"
        ? "ts-badge-sell"
        : signal === "caution"
          ? "ts-badge-caution"
          : "ts-badge-neutral";

  return (
    <span className={`ts-badge ts-badge-lg ${directionClass}`}>
      {AGGREGATE_LABEL[signal]}
    </span>
  );
}

function SignalRow({ item }: { item: TechnicalSignalItem }) {
  const valueDisplay = formatValue(item.value);

  return (
    <div className="ts-signal-row">
      <div className="ts-signal-left">
        <span className="ts-signal-label">{item.label}</span>
        {valueDisplay ? (
          <span className="ts-signal-value">{valueDisplay}</span>
        ) : null}
      </div>
      <div className="ts-signal-right">
        <SignalBadge signal={item.signal} />
        <span className={`ts-strength ts-strength-${item.strength}`}>
          {item.strength}
        </span>
      </div>
      <p className="ts-signal-reason">{item.reason}</p>
    </div>
  );
}

function formatValue(value: TechnicalSignalItem["value"]): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toFixed(2);
  if (typeof value === "string") return value;
  // dict — show key/value pairs for numeric fields of interest
  const parts: string[] = [];
  for (const [k, v] of Object.entries(value)) {
    if (typeof v === "number") {
      parts.push(`${k}: ${v.toFixed(4)}`);
    }
  }
  return parts.slice(0, 3).join(" · ");
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

type Props = {
  data: TechnicalSignalResponse | null;
  loading: boolean;
  error: string | null;
};

export function TechnicalSignalsPanel({ data, loading, error }: Props) {
  const empty = !loading && !error && data === null;

  return (
    <section className="panel ts-panel">
      <div className="panel-header">
        <h2>Technical Research Signals</h2>
        {data ? (
          <span className="panel-meta">
            {new Date(data.aggregate.generated_at).toLocaleTimeString("ru-RU")}
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="chart-state">Generating signals…</div>
      ) : null}

      {error ? (
        <div className="chart-state chart-state-error">{error}</div>
      ) : null}

      {empty ? (
        <div className="chart-state">
          Load instrument data to see Technical Research Signals.
        </div>
      ) : null}

      {data && !loading && !error ? (
        <>
          {/* Aggregate header */}
          <div className="ts-aggregate">
            <AggregateBadge signal={data.aggregate.signal} />
            <div className="ts-aggregate-stats">
              <span className="ts-stat">
                Score: <strong>{data.aggregate.total_score > 0 ? `+${data.aggregate.total_score}` : data.aggregate.total_score}</strong>
              </span>
              <span className="ts-stat">
                Confidence: <strong>{data.aggregate.confidence}</strong>
              </span>
              <span className="ts-stat ts-stat-bull">
                ▲ {data.aggregate.bullish_count}
              </span>
              <span className="ts-stat ts-stat-bear">
                ▼ {data.aggregate.bearish_count}
              </span>
              {data.aggregate.caution_count > 0 ? (
                <span className="ts-stat ts-stat-caution">
                  ⚠ {data.aggregate.caution_count}
                </span>
              ) : null}
            </div>
          </div>

          {/* Per-indicator rows */}
          <div className="ts-signal-list">
            {data.signals.map((item) => (
              <SignalRow key={item.indicator_name} item={item} />
            ))}
          </div>

          <p className="ts-disclaimer">
            Technical Research Signals are for research purposes only and do not
            constitute financial advice.
          </p>
        </>
      ) : null}
    </section>
  );
}
