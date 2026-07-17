import time
import threading
import pandas as pd
import yfinance as yf
from datetime import datetime
from config import (
    RSI_ENTRY, RSI_EXIT, SMA_LONG, SMA_SHORT, MOMENTUM_DAYS,
    STOP_ATR_MULT, STOP_MAX_PCT, PROFIT_RATIO, VIX_CAUTION, VIX_DEFENSIVE,
    SPREAD_WIDTH
)

SECTOR_ETF_MAP = {
    "Technology":             "XLK",
    "Health Care":            "XLV",
    "Healthcare":             "XLV",
    "Financials":             "XLF",
    "Financial Services":     "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Cyclical":      "XLY",   # yfinance's actual label for discretionary
    "Communication Services": "XLC",
    "Industrials":            "XLI",
    "Consumer Staples":       "XLP",
    "Consumer Defensive":     "XLP",   # yfinance's actual label for staples
    "Energy":                 "XLE",
    "Utilities":              "XLU",
    "Real Estate":            "XLRE",
    "Materials":              "XLB",
    "Basic Materials":        "XLB",
}

_cache: dict = {}
_cache_lock = threading.Lock()
CACHE_TTL = 900  # 15 minutes


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    ma_up = up.ewm(com=period - 1, min_periods=period).mean()
    ma_down = down.ewm(com=period - 1, min_periods=period).mean()
    rs = ma_up / ma_down.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    key = f"{symbol}:{period}"
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]

    try:
        hist = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    except Exception:
        hist = pd.DataFrame()

    with _cache_lock:
        _cache[key] = {"data": hist, "ts": time.time()}
    return hist


def get_return_since(symbol: str, start_date: str) -> dict | None:
    """Return from the first close on/after start_date (YYYY-MM-DD) to the latest close."""
    hist = _fetch_history(symbol)
    closes = hist["Close"].dropna() if not hist.empty else hist
    if closes is None or len(closes) == 0:
        return None
    sel = closes[closes.index.strftime("%Y-%m-%d") >= start_date]
    if len(sel) == 0:
        return None
    start, now = float(sel.iloc[0]), float(closes.iloc[-1])
    return {"start": start, "now": now, "pct": (now / start - 1) * 100}


def get_market_regime() -> dict:
    spy_hist = _fetch_history("SPY")
    vix_hist = _fetch_history("^VIX", period="5d")
    fx_hist = _fetch_history("SGDUSD=X", period="5d")

    spy_close = spy_hist["Close"].dropna() if not spy_hist.empty else spy_hist
    if spy_close.empty or len(spy_close) < SMA_LONG:
        return {"error": "Insufficient SPY data"}

    sma200 = spy_close.rolling(SMA_LONG).mean().iloc[-1]
    spy_price = spy_close.iloc[-1]
    if pd.isna(sma200) or pd.isna(spy_price):
        return {"error": "Insufficient SPY data"}
    regime_ok = bool(spy_price > sma200)

    vix = float(vix_hist["Close"].iloc[-1]) if not vix_hist.empty else 18.0
    if pd.isna(vix):
        vix = 18.0
    if vix > VIX_DEFENSIVE:
        vix_status = "EXTREME"
    elif vix > VIX_CAUTION:
        vix_status = "ELEVATED"
    else:
        vix_status = "NORMAL"

    # Size multiplier: 0.5 if bearish or VIX elevated, else 1.0
    size_mult = 0.5 if (not regime_ok or vix > VIX_CAUTION) else 1.0

    sgd_to_usd = float(fx_hist["Close"].iloc[-1]) if not fx_hist.empty else 0.74
    if pd.isna(sgd_to_usd):
        sgd_to_usd = 0.74

    return {
        "spy_price": round(float(spy_price), 2),
        "spy_sma200": round(float(sma200), 2),
        "regime": "BULLISH" if regime_ok else "BEARISH",
        "regime_ok": regime_ok,
        "vix": round(vix, 2),
        "vix_status": vix_status,
        "new_position_size_multiplier": size_mult,
        "sgd_to_usd": round(sgd_to_usd, 4),
        "usd_to_sgd": round(1 / sgd_to_usd, 4),
    }


