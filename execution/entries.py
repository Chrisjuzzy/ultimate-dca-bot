from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

from config import BASE_TRADE_USDT
from risk.exposure import ExposureDecision
from risk.position_sizing import PositionSizeRecommendation
from risk.recovery import RecoveryDecision
from strategy.cooldown import CooldownDecision
from strategy.market_regime import MarketRegimeState
from strategy.scoring import OpportunityScore
from strategy.signals import SignalSnapshot


EntryStatus = Literal["approved", "reduced", "blocked"]
EntryType = Literal[
    "trend_continuation",
    "pullback_continuation",
    "breakout",
    "defensive_scalp",
    "no_entry",
]
EntryGrade = Literal["A+", "A", "B", "C", "Avoid"]


@dataclass(frozen=True)
class EntryConfig:
    min_trade_usdt: float = BASE_TRADE_USDT
    allowed_regimes: tuple[str, ...] = ("bullish", "sideways")
    blocked_recovery_modes: tuple[str, ...] = ("paused",)
    blocked_volatility_states: tuple[str, ...] = ("dangerous",)
    block_low_confidence: bool = True
    require_valid_entry_type: bool = True
    allow_c_grade_entries: bool = False
    apply_recovery_size_multiplier: bool = True
    market_stress_reduce_threshold: int = 60
    market_stress_block_threshold: int = 85
    elevated_stress_score_adjustment: int = 5
    high_stress_score_adjustment: int = 10
    medium_confidence_size_multiplier: float = 0.75
    b_grade_size_multiplier: float = 0.65
    sideways_size_multiplier: float = 0.70
    elevated_stress_size_multiplier: float = 0.75
    high_stress_size_multiplier: float = 0.50


@dataclass(frozen=True)
class EntryContext:
    symbol: str
    signals: SignalSnapshot | dict
    score: OpportunityScore | dict
    regime: MarketRegimeState | dict
    cooldown: CooldownDecision | dict
    position_size: PositionSizeRecommendation | dict
    exposure: ExposureDecision | dict
    recovery: RecoveryDecision | dict
    wallet: dict | None = None
    positions: dict | list | None = None
    market_stress_score: int | None = None


