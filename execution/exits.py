from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal

from config import STOP_LOSS_PERCENT, TRAILING_STOP_PERCENT
from strategy.market_regime import MarketRegimeState
from strategy.signals import SignalSnapshot


ExitAction = Literal[
    "hold",
    "partial_take_profit",
    "update_trailing_stop",
    "trailing_stop",
    "stop_loss",
    "emergency_exit",
    "time_exit",
    "full_exit",
]


@dataclass(frozen=True)
class ExitConfig:
    first_take_profit_percent: float = 2.0
    first_take_profit_fraction: float = 0.25
    second_take_profit_percent: float = 4.0
    second_take_profit_fraction: float = 0.25
    trailing_activation_percent: float = 6.0
    atr_stop_multiplier: float = 2.0
    trailing_atr_multiplier: float = 1.5
    fallback_stop_loss_percent: float = STOP_LOSS_PERCENT
    min_trailing_distance_percent: float = TRAILING_STOP_PERCENT
    emergency_market_stress_threshold: int = 90
    time_exit_hours: int = 48
    stagnation_profit_percent: float = 0.75
    time_exit_fraction: float = 0.50
    min_exit_fraction: float = 0.10


@dataclass(frozen=True)
class PositionState:
    symbol: str
    entry_price: float
    quantity: float
    current_price: float
    opened_at: datetime | None = None
    highest_price: float | None = None
    trailing_stop_price: float | None = None
    partial_exits_taken: tuple[str, ...] = field(default_factory=tuple)
    entry_type: str = "unknown"
    fees_paid_usdt: float = 0.0


