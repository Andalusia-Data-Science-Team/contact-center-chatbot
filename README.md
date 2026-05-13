# Andalusia Health — AI Booking Agent (Nour / نور)

A bilingual (Arabic/English) conversational booking agent for **Andalusia Health Group** (KSA + Egypt). Handles the full doctor-appointment booking flow end-to-end — name capture, specialty routing, doctor selection, slot booking, payment method, confirmation card.

The agent introduces herself as **Nour (نور)** and operates in **Saudi (Khaleeji) Arabic** or English, auto-detected from the first patient message.

---

## Quick Start

```bash
# 1. Copy the env template and fill in credentials
cp .env.example .env
#    edit .env: OPENROUTER_API_KEY, DB_SERVER/USERNAME/PASSWORD, CRM_SERVER/USERNAME, ...

# 2. Install pinned dependencies
pip install -r requirements.txt

# 3. Run
streamlit run app.py
#    open http://localhost:8502
```

Python 3.10+ required. ODBC Driver 17 for SQL Server must be installed on the host.

---

## Architecture

```
Streamlit UI  (app.py)
       │
       ▼
LangGraph pipeline  (graph.py)
       ├── language          → AR/EN detection
       ├── emergency         → keyword check (red-flag symptoms)
       ├── intent            → classify (booking | inquiry | greeting | …)
       │
       └── if booking:
              ├── conversation       → LLM reply + state updates
              ├── routing            → complaint → specialty (keyword prefilter + LLM)
              ├── doctor_selection   → fuzzy doctor match + fetch slots
              ├── slot_selection     → time parse + nearest slot
              ├── patient_info       → completeness check + confirmation card
              └── safety_net         → trim messages, clear transients, repetition guard

External systems:
  • OpenRouter            — LLM (Llama 3.3 70B default; per-label routing configurable)
  • SQL Server (pyodbc)   — live hospital doctor/slot database
  • Dynamics 365 CRM      — doctor reference data + walk-in price (MSAL TDS)
  • SQLite chat_logs.db   — internal observability (sessions, chat, llm_calls, errors)
```

The pipeline is sequential — each node is a "filter" that checks the current `booking_stage` and acts only when relevant. State persists across turns in a `BookingState` TypedDict (`state.py`).

### Which nodes call the LLM

Only some nodes hit the LLM. The rest are deterministic Python.

| Node / call site | LLM call? | `label` (for model routing) | When it fires |
|---|---|---|---|
| `language` | no | — | Every turn |
| `emergency` | no | — | Every turn |
| `intent` | yes | `intent` | Every turn (often cached / hard-locked mid-booking) |
| `conversation` | yes | `conversation` | Every turn during booking (the "brain") |
| `routing` (via `services/router.py`) | yes | `routing` | When routing a complaint to a specialty |
| `triage` (called from `nodes/routing.py`) | yes | `triage` | When a symptom needs a clarifying question |
| `time_parse` (via `services/slot_handler.py`) | yes | `time_parse` | When a patient's free-text time can't be parsed deterministically |
| `doctor_selection` | no | — | Every turn (fuzzy match + DB query) |
| `slot_selection` | no (uses `time_parse` LLM internally) | — | Every turn |
| `patient_info` | no | — | When collecting phone + insurance |
| `safety_net` | no | — | Every turn |

**5 distinct LLM call labels total**: `conversation`, `routing`, `intent`, `triage`, `time_parse`. Each can be routed to a different model via `.env` — see **Per-call model routing** in Configuration below.

### One agent, multiple model calls

Nour is **one agent** from the patient's perspective — one persona, one voice, one conversation. Under the hood, "Nour" is the combination of:
- The **persona prompts** in `prompts/*.py` (model-agnostic — every model reads the same Nour briefing)
- The **deterministic Python pipeline** in `graph.py` that orchestrates everything
- The **5 specialized LLM calls** that each handle one micro-task (classify intent / generate reply / pick specialty / etc.)

Each LLM call is stateless and independent. Swapping a model only changes which "specialist" runs that one micro-task — the agent itself (the prompt + pipeline + state) is unchanged.

---

## Booking Stages

