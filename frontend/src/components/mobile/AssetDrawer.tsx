import { useCallback, useEffect, useRef, useState } from 'react';
import { searchMoex } from '../../api/moexDirect';
import type { MoexSearchResult } from '../../api/moexDirect';
import { makeAssetId } from '../../utils/mobileWatchlist';
import type { WatchlistAsset } from '../../utils/mobileWatchlist';

type Props = {
  open: boolean;
  onClose: () => void;
  watchlist: WatchlistAsset[];
  selectedId: string;
  onSelect: (asset: WatchlistAsset) => void;
  onWatchlistChange: (list: WatchlistAsset[]) => void;
};

export function AssetDrawer({ open, onClose, watchlist, selectedId, onSelect, onWatchlistChange }: Props) {
  const [editMode,       setEditMode]       = useState(false);
  const [showSearch,     setShowSearch]     = useState(false);
  const [searchQuery,    setSearchQuery]    = useState('');
  const [searchResults,  setSearchResults]  = useState<MoexSearchResult[]>([]);
  const [searchLoading,  setSearchLoading]  = useState(false);
  const [dupMsg,         setDupMsg]         = useState(false);
  const [aliasId,        setAliasId]        = useState<string | null>(null);
  const [aliasValue,     setAliasValue]     = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
            <div className="mc-drawer-subtitle">Tap to load chart</div>
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
                    <span className="mc-dsr-meta">{r.engine}/{r.market}/{r.board}</span>
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
                      {asset.engine}/{asset.market}/{asset.board}
                    </span>
                  </div>

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
      </div>
    </>
  );
}
