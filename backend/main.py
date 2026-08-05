import asyncio
import json
import logging
import math
import os
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import ibkr
import futu_broker
import db
import telegram_bot
import news
from analysis import get_market_regime, get_crypto_regime, get_sgx_regime, get_ticker_analysis, get_fundamentals, get_relative_strength, get_sector_etf_status, get_options_premium, get_bull_put_spread, get_return_since, get_holding_events, invalidate_cache
from signals import generate_signal, calculate_position_size
from config import PORTFOLIO_SIZE_SGD, LONGTERM_WATCHLIST, QUANTUM_WATCHLIST, COVERED_CALLS_WATCHLIST, SCREENER_UNIVERSE, SPREAD_UNIVERSE, SPREAD_WIDTH, SPREAD_ACCOUNT_SGD, CRYPTO_WATCHLIST, CRYPTO_POSITION_SGD, TRAILING_TRIGGER, TRAILING_STOP_PCT, SGX_WATCHLIST, SGX_PORTFOLIO_SGD, SGX_HOLDINGS, HOLDINGS_MOVE_ALERT_PCT, RISK_PER_TRADE_PCT, MAX_POSITION_PCT, MAX_SECTOR_PCT, TICKER_SECTORS, NEWS_MOVE_THRESHOLD_PCT, NEWS_HEADLINES_PER_TICKER, STOP_ATR_MULT, STOP_MAX_PCT, PROFIT_RATIO, US10K_ENABLED, US10K_TRACK, US10K_PORTFOLIO_SGD, US10K_VARIANT, US10K_START, US10K_EXPECT_CAGR, US10K_EXPECT_MAXDD, US10K_EXPECT_PER_TRADE

logging.basicConfig(level=logging.INFO)

ACTIONABLE = {"BUY", "SELL", "SELL_HALF", "REVIEW"}
_last_signals: dict[str, str] = {}
_last_signals_b: dict[str, str] = {}
_last_signals_c: dict[str, str] = {}
_last_signals_d: dict[str, str] = {}
_last_crypto_signals: dict[str, str] = {}

# ── Daily summary event log ──────────────────────────────────────────────────
# Every US/SGX signal event lands here (sent immediately or queued); the daily
# summary tasks drain it. In-memory: a redeploy drops undelivered queue entries,
# which is acceptable for a convenience digest — exits always send immediately.
_event_log: list[dict] = []


def _log_event(market: str, text: str, sent: bool):
    _event_log.append({"ts": datetime.now(SGT), "market": market, "sent": sent, "text": text})
    if len(_event_log) > 200:
        del _event_log[: len(_event_log) - 200]


def _drain_events(market: str) -> list[dict]:
    kept = [e for e in _event_log if e["market"] != market]
    drained = [e for e in _event_log if e["market"] == market]
    _event_log[:] = kept
    return drained


# Watcher liveness — last time each watcher completed a scan pass (and, for the
# market watchers, a pass inside the entry-confirm window). Surfaced in /health
# so "did the watcher run through the entry window" is answerable without
# Render log access; /health stays zero-work (in-memory dict read).
_heartbeats: dict[str, str] = {}


def _beat(name: str) -> None:
    _heartbeats[name] = datetime.now(SGT).isoformat(timespec="seconds")


def _us_alert_window() -> bool:
    """First 2 hours after the US open — 21:30–23:30 SGT (DST), when the user is
    awake to act. Entry alerts outside this window queue into the 7:30 AM SGT
    summary instead of buzzing the phone at 2 AM; exits always send."""
    now = datetime.now(ET)
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return open_t <= now < open_t + timedelta(hours=2)


def _send_or_queue_us(tag: str, symbol: str, action: str, detail: str, msg: str, is_exit: bool):
    line = f"[{tag}] {action} {symbol} — {detail}"
    if is_exit or _us_alert_window():
        telegram_bot.send(msg)
        _log_event("US", line, sent=True)
    else:
        _log_event("US", line, sent=False)
_last_sgx_signals: dict[str, str] = {}


def _remember_signal(store: dict[str, str], strategy: str, symbol: str, action: str) -> None:
    """Update in-memory signal state and persist on change, so redeploys don't
    re-fire every active signal on Telegram."""
    if store.get(symbol) != action:
        store[symbol] = action
        try:
            db.save_signal_state(strategy, symbol, action)
        except Exception as e:
            logging.warning(f"signal_state persist failed for {strategy}/{symbol}: {e}")

ET  = ZoneInfo("America/New_York")
SGT = ZoneInfo("Asia/Singapore")

# Shadow-run window agreed 2026-07-12: strategy D vs SPY with pre-committed criteria
VALIDATION_START = "2026-07-13"

# Process start — shown in /health so "heartbeat missing" is readable as
# "restarted recently" vs "watcher actually dead" (heartbeats are in-memory)
_START_TS = datetime.now(ZoneInfo("Asia/Singapore"))


async def _fetch_batch(symbols: list[str], max_concurrent: int = 15) -> list[dict]:
    """Fetch analysis, fundamentals, RS, and sector data for all symbols concurrently."""
    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(symbol: str) -> dict | None:
        async with sem:
            analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
            if not analysis:
                return None
            fundamentals, rel_strength, sector_status = await asyncio.gather(
                loop.run_in_executor(None, get_fundamentals, symbol),
                loop.run_in_executor(None, get_relative_strength, symbol),
                loop.run_in_executor(None, get_sector_etf_status, symbol),
            )
            return {
                "symbol": symbol, "analysis": analysis,
                "fundamentals": fundamentals, "rel_strength": rel_strength,
                "sector_status": sector_status,
            }

    results = await asyncio.gather(*[_one(s) for s in symbols])
    return [r for r in results if r is not None]


def _compute_rs_ranks(stock_data: list[dict]) -> dict[str, int]:
    rs_entries = [
        (d["symbol"], d["rel_strength"].get("rs_3m"))
        for d in stock_data
        if d["rel_strength"] and d["rel_strength"].get("rs_3m") is not None
    ]
    if len(rs_entries) <= 1:
        return {}
    sorted_rs = sorted(rs_entries, key=lambda x: x[1])
    n = len(sorted_rs)
    return {sym: round((i / (n - 1)) * 100) for i, (sym, _) in enumerate(sorted_rs)}


_screener_rs_ranks: dict[str, int] = {}


async def rs_rank_refresher():
    """Hourly RS ranks against the 70-symbol screener universe. Ranking within
    the ~8-symbol watchlist made 'top 25%' nearly meaningless (top 2 of 8) and
    diverged from the backtest; a wide universe gives honest percentiles."""
    await asyncio.sleep(20)
    loop = asyncio.get_event_loop()
    while True:
        try:
            sem = asyncio.Semaphore(10)

            async def _rs(symbol: str):
                async with sem:
                    rs = await loop.run_in_executor(None, get_relative_strength, symbol)
                    return symbol, (rs or {}).get("rs_3m")

            pairs = await asyncio.gather(*[_rs(s) for s in SCREENER_UNIVERSE])
            entries = [(s, v) for s, v in pairs if v is not None]
            if len(entries) > 1:
                ranked = sorted(entries, key=lambda x: x[1])
                n = len(ranked)
                _screener_rs_ranks.clear()
                _screener_rs_ranks.update(
                    {sym: round(i / (n - 1) * 100) for i, (sym, _) in enumerate(ranked)}
                )
                logging.info(f"RS ranks refreshed over {n} screener symbols")
        except Exception as e:
            logging.warning(f"RS rank refresher error: {e}")
        await asyncio.sleep(60 * 60)


def _market_is_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now < close_t


def _seconds_until_next_open() -> float:
    now = datetime.now(ET)
    target = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= target or now.weekday() >= 5:
        target += timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), 60)


def _us_entry_confirm_window() -> bool:
    """True in the last 30 min of the US session (15:30–16:00 ET). Entries are
    only confirmed here: the backtest enters on the completed daily bar (close
    above the Donchian band, full-day volume), while an all-day intraday check
    fires on spikes that fade by the close and reads a partial-volume ratio —
    a different trade distribution from the one that was validated. Exits are
    still evaluated all session."""
    now = datetime.now(ET)
    start = now.replace(hour=15, minute=30, second=0, microsecond=0)
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= now < close


def _sgx_entry_confirm_window() -> bool:
    """True in the last 30 min of the SGX session (17:00–17:30 SGT) — same
    close-confirmation rationale as the US window."""
    now = datetime.now(SGT)
    start = now.replace(hour=17, minute=0, second=0, microsecond=0)
    close = now.replace(hour=17, minute=30, second=0, microsecond=0)
    return start <= now < close


def _us_equity_sgd(sgd_to_usd: float) -> float:
    """Current equity of the shared US/crypto pool: the S$5k base plus realized
    P&L from closed trades (USD-priced symbols; SGX has its own pool). The
    backtest portfolio sim risks 1% of *current* equity — sizing off the fixed
    base never compounds and diverges from the simulated numbers."""
    try:
        rows = db.fetch(
            "SELECT shares, entry_price, exit_price FROM trades "
            "WHERE exit_price IS NOT NULL AND (strategy IS NULL OR strategy != 'SGX')"
        )
        realized_usd = sum(r["shares"] * (r["exit_price"] - r["entry_price"]) for r in rows)
        return PORTFOLIO_SIZE_SGD + (realized_usd / sgd_to_usd if sgd_to_usd else 0)
    except Exception as e:
        logging.warning(f"US equity calc failed, using fixed base: {e}")
        return PORTFOLIO_SIZE_SGD


def _sgx_equity_sgd() -> float:
    """Current equity of the SGX pool (prices already in SGD)."""
    try:
        rows = db.fetch(
            "SELECT shares, entry_price, exit_price FROM trades "
            "WHERE exit_price IS NOT NULL AND strategy = 'SGX'"
        )
        realized = sum(r["shares"] * (r["exit_price"] - r["entry_price"]) for r in rows)
        return SGX_PORTFOLIO_SGD + realized
    except Exception as e:
        logging.warning(f"SGX equity calc failed, using fixed base: {e}")
        return SGX_PORTFOLIO_SGD


def _us10k_equity_sgd(sgd_to_usd: float) -> float:
    """Compounding equity for the auto-logged S$10k US track.

    Same reasoning as _us_equity_sgd: the backtest risks 1% of *current* equity,
    so sizing off the fixed base would drift from the very numbers /algocheck
    compares this track against. Reads only paper_trades.
    """
    try:
        rows = db.fetch(
            "SELECT realized_pnl_usd FROM paper_trades WHERE track = ?", (US10K_TRACK,)
        )
        realized_usd = sum(r.get("realized_pnl_usd") or 0 for r in rows)
        return US10K_PORTFOLIO_SGD + (realized_usd / sgd_to_usd if sgd_to_usd else 0)
    except Exception as e:
        logging.warning(f"us10k equity calc failed, using fixed base: {e}")
        return US10K_PORTFOLIO_SGD


def _run_us10k_track(stock_data: list[dict], regime: dict, sgd_to_usd: float,
                     size_mult: float, rs_rank_map: dict[str, int],
                     entry_window: bool) -> None:
    """Auto-fill the S$10k US paper track from strategy D.

    Runs off the batch signal_watcher has already fetched, so it adds no network
    calls and cannot slow the alert path. Reads and writes ONLY paper_trades —
    the S$5k run's `trades` rows, its alerts and /portfolio are never touched,
    which is what lets this run alongside rather than on top.

    Entries confirm in the same close window the alert path uses (backtest
    parity). Exits are evaluated every pass — a stop that only checks near the
    close is not a stop.

    Silent by design: this track logs, it does not message. Telegram already
    carries the A/B/C/D alerts, and a second stream narrating simulated fills
    would be noise. /track and /algocheck are where it surfaces.
    """
    if not US10K_ENABLED:
        return

    try:
        open_rows = db.get_open_paper_trades(US10K_TRACK)
    except Exception as e:
        logging.warning(f"us10k: cannot read open trades, skipping pass: {e}")
        return

    position_map = {
        r["symbol"]: {
            "avg_cost": r["entry_price"],
            "shares": r["shares"],
            "entry_date": r.get("entry_date"),
            "stop_loss": r.get("stop_loss"),
            "profit_target": r.get("profit_target"),
        }
        for r in open_rows
    }
    by_symbol = {r["symbol"]: r for r in open_rows}
    equity_sgd = _us10k_equity_sgd(sgd_to_usd)
    today = datetime.now(SGT).strftime("%Y-%m-%d")

    for d in stock_data:
        symbol = d["symbol"]
        # crypto has its own watcher and pool; SGX codes aren't US symbols
        if "-USD" in symbol or symbol in SGX_WATCHLIST:
            continue

        position = position_map.get(symbol)
        if position is None and not entry_window:
            continue

        price = d["analysis"].get("price")
        if not price or price <= 0:
            continue

        rs_rank = _screener_rs_ranks.get(symbol, rs_rank_map.get(symbol))
        sector_ok = d["sector_status"].get("above_200sma") if d["sector_status"] else None

        try:
            sig = generate_signal(
                d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
                rs_rank=rs_rank, sector_ok=sector_ok, variant=US10K_VARIANT,
            )
        except Exception as e:
            logging.warning(f"us10k: signal failed for {symbol}: {e}")
            continue

        action = sig["action"]

        # ── Entry ──────────────────────────────────────────────────────────
        if position is None:
            if action != "BUY":
                continue
            size = calculate_position_size(
                equity_sgd, price, d["analysis"]["stop_loss"], sgd_to_usd, size_mult,
            )
            if not size or size["shares"] <= 0:
                continue
            try:
                db.open_paper_trade(
                    US10K_TRACK, symbol, US10K_VARIANT, size["shares"], today, price,
                    d["analysis"].get("stop_loss"), d["analysis"].get("profit_target"),
                    "; ".join(sig.get("reasons") or [])[:400],
                )
                logging.info(
                    f"us10k: OPEN {symbol} {size['shares']} @ ${price:.2f} "
                    f"stop ${d['analysis'].get('stop_loss') or 0:.2f}"
                )
            except Exception as e:
                logging.warning(f"us10k: open failed for {symbol}: {e}")
            continue

        # ── Exits ──────────────────────────────────────────────────────────
        row = by_symbol[symbol]
        try:
            if action == "SELL":
                db.close_paper_trade(row["id"], price, today,
                                     (sig.get("suggested_action") or "Exit signal")[:200])
                logging.info(f"us10k: CLOSE {symbol} @ ${price:.2f}")
                continue

            if action == "SELL_HALF" and not row.get("half_sold"):
                db.sell_half_paper_trade(row["id"], price)
                logging.info(f"us10k: HALF {symbol} @ ${price:.2f}")
                continue

            # Trailing stop on the half-sold remainder. Deliberately mirrors the
            # S$5k rule (arms off gain-from-entry, de-arms on retrace) rather
            # than crypto's persistent trail — US exit logic is frozen until the
            # 2026-09-01 check-in, and a track that exits differently would not
            # be measuring the strategy under review.
            if row.get("half_sold") == 1:
                prev_peak = row.get("peak_price") or row["entry_price"]
                peak = max(prev_peak, price)
                if peak > prev_peak:
                    db.update_paper_peak(row["id"], peak)
                gain = (price - row["entry_price"]) / row["entry_price"]
                if gain >= TRAILING_TRIGGER and price <= peak * (1 - TRAILING_STOP_PCT):
                    db.close_paper_trade(row["id"], price, today, "Trailing stop")
                    logging.info(f"us10k: TRAIL CLOSE {symbol} @ ${price:.2f}")
        except Exception as e:
            logging.warning(f"us10k: exit handling failed for {symbol}: {e}")


def _sector_note(symbol: str, market: str, new_value_sgd: float | None,
                 sgd_to_usd: float) -> str | None:
    """Warn when a BUY would push one sector past MAX_SECTOR_PCT of its pool.

    Advisory only — the signal, sizing and stops are untouched. MAX_SECTOR_PCT
    has always been a stated portfolio rule with nothing surfacing it at the
    moment of decision, which is the only moment it matters.

    Pools are kept separate (SGX has its own capital), and US position values
    convert to SGD so the comparison is against the same equity basis the
    sizer used. Returns None whenever it can't answer confidently — an
    unmapped ticker, an empty book, or a failed query must never block an alert.
    """
    if not new_value_sgd:
        return None
    sector = TICKER_SECTORS.get(symbol.replace(".SI", "").upper())
    if not sector:
        return None
    try:
        is_sgx = market == "SGX"
        scope = "strategy = 'SGX'" if is_sgx else "(strategy IS NULL OR strategy != 'SGX')"
        rows = db.fetch(
            f"SELECT symbol, shares, entry_price FROM trades WHERE exit_price IS NULL AND {scope}"
        )
        equity = _sgx_equity_sgd() if is_sgx else _us_equity_sgd(sgd_to_usd)
        if equity <= 0:
            return None

        held, exposure = [], 0.0
        for r in rows:
            sym = str(r["symbol"]).replace(".SI", "").upper()
            if TICKER_SECTORS.get(sym) != sector:
                continue
            value = r["shares"] * r["entry_price"]
            if not is_sgx and sgd_to_usd:
                value /= sgd_to_usd          # US trades are priced in USD
            exposure += value
            held.append(sym)

        pct = (exposure + new_value_sgd) / equity
        if pct <= MAX_SECTOR_PCT:
            return None
        tail = f"already open: {', '.join(sorted(set(held)))}" if held else "this position alone"
        return (f"⚠️ {sector} would be {pct * 100:.0f}% of the pool "
                f"(cap {MAX_SECTOR_PCT * 100:.0f}%) — {tail}")
    except Exception as e:
        logging.warning(f"Sector exposure check failed for {symbol}: {e}")
        return None


def _sgx_market_is_open() -> bool:
    now = datetime.now(SGT)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=9,  minute=0, second=0, microsecond=0)
    close_t = now.replace(hour=17, minute=30, second=0, microsecond=0)
    lunch_s = now.replace(hour=12, minute=0, second=0, microsecond=0)
    lunch_e = now.replace(hour=13, minute=0, second=0, microsecond=0)
    if lunch_s <= now < lunch_e:
        return False
    return open_t <= now < close_t


