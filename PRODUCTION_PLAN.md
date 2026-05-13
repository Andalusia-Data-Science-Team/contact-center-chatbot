# Production-Hardening Plan — Andalusia Booking Bot

> **Scope guarantee:** zero changes to agent behavior, response wording, decision logic, or pipeline flow. All changes are observability, reliability, performance, and config hygiene. Backward compatible.

---

## A. Findings (snapshot of current state)

### Pipeline & runtime
- **Streamlit + LangGraph** with an 8-node sequential pipeline (`graph.py`). Compiled once at import.
- No checkpointer; state lives only in `st.session_state.bot_state` — fine for Streamlit, will need attention for the planned WhatsApp/FastAPI deployment.
- **State schema (`state.py`)** is a `TypedDict` with 30+ fields, including 6 dev-only `_session_total_*` counters and several transient flags (`_proposed_slot`, `_proposal_shown_this_turn`, `_skip_time_preference_this_turn`, `_alternatives_offered`, `_single_doctor_choice_pending`, `_pending_price_followup`, `_walk_in_price_doctor`, `_partial_doctor_name`, `_price_handled`).
- The pipeline is **fully synchronous**. `compiled_graph.invoke(state)` blocks Streamlit's render thread (1.5–6 s typical).

### LLM layer (`llm/client.py`)
- One `call_llm()` for **conversation, routing, triage, intent, time_parse**. All hit Llama 3.3 70B regardless of complexity.
- ~2 LLM calls per turn typically (intent + conversation), occasionally 3.
- The ~1,800-token system prompt is re-sent every turn with no provider-side caching.
- Retries in place for network errors (3×) and malformed JSON (3×). No jitter, no circuit breaker, no global timeout budget, no structured error type.
- Module-level `_turn_metrics` dict is **not thread-safe** — concurrent Streamlit sessions will scramble each other's per-turn metrics.
- Errors go to `print()` only. No structured logger.

### Graph & state
- All booking nodes run sequentially every turn; each does its own stage check (cheap).
- No explicit recursion limit set on `compile()`.
- `messages[-14:]` is sliced for the conversation LLM. Full `messages` history retained on state.
- Many inner-function imports (`from nodes.X import _Y` inside functions) — currently to dodge circular imports.

### Database layer
- **Hospital DB**: connection pool of 10 via `Queue`. 30s short-TTL doctor cache.
- **CRM DB**: 24h in-memory cache + token cache on disk; heavy `_QUERY_VARIANTS` retrieves all doctor prices at once.
- `get_connection()` is exposed as "legacy" but no callers verified.
- Pool doesn't bound max-age or validate connections — stale pyodbc connections only get caught after a failed query.

### Logging (`db/logger.py`)
- SQLite logger with `sessions` and `chat_logs` tables. Critical gaps for production:
  - No `node_name` or per-LLM-call granularity (token usage aggregated per bot reply).
  - No `model` field — can't tell which model was used when we add routing.
  - No `error` / `error_type` column.
  - One connection opened per turn (no pool).
  - `init_db()` runs at import.

### Resource management / memory
- `atexit.register(_persist)` in `crm_database._load_msal_cache` is called on every token acquire — leaks atexit registrations on hot paths.
- `_doctor_cache` in `services/doctor_selector.py` uses thread-safe lock + cap (200) ✓.
- `messages` list in `BookingState` grows unbounded over a session.

### Error handling
- `app.py`'s outer `try` catches all and prints `str(e)` into the chat — leaks internals.
- LLM JSON failures fall back to `_safe_default` per label ✓.
- DB query failures: hospital DB has no caller-level fallback (transient errors surface as `"An error occurred: ..."` to the patient). CRM fails to `[]` ✓.

### Cost tracking & metrics
- Per-turn metrics captured (calls, in/out tokens, latency, per-call detail). Aggregated into session totals.
- Persistence is **session-level**, not per-LLM-call.
- Dashboard at `pages/logs.py` shows session totals only.
- No daily/hourly rollups, no per-model breakdown, no per-node breakdown.