def get_crypto_regime(equity_regime: dict) -> dict:
    """Crypto-native regime: BTC vs its own 200-day SMA. SPY's trend says little
    about crypto — gating BTC entries on the S&P was blocking valid setups.
    Carries the equity regime's VIX and FX fields through for sizing/formatting."""
    hist = _fetch_history("BTC-USD")
    close = hist["Close"].dropna() if not hist.empty else hist
    if close is None or len(close) < SMA_LONG:
        return equity_regime   # no BTC data — fall back rather than block

    sma200 = float(close.rolling(SMA_LONG).mean().iloc[-1])
    price = float(close.iloc[-1])
    if pd.isna(sma200) or pd.isna(price):
        return equity_regime
    regime_ok = price > sma200

    return {
        **equity_regime,
        "regime_ok": regime_ok,
        "regime": "BULLISH" if regime_ok else "BEARISH",
        "basis": "BTC",
        "btc_price": round(price, 2),
        "btc_sma200": round(sma200, 2),
    }


def get_sgx_regime(equity_regime: dict) -> dict:
    """SGX-native regime: Straits Times Index vs its own 200-day SMA. The S&P's
    trend says little about Singapore banks/REITs — gating SGX entries on SPY
    blocked (or allowed) entries for the wrong reasons. Carries the equity
    regime's VIX and FX fields through for sizing/formatting."""
    hist = _fetch_history("^STI")
    close = hist["Close"].dropna() if not hist.empty else hist
    if close is None or len(close) < SMA_LONG:
        return equity_regime   # no STI data — fall back rather than block

    sma200 = float(close.rolling(SMA_LONG).mean().iloc[-1])
    price = float(close.iloc[-1])
    if pd.isna(sma200) or pd.isna(price):
        return equity_regime
    regime_ok = price > sma200

    return {
        **equity_regime,
        "regime_ok": regime_ok,
        "regime": "BULLISH" if regime_ok else "BEARISH",
        "basis": "STI",
        "sti_price": round(price, 2),
        "sti_sma200": round(sma200, 2),
    }


def get_ticker_analysis(symbol: str) -> dict | None:
    hist = _fetch_history(symbol)
    hist = hist.dropna(subset=["Close"]) if not hist.empty else hist
    if hist.empty or len(hist) < SMA_LONG + 5:
        return None

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]

    price = float(close.iloc[-1])
    sma200 = float(close.rolling(SMA_LONG).mean().iloc[-1])
    sma50 = float(close.rolling(SMA_SHORT).mean().iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1])
    rsi_series = _rsi(close)
    rsi = float(rsi_series.iloc[-1])
    atr_val = float(_atr(high, low, close).iloc[-1])

    avg_vol = float(volume.iloc[-21:-1].mean())   # 20 complete prior days, not today's partial bar
    today_vol = float(volume.iloc[-1])
    vol_ratio = round(today_vol / avg_vol, 2) if avg_vol > 0 else 1.0

    high_20d = float(high.iloc[-(MOMENTUM_DAYS + 1):-1].max())
    high_52w = float(high.max())
    low_52w = float(low.min())
    pct_from_high = round(((price - high_52w) / high_52w) * 100, 1)

    above_200 = price > sma200
    above_50 = price > sma50
    sma20_above_sma50 = sma20 > sma50

    stop = max(price - STOP_ATR_MULT * atr_val, price * (1 - STOP_MAX_PCT))
    stop_pct = round(((price - stop) / price) * 100, 1)
    target = price + PROFIT_RATIO * (price - stop)

    # Strategy D (SWING_LOW_NOCAP) stop: structural 40-day low excluding the most
    # recent week, minus 0.5×ATR, capped at 8% below price, floored at 2%.
    # Must match backtest.py: swing_low = low.shift(6).rolling(40).min()
    swing_low = float(low.iloc[-46:-6].min())
    stop_swing = max(swing_low - 0.5 * atr_val, price * (1 - STOP_MAX_PCT))
    stop_swing = min(stop_swing, price * (1 - 0.02))
    target_swing = price + PROFIT_RATIO * (price - stop_swing)

    # Fundamental data
    try:
        info = yf.Ticker(symbol).info
        pe = info.get("forwardPE") or info.get("trailingPE")
        sector = info.get("sector", "ETF")
    except Exception:
        pe, sector = None, "ETF"

    # Earnings
    earnings_date = None
    days_to_earnings = None
    try:
        cal = yf.Ticker(symbol).calendar
        ed_raw = None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
            if dates:
                ed_raw = dates[0] if isinstance(dates, (list, pd.Series)) else dates
        elif cal is not None and hasattr(cal, "empty") and not cal.empty:
            row = cal.iloc[0]
            ed_raw = row.get("Earnings Date") if hasattr(row, "get") else None
        if ed_raw is not None:
            ed_date = ed_raw.date() if hasattr(ed_raw, "date") else ed_raw
            days_to_earnings = (ed_date - datetime.today().date()).days
            earnings_date = str(ed_date)
    except Exception:
        pass

    earnings_warning = days_to_earnings is not None and 0 < days_to_earnings <= 10

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "rsi": round(rsi, 1),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "atr": round(atr_val, 2),
        "above_200sma": above_200,
        "above_50sma": above_50,
        "sma20_above_sma50": sma20_above_sma50,
        "sma20": round(sma20, 2),
        "volume_ratio": vol_ratio,
        "high_20d": round(high_20d, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_from_52w_high": pct_from_high,
        "stop_loss": round(stop, 2),
        "stop_pct": stop_pct,
        "profit_target": round(target, 2),
        "swing_low": round(swing_low, 2),
        "stop_loss_swing": round(stop_swing, 2),
        "profit_target_swing": round(target_swing, 2),
        "pe_ratio": round(pe, 1) if pe else None,
        "sector": sector,
        "earnings_date": earnings_date,
        "days_to_earnings": days_to_earnings,
        "earnings_warning": earnings_warning,
    }


