# Andalusia Booking Bot — Project Context

You are helping continue development of **Nour (نور)**, an AI-powered WhatsApp booking agent for Andalusia Health Group hospitals (KSA and Egypt).

---

## What This Project Is

A bilingual (Arabic/English) conversational booking chatbot that handles the complete doctor appointment booking flow:
1. Greets the patient, asks their name
2. Captures symptoms or specialty/doctor name → routes to the right medical specialty (75+ specialties)
3. Shows available doctors from the live hospital SQL Server database
4. Handles slot selection with smart time parsing ("tonight", "after 7", "8 PM")
5. Collects phone number + insurance info
6. Confirms the booking with a formatted card + Dotcare app download message

Runs as a Streamlit web app. Deployed at `http://10.24.105.220:8502` (company network).

- **Agent name:** Nour (نور)
- **Languages:** Arabic (Saudi + Egyptian dialects) and English
- **LLM:** OpenRouter, Llama 3.3 70B Instruct

---

## Tech Stack

- **Frontend:** Streamlit (chat UI with dev/stakeholder toggle)
- **Orchestration:** LangGraph (8-node sequential pipeline)
- **LLM:** OpenRouter (`meta-llama/llama-3.3-70b-instruct`)
- **Database:** Microsoft SQL Server (hospital booking system) via pyodbc + ODBC Driver 17
- **Fuzzy matching:** rapidfuzz (doctor names + insurance companies)
- **Config:** `.env` file loaded via python-dotenv

---

## Architecture

### Core Philosophy
- **LLM controls conversation** — what to say, what to extract from patient messages
- **Code controls logic** — DB queries, slot math, fuzzy matching, validation, policy enforcement
- **LLM never queries DB** — it sets state flags; code queries the DB and formats responses
- **Safety nets in code** catch things the LLM misses (slot detection, insurance matching)

### Pipeline (graph.py)
Every patient message flows through these nodes sequentially:

```
language → emergency → intent → conversation → routing
  → doctor_selection → slot_selection → patient_info
```

Non-booking intents are handled by `response_node` instead of the booking pipeline.

### 3 Booking Paths

| Path | Trigger | Flow |
|------|---------|------|
| **A** | Symptoms ("عندي وجع في عيني") | Ask age → triage question → route to specialty → confirm → show doctors |
| **B** | Named specialty ("أبي كشف عيون") | Ask age → skip triage → confirm → show doctors |
| **C** | Named doctor ("د. ولاء السعدني") | Skip routing → show slots directly |

### State Machine (booking_stage field in BookingState)

```
none → greeting_done → complaint → routing → doctor_list
  → slot_selection → patient_info → complete
```

State persists across all turns in `BookingState` TypedDict (see `state.py`).

---

## File Structure (4,158 lines total)

```
Booking_bot_Latest/
├── app.py                  # Streamlit UI + dev/stakeholder toggle (load_dotenv at top)
├── graph.py                # LangGraph orchestrator
├── state.py                # BookingState TypedDict + initial_state()
├── .env                    # Credentials (NEVER commit to git)
├── .env.example            # Template (safe to commit)
├── .gitignore
├── deploy.sh               # Ubuntu deployment script (systemd service)
├── requirements.txt        # streamlit, langgraph, requests, pyodbc, rapidfuzz, python-dotenv
│
├── config/
│   ├── settings.py         # All credentials from os.getenv()
│   └── constants.py        # SPECIALTIES_EN/AR, SPECIALTY_EN_TO_AR (60+ mappings),
│                           # INSURANCE_COMPANIES (with Arabic variants), emergency keywords
│
├── nodes/                  # Pipeline nodes (1,199 lines total)
│   ├── conversation.py     # Main LLM brain (97 lines) — _data_summary, last 14 messages
│   ├── routing.py          # 3-path routing (274 lines) — specialty confirmation, triage
│   ├── doctor_selection.py # Fuzzy matching + slot fallback cascade (124 lines)
│   ├── slot_selection.py   # Time parsing + _slot_safety_net (361 lines — LARGEST node)
│   ├── patient_info.py     # Info collection + confirmation trigger (53 lines)
│   ├── _helpers.py         # apply_llm_updates, insurance safety net (141 lines)
│   ├── intent.py           # Intent classification with hard-lock (45 lines)
│   ├── language.py         # Arabic/English regex detection
│   ├── emergency.py        # Emergency keyword detection
│   └── response.py         # Non-booking response handler
│
├── services/
│   ├── router.py           # Specialty routing — keyword prefilter narrows 75→3-6, LLM picks
│   ├── doctor_selector.py  # SQL Server doctor queries (fetch_doctors, get slots)
│   ├── slot_handler.py     # Time parsing: _PERIODS dict, _try_direct_parse, PM assumption
│   └── formatter.py        # All bilingual message templates (247 lines)
│                           # format_time(t,lang), format_date(d,lang) → Arabic-Indic numerals
│
├── prompts/
│   ├── conversation.py     # Main system prompt (209 lines, ~1,800 tokens)
│   ├── routing.py          # Specialty routing prompt (always returns EN names)
│   ├── triage.py           # Clarifying question prompt
│   └── intent.py           # Intent classifier prompt
│
├── utils/
│   ├── datetime_fmt.py     # format_date(d,lang), format_time(t,lang), resolve_relative_date
│   │                       # Arabic: "اليوم"/"بكرا"/"٩:٤٥ مساءً"
│   ├── fuzzy_match.py      # 4-pass: full name → first → last → any part
│   ├── language.py         # detect_language()
│   └── emergency.py        # is_emergency()
│
├── db/
│   └── database.py         # get_connection(), SQL_QUERY, SLOTS_QUERY
│
└── llm/
    └── client.py           # call_llm(), reset_turn_metrics(), get_turn_metrics()
                            # Tracks real usage.prompt_tokens/completion_tokens from API
```

