from dataclasses import asdict, dataclass
from typing import Iterable, Literal

import numpy as np
import pandas as pd

from strategy.indicators import add_indicators, indicators_ready


Regime = Literal["bullish", "bearish", "sideways", "volatile", "unknown"]
Confidence = Literal["high", "medium", "low"]
VolatilityState = Literal["safe", "elevated", "dangerous", "unknown"]
TrendStrength = Literal["strong", "moderate", "weak", "unknown"]


@dataclass(frozen=True)
class MarketRegimeConfig:
    adx_strong_threshold: float = 25.0
    adx_trend_threshold: float = 20.0
    adx_weak_threshold: float = 16.0
    ema_spread_sideways_percent: float = 0.75
    atr_elevated_percent: float = 4.0
    atr_dangerous_percent: float = 6.0
    candle_expansion_percent: float = 5.0
    price_above_ema50_buffer_percent: float = 0.0
    price_below_ema50_buffer_percent: float = 0.0


@dataclass(frozen=True)
class MarketRegimeState:
    regime: Regime
    strength: int
    confidence: Confidence
    volatility_state: VolatilityState
    trend_strength: TrendStrength
    warnings: list[str]
    reasons: list[str]
    details: dict

    def to_dict(self) -> dict:
        return asdict(self)


def detect_market_regime(
    candles: pd.DataFrame | Iterable[Iterable[float]],
    config: MarketRegimeConfig | None = None,
) -> MarketRegimeState:
    config = config or MarketRegimeConfig()
    df = add_indicators(candles)

    if len(df) < 2 or not indicators_ready(df.iloc[-1]):
        return MarketRegimeState(
            regime="unknown",
            strength=0,
            confidence="low",
            volatility_state="unknown",
            trend_strength="unknown",
            warnings=["Not enough indicator history"],
            reasons=[],
            details={},
        )

    current = df.iloc[-1]
    previous = df.iloc[-2]

    volatility_state = classify_volatility(current, config=config)
    trend_strength = classify_trend_strength(current, config=config)
    regime = classify_regime(
        current=current,
        previous=previous,
        volatility_state=volatility_state,
        config=config,
    )
    strength = estimate_regime_strength(
        current=current,
        regime=regime,
        volatility_state=volatility_state,
        trend_strength=trend_strength,
        config=config,
    )

    return MarketRegimeState(
        regime=regime,
        strength=strength,
        confidence=confidence_for_regime(
            regime=regime,
            strength=strength,
            volatility_state=volatility_state,
            trend_strength=trend_strength,
        ),
        volatility_state=volatility_state,
        trend_strength=trend_strength,
        warnings=build_regime_warnings(
            current=current,
            previous=previous,
            regime=regime,
            volatility_state=volatility_state,
            trend_strength=trend_strength,
            config=config,
        ),
        reasons=build_regime_reasons(
            current=current,
            regime=regime,
            volatility_state=volatility_state,
            trend_strength=trend_strength,
            config=config,
        ),
        details={
            "close": _safe_float(current.get("close")),
            "ema_20": _safe_float(current.get("ema_20")),
            "ema_50": _safe_float(current.get("ema_50")),
            "ema_200": _safe_float(current.get("ema_200")),
            "adx": _safe_float(current.get("adx")),
            "atr_percent": atr_percent(current),
            "ema_spread_percent": ema_spread_percent(current),
            "candle_range_percent": candle_range_percent(current),
            "macd_histogram": _safe_float(current.get("macd_histogram")),
            "previous_macd_histogram": _safe_float(previous.get("macd_histogram")),
        },
    )


def classify_regime(
    current: pd.Series | dict,
    previous: pd.Series | dict | None = None,
    volatility_state: VolatilityState | None = None,
    config: MarketRegimeConfig | None = None,
) -> Regime:
    config = config or MarketRegimeConfig()
    current = pd.Series(current)
    previous = pd.Series(previous) if previous is not None else pd.Series(dtype=float)
    volatility_state = volatility_state or classify_volatility(current, config=config)

    if volatility_state == "dangerous":
        return "volatile"

    if _required_missing(current):
        return "unknown"

    if is_bullish_structure(current, config=config):
        return "bullish"

    if is_bearish_structure(current, config=config):
        return "bearish"

    if is_sideways_structure(current, config=config):
        return "sideways"

    histogram = current.get("macd_histogram")
    previous_histogram = previous.get("macd_histogram")
    if not _has_nan(histogram, previous_histogram) and abs(histogram) < abs(previous_histogram):
        return "sideways"

    return "unknown"


