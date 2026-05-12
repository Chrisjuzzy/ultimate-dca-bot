# Ultimate DCA Bot Finalization Runbook

This system must be validated in this order:

1. Dashboard validation
2. Backtesting validation
3. Paper trading validation
4. VPS deployment
5. Micro real-money validation
6. Safe optimization

## 1. Dashboard Validation

Run:

```powershell
.venv\Scripts\activate
streamlit run dashboard.py
```

Verify:

- Portfolio panel loads.
- Equity curve panel shows either chart data or an empty-state message.
- Open positions table renders.
- Trade feed reads `data/trade_journal.jsonl` when present.
- Bot health panel shows logs, database, positions, and journal state.
- No red Streamlit exceptions appear.

## 2. Backtesting Validation

Use `backtesting/backtester.py` to test BTC and ETH across:

- Bullish trend periods.
- Bearish periods.
- Sideways periods.
- High-volatility periods.

Primary success metrics:

- Low drawdown.
- Reasonable trade frequency.
- Stable recovery behavior.
- No excessive rejected-trade spam.
- Profit factor above 1 after realistic fees.

Do not optimize for maximum profit.

## 3. Paper Trading Validation

Run paper mode for 2-4 weeks minimum.

Watch:

- Trade quality.
- Drawdown.
- Fees.
- Slippage.
- Reconnect behavior.
- Duplicate-order prevention.
- Position state accuracy.
- Journal completeness.

Paper trading must pass before real money.

## 4. VPS Deployment

Recommended low-cost options:

- Oracle Cloud Free Tier.
- Hetzner Cloud.
- Contabo VPS.

Minimum server:

- Ubuntu 22.04.
- 1-2 vCPU.
- 2 GB RAM.
- Python 3.11+.

Install:

```bash
sudo apt update
sudo apt install python3 python3-venv git tmux supervisor -y
```

Deployment requirements:

- `.env` stored only on the server.
- Auto restart via `supervisor` or `systemd`.
- Logs persisted.
- Health checks monitored.
- Telegram alerts verified.

## 5. Micro Real-Money Validation

Start extremely small.

Goal:

- Validate execution stability, not profit.

Watch:

- Real fills.
- Exchange latency.
- Slippage.
- Fees.
- Duplicate prevention.
- Position state recovery after restart.

## 6. Safe Optimization

Only optimize after 100-300+ trades.

Allowed optimization targets:

- Score threshold.
- Cooldown length.
- Take-profit levels.
- ATR stop multipliers.
- Position sizing curves.

Avoid:

- Curve fitting.
- Overtrading.
- Aggressive compounding.
- Adding many coins too early.

Professional priority:

Capital survival > consistency > drawdown control > slow compounding > profit.
