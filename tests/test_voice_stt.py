"""Voice speech-to-text tests: Gemini fallback + provider availability."""

from __future__ import annotations

from app.services.voice_service import VoiceService


# ── fake Gemini client returning a fixed transcription ────────────────────────
class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _Models:
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate_content(self, *, model, contents, config):
        return _Resp(self._text)


def _fake_gemini(text: str):
    client = type("C", (), {})()
    client.aio = type("Aio", (), {"models": _Models(text)})()
    return client


def test_can_transcribe_with_either_provider(settings):
    gemini_only = settings.model_copy(
        update={"elevenlabs_api_key": "", "gemini_api_key": "k"}
    )
    el_only = settings.model_copy(
        update={"elevenlabs_api_key": "k", "gemini_api_key": ""}
    )
    neither = settings.model_copy(
        update={"elevenlabs_api_key": "", "gemini_api_key": "", "gemini_use_vertex": False}
    )
    assert VoiceService(gemini_only).can_transcribe() is True
    assert VoiceService(el_only).can_transcribe() is True
    assert VoiceService(neither).can_transcribe() is False


async def test_transcribe_falls_back_to_gemini(settings, tmp_path, monkeypatch):
    s = settings.model_copy(update={"elevenlabs_api_key": "", "gemini_api_key": "fake"})
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS-fake-audio-bytes")

    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini("ertaga soat 9 da yig'ilishni esla"),
    )
    text = await VoiceService(s).transcribe(str(audio), mime_type="audio/ogg")
    assert text == "ertaga soat 9 da yig'ilishni esla"


async def test_transcribe_no_provider_returns_empty(settings, tmp_path, monkeypatch):
    s = settings.model_copy(
        update={"elevenlabs_api_key": "", "gemini_api_key": "", "gemini_use_vertex": False}
    )
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client", lambda _s: None
    )
    assert await VoiceService(s).transcribe(str(audio)) == ""
