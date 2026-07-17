import os
import json
import urllib.request
import logging

from config import PROFIT_RATIO

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _post_message(text: str, parse_mode: str | None) -> None:
    payload = {"chat_id": CHAT_ID, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=8)


def send(text: str) -> bool:
    if not TOKEN or not CHAT_ID:
        logger.warning("Telegram not configured — skipping notification")
        return False
    try:
        _post_message(text, "HTML")
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed (HTML): {e} — retrying as plain text")
        try:
            _post_message(text, None)
            return True
        except Exception as e2:
            logger.error(f"Telegram send failed (plain text fallback too): {e2}")
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


# ── Message style system (agreed 2026-07-15) ─────────────────────────────────
# Single-event headers: "{action icon} <b>{Event} — {subject}</b> · {market icon}"
# (action-first: the color dot carries urgency, the flag closes the line).
# Market-scoped digests lead with the flag instead: "🇸🇬 <b>SGX Pre-Open — …</b>".
# Data lives in ONE <code> block per message with 8-char padded labels; SGX money
# is S$ with 3dp, US/crypto $ with 2dp; next-step icons are semantic:
# ⚡ act now · 📋 instructions · ⚠️ caution · 📝 log/record.

MARKET_ICON = {"US": "🇺🇸", "SGX": "🇸🇬", "CRYPTO": "🪙"}


def market_of(symbol: str) -> str:
    """Market from a yfinance-style symbol (bare SGX codes must be tagged by the caller)."""
    if symbol.endswith("-USD"):
        return "CRYPTO"
    if symbol.endswith(".SI"):
        return "SGX"
    return "US"


def money_for(market: str):
    cur = "S$" if market == "SGX" else "$"
    dp = 3 if market == "SGX" else 2

    def money(v: float) -> str:
        return f"{cur}{v:,.{dp}f}"
    return money


def format_signal(
    symbol: str,
    signal: dict,
    analysis: dict,
    position_size: dict | None,
    fundamentals: dict | None = None,
    stop: float | None = None,
    target: float | None = None,
    market: str = "US",
) -> str:
    action = signal["action"]
    price = analysis.get("price", 0)
    rsi = analysis.get("rsi")
    above_200 = analysis.get("above_200sma")
    # Callers running a swing-stop variant (SGX) pass their own levels;
    # everyone else falls back to the ATR-based analysis levels
    stop = stop if stop is not None else analysis.get("stop_loss", 0)
    target = target if target is not None else analysis.get("profit_target", 0)
    reason = signal["reasons"][0] if signal["reasons"] else ""
    # Legacy fallback for callers that don't tag the market: old crypto sizing
    # had no risk_sgd. New crypto sizing sets it, so the heuristic alone is
    # unreliable — crypto_watcher passes market="CRYPTO" explicitly.
    is_crypto = market == "CRYPTO" or (position_size is not None and position_size.get("risk_sgd") is None)
    trade_type = signal.get("trade_type", "")

    money = money_for(market)
    flag = MARKET_ICON.get(market, "")

    HEADER = {
        "BUY":       f"🟢 <b>BUY — {symbol}</b> · {flag}",
        "SELL":      f"🔴 <b>SELL — {symbol}</b> · {flag}",
        "SELL_HALF": f"🟠 <b>SELL HALF — {symbol}</b> · {flag}",
        "REVIEW":    f"🟡 <b>REVIEW — {symbol}</b> · {flag}",
    }

    lines = [HEADER.get(action, f"⚪ <b>{action} — {symbol}</b> · {flag}"), ""]
    lines.append(f"{trade_type} · {reason}" if trade_type else reason)

    if action == "BUY":
        fund_line = ""
        if fundamentals and not fundamentals.get("is_etf"):
            grade = fundamentals.get("grade", "?")
            score = fundamentals.get("score", "?")
            fund_line = f"\nFundamentals  {grade} ({score}/5)"

        # Entry zone: signal price up to +0.25 ATR. Past that the fill drifts
        # from the geometry the backtest validated — don't chase.
        atr = analysis.get("atr") or 0
        entry_hi = price + 0.25 * atr
        # Stop is a fixed level; target scales with the actual fill
        # (target = entry + PROFIT_RATIO × (entry − stop))
        target_hi = entry_hi + PROFIT_RATIO * (entry_hi - stop)

        if entry_hi > price:
            entry_line = f"Entry    {money(price)} – {money(entry_hi)}\n"
            stop_line = f"Stop     {money(stop)}  ({_pct(stop, price)} … {_pct(stop, entry_hi)})\n"
            target_line = f"Target   {money(target)} – {money(target_hi)}  ({_pct(target, price)})\n"
        else:
            entry_line = f"Entry    {money(price)}\n"
            stop_line = f"Stop     {money(stop)}  ({_pct(stop, price)})\n"
            target_line = f"Target   {money(target)}  ({_pct(target, price)})\n"

        size_lines = ""
        if position_size:
            size_lines = f"Shares   {position_size['shares']:g}  (S${position_size['position_value_sgd']:,.0f})\n"
            if position_size.get("risk_sgd") is not None:
                size_lines += f"Risk     S${position_size['risk_sgd']:,.0f}\n"

        lines += [
            fund_line,
            "",
            "<code>"
            + entry_line
            + size_lines
            + stop_line
            + target_line
            + f"RSI      {_rsi_label(rsi)}\n"
            + f"Trend    {_trend(above_200)}"
            + "</code>",
        ]
        if position_size and position_size.get("note"):
            lines.append(f"\n<i>{position_size['note']}</i>")
        if is_crypto:
            lines.append("\n⚡ Crypto — enter when ready (24/7 market)")
        elif market == "SGX":
            lines.append("\n⚡ SGX open now — limit order within the entry zone")
        else:
            lines.append("\n⚡ Execute at market open · Mon–Fri 9:30 AM ET")

    elif action == "SELL":
        lines += [
            "",
            "<code>"
            f"Price    {money(price)}\n"
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
            f"Price    {money(price)}\n"
            f"RSI      {_rsi_label(rsi)}\n"
            f"Target   {money(target)}  ({_pct(target, price)})"
            "</code>",
            "",
            "📋 Sell 50% at market open",
            "   Move stop to breakeven on remainder",
            "   Trail at 10% below peak once up another 15%",
        ]

    elif action == "REVIEW":
        days = analysis.get("days_to_earnings")
        days_str = f"{days} days" if days is not None else "soon"
        lines += [
            "",
            f"<code>Price    {money(price)}\nEarnings in {days_str}</code>",
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
    market: str = "US",
) -> str:
    stop = analysis.get("stop_loss", 0) if analysis else 0
    target = analysis.get("profit_target", 0) if analysis else 0
    money = money_for(market)

    data = f"Price    {money(entry_price)}\nShares   {shares:.3f}\nDate     {entry_date}"
    if stop and target:
        data += (
            f"\n\nStop     {money(stop)}  ({_pct(stop, entry_price)})\n"
            f"Target   {money(target)}  ({_pct(target, entry_price)})"
        )

    return f"📥 <b>Trade Logged — {symbol}</b> · {MARKET_ICON.get(market, '')}\n\n<code>{data}</code>"


