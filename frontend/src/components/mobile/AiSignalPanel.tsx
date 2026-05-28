import type { AiSignalResult } from '../../ml/types';
import type { PaShortSignalResult } from '../../ml/paShortSignal';

export type AiPanelMode = 'mock' | 'pa_short';

interface AiSignalPanelProps {
  mode: AiPanelMode;
  onModeChange: (mode: AiPanelMode) => void;
  mockSignal: AiSignalResult | null;
  paSignal: PaShortSignalResult | null;
}

export function AiSignalPanel({ mode, onModeChange, mockSignal, paSignal }: AiSignalPanelProps) {
  return (
    <div className="mc-ai-panel">
      {/* Header row with model toggle */}
      <div className="mc-ai-header">
        <span className="mc-ai-title">
          {mode === 'pa_short' ? 'Experimental AI' : 'AI Signal'}
        </span>
        <div className="mc-ai-mode-toggle" role="group" aria-label="AI model selector">
          <button
            type="button"
            className={`mc-ai-mode-btn${mode === 'mock' ? ' mc-ai-mode-btn-active' : ''}`}
            onClick={() => onModeChange('mock')}
          >
            Mock
          </button>
          <button
            type="button"
            className={`mc-ai-mode-btn${mode === 'pa_short' ? ' mc-ai-mode-btn-active' : ''}`}
            onClick={() => onModeChange('pa_short')}
          >
            PA SHORT
          </button>
        </div>
      </div>

      {mode === 'pa_short' ? (
        <PaShortPanel signal={paSignal} />
      ) : (
        <MockPanel signal={mockSignal} />
      )}
    </div>
  );
}

// ── Mock signal sub-panel (original behaviour) ────────────────────────────────

function MockPanel({ signal }: { signal: AiSignalResult | null }) {
  if (!signal) {
    return <div className="mc-ai-unavailable">Calculating…</div>;
  }

  if (!signal.available) {
    return (
      <>
        <div className="mc-ai-unavailable">{signal.reason ?? 'Signal unavailable'}</div>
        <div className="mc-ai-disclaimer">Experimental local signal. Not financial advice.</div>
      </>
    );
  }

  const dirClass =
    signal.direction === 'LONG'  ? 'mc-ai-dir-long'  :
    signal.direction === 'SHORT' ? 'mc-ai-dir-short'  :
                                   'mc-ai-dir-notrade';
  const dirLabel = signal.direction === 'NO_TRADE' ? 'NO TRADE' : signal.direction;

  const upPct   = Math.round(signal.probabilities.up   * 100);
  const downPct = Math.round(signal.probabilities.down * 100);
  const flatPct = Math.round(signal.probabilities.flat * 100);

  return (
    <>
      <div className="mc-ai-body">
        <div className="mc-ai-row">
          <span className={`mc-ai-direction ${dirClass}`}>{dirLabel}</span>
          <span className={`mc-ai-confidence mc-ai-conf-${signal.confidence}`}>
            {signal.confidence.charAt(0).toUpperCase() + signal.confidence.slice(1)} confidence
          </span>
        </div>
        <div className="mc-ai-probs">
          <span className="mc-ai-prob-up">UP {upPct}%</span>
          <span className="mc-ai-prob-down">DN {downPct}%</span>
          <span className="mc-ai-prob-flat">FL {flatPct}%</span>
        </div>
        <div className="mc-ai-meta">
          <span>Horizon: {signal.horizonCandles} candles</span>
          <span>Model: {signal.modelVersion}</span>
        </div>
      </div>
      <div className="mc-ai-disclaimer">Experimental local signal. Not financial advice.</div>
    </>
  );
}

// ── PA SHORT sub-panel ────────────────────────────────────────────────────────

const RISK_CHIP_CLASS: Record<string, string> = {
  none:     'mc-pa-risk-none',
  watch:    'mc-pa-risk-watch',
  elevated: 'mc-pa-risk-elevated',
  high:     'mc-pa-risk-high',
};

function PaShortPanel({ signal }: { signal: PaShortSignalResult | null }) {
  if (!signal) {
    return <div className="mc-ai-unavailable">Calculating…</div>;
  }

  if (!signal.available) {
    return (
      <>
        <div className="mc-ai-unavailable mc-pa-unavailable">{signal.message}</div>
        <div className="mc-ai-disclaimer mc-pa-disclaimer">
          Research only. Backtest has not shown positive net expectancy.
        </div>
      </>
    );
  }

  const probPct = signal.probabilityShort !== null
    ? `${Math.round(signal.probabilityShort * 100)}%`
    : '--';

  const chipClass = RISK_CHIP_CLASS[signal.riskLevel] ?? 'mc-pa-risk-none';

  return (
    <>
      <div className="mc-pa-body">
        {/* Subtitle */}
        <div className="mc-pa-subtitle">PA SHORT risk</div>

        {/* Probability + risk chip */}
        <div className="mc-pa-row">
          <span className="mc-pa-prob">SHORT risk: {probPct}</span>
          <span className={`mc-pa-risk-chip ${chipClass}`}>
            {signal.riskLevel.toUpperCase()}
          </span>
        </div>

        {/* Meta */}
        <div className="mc-pa-meta">
          <span>Model: {signal.modelId}</span>
          <span>Horizon: 12 candles</span>
          <span>TP 0.4% / SL 0.25%</span>
        </div>
      </div>

      {/* Research warning — always visible */}
      <div className="mc-ai-disclaimer mc-pa-disclaimer">
        Research only. Backtest has not shown positive net expectancy.
      </div>
    </>
  );
}