async def signal_watcher():
    await asyncio.sleep(30)  # let startup finish first
    while True:
        if not _market_is_open():
            secs = _seconds_until_next_open()
            logging.info(f"Market closed — sleeping {secs/3600:.1f}h until next open")
            await asyncio.sleep(secs)
            continue

        try:
            loop = asyncio.get_event_loop()
            regime = await loop.run_in_executor(None, get_market_regime)
            sgd_to_usd = regime.get("sgd_to_usd", 0.74)
            size_mult = regime.get("new_position_size_multiplier", 1.0)

            # Build position map from open DB trades (IBKR may be offline)
            open_rows = db.fetch(
                "SELECT symbol, shares, entry_price, entry_date, stop_loss, profit_target "
                "FROM trades WHERE exit_price IS NULL"
            )
            position_map: dict[str, dict] = {}
            crypto_open: list[str] = []
            for row in open_rows:
                sym = row["symbol"]
                if sym in SGX_WATCHLIST:
                    continue   # bare SGX codes aren't yfinance symbols; sgx_watcher owns them
                if "-USD" in sym:
                    # fetched for trailing-stop prices only — entry/exit signals for
                    # crypto come from crypto_watcher (BTC regime, BASELINE variant)
                    crypto_open.append(sym)
                    continue
                if sym not in position_map:
                    position_map[sym] = {"avg_cost": row["entry_price"], "shares": row["shares"],
                                         "entry_date": row.get("entry_date"),
                                         "stop_loss": row.get("stop_loss"),
                                         "profit_target": row.get("profit_target")}

            watchlist = db.get_watchlist()
            all_symbols = list(dict.fromkeys(watchlist + list(position_map.keys()) + crypto_open))

            stock_data = await _fetch_batch(all_symbols)
            rs_rank_map = _compute_rs_ranks(stock_data)
            batch_prices = {d["symbol"]: d["analysis"]["price"] for d in stock_data}

            # Pass 2: generate signals with RS rank + sector confirmation
            entry_window = _us_entry_confirm_window()
            for d in stock_data:
                symbol = d["symbol"]
                if "-USD" in symbol:
                    continue   # crypto: price fetched for trailing stops only
                position = position_map.get(symbol)
                # Entries confirm near the close only (backtest parity); exits
                # on held positions are evaluated all session
                if position is None and not entry_window:
                    continue
                # Prefer the wide screener-universe rank; watchlist-local rank is
                # a poor percentile with so few symbols
                rs_rank = _screener_rs_ranks.get(symbol, rs_rank_map.get(symbol))
                sector_ok = d["sector_status"].get("above_200sma") if d["sector_status"] else None

                signal = generate_signal(
                    d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
                    rs_rank=rs_rank, sector_ok=sector_ok,
                )
                action = signal["action"]
                prev = _last_signals.get(symbol)

                if action in ACTIONABLE and action != prev:
                    pos_size = None
                    if action == "BUY":
                        pos_size = calculate_position_size(
                            _us_equity_sgd(sgd_to_usd), d["analysis"]["price"],
                            d["analysis"]["stop_loss"], sgd_to_usd, size_mult,
                        )
                        # Auto-register stop and target price alerts
                        stop  = d["analysis"]["stop_loss"]
                        target = d["analysis"]["profit_target"]
                        db.add_price_alert(symbol, stop, "below", source="auto")
                        db.add_price_alert(symbol, target, "above", source="auto")
                        logging.info(f"Auto-alerts set for {symbol}: SL ${stop:.2f} / TP ${target:.2f}")

                    if action == "SELL_HALF":
                        trade = db.fetchone(
                            "SELECT id, half_sold, entry_price FROM trades WHERE symbol = ? AND exit_price IS NULL",
                            (symbol,)
                        )
                        if trade and not trade.get("half_sold"):
                            # Move stop to breakeven on the remaining half (matches backtest)
                            db.mutate(
                                "UPDATE trades SET half_sold = 1, peak_price = ?, stop_loss = ? WHERE id = ?",
                                (d["analysis"]["price"], trade["entry_price"], trade["id"])
                            )
                            db.mutate(
                                "UPDATE price_alerts SET target = ? WHERE symbol = ? AND direction = 'below'",
                                (trade["entry_price"], symbol)
                            )

                    msg = telegram_bot.format_signal(
                        symbol, signal, d["analysis"], pos_size, d["fundamentals"],
                        sector_note=_sector_note(
                            symbol, "US",
                            pos_size.get("position_value_sgd") if pos_size else None,
                            sgd_to_usd,
                        ),
                    )
                    _send_or_queue_us("A", symbol, action, signal["suggested_action"], msg,
                                      is_exit=position is not None)

                # Strategy B (Mean-Rev only) — alert only when it diverges from A
                sig_b = generate_signal(
                    d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
                    rs_rank=rs_rank, sector_ok=sector_ok, variant="MEAN_REV",
                )
                action_b = sig_b["action"]
                if action_b in ACTIONABLE and action_b != _last_signals_b.get(symbol) and action_b != action:
                    _send_or_queue_us(
                        "B", symbol, action_b, sig_b["suggested_action"],
                        f"📊 <b>[B] {symbol} — {action_b}</b>\n"
                        f"<code>Mean-Rev only</code>\n"
                        f"{sig_b['suggested_action']}",
                        is_exit=position is not None,
                    )
                _remember_signal(_last_signals_b, "B", symbol, action_b)

                # Strategy C (Mean-Rev, no RS rank filter) — alert only when diverges from A and B
                sig_c = generate_signal(
                    d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
                    rs_rank=rs_rank, sector_ok=sector_ok, variant="MEAN_REV_NO_RS",
                )
                action_c = sig_c["action"]
                if action_c in ACTIONABLE and action_c != _last_signals_c.get(symbol) and action_c != action and action_c != action_b:
                    _send_or_queue_us(
                        "C", symbol, action_c, sig_c["suggested_action"],
                        f"📊 <b>[C] {symbol} — {action_c}</b>\n"
                        f"<code>Mean-Rev · no RS filter</code>\n"
                        f"{sig_c['suggested_action']}",
                        is_exit=position is not None,
                    )
                _remember_signal(_last_signals_c, "C", symbol, action_c)

                # Strategy D (SWING_LOW_NOCAP: swing-low stop + no RSI ceiling) —
                # walk-forward winner, shadow-running since 2026-07-12.
                # Alert only when it diverges from A, B, and C.
                sig_d = generate_signal(
                    d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
                    rs_rank=rs_rank, sector_ok=sector_ok, variant="SWING_LOW_NOCAP",
                )
                action_d = sig_d["action"]
                if (action_d in ACTIONABLE and action_d != _last_signals_d.get(symbol)
                        and action_d not in (action, action_b, action_c)):
                    _send_or_queue_us(
                        "D", symbol, action_d, sig_d["suggested_action"],
                        f"📊 <b>[D] {symbol} — {action_d}</b>\n"
                        f"<code>Swing-low stop · no RSI ceiling</code>\n"
                        f"{sig_d['suggested_action']}",
                        is_exit=position is not None,
                    )
                _remember_signal(_last_signals_d, "D", symbol, action_d)

                _remember_signal(_last_signals, "A", symbol, action)

            # Auto-logged S$10k US track. Isolated in its own table and wrapped
            # so a failure here can never take down the alert path above.
            try:
                _run_us10k_track(stock_data, regime, sgd_to_usd, size_mult,
                                 rs_rank_map, entry_window)
            except Exception as e:
                logging.warning(f"us10k track pass failed: {e}")

            # Trailing stop check for half-sold positions
            half_sold_trades = db.fetch(
                "SELECT * FROM trades WHERE exit_price IS NULL AND half_sold = 1"
            )
            for trade in half_sold_trades:
                sym = trade["symbol"]
                ep = trade["entry_price"]
                peak = trade["peak_price"] or ep
                current = batch_prices.get(sym)
                if current is None:
                    continue
                new_peak = max(peak, current)
                if new_peak != peak:
                    db.mutate(
                        "UPDATE trades SET peak_price = ? WHERE id = ?",
                        (new_peak, trade["id"])
                    )
                gain = (current - ep) / ep
                trail_stop = new_peak * (1 - TRAILING_STOP_PCT)
                # Crypto arms off PEAK gain and stays armed — the de-arm-on-retrace
                # behavior round-trips crypto winners to breakeven (backtest: fixing
                # it adds ~0.4pp CAGR and cuts drawdown). US keeps the old behavior
                # until the 2026-09-01 A/B/C/D check-in; changing it now would alter
                # frozen exit logic mid-validation.
                arm_gain = (new_peak - ep) / ep if sym in CRYPTO_WATCHLIST else gain
                if arm_gain >= TRAILING_TRIGGER and current <= trail_stop:
                    flag = telegram_bot.MARKET_ICON["CRYPTO" if sym in CRYPTO_WATCHLIST else "US"]
                    telegram_bot.send(
                        f"🔔 <b>Trail Stop — {sym}</b> · {flag}\n\n"
                        f"<code>"
                        f"Price    ${current:.2f}\n"
                        f"Trail    ${trail_stop:.2f}  (10% below peak)\n"
                        f"Peak     ${new_peak:.2f}\n"
                        f"Entry    ${ep:.2f}  ({gain*100:.1f}% gain)"
                        f"</code>\n\n"
                        f"Close remaining half."
                    )
                    _log_event("US", f"Trail stop {sym} — close remaining half at ${current:.2f}", sent=True)
                    db.mutate("UPDATE trades SET half_sold = 2 WHERE id = ?", (trade["id"],))

            # Price alerts — first drop stale auto alerts from untaken BUY signals
            pruned = db.prune_stale_auto_alerts()
            if pruned:
                logging.info(f"Pruned {pruned} stale auto price alerts")
            for alert in db.get_price_alerts():
                analysis = await loop.run_in_executor(None, get_ticker_analysis, alert["symbol"])
                if not analysis:
                    continue
                current = analysis["price"]
                hit = (
                    (alert["direction"] == "above" and current >= alert["target"]) or
                    (alert["direction"] == "below" and current <= alert["target"])
                )
                if hit:
                    arrow = "↑" if alert["direction"] == "above" else "↓"
                    alert_market = telegram_bot.market_of(alert["symbol"])
                    money = telegram_bot.money_for(alert_market)
                    display = alert["symbol"].replace(".SI", "")
                    telegram_bot.send(
                        f"🔔 <b>Price Alert — {display}</b> · {telegram_bot.MARKET_ICON[alert_market]}\n\n"
                        f"<code>"
                        f"Target   {money(alert['target'])}  {arrow}\n"
                        f"Current  {money(current)}"
                        f"</code>"
                    )
                    if alert["symbol"] not in CRYPTO_WATCHLIST:
                        market = "SGX" if alert["symbol"].endswith(".SI") else "US"
                        _log_event(market, f"Price alert {alert['symbol']} {arrow} ${alert['target']:.2f} (now ${current:.2f})", sent=True)
                    db.remove_price_alert(alert["id"])

            _beat("us_scan")
            if entry_window:
                _beat("us_entry_scan")

        except Exception as e:
            logging.warning(f"Signal watcher error: {e}")

        await asyncio.sleep(5 * 60)  # check every 5 minutes


async def _build_crypto_snapshot(regime: dict, sgd_to_usd: float) -> list[dict]:
    """Fetch signals for all crypto watchlist coins + any open crypto positions."""
    open_rows = db.fetch(
        "SELECT symbol, shares, entry_price, entry_date, stop_loss, profit_target "
        "FROM trades WHERE exit_price IS NULL"
    )
    crypto_positions = {
        row["symbol"]: {
            "avg_cost": row["entry_price"],
            "shares": row["shares"],
            "entry_date": row.get("entry_date"),
            "stop_loss": row.get("stop_loss"),
            "profit_target": row.get("profit_target"),
        }
        for row in open_rows if row["symbol"] in CRYPTO_WATCHLIST
    }

    all_crypto = list(dict.fromkeys(CRYPTO_WATCHLIST + list(crypto_positions.keys())))
    stock_data = await _fetch_batch(all_crypto)

    rows = []
    for d in stock_data:
        symbol = d["symbol"]
        position = crypto_positions.get(symbol)
        # BASELINE since 2026-07-13: drops the 20/50 SMA alignment gate —
        # +5.0% vs +4.2% CAGR over COMBINED, consistent across 5 backtest configs
        signal = generate_signal(
            d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
            rs_rank=None, sector_ok=None, variant="BASELINE",
        )
        action = signal["action"]
        pos_size = None
        if action == "BUY" and not position:
            price = d["analysis"]["price"]
            stop = d["analysis"]["stop_loss"]
            # Risk-based sizing like stocks (1% of portfolio at the stop),
            # capped at the crypto allocation limit
            dist = price - stop
            risk_usd = _us_equity_sgd(sgd_to_usd) * sgd_to_usd * RISK_PER_TRADE_PCT
            cap_usd = CRYPTO_POSITION_SGD * sgd_to_usd
            value_usd = min(risk_usd / dist * price, cap_usd) if dist > 0 else cap_usd
            qty = round(value_usd / price, 6)
            actual_risk_sgd = round(qty * dist / sgd_to_usd, 2) if dist > 0 else None
            pos_size = {
                "shares": qty,
                "position_value_sgd": round(value_usd / sgd_to_usd, 2),
                "position_value_usd": round(value_usd, 2),
                "risk_sgd": actual_risk_sgd,
                "note": f"Risk-based (1% at stop), capped at S${CRYPTO_POSITION_SGD}",
            }
        rows.append({
            "symbol": symbol,
            "action": action,
            "signal": signal,
            "analysis": d["analysis"],
            "fundamentals": d["fundamentals"],
            "position": position,
            "pos_size": pos_size,
        })
    return rows


async def crypto_watcher():
    """4-hour digest for all crypto coins. Immediate alert on SELL/stop-loss for held positions."""
    await asyncio.sleep(45)
    loop = asyncio.get_event_loop()
    cycle = 0
    DIGEST_EVERY = 8  # 8 × 30 min = 4 hours

    while True:
        try:
            regime = await loop.run_in_executor(None, get_market_regime)
            # Crypto entries gate on BTC's own 200 SMA, not the S&P's
            regime = await loop.run_in_executor(None, get_crypto_regime, regime)
            sgd_to_usd = regime.get("sgd_to_usd", 0.74)
            rows = await _build_crypto_snapshot(regime, sgd_to_usd)

            for r in rows:
                symbol = r["symbol"]
                action = r["action"]
                prev   = _last_crypto_signals.get(symbol)

                if action != prev:
                    # Immediate alert: new BUY signal
                    if action == "BUY" and not r["position"]:
                        msg = telegram_bot.format_signal(
                            symbol, r["signal"], r["analysis"], r["pos_size"], r["fundamentals"],
                            market="CRYPTO",
                        )
                        telegram_bot.send(msg)
                        stop   = r["analysis"]["stop_loss"]
                        target = r["analysis"]["profit_target"]
                        db.add_price_alert(symbol, stop, "below", source="auto")
                        db.add_price_alert(symbol, target, "above", source="auto")

                    # Immediate alert: exit signal on a held position — don't wait for digest
                    elif r["position"] and action in ("SELL", "SELL_HALF", "REVIEW"):
                        msg = telegram_bot.format_signal(
                            symbol, r["signal"], r["analysis"], None, r["fundamentals"],
                            market="CRYPTO",
                        )
                        telegram_bot.send(msg)

                _remember_signal(_last_crypto_signals, "CRYPTO", symbol, action)

            # Every 4 hours: send full digest
            cycle += 1
            if cycle >= DIGEST_EVERY:
                cycle = 0
                telegram_bot.send(telegram_bot.format_crypto_digest(rows, sgd_to_usd))

            _beat("crypto_scan")

        except Exception as e:
            logging.warning(f"Crypto watcher error: {e}")

        await asyncio.sleep(30 * 60)


