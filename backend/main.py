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
from analysis import get_market_regime, get_crypto_regime, get_sgx_regime, get_ticker_analysis, get_fundamentals, get_relative_strength, get_sector_etf_status, get_options_premium, get_bull_put_spread, invalidate_cache
from signals import generate_signal, calculate_position_size
from config import PORTFOLIO_SIZE_SGD, LONGTERM_WATCHLIST, QUANTUM_WATCHLIST, COVERED_CALLS_WATCHLIST, SCREENER_UNIVERSE, SPREAD_UNIVERSE, SPREAD_WIDTH, SPREAD_ACCOUNT_SGD, CRYPTO_WATCHLIST, CRYPTO_POSITION_SGD, TRAILING_TRIGGER, TRAILING_STOP_PCT, SGX_WATCHLIST, SGX_PORTFOLIO_SGD, RISK_PER_TRADE_PCT, MAX_POSITION_PCT, NEWS_MOVE_THRESHOLD_PCT, NEWS_HEADLINES_PER_TICKER, STOP_ATR_MULT, STOP_MAX_PCT, PROFIT_RATIO

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
            for row in open_rows:
                sym = row["symbol"]
                if sym not in position_map:
                    position_map[sym] = {"avg_cost": row["entry_price"], "shares": row["shares"],
                                         "entry_date": row.get("entry_date"),
                                         "stop_loss": row.get("stop_loss"),
                                         "profit_target": row.get("profit_target")}

            watchlist = db.get_watchlist()
            all_symbols = list(dict.fromkeys(watchlist + list(position_map.keys())))

            stock_data = await _fetch_batch(all_symbols)
            rs_rank_map = _compute_rs_ranks(stock_data)
            batch_prices = {d["symbol"]: d["analysis"]["price"] for d in stock_data}

            # Pass 2: generate signals with RS rank + sector confirmation
            for d in stock_data:
                symbol = d["symbol"]
                position = position_map.get(symbol)
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
                            PORTFOLIO_SIZE_SGD, d["analysis"]["price"],
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
                        symbol, signal, d["analysis"], pos_size, d["fundamentals"]
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
                    telegram_bot.send(
                        f"🔔 <b>Trail Stop — {sym}</b>\n\n"
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
                    telegram_bot.send(
                        f"🔔 <b>Price Alert — {alert['symbol']}</b>\n\n"
                        f"<code>"
                        f"Target   ${alert['target']:.2f}  {arrow}\n"
                        f"Current  ${current:.2f}"
                        f"</code>"
                    )
                    if alert["symbol"] not in CRYPTO_WATCHLIST:
                        market = "SGX" if alert["symbol"].endswith(".SI") else "US"
                        _log_event(market, f"Price alert {alert['symbol']} {arrow} ${alert['target']:.2f} (now ${current:.2f})", sent=True)
                    db.remove_price_alert(alert["id"])

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
            risk_usd = PORTFOLIO_SIZE_SGD * sgd_to_usd * RISK_PER_TRADE_PCT
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
                            symbol, r["signal"], r["analysis"], r["pos_size"], r["fundamentals"]
                        )
                        telegram_bot.send(msg)
                        stop   = r["analysis"]["stop_loss"]
                        target = r["analysis"]["profit_target"]
                        db.add_price_alert(symbol, stop, "below", source="auto")
                        db.add_price_alert(symbol, target, "above", source="auto")

                    # Immediate alert: exit signal on a held position — don't wait for digest
                    elif r["position"] and action in ("SELL", "SELL_HALF", "REVIEW"):
                        msg = telegram_bot.format_signal(
                            symbol, r["signal"], r["analysis"], None, r["fundamentals"]
                        )
                        telegram_bot.send(msg)

                _remember_signal(_last_crypto_signals, "CRYPTO", symbol, action)

            # Every 4 hours: send full digest
            cycle += 1
            if cycle >= DIGEST_EVERY:
                cycle = 0
                telegram_bot.send(telegram_bot.format_crypto_digest(rows, sgd_to_usd))

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

            for d in sgx_data:
                yf_sym   = d["symbol"]
                symbol   = yf_sym.replace(".SI", "")
                position = sgx_position_map.get(symbol)
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
                            risk_sgd = SGX_PORTFOLIO_SGD * RISK_PER_TRADE_PCT
                            qty = max(1, int(risk_sgd / risk_per_share))
                            qty = min(qty, int(SGX_PORTFOLIO_SGD * MAX_POSITION_PCT / price))
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

                        elif action in ("SELL", "SELL_HALF") and position:
                            shares = position["shares"]
                            qty    = int(shares * 0.5) if action == "SELL_HALF" else int(shares)
                            if qty > 0:
                                order_id = futu_broker.place_limit_order(symbol, qty, "SELL", price)
                                order_note = f"\n🤖 Auto-order: SELL {qty} @ S${price:.3f}{tag}"

                    msg = telegram_bot.format_signal(symbol, signal, d["analysis"], msg_pos, d["fundamentals"],
                                                     stop=sgx_stop, target=sgx_target, market="SGX")
                    telegram_bot.send(msg + order_note)
                    _log_event("SGX", f"{action} {symbol} @ S${price:.3f} — {signal['suggested_action']}", sent=True)

                _remember_signal(_last_sgx_signals, "SGX", symbol, action)

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


