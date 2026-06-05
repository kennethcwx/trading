PORTFOLIO_SIZE_SGD = 5000
PORTFOLIO_CURRENCY = "SGD"

# Core watchlist — swing trades, technical signals apply
WATCHLIST = ["AAPL", "MSFT", "NVDA", "BAC", "AMD", "CAT", "ABBV", "AMZN"]

# Long-term growth holdings — accumulate on dips, hold for 1–3 years, fractional shares ok
# Options not viable on these (collateral too high for S$5k portfolio)
LONGTERM_WATCHLIST = [
    "NVDA", "MSFT", "AMZN", "META", "GOOGL",   # mega-cap tech
    "TSLA", "AVGO", "TSM",                       # semis + EV
    "LLY", "UNH",                                # healthcare compounders
]

# Quantum computing — long-term positions, trend-driven (IONQ + QBTS priority)
QUANTUM_WATCHLIST = ["IONQ", "QBTS", "RGTI", "IBM"]

# Covered calls — liquid options, elevated IV, momentum-only entries (MU + ARM priority)
COVERED_CALLS_WATCHLIST = ["MU", "ARM", "SOFI", "F", "COIN"]

# Wide screener — 70 liquid, optionable stocks across all sectors
# Scanned daily; only BUY and WATCH setups are surfaced
SCREENER_UNIVERSE = [
    # Mega-cap
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA",
    # Semiconductors
    "AMD", "INTC", "QCOM", "AVGO", "MU", "TSM", "ARM", "AMAT", "LRCX",
    # Software / Cloud
    "CRM", "ADBE", "ORCL", "NOW", "INTU", "PANW", "SNOW", "PLTR", "DDOG",
    # Financials
    "JPM", "GS", "MS", "BAC", "WFC", "V", "MA", "AXP", "COIN",
    # Healthcare
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "AMGN", "GILD", "REGN",
    # Consumer Discretionary
    "HD", "MCD", "SBUX", "NKE", "LULU", "CMG", "COST", "TGT",
    # Energy
    "XOM", "CVX", "OXY", "SLB",
    # Industrials
    "CAT", "DE", "RTX", "HON", "GE", "BA",
    # High-IV / momentum
    "SOFI", "F", "HOOD", "RBLX",
    # Quantum
    "IONQ", "QBTS",
    # Index ETFs
    "SPY", "QQQ", "IWM",
]

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