@dataclass(frozen=True)
class ExitContext:
    position: PositionState | dict
    signals: SignalSnapshot | dict | None = None
    regime: MarketRegimeState | dict | None = None
    market_stress_score: int | None = None
    atr: float | None = None
    atr_percent: float | None = None
    now: datetime | str | None = None


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    action: ExitAction
    symbol: str
    exit_fraction: float
    quantity_to_exit: float
    estimated_exit_value_usdt: float
    profit_percent: float
    stop_loss_price: float
    trailing_stop_price: float | None
    updated_trailing_stop_price: float | None
    next_take_profit_percent: float | None
    exit_reason: str
    hold_reason: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_exit(
    context: ExitContext | dict,
    config: ExitConfig | None = None,
) -> ExitDecision:
    config = config or ExitConfig()
    exit_context = _coerce_context(context)
    position = _coerce_position(exit_context.position)
    signals = _coerce_signals(exit_context.signals)
    regime = _coerce_regime(exit_context.regime)
    now = _normalize_datetime(exit_context.now) or datetime.now(UTC)

    blockers = validate_position(position)
    if blockers:
        return _decision(
            should_exit=False,
            action="hold",
            position=position,
            exit_fraction=0.0,
            stop_loss_price=0.0,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=position.trailing_stop_price,
            next_take_profit_percent=None,
            exit_reason="",
            hold_reason="Position data is invalid",
            profit_percent=0.0,
            reasons=[],
            warnings=[],
            blockers=blockers,
        )

    profit = calculate_profit_percent(position)
    atr_value = resolve_atr(
        position=position,
        signals=signals,
        regime=regime,
        atr=exit_context.atr,
        atr_percent=exit_context.atr_percent,
    )
    stop_loss_price = calculate_atr_stop_loss(
        entry_price=position.entry_price,
        atr=atr_value,
        config=config,
    )
    highest_price = max(
        position.highest_price or position.current_price,
        position.current_price,
    )
    updated_trailing_stop = calculate_trailing_stop(
        position=position,
        highest_price=highest_price,
        atr=atr_value,
        profit_percent=profit,
        config=config,
    )
    market_stress = (
        clamp_int(exit_context.market_stress_score, 0, 100)
        if exit_context.market_stress_score is not None
        else estimate_exit_stress(signals=signals, regime=regime, atr_percent=_atr_percent(
            atr_value,
            position.current_price,
        ))
    )

    reasons = build_exit_reasons(
        position=position,
        profit_percent=profit,
        stop_loss_price=stop_loss_price,
        updated_trailing_stop_price=updated_trailing_stop,
        market_stress_score=market_stress,
        regime=regime,
    )
    warnings = build_exit_warnings(
        signals=signals,
        regime=regime,
        market_stress_score=market_stress,
    )

    emergency_reason = emergency_exit_reason(
        signals=signals,
        regime=regime,
        market_stress_score=market_stress,
        config=config,
    )
    if emergency_reason:
        return _decision(
            should_exit=True,
            action="emergency_exit",
            position=position,
            exit_fraction=1.0,
            stop_loss_price=stop_loss_price,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=updated_trailing_stop,
            next_take_profit_percent=next_take_profit_percent(position, config=config),
            exit_reason=emergency_reason,
            hold_reason="",
            profit_percent=profit,
            reasons=reasons,
            warnings=warnings,
            blockers=[],
        )

    if position.current_price <= stop_loss_price:
        return _decision(
            should_exit=True,
            action="stop_loss",
            position=position,
            exit_fraction=1.0,
            stop_loss_price=stop_loss_price,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=updated_trailing_stop,
            next_take_profit_percent=next_take_profit_percent(position, config=config),
            exit_reason="ATR adaptive stop loss hit",
            hold_reason="",
            profit_percent=profit,
            reasons=reasons,
            warnings=warnings,
            blockers=[],
        )

    if (
        updated_trailing_stop is not None
        and position.current_price <= updated_trailing_stop
        and profit > 0
    ):
        return _decision(
            should_exit=True,
            action="trailing_stop",
            position=position,
            exit_fraction=1.0,
            stop_loss_price=stop_loss_price,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=updated_trailing_stop,
            next_take_profit_percent=next_take_profit_percent(position, config=config),
            exit_reason="Trailing stop protected open profit",
            hold_reason="",
            profit_percent=profit,
            reasons=reasons,
            warnings=warnings,
            blockers=[],
        )

    partial_exit = partial_take_profit_decision(
        position=position,
        profit_percent=profit,
        config=config,
    )
    if partial_exit is not None:
        label, fraction, reason = partial_exit
        return _decision(
            should_exit=True,
            action="partial_take_profit",
            position=position,
            exit_fraction=fraction,
            stop_loss_price=stop_loss_price,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=updated_trailing_stop,
            next_take_profit_percent=next_take_profit_percent(
                position,
                just_taken=label,
                config=config,
            ),
            exit_reason=reason,
            hold_reason="",
            profit_percent=profit,
            reasons=reasons,
            warnings=warnings,
            blockers=[],
        )

    time_exit = time_based_exit_decision(
        position=position,
        now=now,
        profit_percent=profit,
        regime=regime,
        config=config,
    )
    if time_exit is not None:
        fraction, reason = time_exit
        return _decision(
            should_exit=True,
            action="time_exit",
            position=position,
            exit_fraction=fraction,
            stop_loss_price=stop_loss_price,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=updated_trailing_stop,
            next_take_profit_percent=next_take_profit_percent(position, config=config),
            exit_reason=reason,
            hold_reason="",
            profit_percent=profit,
            reasons=reasons,
            warnings=warnings,
            blockers=[],
        )

    if updated_trailing_stop is not None and updated_trailing_stop != position.trailing_stop_price:
        return _decision(
            should_exit=False,
            action="update_trailing_stop",
            position=position,
            exit_fraction=0.0,
            stop_loss_price=stop_loss_price,
            trailing_stop_price=position.trailing_stop_price,
            updated_trailing_stop_price=updated_trailing_stop,
            next_take_profit_percent=next_take_profit_percent(position, config=config),
            exit_reason="",
            hold_reason="Trailing stop should be raised",
            profit_percent=profit,
            reasons=reasons,
            warnings=warnings,
            blockers=[],
        )

    return _decision(
        should_exit=False,
        action="hold",
        position=position,
        exit_fraction=0.0,
        stop_loss_price=stop_loss_price,
        trailing_stop_price=position.trailing_stop_price,
        updated_trailing_stop_price=updated_trailing_stop,
        next_take_profit_percent=next_take_profit_percent(position, config=config),
        exit_reason="",
        hold_reason="No exit condition is active",
        profit_percent=profit,
        reasons=reasons,
        warnings=warnings,
        blockers=[],
    )


