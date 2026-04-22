"""
Saudi dialect scenario runner for the Andalusia booking bot.

Drives `compiled_graph.invoke()` turn-by-turn with scripted user messages
and writes transcripts + final state to tests/transcripts/<scenario>.md.

Run from repo root:
    python -m tests.run_scenarios
"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime
from pathlib import Path

# Force UTF-8 on Windows consoles that default to cp1252
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load env BEFORE importing anything that reads settings
from dotenv import load_dotenv
load_dotenv()

# Make repo root importable when run as script
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from state import initial_state
from graph import compiled_graph
from llm.client import reset_turn_metrics, get_turn_metrics


_OUTPUT_SUBDIR = os.getenv("OUTPUT_SUBDIR", "").strip()
TRANSCRIPTS_DIR = REPO_ROOT / "tests" / "transcripts"
if _OUTPUT_SUBDIR:
    TRANSCRIPTS_DIR = TRANSCRIPTS_DIR / _OUTPUT_SUBDIR
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Scenarios ────────────────────────────────────────────────────────────────
# Each scenario is a list of user messages. They're pre-scripted to cover a
# realistic Saudi-dialect booking flow. If the bot asks an unexpected question,
# we continue with the next scripted message — that surfaces failure modes.

SCENARIOS = {
    "A1_symptom_cardiology": {
        "description": "Path A: symptoms (chest pain + cough) → cardiology routing",
        "messages": [
            "السلام عليكم",
            "فهد العتيبي",
            "٣٥ سنة",
            "عندي وجع في صدري ومعاي كحه من كم يوم",
            "أيوه صح",
            "بكرا",
            "المغرب",
            "تمام مناسب",
            "0501234567",
            "أيوه عندي تأمين",
            "بوبا",
        ],
    },
    "A2_vague_symptom": {
        "description": "Path A: vague complaint requiring clarification",
        "messages": [
            "هلا",
            "نورة القحطاني",
            "٢٨",
            "تعبانة شوي وما أدري وش فيني",
            "صداع ودوخه",
            "أيوه",
            "اليوم",
            "بعد المغرب",
            "يصير",
            "0555123456",
            "لا ما عندي",
        ],
    },
    "B1_named_specialty_eye": {
        "description": "Path B: patient names specialty directly (ophthalmology)",
        "messages": [
            "مساء الخير",
            "سعود الدوسري",
            "٤٠",
            "أبغى أحجز عند دكتور عيون",
            "أيوه",
            "بكرا ان شاء الله",
            "بعد العصر",
            "مناسب",
            "0533445566",
            "ايوه",
            "تعاونية",
        ],
    },
    "B2_dental_urgent": {
        "description": "Path B: dental with urgency + Saudi slang",
        "messages": [
            "السلام عليكم",
            "ريم الشمري",
            "٢٢",
            "أبي كشف أسنان ضروري اليوم",
            "ايوه",
            "اليوم",
            "٨ مساء",
            "تمام",
            "0566778899",
            "لا ما عندي تأمين",
        ],
    },
    "C1_named_doctor": {
        "description": "Path C: patient names doctor directly with preferred time",
        "messages": [
            "هلا وغلا",
            "عبدالعزيز الحربي",
            "٥٠",
            "أبغى موعد مع د. ولاء السعدني بكرا المغرب",
            "تمام",
            "0544332211",
            "أيوه",
            "مدغلف",
        ],
    },
    "C2_change_mind": {
        "description": "Patient changes mind mid-flow (switch specialty)",
        "messages": [
            "هلا",
            "منال العتيبي",
            "٣٢",
            "أبغى دكتور جلدية",
            "لا استني، خليها باطنة أحسن",
            "ايوه",
            "بكرا",
            "الصبح",
            "مناسب",
            "0577112233",
            "أيوه",
            "اكسا",
        ],
    },
}


# ── Runner ───────────────────────────────────────────────────────────────────

def _fmt_state_snapshot(state: dict) -> str:
    """Return the key state fields for the transcript footer."""
    keys = [
        "language", "booking_stage", "intent", "patient_name", "patient_age",
        "complaint_text", "speciality", "speciality_ar", "specialty_confirmed",
        "doctor", "doctor_ar", "requested_date", "preferred_time",
        "selected_slot", "phone", "insured", "insurance_company",
    ]
    lines = []
    for k in keys:
        v = state.get(k)
        if v not in (None, "", []):
            lines.append(f"- **{k}**: `{v}`")
    return "\n".join(lines)


def run_scenario(name: str, description: str, user_messages: list[str]) -> dict:
    """Drive the graph with scripted messages. Returns a result summary."""
    print(f"\n{'=' * 70}")
    print(f"▶ {name}")
    print(f"  {description}")
    print(f"{'=' * 70}")

    state = initial_state()
    transcript_lines = [
        f"# Scenario: {name}",
        "",
        f"**Description:** {description}",
        f"**Run at:** {datetime.now().isoformat(timespec='seconds')}",
        "",
        "---",
        "",
    ]

    errors = []
    total_tokens_in = 0
    total_tokens_out = 0
    total_latency = 0
    total_calls = 0

    for turn_idx, user_msg in enumerate(user_messages, start=1):
        transcript_lines.append(f"### Turn {turn_idx}")
        transcript_lines.append("")
        transcript_lines.append(f"**User:** {user_msg}")
        transcript_lines.append("")

        print(f"  [T{turn_idx:02d}] USER: {user_msg}")

        state["messages"].append({"role": "user", "content": user_msg})
        reset_turn_metrics()

        try:
            result = compiled_graph.invoke(state)
            state = result
        except Exception as e:
            err = f"EXCEPTION: {type(e).__name__}: {e}"
            errors.append(f"turn {turn_idx}: {err}")
            transcript_lines.append(f"**Bot:** _{err}_")
            transcript_lines.append("")
            print(f"         ✗ {err}")
            break

        # Accumulate metrics
        tm = get_turn_metrics()
        total_tokens_in += tm.get("total_input_tokens", 0)
        total_tokens_out += tm.get("total_output_tokens", 0)
        total_latency += tm.get("total_latency_ms", 0)
        total_calls += tm.get("llm_calls", 0)

        bot_reply = (state.get("last_bot_message") or "").strip()
        followup = (state.get("followup_message") or "").strip()

        if bot_reply:
            state["messages"].append({"role": "assistant", "content": bot_reply})
            transcript_lines.append(f"**Bot:** {bot_reply}")
            transcript_lines.append("")
            print(f"         BOT : {bot_reply[:120]}{'...' if len(bot_reply) > 120 else ''}")
        else:
            transcript_lines.append("**Bot:** _(empty reply)_")
            transcript_lines.append("")
            errors.append(f"turn {turn_idx}: empty bot reply")
            print("         ✗ empty reply")

        if followup:
            state["messages"].append({"role": "assistant", "content": followup})
            transcript_lines.append(f"**Bot (followup):** {followup}")
            transcript_lines.append("")
            state["followup_message"] = None

        # Stage tracking for sanity
        stage = state.get("booking_stage")
        transcript_lines.append(f"_stage: `{stage}`_")
        transcript_lines.append("")

        # Early exit if complete
        if stage == "complete" and turn_idx < len(user_messages):
            transcript_lines.append(
                f"_Booking marked complete at turn {turn_idx}. "
                f"Remaining scripted messages skipped._"
            )
            transcript_lines.append("")
            break

    # Footer: final state
    transcript_lines.append("---")
    transcript_lines.append("")
    transcript_lines.append("## Final State")
    transcript_lines.append("")
    transcript_lines.append(_fmt_state_snapshot(state))
    transcript_lines.append("")
    transcript_lines.append("## Metrics")
    transcript_lines.append("")
    transcript_lines.append(f"- **LLM calls:** {total_calls}")
    transcript_lines.append(
        f"- **Tokens:** {total_tokens_in + total_tokens_out:,} "
        f"(in: {total_tokens_in:,} / out: {total_tokens_out:,})"
    )
    transcript_lines.append(f"- **Total latency:** {total_latency:,} ms")
    transcript_lines.append(f"- **Errors:** {len(errors)}")
    if errors:
        transcript_lines.append("")
        for e in errors:
            transcript_lines.append(f"  - {e}")
    transcript_lines.append("")

    out_path = TRANSCRIPTS_DIR / f"{name}.md"
    out_path.write_text("\n".join(transcript_lines), encoding="utf-8")
    print(f"  → saved: {out_path.relative_to(REPO_ROOT)}")

    return {
        "name": name,
        "description": description,
        "final_stage": state.get("booking_stage"),
        "selected_slot": state.get("selected_slot"),
        "doctor": state.get("doctor") or state.get("doctor_ar"),
        "speciality": state.get("speciality"),
        "phone": state.get("phone"),
        "insured": state.get("insured"),
        "insurance_company": state.get("insurance_company"),
        "errors": errors,
        "total_turns_run": turn_idx,
        "total_calls": total_calls,
        "total_tokens": total_tokens_in + total_tokens_out,
        "transcript_path": str(out_path.relative_to(REPO_ROOT)),
    }


def main():
    # Pick which scenarios to run (all by default; env var allows filtering)
    filter_name = os.getenv("SCENARIO_FILTER", "").strip()
    if filter_name:
        to_run = {k: v for k, v in SCENARIOS.items() if filter_name in k}
        if not to_run:
            print(f"No scenarios match filter '{filter_name}'")
            return
    else:
        to_run = SCENARIOS

    results = []
    for name, cfg in to_run.items():
        try:
            results.append(run_scenario(name, cfg["description"], cfg["messages"]))
        except Exception as e:
            print(f"  FATAL during {name}: {e}")
            results.append({
                "name": name,
                "description": cfg["description"],
                "fatal_error": str(e),
            })

    # Summary JSON for scoring step (placed alongside transcripts)
    summary_path = TRANSCRIPTS_DIR / "run_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'=' * 70}")
    print(f"Summary written: {summary_path.relative_to(REPO_ROOT)}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
