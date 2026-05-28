import { useEffect, useState } from 'react';
import type { MarketDataProviderId } from '../../data/types';
import { storeRefreshToken, clearTokens, hasRefreshToken } from '../../security/tokenStorage';
import { testBcsConnection } from '../../api/bcsAuth';
import type { BcsTestResult } from '../../api/bcsAuth';

type TestState =
  | { phase: 'idle' }
  | { phase: 'testing' }
  | { phase: 'done'; result: BcsTestResult };

type Props = {
  open: boolean;
  onClose: () => void;
  providerId: MarketDataProviderId;
  onProviderChange: (id: MarketDataProviderId) => void;
  fallbackEnabled: boolean;
  onFallbackChange: (enabled: boolean) => void;
};

export function ProviderSettings({
  open,
  onClose,
  providerId,
  onProviderChange,
  fallbackEnabled,
  onFallbackChange,
}: Props) {
  const [tokenInput, setTokenInput] = useState('');
  const [tokenSaved, setTokenSaved] = useState(false);
  const [testState, setTestState] = useState<TestState>({ phase: 'idle' });

  // Sync token status each time modal opens
  useEffect(() => {
    if (open) {
      setTokenSaved(hasRefreshToken());
      setTokenInput('');
      setTestState({ phase: 'idle' });
    }
  }, [open]);

  function handleSaveToken() {
    const t = tokenInput.trim();
    if (!t) return;
    storeRefreshToken(t);
    setTokenInput('');
    setTokenSaved(true);
    setTestState({ phase: 'idle' });
  }

  function handleClearToken() {
    clearTokens();
    setTokenSaved(false);
    setTokenInput('');
    setTestState({ phase: 'idle' });
  }

  async function handleTest() {
    setTestState({ phase: 'testing' });
    const result = await testBcsConnection();
    setTestState({ phase: 'done', result });
  }

  if (!open) return null;

  return (
    <>
      <div
        className="mc-settings-overlay"
        onPointerDown={onClose}
        aria-hidden="true"
      />
      <div className="mc-settings-modal" role="dialog" aria-label="Data provider settings">

        {/* Header */}
        <div className="mc-settings-header">
          <span className="mc-settings-title">Data Provider</span>
          <button
            type="button"
            className="mc-dh-btn mc-dh-close"
            onClick={onClose}
            aria-label="Close settings"
          >
            ×
          </button>
        </div>

        {/* Provider selector */}
        <div className="mc-settings-section">
          <div className="mc-settings-label">Source</div>
          <div className="mc-settings-choices">
            <button
              type="button"
              className={`mc-settings-choice${providerId === 'moex' ? ' mc-settings-choice-on' : ''}`}
              onClick={() => onProviderChange('moex')}
            >
              MOEX
            </button>
            <button
              type="button"
              className={`mc-settings-choice${providerId === 'bcs' ? ' mc-settings-choice-on' : ''}`}
              onClick={() => onProviderChange('bcs')}
            >
              BCS
            </button>
          </div>
        </div>

        {/* BCS-only section */}
        {providerId === 'bcs' && (
          <>
            <div className="mc-settings-warn">
              Use a BCS read-only token only. Do not use a trading token.
            </div>

            {/* Token management */}
            <div className="mc-settings-section">
              <div className="mc-settings-label">
                {tokenSaved ? 'Token status' : 'Paste BCS refresh token'}
              </div>

              {!tokenSaved ? (
                <>
                  <input
                    type="password"
                    className="mc-settings-token-input"
                    placeholder="Paste refresh token here…"
                    value={tokenInput}
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    onChange={e => setTokenInput(e.target.value)}
                  />
                  <button
                    type="button"
                    className="mc-settings-btn mc-settings-btn-save"
                    disabled={!tokenInput.trim()}
                    onClick={handleSaveToken}
                  >
                    Save token
                  </button>
                </>
              ) : (
                <div className="mc-settings-token-row">
                  <span className="mc-settings-token-status">BCS token saved</span>
                  <button
                    type="button"
                    className="mc-settings-btn mc-settings-btn-clear"
                    onClick={handleClearToken}
                  >
                    Clear
                  </button>
                  <button
                    type="button"
                    className="mc-settings-btn"
                    disabled={testState.phase === 'testing'}
                    onClick={() => { void handleTest(); }}
                  >
                    {testState.phase === 'testing' ? 'Testing…' : 'Test'}
                  </button>
                </div>
              )}

              {testState.phase === 'done' && (
                <div
                  className={`mc-settings-test-result${testState.result.ok ? ' mc-settings-test-ok' : ' mc-settings-test-err'}`}
                >
                  {testState.result.ok ? 'Connection OK' : testState.result.message}
                </div>
              )}
            </div>

            {/* Fallback toggle */}
            <div className="mc-settings-section">
              <label className="mc-settings-toggle-row">
                <input
                  type="checkbox"
                  className="mc-settings-checkbox"
                  checked={fallbackEnabled}
                  onChange={e => onFallbackChange(e.target.checked)}
                />
                <span className="mc-settings-toggle-label">Fallback to MOEX if BCS fails</span>
              </label>
            </div>
          </>
        )}

        <div className="mc-settings-note">
          {providerId === 'bcs'
            ? 'Token is in session memory only — lost on app restart. BCS support is experimental.'
            : 'MOEX direct data, no authentication required.'}
        </div>
      </div>
    </>
  );
}
