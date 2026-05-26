import sqlite3
import os

_data_dir = os.environ.get("DATA_DIR", os.path.dirname(__file__))
DB_PATH = os.path.join(_data_dir, "trades.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                shares REAL NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_date TEXT,
                exit_price REAL,
                signal_reason TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
