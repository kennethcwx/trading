import os
import json
import urllib.request
import logging

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send(text: str) -> bool:
    if not TOKEN or not CHAT_ID:
        logger.warning("Telegram not configured — skipping notification")
        return False
    try:
        data = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def _pct(current: float, reference: float) -> str:
    if reference <= 0:
        return ""
    p = ((current - reference) / reference) * 100
    return f"{'+' if p >= 0 else ''}{p:.1f}%"


def _rsi_label(rsi: float | None) -> str:
    if rsi is None:
        return "—"
    if rsi < 40:
        return f"{rsi:.0f} oversold"
    if rsi > 70:
        return f"{rsi:.0f} overbought"
    return f"{rsi:.0f} neutral"


def _trend(above_200: bool | None) -> str:
    if above_200 is None:
        return "—"
    return "▲ uptrend" if above_200 else "▼ broken"


def format_signal(
    symbol: str,
    signal: dict,
    analysis: dict,
    position_size: dict | None,
    fundamentals: dict | None = None,
) -> str:
    action = signal["action"]
    price = analysis.get("price", 0)
    rsi = analysis.get("rsi")
    above_200 = analysis.get("above_200sma")
    stop = analysis.get("stop_loss", 0)
    target = analysis.get("profit_target", 0)
    reason = signal["reasons"][0] if signal["reasons"] else ""
    is_crypto = position_size is not None and position_size.get("risk_sgd") is None

    HEADER = {
        "BUY":       f"🟢 <b>BUY — {symbol}</b>",
        "SELL":      f"🔴 <b>SELL — {symbol}</b>",
        "SELL_HALF": f"🟠 <b>SELL HALF — {symbol}</b>",
        "REVIEW":    f"🟡 <b>REVIEW — {symbol}</b>",
    }

    lines = [HEADER.get(action, f"⚪ <b>{action} — {symbol}</b>"), ""]
    lines.append(reason)

    if action == "BUY" and position_size:
        fund_line = ""
        if fundamentals and not fundamentals.get("is_etf"):
            grade = fundamentals.get("grade", "?")
            score = fundamentals.get("score", "?")
            fund_line = f"\nFundamentals  {grade} ({score}/5)"

        stop_pct = _pct(stop, price)
        target_pct = _pct(target, price)
        risk_line = (
            f"Risk     S${position_size['risk_sgd']:.0f}\n"
            if position_size.get("risk_sgd") is not None
            else ""
        )

        lines += [
            fund_line,
            "",
            "<code>"
            f"Price    ${price:.2f}\n"
            f"Shares   {position_size['shares']:.4f}  (S${position_size['position_value_sgd']:.0f})\n"
            f"Stop     ${stop:.2f}  ({stop_pct})\n"
            f"Target   ${target:.2f}  ({target_pct})\n"
            f"{risk_line}"
            f"RSI      {_rsi_label(rsi)}\n"
            f"Trend    {_trend(above_200)}"
            "</code>",
        ]
        if position_size.get("note"):
            lines.append(f"\n<i>{position_size['note']}</i>")
        if is_crypto:
            lines.append("\n⚡ Crypto — enter when ready (24/7 market)")
        else:
            lines.append("\n⚡ Execute at market open · Mon–Fri 9:30 AM ET")

    elif action == "SELL":
        lines += [
            "",
            "<code>"
            f"Price    ${price:.2f}\n"
            f"RSI      {_rsi_label(rsi)}\n"
            f"Trend    {_trend(above_200)}"
            "</code>",
            "",
            f"📋 {signal['suggested_action']}",
        ]

    elif action == "SELL_HALF":
        lines += [
            "",
            "<code>"
            f"Price    ${price:.2f}\n"
            f"RSI      {_rsi_label(rsi)}\n"
            f"Target   ${target:.2f}  ({_pct(target, price)})"
            "</code>",
            "",
            "📋 Sell 50% at market open",
            "   Move stop to breakeven on the rest",
        ]

    elif action == "REVIEW":
        days = analysis.get("days_to_earnings")
        days_str = f"{days} days" if days is not None else "soon"
        lines += [
            "",
            f"<code>Price    ${price:.2f}\nEarnings in {days_str}</code>",
            "",
            f"⚠️ {signal['suggested_action']}",
        ]

    return "\n".join(lines)


