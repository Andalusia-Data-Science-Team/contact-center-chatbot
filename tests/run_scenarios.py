"""
End-to-end scenario runner for the Andalusia booking bot.

Drives `compiled_graph.invoke()` turn-by-turn with scripted user messages.
Writes:
  - tests/transcripts/<subdir>/<scenario>.md        (per-scenario transcript)
  - tests/transcripts/<subdir>/run_summary.json     (machine-readable summary)
  - tests/transcripts/<subdir>/run_log.txt          (consolidated chronological log)
  - tests/transcripts/<subdir>/summary_table.md     (at-a-glance table)

Run from repo root:
    python -m tests.run_scenarios
    SCENARIO_FILTER=nutrition python -m tests.run_scenarios
    OUTPUT_SUBDIR=2026-04-23 python -m tests.run_scenarios
"""
from __future__ import annotations

import os
import sys
import json
import time
import traceback
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
if not _OUTPUT_SUBDIR:
    _OUTPUT_SUBDIR = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
TRANSCRIPTS_DIR = REPO_ROOT / "tests" / "transcripts" / _OUTPUT_SUBDIR
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIOS
# Each scenario is a scripted patient conversation. If the bot asks something
# unexpected, the runner continues with the next scripted message anyway — that
# surfaces failure modes (we'll see the divergence in the transcript).
#
# Coverage targets: every major specialty + the recently-fixed edge cases.
# ══════════════════════════════════════════════════════════════════════════════

