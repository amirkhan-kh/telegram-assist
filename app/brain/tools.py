"""Anthropic tool definitions — one tool per intent.

Each tool's ``input_schema`` mirrors the matching pydantic model in
:mod:`app.brain.intents`, with the ``TimeSpec`` object INLINED (no ``$ref`` /
``$defs``, which the Anthropic tools API does not accept) and every property
listed as required, with ``additionalProperties: false``.

The descriptions are bilingual (Uzbek + English) and deliberately teach the
model the perspective/time rules; the router forces tool use so these
descriptions carry most of the routing signal.
"""

from __future__ import annotations

from typing import Any

# Inlined TimeSpec schema, reused by every tool that takes a time. Kept as a
# factory so each tool gets its own dict copy (Anthropic mutates nothing, but
# sharing the same object across tools is needlessly fragile).
_TIME_DESC = (
    "Vaqt iborasi. Egasi aytgan vaqt iborasini AYNAN 'raw' ichiga ko'chiring "
    "(masalan '5 minutda', 'yarim soatda', 'ertaga soat 9'). Absolyut vaqtni "
    "o'zingiz hisoblamang. / Copy the owner's time phrase verbatim into 'raw'."
)


def _time_spec_schema(description: str = _TIME_DESC) -> dict[str, Any]:
    """Return a fresh inlined JSON schema for a ``TimeSpec`` object."""

    return {
        "type": "object",
        "description": description,
        "properties": {
            "raw": {
                "type": "string",
                "description": "The time phrase exactly as the owner said it.",
            },
            "kind": {
                "type": "string",
                "enum": ["relative", "absolute", "none"],
                "description": "Whether the phrase is relative, absolute, or absent.",
            },
            "rel_minutes": {
                "type": ["integer", "null"],
                "description": "Relative offset in minutes if clearly stated, else null.",
            },
            "clock_hint": {
                "type": ["string", "null"],
                "description": "A clock time like '09:00' if present, else null.",
            },
        },
        "required": ["raw", "kind", "rel_minutes", "clock_hint"],
        "additionalProperties": False,
    }


def _recurrence_schema() -> dict[str, Any]:
    """Inlined JSON schema for a ``RecurrenceSpec`` (repeating reminder)."""
    return {
        "type": "object",
        "description": (
            "Takror jadvali. Bir martalik bo'lsa freq='none'. «har kuni»=daily, "
            "«har dushanba»=weekly + weekday (0=dushanba..6=yakshanba), «har oy»/"
            "«oy oxirida»=monthly (day_of_month yoki month_end=true). hour/minute "
            "— mahalliy vaqt. / recurrence schedule."
        ),
        "properties": {
            "freq": {
                "type": "string",
                "enum": ["none", "daily", "weekly", "monthly"],
                "description": "Takror chastotasi / recurrence frequency.",
            },
            "weekday": {
                "type": ["integer", "null"],
                "description": "0=dushanba..6=yakshanba (weekly uchun) / weekday.",
            },
            "day_of_month": {
                "type": ["integer", "null"],
                "description": "Oyning kuni 1-31 (monthly uchun) / day of month.",
            },
            "month_end": {
                "type": "boolean",
                "description": "Oyning oxirgi kuni / last day of month.",
            },
            "hour": {"type": "integer", "description": "Soat 0-23 / hour."},
            "minute": {"type": "integer", "description": "Daqiqa 0-59 / minute."},
        },
        "required": [
            "freq",
            "weekday",
            "day_of_month",
            "month_end",
            "hour",
            "minute",
        ],
        "additionalProperties": False,
    }


_DELIVERY_SCHEMA = {
    "type": "string",
    "enum": ["text", "voice", "both", "ask"],
    "description": (
        "Yetkazish usuli. Egasi 'ovozli xabar'/'ovozda yubor'/'audio' desa "
        "'voice'; 'yozma'/'matn'/'text qilib yubor' desa 'text'. Agar usul "
        "AYTILMAGAN bo'lsa 'ask' (yordamchi egadan tugma orqali so'raydi). / "
        "delivery channel; use 'ask' when the owner did not specify one."
    ),
}

