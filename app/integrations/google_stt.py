"""Google Cloud Speech-to-Text V2 (Chirp 2) — the primary Uzbek STT engine.

Chirp 2 is an ASR-specialised multilingual model. Compared with the Gemini
multimodal fallback it transcribes Uzbek more literally and accepts *inline
phrase hints* — used here to bias recognition toward the owner's contact names
(the single biggest source of voice-command errors).

Auth reuses the **same** GCP service account / project as the Vertex Gemini path
(``GOOGLE_CLOUD_PROJECT`` + ``GOOGLE_APPLICATION_CREDENTIALS``); Speech-to-Text is
a separate API on that project, so it must be enabled and the service account
granted the "Cloud Speech-to-Text User" role.

The heavy ``google-cloud-speech`` SDK is imported *inside* the function so the
module imports cleanly when the package is absent (e.g. in unit tests) — callers
then get ``""`` and fall back to ElevenLabs/Gemini. Blocking; run via
``asyncio.to_thread``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.logging_conf import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)

# Cap the contact-name hint list so the adaptation payload stays bounded even
# when the owner has a large synced phonebook.
_HINT_MAX = 200
_COMMAND_HINTS = [
    "shunga salom deb yubor",
    "shu odamga salom deb yubor",
    "shu kontaktga salom deb yubor",
    "unga salom deb yubor",
    "salom deb xabar yubor",
    "oxirgi xabarni yubor",
    "oxirgi rasmni yubor",
    "yozishmalarni xulosa qil",
    "xabar yubor",
    "ovozli xabar yubor",
    "matnli xabar yubor",
]


def _resolve_project_and_creds(settings: Settings):
    """Resolve the GCP project id + service-account creds (mirrors Vertex auth)."""
    project = settings.google_cloud_project
    creds = None
    if settings.google_application_credentials:
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        # Fall back to the project baked into the service account.
        project = project or getattr(creds, "project_id", None)
    return project, creds


def transcribe_chirp_sync(
    settings: Settings, audio_bytes: bytes, hint_names: list[str] | None = None
) -> str:
    """Transcribe ``audio_bytes`` with Chirp 2; ``""`` when unavailable.

    Real API errors propagate to the caller (which logs + falls back); only
    "not installed / not configured" cases return ``""`` quietly.
    """
    try:
        from google.api_core.client_options import ClientOptions
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech
    except ImportError:
        logger.info("stt.chirp.skipped", reason="sdk_not_installed")
        return ""

    project, creds = _resolve_project_and_creds(settings)
    if not project:
        logger.info("stt.chirp.skipped", reason="no_gcp_project")
        return ""

    location = settings.google_stt_location
    # V2 needs a regional endpoint; "global" uses the unprefixed host.
    endpoint = (
        "speech.googleapis.com"
        if location == "global"
        else f"{location}-speech.googleapis.com"
    )
    client = SpeechClient(
        credentials=creds,
        client_options=ClientOptions(api_endpoint=endpoint),
    )

    config = cloud_speech.RecognitionConfig(
        # AutoDetect reads both the preprocessed 16 kHz WAV and the raw Ogg/Opus.
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=[settings.google_stt_language],
        model=settings.google_stt_model,
        features=cloud_speech.RecognitionFeatures(
            enable_automatic_punctuation=True,
        ),
    )
    adaptation = _build_adaptation(cloud_speech, hint_names)
    if adaptation is not None:
        config.adaptation = adaptation

    request = cloud_speech.RecognizeRequest(
        # The inline "_" recognizer means no recognizer resource has to be
        # pre-created — config travels with the request.
        recognizer=f"projects/{project}/locations/{location}/recognizers/_",
        config=config,
        content=audio_bytes,
    )
    response = client.recognize(request=request)
    parts = [
        result.alternatives[0].transcript
        for result in response.results
        if result.alternatives
    ]
    text = " ".join(p.strip() for p in parts if p and p.strip()).strip()
    logger.info("stt.chirp.ok", chars=len(text))
    return text


def _build_adaptation(cloud_speech, hint_names: list[str] | None):
    """Inline phrase-set biasing Chirp toward the owner's contact names.

    Chirp 2 supports plain word/phrase hints (no class tokens, no boost weights),
    so each contact name is added as a bare phrase value.
    """
    names = [n.strip() for n in (hint_names or []) if n and n.strip()]
    phrases_raw = _COMMAND_HINTS + names
    phrases_raw = phrases_raw[:_HINT_MAX]
    if not phrases_raw:
        return None
    phrases = [cloud_speech.PhraseSet.Phrase(value=value) for value in phrases_raw]
    return cloud_speech.SpeechAdaptation(
        phrase_sets=[
            cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                inline_phrase_set=cloud_speech.PhraseSet(phrases=phrases)
            )
        ]
    )
