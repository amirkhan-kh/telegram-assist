"""Voice text-to-speech tests: provider availability + Gemini TTS pipeline."""

from __future__ import annotations

import os
from types import SimpleNamespace

from app.services import audio_service
from app.services.voice_service import VoiceService


# ── fake Gemini client returning canned TTS audio (inline PCM) ────────────────
def _tts_response(pcm: bytes, *, mime: str = "audio/L16;rate=24000") -> SimpleNamespace:
    inline = SimpleNamespace(data=pcm, mime_type=mime)
    part = SimpleNamespace(inline_data=inline)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


class _Models:
    def __init__(self, response: SimpleNamespace) -> None:
        self._response = response

    async def generate_content(self, *, model, contents, config):
        return self._response


def _fake_gemini(response: SimpleNamespace):
    client = type("C", (), {})()
    client.aio = type("Aio", (), {"models": _Models(response)})()
    return client


# ── available() now means ffmpeg + (ElevenLabs OR Gemini) ─────────────────────
def test_available_needs_ffmpeg_and_a_provider(settings, monkeypatch):
    monkeypatch.setattr(audio_service, "ensure_ffmpeg", lambda: True)
    gemini_only = settings.model_copy(
        update={"elevenlabs_api_key": "", "elevenlabs_voice_id": None, "gemini_api_key": "k"}
    )
    neither = settings.model_copy(
        update={"elevenlabs_api_key": "", "gemini_api_key": "", "gemini_use_vertex": False}
    )
    assert VoiceService(gemini_only).available() is True
    assert VoiceService(neither).available() is False


def test_available_false_without_ffmpeg(settings, monkeypatch):
    monkeypatch.setattr(audio_service, "ensure_ffmpeg", lambda: False)
    gemini = settings.model_copy(update={"gemini_api_key": "k"})
    assert VoiceService(gemini).available() is False


# ── _extract_pcm parsing ───────────────────────────────────────────────────────
def test_extract_pcm_reads_data_and_rate():
    data, rate = VoiceService._extract_pcm(_tts_response(b"\x00\x01", mime="audio/L16;rate=16000"))
    assert data == b"\x00\x01"
    assert rate == 16000


def test_extract_pcm_empty_response():
    data, rate = VoiceService._extract_pcm(SimpleNamespace(candidates=[]))
    assert data is None
    assert rate == 24000  # documented default


# ── Gemini TTS pipeline ────────────────────────────────────────────────────────
async def test_tts_gemini_no_client_returns_none(settings, monkeypatch):
    monkeypatch.setattr(audio_service, "ensure_ffmpeg", lambda: True)
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client", lambda _s: None
    )
    s = settings.model_copy(update={"elevenlabs_api_key": "", "gemini_api_key": "k"})
    assert await VoiceService(s).tts_to_voice_note("salom", out_dir="/tmp/x") is None


async def test_tts_to_voice_note_produces_ogg_via_gemini(settings, tmp_path, monkeypatch):
    if not audio_service.ensure_ffmpeg():
        import pytest

        pytest.skip("ffmpeg not installed in this environment")

    # 0.1s of 16-bit mono silence is enough for ffmpeg to transcode.
    pcm = b"\x00\x00" * 2400
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini(_tts_response(pcm)),
    )
    s = settings.model_copy(update={"elevenlabs_api_key": "", "gemini_api_key": "k"})
    ogg = await VoiceService(s).tts_to_voice_note("Assalomu alaykum", out_dir=str(tmp_path))
    assert ogg is not None
    assert os.path.exists(ogg)
    assert ogg.endswith(".ogg")
    assert os.path.getsize(ogg) > 0


def test_preprocess_for_stt_outputs_16k_mono_wav(tmp_path):
    """Inbound denoise yields a 16 kHz mono WAV ready for transcription."""
    import wave

    if not audio_service.ensure_ffmpeg():
        import pytest

        pytest.skip("ffmpeg not installed in this environment")

    src = tmp_path / "in.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x10" * 7200)  # ~0.3s of low-level samples

    out = audio_service.preprocess_for_stt(str(src), out_dir=str(tmp_path))
    assert out.endswith(".wav") and os.path.exists(out)
    with wave.open(out, "rb") as r:
        assert r.getframerate() == 16000
        assert r.getnchannels() == 1
        assert r.getnframes() > 0
