from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
PRIMARY_MODULES = [
    "dashboard.py",
    "bot.py",
    "scripts/validate_project.py",
    "scripts/run_paper_trading.py",
    "scripts/generate_validation_candles.py",
    "scripts/run_validation_suite.py",
    "monitoring/health_monitor.py",
    "data/market_data.py",
    "backtesting/backtester.py",
    "core/engine.py",
    "analytics/performance.py",
    "analytics/trade_journal.py",
    "analytics/metrics.py",
    "analytics/strategy_memory.py",
    "paper/paper_exchange.py",
    "core/runtime_control.py",
    "portfolio/positions.py",
    "execution/order_manager.py",
    "execution/entries.py",
    "execution/exits.py",
    "risk/recovery.py",
    "risk/exposure.py",
    "risk/position_sizing.py",
    "risk/equity_protection.py",
    "strategy/indicators.py",
    "strategy/signals.py",
    "strategy/scoring.py",
    "strategy/market_regime.py",
    "strategy/market_stress.py",
    "strategy/cooldown.py",
]


@dataclass(frozen=True)
class StepResult:
    name: str
    returncode: int

    @property
    def passed(self) -> bool:
        return self.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local professional validation suite."
    )
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--starting-equity", type=float, default=1000.0)
    parser.add_argument(
        "--strict-health",
        action="store_true",
        help="Fail the suite if the health monitor reports operational warnings.",
    )
    args = parser.parse_args()

    python = PYTHON if PYTHON.exists() else Path(sys.executable)
    results = [
        run_step(
            "compile",
            [str(python), "-m", "py_compile", *PRIMARY_MODULES],
        ),
        run_step(
            "project validation",
            [str(python), "scripts/validate_project.py"],
        ),
        run_step(
            "generate validation candles",
            [str(python), "scripts/generate_validation_candles.py"],
        ),
    ]

    paper_command = [
        str(python),
        "scripts/run_paper_trading.py",
        "--data-dir",
        "data/candles/validation",
        "--timeframe",
        "1h",
        "--starting-equity",
        str(args.starting_equity),
    ]
    if args.max_steps is not None:
        paper_command.extend(["--max-steps", str(args.max_steps)])

    results.append(run_step("paper/backtest validation", paper_command))
    health = run_step(
        "health monitor",
        [str(python), "monitoring/health_monitor.py"],
        fail_on_error=args.strict_health,
    )
    results.append(health)

    failed = [result for result in results if not result.passed]
    print("")
    print("Validation suite summary")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"- {status}: {result.name}")

    hard_failures = [
        result
        for result in failed
        if result.name != "health monitor" or args.strict_health
    ]
    if hard_failures:
        raise SystemExit(1)

    print("Validation suite completed.")
    if not args.strict_health:
        print("Health monitor warnings are informational unless --strict-health is used.")


def run_step(
    name: str,
    command: list[str],
    fail_on_error: bool = True,
) -> StepResult:
    print("")
    print(f"=== {name.upper()} ===")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0:
        print(f"{name} exited with code {completed.returncode}")
        if fail_on_error:
            return StepResult(name=name, returncode=completed.returncode)
    return StepResult(name=name, returncode=completed.returncode)


if __name__ == "__main__":
    main()