def build_exit_decision(
    context: ExitContext | dict,
    config: ExitConfig | None = None,
) -> ExitDecision:
    return evaluate_exit(context, config=config)


def validate_position(position: PositionState) -> list[str]:
    blockers = []
    if not position.symbol:
        blockers.append("Position symbol is missing")
    if position.entry_price <= 0:
        blockers.append("Position entry price is invalid")
    if position.current_price <= 0:
        blockers.append("Position current price is invalid")
    if position.quantity <= 0:
        blockers.append("Position quantity is invalid")
    return blockers


def calculate_profit_percent(position: PositionState | dict) -> float:
    position_state = _coerce_position(position)
    if position_state.entry_price <= 0:
        return 0.0
    return (position_state.current_price - position_state.entry_price) / position_state.entry_price * 100


def resolve_atr(
    position: PositionState,
    signals: SignalSnapshot,
    regime: MarketRegimeState,
    atr: float | None = None,
    atr_percent: float | None = None,
) -> float | None:
    if atr is not None and atr > 0:
        return atr

    if atr_percent is None:
        atr_percent = _detail_float(signals.details, "atr_percent")

    if atr_percent is None:
        atr_percent = _detail_float(regime.details, "atr_percent")

    if atr_percent is not None and atr_percent > 0:
        return position.current_price * atr_percent / 100

    return _detail_float(signals.details, "atr") or _detail_float(regime.details, "atr")


def calculate_atr_stop_loss(
    entry_price: float,
    atr: float | None,
    config: ExitConfig | None = None,
) -> float:
    config = config or ExitConfig()
    if atr is not None and atr > 0:
        return round(max(0.0, entry_price - (atr * config.atr_stop_multiplier)), 8)
    return round(entry_price * (1 - config.fallback_stop_loss_percent / 100), 8)


def calculate_trailing_stop(
    position: PositionState,
    highest_price: float,
    atr: float | None,
    profit_percent: float,
    config: ExitConfig | None = None,
) -> float | None:
    config = config or ExitConfig()
    if profit_percent < config.trailing_activation_percent:
        return position.trailing_stop_price

    min_distance = highest_price * config.min_trailing_distance_percent / 100
    atr_distance = atr * config.trailing_atr_multiplier if atr is not None and atr > 0 else 0
    trailing_distance = max(min_distance, atr_distance)
    proposed_stop = highest_price - trailing_distance

    # Once trailing activates, do not let a winning trade trail below breakeven.
    proposed_stop = max(proposed_stop, position.entry_price)
    if position.trailing_stop_price is not None:
        proposed_stop = max(proposed_stop, position.trailing_stop_price)

    return round(proposed_stop, 8)


def partial_take_profit_decision(
    position: PositionState,
    profit_percent: float,
    config: ExitConfig | None = None,
) -> tuple[str, float, str] | None:
    config = config or ExitConfig()
    taken = set(position.partial_exits_taken)

    if profit_percent >= config.first_take_profit_percent and "tp1" not in taken:
        return (
            "tp1",
            config.first_take_profit_fraction,
            f"First take profit reached at {profit_percent:.2f}%",
        )

    if profit_percent >= config.second_take_profit_percent and "tp2" not in taken:
        return (
            "tp2",
            config.second_take_profit_fraction,
            f"Second take profit reached at {profit_percent:.2f}%",
        )

    return None


