import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    buyer_name TEXT, buyer_email TEXT, item TEXT,
    amount REAL, currency TEXT,
    order_date TEXT, delivery_status TEXT, delivered_date TEXT,
    capture_id TEXT, capture_mode TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT REFERENCES orders(id),
    channel TEXT, subject TEXT, message TEXT, received_at TEXT,
    status TEXT DEFAULT 'new',
    intent TEXT, sentiment TEXT, confidence REAL,
    action TEXT, amount REAL, reasoning TEXT, reply TEXT,
    risk TEXT, agent TEXT, trace TEXT,
    refund_id TEXT, final_message TEXT, error TEXT,
    edited INTEGER DEFAULT 0,
    updated_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
        return _conn


def reset():
    with _lock:
        c = conn()
        c.executescript("DROP TABLE IF EXISTS cases; DROP TABLE IF EXISTS orders;" + SCHEMA)
        c.commit()


def query(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args=()) -> int:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def update_case(case_id: int, **fields):
    if "trace" in fields and not isinstance(fields["trace"], str):
        fields["trace"] = json.dumps(fields["trace"])
    fields["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE cases SET {cols} WHERE id = ?", (*fields.values(), case_id))


def refunded_total(order_id: str) -> float:
    row = one("SELECT COALESCE(SUM(amount), 0) AS s FROM cases WHERE order_id = ? AND status = 'refunded'",
              (order_id,))
    return round(row["s"], 2)


def case_view(case_id: int) -> dict | None:
    return one(CASE_SELECT + " WHERE c.id = ?", (case_id,))


CASE_SELECT = """
SELECT c.*, o.buyer_name, o.buyer_email, o.item, o.amount AS order_amount, o.currency,
       o.order_date, o.delivery_status, o.delivered_date, o.capture_id, o.capture_mode
FROM cases c JOIN orders o ON o.id = c.order_id
"""
