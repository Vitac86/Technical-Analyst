import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchMoexQuote, searchMoex } from '../../api/moexDirect';
import type { MoexQuote, MoexSearchResult } from '../../api/moexDirect';
import { makeAssetId } from '../../utils/mobileWatchlist';
import type { WatchlistAsset } from '../../utils/mobileWatchlist';
import { checkForAppUpdate, CURRENT_APP_VERSION_NAME } from '../../api/appUpdate';
import type { AppUpdateManifest } from '../../api/appUpdate';

type UpdatePhase =
  | { phase: 'idle' }
  | { phase: 'checking' }
  | { phase: 'up_to_date' }
  | { phase: 'update_available'; manifest: AppUpdateManifest }
  | { phase: 'unsupported'; manifest: AppUpdateManifest }
  | { phase: 'error'; message: string };

type DrawerQuoteState = {
  quote: MoexQuote | null;
  error: boolean;
};

const QUOTE_POLL_MS = 30000;

type Props = {
  open: boolean;
  onClose: () => void;
  watchlist: WatchlistAsset[];
  selectedId: string;
  onSelect: (asset: WatchlistAsset) => void;
  onWatchlistChange: (list: WatchlistAsset[]) => void;
  onSettingsOpen?: () => void;
};

function formatQuotePrice(price: number): string {
  const abs = Math.abs(price);
  const fractionDigits = abs >= 1000 ? 1 : abs >= 1 ? 2 : 4;
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(price);
}

function formatQuoteChange(changePercent: number): string {
  const sign = changePercent > 0 ? '+' : '';
  return `${sign}${changePercent.toFixed(2)}%`;
}

function quoteTone(changePercent: number): string {
  if (changePercent > 0) return 'pos';
  if (changePercent < 0) return 'neg';
  return 'flat';
}