def format_trade_entry(
    symbol: str,
    shares: float,
    entry_price: float,
    entry_date: str,
    analysis: dict | None = None,
) -> str:
    stop = analysis.get("stop_loss", 0) if analysis else 0
    target = analysis.get("profit_target", 0) if analysis else 0

    data = f"Price    ${entry_price:.2f}\nShares   {shares:.3f}\nDate     {entry_date}"
    if stop and target:
        data += (
            f"\n\nStop     ${stop:.2f}  ({_pct(stop, entry_price)})\n"
            f"Target   ${target:.2f}  ({_pct(target, entry_price)})"
        )

    return f"📥 <b>TRADE LOGGED — {symbol}</b>\n\n<code>{data}</code>"


def format_trade_exit(
    symbol: str,
    shares: float,
    entry_price: float,
    exit_price: float,
    sgd_to_usd: float = 0.74,
) -> str:
    pnl_usd = (exit_price - entry_price) * shares
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
    pnl_sgd = pnl_usd / sgd_to_usd
    sign = "+" if pnl_usd >= 0 else ""
    label = "✅ WIN" if pnl_usd >= 0 else "❌ LOSS"

    return (
        f"📤 <b>CLOSED — {symbol}  {label}</b>\n\n"
        "<code>"
        f"Entry    ${entry_price:.2f}  →  Exit ${exit_price:.2f}\n"
        f"Shares   {shares:.3f}\n"
        f"P&L      {sign}${pnl_usd:.2f}  ({sign}{pnl_pct:.1f}%)  ·  S${pnl_sgd:+.0f}"
        "</code>"
    )


def _options_section(action: str, price: float, stop: float, target: float) -> str:
    if action == "BUY":
        call_strike = round(price * 1.05 / 0.5) * 0.5   # ~5% OTM call
        put_strike  = round(stop / 0.5) * 0.5            # put at stop level
        return (
            "\n🎯 <b>Options plays</b> (30–45 DTE, verify IVR &gt; 30 first)\n"
            "<code>"
            f"Long call   ${call_strike:.2f} strike  (~5% OTM)\n"
            f"            Bullish leverage, defined risk\n\n"
            f"Sell put    ${put_strike:.2f} strike  (at stop level)\n"
            f"            Get paid to wait for a cheaper entry"
            "</code>"
        )
    elif action == "SELL_HALF":
        call_strike = round(target / 0.5) * 0.5          # call at target — exit via premium
        return (
            "\n🎯 <b>Options plays</b> (30–45 DTE, verify IVR &gt; 30 first)\n"
            "<code>"
            f"Sell call   ${call_strike:.2f} strike  (at target)\n"
            f"            Harvest premium while stock fades"
            "</code>"
        )
    elif action == "SELL":
        put_strike = round(price * 0.95 / 0.5) * 0.5    # ~5% OTM put
        return (
            "\n🎯 <b>Options plays</b> (30–45 DTE, verify IVR &gt; 30 first)\n"
            "<code>"
            f"Buy put     ${put_strike:.2f} strike  (~5% OTM)\n"
            f"            Hedge or short with defined risk"
            "</code>"
        )
    return ""


