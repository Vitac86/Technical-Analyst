"""Strategy Lab v1.7: CLI for the MetaTrader 5 **signal-only** bridge.

    v1.7 is signal-only. Execution is intentionally disabled.

This runner connects to a locally running MT5 terminal, reads an exported v1.6
strategy config, computes the rule-based signal on the latest *closed* candle,
and writes logs/alerts. It never opens, closes or modifies orders, never logs in
to the broker, and never enables live trading.

Run one check and exit::

    python backend/app/strategy_lab/run_mt5_signal_bridge.py \
        --config path/to/exported_strategy_config.json --once

Poll on a fixed interval (Ctrl-C to stop)::

    python backend/app/strategy_lab/run_mt5_signal_bridge.py \
        --config path/to/exported_strategy_config.json --poll-seconds 60

Logs are written under ``--output-dir`` (default
``MetaTrader_Data/reports/mt5_signal_bridge/``), which is git-ignored.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow execution both as a script and as a package module.
try:
    from . import mt5_signal_bridge as bridge
    from .signal_store import SignalStore, default_output_dir
except ImportError:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mt5_signal_bridge as bridge  # type: ignore[no-redef]
    from signal_store import SignalStore, default_output_dir  # type: ignore[no-redef]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_mt5_signal_bridge",
        description=(
            "Strategy Lab v1.7 MT5 signal-only bridge. "
            "Signal-only: never opens/closes/modifies orders."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to an exported Strategy Lab v1.6 strategy config JSON.",
    )
    parser.add_argument(
        "--bars",
        type=int,
        default=bridge.DEFAULT_BARS,
        help=f"Number of candles to fetch (default {bridge.DEFAULT_BARS}).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check and exit (default when --poll-seconds is unset).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Polling interval in seconds when not using --once (default 60).",
    )
    parser.add_argument(
        "--recent-limit",
        type=int,
        default=bridge.DEFAULT_RECENT_LIMIT,
        help=(
            "Closed candles of diagnostics to record per check "
            f"(default {bridge.DEFAULT_RECENT_LIMIT}, max {bridge.MAX_RECENT_LIMIT})."
        ),
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Override the config symbol with the broker's exact MT5 name "
        "(e.g. XAUUSDrfd).",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reserved safety flag (default true). v1.7 is signal-only regardless: "
        "no orders are ever placed.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Where to write logs/state (default {default_output_dir()}).",
    )
    return parser


def _print_signal(record: dict) -> None:
    """Pretty-print one emitted signal record to stdout."""
    line = (
        f"[{record['signal_type']}] {record['strategy_id']} "
        f"{record['symbol']} {record['timeframe']} "
        f"@ {record['signal_time']} | close={record['close_price']} "
        f"atr={record['atr_value']} reason={record['reason']} "
        f"(execution_enabled={record['execution_enabled']})"
    )
    print(line)


def _run_check(
    config: dict,
    store: SignalStore,
    mt5,
    symbol: str,
    fetch_ohlc_fn,
    bars: int,
    recent_limit: int,
) -> None:
    """Evaluate one closed candle (refreshing recent diagnostics) and report it."""
    market_context = bridge.read_market_context(mt5, symbol)
    record = bridge.run_once(
        config,
        store,
        symbol=symbol,
        fetch_ohlc_fn=fetch_ohlc_fn,
        bars=bars,
        generated_at=datetime.now(timezone.utc),
        recent_limit=recent_limit,
        market_context=market_context,
    )
    if record is None:
        print(f"No new closed candle / already processed ({_now()}).")
        return
    _print_signal(record)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Hard safety lock: v1.7 is signal-only. Execution is intentionally disabled.
    bridge.assert_signal_only()

    config = bridge.load_config(args.config)
    bridge.validate_config(config)

    store = SignalStore(args.output_dir)
    requested_symbol = args.symbol or config["symbol"]

    mt5 = bridge.load_mt5()
    bridge.initialize_mt5(mt5)
    try:
        symbol = bridge.resolve_symbol(mt5, requested_symbol)
        fetch_ohlc_fn = bridge.make_fetch_ohlc_fn(mt5)

        print(
            f"MT5 signal-only bridge v1.7 | strategy={config['strategy_id']} "
            f"symbol={symbol} timeframe={config['timeframe']} bars={args.bars} | "
            f"SIGNAL-ONLY (execution disabled) | logs -> {store.output_dir}"
        )

        if args.once or args.poll_seconds is None:
            _run_check(
                config, store, mt5, symbol, fetch_ohlc_fn, args.bars, args.recent_limit
            )
            return 0

        print(f"Polling every {args.poll_seconds}s. Press Ctrl-C to stop.")
        while True:
            try:
                _run_check(
                    config, store, mt5, symbol, fetch_ohlc_fn, args.bars, args.recent_limit
                )
            except bridge.BridgeError as exc:
                # Don't kill a long-running poller on a transient data hiccup.
                print(f"WARN: {exc}", file=sys.stderr)
            time.sleep(max(1, args.poll_seconds))
    finally:
        bridge.shutdown_mt5(mt5)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except bridge.BridgeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130) from None