@dataclass(frozen=True)
class EntryDecision:
    can_enter: bool
    status: EntryStatus
    symbol: str
    approved_size: float
    requested_size: float
    entry_grade: EntryGrade
    confidence: str
    mode: str
    entry_type: EntryType
    market_stress_score: int
    score: int
    required_score: int
    base_required_score: int
    recovery_score_adjustment: int
    stress_score_adjustment: int
    size_multiplier: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    adjustments: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_entry(
    context: EntryContext | dict,
    config: EntryConfig | None = None,
) -> EntryDecision:
    config = config or EntryConfig()
    entry_context = _coerce_context(context)

    signals = _coerce_signals(entry_context.signals)
    score = _coerce_score(entry_context.score)
    regime = _coerce_regime(entry_context.regime)
    cooldown = _coerce_cooldown(entry_context.cooldown)
    sizing = _coerce_sizing(entry_context.position_size)
    exposure = _coerce_exposure(entry_context.exposure)
    recovery = _coerce_recovery(entry_context.recovery)

    market_stress = (
        clamp_int(entry_context.market_stress_score, 0, 100)
        if entry_context.market_stress_score is not None
        else calculate_market_stress_score(signals, regime)
    )
    stress_score_adjustment = stress_threshold_adjustment(
        market_stress,
        config=config,
    )
    base_required_score = cooldown.required_score
    required_score = min(
        100,
        base_required_score
        + recovery.score_threshold_adjustment
        + stress_score_adjustment,
    )
    entry_type = classify_entry_type(
        signals=signals,
        score=score,
        regime=regime,
        recovery=recovery,
        required_score=required_score,
    )

    blockers = build_hard_blockers(
        signals=signals,
        score=score,
        regime=regime,
        cooldown=cooldown,
        sizing=sizing,
        exposure=exposure,
        recovery=recovery,
        entry_type=entry_type,
        required_score=required_score,
        market_stress_score=market_stress,
        config=config,
    )
    warnings = build_entry_warnings(
        signals=signals,
        score=score,
        regime=regime,
        cooldown=cooldown,
        sizing=sizing,
        exposure=exposure,
        recovery=recovery,
        market_stress_score=market_stress,
        config=config,
    )
    reasons = build_entry_reasons(
        symbol=entry_context.symbol,
        signals=signals,
        score=score,
        regime=regime,
        cooldown=cooldown,
        sizing=sizing,
        exposure=exposure,
        recovery=recovery,
        entry_type=entry_type,
        required_score=required_score,
        market_stress_score=market_stress,
    )

    entry_grade = determine_entry_grade(
        score=score,
        regime=regime,
        recovery=recovery,
        market_stress_score=market_stress,
        blockers=blockers,
        config=config,
    )
    requested_size = min(
        max(0.0, sizing.recommended_usdt),
        max(0.0, exposure.approved_usdt),
    )
    size_multiplier = final_size_multiplier(
        entry_grade=entry_grade,
        score=score,
        regime=regime,
        recovery=recovery,
        market_stress_score=market_stress,
        config=config,
    )
    approved_size = round(requested_size * size_multiplier, 4)

    if not blockers and approved_size < config.min_trade_usdt:
        blockers.append("Final approved size is below minimum trade size")
        approved_size = 0.0

    if blockers:
        status: EntryStatus = "blocked"
        approved_size = 0.0
        can_enter = False
    else:
        can_enter = True
        status = "approved"
        if approved_size < requested_size or any(
            warning.startswith("Soft reduction") for warning in warnings
        ):
            status = "reduced"

    return EntryDecision(
        can_enter=can_enter,
        status=status,
        symbol=entry_context.symbol,
        approved_size=round(approved_size, 4),
        requested_size=round(requested_size, 4),
        entry_grade=entry_grade,
        confidence=score.confidence,
        mode=recovery.mode,
        entry_type=entry_type if can_enter else "no_entry",
        market_stress_score=market_stress,
        score=score.score,
        required_score=required_score,
        base_required_score=base_required_score,
        recovery_score_adjustment=recovery.score_threshold_adjustment,
        stress_score_adjustment=stress_score_adjustment,
        size_multiplier=round(size_multiplier if can_enter else 0.0, 4),
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=_dedupe(blockers),
        adjustments={
            "quality_multiplier": quality_size_multiplier(entry_grade, config=config),
            "confidence_multiplier": confidence_size_multiplier(score, config=config),
            "regime_multiplier": regime_size_multiplier(regime, config=config),
            "stress_multiplier": stress_size_multiplier(market_stress, config=config),
            "recovery_multiplier": (
                recovery.size_multiplier
                if config.apply_recovery_size_multiplier
                else 1.0
            ),
        },
    )


def build_entry_decision(
    context: EntryContext | dict,
    config: EntryConfig | None = None,
) -> EntryDecision:
    return evaluate_entry(context, config=config)


def calculate_market_stress_score(
    signals: SignalSnapshot | dict,
    regime: MarketRegimeState | dict,
) -> int:
    signal_state = _coerce_signals(signals)
    regime_state = _coerce_regime(regime)
    stress = 0

    if regime_state.regime == "volatile":
        stress += 35
    elif regime_state.regime == "bearish":
        stress += 20
    elif regime_state.regime == "sideways":
        stress += 10

    if regime_state.volatility_state == "dangerous":
        stress += 45
    elif regime_state.volatility_state == "elevated":
        stress += 25

    if not signal_state.volatility_safe:
        stress += 20

    atr_percent = _detail_float(signal_state, regime_state, "atr_percent")
    if atr_percent is not None:
        if atr_percent >= 5.0:
            stress += 25
        elif atr_percent >= 3.0:
            stress += 15
        elif atr_percent >= 2.0:
            stress += 8

    volume_ratio = _detail_float(signal_state, regime_state, "volume_ratio")
    if volume_ratio is not None and volume_ratio >= 2.5:
        stress += 15

    range_percent = _detail_float(signal_state, regime_state, "range_percent")
    if range_percent is not None and range_percent >= 4.0:
        stress += 15

    warning_text = " ".join(signal_state.warnings + regime_state.warnings).lower()
    if "volatility" in warning_text or "atr" in warning_text:
        stress += 10
    if "weakening" in warning_text or "dangerous" in warning_text:
        stress += 10

    return clamp_int(stress, 0, 100)


