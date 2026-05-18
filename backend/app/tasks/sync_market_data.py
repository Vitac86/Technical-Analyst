import argparse
import asyncio
from datetime import date
import json
from typing import Any

from app.db.session import SessionLocal
from app.services.market_data.sync_service import (
    sync_moex_candles,
    sync_moex_instruments,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync market data from MOEX ISS.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("instruments", help="Sync MOEX share instruments.")

    candles = subparsers.add_parser("candles", help="Sync candles for one ticker.")
    candles.add_argument("--ticker", required=True)
    candles.add_argument("--timeframe", default="1d")
    candles.add_argument("--start", required=True, type=date.fromisoformat)
    candles.add_argument("--end", required=True, type=date.fromisoformat)

    return parser


async def run_command(args: argparse.Namespace) -> dict[str, Any]:
    with SessionLocal() as db:
        if args.command == "instruments":
            return await sync_moex_instruments(db)
        if args.command == "candles":
            return await sync_moex_candles(
                db,
                ticker=args.ticker,
                timeframe=args.timeframe,
                start=args.start,
                end=args.end,
            )
    raise ValueError(f"Unsupported command: {args.command}")


def main() -> None:
    args = build_parser().parse_args()
    summary = asyncio.run(run_command(args))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
