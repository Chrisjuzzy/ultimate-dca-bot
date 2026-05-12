from __future__ import annotations

import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_PATHS = [
    "bot.py",
    "dashboard.py",
    "config.py",
    "requirements.txt",
    "README.md",
    ".streamlit/config.toml",
    "data",
    "logs",
    "database",
    "data/market_data.py",
    "strategy",
    "risk",
    "execution",
    "portfolio",
    "analytics",
    "paper",
    "core",
    "backtesting",
    "monitoring",
    "deployment",
    "core/runtime_control.py",
    "scripts/run_paper_trading.py",
    "scripts/generate_validation_candles.py",
    "scripts/run_validation_suite.py",
]

CRITICAL_MODULES = [
    "strategy.indicators",
    "data.market_data",
    "strategy.signals",
    "strategy.scoring",
    "strategy.market_regime",
    "strategy.market_stress",
    "strategy.cooldown",
    "risk.position_sizing",
    "risk.exposure",
    "risk.recovery",
    "risk.equity_protection",
    "execution.entries",
    "execution.exits",
    "execution.order_manager",
    "portfolio.positions",
    "analytics.performance",
    "analytics.trade_history",
    "analytics.trade_journal",
    "analytics.metrics",
    "analytics.strategy_memory",
    "paper.paper_exchange",
    "core.runtime_control",
    "core.engine",
    "backtesting.backtester",
    "monitoring.health_monitor",
]


def main() -> None:
    missing = [path for path in REQUIRED_PATHS if not Path(path).exists()]
    failed_imports = []

    for module_name in CRITICAL_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failed_imports.append((module_name, str(exc)))

    if missing:
        print("Missing paths:")
        for path in missing:
            print(f"- {path}")
    else:
        print("All required paths exist.")

    if failed_imports:
        print("Failed imports:")
        for module_name, error in failed_imports:
            print(f"- {module_name}: {error}")
    else:
        print("All critical modules import successfully.")

    if missing or failed_imports:
        raise SystemExit(1)

    print("Project validation passed.")


if __name__ == "__main__":
    main()
