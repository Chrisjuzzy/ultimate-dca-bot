import logging
import threading
import time
from typing import Any, List, Optional

import ccxt

logger = logging.getLogger(__name__)


class MarketData:
    """Simple CCXT-based public market data helper with short TTL caching

    - Supports public endpoints only (no API keys required)
    - Uses Binance as primary public feed and fails over safely
    - Retries transient fetch errors before returning cached data
    - Provides: price, 24h change, ohlcv, volume
    """

    def __init__(self, ttl: int = 20, max_retries: int = 2, retry_delay: float = 0.5) -> None:
        self._ttl = int(ttl)
        self._lock = threading.Lock()
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._exchange_names = ["binance"]
        self._exchanges: list[ccxt.Exchange] = []

        for name in self._exchange_names:
            try:
                exchange_cls = getattr(ccxt, name)
                exchange = exchange_cls({"enableRateLimit": True})
                exchange.headers = {"User-Agent": "ultimate-dca-bot/1.0"}
                self._exchanges.append(exchange)
            except Exception as exc:  # pragma: no cover - runtime environmental
                logger.warning("Failed to init ccxt.%s: %s", name, exc)

        self._cache: dict[str, tuple[float, Any]] = {}

    def _cached(self, key: str) -> Optional[Any]:
        with self._lock:
            data = self._cache.get(key)
            if not data:
                return None
            ts, payload = data
            if time.time() - ts > self._ttl:
                return None
            return payload

    def _set_cache(self, key: str, payload: Any) -> None:
        with self._lock:
            self._cache[key] = (time.time(), payload)

    def _fetch_with_retry(self, method_name: str, symbol: str, *args: Any, **kwargs: Any) -> Optional[Any]:
        if not self._exchanges:
            return None

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries):
            for exchange in self._exchanges:
                try:
                    method = getattr(exchange, method_name, None)
                    if method is None:
                        continue
                    return method(symbol, *args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "%s %s failed on %s attempt %d: %s",
                        method_name,
                        symbol,
                        exchange.id,
                        attempt + 1,
                        exc,
                    )
            if attempt + 1 < self._max_retries:
                time.sleep(self._retry_delay)

        if last_error is not None:
            logger.warning("All retry attempts failed for %s %s", method_name, symbol)
        return None

    def fetch_ticker(self, symbol: str) -> Optional[dict]:
        key = f"ticker:{symbol}"
        cached = self._cached(key)
        if cached is not None:
            return cached

        ticker = self._fetch_with_retry("fetch_ticker", symbol)
        if ticker is not None:
            self._set_cache(key, ticker)
            return ticker
        return self._cached(key)

    def get_price(self, symbol: str) -> Optional[float]:
        ticker = self.fetch_ticker(symbol)
        if not ticker:
            return None
        last = ticker.get("last")
        try:
            return float(last) if last is not None else None
        except (TypeError, ValueError):
            return None

    def fetch_24h_change(self, symbol: str) -> Optional[float]:
        ticker = self.fetch_ticker(symbol)
        if not ticker:
            return None
        pct = ticker.get("percentage")
        if pct is not None:
            try:
                return float(pct)
            except (TypeError, ValueError):
                pass

        try:
            open_price = float(ticker.get("open", 0) or 0)
            last = float(ticker.get("last", 0) or 0)
            if open_price > 0:
                return (last - open_price) / open_price * 100.0
        except Exception:
            pass
        return None

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 200) -> Optional[List[List[Any]]]:
        key = f"ohlcv:{symbol}:{timeframe}:{limit}"
        cached = self._cached(key)
        if cached is not None:
            return cached

        ohlcv = self._fetch_with_retry("fetch_ohlcv", symbol, timeframe=timeframe, limit=limit)
        if ohlcv is not None:
            self._set_cache(key, ohlcv)
            return ohlcv
        return self._cached(key)

    def fetch_volume(self, symbol: str) -> Optional[float]:
        ticker = self.fetch_ticker(symbol)
        if not ticker:
            return None
        vol = ticker.get("baseVolume") or ticker.get("quoteVolume")
        try:
            return float(vol) if vol is not None else None
        except (TypeError, ValueError):
            return None


# module-level instance for convenience
market_data = MarketData()
