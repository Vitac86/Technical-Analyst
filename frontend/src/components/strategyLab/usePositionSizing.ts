import { useMemo, useState } from "react";

import type { ExecutionSizingOptions } from "../../api/strategyLab";
import type { ExecutionSizing, ExecutionSizingMode } from "../../types/strategyLab";
import {
  SIZING_WARNING_MANUAL_RISK_TOO_HIGH,
  WARN_MANUAL_RISK_HIGH,
} from "../../types/strategyLab";

/**
 * Shared position-sizing state for the demo execution controls (used by both the
 * Demo Robot panel and the Trading Monitor). Owns the three sizing modes and
 * exposes the API options plus syntactic validity so callers can gate buttons.
 */
export interface PositionSizing {
  mode: ExecutionSizingMode;
  setMode: (mode: ExecutionSizingMode) => void;
  manualLot: string;
  setManualLot: (value: string) => void;
  maxLot: string;
  setMaxLot: (value: string) => void;
  maxManualRiskPercent: string;
  setMaxManualRiskPercent: (value: string) => void;
  allowHighManualRisk: boolean;
  setAllowHighManualRisk: (value: boolean) => void;
  /** Serializable options to spread into the execution API calls. */
  options: ExecutionSizingOptions;
  /** Inputs are syntactically valid for the active mode (dry-run gate). */
  valid: boolean;
  /** A manual lot is required (manual mode) but isn't a positive number. */
  manualLotInvalid: boolean;
}

export function usePositionSizing(): PositionSizing {
  const [mode, setMode] = useState<ExecutionSizingMode>("risk_percent_auto");
  const [manualLot, setManualLot] = useState("0.05");
  const [maxLot, setMaxLot] = useState("0.10");
  const [maxManualRiskPercent, setMaxManualRiskPercent] = useState("3.0");
  const [allowHighManualRisk, setAllowHighManualRisk] = useState(false);

  const manualLotNum = Number(manualLot);
  const manualLotValid = Number.isFinite(manualLotNum) && manualLotNum > 0;
  const maxLotNum = Number(maxLot);
  const maxLotEmpty = maxLot.trim() === "";
  const maxLotValid = maxLotEmpty || (Number.isFinite(maxLotNum) && maxLotNum > 0);
  const riskNum = Number(maxManualRiskPercent);
  const riskValid = Number.isFinite(riskNum) && riskNum > 0;

  const valid = useMemo(() => {
    if (mode === "fixed_lot_manual") return manualLotValid && riskValid;
    if (mode === "risk_percent_with_max_lot") return maxLotValid;
    return true;
  }, [mode, manualLotValid, maxLotValid, riskValid]);

  const options: ExecutionSizingOptions = useMemo(
    () => ({
      executionSizingMode: mode,
      manualLot:
        mode === "fixed_lot_manual" && manualLotValid ? manualLotNum : null,
      maxLot:
        mode === "risk_percent_with_max_lot" && !maxLotEmpty && maxLotValid
          ? maxLotNum
          : null,
      maxManualRiskPercent: riskValid ? riskNum : 3.0,
      allowHighManualRisk,
    }),
    [
      mode,
      manualLotValid,
      manualLotNum,
      maxLotEmpty,
      maxLotValid,
      maxLotNum,
      riskValid,
      riskNum,
      allowHighManualRisk,
    ],
  );

  return {
    mode,
    setMode,
    manualLot,
    setManualLot,
    maxLot,
    setMaxLot,
    maxManualRiskPercent,
    setMaxManualRiskPercent,
    allowHighManualRisk,
    setAllowHighManualRisk,
    options,
    valid,
    manualLotInvalid: mode === "fixed_lot_manual" && !manualLotValid,
  };
}

/** Whether the latest decision's sizing flags a high implied manual risk. */
export function decisionFlagsHighManualRisk(
  sizing: ExecutionSizing | null | undefined,
): boolean {
  if (!sizing) return false;
  return (
    sizing.sizing_status === SIZING_WARNING_MANUAL_RISK_TOO_HIGH ||
    (sizing.sizing_warnings ?? []).includes(WARN_MANUAL_RISK_HIGH)
  );
}