---

## B. Proposed changes (grouped)

### 1. LLM call optimization
- **1.1** Per-call model routing via env vars (`OPENROUTER_MODEL_INTENT`, `OPENROUTER_MODEL_TIME_PARSE`, `OPENROUTER_MODEL_TRIAGE`, `OPENROUTER_MODEL_ROUTING`, `OPENROUTER_MODEL_CONVERSATION`), all defaulting to current `OPENROUTER_MODEL`. Default behavior unchanged until ops sets cheaper models.
- **1.2** OpenRouter prompt caching headers via env flag (off by default).
- **1.3** Local response cache for `intent` + `time_parse` only — TTL+LRU kVeyed on `(label, model, normalized_text, booking_stage)`. Size 1k entries, 5-min TTL.
- **1.4** Token-aware history slicing — hard ceiling (e.g. 6k chars) on top of `[-14:]`.
- **1.5** Make `_turn_metrics` thread-local via `contextvars.ContextVar`.

### 2. Async handling and concurrency
- **2.1** Keep the graph synchronous.
- **2.2** Explicit timeout budgets: `LLM_TIMEOUT_SECONDS=30`, `DB_QUERY_TIMEOUT_SECONDS=15` (configurable). No enforcement that rejects turns — observability only.
- **2.3** `DB_POOL_MAX_SIZE` env var + `DB_POOL_MAX_AGE_SECONDS=300` for stale-connection recycling.
- **2.4** Module-level `requests.Session` with retry-adapter in `llm/client.py` for HTTP keep-alive.
- **2.5** Lock `_turn_metrics` writes (subsumed by 1.5).

### 3. LangGraph state management & graph efficiency
- **3.1** Set explicit `recursion_limit=25` on compile.
- **3.2** Centralized `clear_transient_fields(state)` helper called inside `_safety_net` — consolidates the existing per-turn clearing patterns.
- **3.3** Optional checkpointer wired through env flag `LANGGRAPH_CHECKPOINTER=memory|none`, default `none`. **Deferred — see Section E.**
- **3.4** Lift inner-function imports to module top where no real circular dependency exists.

### 4. Error handling and tool-call reliability
- **4.1** Structured `BotError(code, message, retryable)` in new `errors.py`.
- **4.2** `app.py` catch-all replaced with bilingual generic fallback (no exception leakage), error logged via new logger.
- **4.3** Retry jitter in `call_llm` (1±0.3s, 2±0.5s).
- **4.4** DB query retry: 2-attempt retry with one connection refresh in `query_availability` / `query_doctor_slots`, mirroring CRM.
- **4.5** `atexit` leak fix in `crm_database._load_msal_cache` — register once.

### 5. Cost tracking & logging — self-hosted SQLite (extend, don't replace)
- **5.1** Add `llm_calls` table:
  ```sql
  CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER,
    timestamp TEXT NOT NULL,        -- ISO8601 UTC
    node_name TEXT,                 -- 'conversation' | 'routing' | 'intent' | 'triage' | 'time_parse'
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    estimated_cost_usd REAL,
    status TEXT NOT NULL,           -- 'ok' | 'error' | 'json_retry' | 'fallback'
    error_type TEXT,
    error_message TEXT,             -- truncated, max 500 chars
    prompt_hash TEXT,               -- sha1 of (system_prompt + last user msg), 12 chars
    cache_hit INTEGER DEFAULT 0
  );
  CREATE INDEX idx_llm_calls_session ON llm_calls(session_id, turn_number);
  CREATE INDEX idx_llm_calls_timestamp ON llm_calls(timestamp);
  CREATE INDEX idx_llm_calls_node ON llm_calls(node_name);
  ```
