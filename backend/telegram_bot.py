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

        lines += [
            fund_line,
            "",
            "<code>"
            f"Price    ${price:.2f}\n"
            f"Shares   {position_size['shares']:.3f}  (S${position_size['position_value_sgd']:.0f})\n"
            f"Stop     ${stop:.2f}  ({stop_pct})\n"
            f"Target   ${target:.2f}  ({target_pct})\n"
            f"Risk     S${position_size['risk_sgd']:.0f}\n"
            f"RSI      {_rsi_label(rsi)}\n"
            f"Trend    {_trend(above_200)}"
            "</code>",
        ]
        if position_size.get("note"):
            lines.append(f"\n<i>{position_size['note']}</i>")
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


def format_startup(watchlist: list[str]) -> str:
    tickers = "  ".join(watchlist) if watchlist else "none"
    return (
        "📊 <b>Trading Dashboard online</b>\n\n"
        f"<code>Watching   {tickers}</code>\n\n"
        "Signals will be sent as they trigger.\n"
        "Type /help for available commands."
    )


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
