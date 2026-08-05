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

# ── Auto-logged US paper track, S$10k (added 2026-08-05) ───────────────────
# A second paper track that fills itself. Separate from the S$5k run in every
# way that matters: its own capital, its own `paper_trades` rows, its own
# position map. Nothing here writes to `trades`, so /portfolio, /benchmark,
# the A/B/C/D alert path and the shadow run started 2026-07-13 are untouched.
#
# Why it exists: the manual /fill workflow logged ZERO trades in the first
# three weeks of the shadow run, so every downstream report was reading an
# empty table. This track removes the human step from the measurement path.
# /fill still runs, and now serves its real purpose — measuring how far a
# genuine moomoo fill lands from the simulated one.
US10K_ENABLED = True
US10K_TRACK = "us10k"
US10K_PORTFOLIO_SGD = 10000
US10K_VARIANT = "SWING_LOW_NOCAP"   # strategy D — the walk-forward winner
US10K_START = "2026-08-05"

# Backtest expectations for D, used by /algocheck as the comparison baseline.
# Source: research/ walk-forward reports, 2026-07-19 (corrected engine, real
# costs). Pooled per-trade figure is the most robust of the three at low N.
US10K_EXPECT_CAGR = 0.079        # +7.9% CAGR
US10K_EXPECT_MAXDD = -0.076      # -7.6% max drawdown
US10K_EXPECT_PER_TRADE = 0.0196  # +1.96% per trade, pooled over 283 legs

# Sector membership for the MAX_SECTOR_PCT concentration check on BUY alerts.
# Static for the same reason as TICKER_NAMES: no network call on the alert path.
# (analysis.get_sector_etf_status hits yfinance for the sector *trend* filter —
# a different question, and one that can afford to fail.)
# Broad index ETFs are deliberately absent: they aren't a sector, so they're
# skipped, matching how the sector-trend filter already treats them.
TICKER_SECTORS = {
    # ── SGX ──
    "D05": "Banks", "O39": "Banks", "U11": "Banks",
    "A17U": "REITs", "C38U": "REITs", "ME8U": "REITs", "M44U": "REITs",
    "N2IU": "REITs", "AJBU": "REITs", "BUOU": "REITs",
    "9CI": "Real Estate", "C09": "Real Estate", "U14": "Real Estate",
    "S63": "Industrials", "BN4": "Industrials", "U96": "Industrials", "BS6": "Industrials",
    "C6L": "Transport", "C52": "Transport", "S58": "Transport",
    "Z74": "Telco",
    "S68": "Financials",
    "V03": "Technology",
    "F34": "Consumer", "G13": "Consumer", "C07": "Consumer", "Y92": "Consumer",
    # ── US ──
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "INTC": "Technology", "QCOM": "Technology",
    "AVGO": "Technology", "MU": "Technology", "TSM": "Technology",
    "ARM": "Technology", "AMAT": "Technology", "LRCX": "Technology",
    "CRM": "Technology", "ADBE": "Technology", "ORCL": "Technology",
    "NOW": "Technology", "INTU": "Technology", "PANW": "Technology",
    "SNOW": "Technology", "PLTR": "Technology", "DDOG": "Technology",
    "IONQ": "Technology", "QBTS": "Technology", "RGTI": "Technology", "IBM": "Technology",
    "GOOGL": "Communication Services", "META": "Communication Services",
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "LULU": "Consumer Discretionary", "CMG": "Consumer Discretionary",
    "RBLX": "Consumer Discretionary", "F": "Consumer Discretionary",
    "COST": "Consumer Staples", "TGT": "Consumer Staples",
    "JPM": "Financials", "GS": "Financials", "MS": "Financials",
    "BAC": "Financials", "WFC": "Financials", "V": "Financials",
    "MA": "Financials", "AXP": "Financials", "COIN": "Financials",
    "SOFI": "Financials", "HOOD": "Financials",
    "UNH": "Health Care", "JNJ": "Health Care", "LLY": "Health Care",
    "ABBV": "Health Care", "MRK": "Health Care", "AMGN": "Health Care",
    "GILD": "Health Care", "REGN": "Health Care",
    "XOM": "Energy", "CVX": "Energy", "OXY": "Energy", "SLB": "Energy",
    "CAT": "Industrials", "DE": "Industrials", "RTX": "Industrials",
    "HON": "Industrials", "GE": "Industrials", "BA": "Industrials",
}

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