def format_share_card(
    symbol: str,
    signal: dict,
    analysis: dict,
    position_size: dict | None,
    fundamentals: dict | None = None,
) -> str:
    action = signal["action"]
    price = analysis.get("price", 0)
    stop = analysis.get("stop_loss", 0)
    target = analysis.get("profit_target", 0)

    if action == "BUY":
        rr = round((target - price) / (price - stop), 1) if (price - stop) > 0 else 0
        fund_line = ""
        if fundamentals and not fundamentals.get("is_etf"):
            grade = fundamentals.get("grade", "?")
            good = fundamentals.get("reasons_good", [])
            fund_line = f"\n📊 Company health: Grade {grade}"
            if good:
                fund_line += f" — {good[0].lower()}"

        size_line = ""
        if position_size:
            size_line = f"\n💵 Position size: {position_size['shares']:.2f} shares (~S${position_size['position_value_sgd']:.0f})"

        return (
            f"📈 <b>Looking at {symbol}</b>\n\n"
            f"Trading at <b>${price:.2f}</b> — pulled back to an attractive entry zone while still in a long-term uptrend."
            f"{fund_line}\n\n"
            "<code>"
            f"Entry       ${price:.2f}\n"
            f"Stop loss   ${stop:.2f}  ({_pct(stop, price)})\n"
            f"Target      ${target:.2f}  ({_pct(target, price)})\n"
            f"Risk/Reward 1:{rr}"
            "</code>"
            f"{size_line}"
            f"{_options_section(action, price, stop, target)}\n\n"
            "<i>Not financial advice.</i>"
        )

    elif action in ("SELL", "SELL_HALF"):
        verb = "Trimming" if action == "SELL_HALF" else "Exiting"
        detail = (
            "Taking partial profits — selling half and moving stop to breakeven."
            if action == "SELL_HALF"
            else "Closing the full position to protect capital."
        )
        return (
            f"📉 <b>{symbol} — {verb} position</b>\n\n"
            f"Current price: <b>${price:.2f}</b>\n\n"
            f"{detail}"
            f"{_options_section(action, price, stop, target)}\n\n"
            "<i>Not financial advice.</i>"
        )

    return ""


def format_crypto_digest(rows: list[dict], sgd_to_usd: float = 0.74) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    time_str = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%H:%M SGT")

    ACTION_ICON = {
        "BUY": "🟢", "SELL": "🔴", "SELL_HALF": "🟠", "REVIEW": "🟡",
        "HOLD": "⚪", "WATCH": "👀", "SKIP": "⏭",
    }

    lines = [f"🪙 <b>Crypto Pulse — {time_str}</b>", ""]

    table, buy_rows = [], []
    for r in rows:
        coin   = r["symbol"].replace("-USD", "")
        action = r["action"]
        price  = r["analysis"]["price"]
        rsi    = r["analysis"].get("rsi") or 0
        pos    = r["position"]
        icon   = ACTION_ICON.get(action, "⚪")

        if pos:
            pnl = ((price - pos["avg_cost"]) / pos["avg_cost"]) * 100
            sign = "+" if pnl >= 0 else ""
            table.append(f"{icon} {coin:<4} {action:<9} RSI {rsi:>3.0f}  ${price:>10,.2f}  {sign}{pnl:.1f}%")
        else:
            table.append(f"{icon} {coin:<4} {action:<9} RSI {rsi:>3.0f}  ${price:>10,.2f}")
            if action == "BUY":
                buy_rows.append(r)

    lines.append("<code>" + "\n".join(table) + "</code>")

    for r in buy_rows:
        a      = r["analysis"]
        ps     = r["pos_size"]
        stop   = a["stop_loss"]
        target = a["profit_target"]
        price  = a["price"]
        reason = r["signal"]["reasons"][0] if r["signal"]["reasons"] else ""
        lines += [
            "",
            f"💡 <b>Buy {r['symbol']}</b> — {reason}",
            "<code>"
            f"Entry  ${price:.2f}  Stop ${stop:.2f} ({_pct(stop, price)})  Target ${target:.2f} ({_pct(target, price)})"
            + (f"\nSize   {ps['shares']:.4f}  (S${ps['position_value_sgd']:.0f} ≈ ${ps['position_value_usd']:.0f})" if ps else "")
            + "</code>",
        ]

    lines += ["", "<i>Not financial advice · crypto is 24/7 — enter when ready</i>"]
    return "\n".join(lines)