def classify_entry_type(
    signals: SignalSnapshot,
    score: OpportunityScore,
    regime: MarketRegimeState,
    recovery: RecoveryDecision,
    required_score: int,
) -> EntryType:
    if score.score < required_score or not score.tradeable:
        return "no_entry"

    if recovery.mode in {"defensive", "survival"}:
        if regime.regime in {"bullish", "sideways"} and signals.volatility_safe:
            return "defensive_scalp"
        return "no_entry"

    if (
        signals.dip
        and signals.trend == "bullish"
        and regime.regime == "bullish"
        and signals.momentum in {"bullish", "improving"}
    ):
        return "pullback_continuation"

    if (
        signals.momentum == "bullish"
        and signals.volume_confirmed
        and score.score >= 85
        and regime.regime == "bullish"
    ):
        return "breakout"

    if (
        signals.trend == "bullish"
        and signals.momentum in {"bullish", "improving"}
        and regime.regime == "bullish"
    ):
        return "trend_continuation"

    if (
        regime.regime == "sideways"
        and signals.volatility_safe
        and score.score >= required_score + 5
    ):
        return "defensive_scalp"

    return "no_entry"


def build_hard_blockers(
    signals: SignalSnapshot,
    score: OpportunityScore,
    regime: MarketRegimeState,
    cooldown: CooldownDecision,
    sizing: PositionSizeRecommendation,
    exposure: ExposureDecision,
    recovery: RecoveryDecision,
    entry_type: EntryType,
    required_score: int,
    market_stress_score: int,
    config: EntryConfig,
) -> list[str]:
    blockers = []

    if recovery.mode in config.blocked_recovery_modes or not recovery.can_trade:
        blockers.append("Recovery system blocks new entries")
        blockers.extend(recovery.blockers)

    if regime.regime not in config.allowed_regimes:
        blockers.append(f"Regime {regime.regime} is not allowed for new entries")

    if regime.volatility_state in config.blocked_volatility_states:
        blockers.append(f"Volatility state is {regime.volatility_state}")

    if market_stress_score >= config.market_stress_block_threshold:
        blockers.append(f"Market stress score is too high at {market_stress_score}")

    if not cooldown.can_trade:
        blockers.append("Cooldown system blocks new entries")

    if score.score < required_score:
        blockers.append(
            f"Score {score.score} is below required threshold {required_score}"
        )

    if not score.tradeable:
        blockers.append("Opportunity score is not tradeable")

    if config.block_low_confidence and score.confidence == "low":
        blockers.append("Signal confidence is low")

    if sizing.status == "blocked" or sizing.recommended_usdt <= 0:
        blockers.append("Position sizing blocks new entries")

    if not exposure.can_trade or exposure.status == "blocked":
        blockers.append("Exposure system blocks new entries")
        blockers.extend(exposure.blockers)

    if config.require_valid_entry_type and entry_type == "no_entry":
        blockers.append("No high-quality entry pattern detected")

    if not config.allow_c_grade_entries and score.grade in {"C", "Avoid"}:
        blockers.append(f"Score grade {score.grade} is not acceptable")

    return _dedupe(blockers)