| Stage | What's happening |
|---|---|
| `none` | First turn — bot greets and asks for the patient's name |
| `greeting_done` | Name collected — bot asks how to help |
| `complaint` | Collecting symptoms / specialty / doctor name + patient age |
| `routing` | Code-only — keyword prefilter narrows 75 specialties to 3-6, LLM picks one, patient confirms |
| `doctor_list` | Available doctors shown, patient picks one (by number, name, or specialty fallback) |
| `slot_selection` | Time slots presented, patient selects (handles "8 pm" / "after 7" / "tonight" / "بكرا") |
| `patient_info` | Collecting phone + insurance status (cash / insurance + company name) |
| `complete` | Booking confirmed with the full details card + Dotcare app download follow-up |
| `cancelled` | Patient cancelled mid-flow |
| `callback_pending` | Doctor / specialty unavailable in window — contact-center team will reach out |

---

## Example Conversations

### Arabic (Saudi/Khaleeji)

```
Bot:  السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟
User: حسن محمد
Bot:  أهلا وسهلا بحضرتك أ/ حسن 😊 كيف أقدر أساعدك؟
User: ابغى حجز مع دكتور جلدية
Bot:  [routes to Dermatology, asks confirmation, shows doctor list]
User: 2
Bot:  أقرب موعد مع د. نسمة بكره الساعة ٨:٣٠ مساءً. مناسب لحضرتك؟
User: مناسب
Bot:  تمام، لتأكيد الحجز مع د. نسمة بكره الساعة ٨:٣٠ مساءً،
      نرجو تزويدنا برقم الجوال، وهل ستكون الزيارة كاش أم تأمين؟
User: 0501234567 كاش
Bot:  تم تأكيد الحجز أ/ حسن ✅
      👨‍⚕️ مع د. نسمة | 🏥 عيادة الجلدية | 📅 بكره | 🕐 ٨:٣٠ مساءً | 💳 كاش
      واستأذنك الحضور قبل الميعاد بـ ربع ساعة …
```

### English

```
Bot:  Hello! I'm Nour from Andalusia Health Group 😊 May I have your name please?
User: Sarah Johnson
Bot:  Welcome Sarah! How can I help you today?
User: I need to see a dermatologist
Bot:  [routes to Dermatology, asks confirmation, shows doctor list]
User: Dr. Nesma
Bot:  The earliest slot with Dr. Nesma is Tomorrow at 08:30 PM. Does that work for you?
User: Yes
Bot:  Your slot with Dr. Nesma is set for Tomorrow at 08:30 PM.
      I just need your phone number, and will it be cash or insurance?
User: 0501234567 insurance Bupa
Bot:  Your booking is confirmed, Sarah! ✅
      👨‍⚕️ Dr. Nesma | 🏥 Dermatology | 📅 Tomorrow | 🕐 08:30 PM | 💳 Bupa
      Please arrive 15 minutes early …
```

---

## Configuration

All configuration lives in `.env` (see `.env.example` for the annotated template). Every var has a safe default so unchanged config preserves current behavior.

### Required credentials

| Var | Description |
|---|---|
| `OPENROUTER_API_KEY` | LLM provider API key |
| `DB_SERVER` / `DB_DATABASE` / `DB_USERNAME` / `DB_PASSWORD` | Hospital SQL Server |
| `CRM_SERVER` / `CRM_USERNAME` | Dynamics 365 CRM (doctor prices). MSAL handles auth (interactive on first run, refresh-token cache after) |
| `CRM_PASSWORD` | Optional — only when CRM account has MFA disabled |
| `VIEW_MODE` | `stakeholder` (default, clean UI) or `dev` (shows token/cost metrics) |

### Per-call model routing — opt in to cost reductions

All default to `OPENROUTER_MODEL` (currently `meta-llama/llama-3.3-70b-instruct`). Route cheap labels to smaller models for cost savings without touching code:

| Var | Used by |
|---|---|
| `OPENROUTER_MODEL_CONVERSATION` | Main conversation LLM (~2.3k tokens/turn) |
| `OPENROUTER_MODEL_ROUTING` | Specialty routing |
| `OPENROUTER_MODEL_INTENT` | Intent classifier (short text — strong smaller-model candidate) |
| `OPENROUTER_MODEL_TRIAGE` | Symptom triage question |
| `OPENROUTER_MODEL_TIME_PARSE` | Time-of-day parser (short text — strong smaller-model candidate) |

**How model resolution flows from `.env` to the API call:**

