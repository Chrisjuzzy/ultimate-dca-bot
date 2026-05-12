from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

from strategy.indicators import add_indicators, indicators_ready


TrendLabel = Literal["bullish", "bearish", "sideways", "unknown"]
MomentumLabel = Literal["bullish", "bearish", "improving", "weak", "unknown"]


@dataclass(frozen=True)
class SignalConfig:
    adx_trend_threshold: float = 20.0
    adx_sideways_threshold: float = 16.0
    ema_spread_sideways_percent: float = 0.75
    rsi_oversold: float = 35.0
    rsi_recovery_level: float = 40.0
    atr_safe_percent: float = 4.0
    atr_high_percent: float = 6.0
    candle_expansion_percent: float = 5.0
    volume_confirmation_ratio: float = 1.2
    panic_volume_ratio: float = 2.5
    ema_near_percent: float = 1.5


@dataclass(frozen=True)
class SignalSnapshot:
    trend: TrendLabel
    momentum: MomentumLabel
    dip: bool
    volatility_safe: bool
    volume_confirmed: bool
    strength: int
    reasons: list[str]
    warnings: list[str]
    details: dict

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_signals(
    candles: pd.DataFrame,
    config: SignalConfig | None = None,
) -> SignalSnapshot:
    config = config or SignalConfig()
    df = add_indicators(candles)

    if len(df) < 2 or not indicators_ready(df.iloc[-1]):
        return SignalSnapshot(
            trend="unknown",
            momentum="unknown",
            dip=False,
            volatility_safe=False,
            volume_confirmed=False,
            strength=0,
            reasons=[],
            warnings=["Not enough indicator history"],
            details={},
        )

    current = df.iloc[-1]
    previous = df.iloc[-2]

    trend = detect_trend(current, config=config)
    momentum = detect_momentum(current, previous, config=config)
    volatility_safe = is_safe_volatility(current, config=config)
    volume_confirmed = has_volume_confirmation(current, config=config)
    dip = is_valid_dip(current, trend=trend, config=config)

    reasons = _build_reasons(
        current=current,
        trend=trend,
        momentum=momentum,
        dip=dip,
        volatility_safe=volatility_safe,
        volume_confirmed=volume_confirmed,
    )
    warnings = _build_warnings(
        current=current,
        trend=trend,
        volatility_safe=volatility_safe,
        config=config,
    )

    return SignalSnapshot(
        trend=trend,
        momentum=momentum,
        dip=dip,
        volatility_safe=volatility_safe,
        volume_confirmed=volume_confirmed,
        strength=estimate_signal_strength(
            trend=trend,
            momentum=momentum,
            dip=dip,
            volatility_safe=volatility_safe,
            volume_confirmed=volume_confirmed,
            current=current,
            config=config,
        ),
        reasons=reasons,
        warnings=warnings,
        details={
            "close": _safe_float(current.get("close")),
            "rsi": _safe_float(current.get("rsi")),
            "adx": _safe_float(current.get("adx")),
            "atr_percent": _atr_percent(current),
            "volume_ratio": _safe_float(current.get("volume_ratio")),
            "ema_20": _safe_float(current.get("ema_20")),
            "ema_50": _safe_float(current.get("ema_50")),
            "ema_200": _safe_float(current.get("ema_200")),
            "macd": _safe_float(current.get("macd")),
            "macd_signal": _safe_float(current.get("macd_signal")),
            "macd_histogram": _safe_float(current.get("macd_histogram")),
        },
    )


def detect_trend(row: pd.Series | dict, config: SignalConfig | None = None) -> TrendLabel:
    config = config or SignalConfig()
    row = pd.Series(row)

    ema_20 = row.get("ema_20")
    ema_50 = row.get("ema_50")
    ema_200 = row.get("ema_200")
    adx = row.get("adx")
    close = row.get("close")

    if _has_nan(ema_20, ema_50, ema_200, adx, close):
        return "unknown"

    ema_spread = abs(ema_20 - ema_200) / close * 100
    if adx < config.adx_sideways_threshold or ema_spread <= config.ema_spread_sideways_percent:
        return "sideways"

    if ema_20 > ema_50 > ema_200 and adx >= config.adx_trend_threshold:
        return "bullish"

    if ema_20 < ema_50 < ema_200:
        return "bearish"

    return "sideways"


def is_bullish_trend(row: pd.Series | dict, config: SignalConfig | None = None) -> bool:
    return detect_trend(row, config=config) == "bullish"


def is_bearish_trend(row: pd.Series | dict, config: SignalConfig | None = None) -> bool:
    return detect_trend(row, config=config) == "bearish"


def is_sideways_market(row: pd.Series | dict, config: SignalConfig | None = None) -> bool:
    return detect_trend(row, config=config) == "sideways"


def detect_momentum(
    current: pd.Series | dict,
    previous: pd.Series | dict,
    config: SignalConfig | None = None,
) -> MomentumLabel:
    config = config or SignalConfig()
    current = pd.Series(current)
    previous = pd.Series(previous)

    macd = current.get("macd")
    signal = current.get("macd_signal")
    histogram = current.get("macd_histogram")
    previous_histogram = previous.get("macd_histogram")
    rsi = current.get("rsi")
    previous_rsi = previous.get("rsi")

    if _has_nan(macd, signal, histogram, previous_histogram, rsi, previous_rsi):
        return "unknown"

    histogram_rising = histogram > previous_histogram
    rsi_rising = rsi > previous_rsi

    if macd > signal and histogram_rising:
        return "bullish"

    if histogram_rising and rsi_rising and rsi >= config.rsi_recovery_level:
        return "improving"

    if macd < signal and not histogram_rising:
        return "bearish"

    return "weak"


