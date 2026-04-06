# config/settings.py
# ─── External service credentials & tunable thresholds ───
import os

# --- Fireworks LLM ---
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
FIREWORKS_MODEL = os.getenv("FIREWORKS_MODEL", "accounts/fireworks/models/llama-v3p3-70b-instruct")
FIREWORKS_BASE_URL = os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")

# --- SQL Server ---
DB_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER = os.getenv("DB_SERVER", "")
DB_DATABASE = os.getenv("DB_DATABASE", "")
DB_USERNAME = os.getenv("DB_USERNAME", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

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

# --- LLM Pricing (per 1M tokens, Fireworks Llama 3.3 70B) ---
LLM_INPUT_PRICE_PER_M = 0.90    # $0.90 per 1M input tokens
LLM_OUTPUT_PRICE_PER_M = 0.90   # $0.90 per 1M output tokens