async def sgx_watcher():
    """Auto-executes SGX signals via Futu OpenD (paper by default).
    Runs every 5 min during SGX market hours (9:00–12:00, 13:00–17:30 SGT).
    Position sizing is cash-only — never exceeds available cash balance."""
    await asyncio.sleep(60)
    while True:
        if not _sgx_market_is_open():
            await asyncio.sleep(5 * 60)
            continue

        try:
            loop = asyncio.get_event_loop()
            regime = await loop.run_in_executor(None, get_market_regime)
            # SGX entries gate on the STI's own 200 SMA, not the S&P's
            regime = await loop.run_in_executor(None, get_sgx_regime, regime)

            open_rows = db.fetch(
                "SELECT symbol, shares, entry_price, entry_date, stop_loss, profit_target "
                "FROM trades WHERE exit_price IS NULL"
            )
            sgx_position_map = {
                r["symbol"]: {"avg_cost": r["entry_price"], "shares": r["shares"],
                              "entry_date": r.get("entry_date"),
                              "stop_loss": r.get("stop_loss"),
                              "profit_target": r.get("profit_target")}
                for r in open_rows if r["symbol"] in SGX_WATCHLIST
            }

            yf_symbols = [s + ".SI" for s in SGX_WATCHLIST]
            sgx_data = await _fetch_batch(yf_symbols)

            entry_window = _sgx_entry_confirm_window()
            for d in sgx_data:
                yf_sym   = d["symbol"]
                symbol   = yf_sym.replace(".SI", "")
                position = sgx_position_map.get(symbol)
                # Entries confirm in the last 30 min only (backtest enters on the
                # completed daily bar); exits on held positions run all session
                if position is None and not entry_window:
                    continue
                # SWING_LOW_NOCAP since 2026-07-13: on the 27-name universe the
                # swing-low-stop geometry backtests at +6.5% CAGR vs +0.3% for
                # COMBINED on the old 8-name list (see backtest.py runs)
                signal   = generate_signal(d["analysis"], position, regime,
                                           variant="SWING_LOW_NOCAP")
                action   = signal["action"]
                prev     = _last_sgx_signals.get(symbol)
                price    = d["analysis"]["price"]
                # Variant D anchors exits at the structural swing low, not ATR
                sgx_stop   = d["analysis"].get("stop_loss_swing") or d["analysis"]["stop_loss"]
                sgx_target = d["analysis"].get("profit_target_swing") or d["analysis"]["profit_target"]

                if action in ACTIONABLE and action != prev:
                    order_note = ""
                    # Sizing computed here so the alert shows it even when Futu
                    # is down; the order path below re-caps by actual cash
                    msg_pos = None
                    if action == "BUY" and not position:
                        risk_per_share = price - sgx_stop
                        if risk_per_share > 0:
                            # Risk 1% of current SGX equity (base + realized P&L)
                            # so sizing compounds like the backtest portfolio sim
                            sgx_equity = _sgx_equity_sgd()
                            risk_sgd = sgx_equity * RISK_PER_TRADE_PCT
                            qty = max(1, int(risk_sgd / risk_per_share))
                            qty = min(qty, int(sgx_equity * MAX_POSITION_PCT / price))
                            if qty > 0:
                                msg_pos = {"shares": qty,
                                           "position_value_sgd": qty * price,
                                           "risk_sgd": risk_sgd}
                    if futu_broker.is_available():
                        tag = " [PAPER]" if futu_broker.is_paper() else ""
                        if action == "BUY" and not position:
                            if msg_pos:
                                qty = int(msg_pos["shares"])
                                # Cash-only: cap at 95% of available cash balance
                                acct = futu_broker.get_account_summary()
                                if acct["cash"]:
                                    qty = min(qty, int(acct["cash"] * 0.95 / price))
                                if qty > 0:
                                    order_id = futu_broker.place_limit_order(symbol, qty, "BUY", price)
                                    order_note = f"\n🤖 Auto-order: BUY {qty} @ S${price:.3f}{tag}"
                                    if order_id:
                                        # Entry == current price here, so the analysis
                                        # stop/target are valid entry-anchored levels
                                        db.mutate(
                                            "INSERT INTO trades (symbol, shares, entry_date, entry_price, signal_reason, notes, strategy, stop_loss, profit_target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                            (symbol, qty, datetime.today().strftime("%Y-%m-%d"), price,
                                             "SGX Auto-BUY", f"Futu order {order_id}", "SGX",
                                             sgx_stop, sgx_target),
                                        )
                                        db.add_price_alert(yf_sym, sgx_stop,   "below", source="trade")
                                        db.add_price_alert(yf_sym, sgx_target, "above", source="trade")

                        elif action == "SELL" and position:
                            qty = int(position["shares"])
                            if qty > 0:
                                order_id = futu_broker.place_limit_order(symbol, qty, "SELL", price)
                                order_note = f"\n🤖 Auto-order: SELL {qty} @ S${price:.3f}{tag}"
                                if order_id:
                                    # Close the DB row — leaving it open kept the
                                    # position "held" forever and could re-fire a
                                    # SELL for shares no longer owned
                                    db.mutate(
                                        "UPDATE trades SET exit_price = ?, exit_date = ? "
                                        "WHERE symbol = ? AND exit_price IS NULL",
                                        (price, datetime.today().strftime("%Y-%m-%d"), symbol),
                                    )
                                    db.remove_trade_alerts(yf_sym)

                        elif action == "SELL_HALF" and position:
                            trade = db.fetchone(
                                "SELECT id, shares, entry_price, entry_date, half_sold "
                                "FROM trades WHERE symbol = ? AND exit_price IS NULL",
                                (symbol,),
                            )
                            qty = int(trade["shares"] * 0.5) if trade and not trade.get("half_sold") else 0
                            if qty > 0:
                                order_id = futu_broker.place_limit_order(symbol, qty, "SELL", price)
                                order_note = f"\n🤖 Auto-order: SELL {qty} @ S${price:.3f}{tag}"
                                if order_id:
                                    today_str = datetime.today().strftime("%Y-%m-%d")
                                    # Record the sold half as a closed row so realized
                                    # P&L is preserved, then keep the remainder open
                                    # with half_sold=1 + breakeven stop (matches the
                                    # US flow and the backtest exit sequence)
                                    db.mutate(
                                        "INSERT INTO trades (symbol, shares, entry_date, entry_price, exit_date, exit_price, signal_reason, strategy) "
                                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                        (symbol, qty, trade["entry_date"], trade["entry_price"],
                                         today_str, price, "SGX Auto-SELL_HALF", "SGX"),
                                    )
                                    db.mutate(
                                        "UPDATE trades SET shares = shares - ?, half_sold = 1, "
                                        "peak_price = ?, stop_loss = ? WHERE id = ?",
                                        (qty, price, trade["entry_price"], trade["id"]),
                                    )
                                    db.mutate(
                                        "UPDATE price_alerts SET target = ? WHERE symbol = ? AND direction = 'below'",
                                        (trade["entry_price"], yf_sym),
                                    )

                    # Signal-vs-fill log: the SGX backtest edge dies at 0.5%
                    # slippage/leg, so every order-type alert records its signal
                    # price for /fill to report the real moomoo fill against.
                    # REVIEW is actionable but isn't an order — nothing to fill.
                    if action in ("BUY", "SELL", "SELL_HALF"):
                        try:
                            if action == "BUY":
                                fill_qty = msg_pos["shares"] if msg_pos else None
                            elif action == "SELL_HALF" and position:
                                fill_qty = int(position["shares"] * 0.5)
                            else:
                                fill_qty = position["shares"] if position else None
                            db.add_pending_fill(symbol, action, price, fill_qty, sgx_stop, sgx_target,
                                                datetime.now(SGT).isoformat(timespec="seconds"))
                            if not futu_broker.is_available():
                                order_note += f"\n📝 After placing in moomoo, reply: /fill {symbol} &lt;fill price&gt;"
                        except Exception as e:
                            logging.warning(f"SGX pending-fill log failed for {symbol}: {e}")

                    msg = telegram_bot.format_signal(symbol, signal, d["analysis"], msg_pos, d["fundamentals"],
                                                     stop=sgx_stop, target=sgx_target, market="SGX",
                                                     sector_note=_sector_note(
                                                         symbol, "SGX",
                                                         msg_pos.get("position_value_sgd") if msg_pos else None,
                                                         regime.get("sgd_to_usd", 0.74),
                                                     ))
                    telegram_bot.send(msg + order_note)
                    _log_event("SGX", f"{action} {symbol} @ S${price:.3f} — {signal['suggested_action']}", sent=True)

                _remember_signal(_last_sgx_signals, "SGX", symbol, action)

            # Trailing stop on half-sold SGX positions — was only wired for US
            # trades, so the remaining half of an SGX winner had no exit manager.
            # Arms on current gain (non-persistent), matching the backtest
            # geometry variant D was validated with.
            sgx_prices = {d["symbol"].replace(".SI", ""): d["analysis"]["price"]
                          for d in sgx_data}
            half_rows = db.fetch(
                "SELECT * FROM trades WHERE exit_price IS NULL AND half_sold = 1 AND strategy = 'SGX'"
            )
            for trade in half_rows:
                sym = trade["symbol"]
                current = sgx_prices.get(sym)
                if current is None:
                    continue
                ep = trade["entry_price"]
                peak = trade["peak_price"] or ep
                new_peak = max(peak, current)
                if new_peak != peak:
                    db.mutate("UPDATE trades SET peak_price = ? WHERE id = ?",
                              (new_peak, trade["id"]))
                gain = (current - ep) / ep
                trail_stop = new_peak * (1 - TRAILING_STOP_PCT)
                if gain >= TRAILING_TRIGGER and current <= trail_stop:
                    qty = int(trade["shares"])
                    order_note = ""
                    if futu_broker.is_available() and qty > 0:
                        order_id = futu_broker.place_limit_order(sym, qty, "SELL", current)
                        if order_id:
                            tag = " [PAPER]" if futu_broker.is_paper() else ""
                            order_note = f"\n🤖 Auto-order: SELL {qty} @ S${current:.3f}{tag}"
                            db.mutate(
                                "UPDATE trades SET exit_price = ?, exit_date = ? WHERE id = ?",
                                (current, datetime.today().strftime("%Y-%m-%d"), trade["id"]),
                            )
                            db.remove_trade_alerts(sym + ".SI")
                    fill_note = ""
                    try:
                        db.add_pending_fill(sym, "SELL", current, qty, None, None,
                                            datetime.now(SGT).isoformat(timespec="seconds"))
                        if not order_note:
                            fill_note = f"\n📝 After placing in moomoo, reply: /fill {sym} &lt;fill price&gt;"
                    except Exception as e:
                        logging.warning(f"SGX pending-fill log failed for {sym}: {e}")
                    telegram_bot.send(
                        f"🔔 <b>Trail Stop — {sym}</b> · 🇸🇬\n\n"
                        f"<code>"
                        f"Price    S${current:.3f}\n"
                        f"Trail    S${trail_stop:.3f}  (10% below peak)\n"
                        f"Peak     S${new_peak:.3f}\n"
                        f"Entry    S${ep:.3f}  ({gain*100:.1f}% gain)"
                        f"</code>\n\n"
                        f"Close remaining half.{order_note}{fill_note}"
                    )
                    _log_event("SGX", f"Trail stop {sym} — close remaining half at S${current:.3f}", sent=True)
                    if not order_note:
                        # Futu down / order rejected — mark alerted (half_sold=2,
                        # same as the US flow) so this doesn't re-fire every cycle
                        db.mutate("UPDATE trades SET half_sold = 2 WHERE id = ?", (trade["id"],))

            _beat("sgx_scan")
            if entry_window:
                _beat("sgx_entry_scan")

        except Exception as e:
            logging.warning(f"SGX watcher error: {e}")

        # 15 min matches the analysis cache TTL — with 27 symbols a 5-min loop
        # would only re-read cached data anyway while tripling yfinance load
        await asyncio.sleep(15 * 60)


async def news_watcher():
    """Zero-cost news correlation: pairs notable price moves with free yfinance
    headlines. Checks every 5 min (cheap — just price data) but only fetches and
    stores headlines once per ticker per calendar day, the first time it crosses
    NEWS_MOVE_THRESHOLD_PCT, so a mover that stays elevated all day doesn't
    produce duplicate digest rows."""
    await asyncio.sleep(90)
    while True:
        try:
            loop = asyncio.get_event_loop()
            symbol_pairs: list[tuple[str, str]] = []
            if _market_is_open():
                symbol_pairs += [(s, s) for s in db.get_watchlist()]
            if _sgx_market_is_open():
                symbol_pairs += [(s, f"{s}.SI") for s in SGX_WATCHLIST]

            today = datetime.today().strftime("%Y-%m-%d")
            for display_symbol, yf_symbol in symbol_pairs:
                if db.has_news_digest(display_symbol, today):
                    continue
                result = await loop.run_in_executor(None, news.get_daily_pct_change, yf_symbol)
                if result is None:
                    continue
                pct_change, price = result
                if abs(pct_change) < NEWS_MOVE_THRESHOLD_PCT:
                    continue
                headlines = await loop.run_in_executor(
                    None, news.get_recent_news, yf_symbol, NEWS_HEADLINES_PER_TICKER
                )
                if not headlines:
                    continue
                db.add_news_digest(display_symbol, today, pct_change, price, json.dumps(headlines))
                detected_at = datetime.now(SGT).strftime("%H:%M SGT")
                telegram_bot.send(telegram_bot.format_news_alert(
                    display_symbol, pct_change, price, headlines, detected_at
                ))
                logging.info(f"News digest stored for {display_symbol}: {pct_change:+.1f}%")
            _beat("news_scan")
        except Exception as e:
            logging.warning(f"News watcher error: {e}")

        await asyncio.sleep(5 * 60)


class WatchlistUpdate(BaseModel):
    symbols: list[str]


class PriceAlertIn(BaseModel):
    symbol: str
    target: float
    direction: str | None = None   # 'above' | 'below' — inferred from current price if omitted


class TradeIn(BaseModel):
    symbol: str
    shares: float
    entry_date: str
    entry_price: float
    signal_reason: str | None = None
    notes: str | None = None
    strategy: str = "A"


class TradeClose(BaseModel):
    exit_date: str
    exit_price: float
    notes: str | None = None


class OptionsTradeIn(BaseModel):
    symbol: str
    strategy: str
    phase: int = 1
    strike: float                       # short strike for spreads
    long_strike: float | None = None    # set for two-leg spreads only
    expiry_date: str
    dte_at_entry: int | None = None
    premium: float                      # net credit per contract for spreads
    contracts: int = 1
    open_date: str
    notes: str | None = None


class OptionsTradeClose(BaseModel):
    close_date: str
    close_premium: float   # 0 if expired worthless, >0 if closed early
    status: str            # 'expired' | 'closed' | 'assigned'
    notes: str | None = None


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _slippage_verdict(mean_slip: float, n: int) -> str:
    # Decision thresholds from the 2026-07-14 cost-reality backtest: the SGX
    # edge is −1.5% CAGR at 0.5% slippage/leg; ~0.2% is the go/no-go line
    if n < 5:
        return f"📊 {n} fill(s) — need ~5+ before trusting the average."
    if mean_slip > 0.2:
        return ("❌ Mean slippage over the 0.2% threshold — SGX edge likely gone. "
                "Cut universe to liquid names (D05/O39/U11/Z74/C6L) or shelve SGX.")
    if mean_slip > 0.1:
        return "⚠️ Mean slippage 0.1–0.2% — edge thinning, keep measuring."
    return "✓ Mean slippage under 0.1% — execution cost is not killing the edge so far."


def _format_slippage_report(fills: list[dict]) -> str:
    if not fills:
        return ("🇸🇬 <b>SGX Fill Slippage</b>\n\nNo fills recorded yet. When an SGX order "
                "alert fires, place the paper trade in moomoo and reply:\n"
                "<code>/fill D05 33.45 [qty]</code>")
    slips = [f["slippage_pct"] for f in fills]
    mean_slip = sum(slips) / len(slips)
    worst = max(slips)
    lines = [
        "🇸🇬 <b>SGX Fill Slippage</b>  (adverse = positive)",
        "",
        f"<code>Fills   {len(slips)}\n"
        f"Mean    {mean_slip:+.2f}%\n"
        f"Median  {_median(slips):+.2f}%\n"
        f"Worst   {worst:+.2f}%</code>",
        "",
    ]
    by_sym: dict[str, list[float]] = {}
    for f in fills:
        by_sym.setdefault(f["symbol"], []).append(f["slippage_pct"])
    for sym in sorted(by_sym):
        s = by_sym[sym]
        lines.append(f"<code>{sym:<5} n={len(s)}  mean {sum(s)/len(s):+.2f}%</code>")
    lines += ["", _slippage_verdict(mean_slip, len(slips))]
    return "\n".join(lines)


async def _record_sgx_fill(symbol: str, fill_price: float, fill_qty: float | None):
    """Match a reported moomoo fill to the latest pending SGX signal, log the
    slippage, and do the trade bookkeeping the Futu auto-path would have done
    (it never runs on Render — OpenD is a desktop app on localhost)."""
    pending = db.get_pending_fill(symbol)
    if not pending:
        telegram_bot.send(
            f"❌ No unfilled SGX order alert for <b>{symbol}</b>.\n"
            "/fill matches the most recent alert that hasn't been reported yet."
        )
        return

    side = pending["side"]
    signal_price = pending["signal_price"]
    # Adverse-positive: paying up on a BUY or getting less on a SELL is positive
    if side == "BUY":
        slip = (fill_price - signal_price) / signal_price * 100
    else:
        slip = (signal_price - fill_price) / signal_price * 100

    now_sgt = datetime.now(SGT)
    qty = fill_qty if fill_qty is not None else pending.get("signal_qty")
    db.record_fill(pending["id"], fill_price, qty, round(slip, 4),
                   now_sgt.isoformat(timespec="seconds"))

    stale_note = ""
    try:
        age = now_sgt - datetime.fromisoformat(pending["signal_ts"])
        if age > timedelta(days=2):
            stale_note = f"\n⚠️ Signal was {age.days}d ago — matched the right alert?"
    except (ValueError, TypeError):
        pass

    yf_sym = symbol + ".SI"
    today_str = now_sgt.strftime("%Y-%m-%d")
    book_note = ""
    if side == "BUY":
        if qty:
            db.mutate(
                "INSERT INTO trades (symbol, shares, entry_date, entry_price, signal_reason, notes, strategy, stop_loss, profit_target) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, int(qty), today_str, fill_price, "SGX Manual-BUY (paper)",
                 f"/fill vs signal S${signal_price:.3f}", "SGX",
                 pending.get("stop_loss"), pending.get("profit_target")),
            )
            if pending.get("stop_loss"):
                db.add_price_alert(yf_sym, pending["stop_loss"], "below", source="trade")
            if pending.get("profit_target"):
                db.add_price_alert(yf_sym, pending["profit_target"], "above", source="trade")
            book_note = f"\n📋 Open trade logged: {int(qty)} {symbol} @ S${fill_price:.3f}"
        else:
            book_note = (f"\n⚠️ No qty given and none suggested — trade row NOT created. "
                         f"Log it with qty: /fill {symbol} {fill_price} 100")
    elif side == "SELL":
        db.mutate(
            "UPDATE trades SET exit_price = ?, exit_date = ? WHERE symbol = ? AND exit_price IS NULL",
            (fill_price, today_str, symbol),
        )
        db.remove_trade_alerts(yf_sym)
        book_note = f"\n📋 Closed {symbol} @ S${fill_price:.3f}"
    elif side == "SELL_HALF":
        trade = db.fetchone(
            "SELECT id, shares, entry_price, entry_date, half_sold "
            "FROM trades WHERE symbol = ? AND exit_price IS NULL",
            (symbol,),
        )
        if trade and not trade.get("half_sold"):
            sell_qty = int(qty) if qty else int(trade["shares"] * 0.5)
            db.mutate(
                "INSERT INTO trades (symbol, shares, entry_date, entry_price, exit_date, exit_price, signal_reason, strategy) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, sell_qty, trade["entry_date"], trade["entry_price"],
                 today_str, fill_price, "SGX Manual-SELL_HALF (paper)", "SGX"),
            )
            db.mutate(
                "UPDATE trades SET shares = shares - ?, half_sold = 1, "
                "peak_price = ?, stop_loss = ? WHERE id = ?",
                (sell_qty, fill_price, trade["entry_price"], trade["id"]),
            )
            db.mutate(
                "UPDATE price_alerts SET target = ? WHERE symbol = ? AND direction = 'below'",
                (trade["entry_price"], yf_sym),
            )
            book_note = (f"\n📋 Sold half ({sell_qty}) @ S${fill_price:.3f}; "
                         f"remainder on breakeven stop S${trade['entry_price']:.3f}")
        else:
            book_note = "\n⚠️ No un-halved open trade found — bookkeeping skipped."

    fills = db.get_recorded_fills()
    slips = [f["slippage_pct"] for f in fills]
    mean_slip = sum(slips) / len(slips)
    telegram_bot.send(
        f"✅ <b>Fill Logged — {side} {symbol}</b> · 🇸🇬\n\n"
        f"<code>"
        f"Signal  S${signal_price:.3f}\n"
        f"Fill    S${fill_price:.3f}\n"
        f"Slip    {slip:+.2f}%  (adverse = positive)"
        f"</code>\n\n"
        f"Running: n={len(slips)}, mean {mean_slip:+.2f}%, median {_median(slips):+.2f}%\n"
        f"{_slippage_verdict(mean_slip, len(slips))}"
        f"{book_note}{stale_note}"
    )


