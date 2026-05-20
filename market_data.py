import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

import ccxt
import requests

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
        self._http_timeout = 8

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
        if ticker is None:
            ticker = self._fetch_binance_ticker_http(symbol)
        if ticker is None:
            ticker = self._fetch_coingecko_ticker(symbol)
        if ticker is None:
            ticker = self._fallback_ticker_from_local(symbol)
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
        if ohlcv is None:
            ohlcv = self._fetch_binance_ohlcv_http(symbol, timeframe=timeframe, limit=limit)
        if ohlcv is None:
            ohlcv = self._fallback_ohlcv_from_local(symbol, limit=limit)
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

    def _fetch_binance_ticker_http(self, symbol: str) -> Optional[dict]:
        pair = symbol.replace("/", "")
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={pair}"
        try:
            response = requests.get(url, timeout=self._http_timeout)
            response.raise_for_status()
            payload = response.json()
            return {
                "symbol": symbol,
                "last": float(payload.get("lastPrice", 0.0) or 0.0),
                "percentage": float(payload.get("priceChangePercent", 0.0) or 0.0),
                "open": float(payload.get("openPrice", 0.0) or 0.0),
                "baseVolume": float(payload.get("volume", 0.0) or 0.0),
                "quoteVolume": float(payload.get("quoteVolume", 0.0) or 0.0),
            }
        except Exception as exc:
            logger.warning("binance ticker http failed for %s: %s", symbol, exc)
            return None

    def _fetch_binance_ohlcv_http(self, symbol: str, timeframe: str, limit: int) -> Optional[List[List[Any]]]:
        pair = symbol.replace("/", "")
        interval = timeframe if timeframe in {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"} else "1h"
        url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval={interval}&limit={max(1, min(1000, limit))}"
        try:
            response = requests.get(url, timeout=self._http_timeout)
            response.raise_for_status()
            rows = response.json()
            data: list[list[Any]] = []
            for row in rows:
                data.append(
                    [
                        int(row[0]),
                        float(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[5]),
                    ]
                )
            return data
        except Exception as exc:
            logger.warning("binance ohlcv http failed for %s: %s", symbol, exc)
            return None

    def _fetch_coingecko_ticker(self, symbol: str) -> Optional[dict]:
        coin_id = {
            "BTC/USDT": "bitcoin",
            "ETH/USDT": "ethereum",
        }.get(symbol)
        if coin_id is None:
            return None
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd&include_24hr_change=true"
        )
        try:
            response = requests.get(url, timeout=self._http_timeout)
            response.raise_for_status()
            payload = response.json().get(coin_id, {})
            return {
                "symbol": symbol,
                "last": float(payload.get("usd", 0.0) or 0.0),
                "percentage": float(payload.get("usd_24h_change", 0.0) or 0.0),
            }
        except Exception as exc:
            logger.warning("coingecko ticker failed for %s: %s", symbol, exc)
            return None

    def _fallback_ticker_from_local(self, symbol: str) -> Optional[dict]:
        candles = self._fallback_ohlcv_from_local(symbol, limit=30)
        if not candles:
            return None
        last = candles[-1][4]
        first = candles[0][4]
        change = ((last - first) / first * 100.0) if first else 0.0
        return {
            "symbol": symbol,
            "last": float(last),
            "percentage": float(change),
            "open": float(first),
        }

    def _fallback_ohlcv_from_local(self, symbol: str, limit: int) -> Optional[List[List[Any]]]:
        path = Path("data") / "candles" / "validation" / f"{symbol.replace('/', '_')}_1h.csv"
        if not path.exists():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) <= 1:
                return None
            rows = lines[1:][-max(1, limit):]
            data: list[list[Any]] = []
            for row in rows:
                parts = row.split(",")
                if len(parts) < 6:
                    continue
                data.append(
                    [
                        self._to_millis(parts[0]),
                        float(parts[1]),
                        float(parts[2]),
                        float(parts[3]),
                        float(parts[4]),
                        float(parts[5]),
                    ]
                )
            return data or None
        except Exception as exc:
            logger.warning("local ohlcv fallback failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _to_millis(timestamp_value: str) -> int:
        text = str(timestamp_value).strip()
        try:
            return int(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return int(dt.timestamp() * 1000)
            except Exception:
                return int(time.time() * 1000)


# module-level instance for convenience
market_data = MarketData()
