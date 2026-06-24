import { MT5SignalBridgePanel } from "./MT5SignalBridgePanel";
import type { BacktestRequest } from "../../types/strategyLab";

interface Props {
  buildConfigBody: () => BacktestRequest;
  disabled?: boolean;
}

/**
 * Signals tab — the full MT5 Signal Bridge (saved configs, readiness, signal
 * actions, latest signal, recent checks, signal history and logs). Signal-only;
 * no orders are ever placed here.
 */
export function SignalsPanel({ buildConfigBody, disabled }: Props) {
  return (
    <div className="sl-tab-panel">
      <MT5SignalBridgePanel buildConfigBody={buildConfigBody} disabled={disabled} />
    </div>
  );
}