def format_trade_exit(
    symbol: str,
    shares: float,
    entry_price: float,
    exit_price: float,
    sgd_to_usd: float = 0.74,
    market: str = "US",
) -> str:
    pnl = (exit_price - entry_price) * shares
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100
    sign = "+" if pnl >= 0 else "-"
    label = "✅ Win" if pnl >= 0 else "❌ Loss"
    money = money_for(market)

    # Sign leads the currency ("-S$165", never "S$-165")
    if market == "SGX":
        pnl_line = f"P&L      {sign}S${abs(pnl):,.2f}  ({sign}{abs(pnl_pct):.1f}%)"
    else:
        pnl_line = (f"P&L      {sign}${abs(pnl):,.2f}  ({sign}{abs(pnl_pct):.1f}%)"
                    f"  ·  {sign}S${abs(pnl / sgd_to_usd):,.0f}")

    return (
        f"📤 <b>Closed — {symbol}</b> · {MARKET_ICON.get(market, '')}\n\n"
        f"{label}\n"
        "<code>"
        f"Entry    {money(entry_price)}  →  Exit {money(exit_price)}\n"
        f"Shares   {shares:.3f}\n"
        f"{pnl_line}"
        "</code>"
    )


def format_news_alert(symbol: str, pct_change: float, price: float, headlines: list[dict], detected_at: str) -> str:
    icon = "🟢" if pct_change >= 0 else "🔴"
    sign = "+" if pct_change >= 0 else ""
    lines = [f"{icon} <b>{symbol}</b> {sign}{pct_change:.1f}%  ${price:,.2f}  <i>({detected_at})</i>", ""]
    for h in headlines:
        title = h.get("title", "")
        publisher = h.get("publisher")
        lines.append(f"{title} — {publisher}" if publisher else title)
    return "\n".join(lines)


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
            f"Entry  ${price:,.2f}  Stop ${stop:,.2f} ({_pct(stop, price)})  Target ${target:,.2f} ({_pct(target, price)})"
            + (f"\nSize   {ps['shares']:.4f}  (S${ps['position_value_sgd']:,.0f} ≈ ${ps['position_value_usd']:,.0f})" if ps else "")
            + "</code>",
        ]

    lines += ["", "<i>Not financial advice · crypto is 24/7 — enter when ready</i>"]
    return "\n".join(lines)