def time_based_exit_decision(
    position: PositionState,
    now: datetime,
    profit_percent: float,
    regime: MarketRegimeState,
    config: ExitConfig | None = None,
) -> tuple[float, str] | None:
    config = config or ExitConfig()
    if position.opened_at is None:
        return None

    age_hours = (now - _normalize_datetime(position.opened_at)).total_seconds() / 3600
    if age_hours < config.time_exit_hours:
        return None

    if profit_percent <= config.stagnation_profit_percent:
        if profit_percent < 0 or regime.regime in {"bearish", "volatile"}:
            return 1.0, f"Time exit after {age_hours:.1f}h with weak position"
        return (
            config.time_exit_fraction,
            f"Partial time exit after {age_hours:.1f}h of stagnation",
        )

    return None


def emergency_exit_reason(
    signals: SignalSnapshot,
    regime: MarketRegimeState,
    market_stress_score: int,
    config: ExitConfig | None = None,
) -> str | None:
    config = config or ExitConfig()

    if market_stress_score >= config.emergency_market_stress_threshold:
        return f"Emergency exit: market stress is {market_stress_score}"

    if regime.volatility_state == "dangerous":
        return "Emergency exit: volatility is dangerous"

    if regime.regime == "bearish" and regime.confidence == "high":
        return "Emergency exit: regime flipped hard bearish"

    if signals.trend == "bearish" and signals.momentum == "bearish":
        return "Emergency exit: bearish signal and bearish momentum"

    return None


def next_take_profit_percent(
    position: PositionState,
    just_taken: str | None = None,
    config: ExitConfig | None = None,
) -> float | None:
    config = config or ExitConfig()
    taken = set(position.partial_exits_taken)
    if just_taken:
        taken.add(just_taken)

    if "tp1" not in taken:
        return config.first_take_profit_percent
    if "tp2" not in taken:
        return config.second_take_profit_percent
    return None


def estimate_exit_stress(
    signals: SignalSnapshot,
    regime: MarketRegimeState,
    atr_percent: float | None = None,
) -> int:
    stress = 0
    if regime.regime == "volatile":
        stress += 35
    elif regime.regime == "bearish":
        stress += 25

    if regime.volatility_state == "dangerous":
        stress += 45
    elif regime.volatility_state == "elevated":
        stress += 25

    if signals.trend == "bearish":
        stress += 15

    if signals.momentum == "bearish":
        stress += 15

    if not signals.volatility_safe:
        stress += 10

    if atr_percent is not None:
        if atr_percent >= 6:
            stress += 20
        elif atr_percent >= 4:
            stress += 10

    return clamp_int(stress, 0, 100)


def build_exit_reasons(
    position: PositionState,
    profit_percent: float,
    stop_loss_price: float,
    updated_trailing_stop_price: float | None,
    market_stress_score: int,
    regime: MarketRegimeState,
) -> list[str]:
    reasons = [
        f"Position profit is {profit_percent:.2f}%",
        f"ATR adaptive stop is {stop_loss_price:.8f}",
        f"Market stress score is {market_stress_score}",
        f"Regime is {regime.regime}",
    ]

    if updated_trailing_stop_price is not None:
        reasons.append(f"Trailing stop is {updated_trailing_stop_price:.8f}")

    if position.entry_type:
        reasons.append(f"Entry type was {position.entry_type}")

    return reasons


def build_exit_warnings(
    signals: SignalSnapshot,
    regime: MarketRegimeState,
    market_stress_score: int,
) -> list[str]:
    warnings = []

    if market_stress_score >= 70:
        warnings.append(f"Market stress is elevated at {market_stress_score}")

    if regime.regime in {"bearish", "volatile"}:
        warnings.append(f"Regime is {regime.regime}")

    if regime.volatility_state in {"elevated", "dangerous"}:
        warnings.append(f"Volatility state is {regime.volatility_state}")

    warnings.extend(signals.warnings)
    warnings.extend(regime.warnings)
    return _dedupe(warnings)


