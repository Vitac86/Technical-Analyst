// Types mirroring the Strategy Lab v1.6 backend API (/api/strategy-lab).

export interface AllowedRange {
  type: "int" | "float";
  min?: number;
  max?: number;
  nullable?: boolean;
}

export interface Preset {
  preset_id: string;
  display_name: string;
  description: string;
  strategy_name: string;
  family: string;
  timeframe: string;
  direction_mode: string;
  exit_mode: string;
  sizing_mode: string;
  default_parameters: Record<string, number | null>;
  allowed_ranges: Record<string, AllowedRange>;
  research_status: string;
  recommended_use: string;
  warning_notes: string[];
  is_default: boolean;
}

export interface CostScenario {
  name: string;
  fixed_spread_points: number;
  slippage_points: number;
  commission_per_lot_round_turn: number;
  swap_long_per_lot_per_day: number;
  swap_short_per_lot_per_day: number;
}

export interface PresetsResponse {
  presets: Preset[];
  default_preset_id: string;
  cost_scenarios: CostScenario[];
  ml_filter_enabled: boolean;
  ml_note: string;
  disclaimer: string;
}

export interface CustomCosts {
  fixed_spread_points: number;
  slippage_points: number;
  commission_per_lot_round_turn: number;
  swap_long_per_lot_per_day: number;
  swap_short_per_lot_per_day: number;
}

export interface SummaryMetrics {
  initial_equity: number | null;
  final_equity: number | null;
  total_return_pct: number | null;
  net_profit: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number | null;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number | null;
  average_r: number | null;
  median_r: number | null;
  average_lots: number | null;
  max_effective_leverage: number | null;
  average_effective_leverage: number | null;
  stop_out_count: number;
  insufficient_margin_count: number;
}

export interface EquityPoint {
  t: number;
  equity: number | null;
  balance: number | null;
}

export interface DrawdownPoint {
  t: number;
  drawdown_pct: number | null;
}

export interface TradeRow {
  entry_time: string | null;
  exit_time: string | null;
  direction: string;
  lots: number | null;
  entry_price: number | null;
  exit_price: number | null;
  exit_reason: string;
  bars_held: number;
  net_pnl: number | null;
  r_multiple: number | null;
  balance_after_trade: number | null;
}

export interface PeriodRow {
  period: string;
  return_pct: number | null;
  max_drawdown_pct: number | null;
  trades: number;
  profit_factor: number | null;
  net_profit: number | null;
}

export interface ParametersOut {
  strategy: Record<string, number | null>;
  exit: Record<string, number | null>;
  risk: Record<string, number | null>;
}

export interface BacktestResponse {
  preset_id: string;
  display_name: string;
  symbol: string;
  timeframe: string;
  strategy_name: string;
  direction_mode: string;
  exit_mode: string;
  sizing_mode: string;
  cost_scenario: string;
  cost_assumptions: Record<string, number | null>;
  parameters: ParametersOut;
  summary: SummaryMetrics;
  equity_curve: EquityPoint[];
  drawdown_series: DrawdownPoint[];
  trades: TradeRow[];
  trades_total: number;
  trades_truncated: boolean;
  yearly_summary: PeriodRow[];
  walk_forward_summary: PeriodRow[];
  data_range: { start: string | null; end: string | null; bars: number };
  warnings: string[];
  research_disclaimer: string;
  ml_note: string;
}

export interface BacktestRequest {
  preset_id: string;
  symbol?: string;
  timeframe?: string | null;
  cost_scenario?: string;
  custom_costs?: CustomCosts | null;
  start?: string | null;
  end?: string | null;
  trades_limit?: number;
  equity_points?: number;
  // tunable parameters (preset-relevant subset)
  [param: string]: number | string | boolean | null | undefined | CustomCosts;
}

export type ParamValues = Record<string, number | null>;

// ---------------------------------------------------------------------------
// v1.7.1 MT5 signal-only bridge (no execution; see strategy_lab_signals API)
// ---------------------------------------------------------------------------

/** The portable strategy config object produced by /export-config. */
export type StrategyConfig = Record<string, unknown>;

export interface SignalConfigSummary {
  strategy_id: string | null;
  symbol: string | null;
  timeframe: string | null;
  direction_mode: string | null;
  ml_filter_enabled: boolean;
}

export interface SaveSignalConfigResponse {
  file_name: string;
  path: string;
  config_summary: SignalConfigSummary;
  execution_enabled: boolean;
}

export interface SavedSignalConfig {
  file_name: string;
  path: string;
  strategy_id: string | null;
  symbol: string | null;
  timeframe: string | null;
  created_at: string | null;
  modified_at: string | null;
  ml_filter_enabled: boolean;
}

export interface SignalConfigsResponse {
  configs: SavedSignalConfig[];
  execution_enabled: boolean;
}

export type Mt5ReadinessStatus = "ok" | "warning" | "error";

export interface Mt5Readiness {
  status: Mt5ReadinessStatus;
  mt5_package_available: boolean;
  terminal_connected: boolean;
  account_connected: boolean;
  symbol: string | null;
  timeframe: string | null;
  rates_available: boolean;
  bars_fetched: number;
  latest_closed_candle_time: string | null;
  message: string;
  execution_enabled: boolean;
}

export interface SignalRecord {
  signal_id: string;
  generated_at: string;
  symbol: string;
  timeframe: string;
  strategy_id: string;
  signal_time: string;
  signal_type: string; // "BUY" | "NONE"
  reason: string;
  close_price: number | string | null;
  atr_value: number | string | null;
  suggested_entry_reference: string;
  risk_percent: number | string | null;
  initial_stop_loss_atr: number | string | null;
  trailing_stop_atr: number | string | null;
  take_profit_atr: number | string | null;
  status: string;
  execution_enabled: boolean | string;
}

export interface LatestSignalResponse {
  signal: SignalRecord | null;
  execution_enabled: boolean;
}

export interface SignalHistoryResponse {
  signals: SignalRecord[];
  count: number;
  execution_enabled: boolean;
}

export interface CheckOnceResponse {
  ok: boolean;
  emitted: boolean;
  signal: SignalRecord | null;
  stdout: string;
  stderr: string;
  execution_enabled: boolean;
}

export interface BridgeProcessStatus {
  running: boolean;
  pid: number | null;
  started_at: string | null;
  config_path: string | null;
  poll_seconds: number | null;
  bars: number | null;
  status: string;
  started?: boolean;
  stopped?: boolean;
  message?: string;
  execution_enabled: boolean;
  // present on GET /status
  latest_signal?: SignalRecord | null;
  latest_signal_time?: string | null;
  latest_log_excerpt?: string;
  error_excerpt?: string | null;
}

export interface SignalLogsResponse {
  stdout_tail: string;
  stderr_tail: string;
  execution_enabled: boolean;
}
