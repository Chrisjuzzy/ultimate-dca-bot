from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class EquityProtectionConfig:
    caution_drawdown_percent: float = 3.0
    defensive_drawdown_percent: float = 6.0
    survival_drawdown_percent: float = 10.0
    pause_drawdown_percent: float = 15.0
    weak_curve_lookback_points: int = 20


@dataclass(frozen=True)
class EquityProtectionState:
    mode: str
    drawdown_percent: float
    trading_allowed: bool
    size_multiplier: float
    score_adjustment: int
    cooldown_multiplier: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_equity_protection(
    equity_curve: list[dict],
    current_equity_usdt: float | None = None,
    peak_equity_usdt: float | None = None,
    config: EquityProtectionConfig | None = None,
) -> EquityProtectionState:
    config = config or EquityProtectionConfig()
    equity_values = [
        _safe_float(point.get("equity_usdt"))
        for point in equity_curve
        if isinstance(point, dict)
    ]
    if current_equity_usdt is None:
        current_equity_usdt = equity_values[-1] if equity_values else 0.0
    if peak_equity_usdt is None:
        peak_equity_usdt = max(equity_values, default=current_equity_usdt)

    drawdown = calculate_drawdown_percent(current_equity_usdt, peak_equity_usdt)
    weak_curve = is_equity_curve_weak(equity_values, config=config)
    reasons = [f"Current drawdown is {drawdown:.2f}%"]
    warnings: list[str] = []

    mode = "normal"
    trading_allowed = True
    size_multiplier = 1.0
    score_adjustment = 0
    cooldown_multiplier = 1.0

    if drawdown >= config.pause_drawdown_percent:
        mode = "paused"
        trading_allowed = False
        size_multiplier = 0.0
        score_adjustment = 30
        cooldown_multiplier = 4.0
        warnings.append("Equity protection paused new trades")
    elif drawdown >= config.survival_drawdown_percent:
        mode = "survival"
        size_multiplier = 0.25
        score_adjustment = 20
        cooldown_multiplier = 3.0
    elif drawdown >= config.defensive_drawdown_percent:
        mode = "defensive"
        size_multiplier = 0.5
        score_adjustment = 10
        cooldown_multiplier = 2.0
    elif drawdown >= config.caution_drawdown_percent or weak_curve:
        mode = "caution"
        size_multiplier = 0.75
        score_adjustment = 5
        cooldown_multiplier = 1.4

    if weak_curve:
        reasons.append("Recent equity curve is weakening")
        warnings.append("Equity curve protection is reducing aggression")

    return EquityProtectionState(
        mode=mode,
        drawdown_percent=round(drawdown, 4),
        trading_allowed=trading_allowed,
        size_multiplier=size_multiplier,
        score_adjustment=score_adjustment,
        cooldown_multiplier=cooldown_multiplier,
        reasons=reasons,
        warnings=warnings,
    )


def calculate_drawdown_percent(current_equity: float, peak_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity * 100)


def is_equity_curve_weak(
    equity_values: list[float],
    config: EquityProtectionConfig | None = None,
) -> bool:
    config = config or EquityProtectionConfig()
    lookback = config.weak_curve_lookback_points
    if len(equity_values) < max(5, lookback):
        return False
    recent = equity_values[-lookback:]
    return recent[-1] < recent[0] and max(recent) == recent[0]


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
