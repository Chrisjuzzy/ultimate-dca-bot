from __future__ import annotations

from dataclasses import asdict, dataclass, field

from strategy.market_regime import MarketRegimeState
from strategy.signals import SignalSnapshot


@dataclass(frozen=True)
class MarketStressConfig:
    elevated_threshold: int = 60
    high_threshold: int = 80
    extreme_threshold: int = 90
    btc_weight: float = 1.25


@dataclass(frozen=True)
class MarketStressState:
    score: int
    level: str
    trading_allowed: bool
    size_multiplier: float
    score_adjustment: int
    cooldown_multiplier: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_market_stress(
    signals_by_symbol: dict[str, SignalSnapshot | dict],
    regimes_by_symbol: dict[str, MarketRegimeState | dict],
    config: MarketStressConfig | None = None,
) -> MarketStressState:
    config = config or MarketStressConfig()
    scores = []
    reasons: list[str] = []
    warnings: list[str] = []

    symbols = sorted(set(signals_by_symbol) | set(regimes_by_symbol))
    for symbol in symbols:
        signal = _coerce_signal(signals_by_symbol.get(symbol, {}))
        regime = _coerce_regime(regimes_by_symbol.get(symbol, {}))
        symbol_score = symbol_stress_score(signal, regime)
        weight = config.btc_weight if symbol.upper().startswith("BTC/") else 1.0
        scores.append(symbol_score * weight)

        if symbol_score >= config.high_threshold:
            warnings.append(f"{symbol} stress is high at {symbol_score}")
        if regime.volatility_state == "dangerous":
            reasons.append(f"{symbol} volatility is dangerous")
        if regime.regime in {"bearish", "volatile"}:
            reasons.append(f"{symbol} regime is {regime.regime}")

    if not scores:
        return MarketStressState(
            score=50,
            level="unknown",
            trading_allowed=False,
            size_multiplier=0.5,
            score_adjustment=10,
            cooldown_multiplier=1.5,
            warnings=["No market stress inputs available"],
        )

    score = int(round(min(100, max(scores))))
    level = stress_level(score, config=config)
    trading_allowed = score < config.extreme_threshold
    size_multiplier = stress_size_multiplier(score, config=config)
    score_adjustment = stress_score_adjustment(score, config=config)
    cooldown_multiplier = stress_cooldown_multiplier(score, config=config)

    if not trading_allowed:
        warnings.append(f"Global stress blocks new entries at {score}")

    return MarketStressState(
        score=score,
        level=level,
        trading_allowed=trading_allowed,
        size_multiplier=size_multiplier,
        score_adjustment=score_adjustment,
        cooldown_multiplier=cooldown_multiplier,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
    )


def symbol_stress_score(
    signal: SignalSnapshot | dict,
    regime: MarketRegimeState | dict,
) -> int:
    signal_state = _coerce_signal(signal)
    regime_state = _coerce_regime(regime)
    stress = 0

    if regime_state.regime == "volatile":
        stress += 35
    elif regime_state.regime == "bearish":
        stress += 25
    elif regime_state.regime == "unknown":
        stress += 15

    if regime_state.volatility_state == "dangerous":
        stress += 40
    elif regime_state.volatility_state == "elevated":
        stress += 25

    if signal_state.trend == "bearish":
        stress += 15
    if signal_state.momentum == "bearish":
        stress += 15
    if not signal_state.volatility_safe:
        stress += 15

    atr_percent = _safe_float(signal_state.details.get("atr_percent"))
    volume_ratio = _safe_float(signal_state.details.get("volume_ratio"))
    if atr_percent >= 6:
        stress += 20
    elif atr_percent >= 4:
        stress += 10
    if volume_ratio >= 2.5:
        stress += 10

    return max(0, min(100, stress))


def stress_level(score: int, config: MarketStressConfig | None = None) -> str:
    config = config or MarketStressConfig()
    if score >= config.extreme_threshold:
        return "extreme"
    if score >= config.high_threshold:
        return "high"
    if score >= config.elevated_threshold:
        return "elevated"
    return "normal"


def stress_size_multiplier(score: int, config: MarketStressConfig | None = None) -> float:
    config = config or MarketStressConfig()
    if score >= config.extreme_threshold:
        return 0.0
    if score >= config.high_threshold:
        return 0.35
    if score >= config.elevated_threshold:
        return 0.65
    return 1.0


def stress_score_adjustment(score: int, config: MarketStressConfig | None = None) -> int:
    config = config or MarketStressConfig()
    if score >= config.extreme_threshold:
        return 25
    if score >= config.high_threshold:
        return 15
    if score >= config.elevated_threshold:
        return 5
    return 0


def stress_cooldown_multiplier(score: int, config: MarketStressConfig | None = None) -> float:
    config = config or MarketStressConfig()
    if score >= config.extreme_threshold:
        return 3.0
    if score >= config.high_threshold:
        return 2.0
    if score >= config.elevated_threshold:
        return 1.4
    return 1.0


def _coerce_signal(signal: SignalSnapshot | dict) -> SignalSnapshot:
    if isinstance(signal, SignalSnapshot):
        return signal
    return SignalSnapshot(
        trend=signal.get("trend", "unknown"),
        momentum=signal.get("momentum", "unknown"),
        dip=bool(signal.get("dip", False)),
        volatility_safe=bool(signal.get("volatility_safe", False)),
        volume_confirmed=bool(signal.get("volume_confirmed", False)),
        strength=int(signal.get("strength", 0)),
        reasons=list(signal.get("reasons", [])),
        warnings=list(signal.get("warnings", [])),
        details=dict(signal.get("details", {})),
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


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    output = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output
