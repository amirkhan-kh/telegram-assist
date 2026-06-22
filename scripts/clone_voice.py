"""Clone your voice from local audio samples via ElevenLabs — run locally, once.

Provide one or more clean audio samples of your own voice (mp3/wav/m4a...). Only
use voices you are authorised to clone. The script creates an instant voice clone
and prints its ``voice_id``.

Usage::

    python -m scripts.clone_voice sample1.mp3 sample2.mp3

Copy the printed value into ``.env`` as ``ELEVENLABS_VOICE_ID=...``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import get_settings
from app.integrations.elevenlabs_client import get_elevenlabs


def _clone(client: object, name: str, files: list[str]) -> str:
    """Call the clone endpoint defensively across ElevenLabs SDK versions."""
    description = "Personal Telegram assistant owner voice"
    # Newer SDKs expose voices.add / voices.ivc; older ones a top-level clone().
    voices = getattr(client, "voices", None)
    if voices is not None and hasattr(voices, "add"):
        voice = voices.add(name=name, files=[open(f, "rb") for f in files])
    elif voices is not None and hasattr(voices, "ivc") and hasattr(voices.ivc, "create"):
        voice = voices.ivc.create(name=name, files=[open(f, "rb") for f in files])
    elif hasattr(client, "clone"):
        voice = client.clone(name=name, files=files, description=description)
    else:
        raise SystemExit("ElevenLabs SDK da ovoz klonlash usuli topilmadi.")

    voice_id = getattr(voice, "voice_id", None)
    if voice_id is None and isinstance(voice, dict):
        voice_id = voice.get("voice_id")
    if not voice_id:
        raise SystemExit("Klonlash bajarildi, lekin voice_id qaytmadi.")
    return str(voice_id)


def main(paths: list[str]) -> None:
    if not paths:
        raise SystemExit(
            "Kamida bitta audio fayl bering:\n"
            "  python -m scripts.clone_voice namuna1.mp3 [namuna2.mp3 ...]"
        )
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        raise SystemExit("Fayllar topilmadi: " + ", ".join(missing))

    settings = get_settings()
    client = get_elevenlabs(settings)
    if client is None:
        raise SystemExit(
            "ELEVENLABS_API_KEY .env faylda yo'q yoki SDK o'rnatilmagan."
        )

    voice_id = _clone(client, "Owner Voice", paths)
    print("\n" + "=" * 60)
    print("Ovoz kloni yaratildi.")
    print("=" * 60)
    print("\nQuyidagini .env faylga joylang:\n")
    print(f"ELEVENLABS_VOICE_ID={voice_id}")


if __name__ == "__main__":
    main(sys.argv[1:])
