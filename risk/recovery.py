from dataclasses import asdict, dataclass, field
from typing import Literal

from strategy.market_regime import MarketRegimeState


RecoveryMode = Literal["normal", "reduced", "defensive", "survival", "paused"]
RiskState = Literal["green", "yellow", "orange", "red"]


@dataclass(frozen=True)
class RecoveryConfig:
    reduced_drawdown_percent: float = 3.0
    defensive_drawdown_percent: float = 6.0
    survival_drawdown_percent: float = 10.0
    pause_drawdown_percent: float = 12.0
    max_daily_loss_percent: float = 4.0
    max_weekly_loss_percent: float = 8.0
    max_consecutive_losses: int = 4
    reduced_loss_streak: int = 1
    defensive_loss_streak: int = 2
    survival_loss_streak: int = 3
    reduced_size_multiplier: float = 0.75
    defensive_size_multiplier: float = 0.50
    survival_size_multiplier: float = 0.20
    reduced_score_adjustment: int = 5
    defensive_score_adjustment: int = 15
    survival_score_adjustment: int = 25
    reduced_cooldown_multiplier: float = 1.5
    defensive_cooldown_multiplier: float = 2.5
    survival_cooldown_multiplier: float = 4.0
    reduced_extra_cooldown_minutes: int = 60
    defensive_extra_cooldown_minutes: int = 240
    survival_extra_cooldown_minutes: int = 720
    api_error_defensive_count: int = 3
    api_error_survival_count: int = 8
    reconnect_defensive_count: int = 3
    reconnect_survival_count: int = 6
    restore_win_count: int = 3
    restore_max_drawdown_percent: float = 3.0


@dataclass(frozen=True)
class RecoveryContext:
    current_equity_usdt: float = 0.0
    peak_equity_usdt: float = 0.0
    drawdown_percent: float | None = None
    daily_loss_percent: float = 0.0
    weekly_loss_percent: float = 0.0
    consecutive_losses: int = 0
    recent_loss_count: int = 0
    recent_win_count: int = 0
    recent_trade_pnls: tuple[float, ...] = field(default_factory=tuple)
    api_error_count: int = 0
    reconnect_count: int = 0
    volatility_state: str = "unknown"
    manual_pause: bool = False
    emergency_mode: bool = False


