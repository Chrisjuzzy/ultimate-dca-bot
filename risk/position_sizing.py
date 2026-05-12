from dataclasses import asdict, dataclass
from typing import Literal

from config import BASE_TRADE_USDT, MAX_PORTFOLIO_EXPOSURE, MIN_USDT_RESERVE
from strategy.market_regime import MarketRegimeState
from strategy.scoring import OpportunityScore


SizeStatus = Literal["approved", "reduced", "blocked"]


@dataclass(frozen=True)
class PositionSizingConfig:
    base_risk_percent: float = 1.0
    max_risk_percent: float = 2.0
    min_trade_usdt: float = BASE_TRADE_USDT
    max_trade_usdt: float = 25.0
    max_portfolio_exposure_percent: float = MAX_PORTFOLIO_EXPOSURE * 100
    min_cash_reserve_percent: float = MIN_USDT_RESERVE * 100
    daily_risk_budget_percent: float = 5.0
    compound_profit_fraction: float = 0.25
    bullish_adjustment: float = 1.0
    sideways_adjustment: float = 0.55
    bearish_adjustment: float = 0.10
    volatile_adjustment: float = 0.15
    unknown_adjustment: float = 0.35
    safe_volatility_adjustment: float = 1.0
    elevated_volatility_adjustment: float = 0.60
    dangerous_volatility_adjustment: float = 0.20
    high_confidence_adjustment: float = 1.0
    medium_confidence_adjustment: float = 0.70
    low_confidence_adjustment: float = 0.0
    defensive_adjustment: float = 0.50
    loss_adjustment_step: float = 0.15
    max_loss_adjustment: float = 0.60


@dataclass(frozen=True)
class WalletState:
    total_usdt: float
    free_usdt: float
    current_exposure_usdt: float = 0.0
    daily_risk_used_percent: float = 0.0
    realized_profit_usdt: float = 0.0


@dataclass(frozen=True)
class SizingContext:
    defensive_mode: bool = False
    recent_loss_count: int = 0
    symbol_current_exposure_usdt: float = 0.0


