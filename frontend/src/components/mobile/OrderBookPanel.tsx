// OrderBookPanel — mobile order book widget polling BCS market data.
// Polls fetchOrderBook every 2 s when active and page is visible.
// Raw errors are normalised to user-facing messages; no tokens are logged.

import { useEffect, useRef, useState } from 'react';
import {
  fetchOrderBook,
  OrderBookException,
  type OrderBookSnapshot,
} from '../../api/bcsOrderBook';
import { BcsAuthException } from '../../api/bcsAuth';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Depth = 5 | 10 | 20;

interface OrderBookPanelProps {
  ticker: string;
  classCode: string;
  active: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format an ISO timestamp to HH:mm:ss in local time. */
function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '--:--:--';
    return d.toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '--:--:--';
  }
}

/** Normalise any thrown value into a user-facing error string. */
function normaliseError(err: unknown): string {
  // Auth / token errors — direct user to Settings.
  if (err instanceof OrderBookException) {
    if (err.kind === 'auth_required') {
      return 'BCS session expired. Re-authenticate in Settings.';
    }
    if (err.kind === 'rate_limited') {
      return 'BCS rate limit reached. Retrying…';
    }
    if (err.kind === 'network_error') {
      return 'Network error. Check connection.';
    }
    return err.message;
  }

  if (err instanceof BcsAuthException) {
    if (err.kind === 'invalid_token') {
      return 'BCS token missing or expired. Paste a token in Settings.';
    }
    if (err.kind === 'network_error') {
      return 'Network error connecting to BCS. Check connection.';
    }
    if (err.kind === 'rate_limited') {
      return 'BCS auth rate limited. Retrying…';
    }
    return err.message;
  }

  if (err instanceof Error) return err.message;
  return 'Unexpected error loading order book.';
}

