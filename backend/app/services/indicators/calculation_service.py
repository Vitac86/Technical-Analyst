from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import candles as candle_repository
from app.repositories import indicators as indicator_repository
from app.services.indicators.candle_frame import candles_to_dataframe
from app.services.indicators.registry import get_indicator, get_indicator_category


@dataclass(frozen=True)
class IndicatorSpec:
    registry_name: str
    stored_name: str
    category: str
    params: dict[str, Any]
    minimum_rows: int


DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "sma": {"window": 20},
    "ema": {"window": 20},
    "rsi": {"window": 14},
    "macd": {"fast_period": 12, "slow_period": 26, "signal_period": 9},
    "bollinger_bands": {"window": 20, "standard_deviations": 2.0},
    "atr": {"window": 14},
}

DEFAULT_INDICATORS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("sma", {"window": 20}),
    ("ema", {"window": 20}),
    ("rsi", {"window": 14}),
    ("macd", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("bollinger_bands", {"window": 20, "standard_deviations": 2.0}),
    ("atr", {"window": 14}),
)


def calculate_indicator_for_instrument(
    db: Session,
    instrument_id: int,
    timeframe: str,
    indicator_name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate and persist one indicator for stored candles."""
    try:
        summary = _calculate_indicator_for_instrument(
            db,
            instrument_id=instrument_id,
            timeframe=timeframe,
            indicator_name=indicator_name,
            params=params,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return summary


def calculate_default_indicators_for_instrument(
    db: Session,
    instrument_id: int,
    timeframe: str,
) -> dict[str, Any]:
    """Calculate and persist the default indicator set for stored candles."""
    try:
        results = [
            _calculate_indicator_for_instrument(
                db,
                instrument_id=instrument_id,
                timeframe=timeframe,
                indicator_name=registry_name,
                params=params,
            )
            for registry_name, params in DEFAULT_INDICATORS
        ]
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "indicator_names": [result["indicator_name"] for result in results],
        "indicators": results,
    }


def _calculate_indicator_for_instrument(
    db: Session,
    *,
    instrument_id: int,
    timeframe: str,
    indicator_name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = resolve_indicator_spec(indicator_name, params)
    indicator = get_indicator(spec.registry_name)
    if indicator is None:
        raise ValueError(f"Unsupported indicator: {indicator_name}.")

    candles = candle_repository.list_candles(
        db,
        instrument_id=instrument_id,
        timeframe=timeframe,
    )
    candle_frame = candles_to_dataframe(candles)
    candle_count = len(candle_frame)
    if candle_count == 0:
        raise ValueError(
            f"No valid candles found for instrument {instrument_id} and {timeframe}.",
        )
    if candle_count < spec.minimum_rows:
        raise ValueError(
            f"{spec.stored_name} requires at least {spec.minimum_rows} candles; "
            f"found {candle_count}.",
        )

    indicator_values = indicator(candle_frame, **spec.params)
    persistence_summary = indicator_repository.bulk_upsert_indicator_values(
        db,
        instrument_id=instrument_id,
        indicator_name=spec.stored_name,
        category=spec.category,
        timeframe=timeframe,
        timestamps=candle_frame["timestamp"],
        values=indicator_values,
    )

    persisted_rows = (
        persistence_summary["inserted"]
        + persistence_summary["updated"]
        + persistence_summary["unchanged"]
    )
    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "indicator_name": spec.stored_name,
        "registry_name": spec.registry_name,
        "category": spec.category,
        "params": spec.params,
        "candles": candle_count,
        "persisted_rows": persisted_rows,
        "persistence": persistence_summary,
    }


def resolve_indicator_spec(
    indicator_name: str,
    params: dict[str, Any] | None = None,
) -> IndicatorSpec:
    registry_name, parsed_params = _parse_indicator_name(indicator_name)
    defaults = DEFAULT_PARAMS.get(registry_name)
    if defaults is None:
        raise ValueError(f"Unsupported indicator: {indicator_name}.")

    final_params = defaults | parsed_params | (params or {})
    final_params = _coerce_indicator_params(registry_name, final_params)
    stored_name = build_indicator_name(registry_name, final_params)
    category = get_indicator_category(registry_name)
    if category is None:
        raise ValueError(f"Unsupported indicator: {indicator_name}.")

    return IndicatorSpec(
        registry_name=registry_name,
        stored_name=stored_name,
        category=category,
        params=final_params,
        minimum_rows=_minimum_rows(registry_name, final_params),
    )


def build_indicator_name(registry_name: str, params: dict[str, Any]) -> str:
    if registry_name in {"sma", "ema", "rsi", "atr"}:
        return f"{registry_name}_{params['window']}"
    if registry_name == "macd":
        return (
            f"macd_{params['fast_period']}_"
            f"{params['slow_period']}_{params['signal_period']}"
        )
    if registry_name == "bollinger_bands":
        deviations = _format_number_for_name(params["standard_deviations"])
        return f"bollinger_bands_{params['window']}_{deviations}"
    raise ValueError(f"Unsupported indicator: {registry_name}.")


def _parse_indicator_name(indicator_name: str) -> tuple[str, dict[str, Any]]:
    normalized = indicator_name.strip().lower()
    if normalized in DEFAULT_PARAMS:
        return normalized, {}

    if normalized.startswith("bollinger_bands_"):
        raw_params = normalized.removeprefix("bollinger_bands_").split("_")
        if len(raw_params) >= 2:
            return (
                "bollinger_bands",
                {
                    "window": int(raw_params[0]),
                    "standard_deviations": _parse_name_number(raw_params[1:]),
                },
            )
    if normalized.startswith("macd_"):
        raw_params = normalized.removeprefix("macd_").split("_")
        if len(raw_params) == 3:
            return (
                "macd",
                {
                    "fast_period": int(raw_params[0]),
                    "slow_period": int(raw_params[1]),
                    "signal_period": int(raw_params[2]),
                },
            )

    for registry_name in ("sma", "ema", "rsi", "atr"):
        prefix = f"{registry_name}_"
        if normalized.startswith(prefix):
            return registry_name, {"window": int(normalized.removeprefix(prefix))}

    raise ValueError(f"Unsupported indicator: {indicator_name}.")


def _coerce_indicator_params(
    registry_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    if registry_name in {"sma", "ema", "rsi", "atr"}:
        window = _positive_int(params["window"], "window")
        return {"window": window}
    if registry_name == "macd":
        fast_period = _positive_int(params["fast_period"], "fast_period")
        slow_period = _positive_int(params["slow_period"], "slow_period")
        signal_period = _positive_int(params["signal_period"], "signal_period")
        if fast_period >= slow_period:
            raise ValueError("MACD fast_period must be less than slow_period.")
        return {
            "fast_period": fast_period,
            "slow_period": slow_period,
            "signal_period": signal_period,
        }
    if registry_name == "bollinger_bands":
        window = _positive_int(params["window"], "window")
        standard_deviations = float(params["standard_deviations"])
        if standard_deviations <= 0:
            raise ValueError("standard_deviations must be greater than zero.")
        return {
            "window": window,
            "standard_deviations": standard_deviations,
        }
    raise ValueError(f"Unsupported indicator: {registry_name}.")


def _minimum_rows(registry_name: str, params: dict[str, Any]) -> int:
    if registry_name == "rsi":
        return int(params["window"]) + 1
    if registry_name == "macd":
        return int(params["slow_period"])
    return int(params["window"])


def _positive_int(value: Any, field_name: str) -> int:
    converted = int(value)
    if converted <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return converted


def _parse_name_number(parts: list[str]) -> float:
    raw_value = ".".join(parts)
    return float(raw_value)


def _format_number_for_name(value: Any) -> str:
    converted = float(value)
    if converted.is_integer():
        return str(int(converted))
    return str(converted).rstrip("0").rstrip(".").replace(".", "_")
