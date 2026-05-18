import argparse
import json
from typing import Any

from app.db.session import SessionLocal
from app.services.indicators.calculation_service import (
    calculate_default_indicators_for_instrument,
    calculate_indicator_for_instrument,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate stored technical indicators.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    one = subparsers.add_parser("one", help="Calculate one indicator.")
    one.add_argument("--instrument-id", required=True, type=int)
    one.add_argument("--timeframe", default="1d")
    one.add_argument("--indicator", required=True)
    one.add_argument("--window", type=int)
    one.add_argument("--fast-period", type=int)
    one.add_argument("--slow-period", type=int)
    one.add_argument("--signal-period", type=int)
    one.add_argument("--standard-deviations", type=float)

    defaults = subparsers.add_parser("defaults", help="Calculate default indicators.")
    defaults.add_argument("--instrument-id", required=True, type=int)
    defaults.add_argument("--timeframe", default="1d")

    return parser


def run_command(args: argparse.Namespace) -> dict[str, Any]:
    with SessionLocal() as db:
        if args.command == "one":
            return calculate_indicator_for_instrument(
                db,
                instrument_id=args.instrument_id,
                timeframe=args.timeframe,
                indicator_name=args.indicator,
                params=_params_from_args(args),
            )
        if args.command == "defaults":
            return calculate_default_indicators_for_instrument(
                db,
                instrument_id=args.instrument_id,
                timeframe=args.timeframe,
            )
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> None:
    args = build_parser().parse_args()
    summary = run_command(args)
    print(json.dumps(summary, indent=2, default=str))


def _params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for argument_name in (
        "window",
        "fast_period",
        "slow_period",
        "signal_period",
        "standard_deviations",
    ):
        value = getattr(args, argument_name)
        if value is not None:
            params[argument_name] = value
    return params


if __name__ == "__main__":
    main()
