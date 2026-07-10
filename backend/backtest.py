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
    df["vol_avg"]   = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_avg"].replace(0, np.nan)

    # Donchian bands — shift(1) so we only use yesterday's data at entry
    df["don_upper"] = h.shift(1).rolling(MOMENTUM_DAYS).max()  # 20-day high
    df["don_lower"] = l.shift(1).rolling(MOMENTUM_DAYS).min()  # 20-day low

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

    for i, (date, row) in enumerate(df.iterrows()):
        if i < SMA_LONG:
            continue

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
                if gain >= TRAILING_TRIGGER:
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
    exp      = win_rate / 100 * avg_w + (1 - win_rate / 100) * avg_l

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
                     filters_fn) -> dict[str, list[dict]]:
    """Returns {variant: [ {window metrics + trades}, ... ]} oldest window first."""
    results: dict[str, list[dict]] = {v: [] for v in VARIANTS}
    for w in range(n_windows - 1, -1, -1):      # oldest first
        start_days = w * WF_WINDOW_DAYS
        end_days   = (w + 1) * WF_WINDOW_DAYS
        for vname, entry_fn in VARIANTS.items():
            trades: list[dict] = []
            for sym, df in prepared_dfs.items():
                sector_s, rs_s, rs3m_s, _vix = filters_fn(sym)
                trades.extend(_run(sym, df, entry_fn,
                                   entry_start_days=start_days, entry_end_days=end_days,
                                   sector_above_200=sector_s, rs_rank_series=rs_s,
                                   rs3m_series=rs3m_s))
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
    parser.add_argument("--windows",    type=int, default=8,
                        help="Number of walk-forward windows (default 8 = ~4 years of entries)")
    args = parser.parse_args()

    symbols = args.symbols or UNIVERSE
    period = "5y" if args.walkforward else "3y"

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

    def _filters(sym: str) -> tuple[pd.Series | None, pd.Series | None, pd.Series | None, pd.Series]:
        sector_etf = SECTOR_ETF_MAP.get(sym)
        return (
            sector_cache.get(sector_etf) if sector_etf else None,
            rs_rank_map.get(sym),
            rs3m_map.get(sym),
            vix_series,
        )

    # ── Pass 2: run backtest ──────────────────────────────────────────────────
    if args.walkforward:
        print(f"\nRunning walk-forward: {args.windows} × {WF_WINDOW_DAYS}d entry windows, "
              f"{len(VARIANTS)} variants, filters {'off' if args.no_filters else 'on'}…")
        results = _run_walkforward(prepared_dfs, args.windows, _filters)
        _report_walkforward(results, args.windows)

    elif args.diagnose:
        print(f"\nRunning diagnostic (MEAN_REV_ONLY, validate window, filters + VIX tagged)…\n")
        all_trades: list[dict] = []
        for sym, df in prepared_dfs.items():
            sector_s, rs_s, rs3m_s, vix_s = _filters(sym)
            legs = _run(sym, df, _entry_mean_rev_only,
                        entry_start_days=0, entry_end_days=VALIDATE_DAYS,
                        sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s,
                        vix_series=vix_s, diagnose=True)
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
            sector_s, rs_s, rs3m_s, vix_s = _filters(sym)
            counts = []
            for vname, entry_fn in VARIANTS.items():
                train_trades    = _run(sym, df, entry_fn,
                                       entry_start_days=VALIDATE_DAYS, entry_end_days=TRAIN_DAYS,
                                       sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s)
                validate_trades = _run(sym, df, entry_fn,
                                       entry_start_days=0, entry_end_days=VALIDATE_DAYS,
                                       sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s)
                results[vname]["train"].extend(train_trades)
                results[vname]["validate"].extend(validate_trades)
                counts.append(f"{vname[:3]}:{len(train_trades)+len(validate_trades)}")
            print(f"  {sym:<6}   {'  '.join(counts)}")

        _report_compare(results)

    else:
        print(f"\nRunning {args.days}d backtest (BASELINE)…")
        all_trades = []
        for sym, df in prepared_dfs.items():
            sector_s, rs_s, rs3m_s, vix_s = _filters(sym)
            legs = _run(sym, df, _entry_baseline,
                        entry_start_days=0, entry_end_days=args.days,
                        sector_above_200=sector_s, rs_rank_series=rs_s, rs3m_series=rs3m_s)
            all_trades.extend(legs)
            print(f"  {sym:<6}   {len(legs)} legs")

        _report_single(all_trades, args.days)


if __name__ == "__main__":
    main()