@dataclass(frozen=True)
class RecoveryDecision:
    mode: RecoveryMode
    can_trade: bool
    risk_state: RiskState
    drawdown_percent: float
    size_multiplier: float
    score_threshold_adjustment: int
    cooldown_multiplier: float
    extra_cooldown_minutes: int
    recent_loss_count: int
    recent_win_count: int
    consecutive_losses: int
    restore_ready: bool
    wins_needed_to_restore: int
    reasons: list[str]
    warnings: list[str]
    blockers: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_recovery(
    context: RecoveryContext | dict,
    regime: MarketRegimeState | dict | None = None,
    config: RecoveryConfig | None = None,
) -> RecoveryDecision:
    config = config or RecoveryConfig()
    recovery_context = _coerce_context(context)
    regime_state = _coerce_regime(regime) if regime is not None else None

    drawdown = calculate_drawdown_percent(recovery_context)
    recent_loss_count = max(
        recovery_context.recent_loss_count,
        count_recent_losses(recovery_context.recent_trade_pnls),
    )
    recent_win_count = max(
        recovery_context.recent_win_count,
        count_recent_wins(recovery_context.recent_trade_pnls),
    )
    consecutive_losses = max(
        recovery_context.consecutive_losses,
        consecutive_losses_from_pnls(recovery_context.recent_trade_pnls),
    )

    reasons: list[str] = []
    warnings: list[str] = []
    blockers = hard_recovery_blockers(
        recovery_context,
        drawdown_percent=drawdown,
        consecutive_losses=consecutive_losses,
        config=config,
    )

    mode = mode_from_drawdown(drawdown, config=config)
    mode = _most_restrictive(mode, mode_from_losses(consecutive_losses, config=config))
    mode = _most_restrictive(
        mode,
        mode_from_daily_weekly_losses(recovery_context, config=config),
    )
    mode = _most_restrictive(
        mode,
        mode_from_operational_health(recovery_context, config=config),
    )

    volatility_state = recovery_context.volatility_state
    if regime_state is not None:
        volatility_state = regime_state.volatility_state
        if regime_state.regime == "volatile":
            mode = _most_restrictive(mode, "defensive")
            warnings.append("Market regime is volatile")

    if volatility_state == "dangerous":
        mode = _most_restrictive(
            mode,
            "survival" if recent_loss_count > 0 else "defensive",
        )
        warnings.append("Volatility state is dangerous")
    elif volatility_state == "elevated":
        mode = _most_restrictive(mode, "reduced")
        warnings.append("Volatility state is elevated")

    if blockers:
        mode = "paused"

    restore_ready = can_restore_one_step(
        mode=mode,
        drawdown_percent=drawdown,
        recent_win_count=recent_win_count,
        consecutive_losses=consecutive_losses,
        context=recovery_context,
        volatility_state=volatility_state,
        config=config,
    )

    if restore_ready:
        previous_mode = mode
        mode = improve_mode_one_step(mode)
        if mode != previous_mode:
            reasons.append(
                f"Risk restored one step from {previous_mode} to {mode} after clean wins"
            )

    reasons.extend(
        build_recovery_reasons(
            context=recovery_context,
            mode=mode,
            drawdown_percent=drawdown,
            recent_loss_count=recent_loss_count,
            recent_win_count=recent_win_count,
            consecutive_losses=consecutive_losses,
        )
    )
    warnings.extend(
        build_recovery_warnings(
            context=recovery_context,
            mode=mode,
            drawdown_percent=drawdown,
            recent_loss_count=recent_loss_count,
            consecutive_losses=consecutive_losses,
            config=config,
        )
    )

    wins_needed = 0
    if mode not in {"normal", "paused"}:
        wins_needed = max(0, config.restore_win_count - recent_win_count)

    return RecoveryDecision(
        mode=mode,
        can_trade=mode != "paused" and not blockers,
        risk_state=risk_state_for_mode(mode),
        drawdown_percent=round(drawdown, 4),
        size_multiplier=size_multiplier_for_mode(mode, config=config),
        score_threshold_adjustment=score_adjustment_for_mode(mode, config=config),
        cooldown_multiplier=cooldown_multiplier_for_mode(mode, config=config),
        extra_cooldown_minutes=extra_cooldown_minutes_for_mode(mode, config=config),
        recent_loss_count=recent_loss_count,
        recent_win_count=recent_win_count,
        consecutive_losses=consecutive_losses,
        restore_ready=restore_ready,
        wins_needed_to_restore=wins_needed,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=_dedupe(blockers),
    )


def hard_recovery_blockers(
    context: RecoveryContext | dict,
    drawdown_percent: float | None = None,
    consecutive_losses: int | None = None,
    config: RecoveryConfig | None = None,
) -> list[str]:
    config = config or RecoveryConfig()
    recovery_context = _coerce_context(context)
    drawdown = (
        calculate_drawdown_percent(recovery_context)
        if drawdown_percent is None
        else drawdown_percent
    )
    loss_streak = (
        max(
            recovery_context.consecutive_losses,
            consecutive_losses_from_pnls(recovery_context.recent_trade_pnls),
        )
        if consecutive_losses is None
        else consecutive_losses
    )
    blockers = []

    if recovery_context.manual_pause:
        blockers.append("Manual pause is active")

    if recovery_context.emergency_mode:
        blockers.append("Emergency mode is active")

    if drawdown >= config.pause_drawdown_percent:
        blockers.append("Maximum drawdown pause threshold reached")

    if recovery_context.daily_loss_percent >= config.max_daily_loss_percent:
        blockers.append("Maximum daily loss reached")

    if recovery_context.weekly_loss_percent >= config.max_weekly_loss_percent:
        blockers.append("Maximum weekly loss reached")

    if loss_streak >= config.max_consecutive_losses:
        blockers.append("Maximum consecutive losses reached")

    return blockers


def calculate_drawdown_percent(context: RecoveryContext | dict) -> float:
    recovery_context = _coerce_context(context)
    if recovery_context.drawdown_percent is not None:
        return max(0.0, recovery_context.drawdown_percent)

    if recovery_context.peak_equity_usdt <= 0:
        return 0.0

    decline = recovery_context.peak_equity_usdt - recovery_context.current_equity_usdt
    return max(0.0, decline / recovery_context.peak_equity_usdt * 100)


