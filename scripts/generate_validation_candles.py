from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


SCENARIOS = [
    ("bull", 260, 0.0018, 0.008, 1.15),
    ("sideways", 260, 0.0000, 0.006, 0.85),
    ("bear", 260, -0.0017, 0.010, 1.25),
    ("volatile", 260, 0.0004, 0.026, 2.40),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic candles for validation backtests."
    )
    parser.add_argument("--output-dir", default="data/candles/validation")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    assets = {
        "BTC_USDT": 65000.0,
        "ETH_USDT": 3200.0,
    }
    for symbol, start_price in assets.items():
        candles = build_synthetic_candles(start_price=start_price, rng=rng)
        path = output_dir / f"{symbol}_{args.timeframe}.csv"
        candles.to_csv(path, index=False)
        print(f"Wrote {len(candles)} candles: {path}")


def build_synthetic_candles(start_price: float, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    price = start_price
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)

    for scenario, length, drift, volatility, volume_multiplier in SCENARIOS:
        for step in range(length):
            shock = rng.normal(drift, volatility)
            if scenario == "volatile" and step % 53 == 0:
                shock += rng.choice([-1, 1]) * rng.uniform(0.035, 0.07)

            open_price = price
            close_price = max(0.01, open_price * (1 + shock))
            candle_range = abs(close_price - open_price) + open_price * rng.uniform(0.002, volatility * 1.8)
            high = max(open_price, close_price) + candle_range * rng.uniform(0.15, 0.65)
            low = max(0.01, min(open_price, close_price) - candle_range * rng.uniform(0.15, 0.65))
            volume = max(
                1.0,
                rng.normal(1000 * volume_multiplier, 180 * volume_multiplier),
            )

            rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "open": round(open_price, 8),
                    "high": round(high, 8),
                    "low": round(low, 8),
                    "close": round(close_price, 8),
                    "volume": round(volume, 8),
                    "scenario": scenario,
                }
            )

            price = close_price
            timestamp += timedelta(hours=1)

    return pd.DataFrame(rows)


if __name__ == "__main__":
    main()