def _fmt_age(td: timedelta) -> str:
    mins = int(td.total_seconds() // 60)
    if mins < 60:
        return f"{mins}m"
    if mins < 60 * 24:
        return f"{mins // 60}h {mins % 60:02d}m"
    return f"{mins // (60 * 24)}d {(mins % (60 * 24)) // 60}h"


def _trade_group(t: dict) -> str:
    return "SGX" if (t.get("strategy") == "SGX" or str(t["symbol"]).endswith(".SI")) else "US"


async def _portfolio_snapshot() -> dict:
    """Open positions with live prices plus realized/unrealized P&L, split by
    currency (US trades are in USD, SGX trades in SGD) so totals never mix."""
    loop = asyncio.get_event_loop()
    rows = [dict(r) for r in db.fetch("SELECT * FROM trades ORDER BY entry_date DESC")]
    positions = []
    realized = {"US": 0.0, "SGX": 0.0}
    unrealized = {"US": 0.0, "SGX": 0.0}
    for t in rows:
        group = _trade_group(t)
        if t["exit_price"] is not None:
            realized[group] += (t["exit_price"] - t["entry_price"]) * t["shares"]
            continue
        yf_sym = t["symbol"] if str(t["symbol"]).endswith(".SI") or group == "US" else t["symbol"] + ".SI"
        analysis = await loop.run_in_executor(None, get_ticker_analysis, yf_sym)
        current = analysis["price"] if analysis else None
        pnl_pct = None
        if current:
            unrealized[group] += (current - t["entry_price"]) * t["shares"]
            pnl_pct = ((current - t["entry_price"]) / t["entry_price"]) * 100
        positions.append({**t, "group": group, "current": current, "pnl_pct": pnl_pct})
    return {"positions": positions, "realized": realized, "unrealized": unrealized,
            "n_closed": sum(1 for t in rows if t["exit_price"] is not None)}


async def _send_portfolio():
    snap = await _portfolio_snapshot()
    positions = snap["positions"]
    if not positions and not snap["n_closed"]:
        telegram_bot.send("💼 <b>Portfolio</b>\n\nNo trades logged yet — the first SGX BUY /fill will open one.")
        return

    lines = ["💼 <b>Portfolio</b>", ""]
    if positions:
        lines.append(f"📋 <b>Open ({len(positions)})</b>")
        pos_rows = []
        for t in positions:
            money = telegram_bot.money_for(t["group"])
            flag = telegram_bot.MARKET_ICON.get(t["group"], "")
            if t["current"] is not None:
                sign = "+" if t["pnl_pct"] >= 0 else ""
                pos_rows.append(f"{t['symbol']:<6} {t['shares']:g}sh  in {money(t['entry_price'])}"
                                f"  now {money(t['current'])}  {sign}{t['pnl_pct']:.1f}% {flag}")
            else:
                pos_rows.append(f"{t['symbol']:<6} {t['shares']:g}sh  in {money(t['entry_price'])}  (no live price) {flag}")
        lines.append("<code>" + "\n".join(pos_rows) + "</code>")
    else:
        lines.append("📋 <b>Open</b>  none")

    lines += ["", "💰 <b>P&L</b>"]
    pnl_rows = []
    for group, cur, dp in (("US", "$", 2), ("SGX", "S$", 3)):
        r, u = snap["realized"][group], snap["unrealized"][group]
        if r == 0 and u == 0 and not any(p["group"] == group for p in positions):
            continue
        for label, v in (("Realized", r), ("Unrealized", u), ("Total", r + u)):
            sign = "+" if v >= 0 else "-"
            pnl_rows.append(f"{group:<4} {label:<11}{sign}{cur}{abs(v):,.{dp}f}")
    lines.append("<code>" + "\n".join(pnl_rows) + "</code>" if pnl_rows else "<code>Nothing realized yet</code>")
    telegram_bot.send("\n".join(lines))


async def _us10k_snapshot() -> dict:
    """Live state of the auto-logged S$10k US track.

    Realized P&L sums realized_pnl_usd across ALL rows, not just closed ones —
    a half-sold position books its first leg while still open, so filtering to
    closed rows would under-report it.
    """
    loop = asyncio.get_event_loop()
    # One query, then split in Python. db.get_conn() opens a fresh Postgres
    # connection per call and Neon's handshake dominates this endpoint, so
    # three convenience queries cost far more than the filtering they save.
    all_rows = db.get_all_paper_trades(US10K_TRACK)
    open_rows = [r for r in all_rows if r.get("exit_price") is None]
    closed_rows = [r for r in all_rows if r.get("exit_price") is not None]

    async def _price(sym: str):
        try:
            a = await loop.run_in_executor(None, get_ticker_analysis, sym)
            return a["price"] if a else None
        except Exception:
            return None

    prices: dict[str, float | None] = {}
    if open_rows:
        syms = [r["symbol"] for r in open_rows]
        got = await asyncio.gather(*[_price(s) for s in syms], return_exceptions=True)
        prices = {s: (p if isinstance(p, (int, float)) else None) for s, p in zip(syms, got)}

    realized_usd = sum((r.get("realized_pnl_usd") or 0) for r in all_rows)
    unrealized_usd = 0.0
    positions = []
    for r in open_rows:
        cur = prices.get(r["symbol"])
        pnl_pct = None
        if cur:
            unrealized_usd += (cur - r["entry_price"]) * r["shares"]
            pnl_pct = ((cur - r["entry_price"]) / r["entry_price"]) * 100
        positions.append({**r, "current": cur, "pnl_pct": pnl_pct})

    return {
        "track": US10K_TRACK,
        "variant": US10K_VARIANT,
        "base_sgd": US10K_PORTFOLIO_SGD,
        "start": US10K_START,
        "enabled": US10K_ENABLED,
        "positions": positions,
        "closed": closed_rows,
        "n_entries": len(all_rows),
        "realized_usd": realized_usd,
        "unrealized_usd": unrealized_usd,
        # Raw rows for callers that need stats; popped before serialization so
        # the response doesn't ship every row twice.
        "_all_rows": all_rows,
    }


async def _send_track():
    loop = asyncio.get_event_loop()
    snap = await _us10k_snapshot()

    header = [
        f"🇺🇸 <b>US Track · S${snap['base_sgd']:,}</b>",
        f"<code>Strategy D · auto-logged · since {snap['start']}</code>",
        "",
    ]

    # Answer the empty case before paying for an FX lookup that would only
    # scale zeros.
    if not snap["n_entries"]:
        telegram_bot.send("\n".join(header + [
            "No entries yet.",
            "",
            "This track fills itself — the next strategy D BUY in the "
            "03:30–04:00 SGT close window opens the first position. "
            "Nothing for you to log.",
        ]))
        return

    try:
        regime = await asyncio.wait_for(
            loop.run_in_executor(None, get_market_regime), timeout=15)
        sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    except Exception:
        sgd_to_usd = 0.74

    lines = list(header)
    positions = snap["positions"]
    if positions:
        lines.append(f"📋 <b>Open ({len(positions)})</b>")
        rows = []
        for p in positions:
            half = " ·½" if p.get("half_sold") else ""
            if p["current"] is not None:
                sign = "+" if p["pnl_pct"] >= 0 else ""
                rows.append(f"{p['symbol']:<6}{p['shares']:>7.2f}sh  in ${p['entry_price']:.2f}"
                            f"  now ${p['current']:.2f}  {sign}{p['pnl_pct']:.1f}%{half}")
            else:
                rows.append(f"{p['symbol']:<6}{p['shares']:>7.2f}sh  in ${p['entry_price']:.2f}"
                            f"  (no live price){half}")
        lines.append("<code>" + "\n".join(rows) + "</code>")
    else:
        lines.append("📋 <b>Open</b>  none")

    realized = snap["realized_usd"]
    unreal = snap["unrealized_usd"]
    total = realized + unreal
    base_usd = snap["base_sgd"] * sgd_to_usd
    ret_pct = (total / base_usd * 100) if base_usd else 0
    equity_sgd = snap["base_sgd"] + (total / sgd_to_usd if sgd_to_usd else 0)

    def _m(v: float) -> str:
        return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"

    lines += ["", "💰 <b>P&L</b>", "<code>"
              f"Realized    {_m(realized)}\n"
              f"Unrealized  {_m(unreal)}\n"
              f"Total       {_m(total)}  ({'+' if ret_pct >= 0 else ''}{ret_pct:.2f}%)\n"
              f"Equity      S${equity_sgd:,.0f}"
              "</code>"]

    lines += ["", f"📈 {snap['n_entries']} entries · {len(snap['closed'])} closed "
                  f"· {len(positions)} open"]
    lines.append("<code>/algocheck for the verdict vs backtest</code>")
    telegram_bot.send("\n".join(lines))


def _us10k_stats(rows: list[dict], base_usd: float) -> dict:
    """Realized performance of the track. Only exited rows count — an open
    position's mark-to-market is a quote, not a result."""
    closed = [r for r in rows if r.get("exit_price") is not None]
    legs = []
    for r in closed:
        basis = r["entry_price"] * (r.get("entry_shares") or r["shares"])
        if basis > 0:
            legs.append((r.get("realized_pnl_usd") or 0) / basis)

    n = len(legs)
    wins = sum(1 for x in legs if x > 0)
    avg = sum(legs) / n if n else 0.0

    # Realized-only equity curve, ordered by exit. Excursions on positions that
    # are still open are invisible to it, so this is a FLOOR on drawdown, not
    # the true figure — the report labels it that way rather than implying a
    # precision the data doesn't have.
    equity = peak = base_usd
    maxdd = 0.0
    for r in sorted(closed, key=lambda x: (x.get("exit_date") or "", x["id"])):
        equity += r.get("realized_pnl_usd") or 0
        peak = max(peak, equity)
        if peak > 0:
            maxdd = min(maxdd, (equity - peak) / peak)

    return {
        "n": n,
        "wins": wins,
        "win_rate": wins / n if n else 0.0,
        "avg_per_trade": avg,
        "best": max(legs) if legs else 0.0,
        "worst": min(legs) if legs else 0.0,
        "max_dd": maxdd,
        "total_realized_usd": sum(r.get("realized_pnl_usd") or 0 for r in rows),
    }


# Below this many closed legs the sample cannot separate skill from noise, so
# /algocheck reports numbers without a verdict. Chosen to match the robustness
# work: strategy D's walk-forward evidence rests on 283 legs across 8 windows,
# and its own out-of-sample window ran -0.41%/trade in a soft regime. Calling a
# strategy broken off a handful of trades would contradict that finding.
US10K_MIN_LEGS_FOR_VERDICT = 20


async def _send_algocheck():
    """Compare the auto-logged track against strategy D's backtest expectations."""
    loop = asyncio.get_event_loop()
    try:
        rows = db.get_all_paper_trades(US10K_TRACK)
    except Exception as e:
        telegram_bot.send(f"❌ /algocheck failed reading the track.\n<code>{e}</code>")
        return

    n_closed = sum(1 for r in rows if r.get("exit_price") is not None)
    days = (datetime.now(SGT).date()
            - datetime.strptime(US10K_START, "%Y-%m-%d").date()).days

    head = [
        "🔬 <b>Algo Check · US Track</b>",
        f"<code>Strategy D · day {days} · {len(rows)} entries, {n_closed} closed</code>",
        "",
    ]

    # Both early exits below are answerable without an FX rate, so the lookup
    # waits until there is something to convert.
    if not rows:
        telegram_bot.send("\n".join(head + [
            "Nothing logged yet — the track opens its first position on the "
            "next strategy D BUY in the 03:30–04:00 SGT close window.",
        ]))
        return

    if n_closed == 0:
        telegram_bot.send("\n".join(head + [
            f"{len(rows)} position(s) open, none closed yet.",
            "",
            "No verdict is possible until trades exit — an open position's P&L "
            "is a quote, not a result. <code>/track</code> shows the marks.",
        ]))
        return

    try:
        regime = await asyncio.wait_for(
            loop.run_in_executor(None, get_market_regime), timeout=15)
        sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    except Exception:
        sgd_to_usd = 0.74

    base_usd = US10K_PORTFOLIO_SGD * sgd_to_usd
    st = _us10k_stats(rows, base_usd)

    try:
        spy = await asyncio.wait_for(
            loop.run_in_executor(None, get_return_since, "SPY", US10K_START), timeout=20)
    except Exception:
        spy = None

    exp_leg = US10K_EXPECT_PER_TRADE
    lines = list(head)
    lines.append("📊 <b>Realized vs backtest</b>")
    # Only rows with a genuine backtest counterpart belong under "expected" —
    # win rate and best/worst have none, and putting them in that column would
    # read as targets the strategy was supposed to hit.
    rowsf = [
        f"{'':<12}{'actual':>10}{'expected':>11}",
        f"{'Per trade':<12}{st['avg_per_trade']*100:>9.2f}%{exp_leg*100:>10.2f}%",
        f"{'Max DD':<12}{st['max_dd']*100:>9.1f}%{US10K_EXPECT_MAXDD*100:>10.1f}%",
    ]
    lines.append("<code>" + "\n".join(rowsf) + "</code>")
    lines.append(
        f"<code>Win rate {st['win_rate']*100:.0f}% ({st['wins']}/{st['n']}) · "
        f"best {st['best']*100:+.1f}% · worst {st['worst']*100:+.1f}%</code>"
    )

    total_ret = st["total_realized_usd"] / base_usd if base_usd else 0
    bench = [f"{'Track':<8}{total_ret*100:>+8.2f}%"]
    if spy is not None:
        bench.append(f"{'SPY':<8}{spy:>+8.2f}%")
        bench.append(f"{'Edge':<8}{(total_ret*100 - spy):>+8.2f}%")
    lines += ["", "📈 <b>Realized return since start</b>",
              "<code>" + "\n".join(bench) + "</code>"]

    lines.append("")
    if st["n"] < US10K_MIN_LEGS_FOR_VERDICT:
        lines += [
            f"⏳ <b>No verdict — {st['n']}/{US10K_MIN_LEGS_FOR_VERDICT} legs</b>",
            f"Too few trades to tell skill from noise. D's own walk-forward "
            f"evidence rests on 283 legs, and one of its out-of-sample windows "
            f"ran −0.41%/trade in a soft regime. A weak reading here is not yet "
            f"a failing strategy.",
        ]
    else:
        gap = st["avg_per_trade"] - exp_leg
        if st["avg_per_trade"] <= 0:
            verdict = ("🔴 <b>Underperforming</b>",
                       f"Losing {abs(st['avg_per_trade'])*100:.2f}% per trade against "
                       f"a backtest that made {exp_leg*100:.2f}%. Worth a look at "
                       f"whether live entries match the backtest's fills.")
        elif gap < -exp_leg / 2:
            verdict = ("🟡 <b>Below backtest</b>",
                       f"Positive but {abs(gap)*100:.2f}pp/trade short of expectation. "
                       f"Costs and slippage are the usual cause — <code>/slippage</code> "
                       f"measures that directly.")
        else:
            verdict = ("🟢 <b>Tracking backtest</b>",
                       f"Live per-trade return is within half the expected edge. "
                       f"Drawdown floor {st['max_dd']*100:.1f}% vs {US10K_EXPECT_MAXDD*100:.1f}% expected.")
        lines += [verdict[0], verdict[1]]

    lines += ["", "<code>Max DD is realized-only — a floor, not the true "
                  "figure. Open-position excursions aren't counted.</code>"]
    telegram_bot.send("\n".join(lines))


async def _undo_last_fill():
    """Revert the most recent /fill — clears the slippage record and unwinds the
    trade bookkeeping it created, so the alert can be re-reported correctly."""
    last = db.get_last_recorded_fill()
    if not last:
        telegram_bot.send("Nothing to undo — no fills recorded yet.")
        return

    symbol, side, fill_price = last["symbol"], last["side"], last["fill_price"]
    yf_sym = symbol + ".SI"
    today_str = datetime.now(SGT).strftime("%Y-%m-%d")
    book_note = ""

    if side == "BUY":
        trade = db.fetchone(
            "SELECT id FROM trades WHERE symbol=? AND exit_price IS NULL AND entry_price=? "
            "ORDER BY id DESC LIMIT 1",
            (symbol, fill_price),
        )
        if trade:
            db.mutate("DELETE FROM trades WHERE id=?", (trade["id"],))
            db.remove_trade_alerts(yf_sym)
            book_note = "\n📋 Open trade row deleted, stop/target alerts removed."
    elif side == "SELL":
        db.mutate(
            "UPDATE trades SET exit_price=NULL, exit_date=NULL WHERE symbol=? AND exit_price=?",
            (symbol, fill_price),
        )
        reopened = db.fetchone(
            "SELECT stop_loss, profit_target FROM trades WHERE symbol=? AND exit_price IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (symbol,),
        )
        if reopened:
            if reopened.get("stop_loss"):
                db.add_price_alert(yf_sym, reopened["stop_loss"], "below", source="trade")
            if reopened.get("profit_target"):
                db.add_price_alert(yf_sym, reopened["profit_target"], "above", source="trade")
            book_note = "\n📋 Trade reopened, stop/target alerts restored."
    elif side == "SELL_HALF":
        half = db.fetchone(
            "SELECT id, shares FROM trades WHERE symbol=? AND exit_price=? "
            "AND signal_reason='SGX Manual-SELL_HALF (paper)' ORDER BY id DESC LIMIT 1",
            (symbol, fill_price),
        )
        if half:
            db.mutate("DELETE FROM trades WHERE id=?", (half["id"],))
            db.mutate(
                "UPDATE trades SET shares = shares + ?, half_sold = 0 WHERE symbol=? AND exit_price IS NULL",
                (half["shares"], symbol),
            )
            book_note = ("\n📋 Half-sale row deleted, shares restored."
                         "\n⚠️ Stop stayed at breakeven — re-check it if the original stop should apply.")

    db.clear_fill(last["id"])
    telegram_bot.send(
        f"↩️ <b>Fill Undone — {side} {symbol}</b> · 🇸🇬\n\n"
        f"<code>Was     S${fill_price:.3f}</code>\n\n"
        f"The alert is pending again — re-report with 📝 /fill {symbol} &lt;price&gt; [qty]."
        f"{book_note}"
    )


def _format_discipline() -> str:
    rows = db.get_all_sgx_signals()
    if not rows:
        return ("📝 <b>Discipline — SGX Alerts</b> · 🇸🇬\n\n"
                "No SGX order alerts yet. Every BUY/SELL alert lands here when it "
                "fires — report your moomoo fills with /fill.")

    now = datetime.now(SGT)
    acted, missed, fresh = [], [], []
    resp_hours = []
    for r in rows:
        if r.get("fill_ts"):
            acted.append(r)
            try:
                dt = datetime.fromisoformat(r["fill_ts"]) - datetime.fromisoformat(r["signal_ts"])
                resp_hours.append(dt.total_seconds() / 3600)
            except (ValueError, TypeError):
                pass
        else:
            try:
                age = now - datetime.fromisoformat(r["signal_ts"])
            except (ValueError, TypeError):
                age = timedelta(days=99)
            (fresh if age <= timedelta(days=1) else missed).append(r)

    within_1d = sum(1 for h in resp_hours if h <= 24) + (len(acted) - len(resp_hours))
    eligible = len(acted) + len(missed)  # alerts young enough to still act on don't count against
    pct = within_1d / eligible * 100 if eligible else 100.0

    lines = [
        "📝 <b>Discipline — SGX Alerts</b> · 🇸🇬",
        "",
        "<code>"
        f"Alerts    {len(rows)}\n"
        f"Acted ≤1d {within_1d}/{eligible}  ({pct:.0f}%)"
        + (f"\nMedian    {_median(resp_hours):.1f}h to /fill" if resp_hours else "")
        + (f"\nPending   {len(fresh)}  (&lt;1d old)" if fresh else "")
        + "</code>",
        "",
        ("✅ On track — criterion is ≥90% acted within a day"
         if pct >= 90 else "⚠️ Below the pre-committed ≥90% criterion"),
    ]
    outstanding = missed + fresh
    if outstanding:
        lines += ["", "👀 <b>Awaiting /fill</b>"]
        for r in outstanding[-5:]:
            ts = (r.get("signal_ts") or "")[:16].replace("T", " ")
            lines.append(f"• {r['side']} {r['symbol']} — {ts}")
    return "\n".join(lines)


async def _send_benchmark():
    loop = asyncio.get_event_loop()
    snap = await _portfolio_snapshot()
    regime = await asyncio.wait_for(loop.run_in_executor(None, get_market_regime), timeout=20)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    spy = await asyncio.wait_for(
        loop.run_in_executor(None, get_return_since, "SPY", VALIDATION_START), timeout=20)

    us_total = snap["realized"]["US"] + snap["unrealized"]["US"]
    sgx_total = snap["realized"]["SGX"] + snap["unrealized"]["SGX"]
    total_sgd = us_total / sgd_to_usd + sgx_total
    strat_pct = total_sgd / PORTFOLIO_SIZE_SGD * 100
    days = (datetime.now(SGT).date() - datetime.strptime(VALIDATION_START, "%Y-%m-%d").date()).days

    s_sign = "+" if total_sgd >= 0 else "-"
    lines = [
        "📊 <b>Benchmark — Validation vs SPY</b>",
        f"Since {VALIDATION_START} · day {days} of the 8–12 week shadow run",
        "",
        "<code>"
        f"Strategy  {'+' if strat_pct >= 0 else ''}{strat_pct:.2f}%  ({s_sign}S${abs(total_sgd):,.0f} on S${PORTFOLIO_SIZE_SGD:,.0f})",
    ]
    if spy:
        delta = strat_pct - spy["pct"]
        lines += [
            f"SPY       {'+' if spy['pct'] >= 0 else ''}{spy['pct']:.2f}%  (${spy['start']:,.2f} → ${spy['now']:,.2f})\n"
            f"Δ         {'+' if delta >= 0 else ''}{delta:.2f}%"
            "</code>",
            "",
            ("✅ Ahead of SPY" if delta >= 0 else "⚠️ Behind SPY") + " so far",
        ]
    else:
        lines += ["SPY       data unavailable</code>"]
    lines += ["", "<i>Criteria: beat SPY over the window · ≥90% alerts acted (/discipline)\n"
                  "Corrected backtest to track: +8.0% CAGR / −7.6% maxDD</i>"]
    telegram_bot.send("\n".join(lines))


async def _send_health():
    loop = asyncio.get_event_loop()
    commit = os.getenv("RENDER_GIT_COMMIT", "")[:7] or "local"
    now = datetime.now(SGT)
    hb_rows = []
    for name in ("us_scan", "us_entry_scan", "sgx_scan", "sgx_entry_scan", "crypto_scan", "news_scan", "holdings_scan"):
        ts = _heartbeats.get(name)
        if ts:
            try:
                hb_rows.append(f"{name:<15}{_fmt_age(now - datetime.fromisoformat(ts))} ago")
            except ValueError:
                hb_rows.append(f"{name:<15}{ts}")
        else:
            hb_rows.append(f"{name:<15}—")
    msg = (
        "🩺 <b>Backend Health</b>\n\n"
        f"<code>Commit   {commit}\nUp       {_fmt_age(now - _START_TS)}</code>\n\n"
        "<b>Watcher heartbeats</b>\n"
        "<code>" + "\n".join(hb_rows) + "</code>\n"
        "<i>— means no pass since the last restart</i>"
    )
    try:
        regime = await asyncio.wait_for(loop.run_in_executor(None, get_market_regime), timeout=15)
        bullish = regime.get("regime_ok", False)
        size_note = "  ⚠️ half size" if regime.get("new_position_size_multiplier", 1.0) < 1 else ""
        msg += (
            "\n\n<code>"
            f"Regime   {'BULLISH ▲' if bullish else 'BEARISH ▼'}{size_note}\n"
            f"VIX      {regime.get('vix', 0):.1f}\n"
            f"SGD/USD  {regime.get('sgd_to_usd', 0.74):.4f}"
            "</code>"
        )
    except Exception as e:
        logging.warning(f"/health regime fetch skipped: {e}")
        msg += "\n\n<i>Regime fetch timed out — watchers above are the health signal</i>"
    telegram_bot.send(msg)


async def handle_telegram_command(text: str):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    loop = asyncio.get_event_loop()

    if cmd == "/help":
        telegram_bot.send(
            "<b>Available commands</b>\n\n"
            "<b>Validation loop</b>\n"
            "/portfolio — open positions + realized/unrealized P&L\n"
            "/fill D05 33.45 — report your moomoo fill for the last SGX alert\n"
            "/undo — revert the last /fill (wrong price/qty)\n"
            "/slippage — SGX signal-vs-fill slippage report\n"
            "/discipline — alerts acted on vs missed\n"
            "/benchmark — validation P&L vs SPY since 2026-07-13\n\n"
            "<b>US track · S$10k, auto-logged</b>\n"
            "/track — positions, entries and P&L (fills itself, nothing to log)\n"
            "/algocheck — is the algo actually working? vs backtest\n\n"
            "<b>Signals</b>\n"
            "/crypto — current signal for BTC, ETH, SOL\n"
            "/signal AAPL — current signal for any ticker\n"
            "/scan — screen 70 stocks for watchlist candidates + wheel ideas\n"
            "/briefing — send the US pre-open briefing now\n"
            "/sgxbriefing — send the SGX pre-open briefing now\n\n"
            "<b>System</b>\n"
            "/health — backend commit, watcher heartbeats, market regime\n"
            "/watchlist · /add AAPL · /remove AAPL"
        )

    elif cmd == "/crypto":
        telegram_bot.send("⏳ Fetching crypto signals…")
        try:
            loop = asyncio.get_event_loop()
            regime = await asyncio.wait_for(loop.run_in_executor(None, get_market_regime), timeout=20)
            # Same BTC-based gate as crypto_watcher — SPY regime here made the
            # on-demand digest disagree with the watcher's entries
            regime = await asyncio.wait_for(loop.run_in_executor(None, get_crypto_regime, regime), timeout=20)
            sgd_to_usd = regime.get("sgd_to_usd", 0.74)
            rows = await asyncio.wait_for(_build_crypto_snapshot(regime, sgd_to_usd), timeout=45)
            telegram_bot.send(telegram_bot.format_crypto_digest(rows, sgd_to_usd))
        except asyncio.TimeoutError:
            logging.warning("/crypto timed out fetching data")
            telegram_bot.send("❌ Crypto fetch timed out — Yahoo Finance may be slow/rate-limited with the larger watchlist. Try again shortly.")
        except Exception as e:
            logging.warning(f"/crypto error: {e}")
            telegram_bot.send(f"❌ Crypto fetch failed: {e}")

    elif cmd == "/briefing":
        telegram_bot.send("⏳ Generating briefing…")
        try:
            await send_morning_briefing()
        except Exception as e:
            logging.warning(f"/briefing error: {e}")
            telegram_bot.send(f"❌ Briefing failed — check backend logs.\n<code>{e}</code>")

    elif cmd == "/sgxbriefing":
        telegram_bot.send("⏳ Generating SGX briefing…")
        try:
            await send_sgx_morning_briefing()
        except Exception as e:
            logging.warning(f"/sgxbriefing error: {e}")
            telegram_bot.send(f"❌ SGX briefing failed — check backend logs.\n<code>{e}</code>")

    elif cmd == "/fill":
        # Report the real moomoo paper fill for the latest SGX order alert:
        # /fill D05 33.45 [qty]. Records slippage vs signal price and does the
        # trade bookkeeping the Futu auto-path would have done (open/close/split)
        # so exits stay tracked while the system is alerts-only.
        if len(parts) < 3:
            telegram_bot.send("Usage: /fill D05 33.45 [qty]")
            return
        symbol = parts[1].upper().replace(".SI", "")
        try:
            fill_price = float(parts[2])
            fill_qty = float(parts[3]) if len(parts) > 3 else None
            if fill_price <= 0 or (fill_qty is not None and fill_qty <= 0):
                raise ValueError
        except ValueError:
            telegram_bot.send("Usage: /fill D05 33.45 [qty] — price and qty must be positive numbers")
            return
        try:
            await _record_sgx_fill(symbol, fill_price, fill_qty)
        except Exception as e:
            logging.warning(f"/fill error: {e}")
            telegram_bot.send(f"❌ Fill not recorded.\n<code>{e}</code>")

    elif cmd == "/slippage":
        try:
            fills = db.get_recorded_fills()
            telegram_bot.send(_format_slippage_report(fills))
        except Exception as e:
            logging.warning(f"/slippage error: {e}")
            telegram_bot.send(f"❌ Slippage report failed.\n<code>{e}</code>")

    elif cmd == "/scan":
        telegram_bot.send("🔍 Scanning 70 stocks — this takes ~30s…")
        try:
            scan = await run_screener_scan()
            telegram_bot.send(telegram_bot.format_scan_results(scan, tracked=db.get_watchlist()))
        except Exception as e:
            logging.warning(f"/scan error: {e}")
            telegram_bot.send(f"❌ Scan failed.\n<code>{e}</code>")

    elif cmd == "/watchlist":
        symbols = db.get_watchlist()
        tickers = "  ".join(symbols) if symbols else "empty"
        telegram_bot.send(f"<b>Watchlist</b>\n\n<code>{tickers}</code>")

    elif cmd == "/add":
        if len(parts) < 2:
            telegram_bot.send("Usage: /add AAPL")
            return
        symbol = parts[1].upper()
        watchlist = db.get_watchlist()
        if symbol in watchlist:
            telegram_bot.send(f"{symbol} is already in your watchlist")
        else:
            # Validate before saving — a bad ticker in the watchlist trips every
            # subsequent scan, not just this command
            analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
            if not analysis:
                telegram_bot.send(f"❌ Couldn't fetch data for {symbol} — not added. Check the ticker (SGX needs .SI).")
                return
            db.set_watchlist(watchlist + [symbol])
            invalidate_cache()
            telegram_bot.send(f"✅ Added {symbol} — watchlist now: {', '.join(watchlist + [symbol])}")

    elif cmd == "/remove":
        if len(parts) < 2:
            telegram_bot.send("Usage: /remove AAPL")
            return
        symbol = parts[1].upper()
        watchlist = db.get_watchlist()
        if symbol not in watchlist:
            telegram_bot.send(f"{symbol} is not in your watchlist")
        else:
            updated = [s for s in watchlist if s != symbol]
            db.set_watchlist(updated)
            invalidate_cache()
            telegram_bot.send(f"✅ Removed {symbol} — watchlist now: {', '.join(updated) or 'empty'}")

    elif cmd == "/signal":
        if len(parts) < 2:
            telegram_bot.send("Usage: /signal AAPL")
            return
        symbol = parts[1].upper()
        telegram_bot.send(f"⏳ Fetching signal for {symbol}…")
        try:
            regime = await loop.run_in_executor(None, get_market_regime)
            analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
            if not analysis:
                telegram_bot.send(f"❌ Could not fetch data for {symbol} — check the ticker")
                return
            fundamentals, rel_strength, sector_status = await asyncio.gather(
                loop.run_in_executor(None, get_fundamentals, symbol),
                loop.run_in_executor(None, get_relative_strength, symbol),
                loop.run_in_executor(None, get_sector_etf_status, symbol),
            )
            # Single-symbol lookup — no peer group to rank against, skip RS rank filter
            sector_ok = sector_status.get("above_200sma") if sector_status else None
            signal = generate_signal(analysis, None, regime, fundamentals, rel_strength,
                                     sector_ok=sector_ok)
            sgd_to_usd = regime.get("sgd_to_usd", 0.74)
            size_mult = regime.get("new_position_size_multiplier", 1.0)
            pos_size = None
            if signal["action"] == "BUY":
                pos_size = calculate_position_size(
                    PORTFOLIO_SIZE_SGD, analysis["price"], analysis["stop_loss"], sgd_to_usd, size_mult,
                )
            msg = telegram_bot.format_signal(
                symbol, signal, analysis, pos_size, fundamentals,
                sector_note=_sector_note(
                    symbol, "US",
                    pos_size.get("position_value_sgd") if pos_size else None,
                    sgd_to_usd,
                ),
            )
            # Append sector ETF context
            if sector_status:
                etf = sector_status["etf_symbol"]
                trend = "▲ above" if sector_status["above_200sma"] else "▼ BELOW"
                msg += f"\n\n<code>Sector ETF  {etf} {trend} 200 SMA</code>"
            telegram_bot.send(msg)
        except Exception as e:
            logging.warning(f"/signal error: {e}")
            telegram_bot.send(f"❌ Signal fetch failed: {e}")

    elif cmd == "/portfolio":
        telegram_bot.send("⏳ Fetching portfolio…")
        try:
            await _send_portfolio()
        except Exception as e:
            logging.warning(f"/portfolio error: {e}")
            telegram_bot.send(f"❌ Portfolio fetch failed.\n<code>{e}</code>")

    elif cmd == "/undo":
        try:
            await _undo_last_fill()
        except Exception as e:
            logging.warning(f"/undo error: {e}")
            telegram_bot.send(f"❌ Undo failed.\n<code>{e}</code>")

    elif cmd == "/discipline":
        try:
            telegram_bot.send(_format_discipline())
        except Exception as e:
            logging.warning(f"/discipline error: {e}")
            telegram_bot.send(f"❌ Discipline report failed.\n<code>{e}</code>")

    elif cmd == "/benchmark":
        telegram_bot.send("⏳ Computing benchmark…")
        try:
            await _send_benchmark()
        except Exception as e:
            logging.warning(f"/benchmark error: {e}")
            telegram_bot.send(f"❌ Benchmark failed.\n<code>{e}</code>")

    elif cmd == "/track":
        telegram_bot.send("⏳ Fetching the US track…")
        try:
            await _send_track()
        except Exception as e:
            logging.warning(f"/track error: {e}")
            telegram_bot.send(f"❌ Track fetch failed.\n<code>{e}</code>")

    elif cmd == "/algocheck":
        telegram_bot.send("⏳ Checking the algo against backtest…")
        try:
            await _send_algocheck()
        except Exception as e:
            logging.warning(f"/algocheck error: {e}")
            telegram_bot.send(f"❌ Algo check failed.\n<code>{e}</code>")

    elif cmd == "/health":
        await _send_health()

    else:
        telegram_bot.send("Unknown command. Type /help to see what's available.")


async def run_screener_scan() -> dict:
    """Scan the full SCREENER_UNIVERSE for BUY setups and Wheel opportunities."""
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    size_mult = regime.get("new_position_size_multiplier", 1.0)

    stock_data = await _fetch_batch(SCREENER_UNIVERSE)
    rs_rank_map = _compute_rs_ranks(stock_data)

    buy_signals, watch_signals, wheel_signals = [], [], []

    for d in stock_data:
        symbol = d["symbol"]
        rs_rank = rs_rank_map.get(symbol)
        sector_ok = d["sector_status"].get("above_200sma") if d["sector_status"] else None
        signal = generate_signal(
            d["analysis"], None, regime, d["fundamentals"], d["rel_strength"],
            rs_rank=rs_rank, sector_ok=sector_ok,
        )
        action = signal["action"]

        if action == "BUY":
            pos_size = calculate_position_size(
                PORTFOLIO_SIZE_SGD, d["analysis"]["price"],
                d["analysis"]["stop_loss"], sgd_to_usd, size_mult,
            )
            buy_signals.append({
                "symbol": symbol, "signal": signal, "analysis": d["analysis"],
                "fundamentals": d["fundamentals"], "position_size": pos_size,
                "rs_rank": rs_rank,
            })

        elif action == "WATCH":
            rsi = d["analysis"].get("rsi")
            if rsi is not None and rsi < 47:  # only close-to-trigger watches
                watch_signals.append({
                    "symbol": symbol, "signal": signal, "analysis": d["analysis"],
                    "rs_rank": rs_rank,
                })

        # Wheel check — non-ETF stocks only
        is_etf = d["fundamentals"].get("is_etf", True) if d["fundamentals"] else True
        if not is_etf:
            price = d["analysis"]["price"]
            strike = round(price * 0.93, 0)
            collateral_sgd = round(strike * 100 / sgd_to_usd, 0)
            feasible = collateral_sgd <= PORTFOLIO_SIZE_SGD * 0.8
            opt_signal = _options_signal(d["analysis"], regime, feasible)
            if opt_signal["action"] == "SELL PUT":
                wheel_signals.append({
                    "symbol": symbol, "strike": strike,
                    "collateral_sgd": collateral_sgd,
                    "options_signal": opt_signal,
                    "analysis": d["analysis"],
                    "fundamentals": d["fundamentals"],
                })

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    buy_signals.sort(key=lambda x: (
        priority_order.get(x["signal"]["priority"], 3),
        -(x["rs_rank"] or 0),
    ))
    wheel_signals.sort(key=lambda x: priority_order.get(x["options_signal"]["priority"], 3))

    return {
        "regime": regime,
        "buy_signals": buy_signals[:7],
        "watch_signals": watch_signals[:5],
        "wheel_signals": wheel_signals[:5],
        "total_scanned": len(stock_data),
    }


async def send_morning_briefing():
    loop = asyncio.get_event_loop()
    SGT = ZoneInfo("Asia/Singapore")

    now_et = datetime.now(ET)
    market_open_sgt = now_et.replace(hour=9, minute=30, second=0).astimezone(SGT)
    open_time_sgt = market_open_sgt.strftime("%I:%M %p").lstrip("0")

    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)

    rows = db.fetch("SELECT * FROM trades WHERE exit_price IS NULL ORDER BY entry_date DESC")

    open_trades = []
    for row in rows:
        t = dict(row)
        analysis = await loop.run_in_executor(None, get_ticker_analysis, t["symbol"])
        current = analysis["price"] if analysis else None
        pnl_pct = ((current - t["entry_price"]) / t["entry_price"]) * 100 if current else None
        open_trades.append({
            "symbol": t["symbol"],
            "shares": t["shares"],
            "entry_price": t["entry_price"],
            "pnl_pct": pnl_pct,
        })

    actionable_signals, watch_signals = [], []

    briefing_data = await _fetch_batch(db.get_watchlist())
    rs_rank_map = _compute_rs_ranks(briefing_data)

    # Pass 2: generate signals with RS rank + sector confirmation
    for d in briefing_data:
        symbol = d["symbol"]
        rs_rank = rs_rank_map.get(symbol)
        sector_ok = d["sector_status"].get("above_200sma") if d["sector_status"] else None
        signal = generate_signal(d["analysis"], None, regime, d["fundamentals"], d["rel_strength"],
                                 rs_rank=rs_rank, sector_ok=sector_ok)
        action = signal["action"]
        reason = signal["reasons"][0] if signal["reasons"] else signal["suggested_action"]
        if action in ACTIONABLE:
            a = d["analysis"]
            fund = d["fundamentals"]
            grade = fund.get("grade") if fund and not fund.get("is_etf") else None
            actionable_signals.append({
                "symbol": symbol, "action": action, "reason": reason,
                "price": a.get("price"), "stop": a.get("stop_loss"),
                "target": a.get("profit_target"), "grade": grade,
            })
        elif action == "WATCH":
            watch_signals.append({"symbol": symbol, "reason": reason,
                                  "kind": signal.get("watch_kind", "approaching")})

    msg = telegram_bot.format_morning_briefing(
        date_str=now_et.strftime("%a %d %b"),
        open_time_et="9:30 AM",
        open_time_sgt=open_time_sgt,
        regime=regime,
        open_trades=open_trades,
        actionable_signals=actionable_signals,
        watch_signals=watch_signals,
    )
    telegram_bot.send(msg)

    # Run full screener scan and send separately so it doesn't get cut off
    scan = await run_screener_scan()
    scan_msg = telegram_bot.format_scan_results(scan, tracked=db.get_watchlist())
    telegram_bot.send(scan_msg)


