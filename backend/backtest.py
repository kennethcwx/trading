"""
backtest.py — Historical signal backtest + strategy comparator

Runs the technical signal logic over historical daily data across a broad
universe. Fundamentals and earnings filters are skipped (current data only,
not historical), so this tests the technical layer in isolation.

Usage:
  python backtest.py                        # 365d, full universe
  python backtest.py --days 180             # shorter window
  python backtest.py --symbols AAPL MSFT    # specific tickers
  python backtest.py --compare              # run all 4 variants head-to-head
  python backtest.py --compare --symbols AAPL MSFT NVDA
"""

import argparse
import sys

import numpy as np
import pandas as pd
import yfinance as yf

# ── Strategy config (mirrors config.py) ──────────────────────────────────────

RSI_ENTRY       = 40
RSI_EXIT        = 70
SMA_LONG        = 200
SMA_SHORT       = 50
MOMENTUM_DAYS   = 20
VOLUME_MULTIPLIER = 1.5
PROFIT_RATIO    = 2.0
STOP_ATR_MULT   = 1.0
STOP_MAX_PCT    = 0.08
MAX_HOLD_WEEKS  = 8
TRAILING_TRIGGER  = 0.15   # activate trailing stop when second half gains 15% from entry
TRAILING_STOP_PCT = 0.10   # trail 10% below peak after activation
PERSISTENT_TRAIL  = False  # --persistent-trail: arm off PEAK gain and stay armed
                           # (live/backtest both de-arm when price falls below +15%)

SLIPPAGE_PCT    = 0.001   # 0.1% per trade (entry + exit)
COMMISSION_PCT  = 0.0005  # 0.05% per trade leg

UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "AMZN", "TSLA", "INTC", "CRM",
    # Finance
    "JPM", "BAC", "GS", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV",
    # Consumer / industrial
    "HD", "WMT", "KO", "DIS", "BA", "CAT",
    # ETFs
    "SPY", "QQQ", "XLK", "XLF",
]

# Stock → sector ETF mapping (mirrors analysis.py get_sector_etf_status).
# ETFs in the universe have no entry — sector filter is skipped for them.
SECTOR_ETF_MAP: dict[str, str] = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "INTC": "XLK", "CRM": "XLK",
    "GOOGL": "XLC", "META": "XLC", "DIS": "XLC",
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY",
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "V": "XLF", "MA": "XLF",
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV", "ABBV": "XLV",
    "WMT": "XLP", "KO": "XLP",
    "BA": "XLI", "CAT": "XLI",
}

# Non-overlapping train/validate split:
#   validate = most recent 6 months (out-of-sample)
#   train    = the 18 months BEFORE the validate period
VALIDATE_DAYS = 182   # ~6 months  — entries in last 182 days
TRAIN_DAYS    = 730   # 24 months  — train entries from day 182 to day 730 ago


# ── Indicators ────────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = (-delta).clip(lower=0)
    ma_up = up.ewm(com=period - 1, min_periods=period).mean()
    ma_dn = dn.ewm(com=period - 1, min_periods=period).mean()
    rs = ma_up / ma_dn.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


# ── Data preparation ──────────────────────────────────────────────────────────

def _prepare(symbol: str, period: str = "3y") -> pd.DataFrame | None:
    try:
        df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    except Exception as e:
        print(f"  {symbol}: fetch error — {e}", file=sys.stderr)
        return None

    if df.empty or len(df) < SMA_LONG + 30:
        return None

    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    df["rsi"]       = _rsi(c)
    df["sma200"]    = c.rolling(SMA_LONG).mean()
    df["sma50"]     = c.rolling(SMA_SHORT).mean()
    df["sma20"]     = c.rolling(20).mean()
    df["atr"]       = _atr(h, l, c)
    # 20 complete prior days, excluding the current bar — matches live
    # analysis.py (volume.iloc[-21:-1].mean())
    df["vol_avg"]   = v.shift(1).rolling(20).mean()
    df["vol_ratio"] = v / df["vol_avg"].replace(0, np.nan)

    # Donchian bands — shift(1) so we only use yesterday's data at entry
    df["don_upper"] = h.shift(1).rolling(MOMENTUM_DAYS).max()  # 20-day high
    df["don_lower"] = l.shift(1).rolling(MOMENTUM_DAYS).min()  # 20-day low
    # 30-bar channel — on 7-day-week assets 20 bars is only ~3 calendar weeks
    df["don_upper30"] = h.shift(1).rolling(30).max()

    # Structural swing low — lowest low ~7-46 days back, excluding the most
    # recent week so the very pullback that triggers entry doesn't define its own stop
    df["swing_low"] = l.shift(6).rolling(40).min()

    # SMA crossover: 1 on the bar where sma20 crosses above sma50
    prev_above = df["sma20"].shift(1) > df["sma50"].shift(1)
    curr_above = df["sma20"] > df["sma50"]
    df["sma_cross_up"] = (~prev_above) & curr_above

    return df.dropna(subset=["sma200", "atr", "don_upper", "don_lower", "swing_low"])


# ── Live-system filters (RS rank + sector ETF) ───────────────────────────────

def _fetch_vix(period: str = "3y") -> pd.Series:
    """Returns VIX close price indexed by date."""
    try:
        df = yf.Ticker("^VIX").history(period=period, auto_adjust=True)
        return df["Close"].dropna() if not df.empty else pd.Series(dtype=float)
    except Exception:
        return pd.Series(dtype=float)


def _fetch_regime_series(symbol: str = "SPY", period: str = "3y") -> pd.Series:
    """Series[bool] — True on dates when the regime index is above its 200 SMA.
    Mirrors the live gate (get_market_regime / get_sgx_regime / get_crypto_regime):
    no new entries while the index is below its own 200-day SMA."""
    try:
        df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    except Exception:
        return pd.Series(dtype=bool)
    if df.empty or len(df) < SMA_LONG:
        return pd.Series(dtype=bool)
    sma = df["Close"].rolling(SMA_LONG).mean()
    return (df["Close"] > sma).dropna()


def _asof_bool(s: pd.Series, ts, default: bool = True) -> bool:
    """Latest value of s at or before ts (handles calendar mismatches)."""
    if s is None or s.empty:
        return default
    i = s.index.searchsorted(ts, side="right") - 1
    return bool(s.iloc[i]) if i >= 0 else default


def _fetch_earnings_blackout(symbols, days: int = 10) -> dict[str, set]:
    """{symbol: set of dates} on which entries are blocked because earnings are
    1–`days` days ahead. Mirrors live signals.py (earnings_warning → WATCH, no
    entry). ETFs and symbols without earnings data get no blackout."""
    from datetime import timedelta
    out: dict[str, set] = {}
    for sym in symbols:
        try:
            ed = yf.Ticker(sym).get_earnings_dates(limit=40)
        except Exception:
            continue
        if ed is None or getattr(ed, "empty", True):
            continue
        blocked = set()
        for ts in ed.index:
            d0 = ts.date()
            for k in range(1, days + 1):
                blocked.add(d0 - timedelta(days=k))
        out[sym] = blocked
    return out


def _fetch_sector_above_200(etfs: set[str], period: str = "3y") -> dict[str, pd.Series]:
    """Returns {etf: Series[bool]} — True on dates when sector ETF is above 200 SMA."""
    out: dict[str, pd.Series] = {}
    for etf in etfs:
        try:
            df = yf.Ticker(etf).history(period=period, auto_adjust=True)
        except Exception:
            continue
        if df.empty or len(df) < SMA_LONG:
            continue
        sma = df["Close"].rolling(SMA_LONG).mean()
        out[etf] = (df["Close"] > sma).dropna()
    return out


