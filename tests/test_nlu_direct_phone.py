"""Deterministic NLU shortcuts for raw phone-number send commands."""

from __future__ import annotations

from app.brain.intents import DeliveryMode
from app.services.nlu_service import (
    _direct_contact_read,
    _direct_contact_send,
    _direct_meeting_sequence,
    _direct_phone_send,
)


def test_direct_phone_send_preserves_compact_uzbek_number():
    routed = _direct_phone_send('+998909902440 raqamiga "Salom" deb xabar yubor.')

    assert routed is not None
    assert routed.name == "send_message"
    assert routed.params.recipient_name == "+998909902440"
    assert routed.params.content == "Salom"
    assert routed.params.delivery == DeliveryMode.ask


def test_direct_phone_send_preserves_spaced_phone_digits():
    routed = _direct_phone_send('+99 890 840 84 07 raqamiga "Salom" deb xabar yubor.')

    assert routed is not None
    assert routed.params.recipient_name == "+998908408407"
    assert routed.params.content == "Salom"


def test_direct_phone_send_handles_misheard_raqamiga_suffix():
    routed = _direct_phone_send("+998916566533 raqmiga salom deb xabar yubor")

    assert routed is not None
    assert routed.params.recipient_name == "+998916566533"
    assert routed.params.content == "salom"


def test_direct_phone_send_ignores_meta_log_check_text():
    assert (
        _direct_phone_send(
            'log ni tekshir +99 890 840 84 07 raqamiga "Salom" deb xabar yubor.'
        )
        is None
    )


def test_direct_phone_send_extracts_unquoted_content_and_delivery():
    routed = _direct_phone_send(
        "+998 90 990 24 40 ga ovozli salom deb xabar yubor"
    )

    assert routed is not None
    assert routed.params.recipient_name == "+998909902440"
    assert routed.params.content == "salom"
    assert routed.params.delivery == DeliveryMode.voice


def test_direct_phone_send_ignores_non_send_phone_text():
    assert _direct_phone_send("+998909902440 kimning raqami?") is None


def test_direct_contact_send_routes_common_immediate_command():
    routed = _direct_contact_send("Asadbekka salom deb xabar yubor")

    assert routed is not None
    assert routed.name == "send_message"
    assert routed.params.recipient_name == "Asadbek"
    assert routed.params.content == "salom"
    assert routed.params.delivery == DeliveryMode.ask


def test_direct_contact_send_ignores_assistant_prefix():
    routed = _direct_contact_send("Joni, Asadbekka salom deb xabar yubor")

    assert routed is not None
    assert routed.params.recipient_name == "Asadbek"
    assert routed.params.content == "salom"


def test_direct_contact_send_handles_separated_suffix_and_voice():
    routed = _direct_contact_send("Asadbek aka ga ovozli salom yo'lla")

    assert routed is not None
    assert routed.name == "send_message"
    assert routed.params.recipient_name == "Asadbek"
    assert routed.params.content == "salom"
    assert routed.params.delivery == DeliveryMode.voice


def test_direct_contact_send_leaves_scheduled_commands_to_llm():
    assert _direct_contact_send("Asadbekka ertaga soat 9 da salom yubor") is None


def test_direct_contact_read_routes_last_incoming_text_message():
    routed = _direct_contact_read("Asadbek, menga yuborgan oxirgi xabarni yubor.")

    assert routed is not None
    assert routed.name == "get_chat_messages"
    assert routed.params.contact_name == "Asadbek"
    assert routed.params.direction == "incoming"
    assert routed.params.limit == 1


def test_direct_contact_read_ignores_polite_prefix():
    routed = _direct_contact_read(
        "Iltimos Joni, Asadbek, menga yuborgan oxirgi xabarni yubor."
    )

    assert routed is not None
    assert routed.params.contact_name == "Asadbek"


def test_direct_contact_read_routes_incoming_video_media():
    routed = _direct_contact_read(
        "Nizomiddinov Izzatillo menga yuborgan oxirgi 3 ta videoni tashlab ber"
    )

    assert routed is not None
    assert routed.name == "search_chat_media"
    assert routed.params.contact_name == "Nizomiddinov Izzatillo"
    assert routed.params.media_type == "video"
    assert routed.params.direction == "incoming"
    assert routed.params.limit == 3


def test_direct_meeting_sequence_routes_ordered_actions():
    routed = _direct_meeting_sequence(
        "Asadbek bilan ertaga soat 10:00 da miting belgila va hozir ogohlantir "
        "va 12:00 da bir marta, 15:00 da bir marta xabardor qil"
    )

    assert routed is not None
    assert [item.name for item in routed] == [
        "schedule_meeting",
        "send_message",
        "schedule_message",
        "schedule_message",
    ]
    assert routed[0].params.notify_target_name == "Asadbek"
    assert routed[1].params.delivery == DeliveryMode.text
    assert routed[2].params.when.raw == "ertaga soat 12:00"
    assert routed[3].params.when.raw == "ertaga soat 15:00"
