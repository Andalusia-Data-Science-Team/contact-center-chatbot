# Scenario: B2_dental_urgent

**Description:** Path B: dental with urgency + Saudi slang
**Run at:** 2026-04-20T16:19:21

---

### Turn 1

**User:** السلام عليكم

**Bot:** معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `not started`_

### Turn 2

**User:** ريم الشمري

**Bot:** أهلا وسهلا بحضرتك ريم 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٢٢

**Bot:** ازاي أقدر أساعدك؟

_stage: `complaint`_

### Turn 4

**User:** أبي كشف أسنان ضروري اليوم

**Bot:** تمام أ/ ريم الشمري، حضرتك محتاج تخصص **خدمات الأسنان**. تبغى أعرض لك الأطباء المتاحين اليوم؟

_stage: `routing`_

### Turn 5

**User:** ايوه

**Bot:** تخصص **خدمات الأسنان** — الأطباء المتاحين ليوم اليوم:

1. **د. عبدالكريم محمد بخش** — أقرب موعد: ٩:٣٠ صباحاً (12 مواعيد)
2. **د. رغدة عبدالحميد عقدة** — أقرب موعد: ١١:٣٠ مساءً (1 مواعيد)
3. **د. يارا أحمد المغربى** — أقرب موعد: ٦:٣٠ مساءً (1 مواعيد)

تحب تحجز مع مين؟

_stage: `doctor_list`_

### Turn 6

**User:** اليوم

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

### Turn 7

**User:** ٨ مساء

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

### Turn 8

**User:** تمام

**Bot:** ما فهمت الوقت، ممكن توضح الوقت اللي يناسبك؟

_stage: `slot_selection`_

### Turn 9

**User:** 0566778899

**Bot:** ممكن تقول لي هل هي كاش أو تأمين؟

_stage: `slot_selection`_

### Turn 10

**User:** لا ما عندي تأمين

**Bot:** تحب تحجز مع أي دكتور من القائمة؟ قولي رقم الدكتور أو اسمه.

_stage: `doctor_list`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `doctor_list`
- **intent**: `booking`
- **patient_name**: `ريم الشمري`
- **patient_age**: `22`
- **complaint_text**: `كشف أسنان`
- **speciality**: `Dental Services`
- **specialty_confirmed**: `True`
- **preferred_time**: `8 مساء`
- **phone**: `0566778899`
- **insured**: `False`

## Metrics

- **LLM calls:** 13
- **Tokens:** 33,186 (in: 32,644 / out: 542)
- **Total latency:** 22,761 ms
- **Errors:** 0