- **5.2** `log_llm_call()` helper in `db/logger.py`, called from `call_llm()`. Best-effort (try/except, never raises).
- **5.3** Add `errors` table for non-LLM errors (DB, CRM, app catch-all).
- **5.4** Daily-rollup SQL view for cheap cost-by-day / cost-by-node queries.
- **5.5** Single shared SQLite connection with WAL + write lock, instead of per-call open/close.
- **5.6** Extend `pages/logs.py` with one new tab: per-model/per-node breakdowns, errors-by-day, 7-day cost trend. Additive only.
- **5.7** Configurable: `LOG_LLM_CALLS=1`, `LOG_DB_PATH` env vars.

### 6. Resource cleanup & memory management
- **6.1** Bound `messages` list in `BookingState` to last 50 entries (conversation LLM already sees only `[-14:]`; SQLite write per turn captures the full history independently).
- **6.2** `atexit` to close pooled SQL Server / SQLite connections cleanly.
- **6.3** MSAL `atexit` dedupe (= 4.5).
- **6.4** Module-level `requests.Session` close on exit.

### 7. Configuration & deployability
- **7.1** Extend `config/settings.py` with the new env vars; sensible defaults that preserve current behavior.
- **7.2** Update `.env.example` documenting every new var.
- **7.3** Pin `requirements.txt` to currently installed versions (no installs/upgrades — just freeze what's there).

---

## C. What will NOT be touched

- All node logic — `routing_node`, `doctor_selection_node`, `slot_selection_node`, `conversation_node`, `_safety_net`. No flow, no decision, no message changes.
- All user-facing strings in `services/formatter.py`, `nodes/response.py`, `graph.py` (apology/escalation/repeat-detection text).
- All prompts in `prompts/*.py`.
- `BookingState` field names and types — additions only, no removal/rename.
- Graph topology — same nodes, same edges, same entry point.
- Specialty constants, symptom map, insurance company list, fuzzy-match thresholds, pediatric thresholds.
- Streamlit UI styling, dev/stakeholder toggle, dashboard layout.
- The existing `chat_logs.db` schema and `pages/logs.py` (extension only).
- Dotcare app download text, confirmation card, emergency message.
- The existing JSON retry cascade, slot safety net, insurance safety net, dialect sanitizer.

---

## D. Staged rollout

### Stage 1 — Observability foundation (no risk to behavior)
1. 1.5 — thread-local turn metrics
2. 5.1–5.5 — LLM-call logging, errors table, connection reuse
3. 5.6 — dashboard extension
4. 4.5 — atexit dedupe
5. 7.1–7.2 — config + `.env.example`

### Stage 2 — Reliability hardening (low risk)
1. 4.1 — structured errors
2. 4.2 — safer `app.py` catch-all
3. 4.3 — retry jitter
4. 4.4 — DB retry
5. 2.3 — connection age limit
6. 2.4 — `requests.Session` reuse
7. 6.2–6.4 — cleanup hooks

### Stage 3 — Graph & state hygiene (low risk)
1. 3.1 — recursion limit
2. 3.2 — centralized transient clearing
3. 6.1 — bounded messages
4. 3.4 — import lifting

### Stage 4 — Cost optimization (opt-in)
1. 1.1 — per-call model routing (defaults preserve behavior)
2. 1.2 — provider prompt-caching header (env-gated)
3. 1.3 — intent/time_parse response cache
4. 1.4 — prompt size guard

### Stage 5 — Future-readiness (opt-in)
1. 3.3 — checkpointer behind env flag
2. 2.2 — timeout/budget knobs
3. 7.3 — requirements.txt pinning

---

## E. Default decisions (override anytime)

| Question | Default chosen | Override by telling me… |
|---|---|---|
| Logging storage | Extend SQLite (`chat_logs.db`) | "Move logging to Postgres" |
| Per-call model routing | Wire env vars with safe defaults | "Skip model routing" |
| Pin `requirements.txt` | Pin to currently installed versions in Stage 5 | "Leave requirements.txt unpinned" |
| Checkpointer | Defer until WhatsApp work begins | "Add checkpointer now behind env flag" |

---

## F. Progress tracking

See `PRODUCTION_PROGRESS.md` — updated after each item completes.
