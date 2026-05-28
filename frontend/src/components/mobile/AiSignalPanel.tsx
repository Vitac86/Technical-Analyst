import type { AiSignalResult } from '../../ml/types';

interface AiSignalPanelProps {
  signal: AiSignalResult | null;
}

export function AiSignalPanel({ signal }: AiSignalPanelProps) {
  const header = (
    <div className="mc-ai-header">
      <span className="mc-ai-title">AI Signal</span>
      <span className="mc-ai-badge">Experimental</span>
    </div>
  );

  if (!signal) {
    return (
      <div className="mc-ai-panel">
        {header}
        <div className="mc-ai-unavailable">Calculating…</div>
      </div>
    );
  }

  if (!signal.available) {
    return (
      <div className="mc-ai-panel">
        {header}
        <div className="mc-ai-unavailable">{signal.reason ?? 'Signal unavailable'}</div>
        <div className="mc-ai-disclaimer">Experimental local signal. Not financial advice.</div>
      </div>
    );
  }

  const dirClass =
    signal.direction === 'LONG'  ? 'mc-ai-dir-long'    :
    signal.direction === 'SHORT' ? 'mc-ai-dir-short'   :
                                   'mc-ai-dir-notrade';

  const dirLabel = signal.direction === 'NO_TRADE' ? 'NO TRADE' : signal.direction;

  const upPct   = Math.round(signal.probabilities.up   * 100);
  const downPct = Math.round(signal.probabilities.down * 100);
  const flatPct = Math.round(signal.probabilities.flat * 100);

  return (
    <div className="mc-ai-panel">
      {header}
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
    </div>
  );
}
