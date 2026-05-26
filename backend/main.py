import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime

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
from config import WATCHLIST, PORTFOLIO_SIZE_SGD

logging.basicConfig(level=logging.INFO)

ACTIONABLE = {"BUY", "SELL", "SELL_HALF", "REVIEW"}
_last_signals: dict[str, str] = {}


async def signal_watcher():
    await asyncio.sleep(30)  # let startup finish first
    while True:
        try:
            loop = asyncio.get_event_loop()
            regime = await loop.run_in_executor(None, get_market_regime)
            sgd_to_usd = regime.get("sgd_to_usd", 0.74)
            size_mult = regime.get("new_position_size_multiplier", 1.0)

            for symbol in WATCHLIST:
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
                    msg = telegram_bot.format_signal(symbol, signal, analysis, pos_size)
                    telegram_bot.send(msg)

                _last_signals[symbol] = action

        except Exception as e:
            logging.warning(f"Signal watcher error: {e}")

        await asyncio.sleep(5 * 60)  # check every 5 minutes


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    ibkr.connect_background(paper=True)
    watcher = asyncio.create_task(signal_watcher())
    telegram_bot.send("📊 Trading dashboard started")
    yield
    watcher.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def status():
    return {
        "ibkr_connected": ibkr.is_connected(),
        "portfolio_size_sgd": PORTFOLIO_SIZE_SGD,
        "watchlist": WATCHLIST,
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
    return await loop.run_in_executor(None, get_market_regime)


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

    all_symbols = list(dict.fromkeys(WATCHLIST + list(position_map.keys())))

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
    for symbol in WATCHLIST:
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
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY entry_date DESC").fetchall()
    conn.close()

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
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO trades (symbol, shares, entry_date, entry_price, signal_reason, notes) VALUES (?, ?, ?, ?, ?, ?)",
        (trade.symbol.upper(), trade.shares, trade.entry_date, trade.entry_price, trade.signal_reason, trade.notes),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.put("/api/trades/{trade_id}/close")
async def close_trade(trade_id: int, close: TradeClose):
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM trades WHERE id=? AND exit_price IS NULL", (trade_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Trade not found or already closed")
    conn.execute(
        "UPDATE trades SET exit_date=?, exit_price=?, notes=? WHERE id=?",
        (close.exit_date, close.exit_price, close.notes, trade_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.delete("/api/trades/{trade_id}")
async def delete_trade(trade_id: int):
    conn = db.get_conn()
    conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