def _decision(
    should_exit: bool,
    action: ExitAction,
    position: PositionState,
    exit_fraction: float,
    stop_loss_price: float,
    trailing_stop_price: float | None,
    updated_trailing_stop_price: float | None,
    next_take_profit_percent: float | None,
    exit_reason: str,
    hold_reason: str,
    profit_percent: float,
    reasons: list[str],
    warnings: list[str],
    blockers: list[str],
) -> ExitDecision:
    exit_fraction = clamp_float(exit_fraction, 0.0, 1.0)
    quantity_to_exit = position.quantity * exit_fraction
    return ExitDecision(
        should_exit=should_exit,
        action=action,
        symbol=position.symbol,
        exit_fraction=round(exit_fraction, 4),
        quantity_to_exit=round(quantity_to_exit, 8),
        estimated_exit_value_usdt=round(quantity_to_exit * position.current_price, 4),
        profit_percent=round(profit_percent, 4),
        stop_loss_price=round(stop_loss_price, 8),
        trailing_stop_price=trailing_stop_price,
        updated_trailing_stop_price=updated_trailing_stop_price,
        next_take_profit_percent=next_take_profit_percent,
        exit_reason=exit_reason,
        hold_reason=hold_reason,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=_dedupe(blockers),
    )


def _coerce_context(context: ExitContext | dict) -> ExitContext:
    if isinstance(context, ExitContext):
        return context

    return ExitContext(
        position=context.get("position", {}),
        signals=context.get("signals"),
        regime=context.get("regime"),
        market_stress_score=context.get("market_stress_score"),
        atr=_optional_float(context.get("atr")),
        atr_percent=_optional_float(context.get("atr_percent")),
        now=context.get("now"),
    )


def _coerce_position(position: PositionState | dict) -> PositionState:
    if isinstance(position, PositionState):
        return position

    opened_at = _normalize_datetime(position.get("opened_at"))
    partials = position.get(
        "partial_exits_taken",
        position.get("partial_exits", ()),
    )
    if partials is None:
        partials = ()

    return PositionState(
        symbol=str(position.get("symbol", "")),
        entry_price=_safe_float(position.get("entry_price", position.get("avg", 0.0))),
        quantity=_safe_float(position.get("quantity", position.get("qty", 0.0))),
        current_price=_safe_float(
            position.get("current_price", position.get("price", 0.0))
        ),
        opened_at=opened_at,
        highest_price=_optional_float(position.get("highest_price")),
        trailing_stop_price=_optional_float(position.get("trailing_stop_price")),
        partial_exits_taken=tuple(str(item) for item in partials),
        entry_type=str(position.get("entry_type", "unknown")),
        fees_paid_usdt=_safe_float(position.get("fees_paid_usdt", 0.0)),
    )


def _coerce_signals(signals: SignalSnapshot | dict | None) -> SignalSnapshot:
    if isinstance(signals, SignalSnapshot):
        return signals

    if signals is None:
        signals = {}

    return SignalSnapshot(
        trend=signals.get("trend", "unknown"),
        momentum=signals.get("momentum", "unknown"),
        dip=bool(signals.get("dip", False)),
        volatility_safe=bool(signals.get("volatility_safe", True)),
        volume_confirmed=bool(signals.get("volume_confirmed", False)),
        strength=int(signals.get("strength", 0)),
        reasons=list(signals.get("reasons", [])),
        warnings=list(signals.get("warnings", [])),
        details=dict(signals.get("details", {})),
    )


def _coerce_regime(regime: MarketRegimeState | dict | None) -> MarketRegimeState:
    if isinstance(regime, MarketRegimeState):
        return regime

    if regime is None:
        regime = {}

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


def _normalize_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _detail_float(details: dict, key: str) -> float | None:
    value = details.get(key)
    return _optional_float(value)


def _atr_percent(atr: float | None, price: float) -> float | None:
    if atr is None or atr <= 0 or price <= 0:
        return None
    return atr / price * 100


def clamp_int(value: object, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
