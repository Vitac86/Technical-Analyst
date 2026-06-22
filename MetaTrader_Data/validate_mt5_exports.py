from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = [
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "spread",
    "real_volume",
]

NUMERIC_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "spread",
    "real_volume",
]

REPORT_COLUMNS = [
    "file_name",
    "symbol",
    "timeframe",
    "status",
    "error",
    "row_count",
    "first_datetime",
    "last_datetime",
    "duplicate_datetime_count",
    "missing_value_count",
    "invalid_ohlc_row_count",
    "average_spread",
    "median_spread",
    "min_spread",
    "max_spread",
    "expected_interval",
    "unusually_large_gap_count",
    "maximum_observed_gap",
]

# Gaps larger than 1.5 expected bars are reported. This allows for small
# timestamp irregularities while still identifying missing-bar-sized gaps.
LARGE_GAP_MULTIPLIER = 1.5


def parse_filename(csv_path: Path) -> tuple[str, str]:
    """Return the symbol and timeframe from <symbol>_<timeframe>.csv."""
    symbol, separator, timeframe = csv_path.stem.rpartition("_")
    timeframe = timeframe.upper()

    if not separator or not symbol or not timeframe:
        raise ValueError("filename must follow <symbol>_<timeframe>.csv")

    if not re.fullmatch(r"(?:MN|M|H|D|W)\d+", timeframe):
        raise ValueError(f"unsupported timeframe name: {timeframe}")

    return symbol, timeframe


def expected_interval(timeframe: str) -> pd.Timedelta:
    """Convert an MT5 timeframe name to an approximate pandas interval."""
    match = re.fullmatch(r"(MN|M|H|D|W)(\d+)", timeframe)
    if match is None:
        raise ValueError(f"unsupported timeframe name: {timeframe}")

    unit, count_text = match.groups()
    count = int(count_text)

    if count <= 0:
        raise ValueError(f"timeframe interval must be positive: {timeframe}")

    interval_units = {
        "M": "minutes",
        "H": "hours",
        "D": "days",
        "W": "weeks",
        # Calendar months vary in length; 30 days is sufficient for gap
        # estimation if an MN timeframe export is added later.
        "MN": "days",
    }
    multiplier = 30 if unit == "MN" else 1

    return pd.Timedelta(**{interval_units[unit]: count * multiplier})


def empty_report_row(csv_path: Path) -> dict[str, Any]:
    """Create a report row with stable columns for a failed file."""
    return {
        "file_name": csv_path.name,
        "symbol": "",
        "timeframe": "",
        "status": "failed",
        "error": "",
        "row_count": 0,
        "first_datetime": "",
        "last_datetime": "",
        "duplicate_datetime_count": 0,
        "missing_value_count": 0,
        "invalid_ohlc_row_count": 0,
        "average_spread": pd.NA,
        "median_spread": pd.NA,
        "min_spread": pd.NA,
        "max_spread": pd.NA,
        "expected_interval": "",
        "unusually_large_gap_count": 0,
        "maximum_observed_gap": "",
    }


