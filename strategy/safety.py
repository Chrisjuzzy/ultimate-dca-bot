"""
Auto Safe Mode System
Automatically triggers defensive/survival mode based on market conditions.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass
class SafeModeContext:
    """Context for determining safe mode activation."""
    api_reconnect_count: int = 0
    max_api_reconnects_threshold: int = 3
    current_drawdown_percent: float = 0.0
    max_drawdown_threshold_percent: float = 3.0
    is_internet_unstable: bool = False
    consecutive_losses: int = 0
    loss_threshold: int = 3


class SafeModeEngine:
    """Manages automatic safe mode activation."""
    
    @staticmethod
    def evaluate_safe_mode(context: SafeModeContext) -> tuple[bool, str]:
        """
        Evaluate if safe mode should be activated.
        
        Returns:
            (should_activate, reason)
        """
        # Check API reconnect threshold
        if context.api_reconnect_count >= context.max_api_reconnects_threshold:
            return True, f"API instability: {context.api_reconnect_count} reconnects"
        
        # Check drawdown threshold
        if context.current_drawdown_percent >= context.max_drawdown_threshold_percent:
            return True, f"High drawdown: {context.current_drawdown_percent:.2f}%"
        
        # Check internet stability
        if context.is_internet_unstable:
            return True, "Internet unstable"
        
        # Check consecutive losses
        if context.consecutive_losses >= context.loss_threshold:
            return True, f"Consecutive losses: {context.consecutive_losses}"
        
        return False, "Safe mode not required"
    
    @staticmethod
    def get_safe_mode_actions(reason: str) -> dict:
        """
        Get recommended actions when safe mode is triggered.
        
        Returns:
            dict with actions like position_size_reduction, entry_pause, etc.
        """
        return {
            "reduce_position_size": 0.5,  # 50% of normal size
            "pause_new_entries": True,
            "extend_cooldown": 2.0,  # 2x normal cooldown
            "risk_mode": "defensive",
            "reason": reason,
            "alert_telegram": True,
        }
    
    @staticmethod
    def reset_safe_mode_counters() -> SafeModeContext:
        """Reset safe mode counters after recovery."""
        return SafeModeContext(
            api_reconnect_count=0,
            current_drawdown_percent=0.0,
            is_internet_unstable=False,
            consecutive_losses=0,
        )


# Smart Session Filter
TRADING_SESSIONS = {
    "london": {"start": 8, "end": 17},      # 8 AM - 5 PM UTC
    "new_york": {"start": 13, "end": 21},   # 1 PM - 9 PM UTC
    "asian": {"start": 0, "end": 8},        # Midnight - 8 AM UTC
    "dead_hours": {"start": 21, "end": 24}, # 9 PM - Midnight UTC
}

TRADING_QUALITY_BY_SESSION = {
    "london": {"quality_multiplier": 1.0, "min_score": 75, "can_trade": True},
    "new_york": {"quality_multiplier": 1.0, "min_score": 75, "can_trade": True},
    "london_ny_overlap": {"quality_multiplier": 1.2, "min_score": 70, "can_trade": True},  # Best
    "asian": {"quality_multiplier": 0.8, "min_score": 80, "can_trade": True},
    "dead_hours": {"quality_multiplier": 0.5, "min_score": 90, "can_trade": False},
}


class SmartSessionFilter:
    """Filters trades based on trading session quality."""
    
    @staticmethod
    def get_current_session(hour: int) -> str:
        """Determine current trading session based on UTC hour."""
        # London session
        if TRADING_SESSIONS["london"]["start"] <= hour < TRADING_SESSIONS["london"]["end"]:
            # Check for London-NY overlap
            if TRADING_SESSIONS["new_york"]["start"] <= hour < TRADING_SESSIONS["new_york"]["end"]:
                return "london_ny_overlap"
            return "london"
        
        # New York session
        if TRADING_SESSIONS["new_york"]["start"] <= hour < TRADING_SESSIONS["new_york"]["end"]:
            return "new_york"
        
        # Asian session
        if TRADING_SESSIONS["asian"]["start"] <= hour < TRADING_SESSIONS["asian"]["end"]:
            return "asian"
        
        # Dead hours
        return "dead_hours"
    
    @staticmethod
    def get_session_filter(session: str) -> dict:
        """Get trading quality parameters for the session."""
        return TRADING_QUALITY_BY_SESSION.get(session, TRADING_QUALITY_BY_SESSION["dead_hours"])
    
    @staticmethod
    def should_trade(score: float, session: str) -> tuple[bool, str]:
        """Determine if trading should proceed based on session."""
        session_params = SmartSessionFilter.get_session_filter(session)
        
        if not session_params["can_trade"]:
            return False, f"Trading disabled during {session}"
        
        if score < session_params["min_score"]:
            return False, f"Score {score} below session minimum {session_params['min_score']}"
        
        return True, f"OK to trade in {session} session"


# BTC Market Leader Logic
class BTCMarketLeaderLogic:
    """Adjusts ETH trading based on BTC direction."""
    
    @staticmethod
    def adjust_for_btc_direction(
        symbol: str,
        score: float,
        btc_trend: Literal["bullish", "bearish", "sideways"],
        btc_momentum: float,  # -1 to 1
    ) -> tuple[float, str]:
        """
        Adjust trading score for ETH based on BTC conditions.
        
        Args:
            symbol: Trading symbol (e.g., "ETH/USDT")
            score: Base trading score
            btc_trend: Current BTC trend
            btc_momentum: BTC momentum indicator (-1 to 1)
        
        Returns:
            (adjusted_score, reason)
        """
        if symbol != "ETH/USDT":
            return score, "Not ETH, no adjustment"
        
        # BTC bearish: reduce ETH aggressiveness
        if btc_trend == "bearish":
            adjusted_score = score * 0.85
            return adjusted_score, f"BTC bearish: reduced to {adjusted_score:.1f}"
        
        # BTC bullish: allow momentum entries
        if btc_trend == "bullish":
            if btc_momentum > 0.5:  # Strong momentum
                adjusted_score = score * 1.15
                return adjusted_score, f"BTC strong bullish: boosted to {adjusted_score:.1f}"
            else:
                adjusted_score = score * 1.05
                return adjusted_score, f"BTC bullish: boosted to {adjusted_score:.1f}"
        
        # Sideways: neutral
        return score, "BTC sideways: no adjustment"


# Daily Report Generator
class DailyReportGenerator:
    """Generates daily trading report for Telegram."""
    
    @staticmethod
    def generate_daily_report(
        trades_count: int,
        win_rate: float,
        daily_pnl_percent: float,
        best_setup: str,
        worst_setup: str,
        risk_level: str,
    ) -> str:
        """Generate formatted daily report."""
        emoji = "📊"
        return f"""{emoji} DAILY REPORT

Trades: {trades_count}
Win Rate: {win_rate:.2f}%
PnL: {daily_pnl_percent:+.2f}%
Best Setup: {best_setup}
Worst Setup: {worst_setup}

Risk Level: {risk_level}
"""
