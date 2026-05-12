# Ultimate DCA Bot

Safety-first retail trading infrastructure for Binance Spot/Testnet. The system is designed to validate market conditions, control risk, simulate execution, track positions, and expose operational health before any real-money deployment.

## Current Rule

Do not enable real-money trading until validation, backtesting, paper trading, and long-runtime stability checks pass.

## Quick Validation

```powershell
.venv\Scripts\activate
python scripts\run_validation_suite.py --max-steps 400
python scripts\validate_project.py
python monitoring\health_monitor.py
streamlit run dashboard.py
```

## Dashboard Bot Control

The dashboard includes a runtime-safe control panel:

- `START BOT` enables the bot loop.
- `STOP BOT` pauses the bot loop without killing the process.
- `SOFT RESTART` asks the bot to rebuild runtime state and reconnect cleanly.
- `MODE` toggles the operator control file between `paper` and `live` labels.

Control state lives in `data/runtime_control.json`. The safest operating pattern is to keep the process alive, pause trading from the dashboard when needed, and monitor health/logs before re-enabling.

For phone access on the same trusted WiFi:

```powershell
streamlit run dashboard.py --server.address 0.0.0.0
```

Then open `http://YOUR_LAPTOP_IPV4:8504` from your phone. Do not expose this dashboard publicly without authentication and HTTPS.

## Live Market Dashboard

The Overview tab includes lightweight BTC/ETH market cards, candlestick charts, EMA overlays, trade markers, position cards, and a bot-brain panel. Market data is cached for 30 seconds to protect the laptop, mobile browser, and exchange rate limits.

If public Binance market data is unavailable, the dashboard falls back to local validation candles from `data/candles/validation` so the UI remains usable instead of crashing.

For a full synthetic replay, omit `--max-steps`:

```powershell
python scripts\run_validation_suite.py
```

For continuous paper validation:

```powershell
python scripts\run_paper_trading.py --data-dir data/candles/validation --timeframe 1h --watch --interval-seconds 3600
```

Each run appends summary metrics to `data/paper_metrics.jsonl` and refreshes `data/paper_report.json` for the dashboard.

The dashboard defaults to Streamlit's printed URL. If multiple dashboards have been run before, use the exact port Streamlit prints in the terminal.

## Backtesting And Paper Validation

Place local candle CSV files in `data/candles`.

Expected filename examples:

```text
data/candles/BTC_USDT_1h.csv
data/candles/ETH_USDT_1h.csv
```

Expected columns:

```text
timestamp,open,high,low,close,volume
```

Run:

```powershell
python scripts\run_paper_trading.py --timeframe 1h --starting-equity 1000
```

If you do not have real candle files yet, generate deterministic validation data:

```powershell
python scripts\generate_validation_candles.py
python scripts\run_paper_trading.py --data-dir data/candles/validation --timeframe 1h --starting-equity 1000
```

The generated dataset includes bullish, sideways, bearish, and volatile segments. It is for engineering validation only, not profitability claims.

## Final Safety Features

- `strategy/market_stress.py` calculates global market stress from volatility, regimes, ATR, volume, and BTC conditions.
- `analytics/strategy_memory.py` tracks setup performance so future versions can boost strong setups and suppress weak ones.
- `risk/equity_protection.py` reduces risk when equity curve health weakens.

Primary metrics to review:

- `max_equity_drawdown_percent`
- `final_equity_usdt`
- `win_rate`
- `profit_factor`
- trade frequency
- rejected entry reasons

## Professional Validation Order

1. Run `python scripts\validate_project.py`.
2. Run `python monitoring\health_monitor.py`.
3. Launch `streamlit run dashboard.py`.
4. Run offline backtests using local candle CSV files.
5. Run paper trading for 2-4 weeks minimum.
6. Deploy to VPS only after paper stability.
7. Use tiny real-money size only after execution stability is proven.

## Security

Never commit `.env`, API keys, Telegram tokens, `data/positions.json`, database files, or logs. Rotate any keys or tokens that were ever pasted into chat before using real funds.

## System Philosophy

The edge is not prediction. The edge is discipline encoded into software:

- capital preservation
- selective entries
- adaptive risk
- exposure limits
- recovery mode
- execution safety
- measurable analytics