@dataclass(frozen=True)
class PositionSizeRecommendation:
    status: SizeStatus
    recommended_usdt: float
    risk_percent: float
    wallet_exposure_after_trade_percent: float
    regime_adjustment: float
    volatility_adjustment: float
    confidence_adjustment: float
    defensive_adjustment: float
    loss_adjustment: float
    cash_reserve_remaining_usdt: float
    daily_risk_remaining_percent: float
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_position_size(
    wallet: WalletState | dict,
    score: OpportunityScore | dict,
    regime: MarketRegimeState | dict,
    context: SizingContext | None = None,
    config: PositionSizingConfig | None = None,
) -> PositionSizeRecommendation:
    config = config or PositionSizingConfig()
    context = context or SizingContext()
    wallet_state = _coerce_wallet(wallet)
    score_state = _coerce_score(score)
    regime_state = _coerce_regime(regime)

    reasons: list[str] = []
    warnings: list[str] = []

    if wallet_state.total_usdt <= 0 or wallet_state.free_usdt <= 0:
        return _blocked("Wallet balance is unavailable", wallet_state, config)

    if not score_state.tradeable or score_state.confidence == "low":
        return _blocked("Opportunity score is not tradeable", wallet_state, config)

    reserve_usdt = wallet_state.total_usdt * config.min_cash_reserve_percent / 100
    spendable_usdt = max(0.0, wallet_state.free_usdt - reserve_usdt)
    if spendable_usdt <= 0:
        return _blocked("Minimum cash reserve would be violated", wallet_state, config)

    daily_risk_remaining = max(
        0.0,
        config.daily_risk_budget_percent - wallet_state.daily_risk_used_percent,
    )
    if daily_risk_remaining <= 0:
        return _blocked("Daily risk budget is exhausted", wallet_state, config)

    regime_adjustment = regime_risk_adjustment(regime_state, config=config)
    volatility_adjustment = volatility_risk_adjustment(regime_state, config=config)
    confidence_adjustment = confidence_risk_adjustment(score_state, config=config)
    defensive_adjustment = config.defensive_adjustment if context.defensive_mode else 1.0
    loss_adjustment = recent_loss_adjustment(context, config=config)
    score_adjustment = score_quality_adjustment(score_state)
    compound_usdt = max(0.0, wallet_state.realized_profit_usdt) * config.compound_profit_fraction

    adjusted_risk_percent = config.base_risk_percent
    adjusted_risk_percent *= regime_adjustment
    adjusted_risk_percent *= volatility_adjustment
    adjusted_risk_percent *= confidence_adjustment
    adjusted_risk_percent *= defensive_adjustment
    adjusted_risk_percent *= loss_adjustment
    adjusted_risk_percent *= score_adjustment
    adjusted_risk_percent = min(
        adjusted_risk_percent,
        config.max_risk_percent,
        daily_risk_remaining,
    )

    raw_size = wallet_state.total_usdt * adjusted_risk_percent / 100
    raw_size += compound_usdt
    capped_size = min(raw_size, config.max_trade_usdt, spendable_usdt)

    max_exposure_usdt = wallet_state.total_usdt * config.max_portfolio_exposure_percent / 100
    exposure_room = max(0.0, max_exposure_usdt - wallet_state.current_exposure_usdt)
    recommended_usdt = min(capped_size, exposure_room)

    exposure_after = wallet_state.current_exposure_usdt + recommended_usdt
    exposure_after_percent = _percent(exposure_after, wallet_state.total_usdt)
    cash_reserve_remaining = wallet_state.free_usdt - recommended_usdt

    reasons.extend(
        [
            f"Regime adjustment {regime_adjustment:.2f}",
            f"Volatility adjustment {volatility_adjustment:.2f}",
            f"Confidence adjustment {confidence_adjustment:.2f}",
            f"Score adjustment {score_adjustment:.2f}",
        ]
    )

    if context.defensive_mode:
        reasons.append("Defensive sizing is active")

    if context.recent_loss_count:
        reasons.append(f"Recent losses reduced size by {1 - loss_adjustment:.0%}")

    if compound_usdt > 0:
        reasons.append(f"Slow compounding added {compound_usdt:.2f} USDT")

    if recommended_usdt < raw_size:
        warnings.append("Size was capped by reserve, exposure, or max trade limits")

    if regime_state.regime in {"bearish", "volatile"}:
        warnings.append(f"Regime is {regime_state.regime}; sizing is heavily reduced")

    if recommended_usdt < config.min_trade_usdt:
        return PositionSizeRecommendation(
            status="blocked",
            recommended_usdt=0.0,
            risk_percent=round(adjusted_risk_percent, 4),
            wallet_exposure_after_trade_percent=round(exposure_after_percent, 4),
            regime_adjustment=regime_adjustment,
            volatility_adjustment=volatility_adjustment,
            confidence_adjustment=confidence_adjustment,
            defensive_adjustment=defensive_adjustment,
            loss_adjustment=loss_adjustment,
            cash_reserve_remaining_usdt=round(wallet_state.free_usdt, 4),
            daily_risk_remaining_percent=round(daily_risk_remaining, 4),
            reasons=reasons,
            warnings=_dedupe(warnings + ["Recommended size is below minimum trade size"]),
        )

    status: SizeStatus = "approved"
    if recommended_usdt < raw_size or adjusted_risk_percent < config.base_risk_percent:
        status = "reduced"

    return PositionSizeRecommendation(
        status=status,
        recommended_usdt=round(recommended_usdt, 4),
        risk_percent=round(adjusted_risk_percent, 4),
        wallet_exposure_after_trade_percent=round(exposure_after_percent, 4),
        regime_adjustment=regime_adjustment,
        volatility_adjustment=volatility_adjustment,
        confidence_adjustment=confidence_adjustment,
        defensive_adjustment=defensive_adjustment,
        loss_adjustment=loss_adjustment,
        cash_reserve_remaining_usdt=round(cash_reserve_remaining, 4),
        daily_risk_remaining_percent=round(daily_risk_remaining, 4),
        reasons=reasons,
        warnings=_dedupe(warnings),
    )