_FORMALITY_SCHEMA = {
    "type": "string",
    "enum": ["neutral", "formal"],
    "description": (
        "Xabar uslubi. Egasi 'rasmiy'/'rasmiyroq'/'hurmat bilan' desa "
        "'formal' (siz-shaklida, to'liq jumlalar). Aks holda 'neutral'. / "
        "message register; default neutral."
    ),
}


def _send_message_tool() -> dict[str, Any]:
    return {
        "name": "send_message",
        "description": (
            "Kontaktga HOZIR xabar yuborish. Egasi 'Akmalga ... yubor', "
            "'opamga ayt' kabi buyruq bersa ishlatiladi. 'content' ni "
            "qabul qiluvchiga qaratilgan tabiiy o'zbekcha xabar qilib yozing "
            "(aka/opa qo'shing). / Send a message to a contact right now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient_name": {
                    "type": "string",
                    "description": "Qabul qiluvchi ismi / recipient name.",
                },
                "content": {
                    "type": "string",
                    "description": "Tayyor o'zbekcha xabar matni / the message text.",
                },
                "delivery": _DELIVERY_SCHEMA,
                "formality": _FORMALITY_SCHEMA,
            },
            "required": ["recipient_name", "content", "delivery"],
            "additionalProperties": False,
        },
    }


def _schedule_message_tool() -> dict[str, Any]:
    return {
        "name": "schedule_message",
        "description": (
            "Kontaktga KELAJAKDA, belgilangan vaqtda xabar yuborish. Egasi "
            "vaqt aytsa ('ertaga ... yubor') shu tanlanadi. UCHRASHUV haqida "
            "xabar berilsa ('X ga uchrashuvimiz/meeting haqida xabar ber') "
            "meeting_notice=true qiling — xabar HOZIR va belgilangan vaqtda "
            "(when) ikki marta yuboriladi, va 'content' ichida uchrashuv vaqtini "
            "albatta yozing. Online meet/meeting/miting bo'lsa "
            "create_meet_link=true. / Send a message at a future time; set "
            "meeting_notice for a meeting heads-up (sent now + at the time)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient_name": {
                    "type": "string",
                    "description": "Qabul qiluvchi ismi / recipient name.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Tayyor o'zbekcha xabar matni. Uchrashuv haqida bo'lsa "
                        "vaqtni ham yozing (masalan 'ertaga soat 9:35 dagi "
                        "uchrashuvimiz'). / the message text."
                    ),
                },
                "when": _time_spec_schema(),
                "delivery": _DELIVERY_SCHEMA,
                "formality": _FORMALITY_SCHEMA,
                "meeting_notice": {
                    "type": "boolean",
                    "description": (
                        "Uchrashuv haqidagi xabarmi — hozir va when vaqtida ikki "
                        "marta yuboriladi. / meeting heads-up: deliver now + at when."
                    ),
                },
                "create_meet_link": {
                    "type": "boolean",
                    "description": (
                        "Online meet/meeting/miting bo'lsa true — Google Meet "
                        "havolasi yaratilib xabarga qo'shiladi. / mint a Meet link."
                    ),
                },
            },
            "required": [
                "recipient_name",
                "content",
                "when",
                "delivery",
                "meeting_notice",
                "create_meet_link",
            ],
            "additionalProperties": False,
        },
    }


def _create_reminder_tool() -> dict[str, Any]:
    return {
        "name": "create_reminder",
        "description": (
            "Egasining o'ziga eslatma qo'yish ('eslat', 'esimga sol'), bir martalik "
            "yoki TAKRORIY ('har dushanba', 'har kuni', 'oy oxirida'). Hech kimga "
            "xabar yuborilmaydi. / Set a personal reminder, one-shot or recurring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Nimani eslatish / what to be reminded about.",
                },
                "when": _time_spec_schema(),
                "pre_alerts_minutes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Oldindan ogohlantirish daqiqalari / pre-alert minutes.",
                },
                "recurrence": {
                    "anyOf": [_recurrence_schema(), {"type": "null"}],
                    "description": (
                        "Takror jadvali yoki null. Bir martalik bo'lsa null. / "
                        "recurrence schedule or null for one-shot."
                    ),
                },
            },
            "required": ["text", "when", "pre_alerts_minutes", "recurrence"],
            "additionalProperties": False,
        },
    }


