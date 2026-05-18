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
  if (loading) {
    return (
      <div className="quote-card quote-card-loading">
        <span>Loading quote…</span>
      </div>
    );
  }

  if (error || quote === null) {
    return (
      <div className="quote-card quote-card-unavailable">
        <span>Quote unavailable</span>
      </div>
    );
  }

  const changePositive = (quote.change ?? 0) >= 0;
  const changeClass = changePositive ? "quote-change-positive" : "quote-change-negative";
  const changeSign = changePositive ? "+" : "";

  const hasPrice = quote.last_price != null;

  return (
    <div className="quote-card">
      <div className="quote-card-main">
        {hasPrice ? (
          <>
            <span className="quote-last-price">{fmt(quote.last_price)}</span>
            <span className={`quote-change ${changeClass}`}>
              {changeSign}{fmt(quote.change)} ({changeSign}{fmt(quote.change_percent)}%)
            </span>
          </>
        ) : (
          <span className="quote-no-price">No price data</span>
        )}
      </div>

      <div className="quote-card-grid">
        <div className="quote-field">
          <span className="quote-label">Bid</span>
          <span className="quote-value">{fmt(quote.bid)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">Ask</span>
          <span className="quote-value">{fmt(quote.ask)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">Open</span>
          <span className="quote-value">{fmt(quote.open)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">High</span>
          <span className="quote-value">{fmt(quote.high)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">Low</span>
          <span className="quote-value">{fmt(quote.low)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">Prev Close</span>
          <span className="quote-value">{fmt(quote.previous_close)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">Volume</span>
          <span className="quote-value">{fmtVolume(quote.volume)}</span>
        </div>
        <div className="quote-field">
          <span className="quote-label">Value</span>
          <span className="quote-value">{fmtVolume(quote.value)}</span>
        </div>
      </div>

      <div className="quote-card-footer">
        {quote.trade_time ? (
          <span className="quote-meta">Trade: {quote.trade_time}</span>
        ) : null}
        {quote.server_time ? (
          <span className="quote-meta">Server: {quote.server_time}</span>
        ) : null}
        <span className="quote-source">Source: {quote.source.toUpperCase()}</span>
      </div>
    </div>
  );
}
