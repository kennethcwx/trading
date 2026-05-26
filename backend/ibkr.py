"""
IBKR connection via ib_insync.
ib_insync has a known incompatibility with Python 3.12+ event loop handling.
If the import fails, all functions return empty/offline results gracefully —
the dashboard still works fully via yfinance data.
"""
import threading
import logging

logger = logging.getLogger(__name__)

# Optional import — fails on Python 3.12+ with eventkit's event loop assumption
try:
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    from ib_insync import IB
    _ib = IB()
    _IB_AVAILABLE = True
except Exception as e:
    logger.warning(f"ib_insync unavailable: {e}")
    _ib = None
    _IB_AVAILABLE = False

from config import IBKR_HOST, IBKR_PAPER_PORT, IBKR_LIVE_PORT, IBKR_CLIENT_ID

_connected = False
_lock = threading.Lock()


def _try_connect(port: int):
    global _connected
    if not _IB_AVAILABLE or _ib is None:
        return
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    with _lock:
        try:
            if _ib.isConnected():
                _connected = True
                return
            _ib.connect(IBKR_HOST, port, clientId=IBKR_CLIENT_ID, readonly=True, timeout=8)
            _connected = True
            logger.info(f"IBKR connected on port {port}")
        except Exception as e:
            _connected = False
            logger.warning(f"IBKR connection failed: {e}")


def connect_background(paper: bool = True):
    port = IBKR_PAPER_PORT if paper else IBKR_LIVE_PORT
    threading.Thread(target=_try_connect, args=(port,), daemon=True).start()


def reconnect(paper: bool = True):
    global _connected
    if _IB_AVAILABLE and _ib is not None:
        with _lock:
            try:
                if _ib.isConnected():
                    _ib.disconnect()
            except Exception:
                pass
    _connected = False
    connect_background(paper)


def is_connected() -> bool:
    if not _IB_AVAILABLE or _ib is None:
        return False
    try:
        return _connected and _ib.isConnected()
    except Exception:
        return False


def get_portfolio() -> list[dict]:
    if not is_connected() or _ib is None:
        return []
    try:
        items = _ib.portfolio()
        result = []
        for item in items:
            c = item.contract
            result.append({
                "symbol": c.symbol,
                "shares": item.position,
                "avg_cost": round(item.averageCost, 4),
                "market_price": round(item.marketPrice, 2),
                "market_value": round(item.marketValue, 2),
                "unrealized_pnl": round(item.unrealizedPNL, 2),
                "asset_type": c.secType,
            })
        return result
    except Exception as e:
        logger.error(f"Portfolio fetch error: {e}")
        return []


def get_account_summary() -> dict:
    if not is_connected() or _ib is None:
        return {"cash": None, "total_value": None, "currency": "USD", "connected": False}
    try:
        items = _ib.accountSummary()
        summary = {i.tag: i.value for i in items}
        return {
            "cash": float(summary.get("AvailableFunds", 0)),
            "total_value": float(summary.get("NetLiquidation", 0)),
            "currency": summary.get("Currency", "USD"),
            "connected": True,
        }
    except Exception as e:
        logger.error(f"Account summary error: {e}")
        return {"cash": None, "total_value": None, "currency": "USD", "connected": False}
