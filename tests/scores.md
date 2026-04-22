# Saudi Dialect Scenario Scoring Report

**Run date:** 2026-04-20
**Scenarios:** 6 (covering all 3 booking paths + edge cases)
**Rubric:** each dimension scored 0-5 (0 = broken, 5 = perfect)

---

## Scoring Dimensions

1. **Dialect / language fidelity** — Does the bot understand Saudi dialect inputs (أبغى, هلا, أبي, مناسب, تمام, بكرا, المغرب)?
2. **Flow completion** — Does the conversation reach `booking_stage == "complete"` with a selected slot?
3. **Slot / time parsing** — Does the bot parse Saudi time expressions (المغرب, بعد العصر, ٨ مساء, الصبح)?
4. **Insurance handling** — After patient names an insurance company, does the bot acknowledge and advance?
5. **Arabic numerals & formatting** — Does the bot recognize Arabic-Indic digits (٠-٩) in ages/times/phones?
6. **Error recovery** — When the LLM misses a signal, does code recover (no empty replies, no loops)?

---

## Per-Scenario Scores

### A1 — Symptom-based (chest pain + cough)
| Dimension | Score | Notes |
|---|---|---|
| Dialect | 3 | "أيوه صح" failed to confirm specialty — required 3 tries |
| Flow completion | 1 | Stuck at `doctor_list`; never selected a slot |
| Slot parsing | 0 | "بكرا", "المغرب", "تمام مناسب" all ignored — bot kept asking specialty confirmation |
| Insurance | 2 | Captured "Bupa" in state but bot returned **empty reply** — patient sees nothing |
| Numerals | 4 | "٣٥ سنة" captured correctly as age=35 |
| Error recovery | 1 | 2 empty replies; no self-correction |
| **Total** | **11/30** | |

### A2 — Vague complaint (requires clarification)
| Dimension | Score | Notes |
|---|---|---|
| Dialect | 3 | Most words understood; "تعبانة شوي" treated as non-complaint initially |
| Flow completion | 0 | Never advanced past routing confirmation |
| Slot parsing | 0 | "اليوم", "بعد المغرب", "يصير" all ignored |
| Insurance | 0 | Never reached insurance stage |
| Numerals | 4 | "٢٨" captured as age correctly |
| Error recovery | 1 | Infinite loop on "تبغى أعرض لك الأطباء المتاحين؟" (turns 6-10) |
| **Total** | **8/30** | |

### B1 — Named specialty (ophthalmology)
| Dimension | Score | Notes |
|---|---|---|
| Dialect | 3 | "أيوه" (with hamza) failed acceptance; "أبغى" understood |
| Flow completion | 1 | Reached doctor_list but never picked a doctor/slot |
| Slot parsing | 0 | "بكرا ان شاء الله", "بعد العصر" both ignored |
| Insurance | 1 | "التعاونية" captured in state but bot reply was empty |
| Numerals | 4 | "٤٠" captured as age |
| Error recovery | 0 | 4 empty replies; stuck in `doctor_list` |
| **Total** | **9/30** | |

### B2 — Dental urgent (Saudi slang "أبي")
| Dimension | Score | Notes |
|---|---|---|
| Dialect | 4 | "أبي كشف أسنان" routed correctly; "ايوه" (no hamza) worked |
| Flow completion | 2 | Got doctor list; never completed |
| Slot parsing | 0 | "اليوم", "٨ مساء" (Arabic numeral + period) not recognized |
| Insurance | 1 | "لا ما عندي تأمين" produced empty reply |
| Numerals | 3 | Age yes, but "٨ مساء" time ignored |
| Error recovery | 1 | Multiple empty replies |
| **Total** | **11/30** | |

### C1 — Named doctor ("د. ولاء السعدني")
| Dimension | Score | Notes |
|---|---|---|
| Dialect | 3 | Doctor name extracted correctly |
| Flow completion | 0 | Doctor fuzzy-match failed (no specialty context → empty doctor list) |
| Slot parsing | 0 | "بكرا المغرب" captured but couldn't query slots |
| Insurance | 2 | "مدغلف" matched to "Medgulf" — good |
| Numerals | 4 | "٥٠" captured as age |
| Error recovery | 1 | Bot said "specialty **None**" literally in output — bad UX |
| **Total** | **10/30** | |

### C2 — Change mind mid-flow
| Dimension | Score | Notes |
|---|---|---|
| Dialect | 3 | "أبغى", "خليها باطنة" — first recognized, correction ignored |
| Flow completion | 1 | Ignored the specialty change; stuck on original (dermatology) |
| Slot parsing | 0 | "الصبح", "مناسب" ignored; 5 consecutive empty replies |
| Insurance | 3 | "اكسا" → "AXA" matched; bot did reply (to a stale question) |
| Numerals | 4 | "٣٢" captured |
| Error recovery | 0 | 5 consecutive empty replies; no recovery |
| **Total** | **11/30** | |

---

## Aggregate: **60 / 180 (33%)**

---

## Root Causes (ranked by impact)

