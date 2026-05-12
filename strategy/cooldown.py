from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from strategy.market_regime import MarketRegimeState
from strategy.scoring import OpportunityScore


ActivityMode = Literal["active", "normal", "cautious", "defensive", "paused"]


@dataclass(frozen=True)
class CooldownConfig:
    bullish_base_minutes: int = 60
    sideways_base_minutes: int = 180
    bearish_base_minutes: int = 360
    volatile_base_minutes: int = 720
    unknown_base_minutes: int = 240
    strong_trade_discount_percent: int = 35
    cautious_trade_extra_percent: int = 35
    low_confidence_extra_percent: int = 50
    recent_loss_extra_minutes: int = 120
    defensive_extra_minutes: int = 240
    max_cooldown_minutes: int = 1440
    bullish_score_threshold: int = 70
    sideways_score_threshold: int = 80
    bearish_score_threshold: int = 95
    volatile_score_threshold: int = 95
    unknown_score_threshold: int = 85


@dataclass(frozen=True)
class CooldownContext:
    now: datetime | None = None
    last_trade_at: datetime | None = None
    recent_trade_count: int = 0
    recent_loss_count: int = 0
    defensive_mode: bool = False
    manual_pause: bool = False


@dataclass(frozen=True)
class CooldownDecision:
    can_trade: bool
    activity_mode: ActivityMode
    required_score: int
    cooldown_minutes: int
    remaining_seconds: int
    next_trade_at: datetime | None
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["next_trade_at"] = (
            self.next_trade_at.isoformat() if self.next_trade_at else None
        )
        return payload


def evaluate_cooldown(
    score: OpportunityScore | dict,
    regime: MarketRegimeState | dict,
    context: CooldownContext | None = None,
    config: CooldownConfig | None = None,
) -> CooldownDecision:
    config = config or CooldownConfig()
    context = context or CooldownContext()
    now = _normalize_datetime(context.now) or datetime.now(UTC)
    score_state = _coerce_score(score)
    regime_state = _coerce_regime(regime)

    activity_mode = determine_activity_mode(
        score=score_state,
        regime=regime_state,
        context=context,
    )
    required_score = required_score_for_regime(regime_state, config=config)
    cooldown_minutes = adaptive_cooldown_minutes(
        score=score_state,
        regime=regime_state,
        context=context,
        config=config,
    )

    if context.manual_pause:
        return CooldownDecision(
            can_trade=False,
            activity_mode="paused",
            required_score=required_score,
            cooldown_minutes=cooldown_minutes,
            remaining_seconds=cooldown_minutes * 60,
            next_trade_at=None,
            reasons=[],
            warnings=["Manual pause is active"],
        )

    next_trade_at = None
    remaining_seconds = 0
    last_trade_at = _normalize_datetime(context.last_trade_at)

    if last_trade_at is not None:
        next_trade_at = last_trade_at + timedelta(minutes=cooldown_minutes)
        remaining_seconds = max(0, int((next_trade_at - now).total_seconds()))

    reasons = build_cooldown_reasons(
        score=score_state,
        regime=regime_state,
        context=context,
        activity_mode=activity_mode,
        required_score=required_score,
        cooldown_minutes=cooldown_minutes,
    )
    warnings = build_cooldown_warnings(
        score=score_state,
        regime=regime_state,
        context=context,
        remaining_seconds=remaining_seconds,
        required_score=required_score,
    )

    can_trade = (
        score_state.score >= required_score
        and score_state.tradeable
        and activity_mode not in {"defensive", "paused"}
        and remaining_seconds == 0
    )

    return CooldownDecision(
        can_trade=can_trade,
        activity_mode=activity_mode,
        required_score=required_score,
        cooldown_minutes=cooldown_minutes,
        remaining_seconds=remaining_seconds,
        next_trade_at=next_trade_at,
        reasons=reasons,
        warnings=warnings,
    )


def required_score_for_regime(
    regime: MarketRegimeState | dict,
    config: CooldownConfig | None = None,
) -> int:
    config = config or CooldownConfig()
    regime_state = _coerce_regime(regime)

    if regime_state.regime == "bullish":
        return config.bullish_score_threshold
    if regime_state.regime == "sideways":
        return config.sideways_score_threshold
    if regime_state.regime == "bearish":
        return config.bearish_score_threshold
    if regime_state.regime == "volatile":
        return config.volatile_score_threshold
    return config.unknown_score_threshold


