import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras


# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    if _USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    else:
        _data_dir = os.environ.get("DATA_DIR", os.path.dirname(__file__))
        path = os.path.join(_data_dir, "trades.db")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn


def _ph() -> str:
    """Return the correct placeholder for the active DB."""
    return "%s" if _USE_PG else "?"


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          SERIAL PRIMARY KEY,
            symbol      TEXT NOT NULL,
            shares      REAL NOT NULL,
            entry_date  TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_date   TEXT,
            exit_price  REAL,
            signal_reason TEXT,
            notes       TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """ if _USE_PG else """
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT NOT NULL,
            shares        REAL NOT NULL,
            entry_date    TEXT NOT NULL,
            entry_price   REAL NOT NULL,
            exit_date     TEXT,
            exit_price    REAL,
            signal_reason TEXT,
            notes         TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


# ── Query helpers ─────────────────────────────────────────────────────────────

def fetch(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql.replace("?", _ph()), params)
    rows = [dict(r) for r in (cur.fetchall() or [])]
    cur.close()
    conn.close()
    return rows


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql.replace("?", _ph()), params)
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def mutate(sql: str, params: tuple = ()) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql.replace("?", _ph()), params)
    conn.commit()
    cur.close()
    conn.close()


# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist() -> list[str]:
    rows = fetch("SELECT symbol FROM watchlist ORDER BY symbol")
    if not rows:
        from config import WATCHLIST
        set_watchlist(list(WATCHLIST))
        return list(WATCHLIST)
    return [r["symbol"] for r in rows]


def set_watchlist(symbols: list[str]) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist")
    for s in symbols:
        cur.execute(f"INSERT INTO watchlist (symbol) VALUES ({_ph()})", (s,))
    conn.commit()
    cur.close()
    conn.close()