def format_datetime(value: pd.Timestamp | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return value.isoformat()


def calculate_spread_statistics(spread: pd.Series) -> dict[str, Any]:
    valid_spread = spread.dropna()
    if valid_spread.empty:
        return {
            "average_spread": pd.NA,
            "median_spread": pd.NA,
            "min_spread": pd.NA,
            "max_spread": pd.NA,
        }

    return {
        "average_spread": float(valid_spread.mean()),
        "median_spread": float(valid_spread.median()),
        "min_spread": float(valid_spread.min()),
        "max_spread": float(valid_spread.max()),
    }


def validate_csv(csv_path: Path) -> dict[str, Any]:
    """Validate one MT5 export and return a single summary report row."""
    result = empty_report_row(csv_path)

    try:
        symbol, timeframe = parse_filename(csv_path)
        interval = expected_interval(timeframe)
        result["symbol"] = symbol
        result["timeframe"] = timeframe
        result["expected_interval"] = str(interval)

        dataframe = pd.read_csv(csv_path)
        result["row_count"] = len(dataframe)

        if dataframe.empty:
            raise ValueError("CSV contains no data rows")

        dataframe.columns = dataframe.columns.str.strip()
        missing_columns = [
            column for column in REQUIRED_COLUMNS if column not in dataframe.columns
        ]
        if missing_columns:
            raise ValueError(
                "missing required columns: " + ", ".join(missing_columns)
            )

        validated = dataframe.loc[:, REQUIRED_COLUMNS].copy()
        validated["datetime"] = pd.to_datetime(
            validated["datetime"],
            errors="coerce",
            utc=True,
        )

        for column in NUMERIC_COLUMNS:
            validated[column] = pd.to_numeric(
                validated[column],
                errors="coerce",
            )

        validated = validated.sort_values(
            "datetime",
            kind="stable",
            na_position="last",
        ).reset_index(drop=True)

        valid_datetimes = validated["datetime"].dropna()
        result["first_datetime"] = format_datetime(
            valid_datetimes.min() if not valid_datetimes.empty else None
        )
        result["last_datetime"] = format_datetime(
            valid_datetimes.max() if not valid_datetimes.empty else None
        )
        result["duplicate_datetime_count"] = int(
            valid_datetimes.duplicated().sum()
        )
        result["missing_value_count"] = int(
            validated[REQUIRED_COLUMNS].isna().sum().sum()
        )

        complete_ohlc = validated[["open", "high", "low", "close"]].notna().all(
            axis=1
        )
        invalid_ohlc = complete_ohlc & (
            (validated["high"] < validated["low"])
            | (validated["high"] < validated["open"])
            | (validated["high"] < validated["close"])
            | (validated["low"] > validated["open"])
            | (validated["low"] > validated["close"])
            | (validated[["open", "high", "low", "close"]] <= 0).any(axis=1)
        )
        result["invalid_ohlc_row_count"] = int(invalid_ohlc.sum())

        result.update(calculate_spread_statistics(validated["spread"]))

        unique_datetimes = valid_datetimes.drop_duplicates().sort_values()
        observed_gaps = unique_datetimes.diff().dropna()
        if not observed_gaps.empty:
            large_gap_threshold = interval * LARGE_GAP_MULTIPLIER
            result["unusually_large_gap_count"] = int(
                (observed_gaps > large_gap_threshold).sum()
            )
            result["maximum_observed_gap"] = str(observed_gaps.max())

        result["status"] = "success"
        result["error"] = ""

    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as error:
        result["status"] = "failed"
        result["error"] = str(error)
    except Exception as error:
        # Keep processing the remaining files if pandas encounters an
        # unexpected file-specific problem.
        result["status"] = "failed"
        result["error"] = f"{type(error).__name__}: {error}"

    return result


def display_value(value: Any) -> str:
    if value is pd.NA or pd.isna(value):
        return "N/A"
    if isinstance(value, float):
        return f"{value:,.4f}"
    return str(value)


def print_file_summary(result: dict[str, Any]) -> None:
    print("=" * 80)
    identity = result["file_name"]
    if result["symbol"] and result["timeframe"]:
        identity += f" ({result['symbol']} {result['timeframe']})"

    print(identity)
    print(f"Status: {result['status']}")

    if result["status"] == "failed":
        print(f"Error: {result['error']}")
        return

    print(
        f"Rows: {result['row_count']:,} | "
        f"Range: {result['first_datetime']} -> {result['last_datetime']}"
    )
    print(
        f"Duplicates: {result['duplicate_datetime_count']:,} | "
        f"Missing values: {result['missing_value_count']:,} | "
        f"Invalid OHLC rows: {result['invalid_ohlc_row_count']:,}"
    )
    print(
        "Spread (avg / median / min / max): "
        f"{display_value(result['average_spread'])} / "
        f"{display_value(result['median_spread'])} / "
        f"{display_value(result['min_spread'])} / "
        f"{display_value(result['max_spread'])}"
    )
    print(
        f"Expected interval: {result['expected_interval']} | "
        f"Large gaps: {result['unusually_large_gap_count']:,} | "
        f"Maximum gap: {result['maximum_observed_gap'] or 'N/A'}"
    )


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    export_dir = script_dir / "mt5_exports"
    report_path = script_dir / "reports" / "data_quality_report.csv"

    if not export_dir.is_dir():
        print(f"Error: MT5 export directory does not exist: {export_dir}")
        return 1

    csv_files = sorted(
        export_dir.glob("*.csv"),
        key=lambda path: path.name.lower(),
    )
    if not csv_files:
        print(f"Error: no CSV files found in: {export_dir}")
        return 1

    results = []
    for csv_path in csv_files:
        result = validate_csv(csv_path)
        results.append(result)
        print_file_summary(result)

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(results, columns=REPORT_COLUMNS).to_csv(
            report_path,
            index=False,
            encoding="utf-8",
        )
    except OSError as error:
        print("=" * 80)
        print(f"Error: could not save report to {report_path}: {error}")
        return 1

    failed_count = sum(result["status"] == "failed" for result in results)
    print("=" * 80)
    if failed_count == 0:
        print(f"All {len(results)} files processed successfully.")
        print(f"Report saved to: {report_path}")
        return 0

    print(
        f"Processed {len(results)} files with {failed_count} failure(s). "
        f"Review the report: {report_path}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
