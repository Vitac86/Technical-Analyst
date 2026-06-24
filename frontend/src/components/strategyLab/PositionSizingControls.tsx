import type { ExecutionSizing, ExecutionSizingMode } from "../../types/strategyLab";
import {
  WARN_CAPPED_BY_MAX_LOT,
  WARN_MANUAL_LOT_ROUNDED,
} from "../../types/strategyLab";
import { fmtMoney, fmtNum } from "./format";
import type { PositionSizing } from "./usePositionSizing";

interface Props {
  sizing: PositionSizing;
  /** risk_percent from the selected config (shown in Auto risk % mode). */
  configRiskPercent?: number | null;
  /** The latest decision's sizing block (read-outs after a dry-run). */
  decisionSizing?: ExecutionSizing | null;
  disabled?: boolean;
}

const MODE_OPTIONS: { value: ExecutionSizingMode; label: string }[] = [
  { value: "risk_percent_auto", label: "Auto risk %" },
  { value: "fixed_lot_manual", label: "Manual lot" },
  { value: "risk_percent_with_max_lot", label: "Auto risk % with max lot" },
];

const MANUAL_QUICK = [0.01, 0.03, 0.05, 0.1];
const MAX_QUICK = [0.01, 0.05, 0.1];

function fmtLot(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(2)}%`;
}

/**
 * Compact "Position sizing" block shared by the Demo Robot panel and the Trading
 * Monitor. Lets the operator size by auto risk %, a manual fixed lot, or auto
 * risk % capped by a max lot. Read-outs update after a dry-run. Demo-only; this
 * never adds live trading and never weakens the demo safety gates.
 */
export function PositionSizingControls({
  sizing,
  configRiskPercent,
  decisionSizing,
  disabled,
}: Props) {
  const ds = decisionSizing;
  const sameMode = ds?.execution_sizing_mode === sizing.mode;
  const rounded = (ds?.sizing_warnings ?? []).includes(WARN_MANUAL_LOT_ROUNDED);
  const capped =
    (ds?.sizing_warnings ?? []).includes(WARN_CAPPED_BY_MAX_LOT) ||
    Boolean(ds?.capped_by_max_lot);

  return (
    <div className="ps-block">
      <div className="ps-head">
        <span className="ps-title">Position sizing</span>
        <label className="ps-mode">
          <select
            className="sl-input"
            value={sizing.mode}
            disabled={disabled}
            onChange={(e) => sizing.setMode(e.target.value as ExecutionSizingMode)}
          >
            {MODE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {sizing.mode === "risk_percent_auto" ? (
        <div className="ps-body">
          <p className="ps-note">
            Auto risk-percent sizing from the strategy config
            {configRiskPercent != null ? ` (risk ${fmtNum(configRiskPercent, 2)}%)` : ""}.
          </p>
          {sameMode ? (
            <dl className="ps-readout">
              <div>
                <dt>Calculated lot</dt>
                <dd>{fmtLot(ds?.final_lot ?? ds?.rounded_lot)}</dd>
              </div>
              <div>
                <dt>Final risk</dt>
                <dd>
                  {fmtMoney(ds?.final_risk_amount)} · {fmtPct(ds?.final_risk_percent)}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="ps-hint">Run Dry-run once to see the calculated lot.</p>
          )}
        </div>
      ) : null}

      {sizing.mode === "fixed_lot_manual" ? (
        <div className="ps-body">
          <label className="sl-field ps-field">
            <span className="sl-field-label">Manual lot</span>
            <input
              type="number"
              className="sl-input"
              min={0.01}
              step={0.01}
              value={sizing.manualLot}
              disabled={disabled}
              onChange={(e) => sizing.setManualLot(e.target.value)}
            />
          </label>
          <div className="ps-quick">
            {MANUAL_QUICK.map((lot) => (
              <button
                key={lot}
                type="button"
                className="btn btn-ghost ps-quick-btn"
                disabled={disabled}
                onClick={() => sizing.setManualLot(lot.toFixed(2))}
              >
                {lot.toFixed(2)}
              </button>
            ))}
          </div>
          {sizing.manualLotInvalid ? (
            <p className="chart-state chart-state-error ps-error">
              Enter a manual lot greater than 0.
            </p>
          ) : null}
          <label className="sl-field ps-field ps-risk-field">
            <span className="sl-field-label">Max manual risk %</span>
            <input
              type="number"
              className="sl-input"
              min={0.1}
              step={0.1}
              value={sizing.maxManualRiskPercent}
              disabled={disabled}
              onChange={(e) => sizing.setMaxManualRiskPercent(e.target.value)}
            />
          </label>
          <label className="erp-check-inline ps-allow">
            <input
              type="checkbox"
              checked={sizing.allowHighManualRisk}
              disabled={disabled}
              onChange={(e) => sizing.setAllowHighManualRisk(e.target.checked)}
            />
            Allow high manual risk
          </label>
          <p className="ps-warn">
            Manual lot overrides risk-percent sizing. Check implied risk before
            demo execution.
          </p>
          <p className="ps-warn ps-warn-strong">
            Manual lot can risk more than the strategy’s configured risk %. Use
            dry-run first.
          </p>
          {sameMode ? (
            <dl className="ps-readout">
              <div>
                <dt>Resolved lot</dt>
                <dd>
                  {fmtLot(ds?.final_lot)}
                  {rounded ? " (rounded to step)" : ""}
                </dd>
              </div>
              <div>
                <dt>Implied risk</dt>
                <dd>
                  {fmtMoney(ds?.implied_risk_amount)} ·{" "}
                  {fmtPct(ds?.implied_risk_percent)}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="ps-hint">Run Dry-run once to see the implied risk.</p>
          )}
        </div>
      ) : null}

      {sizing.mode === "risk_percent_with_max_lot" ? (
        <div className="ps-body">
          <label className="sl-field ps-field">
            <span className="sl-field-label">Max lot (cap)</span>
            <input
              type="number"
              className="sl-input"
              min={0.01}
              step={0.01}
              value={sizing.maxLot}
              disabled={disabled}
              onChange={(e) => sizing.setMaxLot(e.target.value)}
            />
          </label>
          <div className="ps-quick">
            {MAX_QUICK.map((lot) => (
              <button
                key={lot}
                type="button"
                className="btn btn-ghost ps-quick-btn"
                disabled={disabled}
                onClick={() => sizing.setMaxLot(lot.toFixed(2))}
              >
                {lot.toFixed(2)}
              </button>
            ))}
          </div>
          <p className="ps-note">
            Auto risk-percent sizing, capped at the max lot. Leave blank for no cap.
          </p>
          {sameMode ? (
            <dl className="ps-readout">
              <div>
                <dt>Auto lot → final</dt>
                <dd>
                  {fmtLot(ds?.auto_lot_before_cap)} → {fmtLot(ds?.final_lot)}
                </dd>
              </div>
              <div>
                <dt>Capped by max lot</dt>
                <dd>{capped ? "yes" : "no"}</dd>
              </div>
            </dl>
          ) : (
            <p className="ps-hint">Run Dry-run once to see whether the lot was capped.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
