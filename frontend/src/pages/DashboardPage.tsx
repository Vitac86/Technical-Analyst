import { IndicatorPanel } from "../components/charts/IndicatorPanel";
import { PriceChart } from "../components/charts/PriceChart";
import { InstrumentList } from "../components/dashboard/InstrumentList";
import { SignalSummary } from "../components/dashboard/SignalSummary";

export function DashboardPage() {
  return (
    <div className="dashboard-grid">
      <InstrumentList />
      <PriceChart />
      <SignalSummary />
      <IndicatorPanel />
    </div>
  );
}
