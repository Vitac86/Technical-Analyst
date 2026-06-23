import type { SummaryMetrics } from "../../types/strategyLab";
import {
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtProfitFactor,
  fmtRatePct,
  signClass,
} from "./format";

type Card = { label: string; value: string; tone?: string; hint?: string };

function buildCards(m: SummaryMetrics): Card[] {
  return [
    { label: "Final equity", value: fmtMoney(m.final_equity) },
    {
      label: "Total return",
      value: fmtPct(m.total_return_pct),
      tone: signClass(m.total_return_pct),
    },
    {
      label: "Net profit",
      value: fmtMoney(m.net_profit),
      tone: signClass(m.net_profit),
    },
    {
      label: "Profit factor",
      value: fmtProfitFactor(m.profit_factor, m.net_profit, m.total_trades),
    },
    {
      label: "Max drawdown",
      value: m.max_drawdown_pct === null ? "—" : `${fmtNum(m.max_drawdown_pct, 1)}%`,
      tone: "sl-neg",
    },
    { label: "Trades", value: String(m.total_trades) },
    { label: "Win rate", value: fmtRatePct(m.win_rate) },
    { label: "Average R", value: fmtNum(m.average_r), tone: signClass(m.average_r) },
    {
      label: "Max effective leverage",
      value: m.max_effective_leverage === null ? "—" : `${fmtNum(m.max_effective_leverage, 2)}×`,
    },
    {
      label: "Stop-outs",
      value: String(m.stop_out_count),
      tone: m.stop_out_count > 0 ? "sl-neg" : undefined,
    },
  ];
}

export function MetricCards({ metrics }: { metrics: SummaryMetrics }) {
  return (
    <div className="sl-metric-grid">
      {buildCards(metrics).map((card) => (
        <div key={card.label} className="sl-metric-card">
          <span className="sl-metric-label">{card.label}</span>
          <span className={`sl-metric-value ${card.tone ?? ""}`}>{card.value}</span>
        </div>
      ))}
    </div>
  );
}
