"""Control bot (python-telegram-bot).

The owner-facing control panel. The single :func:`app.bot.application.build_application`
entrypoint builds a configured :class:`telegram.ext.Application` whose lifecycle is
driven manually by :mod:`app.main` (so it can share one event loop with Telethon
and APScheduler).
"""
