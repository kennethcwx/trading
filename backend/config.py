PORTFOLIO_SIZE_SGD = 5000
PORTFOLIO_CURRENCY = "SGD"

# Crypto swing trades — fixed position size, 24/7 monitoring
# Trimmed to majors 2026-06-21 — 18mo backtest showed majors (BTC/ETH/SOL/XRP/INJ)
# at +1.62% expectancy/trade vs the 13 dropped alts at -2.32% expectancy/trade.
# Small-cap alts (DOT, ARB, APT, HBAR especially) blow through technical stops with
# violent trend-defying drops; the strategy's edge only holds on liquid majors.
CRYPTO_WATCHLIST = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "INJ-USD",
]
CRYPTO_POSITION_SGD = 350   # ~$260 USD per trade

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

# Bull put spreads — separate options sub-account (S$2k / ~$1,480 USD), SPY/QQQ only
SPREAD_UNIVERSE = ["SPY", "QQQ"]
SPREAD_WIDTH = 5.0           # $ between short and long put strikes
SPREAD_ACCOUNT_SGD = 2000    # smaller sub-account dedicated to options income

# Market regime
VIX_CAUTION = 25
VIX_DEFENSIVE = 30

# IBKR
IBKR_HOST = "127.0.0.1"
IBKR_PAPER_PORT = 4002
IBKR_LIVE_PORT = 4001
IBKR_CLIENT_ID = 1

# Futu/moomoo — SGX auto-trading via Futu OpenD
# Bare SGX codes (no .SI suffix). yfinance appends .SI; Futu uses SG.{code}
# Expanded 8→27 liquid STI names 2026-07-13: the strategy's SGX edge only
# appears with breadth (backtest: +6.5% CAGR on 27 names vs +0.3% on 8 with
# the same logic — too few concurrent setups to compound on a narrow list).
SGX_WATCHLIST = [
    "D05", "O39", "U11",                              # banks
    "A17U", "C38U", "ME8U", "M44U", "N2IU", "AJBU", "BUOU",   # REITs
    "S63", "S68", "U96", "BN4", "9CI", "C09",         # industrials / property
    "Z74", "C6L", "F34", "V03", "G13", "C52",         # telco / transport / consumer
    "BS6", "S58", "U14", "C07", "Y92",
]
SGX_PORTFOLIO_SGD = 5000     # paper capital allocation for SGX (cash-only, no margin)

# Display names for alert headers. Static on purpose: no network call at alert
# time, so a yfinance outage or rate-limit can never delay or break a signal.
# Short labels, not legal entity names — these are read on a phone, mid-scan.
# Keys are bare SGX codes (the .SI suffix is stripped at lookup) and US tickers.
# A symbol missing from this map simply renders without a name.
TICKER_NAMES = {
    # ── SGX ──
    "D05": "DBS Group",
    "O39": "OCBC Bank",
    "U11": "UOB",
    "A17U": "CapitaLand Ascendas REIT",
    "C38U": "CapitaLand Integrated",
    "ME8U": "Mapletree Industrial",
    "M44U": "Mapletree Logistics",
    "N2IU": "Mapletree PACT",
    "AJBU": "Keppel DC REIT",
    "BUOU": "Frasers Logistics & Comm",
    "S63": "ST Engineering",
    "S68": "Singapore Exchange",
    "U96": "Sembcorp Industries",
    "BN4": "Keppel",
    "9CI": "CapitaLand Investment",
    "C09": "City Developments",
    "Z74": "Singtel",
    "C6L": "Singapore Airlines",
    "F34": "Wilmar International",
    "V03": "Venture Corp",
    "G13": "Genting Singapore",
    "C52": "ComfortDelGro",
    "BS6": "Yangzijiang Shipbuilding",
    "S58": "SATS",
    "U14": "UOL Group",
    "C07": "Jardine Cycle & Carriage",
    "Y92": "Thai Beverage",
    # ── US: mega-cap ──
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "META": "Meta",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "TSLA": "Tesla",
    # ── US: semiconductors ──
    "AMD": "AMD",
    "INTC": "Intel",
    "QCOM": "Qualcomm",
    "AVGO": "Broadcom",
    "MU": "Micron",
    "TSM": "TSMC",
    "ARM": "Arm Holdings",
    "AMAT": "Applied Materials",
    "LRCX": "Lam Research",
    # ── US: software / cloud ──
    "CRM": "Salesforce",
    "ADBE": "Adobe",
    "ORCL": "Oracle",
    "NOW": "ServiceNow",
    "INTU": "Intuit",
    "PANW": "Palo Alto Networks",
    "SNOW": "Snowflake",
    "PLTR": "Palantir",
    "DDOG": "Datadog",
    # ── US: financials ──
    "JPM": "JPMorgan",
    "GS": "Goldman Sachs",
    "MS": "Morgan Stanley",
    "BAC": "Bank of America",
    "WFC": "Wells Fargo",
    "V": "Visa",
    "MA": "Mastercard",
    "AXP": "American Express",
    "COIN": "Coinbase",
    # ── US: healthcare ──
    "UNH": "UnitedHealth",
    "JNJ": "Johnson & Johnson",
    "LLY": "Eli Lilly",
    "ABBV": "AbbVie",
    "MRK": "Merck",
    "AMGN": "Amgen",
    "GILD": "Gilead",
    "REGN": "Regeneron",
    # ── US: consumer discretionary ──
    "HD": "Home Depot",
    "MCD": "McDonald's",
    "SBUX": "Starbucks",
    "NKE": "Nike",
    "LULU": "Lululemon",
    "CMG": "Chipotle",
    "COST": "Costco",
    "TGT": "Target",
    # ── US: energy ──
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "OXY": "Occidental",
    "SLB": "SLB",
    # ── US: industrials ──
    "CAT": "Caterpillar",
    "DE": "Deere",
    "RTX": "RTX",
    "HON": "Honeywell",
    "GE": "GE Aerospace",
    "BA": "Boeing",
    # ── US: high-IV / momentum ──
    "SOFI": "SoFi",
    "F": "Ford",
    "HOOD": "Robinhood",
    "RBLX": "Roblox",
    # ── US: quantum ──
    "IONQ": "IonQ",
    "QBTS": "D-Wave Quantum",
    "RGTI": "Rigetti Computing",
    "IBM": "IBM",
    # ── US: index ETFs ──
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
    "IWM": "Russell 2000 ETF",
}

# Real long-term SG holdings (bare SGX codes) — monitored independently of the
# paper strategy: holdings block in the SGX Pre-Open, intraday big-move alerts,
# trend-break/overbought warnings, and accumulation hints for adding on dips.
# Prices only — no cost basis is stored anywhere.
SGX_HOLDINGS = ["D05", "O39"]
HOLDINGS_MOVE_ALERT_PCT = 2.5   # intraday move that triggers a holdings alert

# News feed — zero-cost: yfinance headlines only, no Claude/LLM calls.
# A ticker crossing this threshold gets its recent headlines stored once per day.
NEWS_MOVE_THRESHOLD_PCT = 3.0
NEWS_HEADLINES_PER_TICKER = 3
