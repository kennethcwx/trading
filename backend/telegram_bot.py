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


def format_startup(watchlist: list[str]) -> str:
    tickers = "  ".join(watchlist) if watchlist else "none"
    return (
        "📊 <b>Trading Dashboard online</b>\n\n"
        f"<code>Watching   {tickers}</code>\n\n"
        "Signals will be sent as they trigger."
    )
