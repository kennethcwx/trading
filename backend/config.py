PORTFOLIO_SIZE_SGD = 5000
PORTFOLIO_CURRENCY = "SGD"

WATCHLIST = ["AAPL", "MSFT", "NVDA", "BAC"]

# Risk rules
RISK_PER_TRADE_PCT = 0.01    # 1% portfolio risk per trade
MAX_POSITION_PCT = 0.10      # 10% max per position
MAX_SECTOR_PCT = 0.25        # 25% max per sector
MIN_CASH_PCT = 0.10          # 10% min cash reserve
MAX_OPTIONS_PCT = 0.20       # 20% max options exposure

# Technical thresholds
RSI_ENTRY = 40               # Mean-reversion entry trigger
RSI_EXIT = 70                # Overbought exit trigger
SMA_LONG = 200
SMA_SHORT = 50
MOMENTUM_DAYS = 20           # Channel breakout lookback
VOLUME_MULTIPLIER = 1.5      # Min volume ratio for breakout

# Exit rules
PROFIT_RATIO = 2.0           # 2:1 reward-to-risk
TRAILING_TRIGGER = 0.15      # 15% gain triggers trailing stop
TRAILING_STOP_PCT = 0.10     # 10% trailing stop
MAX_HOLD_WEEKS = 8
STOP_ATR_MULT = 1.0          # ATR multiplier for stop loss
STOP_MAX_PCT = 0.08          # Hard max 8% stop

# Options
MIN_IVR = 30
TARGET_DELTA = 0.30
WHEEL_DTE_MIN = 30
WHEEL_DTE_MAX = 45

# Market regime
VIX_CAUTION = 25
VIX_DEFENSIVE = 30

# IBKR
IBKR_HOST = "127.0.0.1"
IBKR_PAPER_PORT = 4002
IBKR_LIVE_PORT = 4001
IBKR_CLIENT_ID = 1
