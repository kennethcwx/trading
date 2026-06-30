"""
Futu/moomoo SGX auto-trading via Futu OpenD.
Requires the Futu OpenD desktop app running on localhost:11111.
Set FUTU_PAPER=false in .env to switch from paper to live trading.
"""
import os
import logging

logger = logging.getLogger(__name__)

try:
    import futu as ft
    _SDK_AVAILABLE = True
except ImportError as e:
    logger.warning(f"futu-api not installed: {e}")
    _SDK_AVAILABLE = False

_trade_ctx: "ft.OpenSGTradeContext | None" = None
_AVAILABLE = False
_PAPER = True
_trd_env = None

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111


def _init():
    global _trade_ctx, _AVAILABLE, _PAPER, _trd_env
    if not _SDK_AVAILABLE:
        return

    _PAPER = os.environ.get("FUTU_PAPER", "true").lower() != "false"
    _trd_env = ft.TrdEnv.SIMULATE if _PAPER else ft.TrdEnv.REAL

    try:
        ctx = ft.OpenSGTradeContext(host=OPEND_HOST, port=OPEND_PORT)
        ret, data = ctx.get_acc_list()
        if ret != ft.RET_OK:
            logger.warning(f"Futu: get_acc_list failed — is OpenD running? ({data})")
            ctx.close()
            return
        _trade_ctx = ctx
        _AVAILABLE = True
        mode = "PAPER" if _PAPER else "LIVE"
        logger.info(f"Futu client initialised ({mode}), {len(data)} account(s)")
    except Exception as e:
        logger.warning(f"Futu init failed (OpenD not running?): {e}")


_init()


def is_available() -> bool:
    return _AVAILABLE


def is_paper() -> bool:
    return _PAPER


def get_account_summary() -> dict:
    if not _AVAILABLE or _trade_ctx is None:
        return {"cash": None, "total_value": None, "currency": "SGD", "connected": False}
    try:
        ret, data = _trade_ctx.accinfo_query(trd_env=_trd_env, currency=ft.Currency.SGD)
        if ret != ft.RET_OK:
            return {"cash": None, "total_value": None, "currency": "SGD", "connected": False}
        row = data.iloc[0]
        return {
            "cash":        round(float(row["cash"]), 2),
            "total_value": round(float(row["total_assets"]), 2),
            "currency":    "SGD",
            "connected":   True,
        }
    except Exception as e:
        logger.error(f"Futu account summary error: {e}")
        return {"cash": None, "total_value": None, "currency": "SGD", "connected": False}


def get_positions() -> list[dict]:
    if not _AVAILABLE or _trade_ctx is None:
        return []
    try:
        ret, data = _trade_ctx.position_list_query(trd_env=_trd_env)
        if ret != ft.RET_OK:
            return []
        return [
            {
                "symbol":         row["code"].replace("SG.", ""),
                "shares":         int(row["qty"]),
                "avg_cost":       round(float(row["cost_price"]), 4),
                "market_value":   round(float(row["market_val"]), 2),
                "unrealized_pnl": round(float(row["pl_val"]), 2),
            }
            for _, row in data.iterrows()
        ]
    except Exception as e:
        logger.error(f"Futu get_positions error: {e}")
        return []


def place_limit_order(symbol: str, qty: int, action: str, price: float) -> str | None:
    """
    symbol: bare SGX code without SG. prefix (e.g. "D05" for DBS)
    action: "BUY" or "SELL"
    Returns Futu order_id string on success, None on failure.
    """
    if not _AVAILABLE or _trade_ctx is None or qty <= 0:
        return None
    try:
        code     = f"SG.{symbol}"
        trd_side = ft.TrdSide.BUY if action == "BUY" else ft.TrdSide.SELL
        ret, data = _trade_ctx.place_order(
            price=round(price, 3),
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=ft.OrderType.NORMAL,
            trd_env=_trd_env,
        )
        if ret != ft.RET_OK:
            logger.error(f"Futu place_order failed: {data}")
            return None
        order_id = str(data["order_id"].values[0])
        logger.info(f"Futu order placed: {action} {qty} {symbol} @ S${price:.3f} → id {order_id}")
        return order_id
    except Exception as e:
        logger.error(f"Futu place_order ({action} {qty} {symbol}): {e}")
        return None


def cancel_order(order_id: str) -> bool:
    if not _AVAILABLE or _trade_ctx is None:
        return False
    try:
        ret, _ = _trade_ctx.modify_order(
            modify_order_op=ft.ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=_trd_env,
        )
        return ret == ft.RET_OK
    except Exception as e:
        logger.error(f"Futu cancel_order {order_id}: {e}")
        return False