def _holdings_entry_hint(a: dict, signal_action: str) -> str:
    """Long-term entry read for an owned SGX stock. These are multi-year holds,
    so the anchor is trend intactness + pullback depth, not the swing setup's
    stop/target geometry."""
    money = telegram_bot.money_for("SGX")
    if signal_action == "BUY":
        return "⚡ Add zone — oversold pullback with the uptrend intact"
    if not a["above_200sma"]:
        return f"⚠️ Below the 200 SMA ({money(a['sma200'])}) — wait for a reclaim before adding"
    if a["rsi"] >= 70:
        return f"⚠️ Overbought (RSI {a['rsi']:.0f}) — poor add point; not a sell signal on a long-term hold"
    if a["rsi"] <= 45:
        return f"📋 Reasonable add zone — RSI {a['rsi']:.0f}, uptrend intact"
    ext = (a["price"] / a["sma200"] - 1) * 100
    return f"📋 Hold off — {ext:+.0f}% above the 200 SMA; better adds near {money(a['sma200'])}"


async def _build_holdings_block(sgx_analysis: dict[str, dict], regime: dict) -> list[str]:
    """Real-holdings section of the SGX Pre-Open. sgx_analysis maps bare codes
    to already-fetched analysis dicts; anything missing is fetched here."""
    loop = asyncio.get_event_loop()
    money = telegram_bot.money_for("SGX")
    rows, notes = [], []
    for symbol in SGX_HOLDINGS:
        a = sgx_analysis.get(symbol)
        if a is None:
            a = await loop.run_in_executor(None, get_ticker_analysis, f"{symbol}.SI")
        if a is None:
            notes.append(f"• {symbol} — no data today")
            continue
        chg = await loop.run_in_executor(None, news.get_daily_pct_change, f"{symbol}.SI")
        chg_str = f"{chg[0]:+.1f}%" if chg else "—"
        trend = "▲ trend" if a["above_200sma"] else "▼ trend"
        rows.append(f"{symbol:<8}{money(a['price'])}  {chg_str} · RSI {a['rsi']:.0f} · {trend}")
        signal = generate_signal(a, None, regime, variant="SWING_LOW_NOCAP")
        notes.append(f"• {symbol} — {_holdings_entry_hint(a, signal['action'])}")
        events = await loop.run_in_executor(None, get_holding_events, f"{symbol}.SI")
        ev_bits = []
        if events["days_to_ex_div"] is not None and 0 <= events["days_to_ex_div"] <= 14:
            ev_bits.append(f"ex-div {events['ex_dividend_date']} ({events['days_to_ex_div']}d)")
        if events["days_to_earnings"] is not None and 0 <= events["days_to_earnings"] <= 21:
            ev_bits.append(f"results {events['earnings_date']} ({events['days_to_earnings']}d)")
        if ev_bits:
            notes.append(f"• {symbol} — 📝 " + " · ".join(ev_bits))
    if not rows:
        return []
    return ["", "💼 <b>Your Holdings</b>",
            "<code>" + "\n".join(rows) + "</code>"] + notes