async def handle_telegram_command(text: str):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    loop = asyncio.get_event_loop()

    if cmd == "/help":
        telegram_bot.send(
            "<b>Available commands</b>\n\n"
            "/scan — scan 70 stocks for BUY setups + wheel opportunities\n"
            "/crypto — current signal for BTC, ETH, SOL\n"
            "/signal AAPL — current signal for any ticker\n"
            "/share AAPL — shareable summary to send to friends\n"
            "/positions — your open trades with live P&L\n"
            "/briefing — send today's morning briefing now\n"
            "/alert AAPL 200 — notify when price crosses a level\n"
            "/alerts — list active price alerts\n"
            "/removealert 1 — remove alert by ID\n"
            "/watchlist — show your watchlist\n"
            "/add AAPL — add ticker to watchlist\n"
            "/remove AAPL — remove ticker from watchlist\n"
            "/status — market regime overview"
        )

    elif cmd == "/crypto":
        telegram_bot.send("⏳ Fetching crypto signals…")
        try:
            loop = asyncio.get_event_loop()
            regime = await asyncio.wait_for(loop.run_in_executor(None, get_market_regime), timeout=20)
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

    elif cmd == "/scan":
        telegram_bot.send("🔍 Scanning 70 stocks — this takes ~30s…")
        try:
            scan = await run_screener_scan()
            telegram_bot.send(telegram_bot.format_scan_results(scan))
        except Exception as e:
            logging.warning(f"/scan error: {e}")
            telegram_bot.send(f"❌ Scan failed.\n<code>{e}</code>")

    elif cmd == "/status":
        regime = await loop.run_in_executor(None, get_market_regime)
        bullish = regime.get("regime_ok", False)
        vix = regime.get("vix", 0)
        sgd = regime.get("sgd_to_usd", 0.74)
        mult = regime.get("new_position_size_multiplier", 1.0)
        size_note = "  ⚠️ Use half size" if mult < 1 else ""
        telegram_bot.send(
            f"{'📈' if bullish else '📉'} <b>Market Status</b>\n\n"
            f"<code>"
            f"Regime   {'BULLISH' if bullish else 'BEARISH'}{size_note}\n"
            f"VIX      {vix:.1f}\n"
            f"SGD/USD  {sgd:.4f}"
            f"</code>"
        )

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
            msg = telegram_bot.format_signal(symbol, signal, analysis, pos_size, fundamentals)
            # Append sector ETF context
            if sector_status:
                etf = sector_status["etf_symbol"]
                trend = "▲ above" if sector_status["above_200sma"] else "▼ BELOW"
                msg += f"\n\n<code>Sector ETF  {etf} {trend} 200 SMA</code>"
            telegram_bot.send(msg)
        except Exception as e:
            logging.warning(f"/signal error: {e}")
            telegram_bot.send(f"❌ Signal fetch failed: {e}")

    elif cmd == "/pnl":
        rows = db.fetch("SELECT * FROM trades ORDER BY entry_date DESC")

        if not rows:
            telegram_bot.send("No trades logged yet.")
            return

        realized_usd = 0.0
        unrealized_usd = 0.0
        regime = await loop.run_in_executor(None, get_market_regime)
        sgd_to_usd = regime.get("sgd_to_usd", 0.74)

        for row in rows:
            t = dict(row)
            if t["exit_price"] is not None:
                realized_usd += (t["exit_price"] - t["entry_price"]) * t["shares"]
            else:
                analysis = await loop.run_in_executor(None, get_ticker_analysis, t["symbol"])
                if analysis:
                    unrealized_usd += (analysis["price"] - t["entry_price"]) * t["shares"]

        total_usd = realized_usd + unrealized_usd
        r_sign = "+" if realized_usd >= 0 else ""
        u_sign = "+" if unrealized_usd >= 0 else ""
        t_sign = "+" if total_usd >= 0 else ""

        telegram_bot.send(
            "💰 <b>P&L Summary</b>\n\n"
            "<code>"
            f"Realized    {r_sign}${realized_usd:.2f}  ({r_sign}S${realized_usd/sgd_to_usd:.0f})\n"
            f"Unrealized  {u_sign}${unrealized_usd:.2f}  ({u_sign}S${unrealized_usd/sgd_to_usd:.0f})\n"
            f"─────────────────────────\n"
            f"Total       {t_sign}${total_usd:.2f}  ({t_sign}S${total_usd/sgd_to_usd:.0f})"
            "</code>"
        )

    elif cmd == "/alert":
        if len(parts) < 3:
            telegram_bot.send("Usage: /alert AAPL 200.50")
            return
        symbol = parts[1].upper()
        try:
            target = float(parts[2])
        except ValueError:
            telegram_bot.send("Usage: /alert AAPL 200.50")
            return
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            telegram_bot.send(f"❌ Could not fetch data for {symbol}")
            return
        current = analysis["price"]
        direction = "above" if current < target else "below"
        alert_id = db.add_price_alert(symbol, target, direction)
        arrow = "↑" if direction == "above" else "↓"
        telegram_bot.send(
            f"🔔 Alert #{alert_id} set — <b>{symbol}</b>\n\n"
            f"<code>"
            f"Target   ${target:.2f}  {arrow}\n"
            f"Current  ${current:.2f}"
            f"</code>\n\n"
            f"You'll be notified when the price {'rises above' if direction == 'above' else 'falls below'} ${target:.2f}."
        )

    elif cmd == "/alerts":
        alerts = db.get_price_alerts()
        if not alerts:
            telegram_bot.send("No active price alerts. Use /alert AAPL 200 to set one.")
            return
        lines = ["🔔 <b>Active Alerts</b>", ""]
        for a in alerts:
            arrow = "↑" if a["direction"] == "above" else "↓"
            lines.append(f"<code>#{a['id']}  {a['symbol']:<6}  {arrow}  ${a['target']:.2f}</code>")
        lines.append("\nUse /removealert &lt;id&gt; to cancel one.")
        telegram_bot.send("\n".join(lines))

    elif cmd == "/removealert":
        if len(parts) < 2:
            telegram_bot.send("Usage: /removealert 1  (use /alerts to see IDs)")
            return
        try:
            alert_id = int(parts[1])
        except ValueError:
            telegram_bot.send("Usage: /removealert 1")
            return
        if db.remove_price_alert(alert_id):
            telegram_bot.send(f"✅ Alert #{alert_id} removed.")
        else:
            telegram_bot.send(f"Alert #{alert_id} not found. Use /alerts to see active ones.")

    elif cmd == "/share":
        if len(parts) < 2:
            telegram_bot.send("Usage: /share AAPL")
            return
        symbol = parts[1].upper()
        telegram_bot.send(f"⏳ Generating summary for {symbol}…")
        regime = await loop.run_in_executor(None, get_market_regime)
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            telegram_bot.send(f"❌ Could not fetch data for {symbol} — check the ticker")
            return
        fundamentals = await loop.run_in_executor(None, get_fundamentals, symbol)
        rel_strength = await loop.run_in_executor(None, get_relative_strength, symbol)
        signal = generate_signal(analysis, None, regime, fundamentals, rel_strength)
        sgd_to_usd = regime.get("sgd_to_usd", 0.74)
        size_mult = regime.get("new_position_size_multiplier", 1.0)
        pos_size = None
        if signal["action"] == "BUY":
            pos_size = calculate_position_size(
                PORTFOLIO_SIZE_SGD, analysis["price"], analysis["stop_loss"], sgd_to_usd, size_mult,
            )
        card = telegram_bot.format_share_card(symbol, signal, analysis, pos_size, fundamentals)
        if card:
            telegram_bot.send(card)
        else:
            telegram_bot.send(f"{symbol} has no actionable signal right now — signal is {signal['action']}")

    elif cmd == "/positions":
        rows = db.fetch("SELECT * FROM trades WHERE exit_price IS NULL ORDER BY entry_date DESC")

        if not rows:
            telegram_bot.send("📋 <b>Open Positions</b>\n\nNo open positions.")
            return

        lines = [f"📋 <b>Open Positions ({len(rows)})</b>", ""]
        for row in rows:
            t = dict(row)
            analysis = await loop.run_in_executor(None, get_ticker_analysis, t["symbol"])
            current = analysis["price"] if analysis else None
            if current:
                pnl_pct = ((current - t["entry_price"]) / t["entry_price"]) * 100
                sign = "+" if pnl_pct >= 0 else ""
                lines.append(
                    f"<code>{t['symbol']:<6}  {t['shares']:.2f}sh"
                    f"  in ${t['entry_price']:.2f}  now ${current:.2f}  {sign}{pnl_pct:.1f}%</code>"
                )
            else:
                lines.append(f"<code>{t['symbol']:<6}  {t['shares']:.2f}sh  in ${t['entry_price']:.2f}</code>")
        telegram_bot.send("\n".join(lines))

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
            actionable_signals.append({"symbol": symbol, "action": action, "reason": reason})
        elif action == "WATCH":
            watch_signals.append({"symbol": symbol, "reason": reason})

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
    scan_msg = telegram_bot.format_scan_results(scan)
    telegram_bot.send(scan_msg)


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

    actionable, watching = [], []
    for d in sgx_data:
        symbol = d["symbol"].replace(".SI", "")
        position = sgx_position_map.get(symbol)
        # Must match the sgx_watcher variant or briefing and alerts disagree
        signal = generate_signal(d["analysis"], position, regime,
                                 variant="SWING_LOW_NOCAP")
        action = signal["action"]
        price = d["analysis"]["price"]
        reason = signal["reasons"][0] if signal["reasons"] else signal["suggested_action"]
        if action in ACTIONABLE:
            actionable.append(f"  {action} {symbol} @ S${price:.3f} — {reason}")
        elif action == "WATCH":
            watching.append(f"  {symbol} @ S${price:.3f} — {reason}")

    lines = [f"🇸🇬 SGX Morning — {now_sgt.strftime('%a %d %b')}, opens 9:00 AM"]
    lines.append(f"Regime: {regime.get('regime','—')} ({regime.get('basis','STI')}) | VIX {regime.get('vix','—')}")
    lines.append("")
    if actionable:
        lines.append("Actionable:")
        lines.extend(actionable)
    else:
        lines.append("No actionable signals.")
    if watching:
        lines.append("")
        lines.append("Watching:")
        lines.extend(watching)

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
    label = "SGX Day Summary" if market == "SGX" else "US Session Summary (overnight)"
    lines = [f"{flag} <b>{label} — {datetime.now(SGT).strftime('%a %d %b')}</b>",
             f"Regime: {regime.get('regime','—')} ({regime.get('basis', 'SPY')})", ""]

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
    listener = asyncio.create_task(telegram_command_listener())
    briefing = asyncio.create_task(morning_briefing_task())
    sgx_briefing = asyncio.create_task(sgx_briefing_task())
    # Day summaries: US at 7:30 SGT Tue–Sat (Sun=6, Mon=0 skipped);
    # SGX at 17:45 SGT Mon–Fri (Sat=5, Sun=6 skipped)
    us_summary = asyncio.create_task(daily_summary_task("US", 7, 30, (6, 0)))
    sgx_summary = asyncio.create_task(daily_summary_task("SGX", 17, 45, (5, 6)))
    telegram_bot.set_bot_commands()
    telegram_bot.send(telegram_bot.format_startup(db.get_watchlist()))
    yield
    watcher.cancel()
    crypto.cancel()
    news_task.cancel()
    listener.cancel()
    briefing.cancel()
    us_summary.cancel()
    sgx_summary.cancel()


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
    return {"ok": True, "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7]}


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
    telegram_bot.send(telegram_bot.format_trade_entry(
        symbol, trade.shares, entry, trade.entry_date, analysis
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
    telegram_bot.send(telegram_bot.format_trade_exit(
        symbol, shares, entry_price, close.exit_price, sgd_to_usd
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