---

## Critical Rules (Don't Break These)

### 1. Routing always uses EN specialty names internally
`SPECIALTIES_EN` and `SPECIALTIES_AR` lists have different lengths (67 vs 75). Routing ALWAYS returns EN names, then translates via `SPECIALTY_EN_TO_AR` dict for display. Never `zip()` the two lists.

### 2. Arabic localization
- Arabic-Indic numerals (٠١٢٣٤٥٦٧٨٩) via `_to_arabic_numerals()` in `utils/datetime_fmt.py`
- `format_date(d, lang)` → "اليوم", "بكرا", "الإثنين، ١٠ مارس"
- `format_time(t, lang)` → "٩:٤٥ مساءً"
- ALWAYS pass `lang` parameter to these functions

### 3. The `_proposed_slot` flow
When a patient asks for a non-exact time, code stores the nearest slot in `state["_proposed_slot"]`. Next turn's "yes"/"مناسب" confirms it. Prevents infinite loops.

### 4. Safety nets
- **Slot safety net** (`nodes/slot_selection.py` → `_slot_safety_net`): Regex catches "tonight", "after 7", "8 PM" when LLM misses them
- **Insurance safety net** (`nodes/_helpers.py` → `_try_match_insurance_from_message`): Fuzzy match against 25+ companies including "مدرايت", "بوبه"

### 5. 4-pass fuzzy doctor matching
`utils/fuzzy_match.py`: full name → first name → last name → any part. "Dr Badwy" → "Mahmoud ElBadawy".

### 6. Routing node owns the reply
When `booking_stage == "routing"`, the routing node clears `state["last_bot_message"] = ""` so the conversation LLM's reply doesn't override.

### 7. Context window = 14 messages
LLM sees `messages[-14:]` in `nodes/conversation.py`. Full state persists regardless.

### 8. Slot_selection prompt rules
The conversation prompt (line 108-140) has strict rules: LLM must return `reply: ""` for ALL slot actions and let code handle responses. The `ABSOLUTE RULES` section must not be weakened.

### 9. Credentials in .env only
`load_dotenv()` at top of `app.py`. Settings reads via `os.getenv()`. Never hardcode.

---

## Token Usage & Cost

- Per booking (10-15 turns): ~33,000 tokens, ~$0.003-0.005
- Conversation LLM (each turn): ~2,300 tokens (biggest cost — system prompt is ~1,800 tokens)
- Intent classifier: ~500 tokens per turn (often hard-locked mid-booking)
- LLM pricing: $0.90/M input, $0.90/M output (OpenRouter Llama 3.3 70B)

---

## Running Locally

```bash
cp .env.example .env  # Fill in credentials
pip install -r requirements.txt
streamlit run app.py
# Open http://localhost:8502
```

## Deployment

```bash
bash deploy.sh  # On Ubuntu server
# Sets up systemd service 'andalusia-bot' on port 8502
sudo systemctl restart andalusia-bot  # After code changes
sudo journalctl -u andalusia-bot -f   # Live logs
```

---

## What's NOT Yet Built

- **Inquiry module** — services, prices, offers, working hours, doctor info
- **Cancellation module** — cancel existing appointments
- **Reschedule module** — change appointment times
- **Service booking** — lab tests, imaging, procedures (vs doctor appointments)
- **Dotcare POST integration** — currently read-only; booking doesn't write to Dotcare yet
- **WhatsApp Business API** — currently Streamlit web UI; production needs FastAPI webhook
- **Conversation logging** — no audit trail to DB yet

---

## Debugging

1. Toggle **Dev mode** (button in app.py header) → shows tokens, latency, state inspector
2. Check `state["_llm_updates"]` to see what LLM extracted
3. Check `booking_stage` to see which node should handle
4. Server logs: `sudo journalctl -u andalusia-bot -f`
5. If routing is wrong → check `services/router.py` keyword prefilter + `prompts/routing.py`
6. If time parsing breaks → check `_PERIODS` dict in `services/slot_handler.py`, PM assumed for hours 1-11

---

## Coding Conventions

1. Every user-facing message needs `ar` and `en` versions in `services/formatter.py`
2. When LLM is unreliable for a signal, add a code-level safety net (pattern: slot safety net, insurance safety net)
3. Never hardcode credentials — `os.getenv()` in `config/settings.py`
4. Always pass `lang` to `format_date()` and `format_time()`
5. State updates go through `_llm_updates` dict
6. Use `_to_arabic_numerals()` for any number in Arabic output
7. Booking-related code responses clear `state["last_bot_message"] = ""` when taking over from LLM

---

## Suggested Next Priorities

1. **Inquiry module** — high volume, reuses existing pipeline pattern
2. **Dotcare POST integration** — make bookings actually write to the system
3. **WhatsApp Business API** — FastAPI webhook, reuse entire graph pipeline
4. **Conversation logging** — audit trail for QA
5. **Cancellation + Rescheduling** — completes the patient lifecycle
