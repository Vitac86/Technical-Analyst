import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { IndicatorPanel } from "../components/charts/IndicatorPanel";
import { PriceChart } from "../components/charts/PriceChart";
import { getCandles } from "../api/candles";
import { getIndicatorValues } from "../api/indicators";
import { getInstruments } from "../api/instruments";
import type { Candle } from "../types/candle";
import type { IndicatorValue } from "../types/indicator";
import type { Instrument } from "../types/instrument";

const timeframe = "1d";
const defaultIndicatorNames = [
  "sma_20",
  "ema_20",
  "bollinger_bands_20_2",
  "rsi_14",
  "macd_12_26_9",
] as const;

type IndicatorName = (typeof defaultIndicatorNames)[number];
type IndicatorMap = Record<IndicatorName, IndicatorValue[]>;

export function InstrumentPage() {
  const { ticker } = useParams();
  const navigate = useNavigate();
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [selectedInstrumentId, setSelectedInstrumentId] = useState<number | null>(
    null,
  );
  const [candles, setCandles] = useState<Candle[]>([]);
  const [indicators, setIndicators] = useState<IndicatorMap>(() =>
    createEmptyIndicators(),
  );
  const [instrumentLoading, setInstrumentLoading] = useState(true);
  const [marketLoading, setMarketLoading] = useState(false);
  const [instrumentError, setInstrumentError] = useState<string | null>(null);
  const [marketError, setMarketError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadInstruments() {
      setInstrumentLoading(true);
      setInstrumentError(null);

      try {
        const rows = await getInstruments();

        if (cancelled) {
          return;
        }

        setInstruments(rows);
        setSelectedInstrumentId((current) => {
          if (current !== null && rows.some((instrument) => instrument.id === current)) {
            return current;
          }

          const routeTicker = ticker?.toUpperCase();
          const selected =
            rows.find((instrument) => instrument.ticker.toUpperCase() === routeTicker) ??
            rows.find((instrument) => instrument.ticker.toUpperCase() === "SBER") ??
            rows[0];

          return selected?.id ?? null;
        });
      } catch (error) {
        if (!cancelled) {
          setInstrumentError(errorMessage(error, "Failed to load instruments."));
        }
      } finally {
        if (!cancelled) {
          setInstrumentLoading(false);
        }
      }
    }

    void loadInstruments();

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  useEffect(() => {
    if (instruments.length === 0 || ticker === undefined) {
      return;
    }

    const routeInstrument = instruments.find(
      (instrument) => instrument.ticker.toUpperCase() === ticker.toUpperCase(),
    );

    if (routeInstrument !== undefined) {
      setSelectedInstrumentId(routeInstrument.id);
    }
  }, [instruments, ticker]);

  useEffect(() => {
    if (selectedInstrumentId === null) {
      setCandles([]);
      setIndicators(createEmptyIndicators());
      return;
    }

    let cancelled = false;
    const instrumentId = selectedInstrumentId;

    async function loadMarketData() {
      setMarketLoading(true);
      setMarketError(null);

      try {
        const [nextCandles, ...indicatorRows] = await Promise.all([
          getCandles(instrumentId, timeframe),
          ...defaultIndicatorNames.map((indicatorName) =>
            getIndicatorValues(instrumentId, indicatorName, timeframe),
          ),
        ]);

        if (cancelled) {
          return;
        }

        setCandles(nextCandles);
        setIndicators(
          Object.fromEntries(
            defaultIndicatorNames.map((indicatorName, index) => [
              indicatorName,
              indicatorRows[index] ?? [],
            ]),
          ) as IndicatorMap,
        );
      } catch (error) {
        if (!cancelled) {
          setCandles([]);
          setIndicators(createEmptyIndicators());
          setMarketError(errorMessage(error, "Failed to load chart data."));
        }
      } finally {
        if (!cancelled) {
          setMarketLoading(false);
        }
      }
    }

    void loadMarketData();

    return () => {
      cancelled = true;
    };
  }, [selectedInstrumentId]);

  const selectedInstrument = useMemo(
    () =>
      instruments.find((instrument) => instrument.id === selectedInstrumentId) ??
      null,
    [instruments, selectedInstrumentId],
  );

  const indicatorRowCount = defaultIndicatorNames.reduce(
    (count, indicatorName) => count + indicators[indicatorName].length,
    0,
  );

  function handleInstrumentChange(nextId: string) {
    const instrumentId = Number(nextId);
    const nextInstrument = instruments.find(
      (instrument) => instrument.id === instrumentId,
    );

    setSelectedInstrumentId(Number.isFinite(instrumentId) ? instrumentId : null);

    if (nextInstrument !== undefined) {
      navigate(`/instruments/${nextInstrument.ticker}`);
    }
  }

  return (
    <div className="instrument-page">
      <section className="page-heading instrument-heading">
        <div>
          <p className="eyebrow">Instrument</p>
          <h2>
            {selectedInstrument?.ticker ?? "Chart"}
            {selectedInstrument?.name ? (
              <span>{selectedInstrument.name}</span>
            ) : null}
          </h2>
        </div>
        <div className="instrument-controls">
          <label htmlFor="instrument-select">Ticker</label>
          <select
            id="instrument-select"
            value={selectedInstrumentId ?? ""}
            onChange={(event) => handleInstrumentChange(event.target.value)}
            disabled={instrumentLoading || instruments.length === 0}
          >
            {instruments.map((instrument) => (
              <option key={instrument.id} value={instrument.id}>
                {instrument.ticker} - {instrument.name}
              </option>
            ))}
          </select>
        </div>
      </section>
      {instrumentError ? (
        <div className="page-alert" role="alert">
          {instrumentError}
        </div>
      ) : null}
      <p className="status-line">
        Candles: {candles.length} | Indicator rows: {indicatorRowCount} | Timeframe:{" "}
        {timeframe}
      </p>
      <PriceChart
        candles={candles}
        timeframe={timeframe}
        indicators={{
          sma20: indicators.sma_20,
          ema20: indicators.ema_20,
          bollingerBands: indicators.bollinger_bands_20_2,
        }}
        loading={instrumentLoading || marketLoading}
        error={marketError}
      />
      <div className="indicator-panels">
        <IndicatorPanel
          title="RSI 14"
          values={indicators.rsi_14}
          timeframe={timeframe}
          variant="rsi"
          loading={marketLoading}
          error={marketError}
        />
        <IndicatorPanel
          title="MACD 12 26 9"
          values={indicators.macd_12_26_9}
          timeframe={timeframe}
          variant="macd"
          loading={marketLoading}
          error={marketError}
        />
      </div>
    </div>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function createEmptyIndicators(): IndicatorMap {
  return {
    sma_20: [],
    ema_20: [],
    bollinger_bands_20_2: [],
    rsi_14: [],
    macd_12_26_9: [],
  };
}