/** Format a price value for display. */
function fmtPrice(value: number): string {
  if (!Number.isFinite(value)) return '--';
  const abs = Math.abs(value);
  const decimals = abs >= 100 ? 2 : abs >= 1 ? 2 : 4;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** Format a quantity value for display. */
function fmtQty(value: number): string {
  if (!Number.isFinite(value)) return '--';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString('en-US');
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 2000;
const DEPTH_OPTIONS: Depth[] = [5, 10, 20];

export function OrderBookPanel({ ticker, classCode, active }: OrderBookPanelProps) {
  const [depth, setDepth] = useState<Depth>(20);
  const [snapshot, setSnapshot] = useState<OrderBookSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Stable refs to avoid stale closures in setInterval callback.
  const tickerRef = useRef(ticker);
  const classCodeRef = useRef(classCode);
  const depthRef = useRef(depth);
  const isMountedRef = useRef(true);

  useEffect(() => {
    tickerRef.current = ticker;
  }, [ticker]);

  useEffect(() => {
    classCodeRef.current = classCode;
  }, [classCode]);

  useEffect(() => {
    depthRef.current = depth;
  }, [depth]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // ── Main polling effect ────────────────────────────────────────────────────

  useEffect(() => {
    // Reset state when instrument or depth changes.
    setSnapshot(null);
    setLoading(true);
    setError(null);

    if (!active || !ticker || !classCode) {
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function poll(): Promise<void> {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') return;

      try {
        const result = await fetchOrderBook(
          tickerRef.current,
          classCodeRef.current,
          depthRef.current,
        );
        if (!cancelled && isMountedRef.current) {
          setSnapshot(result);
          setError(null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled && isMountedRef.current) {
          setError(normaliseError(err));
          setLoading(false);
          // Keep previous snapshot — do not clear it.
        }
      }
    }

    // Fire immediately, then on interval.
    void poll();

    const intervalId = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  // depth, ticker, classCode changes reset the snapshot and restart polling.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, ticker, classCode, depth]);

  // ── Spread calculation ─────────────────────────────────────────────────────

  const bestAsk = snapshot?.asks[0] ?? null;
  const bestBid = snapshot?.bids[0] ?? null;

  let spreadRow: { spread: string; spreadPct: string; mid: string } | null = null;
  if (bestAsk && bestBid) {
    const spread = bestAsk.price - bestBid.price;
    const mid = (bestAsk.price + bestBid.price) / 2;
    const spreadPct = mid !== 0 ? (spread / mid) * 100 : 0;
    spreadRow = {
      spread: fmtPrice(spread),
      spreadPct: spreadPct.toFixed(3) + '%',
      mid: fmtPrice(mid),
    };
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="mc-ob-panel">
      {/* Header */}
      <div className="mc-ob-header">
        <span className="mc-ob-title">Order Book</span>
        <span className="mc-ob-badge">BCS</span>
        {snapshot ? (
          <span className="mc-ob-time">{formatTime(snapshot.receivedAt)}</span>
        ) : null}
      </div>

      {/* Depth selector */}
      <div className="mc-ob-depth-row" role="group" aria-label="Order book depth">
        {DEPTH_OPTIONS.map(d => (
          <button
            key={d}
            type="button"
            className={`mc-ob-depth-btn${depth === d ? ' mc-ob-depth-btn-active' : ''}`}
            onClick={() => setDepth(d)}
            aria-pressed={depth === d}
          >
            {d}
          </button>
        ))}
      </div>

      {/* Body */}
      {!active || (!ticker || !classCode) ? (
        <div className="mc-ob-empty">Select an instrument to view order book.</div>
      ) : loading && !snapshot ? (
        <div className="mc-ob-loading">Loading…</div>
      ) : error && !snapshot ? (
        <div className="mc-ob-error" role="alert">{error}</div>
      ) : snapshot ? (
        <>
          {/* Column headers */}
          <div className="mc-ob-cols-header">
            <span className="mc-ob-col-ask-label">Ask (price / qty)</span>
            <span className="mc-ob-col-bid-label">Bid (price / qty)</span>
          </div>

          {/* Spread row */}
          {spreadRow ? (
            <div className="mc-ob-spread-row">
              <span className="mc-ob-spread-label">Spread</span>
              <span className="mc-ob-spread-val">{spreadRow.spread}</span>
              <span className="mc-ob-spread-pct">{spreadRow.spreadPct}</span>
              <span className="mc-ob-spread-mid">Mid {spreadRow.mid}</span>
            </div>
          ) : null}

          {/* Ask / Bid table */}
          <div className="mc-ob-table" role="table" aria-label="Order book levels">
            {/* Determine max rows to render */}
            {Array.from({ length: Math.max(snapshot.asks.length, snapshot.bids.length) }, (_, i) => {
              const ask = snapshot.asks[i] ?? null;
              const bid = snapshot.bids[i] ?? null;
              return (
                <div key={i} className="mc-ob-row" role="row">
                  {/* Ask side */}
                  <div className="mc-ob-ask" role="cell">
                    {ask ? (
                      <>
                        <span className="mc-ob-ask-price">{fmtPrice(ask.price)}</span>
                        <span className="mc-ob-ask-qty">{fmtQty(ask.quantity)}</span>
                      </>
                    ) : (
                      <span className="mc-ob-level-empty">—</span>
                    )}
                  </div>
                  {/* Bid side */}
                  <div className="mc-ob-bid" role="cell">
                    {bid ? (
                      <>
                        <span className="mc-ob-bid-price">{fmtPrice(bid.price)}</span>
                        <span className="mc-ob-bid-qty">{fmtQty(bid.quantity)}</span>
                      </>
                    ) : (
                      <span className="mc-ob-level-empty">—</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Stale error banner — shown over previous snapshot */}
          {error ? (
            <div className="mc-ob-error-banner" role="alert">{error}</div>
          ) : null}
        </>
      ) : (
        <div className="mc-ob-empty">No data.</div>
      )}
    </div>
  );
}
