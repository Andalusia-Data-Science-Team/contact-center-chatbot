# pages/logs.py
"""Chat Logs Dashboard — view all tester conversations and token usage."""

import streamlit as st
from db.logger import get_all_sessions, get_session_logs, get_summary_stats
from config.settings import LLM_INPUT_PRICE_PER_M, LLM_OUTPUT_PRICE_PER_M

st.set_page_config(page_title="Chat Logs", page_icon="📊", layout="wide")

# ── Styling ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #fefaf6; }
    .stApp { background-color: #fefaf6; }
    h1, h2, h3 { color: #5d4037 !important; }
</style>
""", unsafe_allow_html=True)


def _cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * LLM_INPUT_PRICE_PER_M / 1_000_000) + \
           (output_tokens * LLM_OUTPUT_PRICE_PER_M / 1_000_000)


# ── Header ───────────────────────────────────────────────────────────────────
st.title("📊 Chat Logs Dashboard")
st.caption("Monitor tester conversations, token usage, and costs")

# ── Summary Cards ────────────────────────────────────────────────────────────
stats = get_summary_stats()
total_cost = _cost(stats["total_input_tokens"], stats["total_output_tokens"])

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Sessions", stats["total_sessions"])
col2.metric("Total Turns", stats["total_turns"])
col3.metric("Input Tokens", f'{stats["total_input_tokens"]:,}')
col4.metric("Output Tokens", f'{stats["total_output_tokens"]:,}')
col5.metric("Total Cost", f"${total_cost:.4f}")

st.markdown("---")

# ── Session List ─────────────────────────────────────────────────────────────
sessions = get_all_sessions()

if not sessions:
    st.info("No chat sessions recorded yet. Start a conversation on the main page.")
    st.stop()

# Filters
filter_col1, filter_col2 = st.columns([2, 2])
with filter_col1:
    search = st.text_input("🔍 Filter by patient name", "")
with filter_col2:
    stage_filter = st.selectbox(
        "Filter by booking stage",
        ["All"] + sorted(set(s.get("booking_stage") or "—" for s in sessions)),
    )

filtered = sessions
if search:
    filtered = [s for s in filtered if search.lower() in (s.get("patient_name") or "").lower()]
if stage_filter != "All":
    stage_val = None if stage_filter == "—" else stage_filter
    filtered = [s for s in filtered if (s.get("booking_stage") or None) == stage_val]

# ── Sessions Table ───────────────────────────────────────────────────────────
st.subheader(f"Sessions ({len(filtered)})")

for s in filtered:
    s_cost = _cost(s["total_input_tokens"], s["total_output_tokens"])
    total_tokens = s["total_input_tokens"] + s["total_output_tokens"]
    patient = s.get("patient_name") or "—"
    lang = (s.get("language") or "—").upper()
    stage = s.get("booking_stage") or "—"
    started = s["started_at"][:16].replace("T", " ")
    last = s["last_active"][:16].replace("T", " ")

    with st.expander(
        f"**{patient}** | {lang} | Turns: {s['total_turns']} | "
        f"Tokens: {total_tokens:,} | Cost: ${s_cost:.5f} | "
        f"Stage: {stage} | {started}"
    ):
        # Session metrics row
        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
        mc1.metric("Turns", s["total_turns"])
        mc2.metric("LLM Calls", s["total_llm_calls"])
        mc3.metric("Input Tokens", f'{s["total_input_tokens"]:,}')
        mc4.metric("Output Tokens", f'{s["total_output_tokens"]:,}')
        mc5.metric("Cost", f"${s_cost:.5f}")
        mc6.metric("Avg Latency", f'{s["total_latency_ms"] // max(s["total_llm_calls"], 1)} ms')

        st.markdown(f"**Started:** {started} — **Last active:** {last}")
        st.markdown("---")

        # Conversation messages
        logs = get_session_logs(s["session_id"])
        for msg in logs:
            role = msg["role"]
            icon = "🧑" if role == "user" else "🤖"
            label = "Patient" if role == "user" else "Nour"

            st.markdown(f"**{icon} {label}** (turn {msg['turn_number']})")
            st.markdown(f"> {msg['content']}")

            # Show token info for bot replies
            if role == "assistant" and msg["input_tokens"] > 0:
                st.caption(
                    f"Tokens: {msg['input_tokens']:,} in / {msg['output_tokens']:,} out | "
                    f"LLM calls: {msg['llm_calls']} | "
                    f"Latency: {msg['latency_ms']} ms"
                )
            st.markdown("")