```
.env                                                    ← what ops edits
   │   OPENROUTER_MODEL_CONVERSATION=meta-llama/llama-4-maverick
   ▼
config/settings.py                                      ← reads via os.getenv()
   │   OPENROUTER_MODEL_CONVERSATION = os.getenv(
   │       "OPENROUTER_MODEL_CONVERSATION", OPENROUTER_MODEL
   │   )
   ▼
llm/client.py:34  _LABEL_TO_MODEL                       ← label → model map
   │   {"conversation": OPENROUTER_MODEL_CONVERSATION, ...}
   ▼
llm/client.py:43  _model_for(label)                     ← lookup function
   ▼
llm/client.py:309  payload["model"] = _model_for(label) ← THE switch happens here
   ▼
OpenRouter API call
```

**Worked examples:**

```bash
# Example 1 — change everything at once (simplest mental model):
OPENROUTER_MODEL=anthropic/claude-haiku-4.5
# All 5 labels inherit this. No per-label lines needed.

# Example 2 — minimal: only one label different (e.g. one upstream is broken):
# (default stays Llama 3.3 70B)
OPENROUTER_MODEL_CONVERSATION=meta-llama/llama-4-maverick

# Example 3 — cost-optimized mix (cheap models on small calls):
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct
OPENROUTER_MODEL_INTENT=meta-llama/llama-3.1-8b-instruct
OPENROUTER_MODEL_TIME_PARSE=meta-llama/llama-3.1-8b-instruct
```

After any change: `sudo systemctl restart andalusia-bot`. Verify via:

```bash
# Confirm Python picked up the new env values
python -c "
from dotenv import load_dotenv; load_dotenv()
import importlib, config.settings as cs, llm.client as C
importlib.reload(cs); importlib.reload(C)
for label in ('conversation','routing','intent','triage','time_parse'):
    print(f'{label:13s} -> {C._model_for(label)}')
"

# Or after a few real turns, check the dashboard's 'By Node / Model' tab,
# or run:
sqlite3 db/chat_logs.db \
  "SELECT node_name, model, COUNT(*) FROM llm_calls
   WHERE timestamp >= date('now')
   GROUP BY node_name, model ORDER BY node_name;"
```

> ⚠️ **Cost-dashboard caveat:** `llm_calls.estimated_cost_usd` uses a single global price pair (`LLM_INPUT_PRICE_PER_M=0.90`, `LLM_OUTPUT_PRICE_PER_M=0.90` in `config/settings.py`). If you mix models with different price points, those numbers are approximate. Real bill is on OpenRouter's dashboard. Per-model pricing is a future improvement if it matters.

### Reliability knobs

| Var | Default | Effect |
|---|---|---|
| `LLM_TIMEOUT_SECONDS` | `30` | HTTP timeout on OpenRouter call |
| `DB_QUERY_TIMEOUT_SECONDS` | `15` | pyodbc connect timeout |
| `DB_POOL_MAX_SIZE` | `10` | Hospital DB connection pool size |
| `DB_POOL_MAX_AGE_SECONDS` | `300` | Recycle pooled conns past this age. Set `0` to disable reuse (debug) |

### Logging

| Var | Default | Effect |
|---|---|---|
| `LOG_LLM_CALLS` | `1` | Kill switch for per-LLM-call logging. Set `0` to disable entirely |
| `LOG_DB_PATH` | `db/chat_logs.db` | Override SQLite file location (e.g. for shared volume) |

### Feature flags

| Var | Default | Effect |
|---|---|---|
| `OPENROUTER_PROMPT_CACHING` | `0` | Opt-in: send Anthropic-style cache marker on system prompt (50-90% off the cached portion when provider supports it) |
| `RESPONSE_CACHE_ENABLED` | `1` | Local response cache for `intent` + `time_parse` (LRU 1000 entries / 5 min TTL). Set `0` to bisect a regression |
| `LANGGRAPH_CHECKPOINTER` | `none` | Set `memory` to enable in-memory state persistence per `thread_id`. Lever for the future WhatsApp / FastAPI deployment |

---

## Deployment (Ubuntu)

```bash
bash deploy.sh                          # one-time setup — systemd service 'andalusia-bot' on port 8502
sudo systemctl restart andalusia-bot    # after code or .env changes
sudo journalctl -u andalusia-bot -f     # live logs
```

Current deployment URL: `http://10.24.105.220:8502` (company network).

