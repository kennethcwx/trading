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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS options_trades (
            id            SERIAL PRIMARY KEY,
            symbol        TEXT NOT NULL,
            strategy      TEXT NOT NULL,
            phase         INTEGER NOT NULL DEFAULT 1,
            strike        REAL NOT NULL,
            long_strike   REAL,
            expiry_date   TEXT NOT NULL,
            dte_at_entry  INTEGER,
            premium       REAL NOT NULL,
            contracts     INTEGER NOT NULL DEFAULT 1,
            open_date     TEXT NOT NULL,
            close_date    TEXT,
            close_premium REAL,
            status        TEXT NOT NULL DEFAULT 'open',
            notes         TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """ if _USE_PG else """
        CREATE TABLE IF NOT EXISTS options_trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT NOT NULL,
            strategy      TEXT NOT NULL,
            phase         INTEGER NOT NULL DEFAULT 1,
            strike        REAL NOT NULL,
            long_strike   REAL,
            expiry_date   TEXT NOT NULL,
            dte_at_entry  INTEGER,
            premium       REAL NOT NULL,
            contracts     INTEGER NOT NULL DEFAULT 1,
            open_date     TEXT NOT NULL,
            close_date    TEXT,
            close_premium REAL,
            status        TEXT NOT NULL DEFAULT 'open',
            notes         TEXT,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id         SERIAL PRIMARY KEY,
            symbol     TEXT NOT NULL,
            target     REAL NOT NULL,
            direction  TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """ if _USE_PG else """
        CREATE TABLE IF NOT EXISTS price_alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            target     REAL NOT NULL,
            direction  TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_digest (
            id          SERIAL PRIMARY KEY,
            symbol      TEXT NOT NULL,
            move_date   TEXT NOT NULL,
            pct_change  REAL NOT NULL,
            price       REAL NOT NULL,
            headlines   TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, move_date)
        )
    """ if _USE_PG else """
        CREATE TABLE IF NOT EXISTS news_digest (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            move_date   TEXT NOT NULL,
            pct_change  REAL NOT NULL,
            price       REAL NOT NULL,
            headlines   TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, move_date)
        )
    """)
    conn.commit()

    # Migration: add long_strike to options_trades for pre-existing DBs
    if _USE_PG:
        cur.execute("ALTER TABLE options_trades ADD COLUMN IF NOT EXISTS long_strike REAL")
    else:
        cur.execute("PRAGMA table_info(options_trades)")
        cols = {row[1] for row in cur.fetchall()}
        if "long_strike" not in cols:
            cur.execute("ALTER TABLE options_trades ADD COLUMN long_strike REAL")
    conn.commit()

    # Migration: trailing stop + A/B strategy columns for trades
    if _USE_PG:
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS half_sold INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS peak_price REAL")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy TEXT DEFAULT 'A'")
    else:
        cur.execute("PRAGMA table_info(trades)")
        trade_cols = {row[1] for row in cur.fetchall()}
        if "half_sold" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN half_sold INTEGER DEFAULT 0")
        if "peak_price" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN peak_price REAL")
        if "strategy" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT 'A'")
    conn.commit()

    # Migration: entry-anchored exit levels. Stops/targets must be frozen at entry,
    # not recomputed from the current price (which made exit checks unreachable).
    if _USE_PG:
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_loss REAL")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS profit_target REAL")
    else:
        cur.execute("PRAGMA table_info(trades)")
        trade_cols = {row[1] for row in cur.fetchall()}
        if "stop_loss" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL")
        if "profit_target" not in trade_cols:
            cur.execute("ALTER TABLE trades ADD COLUMN profit_target REAL")
    # Backfill open legacy trades with the max-stop rule (8% stop, 2:1 target).
    # ATR at entry is unknowable retroactively; this is the conservative bound.
    cur.execute(
        "UPDATE trades SET stop_loss = entry_price * 0.92, profit_target = entry_price * 1.16 "
        "WHERE exit_price IS NULL AND stop_loss IS NULL"
    )
    conn.commit()

    # Migration: tag alert origin so stale auto-registered alerts (from BUY
    # signals whose trades were never taken) can be pruned without touching
    # alerts the user set manually.
    if _USE_PG:
        cur.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'")
    else:
        cur.execute("PRAGMA table_info(price_alerts)")
        alert_cols = {row[1] for row in cur.fetchall()}
        if "source" not in alert_cols:
            cur.execute("ALTER TABLE price_alerts ADD COLUMN source TEXT DEFAULT 'manual'")
    conn.commit()

    # Last-sent signal per (strategy, symbol) — persisted so Railway redeploys
    # don't re-fire every active signal on Telegram.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_state (
            strategy   TEXT NOT NULL,
            symbol     TEXT NOT NULL,
            action     TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy, symbol)
        )
    """)
    conn.commit()

    # SGX signal-vs-fill log. The SGX backtest edge dies at 0.5% slippage/leg,
    # so every actionable SGX alert records its signal price here and the user
    # reports the actual moomoo paper fill via /fill — the resulting series is
    # the go/no-go input for funding SGX (threshold ~0.2%).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sgx_fills (
            id            SERIAL PRIMARY KEY,
            symbol        TEXT NOT NULL,
            side          TEXT NOT NULL,
            signal_price  REAL NOT NULL,
            signal_qty    REAL,
            stop_loss     REAL,
            profit_target REAL,
            signal_ts     TEXT NOT NULL,
            fill_price    REAL,
            fill_qty      REAL,
            fill_ts       TEXT,
            slippage_pct  REAL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """ if _USE_PG else """
        CREATE TABLE IF NOT EXISTS sgx_fills (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT NOT NULL,
            side          TEXT NOT NULL,
            signal_price  REAL NOT NULL,
            signal_qty    REAL,
            stop_loss     REAL,
            profit_target REAL,
            signal_ts     TEXT NOT NULL,
            fill_price    REAL,
            fill_qty      REAL,
            fill_ts       TEXT,
            slippage_pct  REAL,
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Auto-logged paper tracks (added 2026-08-05). DELIBERATELY SEPARATE from
    # `trades`: signal_watcher builds its position map from `trades`, so writing
    # auto-filled rows there would make the S$5k run generate exit alerts for
    # positions the user never took, and would pollute /portfolio. Keeping this
    # in its own table is what makes "runs alongside, changes nothing" true.
    #
    # `track` scopes every row, so further tracks cost a config entry, not a
    # migration. Unlike `trades`, a half-sale realizes its P&L into
    # realized_pnl_usd and halves `shares` — the accounting has to close itself
    # out without a human, so a flag alone is not enough.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id               SERIAL PRIMARY KEY,
            track            TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            variant          TEXT NOT NULL,
            shares           REAL NOT NULL,
            entry_shares     REAL NOT NULL,
            entry_date       TEXT NOT NULL,
            entry_price      REAL NOT NULL,
            stop_loss        REAL,
            profit_target    REAL,
            exit_date        TEXT,
            exit_price       REAL,
            exit_reason      TEXT,
            half_sold        INTEGER DEFAULT 0,
            peak_price       REAL,
            realized_pnl_usd REAL DEFAULT 0,
            signal_reason    TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """ if _USE_PG else """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            track            TEXT NOT NULL,
            symbol           TEXT NOT NULL,
            variant          TEXT NOT NULL,
            shares           REAL NOT NULL,
            entry_shares     REAL NOT NULL,
            entry_date       TEXT NOT NULL,
            entry_price      REAL NOT NULL,
            stop_loss        REAL,
            profit_target    REAL,
            exit_date        TEXT,
            exit_price       REAL,
            exit_reason      TEXT,
            half_sold        INTEGER DEFAULT 0,
            peak_price       REAL,
            realized_pnl_usd REAL DEFAULT 0,
            signal_reason    TEXT,
            created_at       TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_paper_trades_track "
        "ON paper_trades (track, exit_price)"
    )
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


