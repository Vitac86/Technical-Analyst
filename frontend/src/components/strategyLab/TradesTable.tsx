import type { TradeRow } from "../../types/strategyLab";
import { fmtDateTime, fmtMoney, fmtNum, signClass } from "./format";

type Props = {
  trades: TradeRow[];
  total: number;
  truncated: boolean;
};

export function TradesTable({ trades, total, truncated }: Props) {
  if (trades.length === 0) {
    return <p className="chart-state">No trades for this configuration.</p>;
  }

  return (
    <div className="sl-trades-wrap">
      <div className="sl-trades-meta">
        Showing {trades.length} of {total} trades
        {truncated ? " (capped — refine the date range or raise the limit)" : ""}
      </div>
      <div className="sl-table-scroll">
        <table className="sl-table">
          <thead>
            <tr>
              <th>Entry</th>
              <th>Exit</th>
              <th>Dir</th>
              <th>Lots</th>
              <th>Entry px</th>
              <th>Exit px</th>
              <th>Reason</th>
              <th>Bars</th>
              <th>Net P&L</th>
              <th>R</th>
              <th>Balance</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => (
              <tr key={`${t.entry_time}-${i}`}>
                <td>{fmtDateTime(t.entry_time)}</td>
                <td>{fmtDateTime(t.exit_time)}</td>
                <td>
                  <span className={`sl-dir sl-dir-${t.direction}`}>{t.direction}</span>
                </td>
                <td>{fmtNum(t.lots, 2)}</td>
                <td>{fmtNum(t.entry_price, 2)}</td>
                <td>{fmtNum(t.exit_price, 2)}</td>
                <td>
                  <span className="sl-reason">{t.exit_reason}</span>
                </td>
                <td>{t.bars_held}</td>
                <td className={signClass(t.net_pnl)}>{fmtMoney(t.net_pnl, 2)}</td>
                <td className={signClass(t.r_multiple)}>{fmtNum(t.r_multiple, 2)}</td>
                <td>{fmtMoney(t.balance_after_trade, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
