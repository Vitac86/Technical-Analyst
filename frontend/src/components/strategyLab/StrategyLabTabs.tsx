export type StrategyLabTabId =
  | "monitor"
  | "backtest"
  | "signals"
  | "robot"
  | "research";

export interface StrategyLabTab {
  id: StrategyLabTabId;
  label: string;
  /** Optional short hint shown under the label (e.g. running state). */
  hint?: string;
  /** Optional badge tone for a live indicator dot. */
  live?: boolean;
}

interface Props {
  tabs: StrategyLabTab[];
  active: StrategyLabTabId;
  onChange: (id: StrategyLabTabId) => void;
}

/**
 * Simple, accessible tab bar for the trading-first Strategy Lab layout.
 * Trading Monitor is the default tab; the rest hold the heavier research and
 * control surfaces so the main page stays compact.
 */
export function StrategyLabTabs({ tabs, active, onChange }: Props) {
  return (
    <div className="sl-tabs" role="tablist" aria-label="Strategy Lab views">
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            className={`sl-tab${selected ? " sl-tab-active" : ""}`}
            onClick={() => onChange(tab.id)}
          >
            <span className="sl-tab-label">
              {tab.live ? <span className="sl-tab-live" aria-hidden /> : null}
              {tab.label}
            </span>
            {tab.hint ? <span className="sl-tab-hint">{tab.hint}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