def _compute_rs_rank_series(
    prepared_dfs: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """
    Returns (rank_map, rs3m_map):
      rank_map  — {symbol: Series[float 0-100]} percentile RS rank within universe per date
      rs3m_map  — {symbol: Series[float]} raw rs_3m value (stock 63d return minus SPY 63d return)
    Mirrors live signals.py: rank >= 75 passes the RS filter.
    """
    spy_df = prepared_dfs.get("SPY")
    if spy_df is None:
        return {}, {}
    spy_close = spy_df["Close"]

    rs3m: dict[str, pd.Series] = {}
    for sym, df in prepared_dfs.items():
        stock_ret = df["Close"].pct_change(63) * 100
        spy_ret   = spy_close.reindex(df.index, method="ffill").pct_change(63) * 100
        rs3m[sym] = (stock_ret - spy_ret).dropna()

    rs3m_df  = pd.DataFrame(rs3m).sort_index()
    ranks_df = rs3m_df.rank(axis=1, pct=True) * 100
    rank_map = {sym: ranks_df[sym].dropna() for sym in ranks_df.columns}
    rs3m_map = {sym: rs3m[sym] for sym in rs3m}
    return rank_map, rs3m_map


# ── Signal variants ───────────────────────────────────────────────────────────
#
# Each variant returns (signal_type | None, stop_price).
# signal_type is used to tag trades; None means no entry.

def _entry_baseline(row) -> tuple[str | None, float]:
    """RSI mean-reversion OR Donchian upper-band breakout + volume."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and RSI_ENTRY < rsi_val < RSI_EXIT
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_sma_cross(row) -> tuple[str | None, float]:
    """SMA crossover (20 crosses above 50) as the momentum trigger instead of Donchian breakout.
    Mean-reversion leg kept the same."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = bool(row["sma_cross_up"]) and RSI_ENTRY < rsi_val < RSI_EXIT

    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "SMA_CROSS", stop
    return None, 0.0


def _entry_donchian_stop(row) -> tuple[str | None, float]:
    """Same entries as BASELINE but stop placed at Donchian lower band instead of ATR."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val   = float(row["rsi"])
    don_lower = float(row["don_lower"])
    stop      = max(don_lower, price * (1 - STOP_MAX_PCT))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and RSI_ENTRY < rsi_val < RSI_EXIT
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_combined(row) -> tuple[str | None, float]:
    """BASELINE momentum must ALSO have sma20 > sma50 (trend confirmation).
    Mean-reversion leg kept the same."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val   = float(row["rsi"])
    atr_val   = float(row["atr"])
    stop      = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))
    sma_align = float(row["sma20"]) > float(row["sma50"])

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and RSI_ENTRY < rsi_val < RSI_EXIT
        and sma_align
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM_CONFIRMED", stop
    return None, 0.0


def _entry_swing_low(row) -> tuple[str | None, float]:
    """Same entries as BASELINE but stop placed below the prior structural swing low
    (40-day low, excluding the most recent week) instead of a pure ATR multiple —
    aims to give the trade room through the pullback that triggered entry, rather
    than getting stopped out by the same noise that created the setup."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val    = float(row["rsi"])
    atr_val    = float(row["atr"])
    swing_low  = float(row["swing_low"])
    raw_stop   = swing_low - 0.5 * atr_val
    stop       = max(raw_stop, price * (1 - STOP_MAX_PCT))   # cap max loss at 8%
    stop       = min(stop, price * (1 - 0.02))                # ensure at least 2% breathing room

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and RSI_ENTRY < rsi_val < RSI_EXIT
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_mean_rev_only(row) -> tuple[str | None, float]:
    """Mean-reversion only — RSI < 40 + above 200 SMA. No momentum leg at all."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))
    if rsi_val < RSI_ENTRY:
        return "MEAN_REV", stop
    return None, 0.0


def _entry_mean_rev_sma50(row) -> tuple[str | None, float]:
    """Mean-reversion only, but requires price above SMA50 (stronger trend filter)."""
    price = float(row["Close"])
    if price <= row["sma200"] or price <= row["sma50"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))
    if rsi_val < RSI_ENTRY:
        return "MEAN_REV", stop
    return None, 0.0


def _entry_mean_rev_rsi35(row) -> tuple[str | None, float]:
    """Mean-reversion only, but requires RSI < 35 (deeper pullbacks only)."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))
    if rsi_val < 35:
        return "MEAN_REV", stop
    return None, 0.0


def _entry_no_rsi_cap(row) -> tuple[str | None, float]:
    """BASELINE but momentum has no RSI < 70 ceiling — tests whether excluding
    the strongest breakouts (which often print RSI > 70) costs performance."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and rsi_val > RSI_ENTRY
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_swing_low_no_cap(row) -> tuple[str | None, float]:
    """SWING_LOW stops + no RSI ceiling on momentum — combines the two best ideas."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val    = float(row["rsi"])
    atr_val    = float(row["atr"])
    swing_low  = float(row["swing_low"])
    raw_stop   = swing_low - 0.5 * atr_val
    stop       = max(raw_stop, price * (1 - STOP_MAX_PCT))
    stop       = min(stop, price * (1 - 0.02))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and rsi_val > RSI_ENTRY
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_nocap_atr2_cap15(row) -> tuple[str | None, float]:
    """NO_RSI_CAP entries with crypto-sized stops: 2×ATR, capped at 15%.
    The live 1×ATR/8% geometry sits at noise level on crypto daily vol —
    majors routinely swing 4–6% ATR, so stops get run before the thesis plays."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - 2.0 * atr_val, price * (1 - 0.15))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and rsi_val > RSI_ENTRY
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_swnc_cap15(row) -> tuple[str | None, float]:
    """SWING_LOW_NOCAP entries but with the stop cap widened to 15% and the
    floor to 3% — structural stop with room for crypto-scale volatility."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val   = float(row["rsi"])
    atr_val   = float(row["atr"])
    swing_low = float(row["swing_low"])
    stop = max(swing_low - 0.5 * atr_val, price * (1 - 0.15))
    stop = min(stop, price * (1 - 0.03))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and rsi_val > RSI_ENTRY
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_base_rsi45(row) -> tuple[str | None, float]:
    """BASELINE but mean-rev triggers at RSI < 45 — crypto pullbacks are fast
    and often don't reach the stock-tuned RSI 40 before resuming."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))

    mean_rev = rsi_val < 45
    momentum = (
        price >= row["don_upper"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and 45 < rsi_val < RSI_EXIT
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _entry_base_don30(row) -> tuple[str | None, float]:
    """BASELINE but momentum breaks a 30-bar channel (~1 month on 7-day-week
    assets, matching the stock strategy's calendar horizon)."""
    price = float(row["Close"])
    if price <= row["sma200"]:
        return None, 0.0
    rsi_val = float(row["rsi"])
    atr_val = float(row["atr"])
    stop    = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))

    mean_rev = rsi_val < RSI_ENTRY
    momentum = (
        price >= row["don_upper30"]
        and float(row["vol_ratio"]) >= VOLUME_MULTIPLIER
        and RSI_ENTRY < rsi_val < RSI_EXIT
    )
    if mean_rev:
        return "MEAN_REV", stop
    if momentum:
        return "MOMENTUM", stop
    return None, 0.0


