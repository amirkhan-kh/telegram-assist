"""Userbot layer — the owner's Telegram *account* (Telethon).

The userbot (a real user session, authenticated via a headless ``StringSession``)
is how the assistant sends messages *as the owner* to third parties: follow-up
nudges, scheduled outbound messages, and meeting links. It also ingests
channels in a later phase.

Modules:
  * :mod:`app.userbot.client`   — build/connect the Telethon client.
  * :mod:`app.userbot.safety`   — rate limiting + FloodWait retry.
  * :mod:`app.userbot.sender`   — the high-level send API used by services.
  * :mod:`app.userbot.handlers` — inbound update handlers (minimal for now).
"""