SCENARIOS = {
    # ── PATH A: Symptoms → routing ──────────────────────────────────────────
    "A1_cardiology_chest_pain": {
        "description": "Chest pain + cough → Cardiology/Chest",
        "tags": ["path-a", "cardiology"],
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
    "A2_neurology_vague_headache": {
        "description": "Vague complaint → صداع/دوخة → Neurology",
        "tags": ["path-a", "neurology", "vague"],
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
    "A3_neurology_typo_sade3": {
        "description": "TYPO TEST: 'صدع' (misspelling of صداع) → Neurology",
        "tags": ["path-a", "neurology", "typo", "ar-routing-fix"],
        "messages": [
            "السلام عليكم",
            "محمد السبيعي",
            "٤٢",
            "عندي صدع مزمن من اسبوع",
            "ايوه",
            "بكرا",
            "العصر",
            "مناسب",
            "0501111222",
            "لا كاش",
        ],
    },
    "A4_nutrition_typo_taghzia": {
        "description": "TYPO TEST: 'تغزية' (misspelling of تغذية) → Nutrition",
        "tags": ["path-a", "nutrition", "typo", "ar-routing-fix"],
        "messages": [
            "مساء الخير",
            "ريم الحربي",
            "٣٠",
            "عايزه احجز تغزية",
            "ايوه",
            "بكرا",
            "الصبح",
            "تمام",
            "0502223344",
            "لا ما معي تأمين",
        ],
    },
    "A5_orthopedics_knee_pain": {
        "description": "Knee + back pain → Orthopedics",
        "tags": ["path-a", "orthopedics"],
        "messages": [
            "هلا وغلا",
            "خالد الزهراني",
            "٥٥",
            "وجع في ركبتي وظهري من شهر",
            "نعم",
            "اليوم",
            "بعد العصر",
            "مناسب",
            "0503334455",
            "أيوه",
            "التعاونية",
        ],
    },
    "A6_dermatology_rash": {
        "description": "Skin rash + itching → Dermatology",
        "tags": ["path-a", "dermatology"],
        "messages": [
            "السلام عليكم",
            "سارة الدوسري",
            "٢٤",
            "عندي طفح جلدي وحكه في يدي",
            "ايوه",
            "بكرا",
            "المساء",
            "يناسبني",
            "0504445566",
            "لا",
        ],
    },
    "A7_ent_ear_pain": {
        "description": "Ear pain + hearing issue → ENT",
        "tags": ["path-a", "ent"],
        "messages": [
            "مرحبا",
            "ماجد القرني",
            "٣٨",
            "أذني تألمني وما اسمع كويس",
            "نعم",
            "اليوم",
            "بعد المغرب",
            "تمام",
            "0505556677",
            "ايوه",
            "ميدجلف",
        ],
    },
    "A8_gi_stomach_pain": {
        "description": "Stomach pain + nausea → Internal Medicine",
        "tags": ["path-a", "gi", "internal-medicine"],
        "messages": [
            "السلام عليكم",
            "عبدالله الغامدي",
            "٤٥",
            "عندي وجع في معدتي وغثيان من يومين",
            "ايوه",
            "بكرا",
            "الظهر",
            "مناسب",
            "0506667788",
            "لا كاش",
        ],
    },
    "A9_obgyn_pregnancy": {
        "description": "Pregnancy check → OBE & GYN",
        "tags": ["path-a", "obgyn"],
        "messages": [
            "مساء الخير",
            "منيرة الشهري",
            "٣٢",
            "حامل في الشهر الخامس وابغى متابعة",
            "نعم",
            "بكرا",
            "بعد العصر",
            "تمام",
            "0507778899",
            "أيوه",
            "التعاونية",
        ],
    },
    "A10_pediatrics_child_fever": {
        "description": "Child with fever → General Pediatrics",
        "tags": ["path-a", "pediatrics"],
        "messages": [
            "السلام عليكم",
            "أم أحمد",
            "٦ سنين ولدي",
            "ولدي عنده حراره من امس",
            "ايوه",
            "اليوم",
            "بعد المغرب",
            "مناسب",
            "0508889900",
            "لا",
        ],
    },
    "A11_urology_kidney_stone": {
        "description": "Kidney stone pain → Urology",
        "tags": ["path-a", "urology"],
        "messages": [
            "هلا",
            "يوسف المطيري",
            "٥٠",
            "عندي الم شديد في خاصرتي ويمكن حصوة",
            "نعم",
            "بكرا",
            "الظهر",
            "يصير",
            "0509990011",
            "أيوه",
            "بوبا",
        ],
    },
    "A12_psychiatry_anxiety": {
        "description": "Anxiety + sleep issues → Psychiatry",
        "tags": ["path-a", "psychiatry"],
        "messages": [
            "السلام عليكم",
            "لطيفة العنزي",
            "٢٧",
            "عندي قلق شديد وما اقدر انام",
            "نعم",
            "بكرا",
            "بعد العصر",
            "تمام",
            "0501112233",
            "لا ما عندي تأمين",
        ],
    },

    # ── PATH B: Named specialty ─────────────────────────────────────────────
    "B1_ophthalmology_direct": {
        "description": "Named specialty: eye doctor",
        "tags": ["path-b", "ophthalmology"],
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
        "description": "Named specialty: dental, urgent",
        "tags": ["path-b", "dental"],
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
    "B3_ivf_hqn_mjhry": {
        "description": "KEYWORD TEST: 'حقن مجهري' → IVF (not injection)",
        "tags": ["path-b", "ivf", "ar-routing-fix"],
        "messages": [
            "هلا",
            "هدى القحطاني",
            "٣٥",
            "ابغى دكتور حقن مجهري",
            "ايوه",
            "بكرا",
            "الصبح",
            "تمام",
            "0510002233",
            "أيوه",
            "التعاونية",
        ],
    },
    "B4_endodontic_nerve_tooth": {
        "description": "KEYWORD TEST: 'عصب اسنان' → Endodontic",
        "tags": ["path-b", "endodontic", "ar-routing-fix"],
        "messages": [
            "السلام عليكم",
            "بندر العتيبي",
            "٣٣",
            "محتاج دكتور عصب اسنان",
            "ايوه",
            "اليوم",
            "المساء",
            "مناسب",
            "0511113344",
            "لا كاش",
        ],
    },
    "B5_endocrinology_diabetes": {
        "description": "Diabetes follow-up → Endocrinology",
        "tags": ["path-b", "endocrinology"],
        "messages": [
            "مرحبا",
            "فاطمة السلمي",
            "٦٠",
            "عندي سكري ومحتاجة متابعة",
            "نعم",
            "بكرا",
            "الصبح",
            "تمام",
            "0512224455",
            "أيوه",
            "بوبا",
        ],
    },

    # ── PATH C: Named doctor ────────────────────────────────────────────────
    "C1_named_doctor_direct": {
        "description": "Named doctor directly: Dr Walaa",
        "tags": ["path-c", "named-doctor"],
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
    "C2_switch_specialty_mid_flow": {
        "description": "Patient switches specialty mid-conversation",
        "tags": ["path-c", "switch-mind"],
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

    # ── EDGE CASES: previously-broken flows ─────────────────────────────────
    "EDGE1_inline_cash_phone": {
        "description": "Phone + cash in one message (multi-intent): '0501112233 - كاش'",
        "tags": ["edge", "multi-intent", "insurance"],
        "messages": [
            "السلام عليكم",
            "أحمد الزهراني",
            "٣٧",
            "ابي كشف باطنة",
            "ايوه",
            "بكرا",
            "الظهر",
            "تمام",
            "09183183 - كاش وعايز اعرف سعر الكشف",
        ],
    },
    "EDGE2_price_inquiry_during_slot": {
        "description": "Price question bundled with slot accept: 'تمام الساعة 8 - كم سعر الكشف؟'",
        "tags": ["edge", "price-inquiry", "slot-confirm"],
        "messages": [
            "السلام عليكم",
            "خالد الشمري",
            "٢٩",
            "ابغى دكتور اسنان",
            "ايوه",
            "اليوم",
            "تمام الساعة 8 - كم سعر الكشف؟",
            "كاش",
            "0513335566",
        ],
    },
    "EDGE3_arabic_pm_suffix": {
        "description": "Arabic AM/PM suffix: '٨ مساء' should select 8 PM specifically",
        "tags": ["edge", "time-parsing"],
        "messages": [
            "هلا",
            "نايف المالكي",
            "٤٥",
            "ابي كشف عيون",
            "ايوه",
            "بكرا",
            "٨ مساء",
            "تمام",
            "0514446677",
            "لا كاش",
        ],
    },
    "EDGE4_named_doctor_then_price": {
        "description": "Named doctor + price inquiry in same turn",
        "tags": ["edge", "path-c", "price-inquiry"],
        "messages": [
            "مساء الخير",
            "أسامة القرني",
            "٣٦",
            "ابغى موعد مع د. ولاء وكم سعر الكشف",
            "كاش",
            "بكرا",
            "المغرب",
            "مناسب",
            "0515557788",
        ],
    },
    "EDGE5_digit_during_slot_proposal": {
        "description": "Digit reply ('8') during proposal should NOT be treated as acceptance",
        "tags": ["edge", "slot-confirm"],
        "messages": [
            "السلام عليكم",
            "سلطان العتيبي",
            "٥٢",
            "ابي كشف باطنة",
            "ايوه",
            "اليوم",
            "8",
            "تمام",
            "0516668899",
            "أيوه",
            "بوبا",
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════

_TRACKED_FIELDS = [
    "language", "booking_stage", "intent", "patient_name", "patient_age",
    "complaint_text", "speciality", "speciality_ar", "specialty_confirmed",
    "doctor", "doctor_ar", "requested_date", "preferred_time",
    "selected_slot", "phone", "insured", "insurance_company", "walk_in_price",
]


def _state_snapshot(state: dict) -> dict:
    return {k: state.get(k) for k in _TRACKED_FIELDS if state.get(k) not in (None, "", [])}


def _diff_snapshot(prev: dict, curr: dict) -> dict:
    """Return only keys that changed between two snapshots."""
    diff = {}
    for k in set(prev) | set(curr):
        if prev.get(k) != curr.get(k):
            diff[k] = {"from": prev.get(k), "to": curr.get(k)}
    return diff


def _fmt_state_md(snapshot: dict) -> str:
    if not snapshot:
        return "_(empty)_"
    return "\n".join(f"- **{k}**: `{v}`" for k, v in snapshot.items())


def _fmt_diff_md(diff: dict) -> str:
    if not diff:
        return "_(no change)_"
    lines = []
    for k, change in diff.items():
        lines.append(f"- `{k}`: `{change['from']}` → `{change['to']}`")
    return "\n".join(lines)


def run_scenario(name: str, description: str, tags: list,
                 user_messages: list, run_log) -> dict:
    """Drive the graph with scripted messages. Returns a result summary."""
    header = f"\n{'=' * 78}\n▶ {name}  [{', '.join(tags)}]\n  {description}\n{'=' * 78}"
    print(header)
    run_log.write(header + "\n")

    state = initial_state()
    transcript_lines = [
        f"# Scenario: {name}",
        "",
        f"**Tags:** {', '.join(tags)}  ",
        f"**Description:** {description}  ",
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
    turn_idx = 0
    prev_snapshot = {}

    scenario_started = time.time()

    for turn_idx, user_msg in enumerate(user_messages, start=1):
        transcript_lines.append(f"### Turn {turn_idx}")
        transcript_lines.append("")
        transcript_lines.append(f"**User:** {user_msg}")
        transcript_lines.append("")

        turn_line = f"  [T{turn_idx:02d}] USER: {user_msg}"
        print(turn_line)
        run_log.write(turn_line + "\n")

        state["messages"].append({"role": "user", "content": user_msg})
        reset_turn_metrics()

        t_start = time.time()
        try:
            result = compiled_graph.invoke(state)
            state = result
        except Exception as e:
            err = f"EXCEPTION: {type(e).__name__}: {e}"
            tb = traceback.format_exc()
            errors.append(f"turn {turn_idx}: {err}")
            transcript_lines.append(f"**Bot:** _{err}_")
            transcript_lines.append("")
            transcript_lines.append("```")
            transcript_lines.append(tb)
            transcript_lines.append("```")
            transcript_lines.append("")
            msg = f"         ✗ {err}"
            print(msg)
            run_log.write(msg + "\n" + tb + "\n")
            break
        t_elapsed = int((time.time() - t_start) * 1000)

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
            preview = bot_reply[:140] + ("..." if len(bot_reply) > 140 else "")
            msg = f"         BOT : {preview}"
            print(msg)
            run_log.write(msg + "\n")
        else:
            transcript_lines.append("**Bot:** _(empty reply)_")
            transcript_lines.append("")
            errors.append(f"turn {turn_idx}: empty bot reply")
            print("         ✗ empty reply")
            run_log.write("         ✗ empty reply\n")

        if followup:
            state["messages"].append({"role": "assistant", "content": followup})
            transcript_lines.append(f"**Bot (followup):** {followup}")
            transcript_lines.append("")
            state["followup_message"] = None

        # State diff for this turn
        curr_snapshot = _state_snapshot(state)
        diff = _diff_snapshot(prev_snapshot, curr_snapshot)
        prev_snapshot = curr_snapshot
        transcript_lines.append("**State Δ:**")
        transcript_lines.append("")
        transcript_lines.append(_fmt_diff_md(diff))
        transcript_lines.append("")
        transcript_lines.append(
            f"_stage: `{state.get('booking_stage')}` · "
            f"{tm.get('llm_calls', 0)} LLM calls · "
            f"{tm.get('total_input_tokens', 0) + tm.get('total_output_tokens', 0)} tokens · "
            f"{t_elapsed}ms_"
        )
        transcript_lines.append("")

        stage = state.get("booking_stage")
        if stage == "complete" and turn_idx < len(user_messages):
            transcript_lines.append(
                f"_Booking marked complete at turn {turn_idx}. "
                f"Remaining scripted messages skipped._"
            )
            transcript_lines.append("")
            break

    scenario_duration = int(time.time() - scenario_started)

    # Footer
    transcript_lines += [
        "---",
        "",
        "## Final State",
        "",
        _fmt_state_md(_state_snapshot(state)),
        "",
        "## Metrics",
        "",
        f"- **Turns run:** {turn_idx}",
        f"- **LLM calls:** {total_calls}",
        f"- **Tokens:** {total_tokens_in + total_tokens_out:,} "
        f"(in: {total_tokens_in:,} / out: {total_tokens_out:,})",
        f"- **Total LLM latency:** {total_latency:,} ms",
        f"- **Wall time:** {scenario_duration}s",
        f"- **Errors:** {len(errors)}",
    ]
    if errors:
        transcript_lines.append("")
        for e in errors:
            transcript_lines.append(f"  - {e}")
    transcript_lines.append("")

    out_path = TRANSCRIPTS_DIR / f"{name}.md"
    out_path.write_text("\n".join(transcript_lines), encoding="utf-8")
    saved_msg = f"  → saved: {out_path.relative_to(REPO_ROOT)}"
    print(saved_msg)
    run_log.write(saved_msg + "\n")

    return {
        "name": name,
        "description": description,
        "tags": tags,
        "final_stage": state.get("booking_stage"),
        "speciality": state.get("speciality"),
        "doctor": state.get("doctor") or state.get("doctor_ar"),
        "selected_slot": state.get("selected_slot"),
        "phone": state.get("phone"),
        "insured": state.get("insured"),
        "insurance_company": state.get("insurance_company"),
        "errors": errors,
        "turns_run": turn_idx,
        "total_calls": total_calls,
        "total_tokens": total_tokens_in + total_tokens_out,
        "wall_seconds": scenario_duration,
        "transcript_path": str(out_path.relative_to(REPO_ROOT)),
    }


def _write_summary_table(results: list, path: Path) -> None:
    rows = [
        "# Scenario Run Summary",
        "",
        f"Run at: {datetime.now().isoformat(timespec='seconds')}  ",
        f"Total scenarios: {len(results)}",
        "",
        "| Scenario | Tags | Final Stage | Speciality | Doctor | Slot | Phone | Insured | Errors |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("fatal_error"):
            rows.append(f"| {r['name']} | - | ❌ FATAL | - | - | - | - | - | {r['fatal_error'][:80]} |")
            continue
        ins = "cash" if r.get("insured") is False else (
            r.get("insurance_company") or "?") if r.get("insured") else "-"
        err_mark = f"⚠️ {len(r['errors'])}" if r.get("errors") else "✓"
        rows.append(
            f"| `{r['name']}` "
            f"| {', '.join(r.get('tags', []))} "
            f"| `{r.get('final_stage') or '-'}` "
            f"| {r.get('speciality') or '-'} "
            f"| {r.get('doctor') or '-'} "
            f"| {r.get('selected_slot') or '-'} "
            f"| {r.get('phone') or '-'} "
            f"| {ins} "
            f"| {err_mark} |"
        )

    # Aggregate
    total_tokens = sum(r.get("total_tokens", 0) for r in results)
    total_calls = sum(r.get("total_calls", 0) for r in results)
    total_wall = sum(r.get("wall_seconds", 0) for r in results)
    completed = sum(1 for r in results if r.get("final_stage") == "complete")
    errored = sum(1 for r in results if r.get("errors"))

    rows += [
        "",
        "## Aggregate",
        "",
        f"- **Scenarios completed (final_stage=complete):** {completed}/{len(results)}",
        f"- **Scenarios with turn-level errors:** {errored}",
        f"- **Total LLM calls:** {total_calls:,}",
        f"- **Total tokens:** {total_tokens:,}",
        f"- **Total wall time:** {total_wall}s",
    ]
    path.write_text("\n".join(rows), encoding="utf-8")


def main():
    filter_name = os.getenv("SCENARIO_FILTER", "").strip().lower()
    if filter_name:
        to_run = {
            k: v for k, v in SCENARIOS.items()
            if filter_name in k.lower()
            or filter_name in v["description"].lower()
            or any(filter_name in t for t in v.get("tags", []))
        }
        if not to_run:
            print(f"No scenarios match filter '{filter_name}'")
            return
    else:
        to_run = SCENARIOS

    run_log_path = TRANSCRIPTS_DIR / "run_log.txt"
    header = (
        f"Scenario run started: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Output dir: {TRANSCRIPTS_DIR.relative_to(REPO_ROOT)}\n"
        f"Scenarios to run: {len(to_run)}\n"
    )
    print(header)

    results = []
    with open(run_log_path, "w", encoding="utf-8") as run_log:
        run_log.write(header + "\n")
        overall_start = time.time()
        for name, cfg in to_run.items():
            try:
                results.append(run_scenario(
                    name, cfg["description"], cfg.get("tags", []),
                    cfg["messages"], run_log,
                ))
            except Exception as e:
                err = f"  FATAL during {name}: {type(e).__name__}: {e}"
                print(err)
                run_log.write(err + "\n" + traceback.format_exc() + "\n")
                results.append({
                    "name": name,
                    "description": cfg["description"],
                    "tags": cfg.get("tags", []),
                    "fatal_error": str(e),
                })
        overall_duration = int(time.time() - overall_start)
        run_log.write(f"\nAll scenarios finished in {overall_duration}s\n")

    summary_json = TRANSCRIPTS_DIR / "run_summary.json"
    summary_json.write_text(
        json.dumps(results, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    summary_table = TRANSCRIPTS_DIR / "summary_table.md"
    _write_summary_table(results, summary_table)

    print(f"\n{'=' * 78}")
    print(f"Summary JSON   : {summary_json.relative_to(REPO_ROOT)}")
    print(f"Summary table  : {summary_table.relative_to(REPO_ROOT)}")
    print(f"Run log        : {run_log_path.relative_to(REPO_ROOT)}")
    print(f"Transcripts    : {TRANSCRIPTS_DIR.relative_to(REPO_ROOT)}")
    print(f"{'=' * 78}")


if __name__ == "__main__":
    main()
