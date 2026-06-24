import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { exportConfig, getPresets, runBacktest } from "../api/strategyLab";
import { BacktestPanel } from "../components/strategyLab/BacktestPanel";
import { DemoRobotPanel } from "../components/strategyLab/DemoRobotPanel";
import { ResearchNotesPanel } from "../components/strategyLab/ResearchNotesPanel";
import { SignalsPanel } from "../components/strategyLab/SignalsPanel";
import {
  StrategyLabTabs,
  type StrategyLabTab,
  type StrategyLabTabId,
} from "../components/strategyLab/StrategyLabTabs";
import { TradingMonitorPanel } from "../components/strategyLab/TradingMonitorPanel";
import type {
  BacktestRequest,
  BacktestResponse,
  CustomCosts,
  ParamValues,
  Preset,
  PresetsResponse,
} from "../types/strategyLab";

const TABS: StrategyLabTab[] = [
  { id: "monitor", label: "Trading Monitor" },
  { id: "backtest", label: "Backtest" },
  { id: "signals", label: "Signals" },
  { id: "robot", label: "Demo Robot" },
  { id: "research", label: "Research Notes" },
];

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

  const [activeTab, setActiveTab] = useState<StrategyLabTabId>("monitor");

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

      {/* Single compact warning strip (full disclaimers live in Research Notes). */}
      <div className="sl-warning-strip">
        Research/backtesting and demo-only execution. Live trading is disabled.
      </div>

      <StrategyLabTabs tabs={TABS} active={activeTab} onChange={setActiveTab} />

      {activeTab === "monitor" ? (
        <TradingMonitorPanel buildConfigBody={buildConfigBody} disabled={running} />
      ) : null}

      {activeTab === "backtest" ? (
        <BacktestPanel
          presets={presetsData.presets}
          costScenarios={presetsData.cost_scenarios}
          selectedId={selectedId}
          selectedPreset={selectedPreset}
          params={params}
          costScenario={costScenario}
          customCosts={customCosts}
          startDate={startDate}
          endDate={endDate}
          result={result}
          running={running}
          runError={runError}
          exporting={exporting}
          exportError={exportError}
          onSelectPreset={handleSelectPreset}
          onParamChange={handleParamChange}
          onResetParams={handleResetParams}
          onCostScenario={setCostScenario}
          onCustomChange={(key, value) =>
            setCustomCosts((prev) => ({ ...prev, [key]: value }))
          }
          onStartDate={setStartDate}
          onEndDate={setEndDate}
          onRun={handleRun}
          onExport={handleExport}
        />
      ) : null}

      {activeTab === "signals" ? (
        <SignalsPanel buildConfigBody={buildConfigBody} disabled={running} />
      ) : null}

      {activeTab === "robot" ? (
        <DemoRobotPanel buildConfigBody={buildConfigBody} disabled={running} />
      ) : null}

      {activeTab === "research" ? (
        <ResearchNotesPanel
          presets={presetsData.presets}
          disclaimer={presetsData.disclaimer}
          mlNote={presetsData.ml_note}
        />
      ) : null}
    </div>
  );
}
