from dataclasses import asdict, dataclass, field
from typing import Literal

from strategy.signals import SignalSnapshot


Grade = Literal["A+", "A", "B", "C", "Avoid"]
Confidence = Literal["high", "medium", "low"]
Action = Literal["strong_trade", "cautious_trade", "no_trade"]


@dataclass(frozen=True)
class ScoreConfig:
    bullish_trend_weight: int = 20
    strong_momentum_weight: int = 20
    valid_dip_weight: int = 20
    safe_volatility_weight: int = 15
    volume_confirmation_weight: int = 15
    strong_adx_weight: int = 10
    weak_volume_penalty: int = 10
    high_atr_penalty: int = 15
    sideways_market_penalty: int = 10
    bearish_trend_penalty: int = 25
    weak_momentum_penalty: int = 15
    unknown_data_penalty: int = 20
    strong_trade_threshold: int = 80
    cautious_trade_threshold: int = 70
    high_confidence_threshold: int = 85
    medium_confidence_threshold: int = 70
    strong_adx_threshold: float = 25.0
    high_atr_percent_threshold: float = 5.0


@dataclass(frozen=True)
class ScoreContribution:
    factor: str
    points: int
    reason: str


@dataclass(frozen=True)
class OpportunityScore:
    score: int
    grade: Grade
    tradeable: bool
    action: Action
    confidence: Confidence
    reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    contributions: list[ScoreContribution] = field(default_factory=list)
    raw_signal_strength: int = 0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["contributions"] = [asdict(item) for item in self.contributions]
        return payload


def score_opportunity(
    signals: SignalSnapshot | dict,
    config: ScoreConfig | None = None,
) -> OpportunityScore:
    config = config or ScoreConfig()
    snapshot = _coerce_signal_snapshot(signals)

    contributions: list[ScoreContribution] = []
    penalties: list[str] = []
    warnings = list(snapshot.warnings)

    _score_trend(snapshot, config, contributions, penalties)
    _score_momentum(snapshot, config, contributions, penalties)
    _score_dip(snapshot, config, contributions)
    _score_volatility(snapshot, config, contributions, penalties, warnings)
    _score_volume(snapshot, config, contributions, penalties, warnings)
    _score_adx(snapshot, config, contributions, warnings)
    _score_data_quality(snapshot, config, penalties, warnings)

    score = _clamp_score(
        sum(item.points for item in contributions)
        - _penalty_points(penalties)
    )

    return OpportunityScore(
        score=score,
        grade=grade_score(score),
        tradeable=score >= config.cautious_trade_threshold,
        action=classify_action(score, config=config),
        confidence=confidence_for_score(score, config=config),
        reasons=[item.reason for item in contributions if item.points > 0],
        penalties=penalties,
        warnings=_dedupe(warnings),
        contributions=contributions,
        raw_signal_strength=snapshot.strength,
    )


def grade_score(score: int) -> Grade:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "Avoid"


def confidence_for_score(
    score: int,
    config: ScoreConfig | None = None,
) -> Confidence:
    config = config or ScoreConfig()
    if score >= config.high_confidence_threshold:
        return "high"
    if score >= config.medium_confidence_threshold:
        return "medium"
    return "low"


def classify_action(
    score: int,
    config: ScoreConfig | None = None,
) -> Action:
    config = config or ScoreConfig()
    if score >= config.strong_trade_threshold:
        return "strong_trade"
    if score >= config.cautious_trade_threshold:
        return "cautious_trade"
    return "no_trade"


def _score_trend(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    contributions: list[ScoreContribution],
    penalties: list[str],
) -> None:
    if snapshot.trend == "bullish":
        contributions.append(
            ScoreContribution(
                "bullish_trend",
                config.bullish_trend_weight,
                "Bullish EMA trend structure",
            )
        )
    elif snapshot.trend == "sideways":
        penalties.append(
            _penalty(
                "Sideways market structure",
                config.sideways_market_penalty,
            )
        )
    elif snapshot.trend == "bearish":
        penalties.append(
            _penalty(
                "Bearish trend structure",
                config.bearish_trend_penalty,
            )
        )
    else:
        penalties.append(
            _penalty(
                "Unknown trend structure",
                config.unknown_data_penalty,
            )
        )