def _create_promise_tool() -> dict[str, Any]:
    return {
        "name": "create_promise",
        "description": (
            "Egasi O'ZI biror ishni qilishni va'da qilganda ('men ... "
            "qilaman', 'men ... yuboraman'). Bu shaxsiy va'da, deadline bilan "
            "kuzatiladi. / The owner promised to do something themselves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "what": {
                    "type": "string",
                    "description": "Egasi nima qilishni va'da qildi / what was promised.",
                },
                "deadline": _time_spec_schema(),
                "counterparty_name": {
                    "type": ["string", "null"],
                    "description": "Kim uchun, agar aytilgan bo'lsa / for whom, if stated.",
                },
                "pre_alerts_minutes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Oldindan ogohlantirish daqiqalari / pre-alert minutes.",
                },
            },
            "required": ["what", "deadline", "counterparty_name", "pre_alerts_minutes"],
            "additionalProperties": False,
        },
    }


def _assign_task_with_followup_tool() -> dict[str, Any]:
    return {
        "name": "assign_task_with_followup",
        "description": (
            "BOSHQA ODAM egasi uchun biror ishni bajarishi kerak bo'lganda "
            "('Akmal ... qiladi', 'falonchi ... topshiradi', 'undan ... ol'). "
            "Vazifa kuzatiladi va kerak bo'lsa o'sha odamga eslatma "
            "yuboriladi. / Someone else owes the owner a task; track and "
            "follow up."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "assignee_name": {
                    "type": "string",
                    "description": "Vazifani bajaradigan odam / the assignee.",
                },
                "task": {
                    "type": "string",
                    "description": "Bajarilishi kerak bo'lgan ish / the task.",
                },
                "deadline": _time_spec_schema(),
                "pre_alert_to_owner_minutes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Egasiga oldindan eslatma daqiqalari / owner pre-alerts.",
                },
                "auto_followup_to_assignee": {
                    "type": "boolean",
                    "description": "Odamga avtomatik eslatma yuborilsinmi / auto follow-up.",
                },
                "followup_offsets_minutes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Deadlinega nisbatan eslatma siljishi daqiqalarda "
                        "(masalan -15, 0) / follow-up offsets relative to deadline."
                    ),
                },
            },
            "required": [
                "assignee_name",
                "task",
                "deadline",
                "pre_alert_to_owner_minutes",
                "auto_followup_to_assignee",
                "followup_offsets_minutes",
            ],
            "additionalProperties": False,
        },
    }


def _schedule_meeting_tool() -> dict[str, Any]:
    return {
        "name": "schedule_meeting",
        "description": (
            "Uchrashuv rejalashtirish, kerak bo'lsa Google Meet havolasi "
            "bilan. / Schedule a meeting, optionally with a Meet link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Uchrashuv mavzusi / meeting title.",
                },
                "when": _time_spec_schema(),
                "duration_minutes": {
                    "type": "integer",
                    "description": "Davomiyligi daqiqalarda / duration in minutes.",
                },
                "invitee_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Taklif qilinadiganlar / invitees.",
                },
                "create_meet_link": {
                    "type": "boolean",
                    "description": "Meet havolasi yaratilsinmi / create a Meet link.",
                },
                "notify_target_name": {
                    "type": ["string", "null"],
                    "description": (
                        "Uchrashuv kim bilan bo'lsa, o'sha odamni shu yerga qo'ying. "
                        "Unga 30/15 daqiqa oldin eslatma va boshlanганда Meet havolasi "
                        "yuboriladi. / the meeting partner who gets reminders + link."
                    ),
                },
            },
            "required": [
                "title",
                "when",
                "duration_minutes",
                "invitee_names",
                "create_meet_link",
                "notify_target_name",
            ],
            "additionalProperties": False,
        },
    }


