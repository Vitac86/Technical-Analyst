from datetime import datetime, timezone, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd


SYMBOL = "XAUUSDrfd"

DATE_FROM = datetime(2015, 1, 1, tzinfo=timezone.utc)
DATE_TO = datetime.now(timezone.utc)

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "mt5_exports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TIMEFRAMES = {
    "M5": {
        "value": mt5.TIMEFRAME_M5,
        "chunk_days": 30,
    },
    "M15": {
        "value": mt5.TIMEFRAME_M15,
        "chunk_days": 90,
    },
    "H1": {
        "value": mt5.TIMEFRAME_H1,
        "chunk_days": 180,
    },
    "H4": {
        "value": mt5.TIMEFRAME_H4,
        "chunk_days": 365,
    },
    "D1": {
        "value": mt5.TIMEFRAME_D1,
        "chunk_days": 3650,
    },
}


def print_terminal_info() -> None:
    terminal_info = mt5.terminal_info()
    account_info = mt5.account_info()
    symbol_info = mt5.symbol_info(SYMBOL)

    print("MT5 terminal:", terminal_info.name if terminal_info else "N/A")
    print("MT5 path:", terminal_info.path if terminal_info else "N/A")
    print("Account:", account_info.login if account_info else "N/A")
    print("Symbol:", SYMBOL)

    if symbol_info is None:
        print(f"Symbol info not found for {SYMBOL}")
    else:
        print("Symbol visible:", symbol_info.visible)
        print("Symbol digits:", symbol_info.digits)
        print("Symbol spread:", symbol_info.spread)

    print("-" * 80)


def rates_to_dataframe(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)

    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

    df = df.rename(
        columns={
            "time": "datetime",
            "tick_volume": "volume",
        }
    )

    columns = [
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "spread",
        "real_volume",
    ]

    return df[columns]


def export_rates(symbol: str, timeframe_name: str, timeframe_value: int, chunk_days: int) -> None:
    print(f"Exporting {symbol} {timeframe_name}...")

    chunks = []
    chunk_start = DATE_FROM
    total_chunks = 0
    failed_chunks = 0

    while chunk_start < DATE_TO:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), DATE_TO)

        rates = mt5.copy_rates_range(
            symbol,
            timeframe_value,
            chunk_start,
            chunk_end,
        )

        total_chunks += 1

        if rates is None:
            failed_chunks += 1
            print(
                f"  No data/error for {timeframe_name}: "
                f"{chunk_start.date()} -> {chunk_end.date()} | Error: {mt5.last_error()}"
            )
        elif len(rates) > 0:
            df_chunk = rates_to_dataframe(rates)
            chunks.append(df_chunk)

        chunk_start = chunk_end

    if not chunks:
        print(f"No data collected for {symbol} {timeframe_name}. Failed chunks: {failed_chunks}/{total_chunks}")
        print("-" * 80)
        return

    df = pd.concat(chunks, ignore_index=True)

    df = df.drop_duplicates(subset=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)

    output_path = OUTPUT_DIR / f"{symbol}_{timeframe_name}.csv"
    df.to_csv(output_path, index=False, encoding="utf-8")

    print(f"Saved {len(df):,} rows to {output_path}")
    print(f"First datetime: {df['datetime'].min()}")
    print(f"Last datetime:  {df['datetime'].max()}")
    print(f"Failed chunks:  {failed_chunks}/{total_chunks}")
    print("-" * 80)


if __name__ == "__main__":
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    if not mt5.symbol_select(SYMBOL, True):
        mt5.shutdown()
        raise RuntimeError(f"Cannot select symbol {SYMBOL}: {mt5.last_error()}")

    print_terminal_info()

    for tf_name, config in TIMEFRAMES.items():
        export_rates(
            symbol=SYMBOL,
            timeframe_name=tf_name,
            timeframe_value=config["value"],
            chunk_days=config["chunk_days"],
        )

    mt5.shutdown()