# ── Price alerts ──────────────────────────────────────────────────────────────

def get_price_alerts() -> list[dict]:
    return fetch("SELECT * FROM price_alerts ORDER BY id")


def add_price_alert(symbol: str, target: float, direction: str, source: str = "manual") -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO price_alerts (symbol, target, direction, source) VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()})"
        + (" RETURNING id" if _USE_PG else ""),
        (symbol, target, direction, source),
    )
    if _USE_PG:
        new_id = cur.fetchone()["id"]
    else:
        new_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return new_id


def remove_price_alert(alert_id: int) -> bool:
    row = fetchone("SELECT id FROM price_alerts WHERE id=?", (alert_id,))
    if not row:
        return False
    mutate("DELETE FROM price_alerts WHERE id=?", (alert_id,))
    return True


def prune_stale_auto_alerts(days: int = 14) -> int:
    """Delete auto-registered alerts older than `days` whose symbol has no open
    trade — BUY signals register stop/target alerts even if the trade is never
    taken, and those otherwise fire forever. Manual alerts are never touched."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    stale = fetch(
        "SELECT id FROM price_alerts WHERE source = 'auto' AND created_at < ? "
        "AND symbol NOT IN (SELECT symbol FROM trades WHERE exit_price IS NULL)",
        (cutoff,),
    )
    for row in stale:
        mutate("DELETE FROM price_alerts WHERE id=?", (row["id"],))
    return len(stale)


def remove_trade_alerts(symbol: str) -> None:
    """Drop stop/target alerts for a symbol after its trade closes (only if no
    other open trade holds that symbol). Manual alerts are kept."""
    still_open = fetchone(
        "SELECT id FROM trades WHERE symbol=? AND exit_price IS NULL", (symbol,)
    )
    if not still_open:
        mutate("DELETE FROM price_alerts WHERE symbol=? AND source IN ('auto', 'trade')", (symbol,))


# ── Signal state ──────────────────────────────────────────────────────────────

def load_signal_state(strategy: str) -> dict[str, str]:
    rows = fetch("SELECT symbol, action FROM signal_state WHERE strategy=?", (strategy,))
    return {r["symbol"]: r["action"] for r in rows}


def save_signal_state(strategy: str, symbol: str, action: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    if _USE_PG:
        cur.execute(
            "INSERT INTO signal_state (strategy, symbol, action, updated_at) "
            "VALUES (%s, %s, %s, CURRENT_TIMESTAMP) "
            "ON CONFLICT (strategy, symbol) DO UPDATE SET action = EXCLUDED.action, updated_at = CURRENT_TIMESTAMP",
            (strategy, symbol, action),
        )
    else:
        cur.execute(
            "INSERT INTO signal_state (strategy, symbol, action, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT (strategy, symbol) DO UPDATE SET action = excluded.action, updated_at = CURRENT_TIMESTAMP",
            (strategy, symbol, action),
        )
    conn.commit()
    cur.close()
    conn.close()


# ── SGX fill log ──────────────────────────────────────────────────────────────

def add_pending_fill(symbol: str, side: str, signal_price: float, signal_qty: float | None,
                     stop_loss: float | None, profit_target: float | None, signal_ts: str) -> None:
    mutate(
        "INSERT INTO sgx_fills (symbol, side, signal_price, signal_qty, stop_loss, profit_target, signal_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, side, signal_price, signal_qty, stop_loss, profit_target, signal_ts),
    )


def get_pending_fill(symbol: str) -> dict | None:
    """Most recent unreported signal for a symbol — what /fill matches against."""
    return fetchone(
        "SELECT * FROM sgx_fills WHERE symbol=? AND fill_price IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (symbol,),
    )


def record_fill(fill_id: int, fill_price: float, fill_qty: float | None,
                slippage_pct: float, fill_ts: str) -> None:
    mutate(
        "UPDATE sgx_fills SET fill_price=?, fill_qty=?, slippage_pct=?, fill_ts=? WHERE id=?",
        (fill_price, fill_qty, slippage_pct, fill_ts, fill_id),
    )


def get_recorded_fills() -> list[dict]:
    return fetch(
        "SELECT * FROM sgx_fills WHERE slippage_pct IS NOT NULL ORDER BY id"
    )


def get_pending_fills() -> list[dict]:
    """Every alert still awaiting /fill, oldest first — what the briefing chases."""
    return fetch(
        "SELECT * FROM sgx_fills WHERE fill_price IS NULL ORDER BY id"
    )


def get_last_recorded_fill() -> dict | None:
    """Most recently reported fill — what /undo reverts."""
    return fetchone(
        "SELECT * FROM sgx_fills WHERE fill_price IS NOT NULL "
        "ORDER BY fill_ts DESC, id DESC LIMIT 1"
    )


def clear_fill(fill_id: int) -> None:
    """Reset a fill back to pending so it can be re-reported with /fill."""
    mutate(
        "UPDATE sgx_fills SET fill_price=NULL, fill_qty=NULL, slippage_pct=NULL, fill_ts=NULL WHERE id=?",
        (fill_id,),
    )


def get_all_sgx_signals() -> list[dict]:
    """Every SGX order alert, filled or not — the /discipline dataset."""
    return fetch("SELECT * FROM sgx_fills ORDER BY id")


# ── Auto-logged paper tracks ──────────────────────────────────────────────────
# Every function here is scoped by `track` and touches only `paper_trades`.
# Nothing in this section may write to `trades` — that separation is the whole
# reason a second track can run without disturbing the first.

def open_paper_trade(track: str, symbol: str, variant: str, shares: float,
                     entry_date: str, entry_price: float,
                     stop_loss: float | None, profit_target: float | None,
                     signal_reason: str | None = None) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO paper_trades (track, symbol, variant, shares, entry_shares, "
        "entry_date, entry_price, stop_loss, profit_target, peak_price, signal_reason) "
        f"VALUES ({_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()}, {_ph()})"
        + (" RETURNING id" if _USE_PG else ""),
        (track, symbol, variant, shares, shares, entry_date, entry_price,
         stop_loss, profit_target, entry_price, signal_reason),
    )
    new_id = cur.fetchone()["id"] if _USE_PG else cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return new_id


def get_open_paper_trades(track: str) -> list[dict]:
    return fetch(
        "SELECT * FROM paper_trades WHERE track=? AND exit_price IS NULL ORDER BY id",
        (track,),
    )


def get_closed_paper_trades(track: str) -> list[dict]:
    return fetch(
        "SELECT * FROM paper_trades WHERE track=? AND exit_price IS NOT NULL "
        "ORDER BY exit_date, id",
        (track,),
    )


def get_all_paper_trades(track: str) -> list[dict]:
    return fetch("SELECT * FROM paper_trades WHERE track=? ORDER BY id", (track,))


def get_open_paper_trade(track: str, symbol: str) -> dict | None:
    return fetchone(
        "SELECT * FROM paper_trades WHERE track=? AND symbol=? AND exit_price IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (track, symbol),
    )


def update_paper_peak(trade_id: int, peak: float) -> None:
    mutate("UPDATE paper_trades SET peak_price=? WHERE id=?", (peak, trade_id))


def sell_half_paper_trade(trade_id: int, price: float) -> float:
    """Realize half the position and move the stop to breakeven.

    Returns the realized USD P&L on the half sold. Unlike `trades`, which only
    flags half_sold, this actually books the gain — an auto-logged track has no
    human to reconcile the remainder later, so the arithmetic has to close out
    on its own. Idempotent: a row already half-sold is left alone.
    """
    row = fetchone("SELECT * FROM paper_trades WHERE id=?", (trade_id,))
    if not row or row.get("half_sold") or row.get("exit_price") is not None:
        return 0.0
    half = row["shares"] / 2
    realized = (price - row["entry_price"]) * half
    mutate(
        "UPDATE paper_trades SET shares=?, half_sold=1, stop_loss=?, "
        "realized_pnl_usd=COALESCE(realized_pnl_usd, 0) + ? WHERE id=?",
        (row["shares"] - half, row["entry_price"], realized, trade_id),
    )
    return realized


def close_paper_trade(trade_id: int, exit_price: float, exit_date: str,
                      exit_reason: str) -> float:
    """Close the remaining shares. Returns realized USD P&L on this leg."""
    row = fetchone("SELECT * FROM paper_trades WHERE id=?", (trade_id,))
    if not row or row.get("exit_price") is not None:
        return 0.0
    realized = (exit_price - row["entry_price"]) * row["shares"]
    mutate(
        "UPDATE paper_trades SET exit_price=?, exit_date=?, exit_reason=?, "
        "realized_pnl_usd=COALESCE(realized_pnl_usd, 0) + ? WHERE id=?",
        (exit_price, exit_date, exit_reason, realized, trade_id),
    )
    return realized


# ── News digest ───────────────────────────────────────────────────────────────

def has_news_digest(symbol: str, move_date: str) -> bool:
    row = fetchone(
        "SELECT id FROM news_digest WHERE symbol=? AND move_date=?", (symbol, move_date)
    )
    return row is not None


def add_news_digest(symbol: str, move_date: str, pct_change: float, price: float, headlines: str) -> None:
    """headlines is a JSON string. Ignores the insert if (symbol, move_date) already exists."""
    conn = get_conn()
    cur = conn.cursor()
    if _USE_PG:
        cur.execute(
            "INSERT INTO news_digest (symbol, move_date, pct_change, price, headlines) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (symbol, move_date) DO NOTHING",
            (symbol, move_date, pct_change, price, headlines),
        )
    else:
        cur.execute(
            "INSERT OR IGNORE INTO news_digest (symbol, move_date, pct_change, price, headlines) "
            "VALUES (?, ?, ?, ?, ?)",
            (symbol, move_date, pct_change, price, headlines),
        )
    conn.commit()
    cur.close()
    conn.close()


def get_news_digest(limit: int = 30) -> list[dict]:
    return fetch(
        "SELECT * FROM news_digest ORDER BY move_date DESC, created_at DESC LIMIT ?", (limit,)
    )
