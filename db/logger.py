# db/logger.py
"""SQLite-based chat logger for tracking conversations and token usage."""

import atexit
import json as _json
import sqlite3
import threading
import traceback as _traceback
import uuid
import os
from datetime import datetime

from config.settings import (
    LLM_INPUT_PRICE_PER_M,
    LLM_OUTPUT_PRICE_PER_M,
    LOG_DB_PATH,
    LOG_LLM_CALLS,
)

# LOG_DB_PATH (env var) overrides the in-repo default. Empty string falls back
# to the original location so unchanged config preserves the current behavior.
DB_PATH = LOG_DB_PATH or os.path.join(os.path.dirname(__file__), "chat_logs.db")

# Defensive caps on stored error fields — full stack traces / raw model output
# get truncated so a single bad call can't bloat the DB.
_ERROR_MSG_MAX = 500
_TRACEBACK_MAX = 4000


# ── Shared connection ────────────────────────────────────────────────────────
# One sqlite3.Connection per process (the recommended pattern for an embedded
# SQLite logger). Re-using it avoids the open/close churn every call_llm()
# triggered before, and the explicit write lock prevents "database is locked"
# errors when concurrent Streamlit threads write at the same time.
#
# Readers don't acquire the write lock — WAL mode lets them read a consistent
# snapshot concurrently with a writer.
_conn: sqlite3.Connection | None = None
_conn_init_lock = threading.Lock()  # guards lazy first-time creation only
_write_lock = threading.Lock()      # serialises INSERT / UPDATE / commit


def _get_conn() -> sqlite3.Connection:
    """Return the process-wide shared SQLite connection, creating it lazily.

    Thread-safe via double-checked locking; `check_same_thread=False` lets us
    share one connection across the Streamlit script-runner threads.
    """
    global _conn
    if _conn is not None:
        return _conn
    with _conn_init_lock:
        if _conn is None:  # re-check after acquiring the lock
            c = sqlite3.connect(DB_PATH, check_same_thread=False)
            c.row_factory = sqlite3.Row
            # WAL = concurrent reads with one writer. NORMAL = durable on crash,
            # ~3× faster than FULL on writes; suitable for analytics logs (the
            # only window of loss is OS buffers in the last few seconds before
            # a power cut, which is acceptable for chat-cost telemetry).
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            _conn = c
    return _conn


def _close_conn() -> None:
    """Close the shared connection at process exit. Registered via atexit."""
    global _conn
    with _conn_init_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


atexit.register(_close_conn)


