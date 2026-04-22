import os
import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
from .models import CallRecord, calculate_cost


DB_PATH = Path(os.getenv("DB_PATH", "token_manager.db"))


def init_db(path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email           TEXT UNIQUE NOT NULL,
                hashed_password TEXT NOT NULL,
                tm_api_key      TEXT UNIQUE NOT NULL,
                created_at      TEXT NOT NULL,
                is_active       INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_records (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER REFERENCES users(id),
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
                session_id       TEXT NOT NULL,
                user_id          INTEGER REFERENCES users(id),
                max_tokens       INTEGER,
                max_cost_usd     REAL,
                alert_threshold  REAL DEFAULT 0.8,
                PRIMARY KEY (session_id, user_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON call_records(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON call_records(user_id)")
        conn.commit()

        # migrate existing tables that may be missing user_id
        for sql in [
            "ALTER TABLE call_records ADD COLUMN user_id INTEGER REFERENCES users(id)",
            "ALTER TABLE budgets ADD COLUMN user_id INTEGER REFERENCES users(id)",
        ]:
            try:
                conn.execute(sql)
                conn.commit()
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()


@contextmanager
def get_conn(path: Path = DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------

def create_user(email: str, hashed_password: str, tm_api_key: str,
                path: Path = DB_PATH) -> int:
    with get_conn(path) as conn:
        cursor = conn.execute("""
            INSERT INTO users (email, hashed_password, tm_api_key, created_at)
            VALUES (?, ?, ?, ?)
        """, (email, hashed_password, tm_api_key, datetime.utcnow().isoformat()))
        return cursor.lastrowid


def get_user_by_email(email: str, path: Path = DB_PATH) -> Optional[dict]:
    with get_conn(path) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int, path: Path = DB_PATH) -> Optional[dict]:
    with get_conn(path) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_tm_key(tm_api_key: str, path: Path = DB_PATH) -> Optional[dict]:
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE tm_api_key = ?", (tm_api_key,)
        ).fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------
# Call records
# ------------------------------------------------------------------

def insert_record(record: CallRecord, user_id: Optional[int] = None,
                  path: Path = DB_PATH) -> int:
    with get_conn(path) as conn:
        cursor = conn.execute("""
            INSERT INTO call_records
                (user_id, session_id, agent_name, model, input_tokens, output_tokens,
                 total_tokens, cost_usd, timestamp, prompt_preview)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            record.session_id, record.agent_name, record.model,
            record.input_tokens, record.output_tokens, record.total_tokens,
            record.cost_usd, record.timestamp.isoformat(), record.prompt_preview,
        ))
        return cursor.lastrowid


def get_session_totals(session_id: str, user_id: Optional[int] = None,
                       path: Path = DB_PATH) -> dict:
    with get_conn(path) as conn:
        where = "session_id = ?" + (" AND user_id = ?" if user_id else "")
        params = (session_id, user_id) if user_id else (session_id,)
        row = conn.execute(f"""
            SELECT
                COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                COALESCE(SUM(cost_usd), 0.0)    AS cost_usd,
                COUNT(*)                         AS call_count
            FROM call_records WHERE {where}
        """, params).fetchone()
        return dict(row)


def get_session_records(session_id: str, user_id: Optional[int] = None,
                        path: Path = DB_PATH) -> list[dict]:
    with get_conn(path) as conn:
        where = "session_id = ?" + (" AND user_id = ?" if user_id else "")
        params = (session_id, user_id) if user_id else (session_id,)
        rows = conn.execute(f"""
            SELECT * FROM call_records WHERE {where} ORDER BY timestamp DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


def get_all_sessions_summary(user_id: Optional[int] = None,
                             path: Path = DB_PATH) -> list[dict]:
    with get_conn(path) as conn:
        where = f"WHERE user_id = {user_id}" if user_id else ""
        rows = conn.execute(f"""
            SELECT
                session_id,
                COUNT(*)              AS call_count,
                SUM(total_tokens)     AS total_tokens,
                SUM(cost_usd)         AS total_cost_usd,
                MIN(timestamp)        AS first_call,
                MAX(timestamp)        AS last_call
            FROM call_records {where}
            GROUP BY session_id
            ORDER BY last_call DESC
        """).fetchall()
        return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Budgets
# ------------------------------------------------------------------

def upsert_budget(session_id: str, max_tokens: Optional[int],
                  max_cost_usd: Optional[float], alert_threshold: float,
                  user_id: Optional[int] = None,
                  path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.execute("""
            INSERT INTO budgets (session_id, user_id, max_tokens, max_cost_usd, alert_threshold)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, user_id) DO UPDATE SET
                max_tokens      = excluded.max_tokens,
                max_cost_usd    = excluded.max_cost_usd,
                alert_threshold = excluded.alert_threshold
        """, (session_id, user_id, max_tokens, max_cost_usd, alert_threshold))


def get_budget(session_id: str, user_id: Optional[int] = None,
               path: Path = DB_PATH) -> Optional[dict]:
    with get_conn(path) as conn:
        if user_id:
            row = conn.execute(
                "SELECT * FROM budgets WHERE session_id = ? AND user_id = ?",
                (session_id, user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM budgets WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

def get_stats(user_id: Optional[int] = None, path: Path = DB_PATH) -> dict:
    with get_conn(path) as conn:
        where = f"WHERE user_id = {user_id}" if user_id else ""
        totals = conn.execute(f"""
            SELECT
                COUNT(*)                        AS total_calls,
                COALESCE(SUM(total_tokens), 0)  AS total_tokens,
                COALESCE(SUM(cost_usd), 0.0)    AS total_cost_usd,
                COUNT(DISTINCT session_id)       AS sessions_count
            FROM call_records {where}
        """).fetchone()

        by_model = conn.execute(f"""
            SELECT model,
                   SUM(total_tokens) AS tokens,
                   ROUND(SUM(cost_usd), 6) AS cost
            FROM call_records {where}
            GROUP BY model ORDER BY cost DESC
        """).fetchall()

        by_day = conn.execute(f"""
            SELECT DATE(timestamp) AS date,
                   SUM(total_tokens) AS tokens,
                   ROUND(SUM(cost_usd), 6) AS cost
            FROM call_records {where}
            {"AND" if where else "WHERE"} timestamp >= DATE('now', '-7 days')
            GROUP BY DATE(timestamp) ORDER BY date ASC
        """).fetchall()

        return {
            "total_calls": totals["total_calls"],
            "total_tokens": totals["total_tokens"],
            "total_cost_usd": round(totals["total_cost_usd"], 6),
            "sessions_count": totals["sessions_count"],
            "by_model": [dict(r) for r in by_model],
            "by_day": [dict(r) for r in by_day],
        }


# ------------------------------------------------------------------
# Demo seed
# ------------------------------------------------------------------

def clear_user_data(user_id: int, path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.execute("DELETE FROM call_records WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM budgets WHERE user_id = ?", (user_id,))


def seed_demo_data(user_id: int, path: Path = DB_PATH) -> int:
    models = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"]
    agents = ["chatbot", "summarizer", "code-reviewer", "data-analyst"]
    sessions = [("pipeline-alpha", 0.50), ("pipeline-beta", 0.20), ("dev-sandbox", None)]
    previews = [
        "Summarise this document for me",
        "Review this pull request",
        "Analyse the quarterly sales data",
        "Help me debug this function",
        "Write unit tests for this module",
        "Explain this concept simply",
    ]

    now = datetime.utcnow()
    records = []
    for i in range(60):
        model = random.choice(models)
        session_id, _ = random.choice(sessions)
        agent = random.choice(agents)
        ts = now - timedelta(days=random.randint(0, 6), hours=random.randint(0, 23))
        input_tok = random.randint(200, 6000)
        output_tok = random.randint(100, 2000)
        records.append((
            user_id, session_id, agent, model,
            input_tok, output_tok, input_tok + output_tok,
            calculate_cost(model, input_tok, output_tok),
            ts.isoformat(), random.choice(previews),
        ))

    with get_conn(path) as conn:
        conn.executemany("""
            INSERT INTO call_records
                (user_id, session_id, agent_name, model, input_tokens, output_tokens,
                 total_tokens, cost_usd, timestamp, prompt_preview)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        for sid, budget in sessions:
            if budget:
                conn.execute("""
                    INSERT INTO budgets (session_id, user_id, max_tokens, max_cost_usd, alert_threshold)
                    VALUES (?, ?, NULL, ?, 0.8)
                    ON CONFLICT(session_id, user_id) DO NOTHING
                """, (sid, user_id, budget))

    return len(records)