def mode_from_drawdown(
    drawdown_percent: float,
    config: RecoveryConfig | None = None,
) -> RecoveryMode:
    config = config or RecoveryConfig()
    if drawdown_percent >= config.pause_drawdown_percent:
        return "paused"
    if drawdown_percent >= config.survival_drawdown_percent:
        return "survival"
    if drawdown_percent >= config.defensive_drawdown_percent:
        return "defensive"
    if drawdown_percent >= config.reduced_drawdown_percent:
        return "reduced"
    return "normal"


def mode_from_losses(
    consecutive_losses: int,
    config: RecoveryConfig | None = None,
) -> RecoveryMode:
    config = config or RecoveryConfig()
    if consecutive_losses >= config.max_consecutive_losses:
        return "paused"
    if consecutive_losses >= config.survival_loss_streak:
        return "survival"
    if consecutive_losses >= config.defensive_loss_streak:
        return "defensive"
    if consecutive_losses >= config.reduced_loss_streak:
        return "reduced"
    return "normal"


def mode_from_daily_weekly_losses(
    context: RecoveryContext | dict,
    config: RecoveryConfig | None = None,
) -> RecoveryMode:
    config = config or RecoveryConfig()
    recovery_context = _coerce_context(context)
    daily_ratio = _safe_ratio(
        recovery_context.daily_loss_percent,
        config.max_daily_loss_percent,
    )
    weekly_ratio = _safe_ratio(
        recovery_context.weekly_loss_percent,
        config.max_weekly_loss_percent,
    )
    worst_ratio = max(daily_ratio, weekly_ratio)

    if worst_ratio >= 1.0:
        return "paused"
    if worst_ratio >= 0.85:
        return "survival"
    if worst_ratio >= 0.65:
        return "defensive"
    if worst_ratio >= 0.40:
        return "reduced"
    return "normal"


def mode_from_operational_health(
    context: RecoveryContext | dict,
    config: RecoveryConfig | None = None,
) -> RecoveryMode:
    config = config or RecoveryConfig()
    recovery_context = _coerce_context(context)

    if (
        recovery_context.api_error_count >= config.api_error_survival_count
        or recovery_context.reconnect_count >= config.reconnect_survival_count
    ):
        return "survival"

    if (
        recovery_context.api_error_count >= config.api_error_defensive_count
        or recovery_context.reconnect_count >= config.reconnect_defensive_count
    ):
        return "defensive"

    return "normal"


def can_restore_one_step(
    mode: RecoveryMode,
    drawdown_percent: float,
    recent_win_count: int,
    consecutive_losses: int,
    context: RecoveryContext,
    volatility_state: str = "unknown",
    config: RecoveryConfig | None = None,
) -> bool:
    config = config or RecoveryConfig()
    if mode in {"normal", "paused"}:
        return False
    if recent_win_count < config.restore_win_count:
        return False
    if consecutive_losses > 0:
        return False
    if context.daily_loss_percent > 0 or context.weekly_loss_percent > 0:
        return False
    if context.api_error_count or context.reconnect_count:
        return False
    if volatility_state in {"elevated", "dangerous"}:
        return False
    return drawdown_percent <= config.restore_max_drawdown_percent


def improve_mode_one_step(mode: RecoveryMode) -> RecoveryMode:
    if mode == "survival":
        return "defensive"
    if mode == "defensive":
        return "reduced"
    if mode == "reduced":
        return "normal"
    return mode


def size_multiplier_for_mode(
    mode: RecoveryMode,
    config: RecoveryConfig | None = None,
) -> float:
    config = config or RecoveryConfig()
    if mode == "normal":
        return 1.0
    if mode == "reduced":
        return config.reduced_size_multiplier
    if mode == "defensive":
        return config.defensive_size_multiplier
    if mode == "survival":
        return config.survival_size_multiplier
    return 0.0


def score_adjustment_for_mode(
    mode: RecoveryMode,
    config: RecoveryConfig | None = None,
) -> int:
    config = config or RecoveryConfig()
    if mode == "reduced":
        return config.reduced_score_adjustment
    if mode == "defensive":
        return config.defensive_score_adjustment
    if mode == "survival":
        return config.survival_score_adjustment
    if mode == "paused":
        return 100
    return 0


