"""
Integration tests for the issue fixes (#1-#8 from the recent session).

These tests stub the DB layer so they run without a live SQL Server, then
exercise the real node code paths to verify each fix actually triggers in
the conversation pipeline.

Run with: python scripts/test_issue_fixes.py
Exits non-zero on first failure.
"""
import sys, io, os, datetime
from datetime import date, time, timedelta

# Force UTF-8 console so Arabic prints don't crash on Windows cp1252.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Ensure project root is on sys.path when run from scripts/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── tiny test helpers ────────────────────────────────────────────────────────

PASSED = []
FAILED = []


def section(title):
    print(f"\n=== {title} ===")


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append((name, detail))
        print(f"  ✗ {name}\n    {detail}")


def expect_eq(name, got, want):
    check(name, got == want, f"got {got!r}, want {want!r}")


def expect_in(name, needle, haystack):
    check(name, needle in haystack, f"{needle!r} not in {haystack!r}")


def expect_not_in(name, needle, haystack):
    check(name, needle not in haystack, f"{needle!r} unexpectedly in {haystack!r}")


# ── DB stub ─────────────────────────────────────────────────────────────────
# Each test sets _STUB_ROWS to control what the DB calls return.

import db.database as _db

_STUB_AVAILABILITY = []   # rows for query_availability
_STUB_SLOTS = {}          # {(report_date, doctor_en, doctor_ar): [slot_rows]}


def _stub_query_availability(report_date, specialty_en=None, specialty_ar=None,
                             doctor_en=None, doctor_ar=None):
    out = []
    for row in _STUB_AVAILABILITY:
        if row["Slot_Date"].strftime("%Y-%m-%d") != report_date:
            continue
        if specialty_en and row.get("Specialty") != specialty_en:
            continue
        if specialty_ar and row.get("SpecialtyAR") != specialty_ar:
            continue
        if doctor_en and row.get("Doctor") != doctor_en:
            continue
        if doctor_ar and row.get("DoctorAR") != doctor_ar:
            continue
        out.append(row)
    return out


def _stub_query_doctor_slots(report_date, doctor_en=None, doctor_ar=None):
    return list(_STUB_SLOTS.get((report_date, doctor_en, doctor_ar), []))


_db.query_availability = _stub_query_availability
_db.query_doctor_slots = _stub_query_doctor_slots


# ── disable CRM price enrichment (network-bound) ───────────────────────────
import services.doctor_price as _dp
_dp.enrich_doctors_with_prices = lambda doctors: None
_dp.get_walk_in_price = lambda doc_en: None


