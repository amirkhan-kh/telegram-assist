"""Generate a Telethon ``StringSession`` for the userbot — run locally, once.

Interactive, beginner-friendly login: it asks for your phone, sends the code to
your Telegram app, takes the 5-digit code (validating it locally so a typo does
not burn Telegram's 3-attempt limit), handles 2FA, then writes the resulting
``StringSession`` straight into ``.env`` — no copy/paste needed.

Usage::

    python -m scripts.generate_session

The login code arrives INSIDE the Telegram app (the official "Telegram" chat),
not by SMS. When you get a "new device logged in" notice, that device IS this
userbot — do NOT terminate it, or the session becomes unauthorized.
"""

from __future__ import annotations

import asyncio
import getpass
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from app.config import get_settings

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_KEY = "TELETHON_SESSION"


def _write_session_to_env(session_str: str) -> bool:
    """Replace (or append) the ``TELETHON_SESSION`` line in ``.env``."""
    if not _ENV_PATH.is_file():
        return False
    lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
    new_line = f"{_KEY}={session_str}"
    for i, line in enumerate(lines):
        if line.strip().startswith(_KEY) and "=" in line:
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _ask_phone() -> str:
    """Prompt for a phone number in international format."""
    while True:
        phone = input(
            "\n1) Telefon raqamingizni xalqaro formatda yozing "
            "(masalan +998901802201): "
        ).strip().replace(" ", "")
        if phone.startswith("+") and phone[1:].isdigit() and len(phone) >= 8:
            return phone
        print("   ❌ Noto'g'ri format. '+' va davlat kodi bilan yozing, masalan +998901802201.")


def _ask_code() -> str:
    """Prompt for the 5-digit login code, validated locally."""
    while True:
        code = input(
            "\n3) Telegram ILOVANGIZDAGI 'Telegram' chatiga kelgan KODNI yozing "
            "(faqat raqam, masalan 51234): "
        ).strip().replace(" ", "").replace("-", "")
        if code.isdigit() and 4 <= len(code) <= 7:
            return code
        print("   ❌ Bu kodga o'xshamaydi. Faqat 5 xonali RAQAMNI yozing (masalan 51234).")


async def main() -> None:
    settings = get_settings()
    if not settings.api_id or not settings.api_hash:
        raise SystemExit(
            "API_ID va API_HASH .env faylda to'ldirilishi kerak "
            "(my.telegram.org dan oling)."
        )

    print("=" * 64)
    print("Telegram userbot sessiyasini yaratish")
    print("=" * 64)

    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)
    await client.connect()
    try:
        phone = _ask_phone()
        try:
            await client.send_code_request(phone)
        except PhoneNumberInvalidError:
            raise SystemExit(
                "❌ Bu telefon raqami Telegram'da topilmadi. Tekshirib qayta urining."
            ) from None
        except FloodWaitError as exc:
            raise SystemExit(
                f"❌ Telegram vaqtincha bloklab qo'ydi. {exc.seconds} soniya "
                "kuting va qayta urining."
            ) from None

        print(
            "\n2) ✅ Kod yuborildi. Telefoningizda TELEGRAM ILOVASINI oching →\n"
            "   eng yuqoridagi 'Telegram' (ko'k ✓) chatida 'Login code: 5xxxx' turadi.\n"
            "   (Bu SMS emas — Telegram ilovasining ICHIDA keladi.)"
        )

        while True:
            code = _ask_code()
            try:
                await client.sign_in(phone=phone, code=code)
                break
            except PhoneCodeInvalidError:
                print("   ❌ Kod noto'g'ri. Ilovadagi ENG SO'NGGI kodni qarang va qayta yozing.")
            except PhoneCodeExpiredError:
                print("   ⚠️  Kod eskirdi. Yangi kod yuboryapman...")
                await client.send_code_request(phone)
            except SessionPasswordNeededError:
                print("\n4) Hisobingizda 2FA (ikki bosqichli) parol yoqilgan.")
                while True:
                    pw = getpass.getpass("   2FA parolingizni yozing (ko'rinmaydi): ")
                    try:
                        await client.sign_in(password=pw)
                        break
                    except Exception:  # noqa: BLE001 - wrong password, ask again
                        print("   ❌ Parol noto'g'ri. Qayta urining.")
                break

        session_str = client.session.save()
        me = await client.get_me()
    finally:
        await client.disconnect()

    name = getattr(me, "first_name", "") or str(me.id)
    print("\n" + "=" * 64)
    print("✅ Muvaffaqiyatli kirildi:", name)
    print("=" * 64)

    if _write_session_to_env(session_str):
        print(f"✅ Sessiya avtomatik .env ga yozildi ({_KEY}=...).")
    else:
        print("\n.env topilmadi. Quyidagi satrni .env ga qo'lda joylang:\n")
        print(f"{_KEY}={session_str}")

    print(
        "\n⚠️  Telegram 'yangi qurilmadan kirildi' desa — bu shu userbot. "
        "Uni Telegram > Devices'dan O'CHIRMANG!\n"
        "Endi botni ishga tushiring:  python -m app.main"
    )


if __name__ == "__main__":
    asyncio.run(main())