def build_entry_warnings(
    signals: SignalSnapshot,
    score: OpportunityScore,
    regime: MarketRegimeState,
    cooldown: CooldownDecision,
    sizing: PositionSizeRecommendation,
    exposure: ExposureDecision,
    recovery: RecoveryDecision,
    market_stress_score: int,
    config: EntryConfig,
) -> list[str]:
    warnings = []

    if score.confidence == "medium":
        warnings.append("Soft reduction: confidence is medium")

    if not signals.volume_confirmed:
        warnings.append("Volume confirmation is weak")

    if regime.regime == "sideways":
        warnings.append("Soft reduction: sideways regime")

    if regime.volatility_state == "elevated":
        warnings.append("Soft reduction: volatility is elevated")

    if market_stress_score >= config.market_stress_reduce_threshold:
        warnings.append(f"Soft reduction: market stress is {market_stress_score}")

    if sizing.status == "reduced":
        warnings.append("Position sizing already reduced trade size")

    if exposure.status == "reduced":
        warnings.append("Exposure system reduced trade size")

    if recovery.mode != "normal":
        warnings.append(f"Recovery mode is {recovery.mode}; entry is conservative")

    warnings.extend(score.warnings)
    warnings.extend(regime.warnings)
    warnings.extend(cooldown.warnings)
    warnings.extend(sizing.warnings)
    warnings.extend(exposure.warnings)
    warnings.extend(recovery.warnings)
    return _dedupe(warnings)


def build_entry_reasons(
    symbol: str,
    signals: SignalSnapshot,
    score: OpportunityScore,
    regime: MarketRegimeState,
    cooldown: CooldownDecision,
    sizing: PositionSizeRecommendation,
    exposure: ExposureDecision,
    recovery: RecoveryDecision,
    entry_type: EntryType,
    required_score: int,
    market_stress_score: int,
) -> list[str]:
    reasons = [
        f"Evaluating {symbol}",
        f"Entry type is {entry_type}",
        f"Score is {score.score} and required score is {required_score}",
        f"Regime is {regime.regime}",
        f"Recovery mode is {recovery.mode}",
        f"Market stress score is {market_stress_score}",
        f"Position sizing recommended {sizing.recommended_usdt:.2f} USDT",
        f"Exposure approved {exposure.approved_usdt:.2f} USDT",
    ]

    if cooldown.can_trade:
        reasons.append("Cooldown allows new entries")

    if signals.dip:
        reasons.append("Signal is a valid dip setup")

    reasons.extend(score.reasons)
    reasons.extend(regime.reasons)
    reasons.extend(sizing.reasons)
    reasons.extend(exposure.reasons)
    reasons.extend(recovery.reasons)
    return _dedupe(reasons)


def determine_entry_grade(
    score: OpportunityScore,
    regime: MarketRegimeState,
    recovery: RecoveryDecision,
    market_stress_score: int,
    blockers: list[str],
    config: EntryConfig,
) -> EntryGrade:
    if blockers:
        return "Avoid"

    grade: EntryGrade = score.grade

    if market_stress_score >= config.market_stress_reduce_threshold:
        grade = downgrade_grade(grade)

    if regime.regime == "sideways" or regime.volatility_state == "elevated":
        grade = downgrade_grade(grade)

    if recovery.mode in {"reduced", "defensive", "survival"}:
        grade = downgrade_grade(grade)

    if score.confidence == "medium":
        grade = downgrade_grade(grade)

    if score.confidence == "low":
        return "Avoid"

    return grade


def final_size_multiplier(
    entry_grade: EntryGrade,
    score: OpportunityScore,
    regime: MarketRegimeState,
    recovery: RecoveryDecision,
    market_stress_score: int,
    config: EntryConfig,
) -> float:
    multiplier = 1.0
    multiplier *= quality_size_multiplier(entry_grade, config=config)
    multiplier *= confidence_size_multiplier(score, config=config)
    multiplier *= regime_size_multiplier(regime, config=config)
    multiplier *= stress_size_multiplier(market_stress_score, config=config)

    if config.apply_recovery_size_multiplier:
        multiplier *= max(0.0, recovery.size_multiplier)

    return round(max(0.0, min(1.0, multiplier)), 4)


def quality_size_multiplier(
    entry_grade: EntryGrade,
    config: EntryConfig | None = None,
) -> float:
    config = config or EntryConfig()
    if entry_grade in {"A+", "A"}:
        return 1.0
    if entry_grade == "B":
        return config.b_grade_size_multiplier
    return 0.0


def confidence_size_multiplier(
    score: OpportunityScore,
    config: EntryConfig | None = None,
) -> float:
    config = config or EntryConfig()
    if score.confidence == "high":
        return 1.0
    if score.confidence == "medium":
        return config.medium_confidence_size_multiplier
    return 0.0