def _row(physician_id, doc_en, doc_ar, spec, spec_ar, slot_date, slot_time):
    return {
        "PhysicianID": physician_id,
        "Doctor": doc_en,
        "DoctorAR": doc_ar,
        "Specialty": spec,
        "SpecialtyAR": spec_ar,
        "Slot_Date": slot_date,
        "Slot_Time": slot_time.strftime("%H:%M:%S") if hasattr(slot_time, "strftime") else slot_time,
        "DaysFromToday": (slot_date - date.today()).days,
        "Avg Slot Duration (Min.)": 15,
        "plannedslot": 12,
        "OverbookedSlots": 0,
        "PlannedSlots_without_overbooking": 12,
        "ActualBookedSlots": 0,
        "WorkDate": slot_date,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Issue 1 — Dialect sanitizer
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 1 — dialect sanitizer")

from utils.language import sanitize_dialect

cases = [
    ("ازاي أقدر أساعدك؟", "ar", "كيف"),
    ("عايز اطفال غدد", "ar", "أبغى"),
    ("هكلمك بكرا", "ar", "راح"),
    ("هنتواصل معاك دلوقتي", "ar", "الحين"),
    ("مش مشكلة", "ar", "مو"),
    ("فين الدكتور؟", "ar", "وين"),
    ("أيوه تمام", "ar", "إي"),
]
for txt, lang, expect in cases:
    out = sanitize_dialect(txt, lang)
    expect_in(f"sanitize {txt!r} contains {expect!r}", expect, out)

# Banned tokens fully removed
banned = ["ازاي", "إزاي", "عايز", "عاوز", "هكلمك", "هنتواصل", "دلوقتي", "مش", "فين", "أيوه", "بكرا", "كده", "حلو", "كويس"]
combined = "ازاي عايز هكلمك دلوقتي مش فين أيوه بكرا كده حلو كويس"
out = sanitize_dialect(combined, "ar")
for tok in banned:
    if tok in combined:
        expect_not_in(f"banned {tok!r} removed", tok, out)

# English untouched
out = sanitize_dialect("Hello, how can I help?", "en")
expect_eq("English unchanged", out, "Hello, how can I help?")


# ─────────────────────────────────────────────────────────────────────────────
# Issue 2 — Pediatric subspecialty routing (prefilter only — no LLM call)
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 2 — pediatric subspecialty routing")

from services.router import _keyword_prefilter

cands = _keyword_prefilter("ابغى اطفال غدد", "ar", None)
expect_in("اطفال غدد routes to Pediatric Endocrinology", "Pediatric Endocrinology", cands)
expect_not_in("adult Endocrinology dropped", "Endocrinology", cands)

cands = _keyword_prefilter("اطفال قلب", "ar", None)
expect_in("اطفال قلب routes to Pediatric Cardiology", "Pediatric Cardiology", cands)
expect_not_in("adult Cardiology dropped", "Cardiology", cands)

cands = _keyword_prefilter("اطفال", "ar", None)
expect_in("bare اطفال yields General Pediatrics", "General Pediatrics", cands)

cands = _keyword_prefilter("غدد صماء", "ar", 30)
expect_in("adult غدد still routes to Endocrinology", "Endocrinology", cands)
expect_not_in("no pediatric bias for adult", "Pediatric Endocrinology", cands)

cands = _keyword_prefilter("pediatric heart", "en", None)
expect_in("English 'pediatric heart' → Pediatric Cardiology", "Pediatric Cardiology", cands)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 3 — apply_llm_updates guards complaint_text against acceptance echoes
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 3 — complaint_text protection during specialty confirmation")

from nodes._helpers import apply_llm_updates

state = {
    "speciality": "Endocrinology",
    "specialty_confirmed": False,
    "complaint_text": "اطفال غدد",
    "messages": [{"role": "user", "content": "اه"}],
}
apply_llm_updates(state, {"complaint_text": "اه", "booking_stage": "routing"})
expect_eq("complaint_text frozen on bare 'اه'", state["complaint_text"], "اطفال غدد")

# A genuine new complaint should still update (no specialty yet, or longer text)
state2 = {"speciality": None, "complaint_text": "old", "messages": [{"role": "user", "content": "ابغى عيون"}]}
apply_llm_updates(state2, {"complaint_text": "ابغى عيون"})
expect_eq("complaint_text updates when no specialty set", state2["complaint_text"], "ابغى عيون")

# Long messages still update even with specialty set (not a bare acceptance)
state3 = {
    "speciality": "Endocrinology",
    "complaint_text": "اطفال غدد",
    "messages": [{"role": "user", "content": "بدلا من كده ابغى دكتور قلب"}],
}
apply_llm_updates(state3, {"complaint_text": "ابغى دكتور قلب"})
expect_eq("non-acceptance still updates complaint_text", state3["complaint_text"], "ابغى دكتور قلب")


# ─────────────────────────────────────────────────────────────────────────────
# Issue 5 — "who is available today" detector + reshow path
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 5 — who-is-available re-list")

from nodes.slot_selection import _is_who_is_available_request, slot_selection_node

positive = [
    "مين متاح اليوم", "مين متاح", "مين عنده موعد اليوم",
    "ايش الاطباء المتاحين", "اطباء ثانيين", "دكتور ثاني",
    "who is available today", "any other doctor", "show me the other doctors",
    "different doctor please",
]
negative = ["بعد المغرب", "yes ok", "8 pm", "اليوم", "نعم", "تمام"]
for msg in positive:
    check(f"detector positive: {msg!r}", _is_who_is_available_request(msg))
for msg in negative:
    check(f"detector negative: {msg!r}", not _is_who_is_available_request(msg))

# End-to-end: simulate slot_selection_node with a doctor selected and "مين متاح اليوم"
today = date.today()
_STUB_AVAILABILITY = [
    _row(101, "Ameera Barakat", "اميرة بركات", "Endocrinology", "أمراض الغدد",
         today, time(19, 45)),
    _row(102, "Badrya AlBeiruti", "بدرية البيروتي", "Endocrinology", "أمراض الغدد",
         today, time(11, 48)),
]
state = {
    "language": "ar",
    "booking_stage": "slot_selection",
    "doctor": "Badrya AlBeiruti",
    "doctor_ar": "بدرية البيروتي",
    "speciality": "Endocrinology",
    "available_doctors": [
        {"Doctor": "Ameera Barakat", "DoctorAR": "اميرة بركات",
         "Nearest_Date": today, "Nearest_Time": "19:45:00"},
        {"Doctor": "Badrya AlBeiruti", "DoctorAR": "بدرية البيروتي",
         "Nearest_Date": today, "Nearest_Time": "11:48:00"},
    ],
    "available_slots": [],
    "messages": [{"role": "user", "content": "مين متاح اليوم"}],
    "_llm_updates": {},
    "date": today.strftime("%Y-%m-%d"),
}
slot_selection_node(state)
check("doctor cleared after re-list request", state.get("doctor") is None)
check("booking_stage moved to doctor_list", state.get("booking_stage") == "doctor_list")
check("reply contains a doctor name", "اميرة" in (state.get("last_bot_message") or "")
      or "بدرية" in (state.get("last_bot_message") or ""))


# ─────────────────────────────────────────────────────────────────────────────
# Issue 6 — SQL CTE constraint applied
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 6 — FreeSlotsRanked bound to @ReportDate")

# Static check on the SQL string
sql = _db.SQL_QUERY
expect_in(
    "SQL_QUERY constrains FreeSlotsRanked to @ReportDate",
    "AND sl.StartDate = @ReportDate",
    sql,
)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 7 — same-day alternatives when selected doctor falls back
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 7 — same-day alternatives")

from nodes.doctor_selection import _find_same_day_alternatives, _handle_slot_fetch
from services.formatter import slot_with_alternatives_message

today_iso = today.strftime("%Y-%m-%d")
tomorrow = today + timedelta(days=1)
tomorrow_iso = tomorrow.strftime("%Y-%m-%d")

# Fast path: alternatives come from already-loaded available_doctors
state = {
    "speciality": "Endocrinology",
    "available_doctors": [
        {"Doctor": "Ameera Barakat", "DoctorAR": "اميرة بركات",
         "Nearest_Date": today, "Nearest_Time": "19:45:00"},
        {"Doctor": "Badrya AlBeiruti", "DoctorAR": "بدرية البيروتي",
         "Nearest_Date": today, "Nearest_Time": "11:48:00"},
    ],
}
alts = _find_same_day_alternatives(state, today_iso, exclude_doctor="Badrya AlBeiruti")
expect_eq("fast-path returns 1 alternative", len(alts), 1)
expect_eq("fast-path excludes the picked doctor",
          alts[0]["Doctor"], "Ameera Barakat")

# slot_with_alternatives_message renders both pieces
slot_info = {"first_date": tomorrow, "first_time": time(11, 48)}
msg = slot_with_alternatives_message(
    "Badrya AlBeiruti", "بدرية البيروتي", slot_info, alts, today_iso, "ar",
)
expect_in("AR alt message mentions selected doctor", "بدرية البيروتي", msg)
expect_in("AR alt message mentions alternative doctor", "اميرة بركات", msg)
expect_in("AR alt message says 'today' (اليوم)", "اليوم", msg)

# End-to-end: fetch_slots returns tomorrow → alternatives kick in
_STUB_SLOTS = {
    (tomorrow_iso, "Badrya AlBeiruti", "بدرية البيروتي"): [
        {"StartDate": tomorrow, "StartTime": "11:48:00"},
    ],
    # No today slots for Badrya
}
state = {
    "language": "ar",
    "doctor": "Badrya AlBeiruti",
    "doctor_ar": "بدرية البيروتي",
    "speciality": "Endocrinology",
    "available_doctors": [
        {"Doctor": "Ameera Barakat", "DoctorAR": "اميرة بركات",
         "Nearest_Date": today, "Nearest_Time": "19:45:00"},
        {"Doctor": "Badrya AlBeiruti", "DoctorAR": "بدرية البيروتي",
         "Nearest_Date": today, "Nearest_Time": "11:48:00"},
    ],
    "date": today_iso,
    "requested_date": None,
}
reply = _handle_slot_fetch(state, "ar")
expect_in("E2E reply mentions alternative", "اميرة", reply)
check("E2E sets _alternatives_offered=True", state.get("_alternatives_offered") is True)
check("E2E sets _proposed_slot for fallback",
      state.get("_proposed_slot") and state["_proposed_slot"]["date"] == tomorrow_iso)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 7-extension (mid-flow date refetch in slot_selection)
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 7-ext — mid-flow date refetch surfaces alternatives")

from nodes.slot_selection import _handle_slot_fetch as _slot_fetch

state = {
    "language": "ar",
    "doctor": "Badrya AlBeiruti",
    "doctor_ar": "بدرية البيروتي",
    "speciality": "Endocrinology",
    "available_doctors": [
        {"Doctor": "Ameera Barakat", "DoctorAR": "اميرة بركات",
         "Nearest_Date": today, "Nearest_Time": "19:45:00"},
        {"Doctor": "Badrya AlBeiruti", "DoctorAR": "بدرية البيروتي",
         "Nearest_Date": today, "Nearest_Time": "11:48:00"},
    ],
    "date": today_iso,
    "requested_date": today_iso,  # patient just asked for today
}
reply = _slot_fetch(state, "ar", preferred_date=today_iso)
expect_in("mid-flow refetch surfaces alt", "اميرة", reply)
check("mid-flow sets _alternatives_offered", state.get("_alternatives_offered") is True)
check("mid-flow sets _proposed_slot",
      state.get("_proposed_slot") and state["_proposed_slot"]["date"] == tomorrow_iso)


# ─────────────────────────────────────────────────────────────────────────────
# Issue 8 — no-slots path falls back to other-doctors-with-availability
# ─────────────────────────────────────────────────────────────────────────────
section("Issue 8 — no-slots alternatives")

from nodes.doctor_selection import _other_doctors_with_availability
from services.formatter import no_slots_with_alternatives_message

state = {
    "doctor": "Badrya AlBeiruti",
    "available_doctors": [
        {"Doctor": "Ameera Barakat", "DoctorAR": "اميرة بركات",
         "Nearest_Date": today, "Nearest_Time": "19:45:00"},
        {"Doctor": "Badrya AlBeiruti", "DoctorAR": "بدرية البيروتي",
         "Nearest_Date": today, "Nearest_Time": "11:48:00"},
    ],
}
others = _other_doctors_with_availability(state)
expect_eq("excludes selected doctor", len(others), 1)
expect_eq("returns the other one", others[0]["Doctor"], "Ameera Barakat")

msg = no_slots_with_alternatives_message(
    "Badrya AlBeiruti", "بدرية البيروتي", others, "ar",
)
expect_in("AR no-slots msg mentions alternative", "اميرة بركات", msg)

# End-to-end: fetch_slots returns nothing for the picked doctor → alternatives msg
_STUB_SLOTS = {}  # nothing at all
state = {
    "language": "ar",
    "doctor": "Badrya AlBeiruti",
    "doctor_ar": "بدرية البيروتي",
    "speciality": "Endocrinology",
    "available_doctors": [
        {"Doctor": "Ameera Barakat", "DoctorAR": "اميرة بركات",
         "Nearest_Date": today, "Nearest_Time": "19:45:00"},
        {"Doctor": "Badrya AlBeiruti", "DoctorAR": "بدرية البيروتي",
         "Nearest_Date": today, "Nearest_Time": "11:48:00"},
    ],
    "date": today_iso,
    "requested_date": None,
}
reply = _handle_slot_fetch(state, "ar")
expect_in("E2E no-slots reply mentions alt", "اميرة", reply)


# ─────────────────────────────────────────────────────────────────────────────
# Strict-date — patient asks "today" and no doctors → "no doctors today"
# ─────────────────────────────────────────────────────────────────────────────
section("Strict-date — explicit date with no doctors")

from services.doctor_selector import fetch_doctors

# Today empty, tomorrow has doctors
_STUB_AVAILABILITY = [
    _row(101, "Ameera Barakat", "اميرة بركات", "Endocrinology", "أمراض الغدد",
         tomorrow, time(19, 45)),
]

# Non-strict: rolls forward, finds tomorrow
docs, used = fetch_doctors("Endocrinology", "ar", today_iso, strict_date=False)
check("non-strict finds tomorrow doctors", len(docs) == 1)
expect_eq("non-strict used_date is tomorrow", used, tomorrow_iso)

# Strict: zero results, used_date stays today
docs, used = fetch_doctors("Endocrinology", "ar", today_iso, strict_date=True)
expect_eq("strict returns empty list", docs, [])
expect_eq("strict used_date stays today", used, today_iso)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n=== RESULTS: {len(PASSED)} passed, {len(FAILED)} failed ===")
if FAILED:
    print("\nFailures:")
    for name, detail in FAILED:
        print(f"  ✗ {name}\n    {detail}")
    sys.exit(1)
print("All tests passed.")
