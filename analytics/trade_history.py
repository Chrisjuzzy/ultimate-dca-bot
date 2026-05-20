"""Professional trade history system with advanced analytics and filtering."""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

import pandas as pd


TimeFilter = Literal["today", "7d", "30d", "90d", "all"]


@dataclass(frozen=True)
class TradeHistory:
    """Individual trade record for history tracking."""
    timestamp: str
    symbol: str
    side: str  # "buy" or "sell"
    entry_price: float
    exit_price: float | None
    quantity: float
    pnl_usdt: float
    pnl_pct: float
    hold_minutes: int
    score: int
    regime: str
    entry_reason: list[str] = field(default_factory=list)
    exit_reason: str = ""
    fees_usdt: float = 0.0
    order_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_win(self) -> bool:
        return self.pnl_usdt > 0

    @property
    def is_loss(self) -> bool:
        return self.pnl_usdt < 0

    @property
    def hold_hours(self) -> float:
        return round(self.hold_minutes / 60, 2)


@dataclass(frozen=True)
class DailyPerformance:
    """Daily performance summary."""
    date: str
    trades_count: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_pnl_usdt: float
    best_trade_pnl: float
    worst_trade_pnl: float
    avg_hold_hours: float
    avg_pnl_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WeeklyPerformance:
    """Weekly performance summary."""
    week_start: str
    week_end: str
    trades_count: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_pnl_usdt: float
    avg_daily_pnl: float
    best_day_pnl: float
    worst_day_pnl: float
    recovery_factor: float
    max_drawdown_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TradeStats:
    """Professional trading statistics."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    total_pnl_usdt: float
    total_fees_usdt: float
    net_pnl_usdt: float
    avg_pnl_per_trade: float
    avg_pnl_pct: float
    best_trade_pnl: float
    worst_trade_pnl: float
    largest_win_pct: float
    largest_loss_pct: float
    profit_factor: float  # sum(wins) / abs(sum(losses))
    expectancy: float  # average win/loss per trade
    avg_hold_hours: float
    best_trading_hour: int  # 0-23
    longest_winning_streak: int
    longest_losing_streak: int
    recovery_factor: float

    def to_dict(self) -> dict:
        return asdict(self)


class TradeHistoryManager:
    """Manage and analyze trade history with professional metrics."""

    def __init__(self, path: Path | str = Path("data") / "trade_history.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        pnl_usdt: float,
        pnl_pct: float,
        hold_minutes: int,
        score: int,
        regime: str,
        exit_reason: str = "",
        entry_reason: list[str] | None = None,
        fees_usdt: float = 0.0,
        order_id: str = "",
    ) -> TradeHistory:
        """Record a completed trade."""
        trade = TradeHistory(
            timestamp=datetime.now(UTC).isoformat(),
            symbol=symbol,
            side=side,
            entry_price=round(entry_price, 8),
            exit_price=round(exit_price, 8),
            quantity=round(quantity, 8),
            pnl_usdt=round(pnl_usdt, 8),
            pnl_pct=round(pnl_pct, 4),
            hold_minutes=hold_minutes,
            score=score,
            regime=regime,
            exit_reason=exit_reason,
            entry_reason=entry_reason or [],
            fees_usdt=round(fees_usdt, 8),
            order_id=order_id,
        )
        self._append_trade(trade)
        return trade

    def _append_trade(self, trade: TradeHistory) -> None:
        """Append trade to history file."""
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(trade.to_dict(), ensure_ascii=True) + "\n")

    def load_all(self) -> list[TradeHistory]:
        """Load all trades from history."""
        if not self.path.exists():
            return []

        trades = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    trades.append(self._dict_to_trade(data))
                except json.JSONDecodeError:
                    continue

        return sorted(trades, key=lambda t: t.timestamp)

    def load_filtered(
        self,
        time_filter: TimeFilter = "all",
        symbols: list[str] | None = None,
        trade_type: Literal["all", "wins", "losses"] = "all",
    ) -> list[TradeHistory]:
        """Load trades with filters applied."""
        all_trades = self.load_all()
        filtered = all_trades

        # Time filter
        if time_filter != "all":
            now = datetime.now(UTC)
            cutoff = None
            if time_filter == "today":
                cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif time_filter == "7d":
                cutoff = now - timedelta(days=7)
            elif time_filter == "30d":
                cutoff = now - timedelta(days=30)
            elif time_filter == "90d":
                cutoff = now - timedelta(days=90)

            if cutoff:
                filtered = [
                    t for t in filtered
                    if datetime.fromisoformat(t.timestamp) >= cutoff
                ]

        # Symbol filter
        if symbols:
            filtered = [t for t in filtered if t.symbol in symbols]

        # Trade type filter
        if trade_type == "wins":
            filtered = [t for t in filtered if t.is_win]
        elif trade_type == "losses":
            filtered = [t for t in filtered if t.is_loss]

        return filtered

    def get_daily_performance(
        self, time_filter: TimeFilter = "all"
    ) -> list[DailyPerformance]:
        """Calculate daily performance summaries."""
        trades = self.load_filtered(time_filter)
        if not trades:
            return []

        # Group by date
        df = pd.DataFrame([t.to_dict() for t in trades])
        df["date"] = pd.to_datetime(df["timestamp"]).dt.date

        daily_stats = []
        for date, group in df.groupby("date"):
            wins = group[group["pnl_usdt"] > 0]
            losses = group[group["pnl_usdt"] < 0]
            all_trades_count = len(group)

            daily_stats.append(
                DailyPerformance(
                    date=str(date),
                    trades_count=all_trades_count,
                    winning_trades=len(wins),
                    losing_trades=len(losses),
                    win_rate_pct=round(100 * len(wins) / all_trades_count, 2) if all_trades_count > 0 else 0.0,
                    total_pnl_usdt=round(group["pnl_usdt"].sum(), 8),
                    best_trade_pnl=round(group["pnl_usdt"].max(), 8),
                    worst_trade_pnl=round(group["pnl_usdt"].min(), 8),
                    avg_hold_hours=round(group["hold_minutes"].mean() / 60, 2) if all_trades_count > 0 else 0.0,
                    avg_pnl_pct=round(group["pnl_pct"].mean(), 4) if all_trades_count > 0 else 0.0,
                )
            )

        return sorted(daily_stats, key=lambda x: x.date)

    def get_weekly_performance(
        self, time_filter: TimeFilter = "all"
    ) -> list[WeeklyPerformance]:
        """Calculate weekly performance summaries."""
        trades = self.load_filtered(time_filter)
        if not trades:
            return []

        df = pd.DataFrame([t.to_dict() for t in trades])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["week"] = df["timestamp"].dt.isocalendar().week
        df["year"] = df["timestamp"].dt.isocalendar().year

        weekly_stats = []
        for (year, week), group in df.groupby(["year", "week"]):
            wins = group[group["pnl_usdt"] > 0]
            losses = group[group["pnl_usdt"] < 0]
            all_trades_count = len(group)

            # Get daily stats for this week
            daily_pnls = group.groupby(group["timestamp"].dt.date)["pnl_usdt"].sum()
            week_start = group["timestamp"].min().date()
            week_end = group["timestamp"].max().date()

            # Calculate max drawdown for the week
            equity_curve = [0.0]
            cumulative = 0.0
            for pnl in group.sort_values("timestamp")["pnl_usdt"]:
                cumulative += pnl
                equity_curve.append(cumulative)

            peak = max(equity_curve)
            max_dd = min(equity_curve) - peak if peak != 0 else 0
            max_dd_pct = round(100 * max_dd / peak, 2) if peak > 0 else 0.0

            weekly_stats.append(
                WeeklyPerformance(
                    week_start=str(week_start),
                    week_end=str(week_end),
                    trades_count=all_trades_count,
                    winning_trades=len(wins),
                    losing_trades=len(losses),
                    win_rate_pct=round(100 * len(wins) / all_trades_count, 2) if all_trades_count > 0 else 0.0,
                    total_pnl_usdt=round(group["pnl_usdt"].sum(), 8),
                    avg_daily_pnl=round(daily_pnls.mean(), 8) if len(daily_pnls) > 0 else 0.0,
                    best_day_pnl=round(daily_pnls.max(), 8) if len(daily_pnls) > 0 else 0.0,
                    worst_day_pnl=round(daily_pnls.min(), 8) if len(daily_pnls) > 0 else 0.0,
                    recovery_factor=self._calculate_recovery_factor(group, weekly_stats),
                    max_drawdown_pct=max_dd_pct,
                )
            )

        return sorted(weekly_stats, key=lambda x: x.week_start)

    def get_stats(
        self, time_filter: TimeFilter = "all",
        symbols: list[str] | None = None,
    ) -> TradeStats | None:
        """Get comprehensive trading statistics."""
        trades = self.load_filtered(time_filter, symbols=symbols)
        if not trades:
            return None

        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if t.is_loss]
        pnls = [t.pnl_usdt for t in trades]
        pnls_pct = [t.pnl_pct for t in trades]

        # Streaks
        longest_win_streak = 0
        longest_loss_streak = 0
        current_win_streak = 0
        current_loss_streak = 0

        for trade in trades:
            if trade.is_win:
                current_win_streak += 1
                longest_win_streak = max(longest_win_streak, current_win_streak)
                current_loss_streak = 0
            elif trade.is_loss:
                current_loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, current_loss_streak)
                current_win_streak = 0

        # Best trading hour
        df = pd.DataFrame([t.to_dict() for t in trades])
        df["hour"] = pd.to_datetime(df["timestamp"]).dt.hour
        hourly_pnl = df.groupby("hour")["pnl_usdt"].sum()
        best_hour = hourly_pnl.idxmax() if len(hourly_pnl) > 0 else 0

        # Profit factor
        wins_sum = sum(t.pnl_usdt for t in wins) if wins else 0.0
        losses_sum = abs(sum(t.pnl_usdt for t in losses)) if losses else 1.0
        profit_factor = wins_sum / losses_sum if losses_sum > 0 else 0.0

        # Expectancy
        expectancy = (wins_sum - losses_sum) / len(trades) if trades else 0.0

        return TradeStats(
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            breakeven_trades=len(trades) - len(wins) - len(losses),
            win_rate_pct=round(100 * len(wins) / len(trades), 2) if trades else 0.0,
            total_pnl_usdt=round(sum(pnls), 8),
            total_fees_usdt=round(sum(t.fees_usdt for t in trades), 8),
            net_pnl_usdt=round(sum(pnls) - sum(t.fees_usdt for t in trades), 8),
            avg_pnl_per_trade=round(sum(pnls) / len(trades), 8) if trades else 0.0,
            avg_pnl_pct=round(sum(pnls_pct) / len(trades), 4) if trades else 0.0,
            best_trade_pnl=round(max(pnls), 8) if pnls else 0.0,
            worst_trade_pnl=round(min(pnls), 8) if pnls else 0.0,
            largest_win_pct=round(max(pnls_pct), 4) if pnls_pct else 0.0,
            largest_loss_pct=round(min(pnls_pct), 4) if pnls_pct else 0.0,
            profit_factor=round(profit_factor, 4),
            expectancy=round(expectancy, 8),
            avg_hold_hours=round(sum(t.hold_minutes for t in trades) / len(trades) / 60, 2) if trades else 0.0,
            best_trading_hour=int(best_hour),
            longest_winning_streak=longest_win_streak,
            longest_losing_streak=longest_loss_streak,
            recovery_factor=self._calculate_recovery_factor_stats(trades),
        )

    def get_best_trades(self, limit: int = 5, time_filter: TimeFilter = "all") -> list[TradeHistory]:
        """Get best performing trades."""
        trades = self.load_filtered(time_filter)
        return sorted(trades, key=lambda t: t.pnl_usdt, reverse=True)[:limit]

    def get_worst_trades(self, limit: int = 5, time_filter: TimeFilter = "all") -> list[TradeHistory]:
        """Get worst performing trades."""
        trades = self.load_filtered(time_filter)
        return sorted(trades, key=lambda t: t.pnl_usdt)[:limit]

    def get_best_setup(self) -> dict | None:
        """Get best performing setup (regime + entry score)."""
        trades = self.load_all()
        if not trades:
            return None

        df = pd.DataFrame([t.to_dict() for t in trades])
        df["regime_score"] = df.groupby("regime")["pnl_usdt"].transform("mean")

        best = df.loc[df["regime_score"].idxmax()]
        return {
            "regime": best["regime"],
            "avg_pnl": round(best["regime_score"], 8),
            "trades_count": len(df[df["regime"] == best["regime"]]),
            "win_rate": round(
                100 * len(df[(df["regime"] == best["regime"]) & (df["pnl_usdt"] > 0)])
                / len(df[df["regime"] == best["regime"]]), 2
            ),
        }

    def get_worst_setup(self) -> dict | None:
        """Get worst performing setup."""
        trades = self.load_all()
        if not trades:
            return None

        df = pd.DataFrame([t.to_dict() for t in trades])
        df["regime_score"] = df.groupby("regime")["pnl_usdt"].transform("mean")

        worst = df.loc[df["regime_score"].idxmin()]
        return {
            "regime": worst["regime"],
            "avg_pnl": round(worst["regime_score"], 8),
            "trades_count": len(df[df["regime"] == worst["regime"]]),
            "win_rate": round(
                100 * len(df[(df["regime"] == worst["regime"]) & (df["pnl_usdt"] > 0)])
                / len(df[df["regime"] == worst["regime"]]), 2
            ),
        }

    def export_csv(self, output_path: str | Path, time_filter: TimeFilter = "all") -> Path:
        """Export trade history to CSV."""
        trades = self.load_filtered(time_filter)
        if not trades:
            return Path(output_path)

        df = pd.DataFrame([t.to_dict() for t in trades])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output, index=False)
        return output

    @staticmethod
    def _dict_to_trade(data: dict) -> TradeHistory:
        """Convert dict to TradeHistory and normalize legacy fields."""
        normalized = dict(data)
        if "trade_id" in normalized:
            normalized["order_id"] = normalized.pop("trade_id")
        if "entry_reasons" in normalized:
            normalized["entry_reason"] = normalized.pop("entry_reasons")
        allowed_fields = set(TradeHistory.__annotations__.keys())
        filtered = {key: value for key, value in normalized.items() if key in allowed_fields}
        return TradeHistory(**filtered)

    @staticmethod
    def _calculate_recovery_factor(group, weekly_stats) -> float:
        """Calculate recovery factor for week."""
        total_pnl = group["pnl_usdt"].sum()
        # Simple recovery: total_pnl / max_drawdown
        return 1.0  # Placeholder

    @staticmethod
    def _calculate_recovery_factor_stats(trades: list[TradeHistory]) -> float:
        """Calculate recovery factor for stats."""
        if not trades:
            return 0.0

        pnls = [t.pnl_usdt for t in trades]
        total_pnl = sum(pnls)
        cumulative = 0.0
        max_dd = 0.0
        peak = 0.0

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            drawdown = cumulative - peak
            if drawdown < -max_dd:
                max_dd = drawdown

        if max_dd == 0:
            return 0.0

        return round(abs(total_pnl / max_dd), 4)


class TradeHistoryStore:
    """
    Backward-compatible storage wrapper used by engine/runtime modules.
    """

    def __init__(self, path: Path | str = Path("data") / "trade_history.jsonl") -> None:
        self.manager = TradeHistoryManager(path)

    def append(self, record: TradeHistory | dict) -> TradeHistory:
        trade = _coerce_trade(record)
        self.manager._append_trade(trade)
        return trade

    def record_trade(self, **kwargs) -> TradeHistory:
        return self.manager.record_trade(**kwargs)

    def load(self, limit: int | None = None) -> list[TradeHistory]:
        trades = self.manager.load_all()
        if limit is None:
            return trades
        return trades[-max(0, int(limit)) :]


def records_from_paper_report(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build trade history records from backtest/paper report payload.
    """
    performance = payload.get("performance", {}) if isinstance(payload, dict) else {}
    trades = performance.get("trades", []) if isinstance(performance, dict) else []
    records: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        hold_hours = float(trade.get("hold_hours", 0.0) or 0.0)
        closed_at = str(trade.get("closed_at") or payload.get("generated_at") or datetime.now(UTC).isoformat())
        records.append(
            {
                "timestamp": closed_at,
                "symbol": str(trade.get("symbol", "")),
                "side": "sell",
                "entry_price": float(trade.get("entry_price", 0.0) or 0.0),
                "exit_price": float(trade.get("exit_price", trade.get("entry_price", 0.0)) or 0.0),
                "quantity": float(trade.get("quantity", 0.0) or 0.0),
                "pnl_usdt": float(trade.get("realized_pnl_usdt", 0.0) or 0.0),
                "pnl_pct": float(trade.get("pnl_pct", 0.0) or 0.0),
                "hold_minutes": int(round(hold_hours * 60)),
                "score": int(trade.get("entry_score", 0) or 0),
                "regime": str(trade.get("regime", "unknown")),
                "entry_reason": list(trade.get("entry_reason", trade.get("entry_reasons", [])) or []),
                "exit_reason": str(trade.get("exit_reason", "")),
                "fees_usdt": float(trade.get("total_fees_usdt", 0.0) or 0.0),
                "order_id": str(
                    trade.get("order_id")
                    or f"{trade.get('symbol', '')}_{trade.get('opened_at', '')}_{trade.get('closed_at', '')}"
                ),
            }
        )
    return records


