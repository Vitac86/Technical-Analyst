import { useParams } from "react-router-dom";

import { IndicatorPanel } from "../components/charts/IndicatorPanel";
import { PriceChart } from "../components/charts/PriceChart";
import { SignalSummary } from "../components/dashboard/SignalSummary";

export function InstrumentPage() {
  const { ticker } = useParams();

  return (
    <div className="instrument-page">
      <section className="page-heading">
        <p className="eyebrow">Instrument</p>
        <h2>{ticker ?? "Unknown"}</h2>
      </section>
      <div className="instrument-grid">
        <PriceChart />
        <SignalSummary />
        <IndicatorPanel />
      </div>
    </div>
  );
}
