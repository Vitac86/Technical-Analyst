import type { QuoteSnapshot } from "../../types/quote";

type Props = {
  quote: QuoteSnapshot | null;
  loading: boolean;
  error: string | null;
};

function fmt(value: number | null, decimals = 2): string {
  return value != null ? value.toFixed(decimals) : "—";
}

function fmtVolume(value: number | null): string {
  if (value == null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toFixed(0);
}

export function QuoteSummaryCard({ quote, loading, error }: Props) {
  if (loading && quote === null) {
    return (
      <div className="quote-card quote-card-loading">
        <span>Loading quote…</span>
      </div>
    );
  }

  if (quote === null) {
    return (
      <div className="quote-card quote-card-unavailable">
        <span className="quote-warn-chip">{error ?? "Quote unavailable"}</span>
      </div>
    );
  }

  const changePositive = (quote.change ?? 0) >= 0;
  const changeClass = changePositive ? "quote-value-pos" : "quote-value-neg";
  const changeSign = changePositive ? "+" : "";

  return (
    <div className="quote-card">
      {error ? (
        <div className="quote-warn-chip">Refresh failed — showing cached data</div>
      ) : null}
      <div className="quote-chip-row">
        <div className="quote-chip quote-chip-accent">
          <span className="quote-label">Last</span>
          <span className="quote-value">{fmt(quote.last_price)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Change</span>
          <span className={`quote-value ${changeClass}`}>
            {changeSign}{fmt(quote.change_percent)}%
          </span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Bid / Ask</span>
          <span className="quote-value">{fmt(quote.bid)} / {fmt(quote.ask)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Open</span>
          <span className="quote-value">{fmt(quote.open)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">High</span>
          <span className="quote-value">{fmt(quote.high)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Low</span>
          <span className="quote-value">{fmt(quote.low)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Prev Close</span>
          <span className="quote-value">{fmt(quote.previous_close)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Volume</span>
          <span className="quote-value">{fmtVolume(quote.volume)}</span>
        </div>
        <div className="quote-chip">
          <span className="quote-label">Trade time</span>
          <span className="quote-value quote-value-mono">{quote.trade_time ?? "—"}</span>
        </div>
      </div>
    </div>
  );
}