def regime_size_multiplier(
    regime: MarketRegimeState,
    config: EntryConfig | None = None,
) -> float:
    config = config or EntryConfig()
    if regime.regime == "sideways":
        return config.sideways_size_multiplier
    return 1.0


def stress_size_multiplier(
    market_stress_score: int,
    config: EntryConfig | None = None,
) -> float:
    config = config or EntryConfig()
    if market_stress_score >= config.market_stress_block_threshold:
        return 0.0
    if market_stress_score >= config.market_stress_reduce_threshold:
        return config.high_stress_size_multiplier
    if market_stress_score >= 40:
        return config.elevated_stress_size_multiplier
    return 1.0


def stress_threshold_adjustment(
    market_stress_score: int,
    config: EntryConfig | None = None,
) -> int:
    config = config or EntryConfig()
    if market_stress_score >= config.market_stress_block_threshold:
        return 100
    if market_stress_score >= config.market_stress_reduce_threshold:
        return config.high_stress_score_adjustment
    if market_stress_score >= 40:
        return config.elevated_stress_score_adjustment
    return 0


def downgrade_grade(grade: EntryGrade) -> EntryGrade:
    order: list[EntryGrade] = ["A+", "A", "B", "C", "Avoid"]
    try:
        index = order.index(grade)
    except ValueError:
        return "Avoid"
    return order[min(index + 1, len(order) - 1)]


def _coerce_context(context: EntryContext | dict) -> EntryContext:
    if isinstance(context, EntryContext):
        return context

    return EntryContext(
        symbol=str(context.get("symbol", "")),
        signals=context.get("signals", {}),
        score=context.get("score", {}),
        regime=context.get("regime", {}),
        cooldown=context.get("cooldown", {}),
        position_size=context.get("position_size", context.get("sizing", {})),
        exposure=context.get("exposure", {}),
        recovery=context.get("recovery", {}),
        wallet=context.get("wallet"),
        positions=context.get("positions"),
        market_stress_score=context.get("market_stress_score"),
    )


def _coerce_signals(signals: SignalSnapshot | dict) -> SignalSnapshot:
    if isinstance(signals, SignalSnapshot):
        return signals

    return SignalSnapshot(
        trend=signals.get("trend", "unknown"),
        momentum=signals.get("momentum", "unknown"),
        dip=bool(signals.get("dip", False)),
        volatility_safe=bool(signals.get("volatility_safe", False)),
        volume_confirmed=bool(signals.get("volume_confirmed", False)),
        strength=int(signals.get("strength", 0)),
        reasons=list(signals.get("reasons", [])),
        warnings=list(signals.get("warnings", [])),
        details=dict(signals.get("details", {})),
    )