def init_db():
    """Create tables if they don't exist. Safe to call multiple times."""
    conn = _get_conn()
    with _write_lock:
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

        -- Per-LLM-call ledger for cost tracking, model attribution, and error
        -- triage. One row per `call_llm()` invocation. Populated by
        -- `log_llm_call()` (added in a later item); created here so the schema
        -- migration is decoupled from the write path.
        CREATE TABLE IF NOT EXISTS llm_calls (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT NOT NULL,
            turn_number         INTEGER,
            timestamp           TEXT NOT NULL,        -- ISO8601 UTC
            node_name           TEXT,                 -- 'conversation' | 'routing' | 'intent' | 'triage' | 'time_parse'
            model               TEXT NOT NULL,
            input_tokens        INTEGER,
            output_tokens       INTEGER,
            latency_ms          INTEGER,
            estimated_cost_usd  REAL,
            status              TEXT NOT NULL,        -- 'ok' | 'error' | 'json_retry' | 'fallback'
            error_type          TEXT,
            error_message       TEXT,                 -- truncated, max 500 chars
            prompt_hash         TEXT,                 -- sha1 of (system_prompt + last user msg), 12 chars
            cache_hit           INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_llm_calls_session
            ON llm_calls (session_id, turn_number);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp
            ON llm_calls (timestamp);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_node
            ON llm_calls (node_name);

        -- Non-LLM error ledger: DB / CRM / app catch-all / graph / etc.
        -- LLM errors stay in llm_calls (they carry token + model context);
        -- everything else lands here. Populated by `log_error()` from sites
        -- wired up in a later item.
        CREATE TABLE IF NOT EXISTS errors (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT,                   -- nullable: not every error has session context
            turn_number     INTEGER,
            timestamp       TEXT NOT NULL,          -- ISO8601 UTC
            source          TEXT NOT NULL,          -- 'db' | 'crm' | 'app' | 'graph' | ...
            error_type      TEXT NOT NULL,          -- exception class name
            error_message   TEXT,                   -- truncated, max 500 chars
            traceback       TEXT,                   -- truncated, max 4000 chars
            context         TEXT                    -- optional JSON blob (string fallback)
        );

        CREATE INDEX IF NOT EXISTS idx_errors_timestamp
            ON errors (timestamp);
        CREATE INDEX IF NOT EXISTS idx_errors_source
            ON errors (source);
        CREATE INDEX IF NOT EXISTS idx_errors_session
            ON errors (session_id, turn_number);

        -- ── Daily-rollup views ─────────────────────────────────────────────
        -- SQLite's date() parses ISO8601 'YYYY-MM-DDThh:mm:ss' directly.
        -- Read-only by design; the dashboard (pages/logs.py) queries these
        -- instead of re-aggregating per call.

        CREATE VIEW IF NOT EXISTS v_daily_llm_cost AS
        SELECT
            date(timestamp)                       AS day,
            COUNT(*)                              AS calls,
            COALESCE(SUM(input_tokens), 0)        AS input_tokens,
            COALESCE(SUM(output_tokens), 0)       AS output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0)  AS total_cost_usd,
            CAST(COALESCE(AVG(latency_ms), 0) AS INTEGER) AS avg_latency_ms,
            SUM(CASE WHEN status='ok'         THEN 1 ELSE 0 END) AS ok_count,
            SUM(CASE WHEN status='error'      THEN 1 ELSE 0 END) AS error_count,
            SUM(CASE WHEN status='fallback'   THEN 1 ELSE 0 END) AS fallback_count,
            SUM(CASE WHEN status='json_retry' THEN 1 ELSE 0 END) AS json_retry_count
        FROM llm_calls
        GROUP BY date(timestamp);

        CREATE VIEW IF NOT EXISTS v_daily_llm_cost_by_node AS
        SELECT
            date(timestamp)                       AS day,
            node_name,
            COUNT(*)                              AS calls,
            COALESCE(SUM(input_tokens), 0)        AS input_tokens,
            COALESCE(SUM(output_tokens), 0)       AS output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0)  AS total_cost_usd,
            CAST(COALESCE(AVG(latency_ms), 0) AS INTEGER) AS avg_latency_ms,
            SUM(CASE WHEN status='ok'         THEN 1 ELSE 0 END) AS ok_count,
            SUM(CASE WHEN status='error'      THEN 1 ELSE 0 END) AS error_count,
            SUM(CASE WHEN status='fallback'   THEN 1 ELSE 0 END) AS fallback_count,
            SUM(CASE WHEN status='json_retry' THEN 1 ELSE 0 END) AS json_retry_count
        FROM llm_calls
        GROUP BY date(timestamp), node_name;

        CREATE VIEW IF NOT EXISTS v_daily_llm_cost_by_model AS
        SELECT
            date(timestamp)                       AS day,
            model,
            COUNT(*)                              AS calls,
            COALESCE(SUM(input_tokens), 0)        AS input_tokens,
            COALESCE(SUM(output_tokens), 0)       AS output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0)  AS total_cost_usd,
            CAST(COALESCE(AVG(latency_ms), 0) AS INTEGER) AS avg_latency_ms,
            SUM(CASE WHEN status='ok'         THEN 1 ELSE 0 END) AS ok_count,
            SUM(CASE WHEN status='error'      THEN 1 ELSE 0 END) AS error_count,
            SUM(CASE WHEN status='fallback'   THEN 1 ELSE 0 END) AS fallback_count,
            SUM(CASE WHEN status='json_retry' THEN 1 ELSE 0 END) AS json_retry_count
        FROM llm_calls
        GROUP BY date(timestamp), model;

        CREATE VIEW IF NOT EXISTS v_daily_errors AS
        SELECT
            date(timestamp)  AS day,
            source,
            COUNT(*)         AS error_count
        FROM errors
        GROUP BY date(timestamp), source;
    """)


def create_session() -> str:
    """Create a new session and return its ID."""
    session_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    conn = _get_conn()
    with _write_lock:
        conn.execute(
            "INSERT INTO sessions (session_id, started_at, last_active) VALUES (?, ?, ?)",
            (session_id, now, now),
        )
        conn.commit()
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
    with _write_lock:
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


# ── Per-LLM-call ledger ───────────────────────────────────────────────────────

def log_llm_call(
    session_id,
    turn_number,
    node_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    status: str,
    error_type=None,
    error_message=None,
    prompt_hash=None,
    cache_hit: bool = False,
) -> None:
    """Insert one row into the llm_calls table. Best-effort: never raises.

    Called from `llm.client.call_llm()` at every terminal outcome (success,
    JSON-retry, fallback, error). Cost is computed from the token counts and
    the per-1M-token prices in config/settings.

    `cache_hit` stays False for direct LLM responses; a future response-cache
    layer (intent / time_parse) will flip it to True.

    Honors the `LOG_LLM_CALLS` env-driven kill switch — when False, returns
    immediately without writing.
    """
    if not LOG_LLM_CALLS:
        return
    try:
        in_toks = int(input_tokens or 0)
        out_toks = int(output_tokens or 0)
        cost = (
            in_toks * LLM_INPUT_PRICE_PER_M / 1_000_000
            + out_toks * LLM_OUTPUT_PRICE_PER_M / 1_000_000
        )
        msg = error_message
        if msg is not None and not isinstance(msg, str):
            msg = str(msg)
        if msg and len(msg) > _ERROR_MSG_MAX:
            msg = msg[:_ERROR_MSG_MAX]
        now = datetime.utcnow().isoformat(timespec="seconds")

        conn = _get_conn()
        with _write_lock:
            conn.execute(
                """
                INSERT INTO llm_calls (
                    session_id, turn_number, timestamp, node_name, model,
                    input_tokens, output_tokens, latency_ms, estimated_cost_usd,
                    status, error_type, error_message, prompt_hash, cache_hit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id or "",
                    turn_number,
                    now,
                    node_name,
                    model or "",
                    in_toks,
                    out_toks,
                    int(latency_ms or 0),
                    float(cost),
                    status,
                    error_type,
                    msg,
                    prompt_hash,
                    1 if cache_hit else 0,
                ),
            )
            conn.commit()
    except Exception as e:
        # Logging must never break the booking flow. Surface to stdout only.
        print(f"[log_llm_call] failed: {e}")