def _mk_volconf(base_fn: callable, vol_min: float) -> callable:
    """Wrap a variant: MEAN_REV entries additionally require vol_ratio >= vol_min
    (volume confirmation on the pullback day). Momentum legs already have their
    own volume gate and are passed through untouched."""
    def fn(row) -> tuple[str | None, float]:
        sig, stop = base_fn(row)
        if sig == "MEAN_REV":
            vr = float(row["vol_ratio"]) if not pd.isna(row["vol_ratio"]) else 0.0
            if vr < vol_min:
                return None, 0.0
        return sig, stop
    return fn


VARIANTS: dict[str, callable] = {
    "BASELINE":        _entry_baseline,
    "SMA_CROSS":       _entry_sma_cross,
    "DONCHIAN_STOP":   _entry_donchian_stop,
    "COMBINED":        _entry_combined,
    "SWING_LOW":       _entry_swing_low,
    "NO_RSI_CAP":      _entry_no_rsi_cap,
    "SWING_LOW_NOCAP": _entry_swing_low_no_cap,
    "MEAN_REV_ONLY":   _entry_mean_rev_only,
    "MEAN_REV_SMA50":  _entry_mean_rev_sma50,
    "MEAN_REV_RSI35":  _entry_mean_rev_rsi35,
    # Volume confirmation on the mean-rev leg (KIV #3) — applied to the live
    # shadow variant (D) and to pure mean-rev (B/C proxy)
    "SWNC_MRVOL12":    _mk_volconf(_entry_swing_low_no_cap, 1.2),
    "SWNC_MRVOL15":    _mk_volconf(_entry_swing_low_no_cap, 1.5),
    "MR_ONLY_VOL12":   _mk_volconf(_entry_mean_rev_only, 1.2),
    "MR_ONLY_VOL15":   _mk_volconf(_entry_mean_rev_only, 1.5),
    # Crypto-scale stop geometry (KIV: 1×ATR/8% cap is noise-level on crypto)
    "NOCAP_ATR2_C15":  _entry_nocap_atr2_cap15,
    "SWNC_C15":        _entry_swnc_cap15,
    # Crypto parameter probes (2026-07-13 round 2)
    "BASE_RSI45":      _entry_base_rsi45,
    "BASE_DON30":      _entry_base_don30,
}


# ── Backtest per symbol ───────────────────────────────────────────────────────

def _apply_costs(pnl_pct: float) -> float:
    """Subtract slippage and commission (applied at both entry and exit)."""
    cost = (SLIPPAGE_PCT + COMMISSION_PCT) * 2 * 100  # convert to %
    return pnl_pct - cost


def _run(symbol: str, df: pd.DataFrame, entry_fn: callable,
         entry_start_days: int = 0, entry_end_days: int = 365,
         sector_above_200: pd.Series | None = None,
         rs_rank_series: pd.Series | None = None,
         rs3m_series: pd.Series | None = None,
         vix_series: pd.Series | None = None,
         regime_series: pd.Series | None = None,
         earnings_blocked: set | None = None,
         diagnose: bool = False) -> list[dict]:
    """Run backtest for a single symbol.

    Entry window is [today - entry_end_days, today - entry_start_days], so:
      - validate (last 6m):  entry_start_days=0,   entry_end_days=182
      - train   (prior 18m): entry_start_days=182,  entry_end_days=730
    Exits can happen on any date once a position is open.
    """
    latest = df.index[-1]
    entry_open  = latest - pd.Timedelta(days=entry_end_days)   # earliest allowed entry
    entry_close = latest - pd.Timedelta(days=entry_start_days)  # latest allowed entry
    trades: list[dict] = []
    pos = None

    # No extra warmup skip here: _prepare already drops all rows with unformed
    # indicators (sma200/atr/donchian/swing). Skipping another SMA_LONG rows of
    # VALID data silently starved the oldest walk-forward windows of entries.
    for date, row in df.iterrows():
        price    = float(row["Close"])
        rsi_val  = float(row["rsi"])
        above200 = price > float(row["sma200"])

        # ── EXIT ─────────────────────────────────────────────────────────────
        if pos:
            ep    = pos["entry_price"]
            stop  = pos["stop"]
            tgt   = pos["target"]
            weeks = (date - pos["entry_date"]).days / 7
            low_px  = float(row["Low"])
            high_px = float(row["High"])
            open_px = float(row["Open"])

            def _record(reason: str, exit_px: float, frac: float):
                raw_pnl = (exit_px - ep) / ep * 100
                trade = {
                    "symbol":      symbol,
                    "entry_date":  pos["entry_date"].date(),
                    "exit_date":   date.date(),
                    "entry_price": round(ep, 2),
                    "exit_price":  round(exit_px, 2),
                    "pnl_pct":     round(_apply_costs(raw_pnl), 2),
                    "pnl_raw":     round(raw_pnl, 2),
                    "reason":      reason,
                    "signal_type": pos["signal_type"],
                    "days_held":   (date - pos["entry_date"]).days,
                    "frac":        frac,
                }
                if diagnose:
                    trade["blocked_by"]    = pos.get("blocked_by")
                    trade["rs_rank_val"]   = pos.get("rs_rank_val")
                    trade["rs3m_val"]      = pos.get("rs3m_val")
                    trade["vix_at_entry"]  = pos.get("vix_at_entry")
                trades.append(trade)

            # Stops fill intraday, not at the close: breach when the Low touches
            # the stop; a gap-down opens below it and fills at the open.
            if low_px <= stop:
                _record("stop_hit", min(open_px, stop), pos["shares"])
                pos = None
            elif not above200:
                _record("trend_break", price, pos["shares"])
                pos = None
            elif not pos["sold_half"]:
                # rsi_half requires an open gain — matches live signals.py
                if rsi_val > RSI_EXIT and price > ep:
                    _record("rsi_half", price, 0.5)
                    pos["shares"]    = 0.5
                    pos["sold_half"] = True
                    pos["stop"]      = ep   # move stop to breakeven
                elif high_px >= tgt:
                    # Limit sell at target: gap-up above it fills at the open
                    _record("target_half", max(open_px, tgt), 0.5)
                    pos["shares"]    = 0.5
                    pos["sold_half"] = True
                    pos["stop"]      = ep
                elif weeks >= MAX_HOLD_WEEKS and (price - ep) / ep * 100 < 5:
                    _record("time_stop", price, pos["shares"])
                    pos = None
            else:
                # Second half — track peak and apply trailing stop once 15% gain
                pos["peak"] = max(pos.get("peak", ep), price)
                gain = (price - ep) / ep
                arm_gain = (pos["peak"] - ep) / ep if PERSISTENT_TRAIL else gain
                if arm_gain >= TRAILING_TRIGGER:
                    trail_stop = pos["peak"] * (1 - TRAILING_STOP_PCT)
                    if low_px <= trail_stop:
                        _record("trail_stop", min(open_px, trail_stop), pos["shares"])
                        pos = None
                if pos is not None and weeks >= MAX_HOLD_WEEKS and gain * 100 < 5:
                    _record("time_stop", price, pos["shares"])
                    pos = None

            continue

        # ── ENTRY (within the entry window only) ─────────────────────────────
        if not (entry_open <= date <= entry_close):
            continue

        # Market regime gate — live blocks ALL entries when the regime index is
        # below its 200 SMA (hard gate, applies even in diagnose mode)
        if regime_series is not None and not _asof_bool(regime_series, date):
            continue

        # Earnings blackout — live emits WATCH (no entry) within 10d of earnings
        if earnings_blocked and date.date() in earnings_blocked:
            continue

        # Evaluate filters
        blocked_rs = (
            rs_rank_series is not None
            and date in rs_rank_series.index
            and float(rs_rank_series[date]) < 75
        )
        blocked_sector = (
            sector_above_200 is not None
            and date in sector_above_200.index
            and not bool(sector_above_200[date])
        )

        if not diagnose:
            if blocked_rs or blocked_sector:
                continue

        sig, stop = entry_fn(row)
        if sig:
            risk = price - stop
            if risk <= 0:
                continue
            block_tag = (
                "both"    if blocked_rs and blocked_sector else
                "rs_rank" if blocked_rs else
                "sector"  if blocked_sector else
                None
            )
            # Find nearest VIX date (VIX may not trade on same calendar as stock)
            vix_at_entry = None
            if vix_series is not None and not vix_series.empty:
                idx = vix_series.index.searchsorted(date)
                if idx < len(vix_series):
                    vix_at_entry = float(vix_series.iloc[idx])
                elif len(vix_series):
                    vix_at_entry = float(vix_series.iloc[-1])

            pos = {
                "entry_price":  price,
                "entry_date":   date,
                "stop":         stop,
                "target":       price + PROFIT_RATIO * risk,
                "shares":       1.0,
                "sold_half":    False,
                "peak":         price,
                "signal_type":  sig,
                "blocked_by":   block_tag,
                "rs_rank_val":  float(rs_rank_series[date]) if rs_rank_series is not None and date in rs_rank_series.index else None,
                "rs3m_val":     float(rs3m_series[date])    if rs3m_series    is not None and date in rs3m_series.index    else None,
                "vix_at_entry": vix_at_entry,
            }

    if pos:
        last    = df.iloc[-1]
        last_px = float(last["Close"])
        last_dt = df.index[-1]
        raw_pnl = (last_px - pos["entry_price"]) / pos["entry_price"] * 100
        trade = {
            "symbol":      symbol,
            "entry_date":  pos["entry_date"].date(),
            "exit_date":   last_dt.date(),
            "entry_price": round(pos["entry_price"], 2),
            "exit_price":  round(last_px, 2),
            "pnl_pct":     round(_apply_costs(raw_pnl), 2),
            "pnl_raw":     round(raw_pnl, 2),
            "reason":      "open_mtm",
            "signal_type": pos["signal_type"],
            "days_held":   (last_dt - pos["entry_date"]).days,
            "frac":        pos["shares"],
        }
        if diagnose:
            trade["blocked_by"]   = pos.get("blocked_by")
            trade["rs_rank_val"]  = pos.get("rs_rank_val")
            trade["rs3m_val"]     = pos.get("rs3m_val")
            trade["vix_at_entry"] = pos.get("vix_at_entry")
        trades.append(trade)

    return trades


