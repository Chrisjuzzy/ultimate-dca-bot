from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Iterable

import ccxt
import pandas as pd

from strategy.indicators import add_indicators
from strategy.market_regime import detect_market_regime
from strategy.market_stress import symbol_stress_score
from strategy.signals import analyze_signals


DEFAULT_SYMBOLS = ("BTC/USDT", "ETH/USDT")


@dataclass(frozen=True)
class MarketTicker:
    symbol: str
    price: float
    change_percent_24h: float
    quote_volume_24h: float
    timestamp: str
    trend: str
    volatility_state: str
    regime: str
    stress_score: int

    def to_dict(self) -> dict:
        return asdict(self)


def build_public_exchange() -> ccxt.Exchange:
    return ccxt.binance({"enableRateLimit": True})


def fetch_tickers(symbols: Iterable[str] = DEFAULT_SYMBOLS) -> dict[str, MarketTicker]:
    exchange = build_public_exchange()
    tickers = {}
    for symbol in symbols:
        try:
            ticker = exchange.fetch_ticker(symbol)
            candles = fetch_candles(symbol=symbol, exchange=exchange, limit=220)
            signals = analyze_signals(candles)
            regime = detect_market_regime(candles)
            tickers[symbol] = MarketTicker(
                symbol=symbol,
                price=_safe_float(ticker.get("last")),
                change_percent_24h=_safe_float(ticker.get("percentage")),
                quote_volume_24h=_safe_float(ticker.get("quoteVolume")),
                timestamp=datetime.now(UTC).isoformat(),
                trend=signals.trend,
                volatility_state=regime.volatility_state,
                regime=regime.regime,
                stress_score=symbol_stress_score(signals, regime),
            )
        except Exception:
            continue
    return tickers


def fetch_candles(
    symbol: str,
    timeframe: str = "15m",
    limit: int = 160,
    exchange: ccxt.Exchange | None = None,
) -> pd.DataFrame:
    exchange = exchange or build_public_exchange()
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(
        ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    return add_indicators(df)


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