def log_error(
    source: str,
    exc: BaseException,
    *,
    session_id=None,
    turn_number=None,
    context: dict | None = None,
) -> None:
    """Insert one row into the `errors` table. Best-effort: never raises.

    `source` is a short tag identifying where the error originated
    ('db', 'crm', 'app', 'graph', ...). The caller passes the exception
    object — this helper extracts type, message, and the current traceback
    string. `context` is an optional dict of extra fields serialised to JSON;
    if serialisation fails it falls back to `str(context)`.

    Call from an `except` block so `traceback.format_exc()` captures the
    active exception.
    """
    try:
        err_type = type(exc).__name__ if exc is not None else "UnknownError"
        msg = str(exc) if exc is not None else ""
        if msg and len(msg) > _ERROR_MSG_MAX:
            msg = msg[:_ERROR_MSG_MAX]

        tb = _traceback.format_exc()
        if tb == "NoneType: None\n":
            # Called outside an except block — no live traceback.
            tb = None
        elif tb and len(tb) > _TRACEBACK_MAX:
            tb = tb[:_TRACEBACK_MAX]

        ctx_str = None
        if context is not None:
            try:
                ctx_str = _json.dumps(context, default=str, ensure_ascii=False)
            except Exception:
                # Fallback for anything json can't handle even with default=str.
                ctx_str = str(context)
            if ctx_str and len(ctx_str) > _TRACEBACK_MAX:
                ctx_str = ctx_str[:_TRACEBACK_MAX]

        now = datetime.utcnow().isoformat(timespec="seconds")

        conn = _get_conn()
        with _write_lock:
            conn.execute(
                """
                INSERT INTO errors (
                    session_id, turn_number, timestamp, source,
                    error_type, error_message, traceback, context
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_number,
                    now,
                    source or "unknown",
                    err_type,
                    msg,
                    tb,
                    ctx_str,
                ),
            )
            conn.commit()
    except Exception as e:
        # Logging must never break the booking flow. Surface to stdout only.
        print(f"[log_error] failed: {e}")


# ── Query functions (used by dashboard) ──────────────────────────────────────

def get_all_sessions() -> list[dict]:
    """Return all sessions ordered by most recent."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY last_active DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_session_logs(session_id: str) -> list[dict]:
    """Return all messages for a session in order."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM chat_logs WHERE session_id = ? ORDER BY turn_number, id",
        (session_id,),
    ).fetchall()
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
    return dict(row)


# ── Read helpers for the dashboard's observability tabs ──────────────────────

def get_daily_llm_cost(days: int = 14) -> list[dict]:
    """Last N days from v_daily_llm_cost (one row per day)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM v_daily_llm_cost WHERE day >= date('now', ?) ORDER BY day",
        (f"-{int(days)} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_daily_llm_cost_by_node(days: int = 14) -> list[dict]:
    """Last N days from v_daily_llm_cost_by_node (one row per day, node)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM v_daily_llm_cost_by_node WHERE day >= date('now', ?) ORDER BY day, node_name",
        (f"-{int(days)} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_daily_llm_cost_by_model(days: int = 14) -> list[dict]:
    """Last N days from v_daily_llm_cost_by_model (one row per day, model)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM v_daily_llm_cost_by_model WHERE day >= date('now', ?) ORDER BY day, model",
        (f"-{int(days)} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_daily_errors(days: int = 14) -> list[dict]:
    """Last N days from v_daily_errors (one row per day, source)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM v_daily_errors WHERE day >= date('now', ?) ORDER BY day, source",
        (f"-{int(days)} days",),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_failed_llm_calls(limit: int = 50) -> list[dict]:
    """Most recent LLM calls with status != 'ok' for triage."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM llm_calls WHERE status != 'ok' ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_errors(limit: int = 50) -> list[dict]:
    """Most recent rows from the errors table."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM errors ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


# Initialize DB on import
init_db()
