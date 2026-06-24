import { useMemo } from "react";

import { CostScenarioSelector } from "./CostScenarioSelector";
import { MetricCards } from "./MetricCards";
import { ParameterPanel } from "./ParameterPanel";
import { PeriodTables } from "./PeriodTables";
import { PresetSelector } from "./PresetSelector";
import { StrategyChart } from "./StrategyChart";
import { TradesTable } from "./TradesTable";
import type {
  BacktestResponse,
  CostScenario,
  CustomCosts,
  ParamValues,
  Preset,
} from "../../types/strategyLab";

interface Props {
  presets: Preset[];
  costScenarios: CostScenario[];
  selectedId: string;
  selectedPreset: Preset;
  params: ParamValues;
  costScenario: string;
  customCosts: CustomCosts;
  startDate: string;
  endDate: string;
  result: BacktestResponse | null;
  running: boolean;
  runError: string | null;
  exporting: boolean;
  exportError: string | null;
  onSelectPreset: (presetId: string) => void;
  onParamChange: (key: string, value: number | null) => void;
  onResetParams: () => void;
  onCostScenario: (scenario: string) => void;
  onCustomChange: (key: keyof CustomCosts, value: number) => void;
  onStartDate: (value: string) => void;
  onEndDate: (value: string) => void;
  onRun: () => void;
  onExport: () => void;
}

/**
 * Research / backtest surface. Holds the heavier preset → parameter → backtest
 * → charts → trades flow so the day-to-day Trading Monitor stays compact.
 */
export function BacktestPanel({
  presets,
  costScenarios,
  selectedId,
  selectedPreset,
  params,
  costScenario,
  customCosts,
  startDate,
  endDate,
  result,
  running,
  runError,
  exporting,
  exportError,
  onSelectPreset,
  onParamChange,
  onResetParams,
  onCostScenario,
  onCustomChange,
  onStartDate,
  onEndDate,
  onRun,
  onExport,
}: Props) {
  const equitySeries = useMemo(
    () => [
      {
        data: (result?.equity_curve ?? []).map((p) => ({ t: p.t, value: p.equity })),
        lineColor: "#56ccf2",
        topColor: "rgba(86, 204, 242, 0.35)",
        bottomColor: "rgba(86, 204, 242, 0.02)",
      },
    ],
    [result],
  );

  const drawdownSeries = useMemo(
    () => [
      {
        data: (result?.drawdown_series ?? []).map((p) => ({
          t: p.t,
          value: p.drawdown_pct === null ? null : -p.drawdown_pct,
        })),
        lineColor: "#eb5757",
        topColor: "rgba(235, 87, 87, 0.05)",
        bottomColor: "rgba(235, 87, 87, 0.35)",
      },
    ],
    [result],
  );

  return (
    <div className="sl-tab-panel">
      {/* A. Preset selector */}
      <PresetSelector
        presets={presets}
        selectedId={selectedId}
        onSelect={onSelectPreset}
        disabled={running}
      />

      {/* Preset guidance */}
      <div className="sl-guidance">
        <p className="sl-guidance-use">
          <span className="sl-tag sl-tag-status">{selectedPreset.research_status}</span>{" "}
          {selectedPreset.recommended_use}
        </p>
        {selectedPreset.warning_notes.length > 0 ? (
          <ul className="sl-warnings">
            {selectedPreset.warning_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {/* B + C. Controls */}
      <div className="sl-controls">
        <ParameterPanel
          preset={selectedPreset}
          values={params}
          onChange={onParamChange}
          onReset={onResetParams}
          disabled={running}
        />

        <div className="sl-controls-side">
          <CostScenarioSelector
            scenario={costScenario}
            onScenario={onCostScenario}
            customCosts={customCosts}
            onCustomChange={onCustomChange}
            catalogue={costScenarios}
            disabled={running}
          />

          <div className="sl-date-row">
            <label className="sl-field">
              <span className="sl-field-label">From (optional)</span>
              <input
                type="date"
                className="sl-input"
                value={startDate}
                disabled={running}
                onChange={(e) => onStartDate(e.target.value)}
              />
            </label>
            <label className="sl-field">
              <span className="sl-field-label">To (optional)</span>
              <input
                type="date"
                className="sl-input"
                value={endDate}
                disabled={running}
                onChange={(e) => onEndDate(e.target.value)}
              />
            </label>
          </div>

          {/* D + J. Actions */}
          <div className="sl-actions">
            <button className="btn btn-primary" onClick={onRun} disabled={running}>
              {running ? "Running…" : "Run backtest"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={onExport}
              disabled={running || exporting}
              title="Download this configuration as JSON for a later MT5 robot / signal bridge"
            >
              {exporting ? "Exporting…" : "Export config (JSON)"}
            </button>
          </div>
          {exportError ? (
            <div className="chart-state chart-state-error">{exportError}</div>
          ) : null}
        </div>
      </div>

      {/* Run error */}
      {runError ? <div className="chart-state chart-state-error">{runError}</div> : null}

      {/* Results */}
      {result ? (
        <div className="sl-results">
          <div className="sl-results-head">
            <span className="sl-results-meta">
              {result.display_name} · {result.symbol} {result.timeframe} ·{" "}
              {result.cost_scenario} costs ·{" "}
              {result.data_range.start?.slice(0, 10) ?? "—"} →{" "}
              {result.data_range.end?.slice(0, 10) ?? "—"} ({result.data_range.bars} bars)
            </span>
          </div>

          {result.warnings.length > 0 ? (
            <div className="chart-state chart-state-warn">
              {result.warnings.join(" ")}
            </div>
          ) : null}

          {/* E. Summary metric cards */}
          <MetricCards metrics={result.summary} />

          {/* F + G. Charts */}
          <div className="sl-charts">
            <StrategyChart
              title="Equity curve"
              series={equitySeries}
              precision={0}
              emptyMessage="No equity data."
            />
            <StrategyChart
              title="Drawdown (% below peak)"
              series={drawdownSeries}
              precision={1}
              emptyMessage="No drawdown data."
            />
          </div>

          {/* I. Yearly / walk-forward */}
          <PeriodTables
            yearly={result.yearly_summary}
            walkForward={result.walk_forward_summary}
          />

          {/* H. Trades */}
          <section className="panel sl-trades-panel">
            <div className="panel-header">
              <h2>Trades</h2>
            </div>
            <TradesTable
              trades={result.trades}
              total={result.trades_total}
              truncated={result.trades_truncated}
            />
          </section>

          <p className="ts-disclaimer">
            {result.research_disclaimer} ML filter disabled by default.
          </p>
        </div>
      ) : null}
    </div>
  );
}
