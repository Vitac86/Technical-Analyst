import { MT5ExecutionRobotPanel } from "./MT5ExecutionRobotPanel";
import type { BacktestRequest } from "../../types/strategyLab";

interface Props {
  buildConfigBody: () => BacktestRequest;
  disabled?: boolean;
}

/**
 * Demo Robot tab — the detailed execution control center (safety checklist,
 * dry-run and demo-only execution controls, latest decision, full execution
 * history and logs). Demo-only; live trading is disabled.
 */
export function DemoRobotPanel({ buildConfigBody, disabled }: Props) {
  return (
    <div className="sl-tab-panel">
      <MT5ExecutionRobotPanel
        buildConfigBody={buildConfigBody}
        disabled={disabled}
        defaultOpen
      />
    </div>
  );
}
