# Telegram Assistant

Shaxsiy Telegram yordamchisi: **bot** (boshqaruv paneli) + **userbot** (sizning
hisobingiz), eslatmalar, va'dalar, topshiriqlar, ovozli xabarlar (ElevenLabs),
Google Meet/Calendar va kanal dayjesti.

Owner (egasi) faqat siz: bot faqat `OWNER_CHAT_ID` chatidan buyruq qabul qiladi.
Barcha foydalanuvchiga ko'rinadigan matnlar **o'zbekcha** (lotin).

---

## Architecture (English)

- **Bot** (`python-telegram-bot`) — the control panel you talk to. Owner-only.
- **Userbot** (`telethon`, StringSession) — sends messages from *your* account.
- **Brain** (`anthropic` tool-use) — routes Uzbek utterances to typed intents.
- **Scheduler** (`APScheduler` + SQLAlchemy jobstore) — fires reminders, promise
  alerts, follow-ups, scheduled messages and meeting alerts. Jobs are persisted
  as the module-level callable `app.scheduler.jobs:execute_job`, so a restart
  resumes pending jobs.
- **Voice** (`ElevenLabs` + `ffmpeg`) — TTS to Telegram opus voice notes and
  Scribe STT for incoming voice commands.
- **DB** — SQLAlchemy 2.0 async ORM. SQLite for local dev (zero-config),
  Postgres in Docker. The same database backs both the domain ORM (async DSN)
  and APScheduler's jobstore (sync DSN, derived in `app/config.py`).

---

## Sozlash / Setup

### 1. `.env` faylini tayyorlang

```bash
cp .env.example .env
```

So'ng `.env` ichidagi qiymatlarni to'ldiring:

- `BOT_TOKEN` — [@BotFather](https://t.me/BotFather) dan.
- `OWNER_CHAT_ID` — sizning Telegram raqamli ID'ingiz ([@userinfobot](https://t.me/userinfobot)).
- `API_ID`, `API_HASH` — [my.telegram.org](https://my.telegram.org) dan.
- `ANTHROPIC_API_KEY` — [console.anthropic.com](https://console.anthropic.com) (NLU uchun).
- `ELEVENLABS_API_KEY` — [elevenlabs.io](https://elevenlabs.io) (ovoz uchun, ixtiyoriy).
- `SECRETS_ENC_KEY` — Fernet kaliti:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

`.env` git'ga qo'shilmaydi — haqiqiy maxfiy ma'lumotlarni hech qachon commit qilmang.

### 2. Bog'liqliklarni o'rnating / Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> `ffmpeg` tizimda o'rnatilgan bo'lishi kerak (ovozli xabarlar uchun).
> macOS: `brew install ffmpeg` · Debian/Ubuntu: `apt install ffmpeg`.

### 3. Telethon sessiyasini yarating / Generate the userbot session

Userbot sizning hisobingiz nomidan ishlaydi. Sessiya satrini bir marta lokal
hosil qiling (telefon + kod + 2FA so'raladi):

```bash
python -m scripts.generate_session
```

Chiqqan **StringSession** ni `.env` dagi `TELETHON_SESSION=` ga joylashtiring.
Bu satrni eng yuqori darajadagi maxfiy ma'lumot sifatida saqlang.

### 4. Ovozingizni klonlang (ixtiyoriy) / Clone your voice

Ruxsat etilgan ovoz namunalaringizdan (audio fayllar) ovoz klonini yarating:

```bash
python -m scripts.clone_voice path/to/sample1.mp3 path/to/sample2.mp3
```

Chiqqan **voice_id** ni `.env` dagi `ELEVENLABS_VOICE_ID=` ga yozing.

---

## Database migratsiyalari / Migrations

Skeema o'zgarishlari Alembic orqali boshqariladi. URL `app/config.py` dan
(`sync_database_url`) avtomatik olinadi — `alembic.ini` da saqlanmaydi.

```bash
# Modellardan avtomatik migratsiya yaratish:
alembic revision --autogenerate -m "init schema"

# So'nggi versiyaga ko'tarish:
alembic upgrade head
```

> Lokal SQLite uchun migratsiya ishlatmasdan ham ishga tushirish mumkin
> (`Base.metadata.create_all` startda chaqiriladi); Postgres uchun
> `alembic upgrade head` tavsiya etiladi (compose buni avtomatik bajaradi).

---

## Lokal ishga tushirish / Run locally

```bash
source .venv/bin/activate
python -m app.main
```

Botga (`@BotFather` da yaratganingizga) `/start` yuboring.

---

## Testlar / Tests

```bash
pytest
```

Testlar lokal, izolyatsiyalangan SQLite (xotira) bazasidan foydalanadi va
jonli API chaqiruvlarini talab qilmaydi.

---

## Docker

To'liq stack (Postgres + ilova). `.env` tayyor bo'lishi kerak (`OWNER_CHAT_ID`,
tokenlar, `TELETHON_SESSION`...). `docker-compose.yml` ilova uchun
`DATABASE_URL` ni Postgres'ga o'zgartiradi va `.env` dan maxfiy ma'lumotlarni
yuklaydi.

```bash
docker compose up -d --build      # qurish va ishga tushirish
docker compose logs -f app        # loglarni kuzatish
docker compose down               # to'xtatish
```

Ilova konteyneri ishga tushganda avval `alembic upgrade head`, so'ng
`python -m app.main` bajariladi. Postgres ma'lumotlari va ovoz fayllari
nomli volume'larda (`pgdata`, `media`) saqlanadi.

---

## Milestones / Bosqichlar

1. **Milestone 1 — Core text assistant.** Bot + brain (Claude tool-use) +
   scheduler. Eslatmalar, va'dalar (self-promise), topshiriqlar (delegated +
   follow-up), rejalashtirilgan xabarlar va qarz/hisob yozuvlari. SQLite,
   `TEST_MODE=true` (uchinchi shaxslarga xabarlar egasiga yo'naltiriladi).
2. **Milestone 2 — Voice.** ElevenLabs TTS (Telegram opus voice notes) va
   Scribe STT (kiruvchi ovozli buyruqlar). Userbot ovozli xabar yuboradi.
   `scripts.clone_voice` orqali shaxsiy ovoz klonlanadi.
3. **Milestone 3 — Google + kanallar.** Google Calendar/Meet integratsiyasi
   (bo'sh vaqtlar, Meet havolali uchrashuvlar) va Telegram kanal dayjesti.

---

## Loyiha tuzilishi / Project layout

```
app/
  config.py          # pydantic-settings (sozlamalar)
  registry.py        # ServiceRegistry singleton
  db/                # ORM (Base, engine, models)
  repositories/      # plain async DB functions
  services/          # business logic classes
  scheduler/         # APScheduler factory + jobs
  brain/             # NLU (intents, tools, router, time parsing)
  integrations/      # anthropic / elevenlabs / google clients
  userbot/           # telethon client + sender + safety
  bot/               # python-telegram-bot application + handlers
migrations/          # Alembic (env.py, versions/)
scripts/             # generate_session, clone_voice
tests/               # pytest
```
# avioraassistantbot
