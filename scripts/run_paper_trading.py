from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics.trade_history import records_from_paper_report, sync_records
from backtesting.backtester import BacktestConfig, Backtester


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an offline paper/backtest validation using local candle CSV files."
    )
    parser.add_argument(
        "--data-dir",
        default="data/candles",
        help="Directory containing SYMBOL_TIMEFRAME.csv candle files.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTC_USDT", "ETH_USDT"],
        help="CSV filename prefixes, for example BTC_USDT ETH_USDT.",
    )
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--starting-equity", type=float, default=1000.0)
    parser.add_argument("--warmup", type=int, default=220)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--output",
        default="data/paper_report.json",
        help="Path where the validation report JSON should be written.",
    )
    parser.add_argument(
        "--metrics-log",
        default="data/paper_metrics.jsonl",
        help="JSONL file where each paper validation run appends summary metrics.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously at the chosen interval for long paper validation.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=3600,
        help="Delay between --watch paper validation runs.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Optional maximum number of --watch iterations.",
    )
    args = parser.parse_args()

    iteration = 0
    while True:
        iteration += 1
        run_once(args, iteration=iteration)
        if not args.watch:
            break
        if args.iterations is not None and iteration >= args.iterations:
            break
        print(f"Sleeping {args.interval_seconds} seconds before next paper validation run...")
        time.sleep(max(1, args.interval_seconds))


def run_once(args: argparse.Namespace, iteration: int = 1) -> None:
    candles_by_symbol = {}
    for symbol_key in args.symbols:
        path = Path(args.data_dir) / f"{symbol_key}_{args.timeframe}.csv"
        if not path.exists():
            print(f"Skipping missing candle file: {path}")
            continue
        exchange_symbol = symbol_key.replace("_", "/")
        candles_by_symbol[exchange_symbol] = pd.read_csv(path)

    if not candles_by_symbol:
        raise SystemExit("No candle files found. Add CSV files under data/candles first.")

    result = Backtester(
        BacktestConfig(
            starting_equity_usdt=args.starting_equity,
            warmup_candles=args.warmup,
            max_steps=args.max_steps,
        )
    ).run(candles_by_symbol)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["generated_at"] = datetime.now(UTC).isoformat()
    payload["iteration"] = iteration
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    append_metrics(args.metrics_log, payload)
    synced = sync_records(records_from_paper_report(payload))

    perf = result.performance
    print("Paper validation complete")
    print(f"Report: {output_path}")
    print(f"Trades: {perf.total_trades}")
    print(f"Net PnL: {perf.net_pnl_usdt:.4f} USDT")
    print(f"Win rate: {perf.win_rate_percent:.2f}%")
    print(f"Max drawdown: {perf.max_drawdown_percent:.2f}%")
    print(f"Equity path drawdown: {result.max_equity_drawdown_percent:.2f}%")
    print(f"Final equity: {result.final_equity_usdt:.4f} USDT")
    print(f"Profit factor: {perf.profit_factor}")
    print(f"Trade history records: {len(synced)}")


def append_metrics(path: str, report: dict) -> None:
    performance = report.get("performance", {})
    summary = {
        "timestamp": report.get("generated_at"),
        "iteration": report.get("iteration"),
        "trades": performance.get("total_trades", 0),
        "win_rate_percent": performance.get("win_rate_percent", 0.0),
        "net_pnl_usdt": performance.get("net_pnl_usdt", 0.0),
        "profit_factor": performance.get("profit_factor", 0.0),
        "max_drawdown_percent": performance.get("max_drawdown_percent", 0.0),
        "max_equity_drawdown_percent": report.get("max_equity_drawdown_percent", 0.0),
        "final_equity_usdt": report.get("final_equity_usdt", 0.0),
        "accepted_entries": report.get("accepted_entries", 0),
        "rejected_entries": report.get("rejected_entries", 0),
        "exit_events": report.get("exit_events", 0),
    }
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