### 🔴 P0 — Hamza normalization missing in `is_acceptance`

**Location:** `nodes/_helpers.py:62-71`

`ACCEPTANCE_WORDS` contains `"ايوه"` but not `"أيوه"` (Saudi Arabic commonly writes ي with hamza above the alif). Python string equality is byte-exact, so `"أيوه" != "ايوه"`. The substring check also fails for same reason.

**Blast radius:** Causes specialty confirmation loops (A1 T5, B1 T5), slot confirmation failures, "tomorrow + evening" chains.

**Fix:** Normalize Arabic text before comparison — strip hamza on alif (أ/إ/آ → ا), normalize ta-marbuta (ة → ه), normalize ya (ى → ي).

### 🔴 P0 — Saudi time keywords missing from safety net

**Location:** `nodes/slot_selection.py:293-297`

`_TIME_PERIOD_KEYWORDS` includes `"العصر"`, `"المساء"` but not:
- `"المغرب"` (sunset/~6-7 PM, extremely common in Saudi)
- `"الفجر"`, `"الظهر"` (prayer-time references)
- `"بكرا"` (tomorrow, Saudi dialect — should go to `requested_date`, not time filter)

**Blast radius:** A1 T7 "المغرب", B1 T7 "بعد العصر" → not matched → LLM empty reply → stuck.

### 🔴 P0 — Arabic-Indic digits don't match time patterns

**Location:** `nodes/slot_selection.py:304-305`

`_TIME_PATTERN` and `_BARE_TIME_PATTERN` use `\d` which in Python re is `[0-9]` only. `"٨ مساء"` contains ٨ (Arabic-Indic 8) — not matched. Should either include `[\d\u0660-\u0669]` or convert Arabic-Indic to ASCII before matching.

**Blast radius:** B2 T7 "٨ مساء" lost; any Arabic-typed time lost.

### 🟡 P1 — Path C without specialty routing shows broken message

**Location:** `nodes/doctor_selection.py:39-53`, `services/formatter.py` (doctor_not_found_message)

When patient names a doctor directly (Path C) before any specialty is routed, `available_doctors=[]` and `speciality=None`. The fuzzy match returns `not_matched`, and the message says `ما لقيت الاسم ده في قائمة أطباء **None**`. The literal word "None" leaks to the patient.

**Fix:** When `available_doctors=[]`, don't run the fuzzy-match formatter — instead, either do a global doctor search in the DB, or route by the doctor's specialty (fetched from DB), or ask the patient for specialty first.

### 🟡 P1 — Empty replies when code handles but doesn't fill `last_bot_message`

**Location:** Multiple — `graph.py` `_safety_net`, `slot_selection.py` safety net clears message

The safety net at the end of graph only fills in replies for `routing`, `greeting_done`, and `None` stages. For `doctor_list`, `slot_selection`, `patient_info` when empty, user sees a blank message.

**Fix:** Extend `_safety_net` in `graph.py` to provide a sensible fallback for all stages ("didn't catch that, can you clarify?").

### 🟠 P2 — `is_acceptance` overmatches short strings

`is_acceptance("تمام مناسب")` returns True (correct), but also `is_acceptance("مناسب لي بعد المغرب ان شاء الله")`? Actually it requires `len(t.split()) <= 3`, so "بعد المغرب" (2 words, contains no acceptance word) is False. OK. But "أيوه عندي تأمين" (3 words, contains "أيوه") — acceptance partial check `any(w in t for w in ACCEPTANCE_PARTIAL)` — "ايوه" is in `ACCEPTANCE_PARTIAL` but again hamza issue. After P0 fix, "أيوه عندي تأمين" would match as acceptance, which is wrong for insurance context. Minor — context-dependent, not globally breaking.

### 🟠 P2 — Specialty change-mind not detected

C2 T5 "لا استني، خليها باطنة أحسن" — user cancels derm and wants internal medicine. Current code: `is_acceptance` returns False → re-routes with the new message as clarification → router may or may not pick internal. In observed run, router kept dermatology. Need to detect "لا" (no) clearly and reset `speciality`.

---

## Recommended Fix Plan (in order)

Apply these P0/P1 fixes in `nodes/_helpers.py` and `nodes/slot_selection.py`:

1. **Add `normalize_ar()` helper** and apply it in `is_acceptance` and everywhere the LLM-free code matches Arabic text.
2. **Extend `_TIME_PERIOD_KEYWORDS`** with المغرب, الفجر, الظهر, and handle بكرا/اليوم as `requested_date` not time filter.
3. **Normalize Arabic-Indic digits** in user message before regex time matching.
4. **Handle Path C when `available_doctors=[]`**: look up doctor's specialty from DB, then proceed to slot fetch. Or at minimum: never render "None" to the user — fall back to specialty-agnostic phrasing.
5. **Fallback in `_safety_net`** for any stage that still has empty `last_bot_message` — a generic "ممكن تعيد الطلب؟" / "Could you rephrase?" prevents blank replies.

I'll confirm the approach with you before editing code.
