import { useState } from "react";
import { runScanner } from "../api/scanner";
import { ScannerTable } from "../components/scanner/ScannerTable";
import type { ScannerResponse, ScannerInstrumentRequest } from "../types/scanner";

const DEFAULT_WATCHLIST = `SBER
GAZP
LKOH
ROSN
NVTK
GMKN
VTBR
AFLT`;

const TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"];

function parseWatchlist(text: string): ScannerInstrumentRequest[] {
  return text
    .split("\n")
    .map((line) => line.trim().toUpperCase())
    .filter((line) => line.length > 0)
    .map((ticker) => ({ ticker, engine: "stock", market: "shares", board: "TQBR" }));
}

export function ScannerPage() {
  const [watchlistText, setWatchlistText] = useState(DEFAULT_WATCHLIST);
  const [timeframe, setTimeframe] = useState("1d");
  const [result, setResult] = useState<ScannerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleScan() {
    const instruments = parseWatchlist(watchlistText);
    if (instruments.length === 0) {
      setError("Enter at least one ticker.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const data = await runScanner({ instruments, timeframe, lookback: 100 });
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-content">
      <div className="page-header">
        <h2>Market Scanner</h2>
        <p className="page-subtitle">
          Scan a watchlist using locally stored candles and indicators.
        </p>
      </div>

      <div className="scanner-notice">
        Scanner uses already loaded local candles and indicators. Load data in the
        chart page first.
      </div>

      <div className="scanner-controls">
        <div className="scanner-watchlist-wrap">
          <label className="form-label" htmlFor="watchlist-input">
            Tickers (one per line)
          </label>
          <textarea
            id="watchlist-input"
            className="scanner-watchlist-textarea"
            value={watchlistText}
            onChange={(e) => setWatchlistText(e.target.value)}
            rows={10}
            spellCheck={false}
          />
        </div>

        <div className="scanner-options">
          <div className="form-field">
            <label className="form-label">Timeframe</label>
            <div className="timeframe-selector">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  className={`tf-btn${timeframe === tf ? " tf-btn-active" : ""}`}
                  onClick={() => setTimeframe(tf)}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          <button
            className="btn btn-primary"
            onClick={handleScan}
            disabled={loading}
          >
            {loading ? "Scanning…" : "Scan watchlist"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="chart-state chart-state-error">{error}</div>
      ) : null}

      {result ? (
        <div className="scanner-results">
          <div className="scanner-results-header">
            <span className="scanner-results-meta">
              {result.rows.length} instrument{result.rows.length !== 1 ? "s" : ""} ·{" "}
              {result.timeframe} ·{" "}
              {new Date(result.generated_at).toLocaleTimeString("ru-RU")}
            </span>
            <span className="ts-disclaimer">
              Research signals only — not financial advice.
            </span>
          </div>
          <ScannerTable rows={result.rows} />
        </div>
      ) : null}

      {!result && !loading && !error ? (
        <div className="chart-state">
          Configure your watchlist and press Scan watchlist.
        </div>
      ) : null}
    </div>
  );
}