The bot keeps a process-wide HTTP keep-alive session to OpenRouter and a pooled `pyodbc` connection set to SQL Server. `atexit` hooks close both cleanly on SIGTERM / restart.

---

## Observability

### SQLite tables (`db/chat_logs.db`)

| Table | One row per |
|---|---|
| `sessions` | Chat session (`session_id`, totals, patient name, language, stage) |
| `chat_logs` | User / assistant message (linked to session_id + turn_number) |
| `llm_calls` | LLM call (label, model, in/out tokens, latency, cost, status, cache_hit, prompt_hash) |
| `errors` | Non-LLM error (DB / CRM / app catch-all / graph) with traceback + JSON context |

### Daily-rollup views (pre-aggregated for cheap querying)

- `v_daily_llm_cost` — total calls, tokens, cost, avg latency per day
- `v_daily_llm_cost_by_node` — same, grouped by node (conversation / routing / intent / triage / time_parse)
- `v_daily_llm_cost_by_model` — same, grouped by model
- `v_daily_errors` — non-LLM error counts per (day, source)

### Dashboard (`pages/logs.py`)

Live at `/logs` while the bot is running. Four tabs:
- **Sessions** — per-session breakdown with conversation drill-down
- **Daily Cost** — 14-day cost trend + token / call charts
- **By Node / Model** — side-by-side cost breakdowns
- **Errors** — daily error counts + recent failed LLM calls + recent non-LLM errors

---

## Known LLM-output failure modes

The upstream LLM (Llama 3.3 70B by default) occasionally produces malformed output. OpenRouter routes to multiple providers with different quantizations, and some produce artifacts. The bot has defensive code for each of the failure modes seen in production — they degrade gracefully instead of crashing.

| Symptom | What the LLM did | What the bot does now | First triage step |
|---|---|---|---|
| `'NoneType' object has no attribute 'strip'` in logs | Returned `content: null` | Treats as empty → 3 retries → `_safe_default` | Check `errors` table for source='app'; restart |
| Hex tokens in reply (e.g. `… أحمد 48444a …`) | Garbled emoji into byte-fallback hex tokens | Avoided by removing 😊 from prompt templates (HF2) | Check `chat_logs.content` for the actual stored reply |
| `'list' object has no attribute 'get'` | Returned JSON array `[...]` instead of object `{...}` | `isinstance(result, dict)` check → retries → `_safe_default` | Check `errors` for `AttributeError` with `list` |
| Bot stuck repeating greeting | LLM returned only `<think>...</think>` content with no JSON | Empty-content branch → fallback → repetition detector triggers escalation message | Check `llm_calls` for `status='fallback'` with `error_type='EmptyContent'` and high output_tokens |

**Diagnostic queries:**

```bash
# All non-OK LLM outcomes in the last hour
sqlite3 db/chat_logs.db \
  "SELECT timestamp, node_name, model, status, error_type, output_tokens
   FROM llm_calls
   WHERE status != 'ok' AND timestamp >= datetime('now', '-1 hour')
   ORDER BY id DESC;"

# Most recent application errors with traceback
sqlite3 db/chat_logs.db \
  "SELECT timestamp, error_type, error_message, substr(traceback, 1, 400)
   FROM errors WHERE source='app' ORDER BY id DESC LIMIT 5;"

# Today's per-model fallback / retry rate (signals a degraded provider)
sqlite3 db/chat_logs.db \
  "SELECT model,
          SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END)        AS ok,
          SUM(CASE WHEN status='json_retry' THEN 1 ELSE 0 END) AS retry,
          SUM(CASE WHEN status='fallback' THEN 1 ELSE 0 END)   AS fallback
   FROM llm_calls
   WHERE timestamp >= date('now')
   GROUP BY model;"
```

When fallback / retry rates spike on a specific model, the upstream provider routing for that model is degraded. Workaround: route the affected label to a different model via `.env` (see Configuration → Per-call model routing).

---

## Operations Playbook

### Cost spike overnight

```bash
sqlite3 db/chat_logs.db "SELECT * FROM v_daily_llm_cost WHERE day >= date('now','-7 days');"
sqlite3 db/chat_logs.db "SELECT * FROM v_daily_llm_cost_by_node WHERE day = date('now');"
sqlite3 db/chat_logs.db "SELECT * FROM v_daily_llm_cost_by_model WHERE day = date('now');"
```

Or open the **Daily Cost** and **By Node / Model** dashboard tabs.

