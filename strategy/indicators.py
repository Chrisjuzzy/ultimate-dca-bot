from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange


OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
REQUIRED_PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]
INDICATOR_COLUMNS = [
    "rsi",
    "ema_20",
    "ema_50",
    "ema_200",
    "macd",
    "macd_signal",
    "macd_histogram",
    "atr",
    "adx",
    "adx_positive",
    "adx_negative",
    "volume_sma",
    "volume_ratio",
    "volume_increasing",
]


@dataclass(frozen=True)
class IndicatorConfig:
    rsi_window: int = 14
    ema_short_window: int = 20
    ema_medium_window: int = 50
    ema_macro_window: int = 200
    macd_fast_window: int = 12
    macd_slow_window: int = 26
    macd_signal_window: int = 9
    atr_window: int = 14
    adx_window: int = 14
    volume_sma_window: int = 20


def prepare_ohlcv(candles: pd.DataFrame | Iterable[Iterable[float]]) -> pd.DataFrame:
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(candles, columns=OHLCV_COLUMNS)

    df.columns = [str(column).lower() for column in df.columns]

    missing = [column for column in REQUIRED_PRICE_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required OHLCV columns: {', '.join(missing)}")

    for column in REQUIRED_PRICE_COLUMNS:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if "timestamp" in df.columns:
        if pd.api.types.is_numeric_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    return df.sort_index()


def add_indicators(
    candles: pd.DataFrame | Iterable[Iterable[float]],
    config: IndicatorConfig | None = None,
) -> pd.DataFrame:
    config = config or IndicatorConfig()
    df = prepare_ohlcv(candles)
    if all(column in df.columns for column in INDICATOR_COLUMNS):
        return df

    df["rsi"] = _safe_series(
        df,
        lambda: RSIIndicator(
            close=df["close"],
            window=config.rsi_window,
            fillna=False,
        ).rsi(),
    )

    df["ema_20"] = _safe_series(
        df,
        lambda: EMAIndicator(
            close=df["close"],
            window=config.ema_short_window,
            fillna=False,
        ).ema_indicator(),
    )
    df["ema_50"] = _safe_series(
        df,
        lambda: EMAIndicator(
            close=df["close"],
            window=config.ema_medium_window,
            fillna=False,
        ).ema_indicator(),
    )
    df["ema_200"] = _safe_series(
        df,
        lambda: EMAIndicator(
            close=df["close"],
            window=config.ema_macro_window,
            fillna=False,
        ).ema_indicator(),
    )

    macd = MACD(
        close=df["close"],
        window_fast=config.macd_fast_window,
        window_slow=config.macd_slow_window,
        window_sign=config.macd_signal_window,
        fillna=False,
    )
    df["macd"] = _safe_series(df, macd.macd)
    df["macd_signal"] = _safe_series(df, macd.macd_signal)
    df["macd_histogram"] = _safe_series(df, macd.macd_diff)

    df["atr"] = _safe_series(
        df,
        lambda: AverageTrueRange(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            window=config.atr_window,
            fillna=False,
        ).average_true_range(),
    )

    adx = ADXIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=config.adx_window,
        fillna=False,
    )
    df["adx"] = _safe_series(df, adx.adx)
    df["adx_positive"] = _safe_series(df, adx.adx_pos)
    df["adx_negative"] = _safe_series(df, adx.adx_neg)

    df["volume_sma"] = df["volume"].rolling(config.volume_sma_window).mean()
    df["volume_ratio"] = np.where(
        df["volume_sma"] > 0,
        df["volume"] / df["volume_sma"],
        np.nan,
    )
    df["volume_increasing"] = df["volume"] > df["volume_sma"]

    return df


def latest_indicators(
    candles: pd.DataFrame | Iterable[Iterable[float]],
    config: IndicatorConfig | None = None,
) -> dict:
    df = add_indicators(candles, config=config)
    if df.empty:
        return {}
    return df.iloc[-1].to_dict()


def indicators_ready(indicator_row: pd.Series | dict) -> bool:
    row = pd.Series(indicator_row)
    required = [
        "rsi",
        "ema_20",
        "ema_50",
        "ema_200",
        "macd",
        "macd_signal",
        "atr",
        "adx",
        "volume_sma",
    ]
    return row.reindex(required).notna().all()


def _safe_series(df: pd.DataFrame, calculate) -> pd.Series:
    try:
        return calculate()
    except (IndexError, ValueError):
        return pd.Series(np.nan, index=df.index)
