import type { AiSignalResult } from '../../ml/types';
import type { PaShortSignalResult } from '../../ml/paShortSignal';
import { useTranslation } from '../../i18n/useTranslation';

export type AiPanelMode = 'mock' | 'pa_short';

interface AiSignalPanelProps {
  mode: AiPanelMode;
  onModeChange: (mode: AiPanelMode) => void;
  mockSignal: AiSignalResult | null;
  paSignal: PaShortSignalResult | null;
}

export function AiSignalPanel({ mode, onModeChange, mockSignal, paSignal }: AiSignalPanelProps) {
  const { t } = useTranslation();
  return (
    <div className="mc-ai-panel">
      {/* Header row with model toggle */}
      <div className="mc-ai-header">
        <span className="mc-ai-title">
          {mode === 'pa_short' ? t('ai.experimental') : t('ai.signal')}
        </span>
        <div className="mc-ai-mode-toggle" role="group" aria-label="AI model selector">
          <button
            type="button"
            className={`mc-ai-mode-btn${mode === 'mock' ? ' mc-ai-mode-btn-active' : ''}`}
            onClick={() => onModeChange('mock')}
          >
            {t('settings.ai.mode.mock')}
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
  const { t } = useTranslation();
  if (!signal) {
    return <div className="mc-ai-unavailable">{t('ai.calculating')}</div>;
  }

  if (!signal.available) {
    return (
      <>
        <div className="mc-ai-unavailable">{signal.reason ?? 'Signal unavailable'}</div>
        <div className="mc-ai-disclaimer">{t('ai.disclaimer')}</div>
      </>
    );
  }

  const dirClass =
    signal.direction === 'LONG'  ? 'mc-ai-dir-long'  :
    signal.direction === 'SHORT' ? 'mc-ai-dir-short'  :
                                   'mc-ai-dir-notrade';
  const dirLabel = signal.direction === 'NO_TRADE' ? t('ai.notrade') : signal.direction;

  const upPct   = Math.round(signal.probabilities.up   * 100);
  const downPct = Math.round(signal.probabilities.down * 100);
  const flatPct = Math.round(signal.probabilities.flat * 100);

  const confLabel =
    signal.confidence === 'high'   ? t('ai.confidence.high') :
    signal.confidence === 'medium' ? t('ai.confidence.medium') :
                                     t('ai.confidence.low');

  return (
    <>
      <div className="mc-ai-body">
        <div className="mc-ai-row">
          <span className={`mc-ai-direction ${dirClass}`}>{dirLabel}</span>
          <span className={`mc-ai-confidence mc-ai-conf-${signal.confidence}`}>
            {confLabel}
          </span>
        </div>
        <div className="mc-ai-probs">
          <span className="mc-ai-prob-up">UP {upPct}%</span>
          <span className="mc-ai-prob-down">DN {downPct}%</span>
          <span className="mc-ai-prob-flat">FL {flatPct}%</span>
        </div>
        <div className="mc-ai-meta">
          <span>{t('ai.horizon')}: {signal.horizonCandles} {t('ai.candles')}</span>
          <span>{t('ai.model')}: {signal.modelVersion}</span>
        </div>
      </div>
      <div className="mc-ai-disclaimer">{t('ai.disclaimer')}</div>
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
  const { t } = useTranslation();
  if (!signal) {
    return <div className="mc-ai-unavailable">{t('ai.calculating')}</div>;
  }

  if (!signal.available) {
    return (
      <>
        <div className="mc-ai-unavailable mc-pa-unavailable">{signal.message}</div>
        <div className="mc-ai-disclaimer mc-pa-disclaimer">
          {t('ai.research.warning')}
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
        <div className="mc-pa-subtitle">{t('ai.short.risk')}</div>

        {/* Probability + risk chip */}
        <div className="mc-pa-row">
          <span className="mc-pa-prob">{t('ai.short.risk')}: {probPct}</span>
          <span className={`mc-pa-risk-chip ${chipClass}`}>
            {signal.riskLevel.toUpperCase()}
          </span>
        </div>

        {/* Meta */}
        <div className="mc-pa-meta">
          <span>{t('ai.model')}: {signal.modelId}</span>
          <span>{t('ai.horizon')}: 12 {t('ai.candles')}</span>
          <span>TP 0.4% / SL 0.25%</span>
        </div>
      </div>

      {/* Research warning — always visible */}
      <div className="mc-ai-disclaimer mc-pa-disclaimer">
        {t('ai.research.warning')}
      </div>
    </>
  );
}
