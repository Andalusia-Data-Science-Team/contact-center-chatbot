# config/settings.py
# ─── External service credentials & tunable thresholds ───
import os

# --- OpenRouter LLM ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# --- SQL Server ---
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER = os.getenv("DB_SERVER", "")
DB_DATABASE = os.getenv("DB_DATABASE", "")
DB_USERNAME = os.getenv("DB_USERNAME", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# --- Dynamics 365 CRM (doctor reference data incl. walk-in / cash price) ---
# Accessed via the CRM TDS endpoint (SQL protocol) with Azure AD auth.
CRM_SERVER = os.getenv("CRM_SERVER", "")                 # e.g. "org2f45e702.crm4.dynamics.com,5558"
CRM_CLIENT_ID = os.getenv("CRM_CLIENT_ID", "51f81489-12ee-4a9e-aaae-a2591f45987d")
CRM_TENANT = os.getenv("CRM_TENANT", "organizations")
CRM_USERNAME = os.getenv("CRM_USERNAME", "")
CRM_PASSWORD = os.getenv("CRM_PASSWORD", "")
CRM_DOCTOR_TABLE = os.getenv("CRM_DOCTOR_TABLE", "dbo.cr301_newdoctordataset")
CRM_FEE_TABLE = os.getenv("CRM_FEE_TABLE", "dbo.cr301_table1")
CRM_PRICE_CACHE_TTL_SECONDS = int(os.getenv("CRM_PRICE_CACHE_TTL_SECONDS", "86400"))  # 24h

# --- Thresholds ---
ROUTING_CONFIDENCE_THRESHOLD = 0.65
INTENT_SWITCH_CONFIDENCE_THRESHOLD = 0.85
FUZZY_AUTO_CORRECT_THRESHOLD = 85
FUZZY_CONFIRM_THRESHOLD = 60

# --- Age threshold for pediatric routing ---
PEDIATRIC_AGE_THRESHOLD = 10

# --- View Mode ---
# "stakeholder" = clean UI for demos (no metrics, simplified doctor list)
# "dev"         = developer UI with token counts, latency, cost tracking
VIEW_MODE = os.getenv("VIEW_MODE", "stakeholder")

# --- LLM Pricing (per 1M tokens, OpenRouter Llama 3.3 70B) ---
LLM_INPUT_PRICE_PER_M = 0.90    # $0.90 per 1M input tokens
LLM_OUTPUT_PRICE_PER_M = 0.90   # $0.90 per 1M output tokens


# ─────────────────────────────────────────────────────────────────────────────
# Production-hardening knobs (added in Stage 1.9). Every value below defaults
# to current behavior, so an unchanged .env preserves the production flow.
# ─────────────────────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = True) -> bool:
    """Parse a truthy/falsy env var. None → default; '0'/'false'/'no'/'off'/'' → False."""
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


# --- Per-call model routing (consumed when Stage 4 item 1.1 lands) ---
# Each label can route to a different model; all default to OPENROUTER_MODEL so
# unchanged config keeps everything on Llama 3.3 70B exactly as today.
OPENROUTER_MODEL_CONVERSATION = os.getenv("OPENROUTER_MODEL_CONVERSATION", OPENROUTER_MODEL)
OPENROUTER_MODEL_ROUTING      = os.getenv("OPENROUTER_MODEL_ROUTING",      OPENROUTER_MODEL)
OPENROUTER_MODEL_INTENT       = os.getenv("OPENROUTER_MODEL_INTENT",       OPENROUTER_MODEL)
OPENROUTER_MODEL_TRIAGE       = os.getenv("OPENROUTER_MODEL_TRIAGE",       OPENROUTER_MODEL)
OPENROUTER_MODEL_TIME_PARSE   = os.getenv("OPENROUTER_MODEL_TIME_PARSE",   OPENROUTER_MODEL)


# --- Reliability / timeouts (consumed in Stage 2) ---
LLM_TIMEOUT_SECONDS      = int(os.getenv("LLM_TIMEOUT_SECONDS",      "30"))
DB_QUERY_TIMEOUT_SECONDS = int(os.getenv("DB_QUERY_TIMEOUT_SECONDS", "15"))
DB_POOL_MAX_SIZE         = int(os.getenv("DB_POOL_MAX_SIZE",         "10"))
DB_POOL_MAX_AGE_SECONDS  = int(os.getenv("DB_POOL_MAX_AGE_SECONDS",  "300"))


# --- Logging (consumed by db/logger.py) ---
# Empty string = use the in-repo default (db/chat_logs.db). Override to point
# at a shared volume or an alternate filename.
LOG_DB_PATH = os.getenv("LOG_DB_PATH", "")
# Kill switch for the per-LLM-call ledger. Disable in extreme situations
# (e.g. disk full) — leaves the rest of the booking flow untouched.
LOG_LLM_CALLS = _env_bool("LOG_LLM_CALLS", True)


# --- Feature flags (consumed in later stages) ---
# Stage 4 item 1.2 — opt-in provider-side prompt-caching header.
OPENROUTER_PROMPT_CACHING = _env_bool("OPENROUTER_PROMPT_CACHING", False)
# Stage 4 item 1.3 — local response cache for intent + time_parse. Set to 0 to
# bisect a regression after the cache lands.
RESPONSE_CACHE_ENABLED    = _env_bool("RESPONSE_CACHE_ENABLED", True)
# Stage 5 item 3.3 — 'memory' enables an in-memory LangGraph checkpointer;
# 'none' (default) keeps the current Streamlit-only state model.
LANGGRAPH_CHECKPOINTER    = os.getenv("LANGGRAPH_CHECKPOINTER", "none")
