"""Application configuration via pydantic-settings.

Loads from environment + a local ``.env`` file. Validates at startup; required
fields (bot token, owner id, telegram api credentials) fail fast when missing.

Two database URLs are derived from a single ``DATABASE_URL``:
  * the async URL (asyncpg / aiosqlite) used by the domain ORM, and
  * a sync URL (psycopg / sqlite) required by APScheduler's SQLAlchemyJobStore,
which point at the *same* database.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field, field_validator
from pydantic_core import PydanticUndefined
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Telegram bot (control panel) ──────────────────────────────────────
    bot_token: str = Field(default="", description="@BotFather token")
    owner_chat_id: int = Field(default=0, description="only this chat may command the bot")

    # ── Telegram userbot (the user's account) ─────────────────────────────
    api_id: int = Field(default=0, description="my.telegram.org api_id")
    api_hash: str = Field(default="", description="my.telegram.org api_hash")
    telethon_session: str = Field(default="", description="StringSession (headless)")

    # ── NLU brain ─────────────────────────────────────────────────────────
    # Which LLM understands commands: "anthropic" (Claude) or "gemini" (Google).
    llm_provider: str = "anthropic"

    # Claude (Anthropic)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Gemini (Google). Free path: an AI Studio API key (aistudio.google.com).
    gemini_api_key: str = ""
    # General/bulk model — cheap, used for high-volume jobs (channel digest,
    # document OCR) where flash-lite is accurate enough.
    gemini_model: str = "gemini-2.5-flash-lite"
    # Precision-critical models default to full `flash` (not lite): intent
    # routing (NLU) and voice transcription (STT) drive every command, so the
    # extra accuracy is worth the cost. Override per-task via the env if needed.
    gemini_nlu_model: str = "gemini-2.5-flash"
    gemini_stt_model: str = "gemini-2.5-flash"
    # General Q&A / conversational fallback (answer_question intent). Full
    # `flash` (not lite) — answers reason over the question and, when the owner
    # needs live info, are grounded with Google Search (Vertex). Override per-env.
    gemini_answer_model: str = "gemini-2.5-flash"
    # Ground answer_question with live Google Search when the brain flags a
    # question as needing fresh info (news, prices, current events). Uses Vertex
    # grounding (no extra key). On any grounding error the answer degrades to an
    # ungrounded model reply, so disabling this is always safe.
    answer_web_grounding: bool = True
    # Gemini text-to-speech for outbound voice notes (no ElevenLabs needed).
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    # Prebuilt voice. Male: Charon, Puck, Fenrir, Orus, Enceladus, Iapetus.
    # Female: Kore, Aoede, Leda, Callirrhoe, Autonoe.
    gemini_tts_voice: str = "Charon"
    # Natural-language delivery style for Gemini TTS, prepended to the text as a
    # directive. The model SHAPES delivery with it but does NOT speak it aloud
    # (verified by an STT round-trip). Empty string => plain synthesis.
    gemini_tts_style: str = (
        "Quyidagi o'zbekcha matnni tabiiy, samimiy va ishonchli ohangda, "
        "tushunarli, o'rtacha tezlikda o'qing:"
    )
    # Vertex AI path (uses a GCP service account instead of an API key; billed):
    gemini_use_vertex: bool = False
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    # Path to a service-account JSON (Vertex AI auth). Sets ADC when present.
    google_application_credentials: str | None = None

    # ── Speech-to-Text: Chirp 2 (Google Cloud Speech-to-Text V2) ──────────
    # Primary STT engine when enabled. Reuses the Vertex GCP service account /
    # project above (no extra key). Speech-to-Text is a SEPARATE API from Vertex
    # Gemini: enable it on the project and grant the service account the
    # "Cloud Speech-to-Text User" role before switching this on. On any failure
    # the bot falls back to ElevenLabs/Gemini, so toggling it is always safe.
    stt_use_chirp: bool = False
    google_stt_model: str = "chirp_2"
    google_stt_language: str = "uz-UZ"
    # Regional endpoint for Chirp 2. Uzbek + chirp_2 availability is region-
    # specific; if a region rejects the language/model, try "global".
    google_stt_location: str = "us-central1"
    # TEMP diagnostics: when true, persist each inbound voice clip under
    # data/media/stt_debug/ so real audio can be pulled and analysed. Off in prod.
    stt_debug: bool = False
    # Verify short/ambiguous Chirp transcripts with Gemini before routing.
    stt_verify_chirp: bool = True
    stt_verify_max_chars: int = 90
    stt_verify_timeout_seconds: float = 8.0

    # ── Voice (ElevenLabs) ────────────────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str | None = None
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    elevenlabs_stt_model: str = "scribe_v2"

    # ── Google (Milestone 3) ──────────────────────────────────────────────
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_oauth_refresh_token: str | None = None
    # Working hours (local) used when proposing free calendar slots.
    work_day_start_hour: int = 9
    work_day_end_hour: int = 18
    # Read-only Gmail (uses the same Google OAuth + the gmail.readonly scope).
    # How many unread inbox messages to surface on demand / in the morning plan.
    gmail_max_results: int = 5

    # ── Notion (optional) ─────────────────────────────────────────────────
    # Integration token (https://www.notion.so/my-integrations) + a parent page
    # id the integration is shared with; the bot auto-creates its databases there.
    notion_api_key: str = ""
    notion_parent_page_id: str | None = None

    # ── Channel digest (Milestone 3) ──────────────────────────────────────
    # How far back the on-demand / scheduled digest looks, and the default size.
    digest_window_hours: int = 24
    digest_default_top_n: int = 5
    # Local hour the daily digest is delivered automatically (set <0 to disable).
    # Disabled by default: the news digest is shown only on the "Yangiliklar"
    # button, never auto-pushed.
    digest_daily_hour: int = -1

    # ── Daily briefing & review (CEO assistant) ───────────────────────────
    # Local hour the morning plan is delivered (set <0 to disable).
    morning_briefing_hour: int = 6
    # Local hour the evening day-end review is delivered (set <0 to disable).
    evening_review_hour: int = 21
    # How many days ahead the morning plan surfaces upcoming important dates.
    important_date_lookahead_days: int = 7
    # Morning plan: surface unread Telegram chats (contacts + groups + channels,
    # each with its unread count) read live from the userbot. Off when the
    # userbot session is not set. Per-category caps keep noisy channels in check.
    telegram_unread_in_briefing: bool = True
    telegram_unread_scan_limit: int = 200
    telegram_unread_max_dms: int = 6
    telegram_unread_max_groups: int = 4
    telegram_unread_max_channels: int = 4
    # Use the LLM to merge same-topic posts across channels (different wording).
    digest_semantic_dedup: bool = True
    # On startup, ingest recent history so the digest is not empty after a restart.
    digest_backfill_per_channel: int = 30
    digest_backfill_max_channels: int = 40

    # ── Jarvis assistant tools ────────────────────────────────────────────
    # Default city for weather/briefing requests when the owner does not name
    # a location. Open-Meteo is used for weather, so no extra API key is needed.
    jarvis_default_location: str = "Tashkent"
    # Sources for the "kun yangiliklari" command — Uzbek-Latin news channels read
    # via their public t.me/s web preview (the sites have no RSS/API). Aggregated,
    # merged by recency. Comma-separated t.me usernames; add more to widen world
    # coverage. Posts link back to the source article (daryo.uz, kun.uz…).
    news_channels: str = "Daryo,kunuz"
    jarvis_chat_media_limit: int = 10
    jarvis_chat_summary_limit: int = 50
    # Global Telegram archive search: private chats + groups + channels. These
    # limits keep on-demand searches fast; deeper historical search should be
    # handled by a background indexer.
    jarvis_archive_dialog_limit: int = 1000
    jarvis_archive_messages_per_dialog: int = 80
    jarvis_archive_group_message_limit: int = 1000
    jarvis_archive_channel_message_limit: int = 2000
    # 0 means full private history: Telethon scans until the first/oldest message.
    jarvis_archive_private_message_limit: int = 0
    jarvis_archive_media_analyze_limit: int = 8
    jarvis_archive_media_max_mb: int = 25
    # Background local index for fast archive search. Startup remains non-blocking:
    # every cycle indexes only a bounded slice, then continues later.
    jarvis_archive_index_enabled: bool = True
    jarvis_archive_index_dialog_limit: int = 250
    jarvis_archive_index_messages_per_dialog: int = 40
    jarvis_archive_index_max_messages_per_run: int = 800
    jarvis_archive_index_interval_seconds: int = 600

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./data/assistant.db"

    # ── Security / behaviour ──────────────────────────────────────────────
    secrets_enc_key: str = ""
    user_timezone: str = "Asia/Tashkent"
    log_level: str = "INFO"
    # In test mode, follow-up messages destined for third parties are routed to
    # the owner instead, so the system can be exercised without spamming people.
    test_mode: bool = True

    # ── Userbot safety guards ─────────────────────────────────────────────
    userbot_daily_send_limit: int = 50
    userbot_min_seconds_between_sends: float = 4.0

    @field_validator(
        "owner_chat_id",
        "api_id",
        "work_day_start_hour",
        "work_day_end_hour",
        "gmail_max_results",
        "digest_window_hours",
        "digest_default_top_n",
        "digest_daily_hour",
        "morning_briefing_hour",
        "evening_review_hour",
        "important_date_lookahead_days",
        "userbot_daily_send_limit",
        "userbot_min_seconds_between_sends",
        "jarvis_archive_index_dialog_limit",
        "jarvis_archive_index_messages_per_dialog",
        "jarvis_archive_index_max_messages_per_run",
        "jarvis_archive_index_interval_seconds",
        mode="before",
    )
    @classmethod
    def _blank_numeric_to_default(cls, value: object, info: object) -> object:
        """Treat a blank ``.env`` entry as 'use the default' instead of crashing.

        A blank numeric field (e.g. ``API_ID=``) would otherwise fail pydantic's
        int parsing with a noisy error; falling back to the field default lets
        :meth:`require_runtime` surface a friendly 'missing settings' message.
        """
        if value in ("", None):
            default = cls.model_fields[info.field_name].default  # type: ignore[attr-defined]
            return 0 if default is PydanticUndefined else default
        return value

    # ── Derived URLs ──────────────────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN for APScheduler's SQLAlchemyJobStore (same DB)."""
        url = self.database_url
        if url.startswith("postgresql+asyncpg"):
            return url.replace("postgresql+asyncpg", "postgresql+psycopg", 1)
        if url.startswith("sqlite+aiosqlite"):
            return url.replace("sqlite+aiosqlite", "sqlite", 1)
        return url

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def require_runtime(self) -> None:
        """Raise if mandatory runtime credentials are absent (called from main)."""
        missing = [
            name
            for name, val in (
                ("BOT_TOKEN", self.bot_token),
                ("OWNER_CHAT_ID", self.owner_chat_id),
                ("API_ID", self.api_id),
                ("API_HASH", self.api_hash),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "Missing required settings: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
