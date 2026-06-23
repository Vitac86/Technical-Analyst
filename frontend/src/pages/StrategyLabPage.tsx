import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { exportConfig, getPresets, runBacktest } from "../api/strategyLab";
import { CostScenarioSelector } from "../components/strategyLab/CostScenarioSelector";
import { MetricCards } from "../components/strategyLab/MetricCards";
import { ParameterPanel } from "../components/strategyLab/ParameterPanel";
import { PeriodTables } from "../components/strategyLab/PeriodTables";
import { PresetSelector } from "../components/strategyLab/PresetSelector";
import { StrategyChart } from "../components/strategyLab/StrategyChart";
import { TradesTable } from "../components/strategyLab/TradesTable";
import type {
  BacktestRequest,
  BacktestResponse,
  CustomCosts,
  ParamValues,
  Preset,
  PresetsResponse,
} from "../types/strategyLab";

const DEFAULT_CUSTOM_COSTS: CustomCosts = {
  fixed_spread_points: 30,
  slippage_points: 0,
  commission_per_lot_round_turn: 0,
  swap_long_per_lot_per_day: 0,
  swap_short_per_lot_per_day: 0,
};

function defaultsFor(preset: Preset): ParamValues {
  return { ...preset.default_parameters };
}

export function StrategyLabPage() {
  const [presetsData, setPresetsData] = useState<PresetsResponse | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string>("");
  const [params, setParams] = useState<ParamValues>({});
  const [costScenario, setCostScenario] = useState("Base");
  const [customCosts, setCustomCosts] = useState<CustomCosts>(DEFAULT_CUSTOM_COSTS);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const autoRan = useRef(false);

  const selectedPreset = useMemo(
    () => presetsData?.presets.find((p) => p.preset_id === selectedId) ?? null,
    [presetsData, selectedId],
  );

  // --- Load presets once ---
  useEffect(() => {
    let cancelled = false;
    getPresets()
      .then((data) => {
        if (cancelled) return;
        setPresetsData(data);
        const def =
          data.presets.find((p) => p.preset_id === data.default_preset_id) ??
          data.presets[0];
        if (def) {
          setSelectedId(def.preset_id);
          setParams(defaultsFor(def));
        }
        const base = data.cost_scenarios.find((c) => c.name === "Base");
        if (base) {
          setCustomCosts({
            fixed_spread_points: base.fixed_spread_points,
            slippage_points: base.slippage_points,
            commission_per_lot_round_turn: base.commission_per_lot_round_turn,
            swap_long_per_lot_per_day: base.swap_long_per_lot_per_day,
            swap_short_per_lot_per_day: base.swap_short_per_lot_per_day,
          });
        }
      })
      .catch((err) =>
        setPresetsError(err instanceof Error ? err.message : "Failed to load presets."),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  const buildConfigBody = useCallback((): BacktestRequest => {
    const body: BacktestRequest = {
      preset_id: selectedId,
      symbol: "XAUUSD",
      cost_scenario: costScenario,
      ...params,
    };
    if (costScenario === "Custom") body.custom_costs = customCosts;
    return body;
  }, [selectedId, costScenario, params, customCosts]);

  const handleRun = useCallback(async () => {
    if (!selectedId) return;
    setRunning(true);
    setRunError(null);
    try {
      const body = buildConfigBody();
      if (startDate) body.start = startDate;
      if (endDate) body.end = endDate;
      const data = await runBacktest(body);
      setResult(data);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Backtest failed.");
    } finally {
      setRunning(false);
    }
  }, [selectedId, buildConfigBody, startDate, endDate]);

  // --- Auto-run the default preset once after presets load ---
  useEffect(() => {
    if (autoRan.current || !selectedPreset) return;
    autoRan.current = true;
    void handleRun();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPreset]);

  function handleSelectPreset(presetId: string) {
    const preset = presetsData?.presets.find((p) => p.preset_id === presetId);
    if (!preset) return;
    setSelectedId(presetId);
    setParams(defaultsFor(preset));
    setResult(null);
    setRunError(null);
  }

  function handleParamChange(key: string, value: number | null) {
    setParams((prev) => ({ ...prev, [key]: value }));
  }

  function handleResetParams() {
    if (selectedPreset) setParams(defaultsFor(selectedPreset));
  }

  async function handleExport() {
    if (!selectedId) return;
    setExporting(true);
    setExportError(null);
    try {
      await exportConfig(buildConfigBody());
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setExporting(false);
    }
  }

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

  if (presetsError) {
    return (
      <div className="page-content">
        <div className="chart-state chart-state-error">{presetsError}</div>
      </div>
    );
  }

  if (!presetsData || !selectedPreset) {
    return (
      <div className="page-content">
        <div className="chart-state">Loading Strategy Lab…</div>
      </div>
    );
  }

  return (
    <div className="page-content sl-page">
      <div className="page-header">
        <h2>Rule-Based Strategy Lab</h2>
        <p className="page-subtitle">
          Backtest, inspect and export the confirmed rule-based XAUUSD finalists.
        </p>
      </div>

      {/* Disclaimers */}
      <div className="sl-disclaimer">
        <strong>Research &amp; backtesting only.</strong> {presetsData.disclaimer}
      </div>
      <div className="sl-ml-note">ℹ️ {presetsData.ml_note}</div>

      {/* A. Preset selector */}
      <PresetSelector
        presets={presetsData.presets}
        selectedId={selectedId}
        onSelect={handleSelectPreset}
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
          onChange={handleParamChange}
          onReset={handleResetParams}
          disabled={running}
        />

        <div className="sl-controls-side">
          <CostScenarioSelector
            scenario={costScenario}
            onScenario={setCostScenario}
            customCosts={customCosts}
            onCustomChange={(key, value) =>
              setCustomCosts((prev) => ({ ...prev, [key]: value }))
            }
            catalogue={presetsData.cost_scenarios}
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
                onChange={(e) => setStartDate(e.target.value)}
              />
            </label>
            <label className="sl-field">
              <span className="sl-field-label">To (optional)</span>
              <input
                type="date"
                className="sl-input"
                value={endDate}
                disabled={running}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </label>
          </div>

          {/* D + J. Actions */}
          <div className="sl-actions">
            <button className="btn btn-primary" onClick={handleRun} disabled={running}>
              {running ? "Running…" : "Run backtest"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleExport}
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
