import type { Preset } from "../../types/strategyLab";

interface Props {
  presets: Preset[];
  disclaimer: string;
  mlNote: string;
}

/**
 * Research Notes tab — collects every disclaimer and strategy explanation away
 * from the day-to-day trading surface so the other tabs stay compact.
 */
export function ResearchNotesPanel({ presets, disclaimer, mlNote }: Props) {
  return (
    <div className="sl-tab-panel sl-research">
      <div className="sl-disclaimer">
        <strong>Research &amp; backtesting only.</strong> {disclaimer}
      </div>

      <div className="sl-ml-note">ℹ️ {mlNote}</div>

      <section className="panel sl-research-section">
        <div className="panel-header">
          <h2>Strategy explanations</h2>
        </div>
        <div className="sl-research-strategies">
          {presets.map((preset) => (
            <article key={preset.preset_id} className="sl-research-card">
              <div className="sl-research-card-head">
                <span className="sl-preset-name">{preset.display_name}</span>
                <span className="sl-preset-tf">{preset.timeframe}</span>
              </div>
              <p className="sl-research-desc">{preset.description}</p>
              <div className="sl-preset-tags">
                <span className="sl-tag sl-tag-status">{preset.research_status}</span>
                <span className="sl-tag">{preset.direction_mode}</span>
                <span className="sl-tag">{preset.exit_mode}</span>
                <span className="sl-tag">{preset.sizing_mode}</span>
              </div>
              <p className="sl-research-use">{preset.recommended_use}</p>
              {preset.warning_notes.length > 0 ? (
                <ul className="sl-warnings">
                  {preset.warning_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </div>
      </section>

      <section className="panel sl-research-section">
        <div className="panel-header">
          <h2>Safety &amp; demo-only</h2>
        </div>
        <div className="sl-research-safety">
          <p>
            <strong>Live trading is disabled.</strong> The Signal Bridge runs in
            signal-only mode and never sends, modifies or closes orders. The Demo
            Execution Robot only acts on a <strong>detected demo account</strong>,
            opens a single BUY on a fresh long signal, trails the stop upward, and
            never closes a position.
          </p>
          <p>
            There is <strong>no SELL / SHORT</strong> in v1.8 and no “go live”
            switch. Dry-run is the default for the robot; demo execution requires
            two explicit confirmations and a demo account.
          </p>
          <p className="sl-research-mlnote">
            The ML filter is disabled by default. All presets here are confirmed
            rule-based finalists; results are backtests and are not a promise of
            future performance.
          </p>
        </div>
      </section>
    </div>
  );
}