def has_bullish_momentum(
    current: pd.Series | dict,
    previous: pd.Series | dict,
    config: SignalConfig | None = None,
) -> bool:
    return detect_momentum(current, previous, config=config) in {"bullish", "improving"}


def has_bearish_momentum(
    current: pd.Series | dict,
    previous: pd.Series | dict,
    config: SignalConfig | None = None,
) -> bool:
    return detect_momentum(current, previous, config=config) == "bearish"


def is_valid_dip(
    row: pd.Series | dict,
    trend: TrendLabel | None = None,
    config: SignalConfig | None = None,
) -> bool:
    config = config or SignalConfig()
    row = pd.Series(row)
    trend = trend or detect_trend(row, config=config)

    if trend != "bullish":
        return False

    rsi = row.get("rsi")
    close = row.get("close")
    ema_50 = row.get("ema_50")
    volume_ratio = row.get("volume_ratio")

    if _has_nan(rsi, close, ema_50):
        return False

    near_ema_50 = abs(close - ema_50) / close * 100 <= config.ema_near_percent
    volume_not_panic = _is_nan(volume_ratio) or volume_ratio < config.panic_volume_ratio

    return (
        rsi <= config.rsi_oversold
        and near_ema_50
        and is_safe_volatility(row, config=config)
        and volume_not_panic
    )


def is_high_volatility(row: pd.Series | dict, config: SignalConfig | None = None) -> bool:
    config = config or SignalConfig()
    row = pd.Series(row)
    atr_percent = _atr_percent(row)
    candle_range_percent = _candle_range_percent(row)

    if _is_nan(atr_percent) and _is_nan(candle_range_percent):
        return False

    return (
        (not _is_nan(atr_percent) and atr_percent >= config.atr_high_percent)
        or (
            not _is_nan(candle_range_percent)
            and candle_range_percent >= config.candle_expansion_percent
        )
    )


def is_safe_volatility(row: pd.Series | dict, config: SignalConfig | None = None) -> bool:
    config = config or SignalConfig()
    row = pd.Series(row)
    atr_percent = _atr_percent(row)

    if _is_nan(atr_percent):
        return False

    return atr_percent <= config.atr_safe_percent and not is_high_volatility(
        row,
        config=config,
    )


def has_volume_confirmation(row: pd.Series | dict, config: SignalConfig | None = None) -> bool:
    config = config or SignalConfig()
    row = pd.Series(row)
    volume_ratio = row.get("volume_ratio")
    volume_increasing = bool(row.get("volume_increasing", False))

    if _is_nan(volume_ratio):
        return False

    return volume_ratio >= config.volume_confirmation_ratio and volume_increasing


def estimate_signal_strength(
    trend: TrendLabel,
    momentum: MomentumLabel,
    dip: bool,
    volatility_safe: bool,
    volume_confirmed: bool,
    current: pd.Series | dict,
    config: SignalConfig | None = None,
) -> int:
    config = config or SignalConfig()
    current = pd.Series(current)
    strength = 0

    if trend == "bullish":
        strength += 25
    elif trend == "sideways":
        strength += 8

    if momentum == "bullish":
        strength += 25
    elif momentum == "improving":
        strength += 18

    if dip:
        strength += 20

    if volatility_safe:
        strength += 15

    if volume_confirmed:
        strength += 10

    adx = current.get("adx")
    if not _is_nan(adx) and adx >= config.adx_trend_threshold:
        strength += 5

    return min(strength, 100)


def _build_reasons(
    current: pd.Series,
    trend: TrendLabel,
    momentum: MomentumLabel,
    dip: bool,
    volatility_safe: bool,
    volume_confirmed: bool,
) -> list[str]:
    reasons = []

    if trend == "bullish":
        reasons.append("EMA structure is bullish")
    elif trend == "bearish":
        reasons.append("EMA structure is bearish")
    elif trend == "sideways":
        reasons.append("Trend strength is limited or EMA spread is tight")

    if momentum in {"bullish", "improving"}:
        reasons.append(f"Momentum is {momentum}")

    if dip:
        reasons.append("Bullish pullback near EMA50 with controlled volatility")

    if volatility_safe:
        reasons.append("ATR is within safe range")

    if volume_confirmed:
        reasons.append("Volume confirms participation")

    rsi = current.get("rsi")
    if not _is_nan(rsi):
        reasons.append(f"RSI={rsi:.2f}")

    return reasons


def _build_warnings(
    current: pd.Series,
    trend: TrendLabel,
    volatility_safe: bool,
    config: SignalConfig,
) -> list[str]:
    warnings = []

    if trend == "bearish":
        warnings.append("Bearish trend structure")

    if not volatility_safe:
        warnings.append("Volatility is not safe")

    volume_ratio = current.get("volume_ratio")
    if not _is_nan(volume_ratio) and volume_ratio >= config.panic_volume_ratio:
        warnings.append("Possible panic volume")

    if is_high_volatility(current, config=config):
        warnings.append("High volatility detected")

    return warnings


def _atr_percent(row: pd.Series | dict) -> float:
    row = pd.Series(row)
    atr = row.get("atr")
    close = row.get("close")

    if _has_nan(atr, close) or close == 0:
        return np.nan

    return float(atr / close * 100)


def _candle_range_percent(row: pd.Series | dict) -> float:
    row = pd.Series(row)
    high = row.get("high")
    low = row.get("low")
    close = row.get("close")

    if _has_nan(high, low, close) or close == 0:
        return np.nan

    return float((high - low) / close * 100)


def _safe_float(value) -> float | None:
    if _is_nan(value):
        return None
    return float(value)


def _has_nan(*values) -> bool:
    return any(_is_nan(value) for value in values)


def _is_nan(value) -> bool:
    return value is None or bool(pd.isna(value))