def regime_risk_adjustment(
    regime: MarketRegimeState | dict,
    config: PositionSizingConfig | None = None,
) -> float:
    config = config or PositionSizingConfig()
    regime_state = _coerce_regime(regime)

    if regime_state.regime == "bullish":
        return config.bullish_adjustment
    if regime_state.regime == "sideways":
        return config.sideways_adjustment
    if regime_state.regime == "bearish":
        return config.bearish_adjustment
    if regime_state.regime == "volatile":
        return config.volatile_adjustment
    return config.unknown_adjustment


def volatility_risk_adjustment(
    regime: MarketRegimeState | dict,
    config: PositionSizingConfig | None = None,
) -> float:
    config = config or PositionSizingConfig()
    regime_state = _coerce_regime(regime)

    if regime_state.volatility_state == "safe":
        return config.safe_volatility_adjustment
    if regime_state.volatility_state == "elevated":
        return config.elevated_volatility_adjustment
    if regime_state.volatility_state == "dangerous":
        return config.dangerous_volatility_adjustment
    return config.elevated_volatility_adjustment


def confidence_risk_adjustment(
    score: OpportunityScore | dict,
    config: PositionSizingConfig | None = None,
) -> float:
    config = config or PositionSizingConfig()
    score_state = _coerce_score(score)

    if score_state.confidence == "high":
        return config.high_confidence_adjustment
    if score_state.confidence == "medium":
        return config.medium_confidence_adjustment
    return config.low_confidence_adjustment


def recent_loss_adjustment(
    context: SizingContext,
    config: PositionSizingConfig | None = None,
) -> float:
    config = config or PositionSizingConfig()
    reduction = min(
        config.max_loss_adjustment,
        context.recent_loss_count * config.loss_adjustment_step,
    )
    return max(0.0, 1.0 - reduction)


def score_quality_adjustment(score: OpportunityScore | dict) -> float:
    score_state = _coerce_score(score)

    if score_state.score >= 90:
        return 1.15
    if score_state.score >= 80:
        return 1.0
    if score_state.score >= 70:
        return 0.75
    return 0.0


def _blocked(
    reason: str,
    wallet_state: WalletState,
    config: PositionSizingConfig,
) -> PositionSizeRecommendation:
    daily_risk_remaining = max(
        0.0,
        config.daily_risk_budget_percent - wallet_state.daily_risk_used_percent,
    )
    return PositionSizeRecommendation(
        status="blocked",
        recommended_usdt=0.0,
        risk_percent=0.0,
        wallet_exposure_after_trade_percent=_percent(
            wallet_state.current_exposure_usdt,
            wallet_state.total_usdt,
        ),
        regime_adjustment=0.0,
        volatility_adjustment=0.0,
        confidence_adjustment=0.0,
        defensive_adjustment=0.0,
        loss_adjustment=0.0,
        cash_reserve_remaining_usdt=max(0.0, wallet_state.free_usdt),
        daily_risk_remaining_percent=round(daily_risk_remaining, 4),
        reasons=[],
        warnings=[reason],
    )


def _coerce_wallet(wallet: WalletState | dict) -> WalletState:
    if isinstance(wallet, WalletState):
        return wallet

    return WalletState(
        total_usdt=float(wallet.get("total_usdt", 0.0)),
        free_usdt=float(wallet.get("free_usdt", 0.0)),
        current_exposure_usdt=float(wallet.get("current_exposure_usdt", 0.0)),
        daily_risk_used_percent=float(wallet.get("daily_risk_used_percent", 0.0)),
        realized_profit_usdt=float(wallet.get("realized_profit_usdt", 0.0)),
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


def _percent(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(value / total * 100, 4)


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
