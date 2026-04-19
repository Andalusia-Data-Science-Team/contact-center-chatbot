# db/logger.py
"""SQLite-based chat logger for tracking conversations and token usage."""

import sqlite3
import uuid
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "chat_logs.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent readers
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            started_at   TEXT NOT NULL,
            last_active  TEXT NOT NULL,
            total_turns  INTEGER DEFAULT 0,
            total_input_tokens  INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_llm_calls     INTEGER DEFAULT 0,
            total_latency_ms    INTEGER DEFAULT 0,
            patient_name TEXT,
            language     TEXT,
            booking_stage TEXT
        );

        CREATE TABLE IF NOT EXISTS chat_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            turn_number  INTEGER NOT NULL,
            role         TEXT NOT NULL,
            content      TEXT NOT NULL,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            llm_calls     INTEGER DEFAULT 0,
            latency_ms    INTEGER DEFAULT 0,
            booking_stage TEXT,
            timestamp    TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_chat_logs_session
            ON chat_logs (session_id, turn_number);
    """)
    conn.close()


def create_session() -> str:
    """Create a new session and return its ID."""
    session_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO sessions (session_id, started_at, last_active) VALUES (?, ?, ?)",
        (session_id, now, now),
    )
    conn.commit()
    conn.close()
    return session_id


def log_turn(
    session_id: str,
    turn_number: int,
    user_message: str,
    bot_reply: str,
    turn_metrics: dict,
    state: dict,
):
    """Log a complete turn (user message + bot reply) with metrics."""
    now = datetime.now().isoformat()
    input_tokens = turn_metrics.get("total_input_tokens", 0)
    output_tokens = turn_metrics.get("total_output_tokens", 0)
    llm_calls = turn_metrics.get("llm_calls", 0)
    latency_ms = turn_metrics.get("total_latency_ms", 0)
    booking_stage = state.get("booking_stage", "")

    conn = _get_conn()
    cur = conn.cursor()

    # Insert user message
    cur.execute(
        """INSERT INTO chat_logs
           (session_id, turn_number, role, content, input_tokens, output_tokens,
            llm_calls, latency_ms, booking_stage, timestamp)
           VALUES (?, ?, 'user', ?, 0, 0, 0, 0, ?, ?)""",
        (session_id, turn_number, user_message, booking_stage, now),
    )

    # Insert bot reply
    cur.execute(
        """INSERT INTO chat_logs
           (session_id, turn_number, role, content, input_tokens, output_tokens,
            llm_calls, latency_ms, booking_stage, timestamp)
           VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, turn_number, bot_reply, input_tokens, output_tokens,
         llm_calls, latency_ms, booking_stage, now),
    )

    # Update session summary
    cur.execute(
        """UPDATE sessions SET
            last_active = ?,
            total_turns = ?,
            total_input_tokens = total_input_tokens + ?,
            total_output_tokens = total_output_tokens + ?,
            total_llm_calls = total_llm_calls + ?,
            total_latency_ms = total_latency_ms + ?,
            patient_name = COALESCE(?, patient_name),
            language = COALESCE(?, language),
            booking_stage = ?
           WHERE session_id = ?""",
        (now, turn_number, input_tokens, output_tokens, llm_calls, latency_ms,
         state.get("patient_name"), state.get("language"), booking_stage,
         session_id),
    )

    conn.commit()
    conn.close()


# ── Query functions (used by dashboard) ──────────────────────────────────────

def get_all_sessions() -> list[dict]:
    """Return all sessions ordered by most recent."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY last_active DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session_logs(session_id: str) -> list[dict]:
    """Return all messages for a session in order."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM chat_logs WHERE session_id = ? ORDER BY turn_number, id",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary_stats() -> dict:
    """Return aggregate stats across all sessions."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*)                  AS total_sessions,
            COALESCE(SUM(total_turns), 0)           AS total_turns,
            COALESCE(SUM(total_input_tokens), 0)    AS total_input_tokens,
            COALESCE(SUM(total_output_tokens), 0)   AS total_output_tokens,
            COALESCE(SUM(total_llm_calls), 0)       AS total_llm_calls,
            COALESCE(AVG(total_turns), 0)            AS avg_turns_per_session
        FROM sessions
        WHERE total_turns > 0
    """).fetchone()
    conn.close()
    return dict(row)


# Initialize DB on import
init_db()
