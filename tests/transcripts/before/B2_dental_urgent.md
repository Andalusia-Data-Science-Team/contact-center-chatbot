# Scenario: B2_dental_urgent

**Description:** Path B: dental with urgency + Saudi slang
**Run at:** 2026-04-20T15:57:18

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

**Bot:** ازاي أقدر أساعدك ريم؟

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

تحب تحجز مع مين؟

_stage: `doctor_list`_

### Turn 6

**User:** اليوم

**Bot:** _(empty reply)_

_stage: `doctor_list`_

### Turn 7

**User:** ٨ مساء

**Bot:** _(empty reply)_

_stage: `slot_selection`_

### Turn 8

**User:** تمام

**Bot:** _(empty reply)_

_stage: `doctor_list`_

### Turn 9

**User:** 0566778899

**Bot:** ممكن تعرفني هل هي كاش أو تأمين؟

_stage: `doctor_list`_

### Turn 10

**User:** لا ما عندي تأمين

**Bot:** _(empty reply)_

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
- **Tokens:** 32,842 (in: 32,307 / out: 535)
- **Total latency:** 76,464 ms
- **Errors:** 4

  - turn 6: empty bot reply
  - turn 7: empty bot reply
  - turn 8: empty bot reply
  - turn 10: empty bot reply
