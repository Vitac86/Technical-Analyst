import type { AiSignalResult } from '../../ml/types';
import type { PaShortSignalResult } from '../../ml/paShortSignal';
import { useTranslation } from '../../i18n/useTranslation';

// Unified signal mode used across the app. SuperTrend is a research mode and
// is intentionally shown as "not validated" until the offline backtest
// pipeline produces a selected candidate (see ml/strategies/).
export type SignalMode = 'mock' | 'pa_short' | 'supertrend';

// Legacy alias kept to ease the refactor.
export type AiPanelMode = SignalMode;

interface AiSignalPanelProps {
  mode: SignalMode;
  onModeChange: (mode: SignalMode) => void;
  mockSignal: AiSignalResult | null;
  paSignal: PaShortSignalResult | null;
}

const SIGNAL_MODES: SignalMode[] = ['mock', 'pa_short', 'supertrend'];

export function AiSignalPanel({ mode, onModeChange, mockSignal, paSignal }: AiSignalPanelProps) {
  const { t } = useTranslation();

  const headerTitle =
    mode === 'pa_short'    ? t('ai.experimental') :
    mode === 'supertrend'  ? t('ai.supertrend.title') :
                             t('ai.signal');

  return (
    <div className="mc-ai-panel">
      <div className="mc-ai-header">
        <span className="mc-ai-title">{headerTitle}</span>
        <SignalModeSelector mode={mode} onChange={onModeChange} />
      </div>

      {mode === 'pa_short'   ? <PaShortPanel signal={paSignal} /> :
       mode === 'supertrend' ? <SupertrendPanel /> :
                               <MockPanel signal={mockSignal} />}
    </div>
  );
}

// ── Signal mode selector (segmented control) ──────────────────────────────────

function SignalModeSelector({
  mode,
  onChange,
}: {
  mode: SignalMode;
  onChange: (mode: SignalMode) => void;
}) {
  const { t } = useTranslation();
  const labels: Record<SignalMode, string> = {
    mock:       t('settings.ai.mode.mock'),
    pa_short:   t('settings.ai.mode.pa_short'),
    supertrend: t('settings.ai.mode.supertrend'),
  };
  return (
    <div
      className="mc-ai-mode-toggle"
      role="group"
      aria-label={t('settings.ai.mode')}
    >
      {SIGNAL_MODES.map((m) => (
        <button
          key={m}
          type="button"
          className={`mc-ai-mode-btn${mode === m ? ' mc-ai-mode-btn-active' : ''}`}
          onClick={() => onChange(m)}
        >
          {labels[m]}
        </button>
      ))}
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
        <div className="mc-pa-subtitle">{t('ai.short.risk')}</div>

        <div className="mc-pa-row">
          <span className="mc-pa-prob">{t('ai.short.risk')}: {probPct}</span>
          <span className={`mc-pa-risk-chip ${chipClass}`}>
            {signal.riskLevel.toUpperCase()}
          </span>
        </div>

        <div className="mc-pa-meta">
          <span>{t('ai.model')}: {signal.modelId}</span>
          <span>{t('ai.horizon')}: 12 {t('ai.candles')}</span>
          <span>TP 0.4% / SL 0.25%</span>
        </div>
      </div>

      <div className="mc-ai-disclaimer mc-pa-disclaimer">
        {t('ai.research.warning')}
      </div>
    </>
  );
}

// ── SuperTrend sub-panel (research placeholder) ───────────────────────────────
// This mode is intentionally not wired up to any in-app signal generator.
// The rule-based SuperTrend candidate must clear the offline backtest gate
// (see ml/strategies/) before the app surfaces it as a usable signal.

function SupertrendPanel() {
  const { t } = useTranslation();
  return (
    <>
      <div className="mc-ai-body mc-st-body">
        <div className="mc-ai-row">
          <span className="mc-ai-direction mc-ai-dir-notrade">
            {t('ai.supertrend.research')}
          </span>
          <span className="mc-pa-risk-chip mc-pa-risk-elevated">
            {t('ai.supertrend.notValidated')}
          </span>
        </div>
        <div className="mc-ai-meta">
          <span>{t('ai.supertrend.runBacktest')}</span>
        </div>
      </div>
      <div className="mc-ai-disclaimer mc-pa-disclaimer">
        {t('ai.supertrend.disclaimer')}
      </div>
    </>
  );
}
