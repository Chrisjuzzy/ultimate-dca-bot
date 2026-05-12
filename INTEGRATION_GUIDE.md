"""Integration guide: How to record trades to the professional trade history system."""

# Example 1: Record a trade when a position closes (in execution/exits.py)
# =========================================================================

from analytics.trade_history import TradeHistoryManager

def record_closed_position_to_history(position, exit_price, exit_reason):
    """Record a closed position to the trade history."""
    
    trade_history = TradeHistoryManager()
    
    # Calculate metrics
    hold_minutes = int((datetime.now(UTC) - datetime.fromisoformat(position.opened_at)).total_seconds() / 60)
    pnl_usdt = position.realized_pnl_usdt
    pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100 if position.entry_price > 0 else 0.0
    
    # Record to history
    trade_history.record_trade(
        symbol=position.symbol,
        side="buy",  # or "sell" depending on position type
        entry_price=position.entry_price,
        exit_price=exit_price,
        quantity=position.quantity,
        pnl_usdt=pnl_usdt,
        pnl_pct=pnl_pct,
        hold_minutes=hold_minutes,
        score=position.entry_score,
        regime=position.regime_at_entry,
        exit_reason=exit_reason,  # e.g., "tp1", "tp2", "stoploss", "timeout"
        entry_reason=position.entry_reasons or [],  # List of entry signals
        fees_usdt=position.total_fees_usdt,
        order_id=position.symbol  # or get actual order_id
    )
    
    print(f"✅ Trade recorded: {position.symbol} {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%)")


# Example 2: Enhanced position with entry reasons (in portfolio/positions.py)
# =========================================================================

@dataclass
class Position:
    # ... existing fields ...
    entry_reasons: list[str] = field(default_factory=list)  # Add this field
    
    def add_entry_reason(self, reason: str) -> None:
        """Track why the bot entered this position."""
        if reason not in self.entry_reasons:
            self.entry_reasons.append(reason)


# Example 3: Populate entry reasons during entry decision (in strategy/signals.py)
# ===============================================================================

def evaluate_entry(symbol: str) -> dict:
    """Evaluate entry with reasons."""
    
    reasons = []
    score = 0
    
    # Technical analysis
    if is_bullish_regime():
        reasons.append("bullish_regime")
        score += 20
    
    if rsi_oversold():
        reasons.append("rsi_recovery")
        score += 15
    
    if macd_bullish_cross():
        reasons.append("macd_crossover")
        score += 15
    
    if volume_strong():
        reasons.append("volume_confirmation")
        score += 10
    
    if volatility_safe():
        reasons.append("volatility_safe")
        score += 10
    
    return {
        "should_enter": score >= 50,
        "score": min(100, score),
        "reasons": reasons,
        "regime": get_market_regime(),
    }


# Example 4: Dashboard usage (already implemented in dashboard.py)
# ================================================================

# In the dashboard tab for trade history:
from analytics.trade_history import TradeHistoryManager

trade_history_mgr = TradeHistoryManager(TRADE_HISTORY_FILE)

# Get filtered trades
trades = trade_history_mgr.load_filtered(
    time_filter="today",
    symbols=["BTC/USDT"],
    trade_type="wins"
)

# Get statistics
stats = trade_history_mgr.get_stats(time_filter="7d")
print(f"7-day win rate: {stats.win_rate_pct:.1f}%")
print(f"7-day profit factor: {stats.profit_factor:.2f}")

# Get daily performance
daily_perf = trade_history_mgr.get_daily_performance("today")
if daily_perf:
    today = daily_perf[-1]
    print(f"Today: {today.trades_count} trades, {today.win_rate_pct:.1f}% win rate")

# Export to CSV
trade_history_mgr.export_csv("data/trade_history_2026.csv", time_filter="all")


# Example 5: Professional Analytics (Monthly Review)
# ==================================================

def generate_monthly_review():
    """Generate professional monthly performance review."""
    
    trade_history = TradeHistoryManager()
    stats = trade_history.get_stats(time_filter="30d")
    
    if not stats:
        print("No trades this month.")
        return
    
    print("=" * 60)
    print(f"MONTHLY PERFORMANCE SUMMARY")
    print("=" * 60)
    print(f"Total Trades: {stats.total_trades}")
    print(f"Win Rate: {stats.win_rate_pct:.2f}%")
    print(f"Net PnL: {stats.net_pnl_usdt:+.2f} USDT")
    print(f"Profit Factor: {stats.profit_factor:.2f}")
    print(f"Expectancy: {stats.expectancy:+.4f} USDT/trade")
    print(f"Max Drawdown: (use performance report)")
    print(f"Recovery Factor: {stats.recovery_factor:.2f}")
    print(f"Best Hour: {stats.best_trading_hour}:00")
    print(f"Longest Win Streak: {stats.longest_winning_streak} trades")
    print(f"Longest Loss Streak: {stats.longest_losing_streak} trades")
    print("=" * 60)
    
    # Best and worst setups
    best_setup = trade_history.get_best_setup()
    worst_setup = trade_history.get_worst_setup()
    
    print(f"\n🏆 Best Regime: {best_setup['regime']}")
    print(f"   Avg PnL: {best_setup['avg_pnl']:+.4f} | Win Rate: {best_setup['win_rate']:.1f}%")
    print(f"\n📉 Worst Regime: {worst_setup['regime']}")
    print(f"   Avg PnL: {worst_setup['avg_pnl']:+.4f} | Win Rate: {worst_setup['win_rate']:.1f}%")


# KEY IMPLEMENTATION CHECKLIST
# =============================
# [ ] Add entry_reasons field to Position dataclass
# [ ] Update position.add_entry_reason() calls in strategy modules
# [ ] Update execution exits to call trade_history.record_trade()
# [ ] Populate all entry_reason fields from signals
# [ ] Test dashboard with sample trades
# [ ] Run paper trading validation (2-4 weeks)
# [ ] Review trade history periodically for insights