def is_bullish_structure(
    row: pd.Series | dict,
    config: MarketRegimeConfig | None = None,
) -> bool:
    config = config or MarketRegimeConfig()
    row = pd.Series(row)

    ema_20 = row.get("ema_20")
    ema_50 = row.get("ema_50")
    ema_200 = row.get("ema_200")
    close = row.get("close")
    adx = row.get("adx")

    if _has_nan(ema_20, ema_50, ema_200, close, adx):
        return False

    price_above_ema50 = close >= ema_50 * (1 + config.price_above_ema50_buffer_percent / 100)
    return ema_20 > ema_50 > ema_200 and price_above_ema50 and adx >= config.adx_trend_threshold


def is_bearish_structure(
    row: pd.Series | dict,
    config: MarketRegimeConfig | None = None,
) -> bool:
    config = config or MarketRegimeConfig()
    row = pd.Series(row)

    ema_20 = row.get("ema_20")
    ema_50 = row.get("ema_50")
    ema_200 = row.get("ema_200")
    close = row.get("close")
    adx = row.get("adx")

    if _has_nan(ema_20, ema_50, ema_200, close, adx):
        return False

    price_below_ema50 = close <= ema_50 * (1 - config.price_below_ema50_buffer_percent / 100)
    return ema_20 < ema_50 < ema_200 and price_below_ema50 and adx >= config.adx_trend_threshold


def is_sideways_structure(
    row: pd.Series | dict,
    config: MarketRegimeConfig | None = None,
) -> bool:
    config = config or MarketRegimeConfig()
    row = pd.Series(row)
    spread = ema_spread_percent(row)
    adx = row.get("adx")

    if _has_nan(spread, adx):
        return False

    return adx < config.adx_weak_threshold or spread <= config.ema_spread_sideways_percent


def classify_volatility(
    row: pd.Series | dict,
    config: MarketRegimeConfig | None = None,
) -> VolatilityState:
    config = config or MarketRegimeConfig()
    row = pd.Series(row)
    atr = atr_percent(row)
    candle_range = candle_range_percent(row)

    if _has_nan(atr):
        return "unknown"

    if atr >= config.atr_dangerous_percent or (
        not _is_nan(candle_range)
        and candle_range >= config.candle_expansion_percent
    ):
        return "dangerous"

    if atr >= config.atr_elevated_percent:
        return "elevated"

    return "safe"


def classify_trend_strength(
    row: pd.Series | dict,
    config: MarketRegimeConfig | None = None,
) -> TrendStrength:
    config = config or MarketRegimeConfig()
    row = pd.Series(row)
    adx = row.get("adx")

    if _is_nan(adx):
        return "unknown"

    if adx >= config.adx_strong_threshold:
        return "strong"

    if adx >= config.adx_trend_threshold:
        return "moderate"

    return "weak"


def estimate_regime_strength(
    current: pd.Series | dict,
    regime: Regime,
    volatility_state: VolatilityState,
    trend_strength: TrendStrength,
    config: MarketRegimeConfig | None = None,
) -> int:
    config = config or MarketRegimeConfig()
    current = pd.Series(current)

    if regime == "unknown":
        return 0

    strength = 0

    if regime in {"bullish", "bearish"}:
        strength += 35
    elif regime == "sideways":
        strength += 25
    elif regime == "volatile":
        strength += 45

    if trend_strength == "strong":
        strength += 30
    elif trend_strength == "moderate":
        strength += 20
    elif trend_strength == "weak":
        strength += 5

    spread = ema_spread_percent(current)
    if not _is_nan(spread):
        strength += min(int(spread * 4), 20)

    if volatility_state == "safe":
        strength += 15
    elif volatility_state == "elevated":
        strength += 5
    elif volatility_state == "dangerous":
        strength += 20

    return max(0, min(100, strength))


