from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


MEMORY_FILE = Path("data") / "strategy_memory.json"


@dataclass(frozen=True)
class SetupMemory:
    key: str
    trades: int
    wins: int
    losses: int
    net_pnl_usdt: float
    average_pnl_usdt: float
    win_rate_percent: float
    score_adjustment: int
    size_multiplier: float

    def to_dict(self) -> dict:
        return asdict(self)


class StrategyMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or MEMORY_FILE
        self.memory = {
            "best_regimes": {},
            "worst_regimes": {},
            "profitable_scores": [],
            "losing_scores": [],
            "volatility_performance": {},
            "holding_times": [],
            "trading_hours": {}
        }

    def load(self) -> dict:
        if not self.path.exists():
            return {"setups": {}, "updated_at": None}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"setups": {}, "updated_at": None}
        if not isinstance(payload, dict):
            return {"setups": {}, "updated_at": None}
        payload.setdefault("setups", {})
        payload.setdefault("updated_at", None)
        return payload

    def save(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def update_from_trade(self, trade: dict) -> SetupMemory:
        payload = self.load()
        setups = payload.setdefault("setups", {})
        key = setup_key(trade)
        current = setups.get(key, {})
        pnl = _safe_float(trade.get("realized_pnl_usdt", trade.get("pnl_usdt", 0.0)))

        trades = int(current.get("trades", 0)) + 1
        wins = int(current.get("wins", 0)) + (1 if pnl > 0 else 0)
        losses = int(current.get("losses", 0)) + (1 if pnl < 0 else 0)
        net_pnl = _safe_float(current.get("net_pnl_usdt", 0.0)) + pnl
        memory = build_setup_memory(key, trades, wins, losses, net_pnl)
        setups[key] = memory.to_dict()
        self.save(payload)
        return memory

    def evaluate_setup(self, setup: dict) -> SetupMemory:
        payload = self.load()
        key = setup_key(setup)
        current = payload.get("setups", {}).get(key)
        if not current:
            return build_setup_memory(key, 0, 0, 0, 0.0)
        return SetupMemory(**current)

    def record_trade(self, regime, score, pnl, volatility, holding_time, hour):
        # Track best and worst regimes
        if pnl > 0:
            self.memory["best_regimes"].setdefault(regime, 0)
            self.memory["best_regimes"][regime] += 1
        else:
            self.memory["worst_regimes"].setdefault(regime, 0)
            self.memory["worst_regimes"][regime] += 1

        # Track profitable and losing scores
        if pnl > 0:
            self.memory["profitable_scores"].append(score)
        else:
            self.memory["losing_scores"].append(score)

        # Track volatility performance
        self.memory["volatility_performance"].setdefault(volatility, {"wins": 0, "losses": 0})
        if pnl > 0:
            self.memory["volatility_performance"][volatility]["wins"] += 1
        else:
            self.memory["volatility_performance"][volatility]["losses"] += 1

        # Track holding times
        self.memory["holding_times"].append(holding_time)

        # Track trading hours
        self.memory["trading_hours"].setdefault(hour, {"wins": 0, "losses": 0})
        if pnl > 0:
            self.memory["trading_hours"][hour]["wins"] += 1
        else:
            self.memory["trading_hours"][hour]["losses"] += 1

    def get_best_conditions(self):
        # Analyze memory to find best conditions
        best_regime = max(self.memory["best_regimes"], key=self.memory["best_regimes"].get, default="N/A")
        avg_holding_time = sum(self.memory["holding_times"]) / len(self.memory["holding_times"]) if self.memory["holding_times"] else 0
        best_hour = max(self.memory["trading_hours"], key=lambda h: self.memory["trading_hours"][h]["wins"], default="N/A")

        return {
            "best_regime": best_regime,
            "avg_holding_time": avg_holding_time,
            "best_hour": best_hour
        }

    def adapt_strategy(self):
        # Adaptive learning logic
        for regime, losses in self.memory["worst_regimes"].items():
            if losses > 5:  # Example threshold
                print(f"Adapting strategy: Raising score requirement for {regime}")

        for regime, wins in self.memory["best_regimes"].items():
            if wins > 10:  # Example threshold
                print(f"Adapting strategy: Increasing confidence for {regime}")


def setup_key(payload: dict) -> str:
    symbol = str(payload.get("symbol", "unknown"))
    entry_type = str(payload.get("entry_type", "unknown"))
    regime = str(payload.get("regime", payload.get("regime_at_entry", "unknown")))
    confidence = str(payload.get("confidence", "unknown"))
    return f"{symbol}|{entry_type}|{regime}|{confidence}"


def build_setup_memory(
    key: str,
    trades: int,
    wins: int,
    losses: int,
    net_pnl_usdt: float,
) -> SetupMemory:
    average = net_pnl_usdt / trades if trades else 0.0
    win_rate = wins / trades * 100 if trades else 0.0
    score_adjustment = 0
    size_multiplier = 1.0

    if trades >= 10:
        if win_rate >= 60 and average > 0:
            score_adjustment = -3
            size_multiplier = 1.05
        elif win_rate < 45 or average < 0:
            score_adjustment = 5
            size_multiplier = 0.8

    return SetupMemory(
        key=key,
        trades=trades,
        wins=wins,
        losses=losses,
        net_pnl_usdt=round(net_pnl_usdt, 8),
        average_pnl_usdt=round(average, 8),
        win_rate_percent=round(win_rate, 4),
        score_adjustment=score_adjustment,
        size_multiplier=size_multiplier,
    )


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