def _coerce_score(score: OpportunityScore | dict) -> OpportunityScore:
    if isinstance(score, OpportunityScore):
        return score

    return OpportunityScore(
        score=int(score.get("score", 0)),
        grade=score.get("grade", "Avoid"),
        tradeable=bool(score.get("tradeable", False)),
        action=score.get("action", "no_trade"),
        confidence=score.get("confidence", "low"),
        reasons=list(score.get("reasons", [])),
        penalties=list(score.get("penalties", [])),
        warnings=list(score.get("warnings", [])),
        contributions=list(score.get("contributions", [])),
        raw_signal_strength=int(score.get("raw_signal_strength", 0)),
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


def _coerce_cooldown(cooldown: CooldownDecision | dict) -> CooldownDecision:
    if isinstance(cooldown, CooldownDecision):
        return cooldown

    next_trade_at = cooldown.get("next_trade_at")
    if isinstance(next_trade_at, str):
        try:
            next_trade_at = datetime.fromisoformat(next_trade_at)
        except ValueError:
            next_trade_at = None

    return CooldownDecision(
        can_trade=bool(cooldown.get("can_trade", False)),
        activity_mode=cooldown.get("activity_mode", "paused"),
        required_score=int(cooldown.get("required_score", 100)),
        cooldown_minutes=int(cooldown.get("cooldown_minutes", 0)),
        remaining_seconds=int(cooldown.get("remaining_seconds", 0)),
        next_trade_at=next_trade_at,
        reasons=list(cooldown.get("reasons", [])),
        warnings=list(cooldown.get("warnings", [])),
    )


def _coerce_sizing(
    sizing: PositionSizeRecommendation | dict,
) -> PositionSizeRecommendation:
    if isinstance(sizing, PositionSizeRecommendation):
        return sizing

    return PositionSizeRecommendation(
        status=sizing.get("status", "blocked"),
        recommended_usdt=_safe_float(sizing.get("recommended_usdt", 0.0)),
        risk_percent=_safe_float(sizing.get("risk_percent", 0.0)),
        wallet_exposure_after_trade_percent=_safe_float(
            sizing.get("wallet_exposure_after_trade_percent", 0.0)
        ),
        regime_adjustment=_safe_float(sizing.get("regime_adjustment", 0.0)),
        volatility_adjustment=_safe_float(sizing.get("volatility_adjustment", 0.0)),
        confidence_adjustment=_safe_float(sizing.get("confidence_adjustment", 0.0)),
        defensive_adjustment=_safe_float(sizing.get("defensive_adjustment", 0.0)),
        loss_adjustment=_safe_float(sizing.get("loss_adjustment", 0.0)),
        cash_reserve_remaining_usdt=_safe_float(
            sizing.get("cash_reserve_remaining_usdt", 0.0)
        ),
        daily_risk_remaining_percent=_safe_float(
            sizing.get("daily_risk_remaining_percent", 0.0)
        ),
        reasons=list(sizing.get("reasons", [])),
        warnings=list(sizing.get("warnings", [])),
    )


def _coerce_exposure(exposure: ExposureDecision | dict) -> ExposureDecision:
    if isinstance(exposure, ExposureDecision):
        return exposure

    return ExposureDecision(
        status=exposure.get("status", "blocked"),
        approved_usdt=_safe_float(exposure.get("approved_usdt", 0.0)),
        portfolio_exposure_after_percent=_safe_float(
            exposure.get("portfolio_exposure_after_percent", 0.0)
        ),
        symbol_exposure_after_percent=_safe_float(
            exposure.get("symbol_exposure_after_percent", 0.0)
        ),
        correlated_exposure_after_percent=_safe_float(
            exposure.get("correlated_exposure_after_percent", 0.0)
        ),
        open_positions_after=int(exposure.get("open_positions_after", 0)),
        reasons=list(exposure.get("reasons", [])),
        warnings=list(exposure.get("warnings", [])),
        blockers=list(exposure.get("blockers", [])),
    )


def _coerce_recovery(recovery: RecoveryDecision | dict) -> RecoveryDecision:
    if isinstance(recovery, RecoveryDecision):
        return recovery

    return RecoveryDecision(
        mode=recovery.get("mode", "paused"),
        can_trade=bool(recovery.get("can_trade", False)),
        risk_state=recovery.get("risk_state", "red"),
        drawdown_percent=_safe_float(recovery.get("drawdown_percent", 0.0)),
        size_multiplier=_safe_float(recovery.get("size_multiplier", 0.0)),
        score_threshold_adjustment=int(
            recovery.get("score_threshold_adjustment", 0)
        ),
        cooldown_multiplier=_safe_float(recovery.get("cooldown_multiplier", 1.0)),
        extra_cooldown_minutes=int(recovery.get("extra_cooldown_minutes", 0)),
        recent_loss_count=int(recovery.get("recent_loss_count", 0)),
        recent_win_count=int(recovery.get("recent_win_count", 0)),
        consecutive_losses=int(recovery.get("consecutive_losses", 0)),
        restore_ready=bool(recovery.get("restore_ready", False)),
        wins_needed_to_restore=int(recovery.get("wins_needed_to_restore", 0)),
        reasons=list(recovery.get("reasons", [])),
        warnings=list(recovery.get("warnings", [])),
        blockers=list(recovery.get("blockers", [])),
    )


def _detail_float(
    signals: SignalSnapshot,
    regime: MarketRegimeState,
    key: str,
) -> float | None:
    value = signals.details.get(key)
    if value is None:
        value = regime.details.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_int(value: object, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


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
