from dataclasses import asdict, dataclass
from math import sqrt
from statistics import mean, pstdev
from typing import Iterable


@dataclass(frozen=True)
class BasicMetrics:
    count: int
    total: float
    average: float
    minimum: float
    maximum: float
    standard_deviation: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StreakMetrics:
    current_win_streak: int
    current_loss_streak: int
    longest_win_streak: int
    longest_loss_streak: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RiskMetrics:
    max_drawdown_percent: float
    current_drawdown_percent: float
    volatility: float
    sharpe_ratio: float

    def to_dict(self) -> dict:
        return asdict(self)


def basic_metrics(values: Iterable[float]) -> BasicMetrics:
    numbers = [float(value) for value in values]
    if not numbers:
        return BasicMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return BasicMetrics(
        count=len(numbers),
        total=round(sum(numbers), 8),
        average=round(mean(numbers), 8),
        minimum=round(min(numbers), 8),
        maximum=round(max(numbers), 8),
        standard_deviation=round(pstdev(numbers), 8) if len(numbers) > 1 else 0.0,
    )


def streak_metrics(pnls: Iterable[float]) -> StreakMetrics:
    current_win = 0
    current_loss = 0
    longest_win = 0
    longest_loss = 0
    active_win = 0
    active_loss = 0

    values = [float(pnl) for pnl in pnls]
    for pnl in values:
        if pnl > 0:
            active_win += 1
            active_loss = 0
        elif pnl < 0:
            active_loss += 1
            active_win = 0
        else:
            active_win = 0
            active_loss = 0
        longest_win = max(longest_win, active_win)
        longest_loss = max(longest_loss, active_loss)

    for pnl in reversed(values):
        if pnl > 0 and current_loss == 0:
            current_win += 1
        elif pnl < 0 and current_win == 0:
            current_loss += 1
        else:
            break

    return StreakMetrics(
        current_win_streak=current_win,
        current_loss_streak=current_loss,
        longest_win_streak=longest_win,
        longest_loss_streak=longest_loss,
    )


def drawdown_series(equity_values: Iterable[float]) -> list[float]:
    peak = None
    drawdowns = []
    for equity in [float(value) for value in equity_values]:
        peak = equity if peak is None else max(peak, equity)
        if peak <= 0:
            drawdowns.append(0.0)
        else:
            drawdowns.append(round(max(0.0, (peak - equity) / peak * 100), 4))
    return drawdowns


def risk_metrics(returns: Iterable[float], equity_values: Iterable[float]) -> RiskMetrics:
    return_values = [float(value) for value in returns]
    equity = [float(value) for value in equity_values]
    drawdowns = drawdown_series(equity)
    volatility = pstdev(return_values) if len(return_values) > 1 else 0.0
    sharpe = 0.0
    if volatility > 0:
        sharpe = mean(return_values) / volatility * sqrt(len(return_values))
    return RiskMetrics(
        max_drawdown_percent=max(drawdowns) if drawdowns else 0.0,
        current_drawdown_percent=drawdowns[-1] if drawdowns else 0.0,
        volatility=round(volatility, 8),
        sharpe_ratio=round(sharpe, 8),
    )


def profit_factor(pnls: Iterable[float]) -> float:
    values = [float(pnl) for pnl in pnls]
    gross_profit = sum(pnl for pnl in values if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in values if pnl < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 8)


def win_rate_percent(pnls: Iterable[float]) -> float:
    values = [float(pnl) for pnl in pnls]
    if not values:
        return 0.0
    wins = sum(1 for pnl in values if pnl > 0)
    return round(wins / len(values) * 100, 4)


def expectancy(pnls: Iterable[float]) -> float:
    values = [float(pnl) for pnl in pnls]
    return round(mean(values), 8) if values else 0.0