async def holdings_watcher():
    """Intraday monitor for real long-term holdings (SGX_HOLDINGS), separate
    from the paper strategy: big daily moves (with headlines) plus technical
    state changes — 200 SMA break/reclaim and RSI crossing overbought (70).
    State is in-memory: the first pass after a restart baselines silently, so
    redeploys never re-alert an unchanged state."""
    trend_state: dict[str, bool] = {}
    rsi_hot: dict[str, bool] = {}
    move_alerted: dict[str, str] = {}
    money = telegram_bot.money_for("SGX")
    await asyncio.sleep(120)
    while True:
        try:
            if _sgx_market_is_open():
                _beat("holdings_scan")
                loop = asyncio.get_event_loop()
                today = datetime.now(SGT).strftime("%Y-%m-%d")
                for symbol in SGX_HOLDINGS:
                    yf_symbol = f"{symbol}.SI"
                    a = await loop.run_in_executor(None, get_ticker_analysis, yf_symbol)
                    if a is None:
                        continue

                    if move_alerted.get(symbol) != today:
                        chg = await loop.run_in_executor(None, news.get_daily_pct_change, yf_symbol)
                        if chg and abs(chg[0]) >= HOLDINGS_MOVE_ALERT_PCT:
                            pct, _price = chg
                            icon = "📈" if pct > 0 else "📉"
                            lines = [f"{icon} <b>Holding Move — {symbol}</b> · 🇸🇬",
                                     f"<code>Price    {money(a['price'])}\nDay      {pct:+.1f}%</code>"]
                            headlines = await loop.run_in_executor(
                                None, news.get_recent_news, yf_symbol, NEWS_HEADLINES_PER_TICKER)
                            if headlines:
                                lines += [""] + [f"• {h['title']}" for h in headlines]
                            telegram_bot.send("\n".join(lines))
                            move_alerted[symbol] = today

                    above = bool(a["above_200sma"])
                    prev = trend_state.get(symbol)
                    if prev is not None and above != prev:
                        if above:
                            telegram_bot.send(
                                f"🟢 <b>Trend Reclaimed — {symbol}</b> · 🇸🇬\n"
                                f"<code>Price    {money(a['price'])}\n200 SMA  {money(a['sma200'])}</code>\n"
                                "📋 Long-term uptrend restored — add zone reopens on pullbacks")
                        else:
                            telegram_bot.send(
                                f"⚠️ <b>Trend Break — {symbol}</b> · 🇸🇬\n"
                                f"<code>Price    {money(a['price'])}\n200 SMA  {money(a['sma200'])}</code>\n"
                                "📋 Closed below the 200 SMA — pause adding; review if it stays below for weeks")
                    trend_state[symbol] = above

                    hot = a["rsi"] >= 70
                    prev_hot = rsi_hot.get(symbol)
                    if prev_hot is not None and hot and not prev_hot:
                        telegram_bot.send(
                            f"⚠️ <b>Overbought — {symbol}</b> · 🇸🇬\n"
                            f"<code>Price    {money(a['price'])}\nRSI      {a['rsi']:.0f}</code>\n"
                            "📋 Stretched — poor add point; no action needed on a long-term hold")
                    rsi_hot[symbol] = hot
        except Exception as e:
            logging.warning(f"holdings watcher error: {e}")
        # 15 min matches the analysis cache TTL, same reasoning as sgx_watcher
        await asyncio.sleep(15 * 60)


async def send_sgx_morning_briefing():
    loop = asyncio.get_event_loop()
    now_sgt = datetime.now(SGT)
    regime = await loop.run_in_executor(None, get_market_regime)
    # SGX entries gate on the STI's own 200 SMA, not the S&P's
    regime = await loop.run_in_executor(None, get_sgx_regime, regime)

    yf_symbols = [s + ".SI" for s in SGX_WATCHLIST]
    sgx_data = await _fetch_batch(yf_symbols)

    open_rows = db.fetch(
        "SELECT symbol, shares, entry_price, entry_date, stop_loss, profit_target "
        "FROM trades WHERE exit_price IS NULL"
    )
    sgx_position_map = {
        r["symbol"]: {"avg_cost": r["entry_price"], "shares": r["shares"],
                      "entry_date": r.get("entry_date"),
                      "stop_loss": r.get("stop_loss"),
                      "profit_target": r.get("profit_target")}
        for r in open_rows if r["symbol"] in SGX_WATCHLIST
    }

    money = telegram_bot.money_for("SGX")
    actionable, watching = [], []
    for d in sgx_data:
        symbol = d["symbol"].replace(".SI", "")
        a = d["analysis"]
        position = sgx_position_map.get(symbol)
        # Must match the sgx_watcher variant or briefing and alerts disagree
        signal = generate_signal(a, position, regime, variant="SWING_LOW_NOCAP")
        action = signal["action"]
        price = a["price"]
        reason = signal["reasons"][0] if signal["reasons"] else signal["suggested_action"]
        if action in ACTIONABLE:
            # Mirror the US briefing: action-first line, then a levels sub-line
            # (swing stop/target for BUYs, current price for exits).
            actionable.append(f"• <b>{action.replace('_', ' ')} {telegram_bot.label_for(symbol)}</b> — {reason}")
            stop, target = a.get("stop_loss_swing"), a.get("profit_target_swing")
            if action == "BUY" and price and stop and target:
                actionable.append(f"  <code>Entry ~{money(price)}  Stop {money(stop)}  Target {money(target)}</code>")
            elif price:
                actionable.append(f"  <code>Price {money(price)}</code>")
        elif action == "WATCH":
            watching.append({"line": f"• {telegram_bot.label_for(symbol)} @ {money(price)} — {reason}",
                             "kind": signal.get("watch_kind", "approaching")})

    regime_str = regime.get("regime", "—")
    arrow = " ▲" if regime_str == "BULLISH" else (" ▼" if regime_str == "BEARISH" else "")
    vix = regime.get("vix")
    market_block = f"Regime   {regime_str}{arrow} ({regime.get('basis', 'STI')})"
    if isinstance(vix, (int, float)):
        vix_label = "calm" if vix < 20 else "elevated" if vix < 30 else "fearful"
        # VIX is the US volatility index — shown as context, but SGX entries gate
        # on the STI's own 200 SMA (basis above), not VIX. Labelled to avoid
        # implying it drives SGX decisions.
        market_block += f"\nVIX (US) {vix:.1f}  ({vix_label})"
    lines = [
        f"🇸🇬 <b>SGX Pre-Open — {now_sgt.strftime('%a %d %b')}</b>",
        "Opens <code>9:00 AM SGT</code>",
        "",
        "📊 <b>Market</b>",
        f"<code>{market_block}</code>",
    ]

    # Your real holdings (D05/O39) lead the briefing as one contiguous block —
    # never interleaved with the not-owned strategy candidates below.
    sgx_analysis = {d["symbol"].replace(".SI", ""): d["analysis"] for d in sgx_data}
    lines += await _build_holdings_block(sgx_analysis, regime)

    # Everything below is the paper strategy's 27-name screener: buy candidates
    # and watch-list ideas, none of which are owned. A divider + banner keeps the
    # separation from "Your Holdings" unmistakable on a phone.
    lines += [
        "",
        "━━━━━━━━━━━━━━━━",
        "📈 <b>Paper strategy · not owned</b>",
        "<i>Ideas from the 27-name screener</i>",
        "",
        "⚡ <b>Act at open (9:00 AM SGT)</b>",
    ]
    if actionable:
        lines.extend(actionable)
    else:
        lines.append("• Nothing to do — hold positions as planned")

    # Split the watch list: "Approaching" (near a trigger) vs "Blocked"
    # (triggered/near but held back by a filter). Same grouping as the US briefing.
    approaching = [w["line"] for w in watching if w["kind"] != "blocked"]
    blocked = [w["line"] for w in watching if w["kind"] == "blocked"]
    if approaching:
        lines += ["", "👀 <b>Approaching</b>"]
        lines.extend(approaching)
    if blocked:
        lines += ["", "⏸️ <b>Blocked</b>"]
        lines.extend(blocked)

    lines += ["", "<i>Doesn't account for SG public holidays · verify before trading</i>"]

    telegram_bot.send("\n".join(lines))


async def sgx_briefing_task():
    # Catch-up: if backend starts between 8:30 AM and market open (9:00 AM SGT), send now
    try:
        now_sgt = datetime.now(SGT)
        cutoff = now_sgt.replace(hour=8, minute=30, second=0, microsecond=0)
        market_open = now_sgt.replace(hour=9, minute=0, second=0, microsecond=0)
        if now_sgt.weekday() < 5 and cutoff <= now_sgt < market_open:
            await send_sgx_morning_briefing()
    except Exception as e:
        logging.warning(f"SGX briefing catch-up error: {e}")

    while True:
        try:
            now_sgt = datetime.now(SGT)
            target = now_sgt.replace(hour=8, minute=30, second=0, microsecond=0)
            if now_sgt >= target:
                target += timedelta(days=1)
            while target.weekday() >= 5:
                target += timedelta(days=1)
            await asyncio.sleep((target - now_sgt).total_seconds())
            await send_sgx_morning_briefing()
        except Exception as e:
            logging.warning(f"SGX briefing error: {e}")
            await asyncio.sleep(60)