def _find_free_slots_tool() -> dict[str, Any]:
    return {
        "name": "find_free_slots",
        "description": (
            "Egasining kalendaridan bo'sh vaqtlarni topish. / Find free "
            "slots in the owner's calendar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_range": _time_spec_schema(
                    "Qaysi kun/oraliqda izlash / which day or range to search."
                ),
                "duration_minutes": {
                    "type": "integer",
                    "description": "Kerakli slot uzunligi / desired slot length.",
                },
            },
            "required": ["date_range", "duration_minutes"],
            "additionalProperties": False,
        },
    }


def _add_finance_tool() -> dict[str, Any]:
    return {
        "name": "add_finance",
        "description": (
            "Qarz yoki haqdorlikni qayd qilish. 'men ... qarzdorman' => "
            "direction 'debt' (egasi qarzdor). 'mendan ... qarz' / 'u menga "
            "berishi kerak' => direction 'credit' (kimdir egasiga qarzdor). / "
            "Record a debt the owner owes, or a credit someone owes the owner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["debt", "credit"],
                    "description": "'debt' egasi qarzdor, 'credit' kimdir qarzdor.",
                },
                "counterparty_name": {
                    "type": "string",
                    "description": "Ikkinchi tomon ismi / the counterparty.",
                },
                "amount": {
                    "type": "number",
                    "description": "Summa / amount.",
                },
                "currency": {
                    "type": "string",
                    "description": "Valyuta, odatda UZS / currency.",
                },
                "due": {
                    "anyOf": [
                        _time_spec_schema("To'lov muddati / due date, if any."),
                        {"type": "null"},
                    ],
                    "description": "To'lov muddati yoki null / due time or null.",
                },
                "note": {
                    "type": ["string", "null"],
                    "description": "Izoh / optional note.",
                },
            },
            "required": ["direction", "counterparty_name", "amount", "currency", "due", "note"],
            "additionalProperties": False,
        },
    }


def _get_digest_tool() -> dict[str, Any]:
    return {
        "name": "get_digest",
        "description": (
            "Kanal faoliyatining qisqacha xulosasini olish. / Get a digest "
            "of recent channel activity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_n": {
                    "type": "integer",
                    "description": "Nechta element ko'rsatilsin / how many items.",
                },
            },
            "required": ["top_n"],
            "additionalProperties": False,
        },
    }


def _cancel_item_tool() -> dict[str, Any]:
    return {
        "name": "cancel_item",
        "description": (
            "Avval yaratilgan element (eslatma, va'da, vazifa, uchrashuv yoki "
            "xabar) ni bekor qilish. / Cancel a previously created item."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_kind": {
                    "type": "string",
                    "enum": ["reminder", "promise", "followup", "meeting", "message"],
                    "description": "Bekor qilinadigan element turi / item kind.",
                },
                "selector": {
                    "type": "string",
                    "description": "Qaysi elementni bekor qilish tavsifi / which item.",
                },
            },
            "required": ["item_kind", "selector"],
            "additionalProperties": False,
        },
    }


def _list_contacts_tool() -> dict[str, Any]:
    return {
        "name": "list_contacts",
        "description": (
            "Egasining saqlangan kontaktlari ro'yxatini ko'rsatish yoki qidirish. "
            "'kontaktlarim', 'kontaktlarimni chiqar', 'falonchini top', 'raqami "
            "90... kim'. Ixtiyoriy 'query' bilan ISM, @username/nik yoki TELEFON "
            "raqami bo'yicha qidirish. / List or search the owner's contacts by "
            "name, username/nickname or phone number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": "Ism bo'lagi bo'yicha filtr yoki null / optional name filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Nechta ko'rsatilsin (standart 40) / how many.",
                },
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
    }