# ── Metrics helper ────────────────────────────────────────────────────────────

def _metrics(trades: list[dict], label: str = "", phase: str = "") -> dict:
    df     = pd.DataFrame(trades) if trades else pd.DataFrame()
    closed = df[df["reason"] != "open_mtm"] if not df.empty else pd.DataFrame()

    if closed.empty:
        return {"label": label, "phase": phase, "legs": 0}

    winners  = closed[closed["pnl_pct"] > 0]
    losers   = closed[closed["pnl_pct"] <= 0]
    win_rate = len(winners) / len(closed) * 100
    avg_w    = winners["pnl_pct"].mean() if len(winners) else 0.0
    avg_l    = losers["pnl_pct"].mean()  if len(losers)  else 0.0
    # Capital-weighted expectancy: a half-exit (frac 0.5) counts half a full
    # stop-out (frac 1.0). Unweighted per-leg averaging overweights variants
    # that scale out often — winners produce two 0.5 legs, losers one 1.0 leg.
    w   = closed["frac"] if "frac" in closed.columns else pd.Series(1.0, index=closed.index)
    exp = float((closed["pnl_pct"] * w).sum() / w.sum()) if float(w.sum()) else 0.0

    return {
        "label":    label,
        "phase":    phase,
        "legs":     len(closed),
        "open":     len(df) - len(closed),
        "win_rate": round(win_rate, 1),
        "avg_win":  round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "exp":      round(exp, 2),
        "avg_days": round(closed["days_held"].mean(), 0),
        "best":     round(closed["pnl_pct"].max(), 1),
        "worst":    round(closed["pnl_pct"].min(), 1),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _report_single(all_trades: list[dict], lookback: int):
    if not all_trades:
        print("\nNo trades found in this period.")
        return

    df     = pd.DataFrame(all_trades)
    closed = df[df["reason"] != "open_mtm"].copy()
    open_p = df[df["reason"] == "open_mtm"].copy()

    W = 62
    print(f"\n{'='*W}")
    print(f"  BACKTEST RESULTS -- {lookback}d lookback")
    print(f"{'='*W}")
    print(f"  Total trade legs : {len(df)}  ({len(closed)} closed, {len(open_p)} still open)")

    if len(closed):
        m = _metrics(all_trades)
        print(f"\n  {'Closed trades':-<40}")
        print(f"  Win rate         : {m['win_rate']:.0f}%  ({int(len(closed)*m['win_rate']/100)}W / {int(len(closed)*(1-m['win_rate']/100))}L)")
        print(f"  Avg winner       : +{m['avg_win']:.1f}%")
        print(f"  Avg loser        :  {m['avg_loss']:.1f}%")
        print(f"  Expectancy/trade :  {m['exp']:+.2f}%  (after slippage + commission)")
        print(f"  Avg days held    : {m['avg_days']:.0f}d")
        print(f"  Best leg         : {closed.loc[closed['pnl_pct'].idxmax(), 'symbol']}  {m['best']:+.1f}%")
        print(f"  Worst leg        : {closed.loc[closed['pnl_pct'].idxmin(), 'symbol']}  {m['worst']:+.1f}%")

        print(f"\n  {'By signal type':-<40}")
        for stype, g in closed.groupby("signal_type"):
            gw = g[g["pnl_pct"] > 0]
            wr = len(gw) / len(g) * 100
            print(f"  {stype:<22} {len(g):>3} legs   WR {wr:.0f}%   avg {g['pnl_pct'].mean():+.1f}%")

        print(f"\n  {'By exit reason':-<40}")
        for reason, g in closed.groupby("reason"):
            print(f"  {reason:<22} {len(g):>3} legs   avg {g['pnl_pct'].mean():+.1f}%")

        print(f"\n  {'Per-symbol summary':-<40}")
        print(f"  {'Symbol':<7} {'Legs':>4} {'WR':>5} {'Avg':>7} {'Best':>7} {'Worst':>7}")
        print(f"  {'-'*44}")
        by_sym = (
            closed.groupby("symbol")
            .agg(legs=("pnl_pct","count"),
                 wr=("pnl_pct", lambda x: (x>0).mean()*100),
                 avg=("pnl_pct","mean"),
                 best=("pnl_pct","max"),
                 worst=("pnl_pct","min"))
            .sort_values("avg", ascending=False)
        )
        for sym, r in by_sym.iterrows():
            print(f"  {sym:<7} {r['legs']:>4.0f} {r['wr']:>4.0f}% {r['avg']:>+6.1f}% {r['best']:>+6.1f}% {r['worst']:>+6.1f}%")

    if len(open_p):
        print(f"\n  {'Open positions (mark-to-market)':-<40}")
        for _, r in open_p.sort_values("entry_date").iterrows():
            print(f"  {r['symbol']:<6}  in {r['entry_date']}  "
                  f"${r['entry_price']:.2f} -> ${r['exit_price']:.2f}  {r['pnl_pct']:+.1f}%  ({r['days_held']}d)")

    print(f"\n{'='*W}\n")


def _report_compare(results: dict[str, dict[str, list[dict]]]):
    """
    results = { variant_name: { "train": [...trades], "validate": [...trades] } }
    Prints a summary table for each phase, then calls out the winner.
    """
    W = 78
    print(f"\n{'='*W}")
    print(f"  STRATEGY COMPARISON  (slippage {SLIPPAGE_PCT*100:.1f}% + commission {COMMISSION_PCT*100:.2f}% per leg)")
    print(f"  Train ~18m (days {VALIDATE_DAYS}–{TRAIN_DAYS} ago)  |  Validate ~6m (last {VALIDATE_DAYS}d, out-of-sample)")
    print(f"{'='*W}")

    header = f"  {'Variant':<18} {'Legs':>5} {'WR%':>6} {'AvgW%':>7} {'AvgL%':>7} {'Exp%':>7} {'AvgDays':>8}"
    sep    = f"  {'-'*72}"

    for phase in ("train", "validate"):
        label = "TRAIN (in-sample)" if phase == "train" else "VALIDATE (out-of-sample) << use this to pick winner"
        print(f"\n  {label}")
        print(header)
        print(sep)

        phase_metrics = []
        for variant, phases in results.items():
            m = _metrics(phases[phase], label=variant, phase=phase)
            phase_metrics.append(m)
            if m["legs"] == 0:
                print(f"  {variant:<18} {'—':>5}  no closed trades")
            else:
                print(f"  {variant:<18} {m['legs']:>5} {m['win_rate']:>5.0f}% "
                      f"{m['avg_win']:>+6.1f}% {m['avg_loss']:>+6.1f}% "
                      f"{m['exp']:>+6.2f}% {m['avg_days']:>7.0f}d")

        # Pick winner by expectancy on validate phase
        if phase == "validate":
            valid = [m for m in phase_metrics if m.get("legs", 0) >= 5]
            if valid:
                winner = max(valid, key=lambda m: m["exp"])
                print(f"\n  Winner (highest expectancy, >=5 trades): {winner['label']}  "
                      f"exp {winner['exp']:+.2f}%/trade")

    print(f"\n{'='*W}\n")


# ── Walk-forward validation ──────────────────────────────────────────────────
#
# The single train/validate split picks a "winner" from one 6-month window —
# far too regime-dependent. Walk-forward runs every variant over N consecutive
# non-overlapping 6-month entry windows (oldest→newest) and judges variants on
# pooled expectancy AND consistency (how many windows were positive).

WF_WINDOW_DAYS = 182


def _run_walkforward(prepared_dfs: dict[str, pd.DataFrame], n_windows: int,
                     filters_fn, regime_series: pd.Series | None = None) -> dict[str, list[dict]]:
    """Returns {variant: [ {window metrics + trades}, ... ]} oldest window first."""
    results: dict[str, list[dict]] = {v: [] for v in VARIANTS}
    for w in range(n_windows - 1, -1, -1):      # oldest first
        start_days = w * WF_WINDOW_DAYS
        end_days   = (w + 1) * WF_WINDOW_DAYS
        for vname, entry_fn in VARIANTS.items():
            trades: list[dict] = []
            for sym, df in prepared_dfs.items():
                sector_s, rs_s, rs3m_s, _vix, earn_blk = filters_fn(sym)
                trades.extend(_run(sym, df, entry_fn,
                                   entry_start_days=start_days, entry_end_days=end_days,
                                   sector_above_200=sector_s, rs_rank_series=rs_s,
                                   rs3m_series=rs3m_s, regime_series=regime_series,
                                   earnings_blocked=earn_blk))
            m = _metrics(trades, label=vname)
            m["window"] = f"-{end_days}d…-{start_days}d"
            results[vname].append(m)
    return results


def _report_walkforward(results: dict[str, list[dict]], n_windows: int):
    W = 100
    print(f"\n{'='*W}")
    print(f"  WALK-FORWARD VALIDATION — {n_windows} consecutive {WF_WINDOW_DAYS}-day entry windows (oldest -> newest)")
    print(f"  Cell = expectancy%/trade (closed legs). Judge on POOLED expectancy + consistency, not one window.")
    print(f"{'='*W}")

    win_labels = [m["window"] for m in next(iter(results.values()))]
    header = f"  {'Variant':<17}" + "".join(f"{lbl:>15}" for lbl in win_labels) \
             + f"{'Pooled':>10}{'Legs':>6}{'Win.wins':>9}"
    print(header)
    print(f"  {'-'*(len(header)-2)}")

    summary = []
    for vname, windows in results.items():
        cells = []
        total_legs = 0
        pos_windows = 0
        weighted = 0.0
        for m in windows:
            legs = m.get("legs", 0)
            if legs == 0:
                cells.append(f"{'—':>15}")
                continue
            exp = m["exp"]
            total_legs += legs
            weighted += exp * legs
            if exp > 0:
                pos_windows += 1
            cells.append(f"{exp:>+9.2f} ({legs:>2})")
        pooled = weighted / total_legs if total_legs else None
        summary.append({"variant": vname, "pooled": pooled, "legs": total_legs,
                        "pos": pos_windows, "windows_traded": sum(1 for m in windows if m.get('legs', 0) > 0)})
        pooled_s = f"{pooled:>+9.2f}%" if pooled is not None else f"{'—':>10}"
        print(f"  {vname:<17}" + "".join(cells) + pooled_s
              + f"{total_legs:>6}" + f"{pos_windows:>5}/{summary[-1]['windows_traded']}")

    qualified = [s for s in summary if s["pooled"] is not None and s["legs"] >= 20]
    print(f"\n  Verdict (needs >=20 pooled legs):")
    if not qualified:
        print("  No variant has enough trades to qualify.")
    else:
        for s in sorted(qualified, key=lambda x: x["pooled"], reverse=True)[:3]:
            consistency = f"{s['pos']}/{s['windows_traded']} windows positive"
            print(f"    {s['variant']:<17} pooled {s['pooled']:+.2f}%/trade over {s['legs']} legs — {consistency}")
        best = max(qualified, key=lambda x: x["pooled"])
        steady = [s for s in qualified if s["windows_traded"] and s["pos"] / s["windows_traded"] >= 0.75]
        if steady:
            most_consistent = max(steady, key=lambda x: (x["pos"] / x["windows_traded"], x["pooled"]))
            if most_consistent["variant"] != best["variant"]:
                print(f"    NOTE: {best['variant']} has the best pooled number, but "
                      f"{most_consistent['variant']} is more consistent across regimes.")
    print(f"\n{'='*W}\n")


# ── Portfolio simulation ─────────────────────────────────────────────────────
#
# Per-trade expectancy ignores capital: with S$5k, 1% risk and a 10% position
# cap, many signals can't be taken. This simulates the actual account day by
# day — shared cash pool, risk-based sizing, cash floor — and reports the
# equity curve, CAGR and max drawdown per variant.

PF_START_SGD   = 5000
PF_SGD_TO_USD  = 0.74     # fixed FX — SGD/USD drift is noise at this scale
PF_RISK_PCT    = 0.01     # risk 1% of current equity per trade
PF_MAX_POS_PCT = 0.10     # max 10% of equity per position
PF_MIN_CASH_PCT = 0.10    # keep 10% of equity in cash
PF_COST = SLIPPAGE_PCT + COMMISSION_PCT   # applied to each fill


def _run_portfolio(prepared_dfs: dict[str, pd.DataFrame], entry_fn, filters_fn,
                   regime_series: pd.Series | None = None) -> dict:
    start_usd = PF_START_SGD * PF_SGD_TO_USD
    cash = start_usd
    positions: dict[str, dict] = {}
    closed: list[dict] = []
    skipped_capital = 0
    exposure_days = 0

    # Union trading calendar — _prepare already dropped all warmup rows, so
    # every remaining row has formed indicators (no extra SMA_LONG skip)
    calendars = {s: df.index for s, df in prepared_dfs.items()}
    all_dates = sorted(set().union(*[set(idx) for idx in calendars.values()]))

    equity_curve: list[tuple] = []

    def _close(sym, pos, exit_px, frac, reason, date):
        nonlocal cash
        fill = exit_px * (1 - PF_COST)
        qty = pos["shares"] * frac
        cash += qty * fill
        closed.append({
            "symbol": sym, "reason": reason,
            "pnl_pct": (fill - pos["fill_price"]) / pos["fill_price"] * 100,
            "pnl_usd": qty * (fill - pos["fill_price"]),
            "days_held": (date - pos["entry_date"]).days,
        })

    for date in all_dates:
        # mark-to-market equity (yesterday's close basis is fine intraday)
        mtm = sum(
            pos["shares"] * float(prepared_dfs[s].loc[date, "Close"])
            if date in calendars[s] else pos["shares"] * pos["last_close"]
            for s, pos in positions.items()
        )
        equity = cash + mtm

        # ── exits first (mirrors _run) ──
        for sym in list(positions.keys()):
            if date not in calendars[sym]:
                continue
            row = prepared_dfs[sym].loc[date]
            pos = positions[sym]
            pos["last_close"] = float(row["Close"])
            price   = float(row["Close"])
            low_px  = float(row["Low"])
            high_px = float(row["High"])
            open_px = float(row["Open"])
            rsi_val = float(row["rsi"])
            above200 = price > float(row["sma200"])
            ep, stop, tgt = pos["entry_price"], pos["stop"], pos["target"]
            weeks = (date - pos["entry_date"]).days / 7

            if low_px <= stop:
                _close(sym, pos, min(open_px, stop), 1.0, "stop_hit", date)
                del positions[sym]
            elif not above200:
                _close(sym, pos, price, 1.0, "trend_break", date)
                del positions[sym]
            elif not pos["sold_half"]:
                if rsi_val > RSI_EXIT and price > ep:
                    _close(sym, pos, price, 0.5, "rsi_half", date)
                    pos["shares"] *= 0.5; pos["sold_half"] = True; pos["stop"] = ep
                elif high_px >= tgt:
                    _close(sym, pos, max(open_px, tgt), 0.5, "target_half", date)
                    pos["shares"] *= 0.5; pos["sold_half"] = True; pos["stop"] = ep
                elif weeks >= MAX_HOLD_WEEKS and (price - ep) / ep * 100 < 5:
                    _close(sym, pos, price, 1.0, "time_stop", date)
                    del positions[sym]
            else:
                pos["peak"] = max(pos["peak"], price)
                gain = (price - ep) / ep
                arm_gain = (pos["peak"] - ep) / ep if PERSISTENT_TRAIL else gain
                if arm_gain >= TRAILING_TRIGGER and low_px <= pos["peak"] * (1 - TRAILING_STOP_PCT):
                    _close(sym, pos, min(open_px, pos["peak"] * (1 - TRAILING_STOP_PCT)), 1.0, "trail_stop", date)
                    del positions[sym]
                elif weeks >= MAX_HOLD_WEEKS and gain * 100 < 5:
                    _close(sym, pos, price, 1.0, "time_stop", date)
                    del positions[sym]

        # ── entries: collect today's candidates, best RS rank first ──
        candidates = []
        regime_ok_today = regime_series is None or _asof_bool(regime_series, date)
        for sym, df in prepared_dfs.items():
            if not regime_ok_today:
                break
            if sym in positions or date not in calendars[sym]:
                continue
            sector_s, rs_s, _rs3m, _vix, earn_blk = filters_fn(sym)
            if earn_blk and date.date() in earn_blk:
                continue
            if rs_s is not None and date in rs_s.index and float(rs_s[date]) < 75:
                continue
            if sector_s is not None and date in sector_s.index and not bool(sector_s[date]):
                continue
            row = df.loc[date]
            sig, stop = entry_fn(row)
            if sig and float(row["Close"]) - stop > 0:
                rank = float(rs_s[date]) if rs_s is not None and date in rs_s.index else 0.0
                candidates.append((rank, sym, row, sig, stop))

        for rank, sym, row, sig, stop in sorted(candidates, key=lambda c: -c[0]):
            price = float(row["Close"])
            equity = cash + sum(
                p["shares"] * p["last_close"] for p in positions.values()
            )
            risk_usd = equity * PF_RISK_PCT
            shares = risk_usd / (price - stop)
            pos_value = min(shares * price, equity * PF_MAX_POS_PCT)
            cash_floor = equity * PF_MIN_CASH_PCT
            if cash - pos_value < cash_floor:
                pos_value = cash - cash_floor
            if pos_value < price * 0.05 or pos_value <= 0:   # too small to matter
                skipped_capital += 1
                continue
            shares = pos_value / price
            fill = price * (1 + PF_COST)
            cash -= shares * fill
            positions[sym] = {
                "shares": shares, "entry_price": price, "fill_price": fill,
                "stop": stop, "target": price + PROFIT_RATIO * (price - stop),
                "sold_half": False, "peak": price, "entry_date": date,
                "last_close": price,
            }

        if positions:
            exposure_days += 1
        mtm = sum(p["shares"] * p["last_close"] for p in positions.values())
        equity_curve.append((date, cash + mtm))

    # liquidate remainder at last close
    for sym, pos in list(positions.items()):
        _close(sym, pos, pos["last_close"], 1.0, "open_mtm", all_dates[-1])

    eq = pd.Series({d: v for d, v in equity_curve})
    peak = eq.cummax()
    max_dd = float(((eq - peak) / peak).min()) * 100
    end = float(eq.iloc[-1])
    years = (all_dates[-1] - all_dates[0]).days / 365.25
    cagr = ((end / start_usd) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    wins = [t for t in closed if t["pnl_usd"] > 0]

    return {
        "end_equity_sgd": end / PF_SGD_TO_USD,
        "total_return": (end / start_usd - 1) * 100,
        "cagr": cagr,
        "max_dd": max_dd,
        "trades": len(closed),
        "win_rate": len(wins) / len(closed) * 100 if closed else 0.0,
        "skipped_capital": skipped_capital,
        "exposure_pct": exposure_days / len(all_dates) * 100,
    }


def _report_portfolio(results: dict[str, dict], years_label: str):
    W = 96
    print(f"\n{'='*W}")
    print(f"  PORTFOLIO SIMULATION — S${PF_START_SGD} start, {PF_RISK_PCT*100:.0f}% risk/trade, "
          f"{PF_MAX_POS_PCT*100:.0f}% max position, {PF_MIN_CASH_PCT*100:.0f}% cash floor ({years_label})")
    print(f"{'='*W}")
    print(f"  {'Variant':<17}{'End S$':>9}{'TotRet':>9}{'CAGR':>8}{'MaxDD':>8}"
          f"{'Trades':>8}{'WR%':>6}{'Skipped':>9}{'Exposure':>10}")
    print(f"  {'-'*90}")
    for vname, m in results.items():
        print(f"  {vname:<17}{m['end_equity_sgd']:>9.0f}{m['total_return']:>+8.1f}%{m['cagr']:>+7.1f}%"
              f"{m['max_dd']:>+7.1f}%{m['trades']:>8}{m['win_rate']:>5.0f}%"
              f"{m['skipped_capital']:>9}{m['exposure_pct']:>9.0f}%")
    best = max(results.items(), key=lambda kv: kv[1]["cagr"])
    print(f"\n  Best CAGR: {best[0]}  {best[1]['cagr']:+.1f}%/yr  (max drawdown {best[1]['max_dd']:+.1f}%)")
    print(f"{'='*W}\n")


# ── Diagnostic report ────────────────────────────────────────────────────────

def _report_diagnose(all_trades: list[dict]):
    df     = pd.DataFrame(all_trades)
    closed = df[df["reason"] != "open_mtm"].copy()

    W = 78
    print(f"\n{'='*W}")
    print(f"  DIAGNOSTIC REPORT — filter impact on MEAN_REV_ONLY (validate window)")
    print(f"{'='*W}")

    header = f"  {'Group':<28} {'Legs':>5} {'WR%':>6} {'AvgW%':>7} {'AvgL%':>7} {'Exp%':>7}"
    sep    = f"  {'-'*66}"

    print(header)
    print(sep)

    groups = [
        ("passed",   "Passed both filters",    closed[closed["blocked_by"].isna()]),
        ("rs_rank",  "Blocked by RS rank",     closed[closed["blocked_by"] == "rs_rank"]),
        ("sector",   "Blocked by sector ETF",  closed[closed["blocked_by"] == "sector"]),
        ("both",     "Blocked by both",        closed[closed["blocked_by"] == "both"]),
    ]

    for _, label, subset in groups:
        if subset.empty:
            print(f"  {label:<28} {'—':>5}  no closed trades")
            continue
        m = _metrics(subset.to_dict("records"), label=label)
        if m.get("legs", 0) == 0:
            print(f"  {label:<28} {'—':>5}  no closed trades")
        else:
            print(f"  {label:<28} {m['legs']:>5} {m['win_rate']:>5.0f}%"
                  f" {m['avg_win']:>+6.1f}% {m['avg_loss']:>+6.1f}% {m['exp']:>+6.2f}%")

    print(sep)
    m_all = _metrics(closed.to_dict("records"))
    print(f"  {'All (unfiltered)':<28} {m_all['legs']:>5} {m_all['win_rate']:>5.0f}%"
          f" {m_all['avg_win']:>+6.1f}% {m_all['avg_loss']:>+6.1f}% {m_all['exp']:>+6.2f}%")

    # RS3m distribution — trades blocked by RS rank filter
    rs_blocked = closed[
        closed["blocked_by"].isin(["rs_rank", "both"]) & closed["rs3m_val"].notna()
    ]
    if not rs_blocked.empty:
        print(f"\n  RS3m at entry — trades blocked by RS rank filter")
        print(f"  {'Bucket':<16} {'Legs':>5} {'WR%':>6} {'Avg outcome':>13}")
        bins   = [(-999, -10), (-10, -5), (-5, 0), (0, 5), (5, 10), (10, 999)]
        labels = ["< -10%", "-10 to -5%", "-5 to  0%", "0 to +5%", "+5 to +10%", "> +10%"]
        for (lo, hi), lbl in zip(bins, labels):
            sub = rs_blocked[(rs_blocked["rs3m_val"] >= lo) & (rs_blocked["rs3m_val"] < hi)]
            if sub.empty:
                continue
            wr  = (sub["pnl_pct"] > 0).mean() * 100
            avg = sub["pnl_pct"].mean()
            print(f"  {lbl:<16} {len(sub):>5} {wr:>5.0f}%  avg {avg:>+6.1f}%")

    # VIX at entry distribution — all trades
    vix_tagged = closed[closed["vix_at_entry"].notna()]
    if not vix_tagged.empty:
        print(f"\n  VIX at entry — all trades (unfiltered)")
        print(f"  {'Bucket':<14} {'Legs':>5} {'WR%':>6} {'Avg outcome':>13}  hypothesis")
        vix_bins = [(0, 15), (15, 20), (20, 25), (25, 30), (30, 999)]
        vix_labels = [
            ("< 15",  "complacent — mean-rev ideal"),
            ("15–20", "normal"),
            ("20–25", "mildly elevated"),
            ("25–30", "elevated — potential falling knife"),
            ("> 30",  "fear — high risk of continuation"),
        ]
        for (lo, hi), (lbl, note) in zip(vix_bins, vix_labels):
            sub = vix_tagged[(vix_tagged["vix_at_entry"] >= lo) & (vix_tagged["vix_at_entry"] < hi)]
            if sub.empty:
                continue
            wr  = (sub["pnl_pct"] > 0).mean() * 100
            avg = sub["pnl_pct"].mean()
            print(f"  {lbl:<14} {len(sub):>5} {wr:>5.0f}%  avg {avg:>+6.1f}%   {note}")

    print(f"\n{'='*W}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",       type=int, default=365,
                        help="Lookback window for single-strategy mode (default 365)")
    parser.add_argument("--symbols",    nargs="*",
                        help="Override universe (e.g. --symbols AAPL MSFT NVDA)")
    parser.add_argument("--compare",    action="store_true",
                        help="Run all variants head-to-head with train/validate split")
    parser.add_argument("--no-filters", action="store_true",
                        help="Skip RS rank and sector ETF filters")
    parser.add_argument("--diagnose",   action="store_true",
                        help="Run MEAN_REV_ONLY on validate window, tag trades by filter, show diagnostic report")
    parser.add_argument("--walkforward", action="store_true",
                        help="Run all variants over consecutive 6-month windows (5y data) — judge on pooled expectancy + consistency")
    parser.add_argument("--portfolio",  action="store_true",
                        help="Simulate the actual S$5k account (shared capital, risk sizing, cash floor) per variant on 5y data")
    parser.add_argument("--windows",    type=int, default=8,
                        help="Number of walk-forward windows (default 8 = ~4 years of entries)")
    parser.add_argument("--regime-symbol", default="SPY",
                        help="Index for the 200 SMA regime gate (SPY, ^STI, BTC-USD)")
    parser.add_argument("--no-regime",  action="store_true",
                        help="Disable the market regime gate (live always applies it)")
    parser.add_argument("--earnings-blackout", action="store_true",
                        help="Block entries within 10 days before earnings (mirrors live WATCH)")
    parser.add_argument("--profit-ratio", type=float, default=None,
                        help="Override reward:risk target (default 2.0) — e.g. 1.5 for range-bound SGX, 3.0 for trending crypto")
    parser.add_argument("--max-hold-weeks", type=int, default=None,
                        help="Override the time stop (default 8 weeks)")
    parser.add_argument("--persistent-trail", action="store_true",
                        help="Trailing stop arms off peak gain and stays armed (tests the live de-arm quirk)")
    args = parser.parse_args()

    global PROFIT_RATIO, MAX_HOLD_WEEKS, PERSISTENT_TRAIL
    if args.profit_ratio:
        PROFIT_RATIO = args.profit_ratio
        print(f"Profit ratio override: {PROFIT_RATIO}:1")
    if args.max_hold_weeks:
        MAX_HOLD_WEEKS = args.max_hold_weeks
        print(f"Time stop override: {MAX_HOLD_WEEKS} weeks")
    if args.persistent_trail:
        PERSISTENT_TRAIL = True
        print("Persistent trailing stop: ON")

    symbols = args.symbols or UNIVERSE
    period = "5y" if (args.walkforward or args.portfolio) else "3y"

    # ── Pass 1: fetch and prepare all symbols ────────────────────────────────
    print(f"\nFetching {len(symbols)} symbols ({period})…")
    prepared_dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        sys.stdout.write(f"  {sym:<6} … ")
        sys.stdout.flush()
        df = _prepare(sym, period=period)
        if df is None:
            print("skip")
            continue
        prepared_dfs[sym] = df
        print("ok")

    if not prepared_dfs:
        print("No data available.")
        return

    # ── Pre-compute live-system filters ──────────────────────────────────────
    rs_rank_map:  dict[str, pd.Series] = {}
    rs3m_map:     dict[str, pd.Series] = {}
    sector_cache: dict[str, pd.Series] = {}
    vix_series:   pd.Series            = pd.Series(dtype=float)

    if not args.no_filters:
        print("\nComputing RS ranks…", end=" ", flush=True)
        rs_rank_map, rs3m_map = _compute_rs_rank_series(prepared_dfs)
        print("done")

        needed_etfs = {SECTOR_ETF_MAP[s] for s in prepared_dfs if s in SECTOR_ETF_MAP}
        if needed_etfs:
            print(f"Fetching sector ETFs ({', '.join(sorted(needed_etfs))})…", end=" ", flush=True)
            sector_cache = _fetch_sector_above_200(needed_etfs, period=period)
            print("done")

        print("Fetching VIX…", end=" ", flush=True)
        vix_series = _fetch_vix(period=period)
        print("done")

    regime_series: pd.Series | None = None
    if not args.no_regime:
        print(f"Fetching regime index ({args.regime_symbol})…", end=" ", flush=True)
        regime_series = _fetch_regime_series(args.regime_symbol, period=period)
        print(f"done ({len(regime_series)} days)" if not regime_series.empty else "no data — gate off")
        if regime_series.empty:
            regime_series = None

    earnings_blackout: dict[str, set] = {}
    if args.earnings_blackout:
        print("Fetching earnings dates…", end=" ", flush=True)
        earnings_blackout = _fetch_earnings_blackout(list(prepared_dfs))
        print(f"done ({len(earnings_blackout)} symbols with earnings)")

    def _filters(sym: str) -> tuple:
        sector_etf = SECTOR_ETF_MAP.get(sym)
        return (
            sector_cache.get(sector_etf) if sector_etf else None,
            rs_rank_map.get(sym),
            rs3m_map.get(sym),
            vix_series,
            earnings_blackout.get(sym),
        )

    # ── Pass 2: run backtest ──────────────────────────────────────────────────
    if args.portfolio:
        print(f"\nRunning portfolio simulation ({len(VARIANTS)} variants)…")
        pf_results: dict[str, dict] = {}
        for vname, entry_fn in VARIANTS.items():
            sys.stdout.write(f"  {vname:<17} … ")
            sys.stdout.flush()
            pf_results[vname] = _run_portfolio(prepared_dfs, entry_fn, _filters,
                                               regime_series=regime_series)
            print(f"end S${pf_results[vname]['end_equity_sgd']:.0f}")
        span = next(iter(prepared_dfs.values())).index
        years_label = f"{span[0].date()} to {span[-1].date()}"
        _report_portfolio(pf_results, years_label)

    elif args.walkforward:
        print(f"\nRunning walk-forward: {args.windows} × {WF_WINDOW_DAYS}d entry windows, "
              f"{len(VARIANTS)} variants, filters {'off' if args.no_filters else 'on'}…")
        results = _run_walkforward(prepared_dfs, args.windows, _filters,
                                   regime_series=regime_series)
        _report_walkforward(results, args.windows)

    elif args.diagnose:
        print(f"\nRunning diagnostic (MEAN_REV_ONLY, validate window, filters + VIX tagged)…\n")
        all_trades: list[dict] = []
        for sym, df in prepared_dfs.items():
            sector_s, rs_s, rs3m_s, vix_s, earn_blk = _filters(sym)
            legs = _run(sym, df, _entry_mean_rev_only,
                        entry_start_days=0, entry_end_days=VALIDATE_DAYS,
                        sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s,
                        vix_series=vix_s, regime_series=regime_series,
                        earnings_blocked=earn_blk, diagnose=True)
            if legs:
                print(f"  {sym:<6}   {len(legs)} legs")
            all_trades.extend(legs)
        _report_diagnose(all_trades)

    elif args.compare:
        print(f"\nRunning strategy comparison…")
        print(f"Train ~18m (days {VALIDATE_DAYS}–{TRAIN_DAYS} ago)  |  "
              f"Validate ~6m (last {VALIDATE_DAYS}d, out-of-sample)  |  "
              f"Slippage+commission applied\n")

        results: dict[str, dict[str, list[dict]]] = {v: {"train": [], "validate": []} for v in VARIANTS}

        for sym, df in prepared_dfs.items():
            sector_s, rs_s, rs3m_s, vix_s, earn_blk = _filters(sym)
            counts = []
            for vname, entry_fn in VARIANTS.items():
                train_trades    = _run(sym, df, entry_fn,
                                       entry_start_days=VALIDATE_DAYS, entry_end_days=TRAIN_DAYS,
                                       sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s,
                                       regime_series=regime_series, earnings_blocked=earn_blk)
                validate_trades = _run(sym, df, entry_fn,
                                       entry_start_days=0, entry_end_days=VALIDATE_DAYS,
                                       sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s,
                                       regime_series=regime_series, earnings_blocked=earn_blk)
                results[vname]["train"].extend(train_trades)
                results[vname]["validate"].extend(validate_trades)
                counts.append(f"{vname[:3]}:{len(train_trades)+len(validate_trades)}")
            print(f"  {sym:<6}   {'  '.join(counts)}")

        _report_compare(results)

    else:
        print(f"\nRunning {args.days}d backtest (BASELINE)…")
        all_trades = []
        for sym, df in prepared_dfs.items():
            sector_s, rs_s, rs3m_s, vix_s, earn_blk = _filters(sym)
            legs = _run(sym, df, _entry_baseline,
                        entry_start_days=0, entry_end_days=args.days,
                        sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s,
                        regime_series=regime_series, earnings_blocked=earn_blk)
            all_trades.extend(legs)
            print(f"  {sym:<6}   {len(legs)} legs")

        _report_single(all_trades, args.days)


if __name__ == "__main__":
    main()
