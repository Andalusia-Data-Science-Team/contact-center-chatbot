# Andalusia Health — AI Booking Agent

A conversational AI booking assistant for **Andalusia Health Group** (Egypt & Saudi Arabia).  
Handles doctor appointment booking via WhatsApp-style chat in **Arabic** and **English**.

## Overview

This bot replicates the behavior of real Andalusia call center agents, based on analysis of actual WhatsApp booking conversations. The tone is warm, personal, and efficient — not robotic.

## Conversation Flow

The booking follows this sequence, matching how real human agents handle calls:

```
1. GREETING        → Bot greets, asks for patient's name
2. NAME COLLECTED  → Bot greets by name, asks how to help
3. COMPLAINT       → Patient describes need (specialty, symptoms, or doctor name)
4. ROUTING         → System silently matches to specialty (no triage questions)
5. DOCTOR LIST     → Bot presents available doctors, asks preference
6. SLOT SELECTION  → Bot shows earliest slot, patient picks time
7. PATIENT INFO    → Bot asks for phone + "كاش ام تأمين؟" (cash or insurance?)
8. CONFIRMATION    → Bot confirms with full details + arrival instructions
```

### Key Design Decisions (from real chat analysis)

| What real agents do | How the bot implements it |
|---|---|
| Ask for name **first** before anything else | `greeting_done` stage before complaint collection |
| Never ask medical triage questions | Triage step skipped — direct routing from complaint |
| Use "حضرتك" and "أ/" with names | Prompt instructs warm Saudi/Egyptian dialect |
| Say "كاش ام تأمين؟" not formal insurance question | Prompt and formatter use casual phrasing |
| Confirm with "واستأذنك الحضور قبل الميعاد بربع ساعة" | Confirmation template matches real agent messages |
| Mention the Andalusia Health app after booking | Confirmation includes app download prompt |
| Close with "اي استفسار آخر؟" | Included in confirmation message |

## Booking Stages

| Stage | Description |
|---|---|
| `none` | Initial state — bot greets and asks for name |
| `greeting_done` | Name collected — bot asks how to help |
| `complaint` | Collecting symptoms/reason for visit + patient age |
| `routing` | System routes to specialty (handled by code, not LLM) |
| `doctor_list` | Available doctors shown, patient picks one |
| `slot_selection` | Time slots presented, patient selects |
| `patient_info` | Collecting phone number + insurance status |
| `complete` | Booking confirmed with details card |

## Architecture

```
app.py (Streamlit UI)
  └── graph.py (LangGraph pipeline)
        ├── language_node      → Detect AR/EN
        ├── emergency_node     → Check for emergency keywords
        ├── intent_node        → Classify intent (booking/inquiry/etc.)
        ├── conversation_node  → LLM generates reply + state updates
        ├── routing_node       → Route complaint → specialty → doctors
        ├── doctor_selection   → Fuzzy match doctor name → fetch slots
        ├── slot_selection     → Time preference → nearest slot
        ├── patient_info       → Verify completeness → confirmation
        └── safety_net         → Fallback if reply is empty
```

### Key Files

| File | Purpose |
|---|---|
| `prompts/conversation.py` | Main LLM system prompt — defines personality and stage behavior |
| `prompts/intent.py` | Intent classification prompt |
| `prompts/routing.py` | Specialty routing + time parsing prompts |
| `services/formatter.py` | All user-facing message templates |
| `services/slot_handler.py` | Time parsing, slot filtering, nearest-slot logic |
| `services/doctor_selector.py` | DB queries for doctors + fuzzy name matching |
| `nodes/_helpers.py` | State update logic, acceptance detection |
| `state.py` | BookingState TypedDict definition |
| `config/constants.py` | Specialty lists (EN/AR), emergency keywords |
| `config/settings.py` | API keys, DB credentials, thresholds |

## Language Support

- **Arabic**: Saudi/Egyptian dialect mix (matching real agents who are Egyptian working in Saudi)
- **English**: Warm, friendly tone
- Auto-detected from first message; Arabic locks once detected

## Example Conversations

### Arabic Flow (based on real chats)
```
Bot:  السلام عليكم، معك أندلسية من مجموعة أندلسية صحة، اتشرف بالاسم
User: حسن محمد
Bot:  أهلا وسهلا بحضرتك أ/ حسن، ازاي أقدر أساعدك؟
User: حجز موعد مع دكتورة جلدية
Bot:  [routes to Dermatology, shows doctor list]
User: 2
Bot:  أقرب موعد مع د. نسمة يوم الثلاثاء الساعة 08:30 PM. مناسب لحضرتك؟
User: مناسب
Bot:  تمام أ/ حسن! ممكن رقم جوالك؟ وكاش ام تأمين؟
User: 0501234567 كاش
Bot:  تم تأكيد الحجز أ/ حسن ... واستأذنك الحضور قبل الميعاد بربع ساعة
```

### English Flow
```
Bot:  Hello! I'm Andalusia from Andalusia Health Group. May I have your name please?
User: Sarah Johnson
Bot:  Welcome Sarah! How can I help you today?
User: I need to see a dermatologist
Bot:  [routes to Dermatology, shows doctor list]
User: Dr. Nesma
Bot:  The earliest slot with Dr. Nesma is Tuesday at 08:30 PM. Does that work for you?
User: Yes
Bot:  Done Sarah! Can I have your phone number? And will it be cash or insurance?
User: 0501234567, insurance with Bupa
Bot:  Your booking is confirmed, Sarah! ... Please arrive 15 minutes early
```

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

### Requirements
- Python 3.10+
- SQL Server access (for doctor/slot queries)
- Fireworks AI API key (for LLM)
- See `config/settings.py` for all credentials

## What's NOT Included Yet

These intents are detected but return "module under development" messages:
- **Inquiry** — questions about services, prices, hours
- **Complaint** — patient complaints
- **Cancellation** — cancel existing appointments  
- **Reschedule** — change existing appointments
