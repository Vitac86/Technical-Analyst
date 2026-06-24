"""Strategy Lab v1.8: CLI for the MetaTrader 5 **demo execution robot**.

    Demo only. Dry-run is the default. Live trading is not implemented.

This runner connects to a locally running MT5 terminal, reads an exported D
strategy config, evaluates the rule-based signal on the latest *closed* H4
candle, and either reports what it *would* do (dry-run) or -- only with the
explicit demo-execution flags **and** a detected demo account -- opens a BUY and
trails its stop upward. It never logs in to the broker, never closes a position,
and never sends a SELL/SHORT order.

Dry-run once::

    python backend/app/strategy_lab/run_mt5_execution_robot.py \
        --config MetaTrader_Data/configs/D_supertrend_h4.json --once

Dry-run polling::

    python backend/app/strategy_lab/run_mt5_execution_robot.py \
        --config MetaTrader_Data/configs/D_supertrend_h4.json --poll-seconds 60

Demo execution once (sends orders ONLY on a detected demo account)::

    python backend/app/strategy_lab/run_mt5_execution_robot.py \
        --config MetaTrader_Data/configs/D_supertrend_h4.json --once \
        --execution-enabled --confirm-demo-execution

Demo execution polling::

    python backend/app/strategy_lab/run_mt5_execution_robot.py \
        --config MetaTrader_Data/configs/D_supertrend_h4.json --poll-seconds 60 \
        --execution-enabled --confirm-demo-execution

Logs/state are written under ``--output-dir`` (default
``MetaTrader_Data/reports/mt5_execution_robot/``), which is git-ignored.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow execution both as a script and as a package module.
try:
    from . import mt5_execution_robot as robot
    from . import mt5_signal_bridge as bridge
    from .execution_store import ExecutionStore, default_output_dir
except ImportError:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mt5_execution_robot as robot  # type: ignore[no-redef]
    import mt5_signal_bridge as bridge  # type: ignore[no-redef]
    from execution_store import ExecutionStore, default_output_dir  # type: ignore[no-redef]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_mt5_execution_robot",
        description=(
            "Strategy Lab v1.8 MT5 demo execution robot. "
            "Demo only; dry-run by default; never closes; long-only."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to an exported Strategy Lab D strategy config JSON.",
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
        help="Run a single decision and exit (default when --poll-seconds is unset).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=60,
        help="Polling interval in seconds when not using --once (default 60).",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Override the config symbol with the broker's exact MT5 name.",
    )
    parser.add_argument(
        "--magic",
        type=int,
        default=robot.DEFAULT_MAGIC,
        help=f"Magic number for the robot's orders (default {robot.DEFAULT_MAGIC}).",
    )
    parser.add_argument(
        "--deviation",
        type=int,
        default=robot.DEFAULT_DEVIATION,
        help=f"Max price deviation in points (default {robot.DEFAULT_DEVIATION}).",
    )
    parser.add_argument(
        "--execution-enabled",
        action="store_true",
        help="Enable demo execution (orders are still sent ONLY on a demo account).",
    )
    parser.add_argument(
        "--confirm-demo-execution",
        action="store_true",
        help="Confirm you understand orders may be placed on the connected DEMO account.",
    )
    parser.add_argument(
        "--allow-min-lot-rounding",
        action="store_true",
        help="Allow rounding a sub-minimum lot up to volume_min (increases risk).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Where to write logs/state (default {default_output_dir()}).",
    )
    return parser


def _print_decision(decision: dict) -> None:
    """Pretty-print one decision record to stdout."""
    signal = decision.get("signal") or {}
    sizing = decision.get("sizing") or {}
    reasons = decision.get("refusal_reasons") or []
    line = (
        f"[{decision.get('mode')}] action={decision.get('intended_action')} "
        f"{decision.get('strategy_id')} {decision.get('symbol')} "
        f"{decision.get('timeframe')} signal={signal.get('signal_type')} "
        f"@ {signal.get('signal_time')} lot={sizing.get('rounded_lot')} "
        f"entry={sizing.get('entry_price')} sl={sizing.get('initial_stop_price')}"
    )
    if reasons:
        line += f" refused={','.join(str(r) for r in reasons)}"
    print(line)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_check(
    config: dict,
    store: ExecutionStore,
    mt5,
    *,
    symbol: str | None,
    bars: int,
    execution_enabled: bool,
    confirm_demo_execution: bool,
    magic: int,
    deviation: int,
    allow_min_lot_rounding: bool,
) -> None:
    decision = robot.run_once(
        config,
        store,
        mt5,
        symbol=symbol,
        bars=bars,
        execution_enabled=execution_enabled,
        confirm_demo_execution=confirm_demo_execution,
        magic=magic,
        deviation=deviation,
        allow_min_lot_rounding=allow_min_lot_rounding,
        generated_at=datetime.now(timezone.utc),
    )
    _print_decision(decision)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    config = bridge.load_config(args.config)
    robot.validate_execution_config(config)

    store = ExecutionStore(args.output_dir)
    requested_symbol = args.symbol or config.get("symbol")

    execution_enabled = bool(args.execution_enabled)
    confirm = bool(args.confirm_demo_execution)
    mode = (
        robot.MODE_DEMO_EXECUTION
        if (execution_enabled and confirm)
        else robot.MODE_DRY_RUN
    )

    if execution_enabled and not confirm:
        # Refuse loudly: demo execution requires the explicit confirmation flag.
        print(
            "Refusing demo execution: pass --confirm-demo-execution to acknowledge "
            "that orders may be placed on the connected DEMO account.",
            file=sys.stderr,
        )
        execution_enabled = False
        mode = robot.MODE_DRY_RUN

    mt5 = bridge.load_mt5()
    bridge.initialize_mt5(mt5)
    try:
        symbol = bridge.resolve_symbol(mt5, requested_symbol)
        print(
            f"MT5 demo execution robot v{robot.EXECUTION_ROBOT_VERSION} | "
            f"strategy={config['strategy_id']} symbol={symbol} timeframe="
            f"{robot.SUPPORTED_TIMEFRAME} bars={args.bars} | mode={mode} "
            f"(demo-only; dry-run default) | logs -> {store.output_dir}"
        )

        if args.once or args.poll_seconds is None:
            _run_check(
                config,
                store,
                mt5,
                symbol=symbol,
                bars=args.bars,
                execution_enabled=execution_enabled,
                confirm_demo_execution=confirm,
                magic=args.magic,
                deviation=args.deviation,
                allow_min_lot_rounding=args.allow_min_lot_rounding,
            )
            return 0

        print(f"Polling every {args.poll_seconds}s. Press Ctrl-C to stop.")
        while True:
            try:
                _run_check(
                    config,
                    store,
                    mt5,
                    symbol=symbol,
                    bars=args.bars,
                    execution_enabled=execution_enabled,
                    confirm_demo_execution=confirm,
                    magic=args.magic,
                    deviation=args.deviation,
                    allow_min_lot_rounding=args.allow_min_lot_rounding,
                )
            except bridge.BridgeError as exc:
                print(f"WARN: {exc}", file=sys.stderr)
            except robot.ExecutionError as exc:
                print(f"WARN: {exc}", file=sys.stderr)
            time.sleep(max(1, args.poll_seconds))
    finally:
        bridge.shutdown_mt5(mt5)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (bridge.BridgeError, robot.ExecutionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130) from None