def get_fundamentals(symbol: str) -> dict:
    """
    Fundamental score for stocks. ETFs get is_etf=True and no scoring.
    Cached for 1 hour since fundamentals change slowly.
    """
    key = f"fundamentals:{symbol}"
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < 3600:
            return entry["data"]

    try:
        info = yf.Ticker(symbol).info
        quote_type = info.get("quoteType", "")
        is_etf = quote_type in ("ETF", "MUTUALFUND") or not info.get("sector")

        if is_etf:
            result = {"is_etf": True, "score": None, "grade": None}
        else:
            rev_growth = info.get("revenueGrowth")        # e.g. 0.15 = 15%
            earn_growth = info.get("earningsGrowth")
            margin = info.get("profitMargins")
            debt_eq = info.get("debtToEquity")            # reported as %, e.g. 50 = 0.5x
            roe = info.get("returnOnEquity")
            fwd_pe = info.get("forwardPE")
            rec = info.get("recommendationKey", "")       # "buy","strong_buy","hold","sell"
            target = info.get("targetMeanPrice")
            num_analysts = info.get("numberOfAnalystOpinions", 0)
            cur_price = info.get("currentPrice") or info.get("regularMarketPrice")

            upside = round(((target - cur_price) / cur_price) * 100, 1) \
                if target and cur_price else None

            score = 0
            good, bad = [], []

            if rev_growth is not None:
                if rev_growth > 0.05:
                    score += 1
                    good.append(f"Revenue +{rev_growth*100:.0f}% YoY")
                else:
                    bad.append(f"Revenue growth weak ({rev_growth*100:.0f}%)")

            if earn_growth is not None:
                if earn_growth > 0:
                    score += 1
                    good.append(f"Earnings +{earn_growth*100:.0f}% YoY")
                else:
                    bad.append(f"Earnings declining ({earn_growth*100:.0f}%)")

            if margin is not None:
                if margin > 0.10:
                    score += 1
                    good.append(f"Net margin {margin*100:.0f}%")
                else:
                    bad.append(f"Low margin ({margin*100:.0f}%)")

            if debt_eq is not None:
                if debt_eq < 150:   # 150 = 1.5x D/E
                    score += 1
                    good.append(f"D/E {debt_eq/100:.1f}x")
                else:
                    bad.append(f"High debt D/E {debt_eq/100:.1f}x")

            if rec in ("buy", "strong_buy"):
                score += 1
                good.append(f"Analysts: {rec.replace('_', ' ').title()} ({num_analysts})")
            elif rec in ("sell", "strong_sell"):
                bad.append(f"Analysts: {rec.replace('_', ' ').title()}")

            grade = "A" if score >= 4 else "B" if score == 3 else "C" if score == 2 else "D"

            result = {
                "is_etf": False,
                "score": score,
                "grade": grade,
                "revenue_growth": round(rev_growth * 100, 1) if rev_growth is not None else None,
                "earnings_growth": round(earn_growth * 100, 1) if earn_growth is not None else None,
                "profit_margin": round(margin * 100, 1) if margin is not None else None,
                "debt_equity": round(debt_eq / 100, 2) if debt_eq is not None else None,
                "roe": round(roe * 100, 1) if roe is not None else None,
                "forward_pe": round(fwd_pe, 1) if fwd_pe else None,
                "analyst_rating": rec or None,
                "target_price": round(target, 2) if target else None,
                "upside_pct": upside,
                "num_analysts": num_analysts,
                "reasons_good": good,
                "reasons_bad": bad,
            }
    except Exception as e:
        result = {"is_etf": False, "score": 0, "grade": "?", "error": str(e)}

    with _cache_lock:
        _cache[key] = {"data": result, "ts": time.time()}
    return result