async def _send_daily_summary(market: str):
    """Drain the event log for one market and send a day summary: what fired
    (sent immediately or queued outside the alert window) + open positions."""
    loop = asyncio.get_event_loop()
    events = _drain_events(market)

    rows = db.fetch("SELECT * FROM trades WHERE exit_price IS NULL ORDER BY entry_date DESC")
    if market == "SGX":
        positions = [r for r in rows if r["symbol"] in SGX_WATCHLIST]
    else:
        positions = [r for r in rows
                     if r["symbol"] not in SGX_WATCHLIST and r["symbol"] not in CRYPTO_WATCHLIST]

    if not events and not positions:
        return   # nothing to say — don't send an empty digest

    regime = await loop.run_in_executor(None, get_market_regime)
    if market == "SGX":
        regime = await loop.run_in_executor(None, get_sgx_regime, regime)

    flag = "🇸🇬" if market == "SGX" else "🇺🇸"
    label = "SGX Day Summary" if market == "SGX" else "US Day Summary (overnight)"
    regime_str = regime.get("regime", "—")
    arrow = " ▲" if regime_str == "BULLISH" else (" ▼" if regime_str == "BEARISH" else "")
    lines = [f"{flag} <b>{label} — {datetime.now(SGT).strftime('%a %d %b')}</b>",
             f"<code>Regime   {regime_str}{arrow} ({regime.get('basis', 'SPY')})</code>", ""]

    if events:
        lines.append("Signals (✓ sent · ⏸ queued):")
        for e in events:
            mark = "✓" if e["sent"] else "⏸"
            lines.append(f"  {mark} {e['ts'].strftime('%H:%M')}  {e['text']}")
    else:
        lines.append("No signals fired.")

    if positions:
        lines.append("")
        lines.append("Open positions:")
        for t in positions:
            yf_sym = t["symbol"] + ".SI" if market == "SGX" else t["symbol"]
            analysis = await loop.run_in_executor(None, get_ticker_analysis, yf_sym)
            if analysis:
                pnl = (analysis["price"] - t["entry_price"]) / t["entry_price"] * 100
                lines.append(f"  {t['symbol']}  {t['entry_price']:.2f} → {analysis['price']:.2f}  ({pnl:+.1f}%)")
            else:
                lines.append(f"  {t['symbol']}  entry {t['entry_price']:.2f} (no price data)")

    if any(not e["sent"] for e in events):
        lines.append("")
        lines.append("⏸ queued entries are re-checked in tonight's pre-open briefing — only act if still valid.")

    telegram_bot.send("\n".join(lines))


async def daily_summary_task(market: str, hour: int, minute: int, skip_weekdays: tuple):
    """Send the day summary at a fixed SGT time, skipping non-session days.
    US: 7:30 SGT Tue–Sat (session ends ~4–5 AM SGT). SGX: 17:45 SGT Mon–Fri."""
    await asyncio.sleep(60)
    while True:
        try:
            now = datetime.now(SGT)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            while target.weekday() in skip_weekdays:
                target += timedelta(days=1)
            await asyncio.sleep((target - datetime.now(SGT)).total_seconds())
            await _send_daily_summary(market)
        except Exception as e:
            logging.warning(f"{market} daily summary error: {e}")
            await asyncio.sleep(60)


async def weekly_algocheck_task(weekday: int = 5, hour: int = 9, minute: int = 0):
    """Push the algo verdict weekly — Saturday 09:00 SGT, after Friday's US
    session has closed and settled.

    This is the part that makes the track agentic rather than another dashboard
    to remember to open: the question "is this working" gets answered on a
    schedule instead of waiting to be asked. The manual-input version of this
    loop logged nothing for three weeks.
    """
    await asyncio.sleep(90)
    while True:
        try:
            now = datetime.now(SGT)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            while target.weekday() != weekday:
                target += timedelta(days=1)
            await asyncio.sleep((target - datetime.now(SGT)).total_seconds())
            if US10K_ENABLED:
                await _send_algocheck()
        except Exception as e:
            logging.warning(f"weekly algocheck error: {e}")
            await asyncio.sleep(300)


