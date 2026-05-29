import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchMoexQuote, searchMoex } from '../../api/moexDirect';
import type { MoexQuote, MoexSearchResult } from '../../api/moexDirect';
import { makeAssetId } from '../../utils/mobileWatchlist';
import type { WatchlistAsset } from '../../utils/mobileWatchlist';
import { CURRENT_APP_VERSION_NAME } from '../../api/appUpdate';
import { useTranslation } from '../../i18n/useTranslation';

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
  const { t } = useTranslation();
  const [editMode,       setEditMode]       = useState(false);
  const [showSearch,     setShowSearch]     = useState(false);
  const [searchQuery,    setSearchQuery]    = useState('');
  const [searchResults,  setSearchResults]  = useState<MoexSearchResult[]>([]);
  const [searchLoading,  setSearchLoading]  = useState(false);
  const [dupMsg,         setDupMsg]         = useState(false);
  const [quotes,         setQuotes]         = useState<Record<string, DrawerQuoteState>>({});

  // Drag-reorder state — pointer-based, works in Android WebView where native
  // HTML5 drag-and-drop is unreliable.
  const [dragId,        setDragId]        = useState<string | null>(null);
  const [dragOverId,    setDragOverId]    = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const quoteInFlightRef = useRef(false);

  // Reset local UI state after drawer closes (after slide-out animation)
  useEffect(() => {
    if (!open) {
      const tmr = setTimeout(() => {
        setEditMode(false);
        setShowSearch(false);
        setSearchQuery('');
        setSearchResults([]);
        setDupMsg(false);
        setDragId(null);
        setDragOverId(null);
      }, 280);
      return () => clearTimeout(tmr);
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

  // ── Pointer-based drag reorder ─────────────────────────────────────────────

  function handleDragHandlePointerDown(id: string) {
    setDragId(id);
    setDragOverId(id);
  }

  function handleRowPointerEnter(id: string) {
    if (!dragId) return;
    if (id !== dragOverId) setDragOverId(id);
  }

  function commitReorder() {
    if (!dragId || !dragOverId || dragId === dragOverId) {
      setDragId(null);
      setDragOverId(null);
      return;
    }
    const fromIdx = watchlist.findIndex(a => a.id === dragId);
    const toIdx = watchlist.findIndex(a => a.id === dragOverId);
    if (fromIdx === -1 || toIdx === -1) {
      setDragId(null);
      setDragOverId(null);
      return;
    }
    const next = [...watchlist];
    const [moved] = next.splice(fromIdx, 1);
    next.splice(toIdx, 0, moved);
    onWatchlistChange(next);
    setDragId(null);
    setDragOverId(null);
  }

  // Global pointer-move/up so dragging continues even when the finger leaves
  // a row (rows are tracked via per-row pointerenter).
  useEffect(() => {
    if (!dragId) return;
    function onMove(e: PointerEvent) {
      // Walk the element under the pointer and find the closest row id.
      const el = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
      if (!el) return;
      const row = el.closest('[data-row-id]') as HTMLElement | null;
      const rid = row?.getAttribute('data-row-id') ?? null;
      if (rid && rid !== dragOverId) setDragOverId(rid);
    }
    function onUp() { commitReorder(); }
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragId, dragOverId, watchlist]);

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
        aria-label={t('drawer.assets')}
      >
        {/* ── Drawer header ──────────────────────────────────────────── */}
        <div className="mc-drawer-header">
          <div>
            <div className="mc-drawer-title">{t('drawer.assets')}</div>
            <div className="mc-drawer-subtitle">
              Technical Analyst · v{CURRENT_APP_VERSION_NAME}
            </div>
          </div>
          <div className="mc-drawer-header-btns">
            {editMode ? (
              <button type="button" className="mc-dh-btn mc-dh-done" onClick={() => setEditMode(false)}>
                {t('drawer.done')}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="mc-dh-btn"
                  onClick={() => setShowSearch(s => !s)}
                >
                  {showSearch ? t('drawer.cancel') : t('drawer.add')}
                </button>
                <button
                  type="button"
                  className="mc-dh-btn"
                  onClick={() => { setEditMode(true); setShowSearch(false); }}
                >
                  {t('drawer.manage')}
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
                placeholder={t('drawer.search.placeholder')}
                value={searchQuery}
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="characters"
                spellCheck={false}
                onChange={e => handleSearchInput(e.target.value)}
              />
              {searchLoading && <span className="mc-drawer-search-spin">…</span>}
            </div>
            {dupMsg && <div className="mc-drawer-dup-msg">{t('drawer.duplicate')}</div>}
            {searchResults.length > 0 && (
              <ul className="mc-drawer-search-list">
                {searchResults.map(r => {
                  const resultId = makeAssetId(r.engine, r.market, r.board, r.ticker);
                  const isAdded = watchlist.some(a => a.id === resultId);
                  return (
                    <li
                      key={`${r.engine}:${r.market}:${r.board}:${r.ticker}`}
                      style={isAdded ? { opacity: 0.6 } : undefined}
                      onPointerDown={isAdded ? undefined : () => handleAddResult(r)}
                    >
                      <span className="mc-dsr-ticker">{r.ticker}</span>
                      <span className="mc-dsr-name">{r.name}</span>
                      <span className="mc-dsr-meta">{r.board}</span>
                      {isAdded && <span className="mc-dsr-added">{t('drawer.added')}</span>}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {/* ── Asset list ─────────────────────────────────────────────── */}
        <div className="mc-drawer-list" ref={listRef}>
          {watchlist.length === 0 ? (
            <div className="mc-drawer-empty">
              <p className="mc-drawer-empty-title">{t('drawer.empty.title')}</p>
              <p className="mc-drawer-empty-sub">{t('drawer.empty.sub')}</p>
              <button type="button" className="mc-dh-btn" onClick={() => setShowSearch(true)}>
                {t('drawer.add.asset')}
              </button>
            </div>
          ) : (
            <>
              {editMode && (
                <div className="mc-drawer-drag-hint">{t('drawer.drag.hint')}</div>
              )}
              {watchlist.map((asset) => {
                const active = asset.id === selectedId;
                const quoteState = quotes[asset.id];
                const quote = quoteState?.quote ?? null;
                const changePercent = quote?.changePercent ?? null;
                const isDragging = dragId === asset.id;
                const isDragOver = !!dragId && dragOverId === asset.id && dragId !== asset.id;
                return (
                  <div
                    key={asset.id}
                    data-row-id={asset.id}
                    className={
                      `mc-drawer-item` +
                      (active     ? ' mc-drawer-item-sel'     : '') +
                      (editMode   ? ' mc-drawer-item-edit'    : '') +
                      (isDragging ? ' mc-drawer-item-dragging' : '') +
                      (isDragOver ? ' mc-drawer-item-dragover' : '')
                    }
                    onPointerEnter={() => handleRowPointerEnter(asset.id)}
                    onClick={
                      editMode
                        ? undefined
                        : () => { onSelect(asset); onClose(); }
                    }
                  >
                    {editMode && (
                      <div
                        className="mc-drag-handle"
                        role="button"
                        aria-label={t('drawer.drag.hint')}
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          handleDragHandlePointerDown(asset.id);
                        }}
                      >
                        <span />
                        <span />
                        <span />
                      </div>
                    )}

                    {/* Asset info — display name comes from instrument metadata only */}
                    <div className="mc-drawer-item-body">
                      <span className="mc-drawer-item-ticker">
                        {asset.ticker}
                      </span>
                      {asset.name && (
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

                    {/* Edit controls — only remove, no rename, no up/down */}
                    {editMode && (
                      <div className="mc-drawer-edit-row" onClick={e => e.stopPropagation()}>
                        <button
                          type="button"
                          className="mc-edit-btn mc-edit-del"
                          title={t('drawer.remove')}
                          aria-label={t('drawer.remove')}
                          onClick={() => handleDelete(asset.id)}
                        >
                          ×
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </>
          )}
        </div>

        {/* ── Footer ──────────────────────────────────────────────────── */}
        <div className="mc-drawer-footer">
          <span className="mc-drawer-footer-version">v{CURRENT_APP_VERSION_NAME}</span>
          {onSettingsOpen&&(<button type="button" className="mc-drawer-settings-btn"
            onClick={()=>{onClose();onSettingsOpen();}} aria-label={t('drawer.settings')}>{t('drawer.settings')}</button>)}
        </div>
      </div>
    </>
  );
}
