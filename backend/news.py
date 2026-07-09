"""Zero-cost news correlation: pairs notable price moves with yfinance headlines.
No Claude/LLM calls — yf.Ticker(...).news is free, so this only costs what
yfinance already costs (nothing beyond the existing dependency).
"""
import logging

import yfinance as yf


def get_daily_pct_change(yf_symbol: str) -> tuple[float, float] | None:
    """Returns (pct_change, current_price) comparing today's close to the prior close."""
    try:
        hist = yf.Ticker(yf_symbol).history(period="5d")
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        prev_close = float(closes.iloc[-2])
        price = float(closes.iloc[-1])
        if prev_close == 0:
            return None
        pct_change = round((price - prev_close) / prev_close * 100, 2)
        return pct_change, round(price, 2)
    except Exception as e:
        logging.warning(f"news: pct change fetch failed for {yf_symbol}: {e}")
        return None


def get_recent_news(yf_symbol: str, limit: int = 3) -> list[dict]:
    """Recent headlines for a ticker. Handles both the flat and nested (>=0.2.4x
    'content') yfinance response shapes defensively."""
    try:
        raw = yf.Ticker(yf_symbol).news or []
    except Exception as e:
        logging.warning(f"news: headline fetch failed for {yf_symbol}: {e}")
        return []

    headlines: list[dict] = []
    for item in raw[:limit]:
        content = item.get("content", item)  # nested shape falls back to flat
        title = content.get("title")
        if not title:
            continue
        link = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
            or content.get("link")
        )
        publisher = (content.get("provider") or {}).get("displayName") or content.get("publisher")
        published = content.get("pubDate") or content.get("providerPublishTime")
        headlines.append({
            "title": title,
            "link": link,
            "publisher": publisher,
            "published": str(published) if published else None,
        })
    return headlines
