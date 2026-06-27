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

def _prepare(symbol: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(symbol).history(period="3y", auto_adjust=True)
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


VARIANTS: dict[str, callable] = {
    "BASELINE":       _entry_baseline,
    "SMA_CROSS":      _entry_sma_cross,
    "DONCHIAN_STOP":  _entry_donchian_stop,
    "COMBINED":       _entry_combined,
    "SWING_LOW":      _entry_swing_low,
}


# ── Backtest per symbol ───────────────────────────────────────────────────────

def _apply_costs(pnl_pct: float) -> float:
    """Subtract slippage and commission (applied at both entry and exit)."""
    cost = (SLIPPAGE_PCT + COMMISSION_PCT) * 2 * 100  # convert to %
    return pnl_pct - cost


def _run(symbol: str, df: pd.DataFrame, entry_fn: callable,
         entry_start_days: int = 0, entry_end_days: int = 365) -> list[dict]:
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

            def _record(reason: str, exit_px: float, frac: float):
                raw_pnl = (exit_px - ep) / ep * 100
                trades.append({
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
                })

            if price <= stop:
                _record("stop_hit", price, pos["shares"])
                pos = None
            elif not above200:
                _record("trend_break", price, pos["shares"])
                pos = None
            elif not pos["sold_half"]:
                if rsi_val > RSI_EXIT:
                    _record("rsi_half", price, 0.5)
                    pos["shares"]    = 0.5
                    pos["sold_half"] = True
                    pos["stop"]      = ep   # move stop to breakeven
                elif price >= tgt:
                    _record("target_half", price, 0.5)
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
                    if price <= trail_stop:
                        _record("trail_stop", price, pos["shares"])
                        pos = None
                if pos is not None and weeks >= MAX_HOLD_WEEKS and gain * 100 < 5:
                    _record("time_stop", price, pos["shares"])
                    pos = None

            continue

        # ── ENTRY (within the entry window only) ─────────────────────────────
        if not (entry_open <= date <= entry_close):
            continue

        sig, stop = entry_fn(row)
        if sig:
            risk = price - stop
            if risk <= 0:
                continue
            pos = {
                "entry_price": price,
                "entry_date":  date,
                "stop":        stop,
                "target":      price + PROFIT_RATIO * risk,
                "shares":      1.0,
                "sold_half":   False,
                "peak":        price,
                "signal_type": sig,
            }

    if pos:
        last    = df.iloc[-1]
        last_px = float(last["Close"])
        last_dt = df.index[-1]
        raw_pnl = (last_px - pos["entry_price"]) / pos["entry_price"] * 100
        trades.append({
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
        })

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


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days",    type=int, default=365,
                        help="Lookback window for single-strategy mode (default 365)")
    parser.add_argument("--symbols", nargs="*",
                        help="Override universe (e.g. --symbols AAPL MSFT NVDA)")
    parser.add_argument("--compare", action="store_true",
                        help="Run all 4 variants head-to-head with train/validate split")
    args = parser.parse_args()

    symbols = args.symbols or UNIVERSE

    if args.compare:
        print(f"\nFetching {len(symbols)} symbols for strategy comparison…")
        print(f"Train ~18m (days {VALIDATE_DAYS}–{TRAIN_DAYS} ago)  |  "
              f"Validate ~6m (last {VALIDATE_DAYS}d, out-of-sample)  |  "
              f"Slippage+commission applied\n")

        # results[variant][phase] = list of trade dicts
        results: dict[str, dict[str, list[dict]]] = {v: {"train": [], "validate": []} for v in VARIANTS}

        for sym in symbols:
            sys.stdout.write(f"  {sym:<6} … ")
            sys.stdout.flush()
            df = _prepare(sym)
            if df is None:
                print("skip")
                continue

            counts = []
            for vname, entry_fn in VARIANTS.items():
                # Train: entries from TRAIN_DAYS to VALIDATE_DAYS ago (non-overlapping with validate)
                train_trades    = _run(sym, df, entry_fn,
                                       entry_start_days=VALIDATE_DAYS, entry_end_days=TRAIN_DAYS)
                # Validate: entries in the most recent VALIDATE_DAYS (true out-of-sample)
                validate_trades = _run(sym, df, entry_fn,
                                       entry_start_days=0, entry_end_days=VALIDATE_DAYS)
                results[vname]["train"].extend(train_trades)
                results[vname]["validate"].extend(validate_trades)
                counts.append(f"{vname[:3]}:{len(train_trades)+len(validate_trades)}")
            print("  ".join(counts))

        _report_compare(results)

    else:
        print(f"\nFetching {len(symbols)} symbols — {args.days}d backtest window…")
        all_trades: list[dict] = []
        for sym in symbols:
            sys.stdout.write(f"  {sym:<6} … ")
            sys.stdout.flush()
            df = _prepare(sym)
            if df is None:
                print("skip")
                continue
            legs = _run(sym, df, _entry_baseline, entry_start_days=0, entry_end_days=args.days)
            all_trades.extend(legs)
            print(f"{len(legs)} legs")

        _report_single(all_trades, args.days)


if __name__ == "__main__":
    main()
