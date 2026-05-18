import { Link } from "react-router-dom";

import { InstrumentList } from "../components/dashboard/InstrumentList";
import { SignalSummary } from "../components/dashboard/SignalSummary";

export function DashboardPage() {
  return (
    <div className="dashboard-grid">
      <InstrumentList />
      <section className="panel dashboard-chart-card">
        <div className="panel-header">
          <h2>Chart Workspace</h2>
          <span className="panel-meta">1d</span>
        </div>
        <p>SBER daily chart, candles, and stored indicators.</p>
        <Link className="primary-link" to="/chart">
          Open chart
        </Link>
      </section>
      <SignalSummary />
    </div>
  );
}