def format_scan_results(results: dict) -> str:
    regime     = results["regime"]
    buys       = results["buy_signals"]
    watches    = results["watch_signals"]
    wheels     = results["wheel_signals"]
    total      = results.get("total_scanned", 0)
    bullish    = regime.get("regime_ok", False)
    vix        = regime.get("vix", 0)

    lines = [
        f"🔍 <b>Market Scan — {total} stocks</b>",
        f"<code>Regime {'BULLISH ▲' if bullish else 'BEARISH ▼'}   VIX {vix:.1f}</code>",
        "",
    ]

    # ── BUY setups ────────────────────────────────────────────────────────────
    if buys:
        lines.append(f"🟢 <b>BUY Setups ({len(buys)})</b>")
        for b in buys:
            a    = b["analysis"]
            fund = b["fundamentals"]
            ps   = b["position_size"]
            grade = (fund.get("grade") or "ETF") if fund and not fund.get("is_etf") else "ETF"
            reason = b["signal"]["reasons"][0]
            rs_str = f"  RS {b['rs_rank']}th" if b.get("rs_rank") is not None else ""
            size_str = (
                f"\n  <code>Size  {ps['shares']:.3f} sh  S${ps['position_value_sgd']:.0f}</code>"
                if ps else ""
            )
            lines.append(
                f"\n<b>{b['symbol']}</b>  [{grade}]{rs_str}\n"
                f"  {reason}\n"
                f"  <code>Entry ~${a['price']:.2f}  Stop ${a['stop_loss']:.2f}  "
                f"Target ${a['profit_target']:.2f}</code>"
                f"{size_str}"
            )
    else:
        lines.append("🟢 <b>BUY Setups</b>  none — market may be extended")

    # ── Close to entry ────────────────────────────────────────────────────────
    if watches:
        lines += ["", f"👀 <b>Close to Entry ({len(watches)})</b>"]
        for w in watches:
            rsi = w["analysis"].get("rsi")
            suggested = w["signal"]["suggested_action"]
            lines.append(f"• <b>{w['symbol']}</b>  RSI {rsi:.0f}  — {suggested}")

    # ── Wheel opportunities ───────────────────────────────────────────────────
    if wheels:
        lines += ["", f"🎡 <b>Wheel — Sell Put ({len(wheels)})</b>"]
        for w in wheels:
            a     = w["analysis"]
            fund  = w["fundamentals"]
            grade = (fund.get("grade") or "?") if fund and not fund.get("is_etf") else "?"
            rsi   = a.get("rsi")
            lines.append(
                f"• <b>{w['symbol']}</b>  [{grade}]  RSI {rsi:.0f}  "
                f"Strike ${w['strike']}  Collateral S${w['collateral_sgd']:,.0f}\n"
                f"  {w['options_signal']['reason']}"
            )

    if not buys and not watches and not wheels:
        lines += ["", "Nothing actionable right now. Check back after the next move."]

    lines += ["", "<i>Verify IVR &gt; 30 before any options trade. Not financial advice.</i>"]
    return "\n".join(lines)