def _list_finance_tool() -> dict[str, Any]:
    return {
        "name": "list_finance",
        "description": (
            "Qarzlar ro'yxatini umumiy summa bilan ko'rsatish. 'mendan qarzi "
            "borlar ro'yxati' / 'kim menga qarzdor' => direction 'they_owe_me'. "
            "'men kimga qarzdorman' => 'i_owe_them'. 'barcha qarzlar' => 'all'. / "
            "List outstanding debts/credits with totals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["they_owe_me", "i_owe_them", "all"],
                    "description": "'they_owe_me' menga qarzdorlar, 'i_owe_them' men qarzdor.",
                },
            },
            "required": ["direction"],
            "additionalProperties": False,
        },
    }


def _list_agenda_tool() -> dict[str, Any]:
    return {
        "name": "list_agenda",
        "description": (
            "Egasining joriy rejasini ko'rsatish: eslatmalar, va'dalar, "
            "nazoratdagi topshiriqlar va uchrashuvlar. 'bugungi rejam', "
            "'rejalarim', 'eslatmalarim ro'yxati', 'nima ishlarim bor' kabi "
            "so'rovlarda. / List the owner's plan (reminders, promises, tasks, "
            "meetings)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "all"],
                    "description": "'today' faqat bugun, 'all' barchasi.",
                },
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
    }


def _list_meetings_tool() -> dict[str, Any]:
    return {
        "name": "list_meetings",
        "description": (
            "Egasining rejalashtirilgan uchrashuvlarini (Meet havolalari bilan) "
            "ko'rsatish. 'meetinglarim', 'uchrashuvlarim', 'meetlarim' kabi "
            "so'rovlarda. / List the owner's scheduled meetings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "all"],
                    "description": "'today' faqat bugun, 'all' barchasi.",
                },
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
    }


def _add_important_date_tool() -> dict[str, Any]:
    return {
        "name": "add_important_date",
        "description": (
            "Muhim sana yoki tug'ilgan kunni saqlash, oldindan eslatma bilan. "
            "'5-avgust Alining tug'ilgan kuni', 'pasport muddati 12-dekabr', "
            "'sug'urta 3-martda tugaydi', 'mashina tex ko'rik 20-iyun'. Oy/kunni "
            "raqamga aylantiring (5-avgust => month=8, day=5). / Save an important "
            "date / birthday with day-before reminders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Sana nomi / what the date is.",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "birthday",
                        "document",
                        "payment",
                        "travel",
                        "health",
                        "other",
                    ],
                    "description": (
                        "Turi: tug'ilgan kun=birthday, hujjat/pasport/sug'urta="
                        "document, to'lov=payment, safar=travel, salomatlik/doktor="
                        "health, boshqa=other."
                    ),
                },
                "month": {"type": "integer", "description": "Oy 1-12 / month."},
                "day": {"type": "integer", "description": "Kun 1-31 / day of month."},
                "year": {
                    "type": ["integer", "null"],
                    "description": "Yil (faqat bir martalik sana uchun) yoki null.",
                },
                "yearly": {
                    "type": "boolean",
                    "description": "Har yili takrorlanadimi / repeats yearly.",
                },
                "remind_days_before": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Necha kun oldin eslatish (masalan [7,1]).",
                },
            },
            "required": [
                "title",
                "category",
                "month",
                "day",
                "year",
                "yearly",
                "remind_days_before",
            ],
            "additionalProperties": False,
        },
    }


def _list_important_dates_tool() -> dict[str, Any]:
    return {
        "name": "list_important_dates",
        "description": (
            "Saqlangan muhim sanalar/tug'ilgan kunlar ro'yxatini ko'rsatish. "
            "'muhim sanalarim', 'tug'ilgan kunlar', 'qanday sanalarim bor'. / List "
            "saved important dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Necha kun oldinga qarash (standart 365).",
                },
            },
            "required": ["days"],
            "additionalProperties": False,
        },
    }