async def morning_briefing_task():
    # Catch-up: if the backend starts after 8:30 AM on a weekday and before
    # market close (4 PM ET), the briefing was missed — send it now.
    try:
        now_et = datetime.now(ET)
        cutoff = now_et.replace(hour=8, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if now_et.weekday() < 5 and cutoff <= now_et < market_close:
            logging.info("Morning briefing: sending catch-up (backend started after 8:30 AM ET)")
            await send_morning_briefing()
    except Exception as e:
        logging.warning(f"Morning briefing catch-up error: {e}")

    while True:
        try:
            now_et = datetime.now(ET)
            target = now_et.replace(hour=8, minute=30, second=0, microsecond=0)
            if now_et >= target:
                target += timedelta(days=1)
            # Skip straight to Monday if target lands on a weekend
            while target.weekday() >= 5:
                target += timedelta(days=1)
            await asyncio.sleep((target - now_et).total_seconds())
            await send_morning_briefing()
        except Exception as e:
            logging.warning(f"Morning briefing error: {e}")
            await asyncio.sleep(60)


async def telegram_command_listener():
    offset = 0
    while True:
        try:
            loop = asyncio.get_event_loop()
            updates = await loop.run_in_executor(None, lambda: telegram_bot.get_updates(offset))
            for update in updates:
                offset = update["update_id"] + 1
                text = update.get("message", {}).get("text", "").strip()
                if text.startswith("/"):
                    await handle_telegram_command(text)
        except Exception as e:
            logging.warning(f"Telegram command listener error: {e}")
            await asyncio.sleep(30)  # back off on error — prevents tight loop on 409 Conflict


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Restore last-sent signal state so a redeploy doesn't re-alert everything
    try:
        _last_signals.update(db.load_signal_state("A"))
        _last_signals_b.update(db.load_signal_state("B"))
        _last_signals_c.update(db.load_signal_state("C"))
        _last_signals_d.update(db.load_signal_state("D"))
        _last_crypto_signals.update(db.load_signal_state("CRYPTO"))
        _last_sgx_signals.update(db.load_signal_state("SGX"))
        logging.info(f"Restored signal state: {sum(len(d) for d in (_last_signals, _last_signals_b, _last_signals_c, _last_crypto_signals, _last_sgx_signals))} entries")
    except Exception as e:
        logging.warning(f"Could not restore signal state: {e}")
    ibkr.connect_background(paper=True)
    watcher = asyncio.create_task(signal_watcher())
    rs_refresher = asyncio.create_task(rs_rank_refresher())
    crypto = asyncio.create_task(crypto_watcher())
    sgx = asyncio.create_task(sgx_watcher())
    news_task = asyncio.create_task(news_watcher())
    holdings = asyncio.create_task(holdings_watcher())
    listener = asyncio.create_task(telegram_command_listener())
    briefing = asyncio.create_task(morning_briefing_task())
    sgx_briefing = asyncio.create_task(sgx_briefing_task())
    # Day summaries: US at 7:30 SGT Tue–Sat (Sun=6, Mon=0 skipped);
    # SGX at 17:45 SGT Mon–Fri (Sat=5, Sun=6 skipped)
    us_summary = asyncio.create_task(daily_summary_task("US", 7, 30, (6, 0)))
    sgx_summary = asyncio.create_task(daily_summary_task("SGX", 17, 45, (5, 6)))
    # Weekly algo verdict: Saturday 09:00 SGT, after Friday's US close settles
    algocheck = asyncio.create_task(weekly_algocheck_task())
    telegram_bot.set_bot_commands()
    telegram_bot.send(telegram_bot.format_startup(db.get_watchlist()))
    yield
    watcher.cancel()
    crypto.cancel()
    news_task.cancel()
    holdings.cancel()
    listener.cancel()
    briefing.cancel()
    us_summary.cancel()
    sgx_summary.cancel()
    algocheck.cancel()


def _sanitize_nan(obj):
    """yfinance occasionally yields NaN floats that crash Starlette's strict JSON
    encoder; replace them with None so a single bad data point doesn't 500 the whole
    response."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    def render(self, content):
        return super().render(_sanitize_nan(content))


app = FastAPI(lifespan=lifespan, default_response_class=SafeJSONResponse)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/watchlist")
async def get_watchlist():
    return {"watchlist": db.get_watchlist()}


@app.put("/api/watchlist")
async def update_watchlist(body: WatchlistUpdate):
    symbols = [s.strip().upper() for s in body.symbols if s.strip()]
    db.set_watchlist(symbols)
    invalidate_cache()
    return {"watchlist": symbols}


@app.get("/api/alerts")
async def get_alerts():
    return {"alerts": db.get_price_alerts()}


@app.post("/api/alerts")
async def add_alert(alert: PriceAlertIn):
    symbol = alert.symbol.strip().upper()
    analysis = await asyncio.get_event_loop().run_in_executor(None, get_ticker_analysis, symbol)
    if not analysis:
        raise HTTPException(status_code=400, detail=f"Could not fetch data for {symbol}")
    direction = alert.direction or ("above" if analysis["price"] < alert.target else "below")
    alert_id = db.add_price_alert(symbol, alert.target, direction)
    return {"id": alert_id, "symbol": symbol, "target": alert.target, "direction": direction}


@app.delete("/api/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    if not db.remove_price_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "ok"}


@app.get("/api/news-feed")
async def news_feed():
    rows = db.get_news_digest()
    for row in rows:
        row["headlines"] = json.loads(row["headlines"])
    return {"items": rows, "threshold_pct": NEWS_MOVE_THRESHOLD_PCT}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    # Keep-warm/health-check target: must do zero work (no DB, no IBKR)
    # so heavy watcher scans on the tiny free-tier CPU never make the
    # app look dead to Render or UptimeRobot. Accepts HEAD because
    # UptimeRobot pings with HEAD by default.
    return {"ok": True, "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7], "scans": _heartbeats}


@app.get("/api/status")
async def status():
    return {
        "ibkr_connected": ibkr.is_connected(),
        "portfolio_size_sgd": PORTFOLIO_SIZE_SGD,
        "watchlist": db.get_watchlist(),
    }


@app.post("/api/reconnect")
async def reconnect():
    ibkr.reconnect(paper=True)
    return {"status": "reconnecting"}


@app.post("/api/refresh-cache")
async def refresh_cache():
    invalidate_cache()
    return {"status": "cache cleared"}


@app.get("/api/market-regime")
async def market_regime():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, get_market_regime)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@app.get("/api/portfolio")
async def portfolio():
    loop = asyncio.get_event_loop()
    positions = await loop.run_in_executor(None, ibkr.get_portfolio)
    account = await loop.run_in_executor(None, ibkr.get_account_summary)

    enriched = []
    for pos in positions:
        if pos["asset_type"] == "STK":
            analysis = await loop.run_in_executor(None, get_ticker_analysis, pos["symbol"])
            if analysis:
                pos["analysis"] = analysis
        enriched.append(pos)

    return {
        "positions": enriched,
        "account": account,
        "ibkr_connected": ibkr.is_connected(),
    }


@app.get("/api/signals")
async def signals(group: str = "core"):
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    if group == "crypto":
        regime = await loop.run_in_executor(None, get_crypto_regime, regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    size_mult = regime.get("new_position_size_multiplier", 1.0)

    positions = await loop.run_in_executor(None, ibkr.get_portfolio)
    position_map = {p["symbol"]: p for p in positions if p["asset_type"] == "STK"}

    # Overlay entry-anchored exit levels from logged trades (IBKR has no stops)
    open_trades = db.fetch(
        "SELECT symbol, entry_date, stop_loss, profit_target FROM trades WHERE exit_price IS NULL"
    )
    for row in open_trades:
        pos = position_map.get(row["symbol"])
        if pos is not None:
            pos.setdefault("entry_date", row.get("entry_date"))
            pos["stop_loss"] = row.get("stop_loss")
            pos["profit_target"] = row.get("profit_target")

    if group == "quantum":
        base_symbols = QUANTUM_WATCHLIST
    elif group == "covered_calls":
        base_symbols = COVERED_CALLS_WATCHLIST
    elif group == "screener":
        base_symbols = SCREENER_UNIVERSE
    elif group == "long_term":
        base_symbols = LONGTERM_WATCHLIST
    elif group == "crypto":
        base_symbols = CRYPTO_WATCHLIST
    else:
        base_symbols = db.get_watchlist()

    all_symbols = list(dict.fromkeys(base_symbols + list(position_map.keys())))

    stock_data = await _fetch_batch(all_symbols)
    rs_rank_map = _compute_rs_ranks(stock_data)

    # Pass 2: generate signals with RS rank + sector confirmation
    results = []
    for d in stock_data:
        symbol = d["symbol"]
        position = position_map.get(symbol)
        # Crypto: skip RS rank and sector filters (no meaningful peer group / no sector ETF)
        rs_rank = None if group == "crypto" else rs_rank_map.get(symbol)
        sector_ok = None if group == "crypto" else (
            d["sector_status"].get("above_200sma") if d["sector_status"] else None
        )

        signal = generate_signal(
            d["analysis"], position, regime, d["fundamentals"], d["rel_strength"],
            rs_rank=rs_rank, sector_ok=sector_ok,
        )

        if group == "covered_calls":
            signal = _adapt_for_covered_calls(signal)
        elif group == "long_term":
            signal = _adapt_for_longterm(signal)

        pos_size = None
        if signal["action"] == "BUY":
            if group == "crypto":
                price = d["analysis"]["price"]
                qty = round(CRYPTO_POSITION_SGD * sgd_to_usd / price, 6)
                pos_size = {
                    "shares": qty,
                    "position_value_sgd": CRYPTO_POSITION_SGD,
                    "position_value_usd": round(CRYPTO_POSITION_SGD * sgd_to_usd, 2),
                    "risk_sgd": None,
                    "note": f"Fixed S${CRYPTO_POSITION_SGD} allocation (fractional)",
                }
            else:
                # Long-term: fixed S$150 accumulation; all others: risk-based
                lt_size = 150 if group == "long_term" else None
                pos_size = calculate_position_size(
                    lt_size or PORTFOLIO_SIZE_SGD,
                    d["analysis"]["price"],
                    d["analysis"]["stop_loss"],
                    sgd_to_usd,
                    size_mult,
                )

        results.append({
            "symbol": symbol,
            "in_portfolio": symbol in position_map,
            "analysis": d["analysis"],
            "fundamentals": d["fundamentals"],
            "rel_strength": d["rel_strength"],
            "sector_etf": d["sector_status"],
            "signal": signal,
            "position_size": pos_size,
        })

    # Screener: only surface actionable setups — skip holds, skips, and exits
    if group == "screener":
        results = [r for r in results if r["signal"]["action"] in ("BUY", "WATCH")]

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    action_order = {"BUY": 0, "WATCH": 1}
    results.sort(key=lambda x: (
        action_order.get(x["signal"]["action"], 2),
        priority_order.get(x["signal"]["priority"], 3),
        0 if x["in_portfolio"] else 1,
    ))

    return {
        "signals": results,
        "regime": regime,
        "generated_at": datetime.now().isoformat(),
        **({"total_scanned": len(stock_data)} if group == "screener" else {}),
    }


def _adapt_for_covered_calls(signal: dict) -> dict:
    """Post-process a standard signal for the covered-calls group.
    CC strategy wants momentum entries only (not mean-reversion), and HOLD/SELL
    messages need to account for an open covered call position.
    """
    action = signal["action"]
    reasons = signal.get("reasons", [])

    if action == "BUY":
        if any("Mean-reversion" in r for r in reasons):
            return {
                "action": "WATCH",
                "priority": "LOW",
                "reasons": ["Stock pulling back — mean-reversion entry not suited for CC setup"],
                "suggested_action": "Wait for breakout to 20-day high + volume surge, then buy 100 shares",
            }
        # Momentum entry — adapt the suggested action for CC context
        return {
            **signal,
            "suggested_action": "Buy 100 shares · sell covered call once RSI reaches 45–60 (see Wheel Strategy panel)",
        }

    if action == "HOLD":
        return {
            **signal,
            "suggested_action": "Hold shares · check Wheel Strategy panel for covered call entry timing",
        }

    if action in ("SELL", "SELL_HALF"):
        base = signal["suggested_action"]
        return {
            **signal,
            "suggested_action": f"Buy back any open covered call first, then {base[0].lower() + base[1:]}",
        }

    return signal


def _adapt_for_longterm(signal: dict) -> dict:
    """Post-process a signal for the long-term holds group.
    These are accumulation positions held for years — suppress short-term exits,
    reframe BUY as accumulation, treat SELL signals as hold-and-review.
    """
    action = signal["action"]

    if action == "BUY":
        return {**signal,
                "suggested_action": "Accumulate fractional shares · hold for 1–3 years · size S$100–200"}

    if action == "WATCH":
        return {**signal,
                "suggested_action": signal["suggested_action"].replace(
                    "At current price", "If triggered")}

    if action in ("SELL_HALF", "SELL"):
        # Don't exit long-term positions on routine overbought signals
        return {**signal,
                "action": "HOLD",
                "priority": "LOW",
                "reasons": ["Long-term hold — short-term exit signal suppressed"],
                "suggested_action": "Hold · only exit if fundamental thesis breaks (missed earnings, sector decline)"}

    if action == "HOLD":
        return {**signal,
                "suggested_action": "Hold · long-term position · ignore short-term noise"}

    return signal


def _covered_call_signal(analysis: dict, regime: dict, avg_cost: float | None) -> dict:
    rsi = analysis.get("rsi")
    price = analysis.get("price", 0)
    days_to_earnings = analysis.get("days_to_earnings")
    vix_status = regime.get("vix_status", "NORMAL")

    if days_to_earnings is not None and days_to_earnings < 30:
        return {
            "action": "AVOID", "priority": "HIGH",
            "reason": f"Earnings in {days_to_earnings}d — skip this cycle (IV crush risk after report)",
            "suggested_action": "Wait until after earnings, then sell covered call",
        }

    if rsi is not None and rsi < 40:
        return {
            "action": "WATCH", "priority": "LOW",
            "reason": f"RSI {rsi:.0f} — stock still falling, don't cap the upside yet",
            "suggested_action": "Wait for RSI > 40 to stabilise before selling call",
        }

    if rsi is not None and rsi > 65:
        return {
            "action": "WATCH", "priority": "LOW",
            "reason": f"RSI {rsi:.0f} — stock running hard, let it move first",
            "suggested_action": "Let it run; revisit when RSI cools to 45–60",
        }

    # RSI 40–65 is the sweet spot
    vix_note = " — elevated IV = more premium" if vix_status != "NORMAL" else ""
    priority = "HIGH" if (rsi is not None and 45 <= rsi <= 60) else "MEDIUM"

    # Strike: 5% OTM, but never lock in a loss
    raw_strike = round(price * 1.05, 0)
    if avg_cost and raw_strike < avg_cost:
        strike = round(avg_cost * 1.01, 0)
        strike_note = f"Strike raised above avg cost (${avg_cost:.0f}) — don't lock in a loss"
    else:
        strike = raw_strike
        strike_note = "Check IVR > 30 on Market Chameleon before entering"

    rsi_str = f"RSI {rsi:.0f}" if rsi is not None else "RSI —"
    return {
        "action": "SELL CALL", "priority": priority,
        "reason": f"{rsi_str} — stock stabilising, good covered call window{vix_note}",
        "suggested_action": f"Sell 30-delta call ~${strike:.0f} at 30–45 DTE · {strike_note}",
    }


def _options_signal(analysis: dict, regime: dict, feasible: bool) -> dict:
    rsi = analysis.get("rsi")
    above_200 = analysis.get("above_200sma")
    days_to_earnings = analysis.get("days_to_earnings")
    regime_ok = regime.get("regime_ok", True)
    vix_status = regime.get("vix_status", "NORMAL")

    if not feasible:
        return {
            "action": "AVOID", "priority": "LOW",
            "reason": "Insufficient capital for collateral",
            "suggested_action": "Build portfolio to S$15,000+ to unlock options",
        }

    if days_to_earnings is not None and days_to_earnings < 30:
        return {
            "action": "AVOID", "priority": "HIGH",
            "reason": f"Earnings in {days_to_earnings}d — IV crush risk after announcement",
            "suggested_action": "Wait until after earnings, then re-evaluate",
        }

    if not above_200:
        return {
            "action": "AVOID", "priority": "HIGH",
            "reason": "Below 200 SMA — downtrend, don't sell puts",
            "suggested_action": "Wait for price to recover above 200 SMA",
        }

    if not regime_ok:
        return {
            "action": "AVOID", "priority": "MEDIUM",
            "reason": "Market regime BEARISH — avoid new options positions",
            "suggested_action": "Wait for SPY to reclaim its 200 SMA",
        }

    vix_note = " — elevated IV means higher premium" if vix_status != "NORMAL" else ""

    if rsi is not None and rsi < 45:
        return {
            "action": "SELL PUT", "priority": "HIGH",
            "reason": f"RSI {rsi:.0f} — oversold pullback, ideal put-selling entry{vix_note}",
            "suggested_action": "Check IVR > 30, sell 30-delta put at 30–45 DTE",
        }

    if rsi is not None and rsi < 60:
        return {
            "action": "SELL PUT", "priority": "MEDIUM",
            "reason": f"RSI {rsi:.0f} — neutral momentum, conditions acceptable{vix_note}",
            "suggested_action": "Check IVR > 30, sell 30-delta put at 30–45 DTE",
        }

    return {
        "action": "WATCH", "priority": "LOW",
        "reason": f"RSI {rsi:.0f} — overbought, wait for pullback before selling put" if rsi else "No entry signal",
        "suggested_action": "Wait for RSI < 60 then reassess",
    }


def _bull_put_spread_signal(analysis: dict, regime: dict) -> dict:
    """Entry logic for bull put credit spreads on index ETFs (SPY/QQQ). These profit
    from neutral-to-bullish drift, so the same uptrend + non-bearish-regime checks as
    cash-secured puts apply, with overbought RSI flagged as pullback risk into expiry."""
    rsi = analysis.get("rsi")
    above_200 = analysis.get("above_200sma")
    regime_ok = regime.get("regime_ok", True)
    vix_status = regime.get("vix_status", "NORMAL")

    if not regime_ok:
        return {
            "action": "AVOID", "priority": "HIGH",
            "reason": "Market regime BEARISH — a fast move down can blow through both strikes",
            "suggested_action": "Wait for SPY to reclaim its 200 SMA before opening new spreads",
        }

    if not above_200:
        return {
            "action": "AVOID", "priority": "HIGH",
            "reason": "Below 200 SMA — downtrend risk, don't sell put spreads here",
            "suggested_action": "Wait for price to recover above the 200-day average",
        }

    vix_note = " — elevated IV means a fatter credit" if vix_status != "NORMAL" else ""

    if rsi is not None and rsi > 75:
        return {
            "action": "WATCH", "priority": "LOW",
            "reason": f"RSI {rsi:.0f} — overbought, pullback risk before expiry",
            "suggested_action": "Wait for RSI to cool below 70, then open the spread",
        }

    if rsi is not None and rsi < 50:
        return {
            "action": "OPEN SPREAD", "priority": "HIGH",
            "reason": f"RSI {rsi:.0f} — pullback within an uptrend, strong premium-selling entry{vix_note}",
            "suggested_action": "Sell the spread at 30–45 DTE; aim for credit ≈ 1/3 of the strike width",
        }

    return {
        "action": "OPEN SPREAD", "priority": "MEDIUM",
        "reason": f"RSI {rsi:.0f} — uptrend intact, acceptable entry conditions{vix_note}",
        "suggested_action": "Sell the spread at 30–45 DTE; aim for credit ≈ 1/3 of the strike width",
    }


@app.get("/api/spread-opportunities")
async def spread_opportunities():
    """Bull put spread scanner for the dedicated options sub-account (S$2k / ~$1,480 USD).
    Scans SPY/QQQ only — defined-risk credit spreads sized to fit a small account."""
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    account_usd = round(SPREAD_ACCOUNT_SGD * sgd_to_usd, 0)

    opportunities = []
    for symbol in SPREAD_UNIVERSE:
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            continue

        price = analysis["price"]
        signal = _bull_put_spread_signal(analysis, regime)
        spread = await loop.run_in_executor(None, get_bull_put_spread, symbol, price, SPREAD_WIDTH)

        fits_account = spread["max_loss"] <= account_usd * 0.5 if spread else None

        opportunities.append({
            "symbol": symbol,
            "strategy": "Bull Put Spread",
            "price": price,
            "spread": spread,
            "fits_account": fits_account,
            "signal": signal,
            "rsi": analysis.get("rsi"),
            "above_200sma": analysis.get("above_200sma"),
            "days_to_earnings": analysis.get("days_to_earnings"),
        })

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    action_order = {"OPEN SPREAD": 0, "WATCH": 1, "AVOID": 2}
    opportunities.sort(key=lambda o: (
        action_order.get(o["signal"]["action"], 3),
        priority_order.get(o["signal"]["priority"], 3),
    ))

    return {
        "opportunities": opportunities,
        "regime": regime,
        "account_size_sgd": SPREAD_ACCOUNT_SGD,
        "account_size_usd": account_usd,
        "spread_width": SPREAD_WIDTH,
        "note": "Defined-risk credit spreads — max loss is capped at entry. Verify live bid/ask before opening; yfinance quotes can lag.",
    }


@app.get("/api/options-opportunities")
async def options_opportunities():
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)

    # Build a weighted avg-cost map from open trades (Phase 2 detection)
    open_rows = db.fetch("SELECT symbol, shares, entry_price FROM trades WHERE exit_price IS NULL")
    position_map: dict[str, float] = {}
    cost_accumulator: dict[str, dict] = {}
    for row in open_rows:
        sym = row["symbol"]
        if sym not in cost_accumulator:
            cost_accumulator[sym] = {"total_cost": 0.0, "total_shares": 0.0}
        cost_accumulator[sym]["total_cost"] += row["entry_price"] * row["shares"]
        cost_accumulator[sym]["total_shares"] += row["shares"]
    for sym, v in cost_accumulator.items():
        if v["total_shares"] > 0:
            position_map[sym] = v["total_cost"] / v["total_shares"]

    opportunities = []
    for symbol in db.get_watchlist():
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            continue

        price = analysis["price"]
        avg_cost = position_map.get(symbol)
        phase = 2 if avg_cost is not None else 1

        if phase == 2:
            # Covered call (Wheel Phase 2) — already hold shares
            strike = round(price * 1.05, 0)
            if avg_cost and strike < avg_cost:
                strike = round(avg_cost * 1.01, 0)
            options_signal = _covered_call_signal(analysis, regime, avg_cost)
            strategy = "Covered Call (Wheel — Phase 2)"
            collateral_sgd = 0.0
            option_type = "call"
        else:
            # Cash-secured put (Wheel Phase 1) — no position yet
            strike = round(price * 0.93, 0)
            collateral_usd = strike * 100
            collateral_sgd = round(collateral_usd / sgd_to_usd, 0)
            feasible = collateral_sgd <= PORTFOLIO_SIZE_SGD * 0.8
            options_signal = _options_signal(analysis, regime, feasible)
            strategy = "Cash-Secured Put (Wheel — Phase 1)"
            option_type = "put"

        live_premium = await loop.run_in_executor(
            None, get_options_premium, symbol, float(strike), option_type
        )

        opportunities.append({
            "symbol": symbol,
            "phase": phase,
            "strategy": strategy,
            "price": price,
            "suggested_strike": strike,
            "dte_target": "30–45 DTE",
            "collateral_sgd": collateral_sgd,
            "avg_cost": round(avg_cost, 2) if avg_cost else None,
            "options_signal": options_signal,
            "live_premium": live_premium,
            "rsi": analysis.get("rsi"),
            "days_to_earnings": analysis.get("days_to_earnings"),
            "above_200sma": analysis.get("above_200sma"),
        })

    # Sort: Phase 2 first (you already own it), then by action+priority
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    action_order = {"SELL PUT": 0, "SELL CALL": 0, "WATCH": 1, "AVOID": 2}
    opportunities.sort(key=lambda o: (
        action_order.get(o["options_signal"]["action"], 3),
        priority_order.get(o["options_signal"]["priority"], 3),
        0 if o["phase"] == 2 else 1,
    ))

    return {
        "opportunities": opportunities,
        "regime": regime,
        "portfolio_size_sgd": PORTFOLIO_SIZE_SGD,
        "note": "Verify IVR > 30 on Market Chameleon or ThinkorSwim before any options trade",
    }


@app.get("/api/track")
async def get_track():
    """Auto-logged US paper track: positions, closed legs and realized stats.

    Deliberately separate from /api/trades, which serves the manually-filled
    S$5k run — the two are different pools and must never be summed.
    """
    loop = asyncio.get_event_loop()
    snap = await _us10k_snapshot()
    rows = snap.pop("_all_rows")
    # Skip the FX lookup when there is nothing to convert. It costs a network
    # round trip (up to 15s on a cold free-tier backend) and every figure it
    # would scale is zero, so an empty track used to sit on a spinner for no
    # reason at all.
    sgd_to_usd = 0.74
    if rows:
        try:
            regime = await asyncio.wait_for(
                loop.run_in_executor(None, get_market_regime), timeout=15)
            sgd_to_usd = regime.get("sgd_to_usd", 0.74)
        except Exception:
            pass
    base_usd = US10K_PORTFOLIO_SGD * sgd_to_usd
    stats = _us10k_stats(rows, base_usd)
    return {
        **snap,
        "stats": stats,
        "sgd_to_usd": sgd_to_usd,
        "min_legs_for_verdict": US10K_MIN_LEGS_FOR_VERDICT,
        "expectations": {
            "cagr": US10K_EXPECT_CAGR,
            "max_dd": US10K_EXPECT_MAXDD,
            "per_trade": US10K_EXPECT_PER_TRADE,
        },
    }


@app.get("/api/trades")
async def get_trades():
    loop = asyncio.get_event_loop()
    rows = db.fetch("SELECT * FROM trades ORDER BY entry_date DESC")

    trades = []
    for row in rows:
        t = dict(row)
        if t["exit_price"] is None:
            analysis = await loop.run_in_executor(None, get_ticker_analysis, t["symbol"])
            current = analysis["price"] if analysis else None
            t["current_price"] = current
            if current:
                t["unrealized_pnl"] = round((current - t["entry_price"]) * t["shares"], 2)
                t["unrealized_pnl_pct"] = round(((current - t["entry_price"]) / t["entry_price"]) * 100, 2)
            else:
                t["unrealized_pnl"] = None
                t["unrealized_pnl_pct"] = None
        else:
            t["realized_pnl"] = round((t["exit_price"] - t["entry_price"]) * t["shares"], 2)
            t["realized_pnl_pct"] = round(((t["exit_price"] - t["entry_price"]) / t["entry_price"]) * 100, 2)
        trades.append(t)

    return {"trades": trades}


@app.post("/api/trades")
async def add_trade(trade: TradeIn):
    symbol = trade.symbol.upper()
    loop = asyncio.get_event_loop()
    analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)

    # Freeze exit levels at entry: ATR stop off the entry price (current ATR is
    # the best available estimate at logging time), capped at the max-stop rule.
    # Strategy D anchors to the structural swing low instead (8% cap, 2% floor).
    entry = trade.entry_price
    if trade.strategy == "D" and analysis and analysis.get("swing_low") is not None and analysis.get("atr"):
        stop = max(analysis["swing_low"] - 0.5 * analysis["atr"], entry * (1 - STOP_MAX_PCT))
        stop = min(stop, entry * (1 - 0.02))
    elif analysis and analysis.get("atr"):
        stop = max(entry - STOP_ATR_MULT * analysis["atr"], entry * (1 - STOP_MAX_PCT))
    else:
        stop = entry * (1 - STOP_MAX_PCT)
    target = entry + PROFIT_RATIO * (entry - stop)

    db.mutate(
        "INSERT INTO trades (symbol, shares, entry_date, entry_price, signal_reason, notes, strategy, stop_loss, profit_target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, trade.shares, trade.entry_date, entry, trade.signal_reason, trade.notes, trade.strategy,
         round(stop, 4), round(target, 4)),
    )
    db.add_price_alert(symbol, round(stop, 4), "below", source="trade")
    db.add_price_alert(symbol, round(target, 4), "above", source="trade")
    market = "SGX" if symbol in SGX_WATCHLIST else ("CRYPTO" if "-USD" in symbol else "US")
    telegram_bot.send(telegram_bot.format_trade_entry(
        symbol, trade.shares, entry, trade.entry_date, analysis, market=market
    ))
    return {"status": "ok"}


@app.put("/api/trades/{trade_id}/close")
async def close_trade(trade_id: int, close: TradeClose):
    row = db.fetchone(
        "SELECT symbol, shares, entry_price FROM trades WHERE id=? AND exit_price IS NULL", (trade_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found or already closed")
    symbol, shares, entry_price = row["symbol"], row["shares"], row["entry_price"]
    db.mutate(
        "UPDATE trades SET exit_date=?, exit_price=?, notes=? WHERE id=?",
        (close.exit_date, close.exit_price, close.notes, trade_id),
    )
    db.remove_trade_alerts(symbol)
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    market = "SGX" if symbol in SGX_WATCHLIST else ("CRYPTO" if "-USD" in symbol else "US")
    telegram_bot.send(telegram_bot.format_trade_exit(
        symbol, shares, entry_price, close.exit_price, sgd_to_usd, market=market
    ))
    return {"status": "ok"}


@app.delete("/api/trades/{trade_id}")
async def delete_trade(trade_id: int):
    db.mutate("DELETE FROM trades WHERE id=?", (trade_id,))
    return {"status": "ok"}


@app.get("/api/options-trades")
async def get_options_trades():
    rows = db.fetch("SELECT * FROM options_trades ORDER BY open_date DESC")
    trades = []
    for row in rows:
        t = dict(row)
        contracts = t.get("contracts", 1)
        premium = t["premium"]
        total_premium = round(premium * 100 * contracts, 2)

        if t["close_premium"] is not None:
            close_total = round(t["close_premium"] * 100 * contracts, 2)
            pnl = round(total_premium - close_total, 2)
        else:
            pnl = None

        t["total_premium"] = total_premium
        t["pnl"] = pnl
        trades.append(t)
    return {"trades": trades}


@app.post("/api/options-trades")
async def add_options_trade(trade: OptionsTradeIn):
    db.mutate(
        "INSERT INTO options_trades (symbol, strategy, phase, strike, long_strike, expiry_date, dte_at_entry, premium, contracts, open_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (trade.symbol.upper(), trade.strategy, trade.phase, trade.strike, trade.long_strike,
         trade.expiry_date, trade.dte_at_entry, trade.premium, trade.contracts,
         trade.open_date, trade.notes),
    )
    return {"status": "ok"}


@app.put("/api/options-trades/{trade_id}/close")
async def close_options_trade(trade_id: int, close: OptionsTradeClose):
    row = db.fetchone("SELECT id FROM options_trades WHERE id=? AND status='open'", (trade_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found or already closed")
    db.mutate(
        "UPDATE options_trades SET close_date=?, close_premium=?, status=?, notes=? WHERE id=?",
        (close.close_date, close.close_premium, close.status, close.notes, trade_id),
    )
    return {"status": "ok"}


@app.delete("/api/options-trades/{trade_id}")
async def delete_options_trade(trade_id: int):
    db.mutate("DELETE FROM options_trades WHERE id=?", (trade_id,))
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
