"""Deterministic NLU shortcuts for raw phone-number send commands."""

from __future__ import annotations

from app.brain.intents import DeliveryMode
from app.services.nlu_service import _direct_phone_send


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