def _log_decision_tool() -> dict[str, Any]:
    return {
        "name": "log_decision",
        "description": (
            "Egasi qabul qilgan QARORni jurnalga yozish. 'bugun qaror qildim: ...', "
            "'qaror: ...', 'shunday qaror qabul qildim'. / Record a personal "
            "decision in the journal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Qaror matni / the decision.",
                },
                "tag": {
                    "type": ["string", "null"],
                    "description": "Ixtiyoriy yorliq (masalan 'loyiha') yoki null.",
                },
            },
            "required": ["text", "tag"],
            "additionalProperties": False,
        },
    }


def _list_decisions_tool() -> dict[str, Any]:
    return {
        "name": "list_decisions",
        "description": (
            "Egasining so'nggi qarorlari arxivini ko'rsatish. 'qarorlarim', "
            "'qaror arxivi', 'qanday qarorlar qabul qilganman'. / List recent "
            "journalled decisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Nechta ko'rsatilsin (standart 20) / how many.",
                },
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    }


def _list_reminders_tool() -> dict[str, Any]:
    return {
        "name": "list_reminders",
        "description": (
            "Egasining eslatmalari ro'yxatini ko'rsatish (bir martalik + takroriy). "
            "'eslatmalarim', 'eslatmalar ro'yxati', 'qanaqa eslatmalarim bor'. / "
            "List the owner's active reminders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Nechta ko'rsatilsin (standart 50) / how many.",
                },
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    }


def _list_emails_tool() -> dict[str, Any]:
    return {
        "name": "list_emails",
        "description": (
            "Gmail'dagi muhim / o'qilmagan xatlarni ko'rsatish. 'emaillarim', "
            "'muhim xatlar', 'pochtamni tekshir', 'o'qilmagan xatlar'. / Show "
            "important / unread Gmail messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Nechta xat ko'rsatilsin (standart 5) / how many.",
                },
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
    }


def _save_to_notion_tool() -> dict[str, Any]:
    return {
        "name": "save_to_notion",
        "description": (
            "Eslatma yoki rejani Notion'ga saqlash. 'Notion'ga saqla: ...', "
            "'Notion'ga yoz: ...', 'buni Notionga qo'sh'. / Save a note or plan "
            "to Notion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Saqlanadigan matn / the note text.",
                },
                "title": {
                    "type": ["string", "null"],
                    "description": "Ixtiyoriy sarlavha yoki null / optional title.",
                },
            },
            "required": ["text", "title"],
            "additionalProperties": False,
        },
    }


def _show_calendar_tool() -> dict[str, Any]:
    return {
        "name": "show_calendar",
        "description": (
            "Google Calendar'dagi voqealarni ko'rsatish. 'kalendar', 'kalendarim', "
            "'taqvim', 'bu haftagi kalendar', 'bugungi kalendar'. / Show the "
            "owner's Google Calendar events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["today", "week"],
                    "description": "'today' faqat bugun, 'week' bu hafta (standart).",
                },
            },
            "required": ["scope"],
            "additionalProperties": False,
        },
    }


def build_tools() -> list[dict[str, Any]]:
    """Return the full list of Anthropic tool definitions (fresh copies)."""

    return [
        _send_message_tool(),
        _schedule_message_tool(),
        _create_reminder_tool(),
        _create_promise_tool(),
        _assign_task_with_followup_tool(),
        _schedule_meeting_tool(),
        _find_free_slots_tool(),
        _add_finance_tool(),
        _get_digest_tool(),
        _cancel_item_tool(),
        _list_contacts_tool(),
        _list_finance_tool(),
        _list_agenda_tool(),
        _list_meetings_tool(),
        _add_important_date_tool(),
        _list_important_dates_tool(),
        _log_decision_tool(),
        _list_decisions_tool(),
        _list_reminders_tool(),
        _list_emails_tool(),
        _save_to_notion_tool(),
        _show_calendar_tool(),
    ]


# Eagerly built module-level list for convenient import/inspection.
ANTHROPIC_TOOLS: list[dict[str, Any]] = build_tools()