def confidence_for_regime(
    regime: Regime,
    strength: int,
    volatility_state: VolatilityState,
    trend_strength: TrendStrength,
) -> Confidence:
    if regime == "unknown":
        return "low"

    if regime == "volatile" and volatility_state == "dangerous":
        return "high"

    if strength >= 75 and trend_strength in {"strong", "moderate"}:
        return "high"

    if strength >= 55:
        return "medium"

    return "low"


def build_regime_reasons(
    current: pd.Series | dict,
    regime: Regime,
    volatility_state: VolatilityState,
    trend_strength: TrendStrength,
    config: MarketRegimeConfig | None = None,
) -> list[str]:
    config = config or MarketRegimeConfig()
    current = pd.Series(current)
    reasons = []

    if regime == "bullish":
        reasons.append("EMA alignment is bullish")
        reasons.append("Price is above EMA50")
    elif regime == "bearish":
        reasons.append("EMA alignment is bearish")
        reasons.append("Price is below EMA50")
    elif regime == "sideways":
        reasons.append("ADX is weak or EMA spread is tight")
    elif regime == "volatile":
        reasons.append("Volatility is dangerous")

    if trend_strength in {"strong", "moderate"}:
        reasons.append(f"ADX indicates {trend_strength} trend strength")

    if volatility_state == "safe":
        reasons.append("ATR is in a safe range")
    elif volatility_state == "elevated":
        reasons.append("ATR is elevated")

    spread = ema_spread_percent(current)
    if not _is_nan(spread):
        reasons.append(f"EMA spread is {spread:.2f}%")

    return reasons


def build_regime_warnings(
    current: pd.Series | dict,
    previous: pd.Series | dict,
    regime: Regime,
    volatility_state: VolatilityState,
    trend_strength: TrendStrength,
    config: MarketRegimeConfig | None = None,
) -> list[str]:
    config = config or MarketRegimeConfig()
    current = pd.Series(current)
    previous = pd.Series(previous)
    warnings = []

    if volatility_state == "elevated":
        warnings.append("Volatility is elevated")
    elif volatility_state == "dangerous":
        warnings.append("Volatility is dangerous")

    if trend_strength == "weak" and regime not in {"sideways", "unknown"}:
        warnings.append("Trend strength is weak")

    histogram = current.get("macd_histogram")
    previous_histogram = previous.get("macd_histogram")
    if not _has_nan(histogram, previous_histogram):
        if regime == "bullish" and histogram < previous_histogram:
            warnings.append("Bullish momentum is weakening")
        elif regime == "bearish" and histogram > previous_histogram:
            warnings.append("Bearish momentum is weakening")

    if regime == "unknown":
        warnings.append("Regime is uncertain")

    return warnings


def atr_percent(row: pd.Series | dict) -> float:
    row = pd.Series(row)
    atr = row.get("atr")
    close = row.get("close")

    if _has_nan(atr, close) or close == 0:
        return np.nan

    return float(atr / close * 100)


def ema_spread_percent(row: pd.Series | dict) -> float:
    row = pd.Series(row)
    ema_20 = row.get("ema_20")
    ema_200 = row.get("ema_200")
    close = row.get("close")

    if _has_nan(ema_20, ema_200, close) or close == 0:
        return np.nan

    return float(abs(ema_20 - ema_200) / close * 100)


def candle_range_percent(row: pd.Series | dict) -> float:
    row = pd.Series(row)
    high = row.get("high")
    low = row.get("low")
    close = row.get("close")

    if _has_nan(high, low, close) or close == 0:
        return np.nan

    return float((high - low) / close * 100)


def _required_missing(row: pd.Series) -> bool:
    return _has_nan(
        row.get("close"),
        row.get("ema_20"),
        row.get("ema_50"),
        row.get("ema_200"),
        row.get("adx"),
        row.get("atr"),
    )


def _safe_float(value) -> float | None:
    if _is_nan(value):
        return None
    return float(value)


def _has_nan(*values) -> bool:
    return any(_is_nan(value) for value in values)


def _is_nan(value) -> bool:
    return value is None or bool(pd.isna(value))