def cooldown_multiplier_for_mode(
    mode: RecoveryMode,
    config: RecoveryConfig | None = None,
) -> float:
    config = config or RecoveryConfig()
    if mode == "reduced":
        return config.reduced_cooldown_multiplier
    if mode == "defensive":
        return config.defensive_cooldown_multiplier
    if mode == "survival":
        return config.survival_cooldown_multiplier
    if mode == "paused":
        return 0.0
    return 1.0


def extra_cooldown_minutes_for_mode(
    mode: RecoveryMode,
    config: RecoveryConfig | None = None,
) -> int:
    config = config or RecoveryConfig()
    if mode == "reduced":
        return config.reduced_extra_cooldown_minutes
    if mode == "defensive":
        return config.defensive_extra_cooldown_minutes
    if mode == "survival":
        return config.survival_extra_cooldown_minutes
    if mode == "paused":
        return 1440
    return 0


def risk_state_for_mode(mode: RecoveryMode) -> RiskState:
    if mode == "normal":
        return "green"
    if mode == "reduced":
        return "yellow"
    if mode == "defensive":
        return "orange"
    return "red"


def apply_recovery_to_score_threshold(
    base_threshold: int,
    decision: RecoveryDecision | dict,
) -> int:
    recovery_decision = _coerce_decision(decision)
    return min(100, base_threshold + recovery_decision.score_threshold_adjustment)


def apply_recovery_to_position_size(
    proposed_usdt: float,
    decision: RecoveryDecision | dict,
) -> float:
    recovery_decision = _coerce_decision(decision)
    return round(max(0.0, proposed_usdt * recovery_decision.size_multiplier), 4)


def apply_recovery_to_cooldown_minutes(
    base_minutes: int,
    decision: RecoveryDecision | dict,
) -> int:
    recovery_decision = _coerce_decision(decision)
    amplified = base_minutes * recovery_decision.cooldown_multiplier
    amplified += recovery_decision.extra_cooldown_minutes
    return max(0, int(amplified))


def count_recent_losses(pnls: tuple[float, ...] | list[float]) -> int:
    return sum(1 for pnl in pnls if pnl < 0)


def count_recent_wins(pnls: tuple[float, ...] | list[float]) -> int:
    return sum(1 for pnl in pnls if pnl > 0)


def consecutive_losses_from_pnls(pnls: tuple[float, ...] | list[float]) -> int:
    streak = 0
    for pnl in reversed(tuple(pnls)):
        if pnl < 0:
            streak += 1
            continue
        break
    return streak


def build_recovery_reasons(
    context: RecoveryContext,
    mode: RecoveryMode,
    drawdown_percent: float,
    recent_loss_count: int,
    recent_win_count: int,
    consecutive_losses: int,
) -> list[str]:
    reasons = [
        f"Recovery mode is {mode}",
        f"Drawdown is {drawdown_percent:.2f}%",
    ]

    if recent_loss_count:
        reasons.append(f"Recent loss count is {recent_loss_count}")

    if recent_win_count:
        reasons.append(f"Recent win count is {recent_win_count}")

    if consecutive_losses:
        reasons.append(f"Consecutive losses are {consecutive_losses}")

    if context.api_error_count:
        reasons.append(f"API error count is {context.api_error_count}")

    if context.reconnect_count:
        reasons.append(f"Reconnect count is {context.reconnect_count}")

    return reasons


def build_recovery_warnings(
    context: RecoveryContext,
    mode: RecoveryMode,
    drawdown_percent: float,
    recent_loss_count: int,
    consecutive_losses: int,
    config: RecoveryConfig,
) -> list[str]:
    warnings = []

    if mode in {"defensive", "survival"}:
        warnings.append(f"Risk is restricted because mode is {mode}")

    if drawdown_percent >= config.reduced_drawdown_percent:
        warnings.append(f"Drawdown is elevated at {drawdown_percent:.2f}%")

    if recent_loss_count:
        warnings.append("Recent losses are reducing risk")

    if consecutive_losses:
        warnings.append("Loss streak is active")

    if context.daily_loss_percent > 0:
        warnings.append(f"Daily loss is {context.daily_loss_percent:.2f}%")

    if context.weekly_loss_percent > 0:
        warnings.append(f"Weekly loss is {context.weekly_loss_percent:.2f}%")

    if context.api_error_count >= config.api_error_defensive_count:
        warnings.append("API instability is forcing defensive behavior")

    if context.reconnect_count >= config.reconnect_defensive_count:
        warnings.append("Reconnect instability is forcing defensive behavior")

    return warnings