def format_morning_briefing(
    date_str: str,
    open_time_et: str,
    open_time_sgt: str,
    regime: dict,
    open_trades: list[dict],
    actionable_signals: list[dict],
    watch_signals: list[dict],
) -> str:
    bullish = regime.get("regime_ok", False)
    vix = regime.get("vix", 0)
    sgd = regime.get("sgd_to_usd", 0.74)
    vix_label = "calm" if vix < 20 else "elevated" if vix < 30 else "fearful"
    mult = regime.get("new_position_size_multiplier", 1.0)
    size_warn = "  ⚠️ half size" if mult < 1 else ""

    lines = [
        f"🌅 <b>Morning Briefing — {date_str}</b>",
        "",
        f"📅 US market opens today",
        f"   <code>9:30 AM ET  ·  {open_time_sgt} SGT</code>",
        "",
        "📊 <b>Market</b>",
        "<code>"
        f"Regime   {'BULLISH ▲' if bullish else 'BEARISH ▼'}{size_warn}\n"
        f"VIX      {vix:.1f}  ({vix_label})\n"
        f"SGD/USD  {sgd:.4f}"
        "</code>",
    ]

    if open_trades:
        lines += ["", f"📋 <b>Open Positions ({len(open_trades)})</b>"]
        for t in open_trades:
            if t.get("pnl_pct") is not None:
                sign = "+" if t["pnl_pct"] >= 0 else ""
                lines.append(
                    f"<code>{t['symbol']:<6}  {t['shares']:.2f}sh"
                    f"  in ${t['entry_price']:.2f}  {sign}{t['pnl_pct']:.1f}%</code>"
                )
            else:
                lines.append(f"<code>{t['symbol']:<6}  {t['shares']:.2f}sh  in ${t['entry_price']:.2f}</code>")
    else:
        lines += ["", "📋 <b>Open Positions</b>  none"]

    lines += ["", "⚡ <b>Act at market open (9:30 AM ET)</b>"]
    if actionable_signals:
        for s in actionable_signals:
            lines.append(f"• <b>{s['action'].replace('_', ' ')} {s['symbol']}</b> — {s['reason']}")
    else:
        lines.append("• Nothing to do — hold positions as planned")

    if watch_signals:
        lines += ["", "👀 <b>Watch closely</b>"]
        for s in watch_signals:
            lines.append(f"• {s['symbol']} — {s['reason']}")

    lines += ["", "<i>Doesn't account for US public holidays. Verify before trading.</i>"]
    return "\n".join(lines)


def format_startup(watchlist: list[str]) -> str:
    tickers = "  ".join(watchlist) if watchlist else "none"
    return (
        "📊 <b>Trading Dashboard online</b>\n\n"
        f"<code>Watching   {tickers}</code>\n\n"
        "Signals will be sent as they trigger.\n"
        "Type /help for available commands."
    )


def set_bot_commands() -> bool:
    if not TOKEN:
        return False
    commands = [
        {"command": "scan",         "description": "Scan 70 stocks for BUY setups + wheel opportunities"},
        {"command": "crypto",      "description": "Current signal for BTC, ETH, SOL"},
        {"command": "briefing",    "description": "Send today's morning briefing now"},
        {"command": "signal",      "description": "Signal for any ticker — /signal AAPL"},
        {"command": "share",       "description": "Shareable summary for friends — /share AAPL"},
        {"command": "positions",   "description": "Open trades with live P&L"},
        {"command": "pnl",         "description": "Total realized + unrealized P&L"},
        {"command": "alert",       "description": "Set price alert — /alert AAPL 200"},
        {"command": "alerts",      "description": "List active price alerts"},
        {"command": "removealert", "description": "Remove alert by ID — /removealert 1"},
        {"command": "status",      "description": "Market regime, VIX, SGD/USD"},
        {"command": "watchlist",   "description": "Show your watchlist"},
        {"command": "add",         "description": "Add ticker — /add AAPL"},
        {"command": "remove",      "description": "Remove ticker — /remove AAPL"},
        {"command": "help",        "description": "Show all commands"},
    ]
    try:
        data = json.dumps({"commands": commands}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/setMyCommands",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception as e:
        logger.warning(f"Telegram setMyCommands failed: {e}")
        return False


def get_updates(offset: int = 0, timeout: int = 20) -> list[dict]:
    if not TOKEN:
        return []
    try:
        data = json.dumps({
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=timeout + 5)
        return json.loads(resp.read()).get("result", [])
    except Exception as e:
        logger.warning(f"Telegram getUpdates failed: {e}")
        return []