def format_scan_results(results: dict, tracked: list[str] | None = None) -> str:
    # Screening ideas, not entry signals: nothing here fires an entry alert
    # unless the symbol is on the watchlist, so every candidate line carries
    # its /add hint instead of BUY styling (user request 2026-07-17).
    regime     = results["regime"]
    buys       = results["buy_signals"]
    watches    = results["watch_signals"]
    wheels     = results["wheel_signals"]
    total      = results.get("total_scanned", 0)
    bullish    = regime.get("regime_ok", False)
    vix        = regime.get("vix", 0)
    tracked    = tracked or []

    lines = [
        f"🇺🇸 <b>US Screener — {total} stocks</b>",
        f"<code>Regime   {'BULLISH ▲' if bullish else 'BEARISH ▼'}\nVIX      {vix:.1f}</code>",
        "",
        "<i>Ideas for the watchlist, not buy signals — entries only confirm "
        "on a daily close for tracked symbols.</i>",
        "",
    ]

    # ── Candidates (screener setups) ──────────────────────────────────────────
    if buys:
        lines.append(f"🔎 <b>Candidates ({len(buys)})</b>")
        for b in buys:
            a    = b["analysis"]
            fund = b["fundamentals"]
            grade = (fund.get("grade") or "ETF") if fund and not fund.get("is_etf") else "ETF"
            reason = b["signal"]["reasons"][0]
            rs_str = f"  RS {b['rs_rank']}th" if b.get("rs_rank") is not None else ""
            trade_type = b["signal"].get("trade_type", "")
            type_tag = f"  {trade_type}" if trade_type else ""
            hint = ("already tracked — a real alert will confirm at the close"
                    if b["symbol"] in tracked else f"📝 /add {b['symbol']} to track it")
            lines.append(
                f"\n<b>{b['symbol']}</b>  [{grade}]{rs_str}{type_tag}  ~${a['price']:.2f}\n"
                f"  {reason}\n"
                f"  {hint}"
            )
    else:
        lines.append("🔎 <b>Candidates</b>  none — market may be extended")

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
        lines += ["", "<i>Verify IVR &gt; 30 before any options trade.</i>"]

    if not buys and not watches and not wheels:
        lines += ["", "Nothing notable right now. Check back after the next move."]

    lines += ["", "<i>Not financial advice.</i>"]
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
        f"🇺🇸 <b>US Pre-Open — {date_str}</b>",
        f"Opens tonight <code>{open_time_sgt} SGT</code> ({open_time_et} ET)",
        "",
        "📊 <b>Market</b>",
        "<code>"
        f"Regime   {'BULLISH ▲' if bullish else 'BEARISH ▼'}{size_warn}\n"
        f"VIX      {vix:.1f}  ({vix_label})\n"
        f"SGD/USD  {sgd:.4f}"
        "</code>",
    ]

    # Entries only confirm in the last 30 min of the session and queue into the
    # 7:30 AM summary — this pre-open recheck is the action point, so it leads.
    lines += ["", f"⚡ <b>Act at open ({open_time_sgt} SGT)</b>"]
    if actionable_signals:
        for s in actionable_signals:
            lines.append(f"• <b>{s['action'].replace('_', ' ')} {s['symbol']}</b> — {s['reason']}")
        lines.append("<i>Re-checked against the latest completed close — a queued entry from the 7:30 AM summary that isn't listed here is no longer valid.</i>")
    else:
        lines.append("• Nothing to do — hold positions as planned")

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

    if watch_signals:
        lines += ["", "👀 <b>Watch closely</b>"]
        for s in watch_signals:
            lines.append(f"• {s['symbol']} — {s['reason']}")

    lines += ["", "<i>Doesn't account for US public holidays · verify before trading</i>"]
    return "\n".join(lines)


def format_startup(watchlist: list[str]) -> str:
    tickers = "  ".join(watchlist) if watchlist else "none"
    return (
        "📊 <b>Trading Dashboard Online</b>\n\n"
        f"<code>Watching {tickers}</code>\n\n"
        "Signals will be sent as they trigger.\n"
        "Type /help for available commands."
    )


def set_bot_commands() -> bool:
    if not TOKEN:
        return False
    commands = [
        {"command": "portfolio",  "description": "Open positions + realized/unrealized P&L"},
        {"command": "fill",       "description": "Report moomoo fill for the last SGX alert — /fill D05 33.45"},
        {"command": "undo",       "description": "Revert the last /fill (wrong price/qty)"},
        {"command": "slippage",   "description": "SGX signal-vs-fill slippage report"},
        {"command": "discipline", "description": "Alerts acted on vs missed (validation criterion)"},
        {"command": "benchmark",  "description": "Validation P&L vs SPY since 2026-07-13"},
        {"command": "health",     "description": "Backend commit, watcher heartbeats, market regime"},
        {"command": "crypto",     "description": "Current signal for BTC, ETH, SOL"},
        {"command": "signal",     "description": "Signal for any ticker — /signal AAPL"},
        {"command": "scan",       "description": "Screen 70 stocks for watchlist candidates + wheel ideas"},
        {"command": "briefing",   "description": "Send the US pre-open briefing now"},
        {"command": "watchlist",  "description": "Show your watchlist"},
        {"command": "add",        "description": "Add ticker — /add AAPL"},
        {"command": "remove",     "description": "Remove ticker — /remove AAPL"},
        {"command": "help",       "description": "Show all commands"},
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
