# app.py
from dotenv import load_dotenv
load_dotenv()  # Load .env file before anything else

import streamlit as st
from state import initial_state
from graph import compiled_graph
from config.settings import VIEW_MODE, LLM_INPUT_PRICE_PER_M, LLM_OUTPUT_PRICE_PER_M
from llm.client import reset_turn_metrics, get_turn_metrics


# ── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Andalusia Hospital Booking",
    page_icon="hospital",
    layout="wide",
)

st.markdown("""
<style>
    .main { background-color: #fefaf6; padding: 2rem; font-size: 18px; }
    .stApp { background-color: #fefaf6; font-size: 18px; }
    html, body, [class*="css"] { font-size: 18px; }

    h1 { color: #5d4037; font-size: 2.4rem; font-weight: 700; }
    h2, h3 { color: #5d4037 !important; }
    .stCaption { color: #9C8360; font-size: 1rem; }

    .stChatMessage {
        border-radius: 12px;
        background-color: #f8f4f0;
        border: 1px solid #d4b896;
    }

    .stChatInput > div {
        background-color: #f8f4f0 !important;
        border: 2px solid #d4b896 !important;
        border-radius: 10px !important;
        font-size: 1.1rem !important;
    }

    .stButton > button {
        background: linear-gradient(135deg, #B49A79 0%, #9C8360 100%) !important;
        color: white !important;
        border: none !important;
        padding: 12px 30px !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        font-size: 1rem !important;
        box-shadow: 0 4px 10px rgba(0,0,0,0.12) !important;
        transition: all 0.3s ease !important;
        letter-spacing: 0.02em !important;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #9C8360 0%, #846B4D 100%) !important;
        box-shadow: 0 6px 15px rgba(0,0,0,0.18) !important;
        transform: translateY(-2px) !important;
    }

    .stAlert {
        border-radius: 8px;
        border-left: 4px solid #B49A79;
        font-size: 1.1rem;
        background-color: #f8f4f0 !important;
        color: #5d4037 !important;
    }

    .typing-indicator {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 14px 18px;
        background-color: #f8f4f0;
        border: 1px solid #d4b896;
        border-radius: 12px;
        width: fit-content;
    }
    .typing-indicator .dot {
        display: inline-block;
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background-color: #B49A79;
        animation: typing-bounce 1.2s infinite ease-in-out;
    }
    .typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
    .typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }
    .typing-status {
        margin-left: 8px;
        font-size: 0.95rem;
        color: #9C8360;
        white-space: nowrap;
    }

    @keyframes typing-bounce {
        0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
        30%            { transform: translateY(-6px); opacity: 1; }
    }

    .stSpinner { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ── Session State Init ───────────────────────────────────────────────────────
if "bot_state" not in st.session_state:
    st.session_state.bot_state = initial_state()

if "chat_display" not in st.session_state:
    st.session_state.chat_display = []

if "is_typing" not in st.session_state:
    st.session_state.is_typing = False

if "view_mode" not in st.session_state:
    st.session_state.view_mode = VIEW_MODE  # Initialize from settings.py default

# Allow runtime override
active_view_mode = st.session_state.view_mode

# ── Header ───────────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    st.title("Andalusia Hospital")
    if active_view_mode == "dev":
        st.caption("AI Booking Assistant — 🛠️ Developer Mode")
    else:
        st.caption("AI Booking Assistant")
with col3:
    st.image("your_company_logo.png", width=450)

st.markdown("""
<div style="height:4px; background:linear-gradient(90deg,#B49A79,#9C8360);
            margin:20px 0; border-radius:2px; box-shadow:0 2px 4px rgba(0,0,0,0.08);"></div>
""", unsafe_allow_html=True)

_, toggle_col, reset_col = st.columns([4, 1, 1])
with toggle_col:
    current_mode = st.session_state.view_mode
    button_label = "🛠️ Dev Mode" if current_mode == "stakeholder" else "👔 Stakeholder"
    if st.button(button_label, use_container_width=True):
        st.session_state.view_mode = "dev" if current_mode == "stakeholder" else "stakeholder"
        st.rerun()
with reset_col:
    if st.button("Reset Conversation", use_container_width=True):
        st.session_state.bot_state = initial_state()
        st.session_state.chat_display = []
        st.session_state.is_typing = False
        st.rerun()

# ── Status Messages ───────────────────────────────────────────────────────────
def _get_status_message(state: dict) -> str:
    lang = state.get("language", "en")
    stage = state.get("booking_stage")

    if stage == "routing":
        return "🔍 جاري البحث عن الأطباء المتاحين..." if lang == "ar" else "🔍 Searching for available doctors..."
    if stage == "doctor_list" and state.get("doctor"):
        return "📅 جاري تحميل المواعيد المتاحة..." if lang == "ar" else "📅 Loading available slots..."
    if stage == "slot_selection":
        return "⏰ جاري تأكيد الموعد..." if lang == "ar" else "⏰ Confirming your slot..."
    if stage in ("patient_info", "complete"):
        return "✅ جاري تأكيد الحجز..." if lang == "ar" else "✅ Confirming your booking..."
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# DEV MODE: Sidebar Metrics Dashboard
# ══════════════════════════════════════════════════════════════════════════════
if active_view_mode == "dev":
    with st.sidebar:
        st.markdown("## 🛠️ Dev Dashboard")
        st.markdown("---")

        state = st.session_state.bot_state

        # ── Session Totals ──
        total_in = state.get("_session_total_input_tokens", 0)
        total_out = state.get("_session_total_output_tokens", 0)
        total_tokens = total_in + total_out
        total_calls = state.get("_session_total_llm_calls", 0)
        total_latency = state.get("_session_total_latency_ms", 0)
        turns = state.get("_session_turns", 0)
        cost = (total_in * LLM_INPUT_PRICE_PER_M / 1_000_000) + \
               (total_out * LLM_OUTPUT_PRICE_PER_M / 1_000_000)

        st.markdown("### 📊 Session Totals")
        col_a, col_b = st.columns(2)
        col_a.metric("Turns", turns)
        col_b.metric("LLM Calls", total_calls)

        col_c, col_d = st.columns(2)
        col_c.metric("Input Tokens", f"{total_in:,}")
        col_d.metric("Output Tokens", f"{total_out:,}")

        col_e, col_f = st.columns(2)
        col_e.metric("Total Tokens", f"{total_tokens:,}")
        col_f.metric("Est. Cost", f"${cost:.5f}")

        col_g, col_h = st.columns(2)
        col_g.metric("Total Latency", f"{total_latency:,} ms")
        avg_latency = total_latency // max(total_calls, 1)
        col_h.metric("Avg / Call", f"{avg_latency} ms")

        st.markdown("---")

        # ── Last Turn Breakdown ──
        last_metrics = state.get("_last_turn_metrics")
        if last_metrics:
            st.markdown("### ⏱️ Last Turn")
            turn_in = last_metrics.get("total_input_tokens", 0)
            turn_out = last_metrics.get("total_output_tokens", 0)
            turn_calls = last_metrics.get("llm_calls", 0)
            turn_latency = last_metrics.get("total_latency_ms", 0)
            turn_cost = (turn_in * LLM_INPUT_PRICE_PER_M / 1_000_000) + \
                        (turn_out * LLM_OUTPUT_PRICE_PER_M / 1_000_000)

            st.markdown(
                f"**Calls:** {turn_calls} · "
                f"**Tokens:** {turn_in + turn_out:,} (in:{turn_in:,} / out:{turn_out:,}) · "
                f"**Latency:** {turn_latency}ms · "
                f"**Cost:** ${turn_cost:.6f}"
            )

            # Per-call breakdown table
            details = last_metrics.get("calls_detail", [])
            if details:
                st.markdown("**Per-call breakdown:**")
                for d in details:
                    st.markdown(
                        f"- `{d['label']}` — "
                        f"{d['input_tokens']}+{d['output_tokens']} tokens, "
                        f"{d['latency_ms']}ms"
                    )

        st.markdown("---")

        # ── Current State Inspector ──
        st.markdown("### 🔍 State")
        important_fields = [
            "language", "booking_stage", "intent", "patient_name",
            "complaint_text", "patient_age", "speciality", "speciality_ar",
            "specialty_confirmed", "doctor", "doctor_ar", "selected_slot",
            "date", "phone", "insured", "insurance_company",
        ]
        for field in important_fields:
            val = state.get(field)
            if val is not None and val != "" and val != []:
                st.markdown(f"**{field}:** `{val}`")


# ══════════════════════════════════════════════════════════════════════════════
# Chat Area (both modes)
# ══════════════════════════════════════════════════════════════════════════════
chat_container = st.container(height=560)
with chat_container:
    if not st.session_state.chat_display:
        with st.chat_message("assistant"):
            st.markdown(
                "**السلام عليكم، معك نور من مجموعة أندلسية صحة** 😊\n\n"
                "اتشرف بالاسم؟\n\n"
                "---\n\n"
                "**Hello! I'm Nour from Andalusia Health Group** 😊\n\n"
                "May I have your name please?"
            )

    for msg in st.session_state.chat_display:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if st.session_state.is_typing:
        with st.chat_message("assistant"):
            status_msg = _get_status_message(st.session_state.bot_state)
            status_html = f'<span class="typing-status">{status_msg}</span>' if status_msg else ""
            st.markdown(
                f'<div class="typing-indicator">'
                f'<span class="dot"></span><span class="dot"></span><span class="dot"></span>'
                f'{status_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

# ── Input & Processing ────────────────────────────────────────────────────────
user_input = st.chat_input("Type your message here...")

if user_input:
    st.session_state.chat_display.append({"role": "user", "content": user_input})
    st.session_state.is_typing = True
    st.rerun()

if st.session_state.is_typing:
    state = st.session_state.bot_state
    last_user_msg = st.session_state.chat_display[-1]["content"]

    state["messages"].append({"role": "user", "content": last_user_msg})
    state["next_action"] = None

    # Reset per-turn metrics before pipeline runs
    reset_turn_metrics()

    try:
        result = compiled_graph.invoke(state)
        st.session_state.bot_state = result

        # ── Collect metrics from this turn ──
        turn_metrics = get_turn_metrics()
        result["_last_turn_metrics"] = turn_metrics
        result["_session_turns"] = result.get("_session_turns", 0) + 1
        result["_session_total_input_tokens"] = \
            result.get("_session_total_input_tokens", 0) + turn_metrics["total_input_tokens"]
        result["_session_total_output_tokens"] = \
            result.get("_session_total_output_tokens", 0) + turn_metrics["total_output_tokens"]
        result["_session_total_llm_calls"] = \
            result.get("_session_total_llm_calls", 0) + turn_metrics["llm_calls"]
        result["_session_total_latency_ms"] = \
            result.get("_session_total_latency_ms", 0) + turn_metrics["total_latency_ms"]

        bot_reply = (result.get("last_bot_message") or "").strip()
        if bot_reply:
            result["messages"].append({"role": "assistant", "content": bot_reply})
            st.session_state.chat_display.append(
                {"role": "assistant", "content": bot_reply}
            )

        # Show followup message as a separate bubble (e.g., app download prompt)
        followup = (result.get("followup_message") or "").strip()
        if followup:
            result["messages"].append({"role": "assistant", "content": followup})
            st.session_state.chat_display.append(
                {"role": "assistant", "content": followup}
            )
            result["followup_message"] = None

    except Exception as e:
        err_msg = f"An error occurred: {str(e)}"
        st.session_state.chat_display.append({"role": "assistant", "content": err_msg})

    finally:
        st.session_state.is_typing = False

    st.rerun()
