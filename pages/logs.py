# pages/logs.py
"""Chat Logs Dashboard — view all tester conversations and token usage."""

import pandas as pd
import streamlit as st
from db.logger import (
    get_all_sessions,
    get_session_logs,
    get_summary_stats,
    get_daily_llm_cost,
    get_daily_llm_cost_by_node,
    get_daily_llm_cost_by_model,
    get_daily_errors,
    get_recent_failed_llm_calls,
    get_recent_errors,
)
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

# ── Tabs ─────────────────────────────────────────────────────────────────────
tab_sessions, tab_daily, tab_breakdown, tab_errors = st.tabs([
    "Sessions", "Daily Cost", "By Node / Model", "Errors",
])

# ── Tab 1: Sessions (original view, unchanged) ───────────────────────────────
with tab_sessions:
    sessions = get_all_sessions()

    if not sessions:
        st.info("No chat sessions recorded yet. Start a conversation on the main page.")
    else:
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


# ── Tab 2: Daily Cost (last 14 days) ─────────────────────────────────────────
with tab_daily:
    daily_rows = get_daily_llm_cost(days=14)
    if not daily_rows:
        st.info("No LLM calls recorded in the last 14 days.")
    else:
        df = pd.DataFrame(daily_rows).set_index("day")
        st.subheader("Daily LLM cost (last 14 days)")
        st.line_chart(df[["total_cost_usd"]])

        st.markdown("---")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Daily calls**")
            st.bar_chart(df[["calls"]])
        with col_b:
            st.markdown("**Daily tokens**")
            st.bar_chart(df[["input_tokens", "output_tokens"]])

        st.markdown("---")
        st.markdown("**Detail**")
        st.dataframe(df, use_container_width=True)


# ── Tab 3: By Node / Model ───────────────────────────────────────────────────
with tab_breakdown:
    node_rows = get_daily_llm_cost_by_node(days=14)
    model_rows = get_daily_llm_cost_by_model(days=14)

    col_n, col_m = st.columns(2)

    with col_n:
        st.subheader("Cost by node (last 14 days)")
        if not node_rows:
            st.info("No node data yet.")
        else:
            dfn = pd.DataFrame(node_rows)
            agg_n = dfn.groupby("node_name", as_index=False).agg(
                calls=("calls", "sum"),
                total_cost_usd=("total_cost_usd", "sum"),
                input_tokens=("input_tokens", "sum"),
                output_tokens=("output_tokens", "sum"),
            ).sort_values("total_cost_usd", ascending=False)
            st.bar_chart(agg_n.set_index("node_name")[["total_cost_usd"]])
            st.dataframe(agg_n, use_container_width=True, hide_index=True)

    with col_m:
        st.subheader("Cost by model (last 14 days)")
        if not model_rows:
            st.info("No model data yet.")
        else:
            dfm = pd.DataFrame(model_rows)
            agg_m = dfm.groupby("model", as_index=False).agg(
                calls=("calls", "sum"),
                total_cost_usd=("total_cost_usd", "sum"),
                input_tokens=("input_tokens", "sum"),
                output_tokens=("output_tokens", "sum"),
            ).sort_values("total_cost_usd", ascending=False)
            st.bar_chart(agg_m.set_index("model")[["total_cost_usd"]])
            st.dataframe(agg_m, use_container_width=True, hide_index=True)


# ── Tab 4: Errors ────────────────────────────────────────────────────────────
with tab_errors:
    err_daily = get_daily_errors(days=14)
    failed_llm = get_recent_failed_llm_calls(limit=50)
    recent_errs = get_recent_errors(limit=50)

    st.subheader("Daily errors by source (last 14 days)")
    if err_daily:
        dfe = pd.DataFrame(err_daily)
        pivot = dfe.pivot_table(
            index="day", columns="source", values="error_count",
            aggfunc="sum", fill_value=0,
        )
        st.bar_chart(pivot)
    else:
        st.info("No non-LLM errors recorded.")

    st.markdown("---")
    st.subheader("Recent failed LLM calls")
    if failed_llm:
        df_fl = pd.DataFrame(failed_llm)[
            ["timestamp", "node_name", "model", "status", "error_type", "error_message"]
        ]
        st.dataframe(df_fl, use_container_width=True, hide_index=True)
    else:
        st.success("No failed LLM calls in the last 50.")

    st.markdown("---")
    st.subheader("Recent non-LLM errors")
    if recent_errs:
        df_er = pd.DataFrame(recent_errs)[
            ["timestamp", "source", "error_type", "error_message", "session_id"]
        ]
        st.dataframe(df_er, use_container_width=True, hide_index=True)
    else:
        st.success("No non-LLM errors recorded.")
