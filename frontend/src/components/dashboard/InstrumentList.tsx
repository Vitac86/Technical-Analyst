import { Link } from "react-router-dom";

const placeholderInstruments = [
  { ticker: "SBER", name: "Sberbank" },
  { ticker: "GAZP", name: "Gazprom" },
  { ticker: "LKOH", name: "Lukoil" },
];

export function InstrumentList() {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Instruments</h2>
        <span className="panel-meta">MOEX first</span>
      </div>
      <div className="instrument-list">
        {placeholderInstruments.map((instrument) => (
          <Link
            className="instrument-row"
            key={instrument.ticker}
            to={`/instruments/${instrument.ticker}`}
          >
            <span>{instrument.ticker}</span>
            <small>{instrument.name}</small>
          </Link>
        ))}
      </div>
    </section>
  );
}
