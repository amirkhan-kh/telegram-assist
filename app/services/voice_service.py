"""VoiceService — text-to-speech / speech-to-text glued to Telegram voice notes.

TTS: synthesize speech from text and transcode it to Ogg/Opus, returning the
ogg path. Two providers are supported, preferred in this order:
  * ElevenLabs (the owner's *cloned* voice) when a key + voice id are set;
  * Gemini's prebuilt voices (free, no clone) otherwise — this is the default.
STT: transcribe an audio file to text (ElevenLabs Scribe, else Gemini).

All blocking SDK / ffmpeg work runs through ``asyncio.to_thread``. The ElevenLabs
SDK surface has drifted across versions, so those calls are made defensively.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.integrations import google_stt
from app.integrations.elevenlabs_client import get_elevenlabs
from app.logging_conf import get_logger
from app.services import audio_service

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)

# System persona for the Gemini transcriber: built for noisy, real-world audio
# (a moving car, wind, street). It tells the model to lock onto the nearest
# speaker, ignore background noise, and recover noise-masked words from context
# rather than emit gibberish — the goal is 95%+ usable, clean Uzbek text.
_STT_SYSTEM = (
    "Siz juda yuqori shovqinli muhitda (shamol, mashina, ko'cha shovqini, "
    "signalizatsiya) yozilgan o'zbekcha audiolar bilan ishlovchi professional "
    "nutqni aniqlash (STT) tizimisiz. Fon shovqinlariga mutlaqo e'tibor "
    "bermang — faqat mikrofonga eng yaqin, asosiy so'zlovchining nutqini "
    "tahlil qiling. Agar ba'zi so'zlar shovqin ostida qolib noaniq eshitilsa, "
    "gapning umumiy kontekstidan kelib chiqib, mantiqan eng to'g'ri o'zbekcha "
    "so'z bilan to'ldiring; tasodifiy tovushni so'z deb yozmang. Maqsad — "
    "foydalanuvchi aytmoqchi bo'lgan gapni 95%+ aniqlikda, toza matn "
    "ko'rinishida qaytarish."
)

_STT_ACTION_RE = re.compile(
    r"\b(?:yubor|jo['‘’ʻʼ`]?nat|yoz|ayt|eslat|qil|top|qidir|ko['‘’ʻʼ`]?rsat|"
    r"chiqar|xulosa|ob[-\s]?havo|havo|reja|kalendar|taqvim|qarz|kontakt|"
    r"xabar|salom|rasm|video|dokument|fayl|chat|yozishma)\b",
    re.IGNORECASE,
)
_STT_COMMANDISH_RE = re.compile(
    r"\b(?:ga|ka|qa|menga|unga|shunga|hozir|ertaga|soat|xabar|salom|yubor|"
    r"ayt|yoz|miting|meeting|uchrashuv)\b",
    re.IGNORECASE,
)
_STT_SHORT_REF_RE = re.compile(
    r"\b(?:ha|shunga|shuga|unga|shu\s+odamga|shu\s+kontaktga)\b",
    re.IGNORECASE,
)
_STT_SALOM_MISHEAR_RE = re.compile(
    r"\bsalom\s+berib\s+(?:o['‘’ʻʼ`]?tdik|utdik|o['‘’ʻʼ`]?tdi|utdi)\b",
    re.IGNORECASE,
)
_STT_CONTACT_HALLUCINATION_RE = re.compile(
    r"\b(?:aka|opa|buva|bobo|yuksalish)\b", re.IGNORECASE
)
_STT_INITIALS_LIKE_RE = re.compile(r"\b[A-Za-z]\.\s*[A-Za-z]\b")
_STT_TOKEN_RE = re.compile(r"[A-Za-zʻʼ‘’`']+")
_STT_COMMAND_HINTS = (
    "Agar audioda 'shunga salom deb yubor', 'shu odamga salom deb yubor', "
    "'unga salom deb yubor' kabi buyruq eshitilsa, aynan shunday yoz. "
    "'salom deb yubor' iborasini 'salom berib o'tdik' deb o'zgartirma."
)
_STT_HINT_LEAK_PHRASES = (
    "shunga salom deb yubor",
    "shu odamga salom deb yubor",
    "shu kontaktga salom deb yubor",
    "unga salom deb yubor",
    "yozishmalarni xulosa qil",
    "oxirgi xabarni yubor",
    "oxirgi rasmni yubor",
)


class VoiceService:
    """Owner-voice text-to-speech and speech-to-text via ElevenLabs."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: object | None = None
        self._client_built = False

    # ── client (lazy) ─────────────────────────────────────────────────────
    def _get_client(self) -> object | None:
        if not self._client_built:
            self._client = get_elevenlabs(self.settings)
            self._client_built = True
        return self._client

    def available(self) -> bool:
        """True when a voice note can be produced (ffmpeg + a TTS provider).

        TTS works through either ElevenLabs (cloned voice; key + voice id) or
        Gemini's built-in prebuilt voices (free, no clone needed). Both paths
        need ffmpeg to transcode to Telegram's Ogg/Opus.
        """
        if not audio_service.ensure_ffmpeg():
            return False
        return self._elevenlabs_ready() or self._gemini_tts_ready()

    def _elevenlabs_ready(self) -> bool:
        """True when ElevenLabs TTS (cloned voice) is fully configured."""
        return bool(
            self.settings.elevenlabs_api_key
            and self.settings.elevenlabs_voice_id
            and self._get_client() is not None
        )

    def _gemini_tts_ready(self) -> bool:
        """True when Gemini TTS can be used (an API key or Vertex is set)."""
        return bool(self.settings.gemini_api_key or self.settings.gemini_use_vertex)

    def can_transcribe(self) -> bool:
        """True when speech-to-text is possible (ElevenLabs Scribe OR Gemini).

        STT needs neither a cloned voice nor ffmpeg — just one provider key.
        (Chirp 2 reuses the Vertex GCP path, already covered below.)
        """
        s = self.settings
        return bool(s.elevenlabs_api_key or s.gemini_api_key or s.gemini_use_vertex)

    def _chirp_ready(self) -> bool:
        """True when Chirp 2 (Speech-to-Text V2) is the chosen primary STT.

        Reuses the Vertex GCP auth: a project id must be resolvable, directly or
        via the service-account file. Disable with ``STT_USE_CHIRP=false``.
        """
        s = self.settings
        if not s.stt_use_chirp:
            return False
        return bool(s.google_cloud_project or s.google_application_credentials)

    # ── TTS ───────────────────────────────────────────────────────────────
    async def tts_to_voice_note(
        self, text: str, *, out_dir: str = "data/media"
    ) -> str | None:
        """Synthesize ``text`` to a Telegram Ogg/Opus voice note.

        Prefers ElevenLabs (cloned voice) when configured, otherwise uses
        Gemini's prebuilt voice. Returns the ogg path, or ``None`` when no
        provider/ffmpeg is available.
        """
        if not audio_service.ensure_ffmpeg():
            logger.info("voice.tts.skipped", reason="no_ffmpeg")
            return None
        if self._elevenlabs_ready():
            try:
                return await asyncio.to_thread(self._tts_sync, text, out_dir)
            except Exception as exc:  # noqa: BLE001 - degrade to Gemini on SDK drift
                logger.warning("voice.tts.elevenlabs_failed", error=str(exc))
        if self._gemini_tts_ready():
            return await self._tts_gemini(text, out_dir)
        logger.info("voice.tts.skipped", reason="no_provider")
        return None

    async def _tts_gemini(self, text: str, out_dir: str) -> str | None:
        """Synthesize ``text`` with a Gemini prebuilt voice -> Ogg/Opus path."""
        from app.integrations.gemini_client import get_gemini_client

        gemini = get_gemini_client(self.settings)
        if gemini is None:
            logger.info("voice.tts.skipped", reason="no_gemini")
            return None
        try:
            from google.genai import types

            # A natural-language style directive steers delivery (tone, pace)
            # without being spoken aloud; plain text when no style is configured.
            style = (self.settings.gemini_tts_style or "").strip()
            contents = f"{style}\n\n{text}" if style else text
            response = await asyncio.wait_for(
                gemini.aio.models.generate_content(
                    model=self.settings.gemini_tts_model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=self.settings.gemini_tts_voice
                                )
                            )
                        ),
                    ),
                ),
                timeout=30,
            )
            pcm, sample_rate = self._extract_pcm(response)
            if not pcm:
                logger.warning("voice.tts.gemini_empty")
                return None
            return await asyncio.to_thread(
                audio_service.pcm_to_telegram_voice,
                pcm,
                sample_rate=sample_rate,
                out_dir=out_dir,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the send path
            logger.warning("voice.tts.gemini_failed", error=str(exc))
            return None

    @staticmethod
    def _extract_pcm(response: Any) -> tuple[bytes | None, int]:
        """Pull raw PCM bytes + sample rate from a Gemini TTS response.

        The audio rides in ``candidates[].content.parts[].inline_data`` with a
        mime type like ``audio/L16;rate=24000``; the rate is parsed when present.
        """
        sample_rate = 24000
        for cand in getattr(response, "candidates", None) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is None:
                    continue
                mime = getattr(inline, "mime_type", "") or ""
                if "rate=" in mime:
                    try:
                        sample_rate = int(mime.split("rate=")[1].split(";")[0])
                    except (ValueError, IndexError):
                        pass
                data = getattr(inline, "data", None)
                if data:
                    return data, sample_rate
        return None, sample_rate

    def _tts_sync(self, text: str, out_dir: str) -> str:
        """Blocking TTS + transcode. Runs inside ``asyncio.to_thread``."""
        client = self._get_client()
        assert client is not None  # guarded by available()
        os.makedirs(out_dir, exist_ok=True)
        audio = self._call_tts(client, text)
        mp3_path = os.path.join(out_dir, f"tts_{uuid.uuid4().hex}.mp3")
        self._write_audio(audio, mp3_path)
        ogg_path = audio_service.to_telegram_voice(mp3_path, out_dir=out_dir)
        # Best-effort cleanup of the intermediate mp3.
        try:
            os.remove(mp3_path)
        except OSError:
            pass
        return ogg_path

    def _call_tts(self, client: Any, text: str) -> Any:
        """Invoke text_to_speech.convert defensively across SDK versions."""
        tts = getattr(client, "text_to_speech", None)
        if tts is None or not hasattr(tts, "convert"):
            raise AttributeError("ElevenLabs SDK has no text_to_speech.convert")
        return tts.convert(
            text=text,
            voice_id=self.settings.elevenlabs_voice_id,
            model_id=self.settings.elevenlabs_tts_model,
            output_format="mp3_44100_128",
        )

    @staticmethod
    def _write_audio(audio: Any, path: str) -> None:
        """Persist the SDK's audio result (bytes or an iterator of chunks)."""
        with open(path, "wb") as fh:
            if isinstance(audio, bytes | bytearray):
                fh.write(audio)
            else:
                for chunk in audio:
                    if chunk:
                        fh.write(chunk)

    # ── STT ───────────────────────────────────────────────────────────────
    async def transcribe(
        self,
        audio_path: str,
        *,
        language_code: str = "uzb",
        mime_type: str = "audio/ogg",
        hint_names: list[str] | None = None,
    ) -> str:
        """Transcribe ``audio_path`` to text (``""`` on failure).

        Prefers ElevenLabs Scribe when configured; otherwise falls back to
        Gemini's multimodal transcription (free) so voice commands work without
        ElevenLabs. Any speaker (male/female) is supported.

        ``hint_names`` is the owner's known contact names; they are fed to every
        engine (Chirp phrase hints / Gemini prompt) so spoken names are spelled
        exactly as saved (e.g. "Asadbek" never becomes "Asatbek") — the single
        biggest win for voice-command accuracy.

        Engine order: Chirp 2 (primary, when enabled) -> ElevenLabs Scribe (when
        a cloned-voice key is set) -> Gemini multimodal (free fallback). Each
        failure degrades to the next so a voice command is never lost.
        """
        # 1. Chirp 2 (Google Cloud Speech-to-Text V2) — primary ASR engine.
        if self._chirp_ready():
            try:
                data = await asyncio.to_thread(Path(audio_path).read_bytes)
                text = await asyncio.to_thread(
                    google_stt.transcribe_chirp_sync, self.settings, data, hint_names
                )
                if text:
                    text = self._repair_stt_text(text)
                    if self._should_verify_chirp(audio_path, text):
                        gemini_text = self._repair_stt_text(
                            await self._verify_chirp_with_gemini(
                                audio_path, mime_type, hint_names
                            )
                        )
                        chosen = self._choose_verified_stt(text, gemini_text)
                        logger.info(
                            "voice.stt.chirp_verified",
                            chirp=text,
                            gemini=gemini_text,
                            chosen=chosen,
                        )
                        return chosen
                    return text
            except Exception as exc:  # noqa: BLE001 - degrade to ElevenLabs/Gemini
                logger.warning("voice.stt.chirp_failed", error=str(exc))
        # 2. ElevenLabs Scribe — when a cloned-voice key is configured.
        client = self._get_client()
        if client is not None:
            try:
                text = await asyncio.to_thread(
                    self._transcribe_sync, client, audio_path, language_code
                )
                if text:
                    return text
            except Exception as exc:  # noqa: BLE001 - degrade to the Gemini path
                logger.warning("voice.stt.elevenlabs_failed", error=str(exc))
        # 3. Gemini multimodal — free fallback.
        return await self._transcribe_gemini(audio_path, mime_type, hint_names)

    @staticmethod
    def _stt_prompt(hint_names: list[str] | None) -> str:
        """Build the Gemini transcription prompt, biased toward known names."""
        prompt = (
            "Sen o'zbek tili (lotin) bo'yicha eng aniq transkripsiya tizimisan. "
            "Ushbu ovozli xabarni SO'ZMA-SO'Z, hech narsa qo'shmasdan va "
            "tushirmasdan matnga o'gir. Qoidalar:\n"
            "- Faqat o'zbek lotin alifbosida yoz; o' va g' harflarini to'g'ri "
            "qo'lla.\n"
            "- Ismlar, familiyalar va joy nomlarini to'liq va to'g'ri yoz.\n"
            "- Raqam, vaqt va sanalarni eshitilganidek aniq saqla, buzma.\n"
            "- Tarjima qilma, qisqartirma, izoh berma, savol berma.\n"
            "- Fon shovqini, duduqlanish va tasodifiy tovushlarni e'tiborsiz "
            "qoldir.\n"
            "- Faqat aytilgan matnni qaytar — sarlavha, tirnoq yoki qo'shimcha "
            "so'z qo'shma.\n"
            f"- {_STT_COMMAND_HINTS}"
        )
        # Bias the model toward exact contact spellings. Cap the list so the
        # prompt stays bounded even when the owner has a large phonebook.
        names = [n.strip() for n in (hint_names or []) if n and n.strip()]
        if names:
            joined = ", ".join(names[:200])
            prompt += (
                " Quyidagilar foydalanuvchining kontaktlari — agar audioda "
                "shulardan biriga o'xshash ism eshitilsa, AYNAN shu "
                f"ko'rinishda yoz: {joined}."
            )
        return prompt

    async def _transcribe_gemini(
        self,
        audio_path: str,
        mime_type: str,
        hint_names: list[str] | None = None,
    ) -> str:
        """Transcribe audio via Gemini (multimodal). Returns ``""`` on failure."""
        from app.integrations.gemini_client import get_gemini_client

        gemini = get_gemini_client(self.settings)
        if gemini is None:
            logger.info("voice.stt.skipped", reason="no_provider")
            return ""
        try:
            from google.genai import types

            data = await asyncio.to_thread(Path(audio_path).read_bytes)
            response = await gemini.aio.models.generate_content(
                model=self.settings.gemini_stt_model,
                contents=[
                    types.Part.from_bytes(data=data, mime_type=mime_type or "audio/ogg"),
                    self._stt_prompt(hint_names),
                ],
                config=types.GenerateContentConfig(
                    temperature=0,
                    system_instruction=_STT_SYSTEM,
                ),
            )
            return (response.text or "").strip()
        except Exception as exc:  # noqa: BLE001 - never crash the voice handler
            logger.warning("voice.stt.gemini_failed", error=str(exc))
            return ""

    async def _verify_chirp_with_gemini(
        self,
        audio_path: str,
        mime_type: str,
        hint_names: list[str] | None = None,
    ) -> str:
        """Run the slower Gemini STT audit with a strict timeout."""
        timeout = float(self.settings.stt_verify_timeout_seconds)
        if timeout <= 0:
            timeout = 8.0
        try:
            return await asyncio.wait_for(
                self._transcribe_gemini(audio_path, mime_type, hint_names),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("voice.stt.chirp_verify_timeout", timeout=timeout)
            return ""

    def _should_verify_chirp(self, audio_path: str, text: str) -> bool:
        """True when a Chirp transcript is short enough to deserve Gemini audit."""
        if not self.settings.stt_verify_chirp:
            return False
        cleaned = (text or "").strip()
        if not cleaned:
            return False
        if self._looks_like_hint_leak(cleaned):
            return True
        if self._too_long_for_audio(audio_path, cleaned):
            return True
        if len(cleaned) > self.settings.stt_verify_max_chars:
            return False
        if not self._has_action_signal(cleaned):
            return True
        # Contact-name-looking fragments with punctuation are a common failure
        # mode for short commands ("shunga..." -> "Shohjahon aka...").
        if "," in cleaned and _STT_CONTACT_HALLUCINATION_RE.search(cleaned):
            return True
        return False

    @staticmethod
    def _has_action_signal(text: str) -> bool:
        return _STT_ACTION_RE.search(text or "") is not None

    def _choose_verified_stt(self, chirp_text: str, gemini_text: str) -> str:
        """Pick the safer transcript after a suspicious Chirp result."""
        chirp = self._repair_stt_text(chirp_text)
        gemini = self._repair_stt_text(gemini_text)
        chirp_suspicious = (
            self._looks_like_contact_hallucination(chirp)
            or self._looks_like_hint_leak(chirp)
        )
        if not gemini or self._looks_like_bad_verification(gemini):
            if chirp_suspicious:
                return ""
            return chirp
        if self._has_action_signal(gemini) and not self._has_action_signal(chirp):
            return gemini
        if self._looks_more_like_command(gemini, chirp):
            return gemini
        if chirp_suspicious:
            return gemini
        return chirp

    @staticmethod
    def _looks_more_like_command(gemini: str, chirp: str) -> bool:
        """Prefer Gemini when it forms a plausible command and Chirp is a fragment."""
        if not gemini or not chirp:
            return False
        if len(gemini) < 8:
            return False
        gemini_hits = len(_STT_COMMANDISH_RE.findall(gemini))
        chirp_hits = len(_STT_COMMANDISH_RE.findall(chirp))
        if gemini_hits < 2:
            return False
        # Example from production: Chirp "Asadbekka Kilent", Gemini
        # "Joni, Asadbekka salom." The latter is the real command; the former
        # is a contact-like fragment plus a hallucinated word.
        if chirp_hits <= 1 and len(chirp.split()) <= 4:
            return True
        return gemini_hits >= chirp_hits + 2

    @staticmethod
    def _looks_like_contact_hallucination(text: str) -> bool:
        if not text:
            return False
        return (
            len(text) <= 90
            and _STT_ACTION_RE.search(text) is None
            and (
                _STT_CONTACT_HALLUCINATION_RE.search(text) is not None
                or "," in text
                or _STT_INITIALS_LIKE_RE.search(text) is not None
            )
        )

    def _too_long_for_audio(self, audio_path: str, text: str) -> bool:
        """Detect impossible transcripts, e.g. a 2s clip becoming 300 chars."""
        duration = self._audio_duration_seconds(audio_path)
        if duration is None or duration <= 0:
            return False
        # Uzbek speech is usually far below this. Keep the ceiling generous so
        # real fast speech passes, but leaked phrase lists from short clips fail.
        max_chars = max(90, int(duration * 32) + 40)
        too_long = len(text) > max_chars
        if too_long:
            logger.warning(
                "voice.stt.chirp_implausible_length",
                chars=len(text),
                duration=round(duration, 2),
                max_chars=max_chars,
            )
        return too_long

    @staticmethod
    def _audio_duration_seconds(audio_path: str) -> float | None:
        """Best-effort duration for preprocessed WAV files."""
        try:
            with wave.open(audio_path, "rb") as fh:
                frames = fh.getnframes()
                rate = fh.getframerate()
                if rate:
                    return frames / float(rate)
        except Exception:  # noqa: BLE001 - duration is only a confidence signal
            return None
        return None

    @staticmethod
    def _looks_like_hint_leak(text: str) -> bool:
        """True when STT returned the phrase/contact hints instead of speech."""
        cleaned = " ".join((text or "").lower().split())
        if not cleaned:
            return False
        comma_count = cleaned.count(",")
        phrase_hits = sum(1 for phrase in _STT_HINT_LEAK_PHRASES if phrase in cleaned)
        if comma_count >= 5 and phrase_hits >= 1:
            return True
        tokens = [
            t.strip("'`ʻʼ‘’").lower()
            for t in _STT_TOKEN_RE.findall(cleaned)
            if t.strip("'`ʻʼ‘’")
        ]
        if comma_count >= 8 and len(tokens) >= 20:
            return True
        return False

    def _looks_like_bad_verification(self, text: str) -> bool:
        """Reject Gemini audit output that is clearly not a transcript."""
        cleaned = " ".join((text or "").strip().split())
        if not cleaned:
            return True
        max_reasonable = max(160, int(self.settings.stt_verify_max_chars) * 2)
        if len(cleaned) > max_reasonable:
            return True
        tokens = [
            t.strip("'`ʻʼ‘’").lower()
            for t in _STT_TOKEN_RE.findall(cleaned)
            if t.strip("'`ʻʼ‘’")
        ]
        if len(tokens) < 12:
            return False
        single_letters = sum(1 for token in tokens if len(token) == 1)
        if single_letters / len(tokens) > 0.6:
            return True
        return len(set(tokens)) <= max(3, len(tokens) // 10)

    @staticmethod
    def _repair_stt_text(text: str) -> str:
        """Fix high-confidence command mishears seen in short Uzbek voice notes."""
        cleaned = " ".join((text or "").strip().split())
        if not cleaned:
            return ""
        if (
            _STT_SHORT_REF_RE.search(cleaned)
            and _STT_SALOM_MISHEAR_RE.search(cleaned)
        ):
            ref = "shunga"
            lowered = cleaned.lower()
            if "shu odam" in lowered:
                ref = "shu odamga"
            elif "shu kontakt" in lowered:
                ref = "shu kontaktga"
            elif "unga" in lowered and "shunga" not in lowered and "shuga" not in lowered:
                ref = "unga"
            return f"Ha, {ref} salom deb yubor."
        return cleaned

    def _transcribe_sync(
        self, client: Any, audio_path: str, language_code: str
    ) -> str:
        """Blocking STT call. Runs inside ``asyncio.to_thread``."""
        stt = getattr(client, "speech_to_text", None)
        if stt is None or not hasattr(stt, "convert"):
            raise AttributeError("ElevenLabs SDK has no speech_to_text.convert")
        with open(audio_path, "rb") as fh:
            result = stt.convert(
                file=fh,
                model_id=self.settings.elevenlabs_stt_model,
                language_code=language_code,
            )
        text = getattr(result, "text", None)
        if text is None and isinstance(result, dict):
            text = result.get("text")
        return str(text or "").strip()
