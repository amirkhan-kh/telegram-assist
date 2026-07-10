"""Chirp 2 (Speech-to-Text V2) STT path: primacy, hints, and safe fallback.

The real ``google-cloud-speech`` SDK is never exercised here — the blocking
``google_stt.transcribe_chirp_sync`` is monkeypatched, so these tests run offline
and assert only the wiring inside :class:`VoiceService`.
"""

from __future__ import annotations

import asyncio
import wave

from app.services.voice_service import VoiceService


# ── minimal fake Gemini client (reused fallback shape) ────────────────────────
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


class _SlowModels:
    async def generate_content(self, *, model, contents, config):
        await asyncio.sleep(0.05)
        return _Resp("Ha, shunga salom berib o'tdik.")


def _slow_gemini():
    client = type("C", (), {})()
    client.aio = type("Aio", (), {"models": _SlowModels()})()
    return client


def _chirp_settings(settings, **extra):
    """Settings with Chirp enabled + a resolvable project, ElevenLabs off."""
    update = {
        "stt_use_chirp": True,
        "google_cloud_project": "demo-project",
        "elevenlabs_api_key": "",
        "gemini_api_key": "fake",
    }
    update.update(extra)
    return settings.model_copy(update=update)


def _write_audio(tmp_path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF-fake-wav-bytes")
    return str(audio)


def _write_wav(tmp_path, seconds: float = 2.5):
    audio = tmp_path / "real_voice.wav"
    rate = 16000
    frames = int(rate * seconds)
    with wave.open(str(audio), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * frames)
    return str(audio)


async def test_chirp_used_when_enabled(settings, tmp_path, monkeypatch):
    """When enabled, Chirp transcribes and Gemini is never consulted."""
    captured: dict = {}

    def fake_chirp(s, audio_bytes, hint_names=None):
        captured["hint_names"] = hint_names
        captured["audio_bytes"] = audio_bytes
        return "Asadbekka qo'ng'iroq qil"

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync", fake_chirp
    )

    def _no_gemini(_s):
        raise AssertionError("Gemini must not be called when Chirp succeeds")

    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client", _no_gemini
    )

    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(
        _write_audio(tmp_path), hint_names=["Asadbek", "Dilnoza"]
    )
    assert text == "Asadbekka qo'ng'iroq qil"
    assert captured["hint_names"] == ["Asadbek", "Dilnoza"]
    assert captured["audio_bytes"] == b"RIFF-fake-wav-bytes"


async def test_suspicious_chirp_is_verified_and_repaired(
    settings, tmp_path, monkeypatch
):
    """Short contact-hallucination transcripts are audited with Gemini."""

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync",
        lambda s, b, hint_names=None: "Shohjahon aka yuksalish,Buva",
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini("Ha, shunga salom berib o'tdik."),
    )

    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == "Ha, shunga salom deb yubor."


async def test_command_like_gemini_beats_chirp_fragment(settings, tmp_path, monkeypatch):
    """When Gemini hears the real command and Chirp returns a fragment, use Gemini."""

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync",
        lambda s, b, hint_names=None: "Asadbekka Kilent",
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini("Joni, Asadbekka salom."),
    )

    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == "Joni, Asadbekka salom."


async def test_suspicious_chirp_rejects_bad_gemini_audit(
    settings, tmp_path, monkeypatch
):
    """Do not route contact-list-like STT output when Gemini audit is garbage."""

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync",
        lambda s, b, hint_names=None: "M.B M.B,Guray,gulzora s,Dadam",
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini(" ".join(["N. B. G."] * 80)),
    )

    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == ""


async def test_chirp_rejects_long_hint_leak_for_short_audio(
    settings, tmp_path, monkeypatch
):
    """A short clip cannot be a long comma-separated contact/hint list."""

    leaked = (
        "Holis Holam,Ilhomaka Oglilari,Shohobiddin,Ulugbek,M S,"
        "Shohjahon aka yuksalish,Buva,Dom Qoshni,unga salom deb yubor,"
        "shu odamga salom deb yubor,Ihtiyor,yozishmalarni xulosa qil,"
        "Shahmardon Shopir,ibrhmv_zz Ziyodullo,Emilbek,Farhot Domla,"
        "gulzora s,Nasibullo,Nasibullo,Nurilloh Saltarow,Asya Almaty,"
        "Mashhur Togam,English"
    )
    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync",
        lambda s, b, hint_names=None: leaked,
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini(" ".join(["N. B. G."] * 80)),
    )

    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(_write_wav(tmp_path, seconds=2.5))
    assert text == ""


async def test_suspicious_chirp_verify_timeout_is_not_routed(
    settings, tmp_path, monkeypatch
):
    """A stuck verifier must fail closed instead of sending to a wrong contact."""

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync",
        lambda s, b, hint_names=None: "M.B M.B,Guray,gulzora s,Dadam",
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _slow_gemini(),
    )

    s = _chirp_settings(settings, stt_verify_timeout_seconds=0.01)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == ""


async def test_chirp_empty_falls_back_to_gemini(settings, tmp_path, monkeypatch):
    """An empty Chirp result degrades to the Gemini fallback."""
    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync",
        lambda s, b, hint_names=None: "",
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini("ertaga yig'ilish"),
    )
    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == "ertaga yig'ilish"


async def test_chirp_error_falls_back_to_gemini(settings, tmp_path, monkeypatch):
    """A Chirp exception (e.g. API/region error) degrades to Gemini, never crashes."""

    def boom(s, b, hint_names=None):
        raise RuntimeError("chirp region rejected uz-UZ")

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync", boom
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini("fallback matni"),
    )
    s = _chirp_settings(settings)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == "fallback matni"


async def test_chirp_skipped_when_flag_off(settings, tmp_path, monkeypatch):
    """With STT_USE_CHIRP=false the Chirp path is not even attempted."""

    def must_not_run(s, b, hint_names=None):
        raise AssertionError("Chirp must not run when the flag is off")

    monkeypatch.setattr(
        "app.integrations.google_stt.transcribe_chirp_sync", must_not_run
    )
    monkeypatch.setattr(
        "app.integrations.gemini_client.get_gemini_client",
        lambda _s: _fake_gemini("gemini ishladi"),
    )
    s = _chirp_settings(settings, stt_use_chirp=False)
    text = await VoiceService(s).transcribe(_write_audio(tmp_path))
    assert text == "gemini ishladi"


def test_chirp_not_ready_without_project(settings):
    """Chirp needs a resolvable GCP project even when the flag is on."""
    on_no_project = settings.model_copy(
        update={
            "stt_use_chirp": True,
            "google_cloud_project": None,
            "google_application_credentials": None,
        }
    )
    assert VoiceService(on_no_project)._chirp_ready() is False
    off = settings.model_copy(update={"stt_use_chirp": False})
    assert VoiceService(off)._chirp_ready() is False