def get_relative_strength(symbol: str) -> dict:
    """Return 1m and 3m return vs SPY. Positive = outperforming."""
    if symbol == "SPY":
        return {"rs_1m": 0.0, "rs_3m": 0.0, "return_1m": None, "return_3m": None,
                "outperforming_3m": True}

    hist = _fetch_history(symbol)
    spy = _fetch_history("SPY")
    if hist.empty or spy.empty:
        return {}

    def pct_ret(df: pd.DataFrame, days: int) -> float | None:
        if len(df) < days:
            return None
        return float(((df["Close"].iloc[-1] / df["Close"].iloc[-days]) - 1) * 100)

    r1 = pct_ret(hist, 21)
    r3 = pct_ret(hist, 63)
    s1 = pct_ret(spy, 21)
    s3 = pct_ret(spy, 63)

    rs_1m = round(r1 - s1, 1) if r1 is not None and s1 is not None else None
    rs_3m = round(r3 - s3, 1) if r3 is not None and s3 is not None else None

    return {
        "return_1m": round(r1, 1) if r1 is not None else None,
        "return_3m": round(r3, 1) if r3 is not None else None,
        "rs_1m": rs_1m,
        "rs_3m": rs_3m,
        "outperforming_3m": rs_3m > -10 if rs_3m is not None else True,
    }


def get_options_premium(symbol: str, target_strike: float, option_type: str = "put") -> dict | None:
    """Fetch live bid/ask/mid for the nearest 30-45 DTE option at the target strike.
    option_type: 'put' for CSP (Phase 1), 'call' for CC (Phase 2). Cached 10 min."""
    import datetime as _dt
    key = f"options_premium:{symbol}:{target_strike}:{option_type}"
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < 600:
            return entry["data"]

    result = None
    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            raise ValueError("no expirations")

        today = _dt.date.today()
        # Find expiry closest to 38 DTE within 25-55 day window
        best_expiry, best_dte = None, None
        for exp_str in expirations:
            dte = (_dt.date.fromisoformat(exp_str) - today).days
            if 25 <= dte <= 55:
                if best_dte is None or abs(dte - 38) < abs(best_dte - 38):
                    best_expiry, best_dte = exp_str, dte

        if not best_expiry:
            raise ValueError("no suitable expiry")

        chain = ticker.option_chain(best_expiry)
        opts = chain.puts if option_type == "put" else chain.calls
        if opts.empty:
            raise ValueError("empty chain")

        opts = opts.copy()
        opts["strike_diff"] = (opts["strike"] - target_strike).abs()
        row = opts.loc[opts["strike_diff"].idxmin()]

        bid  = float(row.get("bid", 0) or 0)
        ask  = float(row.get("ask", 0) or 0)
        last = float(row.get("lastPrice", 0) or 0)
        mid  = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else last
        iv   = float(row.get("impliedVolatility", 0) or 0)

        result = {
            "expiry":        best_expiry,
            "dte":           best_dte,
            "strike":        float(row["strike"]),
            "bid":           round(bid, 2),
            "ask":           round(ask, 2),
            "mid":           round(mid, 2),
            "total_contract": round(mid * 100, 2),
            "iv_pct":        round(iv * 100, 1) if iv else None,
        }
    except Exception:
        pass

    with _cache_lock:
        _cache[key] = {"data": result, "ts": time.time()}
    return result


