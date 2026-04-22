import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
from .models import CallRecord


DB_PATH = Path("token_manager.db")


def init_db(path: Path = DB_PATH) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_records (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     TEXT NOT NULL,
                agent_name     TEXT NOT NULL,
                model          TEXT NOT NULL,
                input_tokens   INTEGER NOT NULL,
                output_tokens  INTEGER NOT NULL,
                total_tokens   INTEGER NOT NULL,
                cost_usd       REAL NOT NULL,
                timestamp      TEXT NOT NULL,
                prompt_preview TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                session_id       TEXT PRIMARY KEY,
                max_tokens       INTEGER,
                max_cost_usd     REAL,
                alert_threshold  REAL DEFAULT 0.8
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON call_records(session_id)")
        conn.commit()


@contextmanager
def get_conn(path: Path = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_record(record: CallRecord, path: Path = DB_PATH) -> int:
    with get_conn(path) as conn:
        cursor = conn.execute("""
            INSERT INTO call_records
                (session_id, agent_name, model, input_tokens, output_tokens,
                 total_tokens, cost_usd, timestamp, prompt_preview)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.session_id, record.agent_name, record.model,
            record.input_tokens, record.output_tokens, record.total_tokens,
            record.cost_usd, record.timestamp.isoformat(), record.prompt_preview,
        ))
        return cursor.lastrowid


def get_session_totals(session_id: str, path: Path = DB_PATH) -> dict:
    with get_conn(path) as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
                COUNT(*)                         AS call_count
            FROM call_records WHERE session_id = ?
        """, (session_id,)).fetchone()
        return dict(row)


def get_session_records(session_id: str, path: Path = DB_PATH) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute("""
            SELECT * FROM call_records
            WHERE session_id = ?
            ORDER BY timestamp DESC
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]


def get_all_sessions_summary(path: Path = DB_PATH) -> list[dict]:
    with get_conn(path) as conn:
        rows = conn.execute("""
            SELECT
                session_id,
                COUNT(*)              AS call_count,
                SUM(total_tokens)     AS total_tokens,
                SUM(cost_usd)         AS total_cost_usd,
                MIN(timestamp)        AS first_call,
                MAX(timestamp)        AS last_call
            FROM call_records
            GROUP BY session_id
            ORDER BY last_call DESC
        """).fetchall()
        return [dict(r) for r in rows]


def upsert_budget(session_id: str, max_tokens: Optional[int],
                  max_cost_usd: Optional[float], alert_threshold: float,
                  path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.execute("""
            INSERT INTO budgets (session_id, max_tokens, max_cost_usd, alert_threshold)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                max_tokens      = excluded.max_tokens,
                max_cost_usd    = excluded.max_cost_usd,
                alert_threshold = excluded.alert_threshold
        """, (session_id, max_tokens, max_cost_usd, alert_threshold))


def get_budget(session_id: str, path: Path = DB_PATH) -> Optional[dict]:
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT * FROM budgets WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