def sync_records(
    records: Iterable[TradeHistory | dict],
    path: Path | str = Path("data") / "trade_history.jsonl",
) -> list[TradeHistory]:
    """
    Idempotently append records and return full synced history.
    """
    manager = TradeHistoryManager(path)
    existing = manager.load_all()
    existing_fingerprints = {_trade_fingerprint(trade) for trade in existing}

    for item in records:
        trade = _coerce_trade(item)
        fingerprint = _trade_fingerprint(trade)
        if fingerprint in existing_fingerprints:
            continue
        manager._append_trade(trade)
        existing.append(trade)
        existing_fingerprints.add(fingerprint)

    return sorted(existing, key=lambda trade: trade.timestamp)


def _coerce_trade(record: TradeHistory | dict) -> TradeHistory:
    if isinstance(record, TradeHistory):
        return record
    if isinstance(record, dict):
        return TradeHistoryManager._dict_to_trade(record)
    raise TypeError(f"Unsupported trade record type: {type(record)!r}")


def _trade_fingerprint(trade: TradeHistory) -> str:
    return "|".join(
        [
            trade.timestamp,
            trade.symbol,
            f"{trade.entry_price:.8f}",
            f"{float(trade.exit_price or 0.0):.8f}",
            f"{trade.quantity:.8f}",
            trade.order_id,
        ]
    )