def adaptive_cooldown_minutes(
    score: OpportunityScore | dict,
    regime: MarketRegimeState | dict,
    context: CooldownContext | None = None,
    config: CooldownConfig | None = None,
) -> int:
    config = config or CooldownConfig()
    context = context or CooldownContext()
    score_state = _coerce_score(score)
    regime_state = _coerce_regime(regime)

    minutes = _base_minutes_for_regime(regime_state, config=config)

    if score_state.action == "strong_trade" and score_state.confidence == "high":
        minutes = int(minutes * (100 - config.strong_trade_discount_percent) / 100)
    elif score_state.action == "cautious_trade":
        minutes = int(minutes * (100 + config.cautious_trade_extra_percent) / 100)

    if score_state.confidence == "low":
        minutes = int(minutes * (100 + config.low_confidence_extra_percent) / 100)

    if context.recent_loss_count > 0:
        minutes += config.recent_loss_extra_minutes * context.recent_loss_count

    if context.defensive_mode:
        minutes += config.defensive_extra_minutes

    if regime_state.volatility_state == "dangerous":
        minutes = max(minutes, config.volatile_base_minutes)

    return max(0, min(config.max_cooldown_minutes, minutes))


def determine_activity_mode(
    score: OpportunityScore | dict,
    regime: MarketRegimeState | dict,
    context: CooldownContext | None = None,
) -> ActivityMode:
    context = context or CooldownContext()
    score_state = _coerce_score(score)
    regime_state = _coerce_regime(regime)

    if context.manual_pause:
        return "paused"

    if (
        context.defensive_mode
        or context.recent_loss_count >= 2
        or regime_state.regime == "volatile"
        or regime_state.volatility_state == "dangerous"
    ):
        return "defensive"

    if regime_state.regime == "bearish":
        return "defensive"

    if regime_state.regime == "sideways" or score_state.confidence == "low":
        return "cautious"

    if (
        regime_state.regime == "bullish"
        and regime_state.confidence == "high"
        and score_state.action == "strong_trade"
    ):
        return "active"

    return "normal"


def build_cooldown_reasons(
    score: OpportunityScore,
    regime: MarketRegimeState,
    context: CooldownContext,
    activity_mode: ActivityMode,
    required_score: int,
    cooldown_minutes: int,
) -> list[str]:
    reasons = [
        f"Regime is {regime.regime}",
        f"Activity mode is {activity_mode}",
        f"Required score is {required_score}",
        f"Cooldown is {cooldown_minutes} minutes",
    ]

    if score.score >= required_score:
        reasons.append("Opportunity score meets regime threshold")

    if context.recent_trade_count:
        reasons.append(f"Recent trade count is {context.recent_trade_count}")

    return reasons


def build_cooldown_warnings(
    score: OpportunityScore,
    regime: MarketRegimeState,
    context: CooldownContext,
    remaining_seconds: int,
    required_score: int,
) -> list[str]:
    warnings = []

    if score.score < required_score:
        warnings.append(
            f"Score {score.score} is below required threshold {required_score}"
        )

    if remaining_seconds > 0:
        warnings.append(f"Cooldown has {remaining_seconds} seconds remaining")

    if context.recent_loss_count:
        warnings.append(f"Recent loss count is {context.recent_loss_count}")

    if regime.regime in {"bearish", "volatile"}:
        warnings.append(f"Regime is {regime.regime}; trading should be defensive")

    if regime.volatility_state == "dangerous":
        warnings.append("Volatility state is dangerous")

    warnings.extend(score.warnings)
    warnings.extend(regime.warnings)
    return _dedupe(warnings)


def _base_minutes_for_regime(
    regime: MarketRegimeState,
    config: CooldownConfig,
) -> int:
    if regime.regime == "bullish":
        return config.bullish_base_minutes
    if regime.regime == "sideways":
        return config.sideways_base_minutes
    if regime.regime == "bearish":
        return config.bearish_base_minutes
    if regime.regime == "volatile":
        return config.volatile_base_minutes
    return config.unknown_base_minutes


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


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
