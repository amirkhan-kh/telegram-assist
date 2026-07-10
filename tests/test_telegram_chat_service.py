from __future__ import annotations

from app.services.telegram_chat_service import _matches_media


def test_private_chat_mp4_document_matches_video():
    class _File:
        mime_type = "video/mp4"

    class _Message:
        media = object()
        document = object()
        file = _File()

    msg = _Message()
    assert _matches_media(msg, "video") is True
    assert _matches_media(msg, "document") is False
    assert _matches_media(msg, "any") is True


def test_private_chat_plain_document_does_not_match_video():
    class _File:
        mime_type = "application/pdf"

    class _Message:
        media = object()
        document = object()
        file = _File()

    msg = _Message()
    assert _matches_media(msg, "video") is False
    assert _matches_media(msg, "document") is True