def _score_momentum(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    contributions: list[ScoreContribution],
    penalties: list[str],
) -> None:
    if snapshot.momentum == "bullish":
        contributions.append(
            ScoreContribution(
                "strong_momentum",
                config.strong_momentum_weight,
                "Bullish momentum confirmation",
            )
        )
    elif snapshot.momentum == "improving":
        contributions.append(
            ScoreContribution(
                "improving_momentum",
                int(config.strong_momentum_weight * 0.75),
                "Momentum is improving",
            )
        )
    elif snapshot.momentum in {"weak", "bearish"}:
        penalties.append(
            _penalty(
                f"{snapshot.momentum.title()} momentum",
                config.weak_momentum_penalty,
            )
        )
    else:
        penalties.append(
            _penalty(
                "Unknown momentum state",
                config.unknown_data_penalty,
            )
        )


def _score_dip(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    contributions: list[ScoreContribution],
) -> None:
    if snapshot.dip:
        contributions.append(
            ScoreContribution(
                "valid_dip",
                config.valid_dip_weight,
                "Valid bullish pullback for DCA entry",
            )
        )


def _score_volatility(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    contributions: list[ScoreContribution],
    penalties: list[str],
    warnings: list[str],
) -> None:
    atr_percent = _detail_float(snapshot, "atr_percent")

    if snapshot.volatility_safe:
        contributions.append(
            ScoreContribution(
                "safe_volatility",
                config.safe_volatility_weight,
                "ATR is inside safe range",
            )
        )
    else:
        penalties.append(_penalty("Unsafe volatility", config.high_atr_penalty))

    if atr_percent is not None and atr_percent >= config.high_atr_percent_threshold:
        warnings.append(f"ATR percent is elevated at {atr_percent:.2f}%")


def _score_volume(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    contributions: list[ScoreContribution],
    penalties: list[str],
    warnings: list[str],
) -> None:
    volume_ratio = _detail_float(snapshot, "volume_ratio")

    if snapshot.volume_confirmed:
        contributions.append(
            ScoreContribution(
                "volume_confirmation",
                config.volume_confirmation_weight,
                "Volume confirms participation",
            )
        )
    else:
        penalties.append(_penalty("Weak volume confirmation", config.weak_volume_penalty))
        if volume_ratio is not None:
            warnings.append(f"Volume ratio is {volume_ratio:.2f}")


def _score_adx(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    contributions: list[ScoreContribution],
    warnings: list[str],
) -> None:
    adx = _detail_float(snapshot, "adx")

    if adx is None:
        warnings.append("ADX unavailable")
        return

    if adx >= config.strong_adx_threshold:
        contributions.append(
            ScoreContribution(
                "strong_adx",
                config.strong_adx_weight,
                "ADX confirms strong trend",
            )
        )
    else:
        warnings.append(f"ADX is not strong yet at {adx:.2f}")


def _score_data_quality(
    snapshot: SignalSnapshot,
    config: ScoreConfig,
    penalties: list[str],
    warnings: list[str],
) -> None:
    if snapshot.trend == "unknown" or snapshot.momentum == "unknown":
        penalties.append(_penalty("Incomplete signal data", config.unknown_data_penalty))

    for warning in snapshot.warnings:
        if "not enough" in warning.lower():
            penalties.append(_penalty(warning, config.unknown_data_penalty))

    warnings.extend(snapshot.warnings)


def _coerce_signal_snapshot(signals: SignalSnapshot | dict) -> SignalSnapshot:
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


def _detail_float(snapshot: SignalSnapshot, key: str) -> float | None:
    value = snapshot.details.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _penalty(reason: str, points: int) -> str:
    return f"{reason} (-{points})"


def _penalty_points(penalties: list[str]) -> int:
    total = 0
    for penalty in penalties:
        marker = "(-"
        if marker not in penalty or not penalty.endswith(")"):
            continue
        value = penalty.split(marker, 1)[1].rstrip(")")
        if value.isdigit():
            total += int(value)
    return total


def _clamp_score(score: int) -> int:
    return max(0, min(100, int(score)))


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
