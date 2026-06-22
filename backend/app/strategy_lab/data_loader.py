"""Load and validate MetaTrader 5 historical CSV exports.

The loader never mutates the source CSV files. It returns a clean, sorted,
UTC-indexed-by-column DataFrame ready for indicator and backtest code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Columns that must be present for the data to be usable as candles.
REQUIRED_OHLC_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")

# Columns we keep when present, but that are not strictly required.
OPTIONAL_COLUMNS: tuple[str, ...] = ("volume", "spread", "real_volume")

# Recognised MT5 timeframe suffixes (used only to parse the file name).
_KNOWN_TIMEFRAMES: frozenset[str] = frozenset(
    {"M1", "M5", "M15", "M30", "H1", "H2", "H4", "H8", "H12", "D1", "W1", "MN1"}
)

# e.g. "XAUUSDrfd_H1" -> symbol="XAUUSDrfd", timeframe="H1"
_FILENAME_RE = re.compile(r"^(?P<symbol>.+)_(?P<timeframe>[A-Za-z]+\d+)$")


def parse_symbol_timeframe(file_path: str | Path) -> tuple[str | None, str | None]:
    """Best-effort parse of ``SYMBOL_TIMEFRAME`` from a CSV file name.

    Returns ``(symbol, timeframe)``. Either element may be ``None`` when the
    name does not follow the expected convention.
    """
    stem = Path(file_path).stem
    match = _FILENAME_RE.match(stem)
    if match is None:
        return None, None
    symbol = match.group("symbol")
    timeframe = match.group("timeframe").upper()
    if timeframe not in _KNOWN_TIMEFRAMES:
        # Still informative, but flag that it is not a known MT5 timeframe.
        return symbol, timeframe
    return symbol, timeframe


def _validate_ohlc(df: pd.DataFrame, source_name: str) -> None:
    """Raise ``ValueError`` if OHLC data is missing or internally inconsistent."""
    ohlc = df[list(REQUIRED_OHLC_COLUMNS)]
    if ohlc.isna().any().any():
        raise ValueError(f"NaN values found in OHLC columns of {source_name}")

    invalid_high_low = df["high"] < df["low"]
    if bool(invalid_high_low.any()):
        count = int(invalid_high_low.sum())
        raise ValueError(f"{count} rows with high < low in {source_name}")


def load_mt5_csv(
    file_path: str | Path,
    *,
    datetime_column: str = "datetime",
    validate: bool = True,
) -> pd.DataFrame:
    """Load a single MT5 CSV export into a clean DataFrame.

    The returned frame is sorted ascending by ``datetime`` (parsed as UTC),
    de-duplicated on the timestamp, and carries ``symbol``/``timeframe`` columns
    parsed from the file name when possible.

    Parameters
    ----------
    file_path:
        Path to the CSV file. The file is read only; it is never modified.
    datetime_column:
        Name of the timestamp column in the CSV (default ``"datetime"``).
    validate:
        When ``True`` (default), run basic OHLC sanity checks.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"MT5 CSV not found: {path}")

    df = pd.read_csv(path)
    # Normalise column names so the loader is tolerant of casing/whitespace.
    df.columns = [str(c).strip().lower() for c in df.columns]

    if datetime_column not in df.columns:
        raise ValueError(
            f"Missing '{datetime_column}' column in {path.name}; "
            f"found columns: {list(df.columns)}"
        )

    missing = [c for c in REQUIRED_OHLC_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required OHLC columns {missing} in {path.name}")

    # Parse timestamps as timezone-aware UTC.
    df["datetime"] = pd.to_datetime(df[datetime_column], utc=True, errors="coerce")
    if bool(df["datetime"].isna().any()):
        bad = int(df["datetime"].isna().sum())
        raise ValueError(f"{bad} unparseable datetime value(s) in {path.name}")

    # Coerce numeric columns; OHLC must be numeric, optional columns when present.
    for col in (*REQUIRED_OHLC_COLUMNS, *OPTIONAL_COLUMNS):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.sort_values("datetime")
        .drop_duplicates(subset="datetime", keep="last")
        .reset_index(drop=True)
    )

    symbol, timeframe = parse_symbol_timeframe(path)
    df["symbol"] = symbol
    df["timeframe"] = timeframe

    if validate:
        _validate_ohlc(df, path.name)

    ordered = [
        "datetime",
        *REQUIRED_OHLC_COLUMNS,
        *[c for c in OPTIONAL_COLUMNS if c in df.columns],
        "symbol",
        "timeframe",
    ]
    return df.loc[:, ordered].copy()
