# ======================================
# TRADING CONFIGURATION
# ======================================

SYMBOLS = ["BTC/USDT", "ETH/USDT"]

# ======================================
# RISK SETTINGS
# ======================================

BASE_TRADE_USDT = 0.50

MAX_PORTFOLIO_EXPOSURE = 0.25
MIN_USDT_RESERVE = 0.70

MAX_DAILY_LOSS_PERCENT = 5

MAX_OPEN_POSITIONS = 2

# ======================================
# TRADE QUALITY SETTINGS (PHASE B)
# ======================================

MIN_SCORE_FOR_TRADE = 82  # Reject trades below this score (stop overtrading)
CONSERVATIVE_MODE = False  # Set to True for fewer but higher quality trades
CONSERVATIVE_MIN_SCORE = 87  # Higher threshold in conservative mode

# ======================================
# STRATEGY SETTINGS
# ======================================

RSI_THRESHOLD = 35

TAKE_PROFIT_PERCENT = 4
STOP_LOSS_PERCENT = 8

TRAILING_STOP_PERCENT = 2

COOLDOWN_MINUTES = 180

# ======================================
# INDICATORS
# ======================================

EMA_FAST = 50
EMA_SLOW = 200

ADX_THRESHOLD = 20

# ======================================
# SAFETY MODE SETTINGS
# ======================================

AUTO_SAFE_MODE_ENABLED = True
MAX_API_RECONNECTS = 3
MAX_DRAWDOWN_THRESHOLD_PERCENT = 3.0
MAX_CONSECUTIVE_LOSSES = 3

# ======================================
# DASHBOARD
# ======================================

REFRESH_SECONDS = 10
