import asyncio
import itertools
import logging
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import ibkr
import db
import telegram_bot
from analysis import get_market_regime, get_ticker_analysis, get_fundamentals, get_relative_strength, invalidate_cache
from signals import generate_signal, calculate_position_size
from config import PORTFOLIO_SIZE_SGD

logging.basicConfig(level=logging.INFO)

ACTIONABLE = {"BUY", "SELL", "SELL_HALF", "REVIEW"}
_last_signals: dict[str, str] = {}
_price_alerts: dict[int, dict] = {}
_alert_id_gen = itertools.count(1)

ET = ZoneInfo("America/New_York")


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

            # Signals
            for symbol in db.get_watchlist():
                analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
                if not analysis:
                    continue
                fundamentals = await loop.run_in_executor(None, get_fundamentals, symbol)
                rel_strength = await loop.run_in_executor(None, get_relative_strength, symbol)
                signal = generate_signal(analysis, None, regime, fundamentals, rel_strength)
                action = signal["action"]
                prev = _last_signals.get(symbol)

                if action in ACTIONABLE and action != prev:
                    pos_size = None
                    if action == "BUY":
                        pos_size = calculate_position_size(
                            PORTFOLIO_SIZE_SGD, analysis["price"],
                            analysis["stop_loss"], sgd_to_usd, size_mult,
                        )
                    msg = telegram_bot.format_signal(symbol, signal, analysis, pos_size, fundamentals)
                    telegram_bot.send(msg)

                _last_signals[symbol] = action

            # Price alerts
            fired_ids = []
            for alert_id, alert in list(_price_alerts.items()):
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
                    fired_ids.append(alert_id)
            for aid in fired_ids:
                _price_alerts.pop(aid, None)

        except Exception as e:
            logging.warning(f"Signal watcher error: {e}")

        await asyncio.sleep(5 * 60)  # check every 5 minutes


class WatchlistUpdate(BaseModel):
    symbols: list[str]


class TradeIn(BaseModel):
    symbol: str
    shares: float
    entry_date: str
    entry_price: float
    signal_reason: str | None = None
    notes: str | None = None


class TradeClose(BaseModel):
    exit_date: str
    exit_price: float
    notes: str | None = None


async def handle_telegram_command(text: str):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    loop = asyncio.get_event_loop()

    if cmd == "/help":
        telegram_bot.send(
            "<b>Available commands</b>\n\n"
            "/signal AAPL — current signal for any ticker\n"
            "/share AAPL — shareable summary to send to friends\n"
            "/positions — your open trades with live P&L\n"
            "/alert AAPL 200 — notify when price crosses a level\n"
            "/alerts — list active price alerts\n"
            "/removealert 1 — remove alert by ID\n"
            "/watchlist — show your watchlist\n"
            "/add AAPL — add ticker to watchlist\n"
            "/remove AAPL — remove ticker from watchlist\n"
            "/status — market regime overview"
        )

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
        msg = telegram_bot.format_signal(symbol, signal, analysis, pos_size, fundamentals)
        telegram_bot.send(msg)

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
        alert_id = next(_alert_id_gen)
        _price_alerts[alert_id] = {"symbol": symbol, "target": target, "direction": direction}
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
        if not _price_alerts:
            telegram_bot.send("No active price alerts. Use /alert AAPL 200 to set one.")
            return
        lines = ["🔔 <b>Active Alerts</b>", ""]
        for aid, a in _price_alerts.items():
            arrow = "↑" if a["direction"] == "above" else "↓"
            lines.append(f"<code>#{aid}  {a['symbol']:<6}  {arrow}  ${a['target']:.2f}</code>")
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
        if _price_alerts.pop(alert_id, None):
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
    for symbol in db.get_watchlist():
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            continue
        fundamentals = await loop.run_in_executor(None, get_fundamentals, symbol)
        rel_strength = await loop.run_in_executor(None, get_relative_strength, symbol)
        signal = generate_signal(analysis, None, regime, fundamentals, rel_strength)
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


