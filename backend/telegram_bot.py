import os
import json
import urllib.request
import logging

logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

EMOJI = {
    "BUY":       "🟢",
    "SELL":      "🔴",
    "SELL_HALF": "🟠",
    "REVIEW":    "🟡",
}


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


def format_signal(symbol: str, signal: dict, analysis: dict, position_size: dict | None) -> str:
    action = signal["action"]
    emoji = EMOJI.get(action, "⚪")
    reason = signal["reasons"][0] if signal["reasons"] else ""

    lines = [f"{emoji} <b>{action.replace('_', ' ')} — {symbol}</b>", "", reason, ""]

    if action == "BUY" and position_size:
        lines += [
            f"💰 Buy {position_size['shares']:.2f} shares at ~${analysis['price']:.2f}",
            f"🛑 Stop if falls to ${analysis['stop_loss']:.2f}",
            f"🎯 Target ${analysis['profit_target']:.2f}",
        ]
    elif action in ("SELL", "SELL_HALF"):
        lines.append(f"📋 {signal['suggested_action']}")
    elif action == "REVIEW":
        lines.append(f"⚠️ {signal['suggested_action']}")

    return "\n".join(lines)
