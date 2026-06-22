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
    # Gemini text-to-speech for outbound voice notes (no ElevenLabs needed).
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    # Prebuilt voice. Male: Charon, Puck, Fenrir, Orus, Enceladus, Iapetus.
    # Female: Kore, Aoede, Leda, Callirrhoe, Autonoe.
    gemini_tts_voice: str = "Charon"
    # Vertex AI path (uses a GCP service account instead of an API key; billed):
    gemini_use_vertex: bool = False
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    # Path to a service-account JSON (Vertex AI auth). Sets ADC when present.
    google_application_credentials: str | None = None

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
    # Use the LLM to merge same-topic posts across channels (different wording).
    digest_semantic_dedup: bool = True
    # On startup, ingest recent history so the digest is not empty after a restart.
    digest_backfill_per_channel: int = 30
    digest_backfill_max_channels: int = 40

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