export function AssetDrawer({ open, onClose, watchlist, selectedId, onSelect, onWatchlistChange, onSettingsOpen }: Props) {
  const [editMode,       setEditMode]       = useState(false);
  const [showSearch,     setShowSearch]     = useState(false);
  const [searchQuery,    setSearchQuery]    = useState('');
  const [searchResults,  setSearchResults]  = useState<MoexSearchResult[]>([]);
  const [searchLoading,  setSearchLoading]  = useState(false);
  const [dupMsg,         setDupMsg]         = useState(false);
  const [aliasId,        setAliasId]        = useState<string | null>(null);
  const [aliasValue,     setAliasValue]     = useState('');
  const [updateState,    setUpdateState]    = useState<UpdatePhase>({ phase: 'idle' });
  const [quotes,         setQuotes]         = useState<Record<string, DrawerQuoteState>>({});
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const quoteInFlightRef = useRef(false);

  // Reset local UI state after drawer closes (after slide-out animation)
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        setEditMode(false);
        setShowSearch(false);
        setSearchQuery('');
        setSearchResults([]);
        setAliasId(null);
        setDupMsg(false);
      }, 280);
      return () => clearTimeout(t);
    }
  }, [open]);

  const triggerSearch = useCallback((q: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (q.trim().length < 2) { setSearchResults([]); return; }
    debounceRef.current = setTimeout(async () => {
      setSearchLoading(true);
      try {
        setSearchResults(await searchMoex(q.trim()));
      } catch {
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 350);
  }, []);

  useEffect(() => {
    if (!open) return;

    if (watchlist.length === 0) {
      setQuotes({});
      return;
    }

    let cancelled = false;
    const controller = new AbortController();

    async function loadQuotes() {
      if (quoteInFlightRef.current) return;
      if (document.visibilityState !== 'visible') return;

      quoteInFlightRef.current = true;
      const snapshot = watchlist;

      try {
        const entries = await Promise.all(snapshot.map(async asset => {
          try {
            const quote = await fetchMoexQuote(asset, controller.signal);
            return [asset.id, { quote, error: false }] as const;
          } catch {
            return [asset.id, { quote: null, error: !controller.signal.aborted }] as const;
          }
        }));

        if (cancelled || controller.signal.aborted) return;
        setQuotes(Object.fromEntries(entries) as Record<string, DrawerQuoteState>);
      } finally {
        if (!cancelled) quoteInFlightRef.current = false;
      }
    }

    function onVisibilityChange() {
      if (document.visibilityState === 'visible') void loadQuotes();
    }

    void loadQuotes();
    const id = setInterval(() => { void loadQuotes(); }, QUOTE_POLL_MS);
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      quoteInFlightRef.current = false;
      controller.abort();
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [open, watchlist]);

  function handleSearchInput(val: string) {
    setSearchQuery(val);
    triggerSearch(val);
  }

  function handleAddResult(r: MoexSearchResult) {
    const id = makeAssetId(r.engine, r.market, r.board, r.ticker);
    if (watchlist.some(a => a.id === id)) {
      setDupMsg(true);
      setTimeout(() => setDupMsg(false), 2000);
      return;
    }
    const newAsset: WatchlistAsset = {
      id,
      ticker: r.ticker,
      name:   r.name,
      engine: r.engine,
      market: r.market,
      board:  r.board,
    };
    onWatchlistChange([...watchlist, newAsset]);
    onSelect(newAsset);
    onClose();
  }

  function handleDelete(id: string) {
    const newList = watchlist.filter(a => a.id !== id);
    onWatchlistChange(newList);
    if (id === selectedId && newList.length > 0) onSelect(newList[0]);
  }

  function swap(idx: number, dir: -1 | 1) {
    const j = idx + dir;
    if (j < 0 || j >= watchlist.length) return;
    const next = [...watchlist];
    [next[idx], next[j]] = [next[j], next[idx]];
    onWatchlistChange(next);
  }

  function startAlias(asset: WatchlistAsset) {
    setAliasId(asset.id);
    setAliasValue(asset.alias ?? asset.ticker);
  }

  function commitAlias(id: string) {
    onWatchlistChange(
      watchlist.map(a => a.id === id ? { ...a, alias: aliasValue.trim() || undefined } : a),
    );
    setAliasId(null);
  }

  async function handleCheckUpdate() {
    setUpdateState({ phase: 'checking' });
    try {
      const result = await checkForAppUpdate();
      if (result.status === 'up_to_date') {
        setUpdateState({ phase: 'up_to_date' });
      } else if (result.status === 'update_available') {
        setUpdateState({ phase: 'update_available', manifest: result.manifest });
      } else {
        setUpdateState({ phase: 'unsupported', manifest: result.manifest });
      }
    } catch (err) {
      setUpdateState({
        phase: 'error',
        message: err instanceof Error ? err.message : 'Update check failed',
      });
    }
  }

  function openApkUrl(url: string) {
    const opened = window.open(url, '_system');
    if (!opened) window.location.href = url;
  }

  return (
    <>
      {/* Dim overlay — tap to close */}
      <div
        className={`mc-drawer-overlay${open ? ' mc-drawer-overlay-on' : ''}`}
        onPointerDown={onClose}
        aria-hidden="true"
      />

      {/* Slide-in drawer */}
      <div
        className={`mc-drawer${open ? ' mc-drawer-open' : ''}`}
        role="dialog"
        aria-label="Asset list"
      >
        {/* ── Drawer header ──────────────────────────────────────────── */}
        <div className="mc-drawer-header">
          <div>
            <div className="mc-drawer-title">Assets</div>
            <div className="mc-drawer-subtitle">
              Technical Analyst · v{CURRENT_APP_VERSION_NAME}
            </div>
          </div>
          <div className="mc-drawer-header-btns">
            {editMode ? (
              <button type="button" className="mc-dh-btn mc-dh-done" onClick={() => setEditMode(false)}>
                Done
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="mc-dh-btn"
                  onClick={() => setShowSearch(s => !s)}
                >
                  {showSearch ? 'Cancel' : '+ Add'}
                </button>
                <button
                  type="button"
                  className="mc-dh-btn"
                  onClick={() => { setEditMode(true); setShowSearch(false); }}
                >
                  Edit
                </button>
              </>
            )}
            <button
              type="button"
              className="mc-dh-btn mc-dh-close"
              onClick={onClose}
              aria-label="Close"
            >
              ×
            </button>
          </div>
        </div>

        {/* ── Inline search ──────────────────────────────────────────── */}
        {showSearch && !editMode && (
          <div className="mc-drawer-search">
            <div className="mc-drawer-search-row">
              <input
                type="search"
                inputMode="text"
                className="mc-drawer-search-input"
                placeholder="Search MOEX…"
                value={searchQuery}
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="characters"
                spellCheck={false}
                onChange={e => handleSearchInput(e.target.value)}
              />
              {searchLoading && <span className="mc-drawer-search-spin">…</span>}
            </div>
            {dupMsg && <div className="mc-drawer-dup-msg">Already in watchlist</div>}
            {searchResults.length > 0 && (
              <ul className="mc-drawer-search-list">
                {searchResults.map(r => (
                  <li
                    key={`${r.engine}:${r.market}:${r.board}:${r.ticker}`}
                    onPointerDown={() => handleAddResult(r)}
                  >
                    <span className="mc-dsr-ticker">{r.ticker}</span>
                    <span className="mc-dsr-name">{r.name}</span>
                    <span className="mc-dsr-meta">{r.board}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* ── Asset list ─────────────────────────────────────────────── */}
        <div className="mc-drawer-list">
          {watchlist.length === 0 ? (
            <div className="mc-drawer-empty">
              <p>No assets in watchlist.</p>
              <button type="button" className="mc-dh-btn" onClick={() => setShowSearch(true)}>
                + Add asset
              </button>
            </div>
          ) : (
            watchlist.map((asset, idx) => {
              const active = asset.id === selectedId;
              const quoteState = quotes[asset.id];
              const quote = quoteState?.quote ?? null;
              const changePercent = quote?.changePercent ?? null;
              return (
                <div
                  key={asset.id}
                  className={
                    `mc-drawer-item` +
                    (active    ? ' mc-drawer-item-sel'  : '') +
                    (editMode  ? ' mc-drawer-item-edit' : '')
                  }
                  onClick={editMode ? undefined : () => { onSelect(asset); onClose(); }}
                >
                  {/* Asset info */}
                  <div className="mc-drawer-item-body">
                    {aliasId === asset.id ? (
                      <input
                        type="text"
                        className="mc-drawer-alias-input"
                        value={aliasValue}
                        autoFocus
                        onChange={e => setAliasValue(e.target.value)}
                        onBlur={() => commitAlias(asset.id)}
                        onKeyDown={e => { if (e.key === 'Enter') commitAlias(asset.id); }}
                        onClick={e => e.stopPropagation()}
                      />
                    ) : (
                      <span className="mc-drawer-item-ticker">
                        {asset.alias ?? asset.ticker}
                      </span>
                    )}
                    {asset.name && aliasId !== asset.id && (
                      <span className="mc-drawer-item-name">{asset.name}</span>
                    )}
                    <span className="mc-drawer-item-meta">
                      {asset.market === 'selt'
                        ? `FX · ${asset.board}`
                        : asset.market === 'forts'
                          ? `FORTS · ${asset.board}`
                          : asset.board}
                    </span>
                  </div>

                  {!editMode && (
                    <div
                      className="mc-drawer-quote"
                      title={quote?.updatedAt ? `Quote ${quote.updatedAt}` : undefined}
                    >
                      {quote?.price != null ? (
                        <span className="mc-drawer-quote-price">
                          {formatQuotePrice(quote.price)}
                        </span>
                      ) : (
                        <span className="mc-drawer-quote-dash">--</span>
                      )}
                      {changePercent != null ? (
                        <span className={`mc-drawer-quote-change mc-drawer-quote-change-${quoteTone(changePercent)}`}>
                          {formatQuoteChange(changePercent)}
                        </span>
                      ) : quoteState?.error ? (
                        <span className="mc-drawer-quote-error">!</span>
                      ) : null}
                    </div>
                  )}

                  {/* Edit controls */}
                  {editMode && (
                    <div className="mc-drawer-edit-row" onClick={e => e.stopPropagation()}>
                      <button
                        type="button"
                        className="mc-edit-btn mc-edit-alias"
                        title="Rename"
                        onClick={() => startAlias(asset)}
                      >
                        Aa
                      </button>
                      <button
                        type="button"
                        className="mc-edit-btn mc-edit-up"
                        disabled={idx === 0}
                        title="Move up"
                        onClick={() => swap(idx, -1)}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="mc-edit-btn mc-edit-dn"
                        disabled={idx === watchlist.length - 1}
                        title="Move down"
                        onClick={() => swap(idx, 1)}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="mc-edit-btn mc-edit-del"
                        title="Delete"
                        onClick={() => handleDelete(asset.id)}
                      >
                        ×
                      </button>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* ── Settings section ────────────────────────────────────────── */}
        {onSettingsOpen && (
          <div className="mc-update-section" style={{ borderBottom: '1px solid var(--mc-border)', paddingBottom: 10 }}>
            <button
              type="button"
              className="mc-update-check-btn"
              onClick={() => { onClose(); onSettingsOpen(); }}
            >
              ⚙ Data source
            </button>
          </div>
        )}

        {/* ── Update section ──────────────────────────────────────────── */}
        <div className="mc-update-section">
          <div className="mc-update-version">v{CURRENT_APP_VERSION_NAME}</div>
          {updateState.phase === 'idle' && (
            <button type="button" className="mc-update-check-btn" onClick={handleCheckUpdate}>
              Check update
            </button>
          )}
          {updateState.phase === 'checking' && (
            <span className="mc-update-status-text">Checking…</span>
          )}
          {updateState.phase === 'up_to_date' && (
            <span className="mc-update-status-text mc-update-ok">App is up to date</span>
          )}
          {updateState.phase === 'update_available' && (
            <div className="mc-update-available">
              <div className="mc-update-info">Update available: v{updateState.manifest.versionName}</div>
              {updateState.manifest.notes && updateState.manifest.notes.length > 0 && (
                <ul className="mc-update-notes">
                  {updateState.manifest.notes.map((note, i) => (
                    <li key={i}>{note}</li>
                  ))}
                </ul>
              )}
              <button type="button" className="mc-update-download-btn" onClick={() => openApkUrl(updateState.manifest.apkUrl)}>
                Download APK
              </button>
            </div>
          )}
          {updateState.phase === 'unsupported' && (
            <div className="mc-update-available">
              <div className="mc-update-unsupported">This version is no longer supported</div>
              <button type="button" className="mc-update-download-btn" onClick={() => openApkUrl(updateState.manifest.apkUrl)}>
                Download APK
              </button>
            </div>
          )}
          {updateState.phase === 'error' && (
            <div className="mc-update-error-row">
              <span className="mc-update-error">{updateState.message}</span>
              <button type="button" className="mc-update-check-btn" onClick={handleCheckUpdate}>
                Retry
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
