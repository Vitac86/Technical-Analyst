import { useNavigate } from "react-router-dom";
import type { ScannerRow, ScanStatus } from "../../types/scanner";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_LABEL: Record<ScanStatus, string> = {
  ok: "OK",
  no_instrument: "Not found",
  no_candles: "No candles",
  no_indicators: "No indicators",
  error: "Error",
};

const SIGNAL_LABEL: Record<string, string> = {
  strong_buy: "Strong Buy",
  buy: "Buy",
  neutral: "Neutral",
  sell: "Sell",
  strong_sell: "Strong Sell",
  caution: "Caution",
  no_data: "—",
};

function signalClass(signal: string | null): string {
  if (!signal) return "";
  if (signal === "strong_buy" || signal === "buy") return "ts-badge ts-badge-buy";
  if (signal === "strong_sell" || signal === "sell") return "ts-badge ts-badge-sell";
  if (signal === "caution") return "ts-badge ts-badge-caution";
  return "ts-badge ts-badge-neutral";
}

function statusClass(status: ScanStatus): string {
  if (status === "ok") return "scanner-status-ok";
  if (status === "error") return "scanner-status-error";
  return "scanner-status-missing";
}

function fmt(value: number | null, decimals = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(decimals);
}

function fmtScore(score: number | null): string {
  if (score === null || score === undefined) return "—";
  return score > 0 ? `+${score}` : String(score);
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function TableRow({ row }: { row: ScannerRow }) {
  const navigate = useNavigate();

  function handleTickerClick() {
    navigate(`/instruments/${row.ticker}`);
  }

  return (
    <tr className={row.status !== "ok" ? "scanner-row-dim" : undefined}>
      <td>
        <button className="scanner-ticker-link" onClick={handleTickerClick}>
          {row.ticker}
        </button>
        {row.name ? <span className="scanner-name">{row.name}</span> : null}
      </td>
      <td>
        <span className={statusClass(row.status)}>{STATUS_LABEL[row.status]}</span>
        {row.error ? <span className="scanner-error-hint" title={row.error}>(!)</span> : null}
      </td>
      <td>
        {row.aggregate_signal ? (
          <span className={signalClass(row.aggregate_signal)}>
            {SIGNAL_LABEL[row.aggregate_signal] ?? row.aggregate_signal}
          </span>
        ) : "—"}
      </td>
      <td>{fmtScore(row.total_score)}</td>
      <td>{row.confidence ?? "—"}</td>
      <td>{fmt(row.last_close)}</td>
      <td>{fmt(row.rsi)}</td>
      <td>{fmt(row.macd_histogram, 4)}</td>
      <td>{row.atr_percent !== null ? `${fmt(row.atr_percent)}%` : "—"}</td>
      <td>
        {row.nearest_support !== null
          ? `${fmt(row.nearest_support)} (${fmt(row.distance_to_support_percent)}%)`
          : "—"}
      </td>
      <td>
        {row.nearest_resistance !== null
          ? `${fmt(row.nearest_resistance)} (${fmt(row.distance_to_resistance_percent)}%)`
          : "—"}
      </td>
      <td className="scanner-summary-cell">{row.summary ?? "—"}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

type Props = {
  rows: ScannerRow[];
};

export function ScannerTable({ rows }: Props) {
  if (rows.length === 0) {
    return <p className="chart-state">No results.</p>;
  }

  return (
    <div className="scanner-table-wrap">
      <table className="scanner-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Status</th>
            <th>Signal</th>
            <th>Score</th>
            <th>Confidence</th>
            <th>Last</th>
            <th>RSI</th>
            <th>MACD hist</th>
            <th>ATR %</th>
            <th>Support</th>
            <th>Resistance</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <TableRow key={`${row.ticker}-${row.timeframe}`} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