async def morning_briefing_task():
    while True:
        try:
            now_et = datetime.now(ET)
            target = now_et.replace(hour=8, minute=30, second=0, microsecond=0)
            if now_et >= target:
                target += timedelta(days=1)
            await asyncio.sleep((target - now_et).total_seconds())
            now_et = datetime.now(ET)
            if now_et.weekday() < 5:  # Mon–Fri only
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    ibkr.connect_background(paper=True)
    watcher = asyncio.create_task(signal_watcher())
    listener = asyncio.create_task(telegram_command_listener())
    briefing = asyncio.create_task(morning_briefing_task())
    telegram_bot.set_bot_commands()
    telegram_bot.send(telegram_bot.format_startup(db.get_watchlist()))
    yield
    watcher.cancel()
    listener.cancel()
    briefing.cancel()


app = FastAPI(lifespan=lifespan)

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
async def signals():
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)
    size_mult = regime.get("new_position_size_multiplier", 1.0)

    positions = await loop.run_in_executor(None, ibkr.get_portfolio)
    position_map = {p["symbol"]: p for p in positions if p["asset_type"] == "STK"}

    all_symbols = list(dict.fromkeys(db.get_watchlist() + list(position_map.keys())))

    results = []
    for symbol in all_symbols:
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            continue

        fundamentals = await loop.run_in_executor(None, get_fundamentals, symbol)
        rel_strength = await loop.run_in_executor(None, get_relative_strength, symbol)

        position = position_map.get(symbol)
        signal = generate_signal(analysis, position, regime, fundamentals, rel_strength)

        pos_size = None
        if signal["action"] == "BUY":
            pos_size = calculate_position_size(
                PORTFOLIO_SIZE_SGD,
                analysis["price"],
                analysis["stop_loss"],
                sgd_to_usd,
                size_mult,
            )

        results.append({
            "symbol": symbol,
            "in_portfolio": symbol in position_map,
            "analysis": analysis,
            "fundamentals": fundamentals,
            "rel_strength": rel_strength,
            "signal": signal,
            "position_size": pos_size,
        })

    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    results.sort(key=lambda x: (
        priority_order.get(x["signal"]["priority"], 3),
        0 if x["in_portfolio"] else 1,
    ))

    return {
        "signals": results,
        "regime": regime,
        "generated_at": datetime.now().isoformat(),
    }


@app.get("/api/options-opportunities")
async def options_opportunities():
    loop = asyncio.get_event_loop()
    regime = await loop.run_in_executor(None, get_market_regime)
    sgd_to_usd = regime.get("sgd_to_usd", 0.74)

    opportunities = []
    for symbol in db.get_watchlist():
        analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
        if not analysis:
            continue
        if not analysis.get("above_200sma") or analysis.get("earnings_warning"):
            continue

        price = analysis["price"]
        # ~30-delta put strike: ~7% OTM
        strike = round(price * 0.93, 0)
        collateral_usd = strike * 100
        collateral_sgd = collateral_usd / sgd_to_usd
        feasible = collateral_sgd <= PORTFOLIO_SIZE_SGD * 0.8

        if feasible:
            note = "Check IVR > 30 on Market Chameleon before entering"
        else:
            note = f"Needs ~S${collateral_sgd:,.0f} collateral — above current portfolio size"

        opportunities.append({
            "symbol": symbol,
            "strategy": "Cash-Secured Put (Wheel)",
            "price": price,
            "suggested_strike": strike,
            "dte_target": "30–45 DTE",
            "collateral_usd": round(collateral_usd, 2),
            "collateral_sgd": round(collateral_sgd, 0),
            "feasible": feasible,
            "note": note,
            "rsi": analysis.get("rsi"),
            "days_to_earnings": analysis.get("days_to_earnings"),
        })

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
    db.mutate(
        "INSERT INTO trades (symbol, shares, entry_date, entry_price, signal_reason, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (symbol, trade.shares, trade.entry_date, trade.entry_price, trade.signal_reason, trade.notes),
    )
    loop = asyncio.get_event_loop()
    analysis = await loop.run_in_executor(None, get_ticker_analysis, symbol)
    telegram_bot.send(telegram_bot.format_trade_entry(
        symbol, trade.shares, trade.entry_price, trade.entry_date, analysis
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


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
