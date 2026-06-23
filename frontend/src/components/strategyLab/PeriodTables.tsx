import type { PeriodRow } from "../../types/strategyLab";
import { fmtMoney, fmtNum, fmtPct, fmtProfitFactor, signClass } from "./format";

function PeriodTable({ title, rows }: { title: string; rows: PeriodRow[] }) {
  return (
    <div className="sl-period-block">
      <span className="form-label">{title}</span>
      {rows.length === 0 ? (
        <p className="chart-state">No data.</p>
      ) : (
        <div className="sl-table-scroll">
          <table className="sl-table">
            <thead>
              <tr>
                <th>Period</th>
                <th>Return</th>
                <th>Max DD</th>
                <th>Trades</th>
                <th>PF</th>
                <th>Net profit</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.period}>
                  <td>{row.period}</td>
                  <td className={signClass(row.return_pct)}>{fmtPct(row.return_pct)}</td>
                  <td>
                    {row.max_drawdown_pct === null
                      ? "—"
                      : `${fmtNum(row.max_drawdown_pct, 1)}%`}
                  </td>
                  <td>{row.trades}</td>
                  <td>{fmtProfitFactor(row.profit_factor, row.net_profit, row.trades)}</td>
                  <td className={signClass(row.net_profit)}>{fmtMoney(row.net_profit, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type Props = {
  yearly: PeriodRow[];
  walkForward: PeriodRow[];
};

export function PeriodTables({ yearly, walkForward }: Props) {
  return (
    <div className="sl-period-grid">
      <PeriodTable title="Yearly breakdown" rows={yearly} />
      <PeriodTable title="Walk-forward periods" rows={walkForward} />
    </div>
  );
}