### Patient seeing the generic apology fallback

```bash
sqlite3 db/chat_logs.db \
  "SELECT timestamp, error_type, error_message, context FROM errors WHERE source='app' ORDER BY id DESC LIMIT 10;"
```

### Bot stuck in escalation loop ("آسفة، يبدو إني عالقة في نفس النقطة …")

This is the repetition detector in `graph.py:_is_repeating()` firing — the bot was about to send the same reply twice in a row, and `_safety_net` substituted an escalation offering a callback. **Almost always means the LLM is failing silently** (returning empty or malformed output) so state can't advance. Check:

```bash
# Look for high retry / fallback rate on conversation calls
sqlite3 db/chat_logs.db \
  "SELECT timestamp, model, status, error_type, output_tokens
   FROM llm_calls
   WHERE node_name='conversation' AND timestamp >= datetime('now', '-1 hour')
   ORDER BY id DESC;"
```

If many rows show `status='fallback'` with `error_type='EmptyContent'` and `output_tokens > 0`, the LLM is producing `<think>` tags but no JSON — switch the conversation model (see next scenario).

### LLM provider degraded — switch the affected model

When fallback / retry rate on a specific label spikes, the upstream provider routing for that model is bad today. Override just that label without touching code:

```bash
# in .env — example: route conversation away from Llama 3.3 70B
OPENROUTER_MODEL_CONVERSATION=meta-llama/llama-4-maverick
sudo systemctl restart andalusia-bot
```

Reversible — delete the line and restart to revert. Verify the switch landed via the dashboard's **By Node / Model** tab.

### CRM prices not appearing

1. Check `errors` for `source='crm'` — likely auth or network.
2. Check MSAL token cache: `~/.andalusia_crm_cache.bin` (path overridable via `MSAL_TOKEN_CACHE_PATH`).
3. If MFA is enabled and the cache is stale: run `scripts/check_crm_prices.py` locally (browser flow) and copy the cache to the server.

### Kill switch — disable per-LLM-call logging

```bash
# in .env
LOG_LLM_CALLS=0
sudo systemctl restart andalusia-bot
```

### Cost reduction — route cheap labels to smaller model

```bash
# in .env
OPENROUTER_MODEL_INTENT=meta-llama/llama-3.1-8b-instruct
OPENROUTER_MODEL_TIME_PARSE=meta-llama/llama-3.1-8b-instruct
sudo systemctl restart andalusia-bot
```

### Enable prompt caching (the big cost win)

```bash
# in .env
OPENROUTER_PROMPT_CACHING=1
sudo systemctl restart andalusia-bot
```

Note: `llm_calls.estimated_cost_usd` will over-estimate when cache hits occur (we charge full input rate); real savings appear on the OpenRouter dashboard.

### Stale DB connections — quick recycle

```bash
# in .env
DB_POOL_MAX_AGE_SECONDS=60      # aggressive recycle (default 300)
# or fully disable pool reuse for debugging:
DB_POOL_MAX_AGE_SECONDS=0
sudo systemctl restart andalusia-bot
```

### Disable response cache to bisect a regression

```bash
# in .env
RESPONSE_CACHE_ENABLED=0
sudo systemctl restart andalusia-bot
```

---