def _coerce_context(context: RecoveryContext | dict) -> RecoveryContext:
    if isinstance(context, RecoveryContext):
        return context

    recent_trade_pnls = context.get("recent_trade_pnls", context.get("trade_pnls", ()))
    if recent_trade_pnls is None:
        recent_trade_pnls = ()

    drawdown = context.get("drawdown_percent")
    return RecoveryContext(
        current_equity_usdt=_safe_float(
            context.get("current_equity_usdt", context.get("equity_usdt", 0.0))
        ),
        peak_equity_usdt=_safe_float(
            context.get("peak_equity_usdt", context.get("peak_equity", 0.0))
        ),
        drawdown_percent=None if drawdown is None else _safe_float(drawdown),
        daily_loss_percent=_safe_float(context.get("daily_loss_percent", 0.0)),
        weekly_loss_percent=_safe_float(context.get("weekly_loss_percent", 0.0)),
        consecutive_losses=int(context.get("consecutive_losses", 0)),
        recent_loss_count=int(context.get("recent_loss_count", 0)),
        recent_win_count=int(context.get("recent_win_count", 0)),
        recent_trade_pnls=tuple(_safe_float(pnl) for pnl in recent_trade_pnls),
        api_error_count=int(context.get("api_error_count", 0)),
        reconnect_count=int(context.get("reconnect_count", 0)),
        volatility_state=str(context.get("volatility_state", "unknown")),
        manual_pause=bool(context.get("manual_pause", False)),
        emergency_mode=bool(context.get("emergency_mode", False)),
    )


def _coerce_regime(regime: MarketRegimeState | dict) -> MarketRegimeState:
    if isinstance(regime, MarketRegimeState):
        return regime

    return MarketRegimeState(
        regime=regime.get("regime", "unknown"),
        strength=int(regime.get("strength", 0)),
        confidence=regime.get("confidence", "low"),
        volatility_state=regime.get("volatility_state", "unknown"),
        trend_strength=regime.get("trend_strength", "unknown"),
        warnings=list(regime.get("warnings", [])),
        reasons=list(regime.get("reasons", [])),
        details=dict(regime.get("details", {})),
    )


def _coerce_decision(decision: RecoveryDecision | dict) -> RecoveryDecision:
    if isinstance(decision, RecoveryDecision):
        return decision

    return RecoveryDecision(
        mode=decision.get("mode", "normal"),
        can_trade=bool(decision.get("can_trade", True)),
        risk_state=decision.get("risk_state", "green"),
        drawdown_percent=_safe_float(decision.get("drawdown_percent", 0.0)),
        size_multiplier=_safe_float(decision.get("size_multiplier", 1.0)),
        score_threshold_adjustment=int(
            decision.get("score_threshold_adjustment", 0)
        ),
        cooldown_multiplier=_safe_float(decision.get("cooldown_multiplier", 1.0)),
        extra_cooldown_minutes=int(decision.get("extra_cooldown_minutes", 0)),
        recent_loss_count=int(decision.get("recent_loss_count", 0)),
        recent_win_count=int(decision.get("recent_win_count", 0)),
        consecutive_losses=int(decision.get("consecutive_losses", 0)),
        restore_ready=bool(decision.get("restore_ready", False)),
        wins_needed_to_restore=int(decision.get("wins_needed_to_restore", 0)),
        reasons=list(decision.get("reasons", [])),
        warnings=list(decision.get("warnings", [])),
        blockers=list(decision.get("blockers", [])),
    )


def _most_restrictive(first: RecoveryMode, second: RecoveryMode) -> RecoveryMode:
    return first if _mode_rank(first) >= _mode_rank(second) else second


def _mode_rank(mode: RecoveryMode) -> int:
    ranks = {
        "normal": 0,
        "reduced": 1,
        "defensive": 2,
        "survival": 3,
        "paused": 4,
    }
    return ranks.get(mode, 0)


def _safe_ratio(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return max(0.0, value / maximum)


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