def get_bull_put_spread(symbol: str, price: float, width: float = SPREAD_WIDTH) -> dict | None:
    """Fetch a 30-45 DTE bull put credit spread on an index ETF: sell a put ~5% out of
    the money, buy a put `width` dollars further out to cap risk. Returns net credit,
    max profit/loss, breakeven and ROI on risk. Cached 10 min."""
    import datetime as _dt
    if pd.isna(price):
        return None
    short_target = round(price * 0.95 / 5) * 5   # nearest $5 strike, ~5% OTM
    key = f"bull_put_spread:{symbol}:{short_target}:{width}"
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < 600:
            return entry["data"]

    result = None
    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            raise ValueError("no expirations")

        today = _dt.date.today()
        best_expiry, best_dte = None, None
        for exp_str in expirations:
            dte = (_dt.date.fromisoformat(exp_str) - today).days
            if 25 <= dte <= 55:
                if best_dte is None or abs(dte - 38) < abs(best_dte - 38):
                    best_expiry, best_dte = exp_str, dte
        if not best_expiry:
            raise ValueError("no suitable expiry")

        puts = ticker.option_chain(best_expiry).puts
        if puts.empty:
            raise ValueError("empty chain")
        puts = puts.copy()

        puts["short_diff"] = (puts["strike"] - short_target).abs()
        short_row = puts.loc[puts["short_diff"].idxmin()]
        short_strike = float(short_row["strike"])

        long_target = short_strike - width
        puts["long_diff"] = (puts["strike"] - long_target).abs()
        long_candidates = puts[puts["strike"] < short_strike]
        if long_candidates.empty:
            raise ValueError("no strike below short leg")
        long_row = long_candidates.loc[long_candidates["long_diff"].idxmin()]
        long_strike = float(long_row["strike"])

        def _mid(row) -> float:
            bid = float(row.get("bid", 0) or 0)
            ask = float(row.get("ask", 0) or 0)
            last = float(row.get("lastPrice", 0) or 0)
            return round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else last

        short_mid = _mid(short_row)
        long_mid = _mid(long_row)
        net_credit  = round(short_mid - long_mid, 2)
        actual_width = round(short_strike - long_strike, 2)
        max_profit  = round(net_credit * 100, 2)
        max_loss    = round((actual_width - net_credit) * 100, 2)
        breakeven   = round(short_strike - net_credit, 2)
        iv          = float(short_row.get("impliedVolatility", 0) or 0)

        result = {
            "expiry":       best_expiry,
            "dte":          best_dte,
            "short_strike": short_strike,
            "long_strike":  long_strike,
            "width":        actual_width,
            "net_credit":   net_credit,
            "max_profit":   max_profit,
            "max_loss":     max_loss,
            "breakeven":    breakeven,
            "roi_pct":      round((max_profit / max_loss) * 100, 1) if max_loss > 0 else None,
            "short_iv_pct": round(iv * 100, 1) if iv else None,
        }
    except Exception:
        pass

    with _cache_lock:
        _cache[key] = {"data": result, "ts": time.time()}
    return result


def get_sector_etf_status(symbol: str) -> dict | None:
    """Return the sector ETF's trend status for a stock. None for ETFs or unknown sectors.
    Cached 1 hour — sector membership doesn't change day to day."""
    key = f"sector_etf:{symbol}"
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < 3600:
            return entry["data"]

    result = None
    try:
        info = yf.Ticker(symbol).info
        is_etf = info.get("quoteType", "") in ("ETF", "MUTUALFUND") or not info.get("sector")
        if not is_etf:
            etf_sym = SECTOR_ETF_MAP.get(info.get("sector", ""))
            if etf_sym:
                hist = _fetch_history(etf_sym)
                if not hist.empty and len(hist) >= SMA_LONG:
                    close = hist["Close"]
                    sma = float(close.rolling(SMA_LONG).mean().iloc[-1])
                    price = float(close.iloc[-1])
                    result = {
                        "etf_symbol": etf_sym,
                        "above_200sma": price > sma,
                        "etf_price": round(price, 2),
                        "etf_sma200": round(sma, 2),
                    }
    except Exception:
        pass

    with _cache_lock:
        _cache[key] = {"data": result, "ts": time.time()}
    return result


def invalidate_cache():
    with _cache_lock:
        _cache.clear()