## Key Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI + per-turn pipeline driver |
| `graph.py` | LangGraph pipeline wiring + safety net + transient-field cleanup |
| `state.py` | `BookingState` TypedDict |
| `errors.py` | Structured `BotError` exception type |
| `config/settings.py` | All env-driven config (credentials, model routing, reliability, feature flags) |
| `config/constants.py` | Specialty lists (75 EN / 67 AR), insurance companies, emergency keywords, pediatric specialties |
| `prompts/conversation.py` | Main LLM system prompt (Saudi-dialect rules, stage behaviors, JSON return shape) |
| `prompts/{intent,routing,triage}.py` | Smaller per-label prompts |
| `nodes/conversation.py` | LLM brain — generates reply + state updates |
| `nodes/routing.py` | 3-path specialty routing (symptoms / specialty / doctor name) |
| `nodes/doctor_selection.py` | Fuzzy doctor matching + slot fetch + alternative-doctor surfacing |
| `nodes/slot_selection.py` | Time parsing + slot safety net |
| `nodes/patient_info.py` | Phone + insurance collection → confirmation |
| `nodes/_helpers.py` | State update logic, acceptance detection, insurance fuzzy match |
| `services/router.py` | Specialty routing (keyword prefilter + LLM stage) |
| `services/doctor_selector.py` | Doctor DB queries + 30s short-TTL cache |
| `services/slot_handler.py` | Time parsing (`_PERIODS` dict, PM assumption, period filters) |
| `services/formatter.py` | All bilingual user-facing message templates |
| `services/doctor_price.py` | CRM-side walk-in price matching (4-pass fuzzy) |
| `db/database.py` | Hospital SQL Server pool + retry + age-out + atexit drain |
| `db/crm_database.py` | Dynamics 365 CRM connector (MSAL auth + retry + token cache) |
| `db/logger.py` | SQLite logger — schema, `log_turn`, `log_llm_call`, `log_error`, dashboard queries |
| `llm/client.py` | OpenRouter HTTP client — session reuse, retry+jitter, JSON retry, response cache, size guard, per-turn metrics |
| `utils/datetime_fmt.py` | Bilingual date/time formatting + Arabic-Indic numerals |
| `utils/language.py` | Arabic normalization (hamza/ta-marbuta/ya) + dialect sanitizer |
| `utils/fuzzy_match.py` | 4-pass doctor-name fuzzy match (full → first → last → any part) |
| `pages/logs.py` | Streamlit observability dashboard (4 tabs) |
| `deploy.sh` | Ubuntu systemd deploy script |

---

## Not Yet Built

These intents are detected but currently return a "module under development" response:

- **Inquiry** — pricing, hours, services questions
- **Complaint** — patient complaints handling
- **Cancellation** — cancel existing appointments
- **Reschedule** — change appointment times

Other future work:
- **Service booking** — lab tests, imaging, procedures (today: doctor appointments only)
- **Dotcare POST** — bookings are read-only on the hospital DB; not yet written back to the booking system
- **WhatsApp Business API** — currently Streamlit; needs FastAPI webhook for production WhatsApp deployment (`LANGGRAPH_CHECKPOINTER=memory` is wired and ready as the state-persistence lever)
- **Conversation audit beyond SQLite** — if a centralized observability stack is added, the per-call structured rows are ready to ship

---

## Further Reading

- **`CLAUDE.md`** — detailed dev notes: prompt rules, architecture nuances, the 9 critical rules, debugging hints. Read this when modifying prompts or adding nodes.
- **`PRODUCTION_PLAN.md`** — the production-hardening plan (Stages 1-5, 26 items). Read this to understand the design tradeoffs.
- **`PRODUCTION_PROGRESS.md`** — what shipped, with per-item summaries and verification notes. **Includes the hotfix log at the bottom (HF1/HF2/HF3) documenting the LLM-output failure modes and the fixes applied.** Read this when triaging a regression.
- **`.env.example`** — annotated env-var template with all categories. The reference for what each env var does.
- **`deploy.sh`** — deployment script (Ubuntu systemd).
- **`state.py`** — `BookingState` TypedDict — the contract every node reads/writes. Read this before touching any node.

### When to read what

| Task | Read |
|---|---|
| "I'm new — how do I run this?" | This README → Quick Start |
| "Something just crashed" | This README → Operations Playbook + Known failure modes; then `errors` table |
| "I want to add a feature" | `CLAUDE.md` + `state.py` + the relevant node in `nodes/` |
| "I want to change the prompt" | `CLAUDE.md` § Critical Rules + `prompts/conversation.py` |
| "Why was X designed this way?" | `PRODUCTION_PLAN.md` + git history |
| "Bot is misbehaving for one specific patient" | Dashboard's Sessions tab → drill into their session_id |

---

## Design Principles

1. **LLM controls conversation, code controls logic.** The LLM never queries the DB; it sets state flags and code does the rest. Safety nets in code catch what the LLM misses (slot detection, insurance fuzzy match, dialect sanitization).
2. **Every safety net is intentional.** The slot safety net (`_slot_safety_net`), insurance safety net, repetition detector, escalation message, and dialect sanitizer all exist because real conversations stress-tested them.
3. **Defaults preserve current behavior.** Every Stage 1-5 hardening change is opt-in via `.env` — unchanged config keeps the bot byte-identical to its pre-hardening behavior.
4. **Observability is a first-class citizen.** Every LLM call, every error, every cache hit is logged structurally for ops triage.
