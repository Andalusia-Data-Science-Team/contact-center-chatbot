# Scenario: A2_vague_symptom

**Description:** Path A: vague complaint requiring clarification
**Run at:** 2026-04-20T15:55:21

---

### Turn 1

**User:** هلا

**Bot:** السلام عليكم، معك نور من مجموعة أندلسية صحة 😊 اتشرف بالاسم؟

_stage: `None`_

### Turn 2

**User:** نورة القحطاني

**Bot:** أهلا وسهلا بحضرتك نورة القحطاني 😊 ازاي أقدر أساعدك؟

_stage: `greeting_done`_

### Turn 3

**User:** ٢٨

**Bot:** حسناً أ/ نورة، كم عمر المريض؟

_stage: `complaint`_

### Turn 4

**User:** تعبانة شوي وما أدري وش فيني

**Bot:** أهلا أ/ نورة، أنا نور. ازاي أقدر أساعدك؟

_stage: `complaint`_

### Turn 5

**User:** صداع ودوخه

**Bot:** هل الصداع في جنب واحد ولا في كل الراس؟

_stage: `routing`_

### Turn 6

**User:** أيوه

**Bot:** تمام أ/ نورة القحطاني، حضرتك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين اليوم؟

_stage: `routing`_

### Turn 7

**User:** اليوم

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 8

**User:** بعد المغرب

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 9

**User:** يصير

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 10

**User:** 0555123456

**Bot:** تمام أ/ نورة القحطاني، يبدو إنك محتاج تخصص **المخ والأعصاب**. تبغى أعرض لك الأطباء المتاحين؟

_stage: `routing`_

### Turn 11

**User:** لا ما عندي

**Bot:** _(empty reply)_

_stage: `doctor_list`_

---

## Final State

- **language**: `ar`
- **booking_stage**: `doctor_list`
- **intent**: `booking`
- **patient_name**: `نورة القحطاني`
- **patient_age**: `28`
- **complaint_text**: `صداع ودوخه`
- **speciality**: `Neurology`
- **specialty_confirmed**: `False`
- **phone**: `0555123456`
- **insured**: `False`

## Metrics

- **LLM calls:** 19
- **Tokens:** 37,525 (in: 36,846 / out: 679)
- **Total latency:** 75,116 ms
- **Errors:** 1

  - turn 11: empty bot reply
