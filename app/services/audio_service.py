"""Audio transcoding helpers for Telegram voice notes.

Telegram voice messages must be Ogg/Opus mono. These helpers shell out to
``ffmpeg`` to convert an arbitrary source audio file (e.g. an mp3 from TTS)
into the required format. ``ffmpeg`` is invoked via ``subprocess`` and is meant
to be wrapped in ``asyncio.to_thread`` by async callers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
import wave

from app.logging_conf import get_logger

logger = get_logger(__name__)


def ensure_ffmpeg() -> bool:
    """Return True when an ``ffmpeg`` binary is available on PATH."""
    return shutil.which("ffmpeg") is not None


def to_telegram_voice(src_path: str, *, out_dir: str | None = None) -> str:
    """Transcode ``src_path`` into a Telegram-ready Ogg/Opus voice note.

    Returns the path to the generated ``.ogg`` file. Raises
    ``FileNotFoundError`` if ffmpeg is missing and ``subprocess.CalledProcessError``
    if the transcode fails. This is a blocking call; run it via
    ``asyncio.to_thread`` from async code.
    """
    if not ensure_ffmpeg():
        raise FileNotFoundError(
            "ffmpeg not found on PATH; cannot build Telegram voice note."
        )
    target_dir = out_dir or os.path.dirname(os.path.abspath(src_path))
    os.makedirs(target_dir, exist_ok=True)
    out_path = os.path.join(target_dir, f"voice_{uuid.uuid4().hex}.ogg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src_path,
        "-ac",
        "1",
        "-ar",
        "48000",
        "-c:a",
        "libopus",
        "-b:a",
        "32k",
        "-application",
        "voip",
        out_path,
    ]
    logger.info("audio.transcode.start", src=src_path, out=out_path)
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("audio.transcode.done", out=out_path)
    return out_path


def pcm_to_telegram_voice(
    pcm: bytes,
    *,
    sample_rate: int = 24000,
    out_dir: str = "data/media",
) -> str:
    """Wrap raw 16-bit mono PCM in a WAV, then transcode to a Telegram voice note.

    Gemini TTS returns signed 16-bit little-endian mono PCM (24 kHz by default).
    We add a WAV header so ffmpeg can read it, then reuse
    :func:`to_telegram_voice` for the Ogg/Opus transcode. Returns the ``.ogg``
    path. This is a blocking call; run it via ``asyncio.to_thread``.
    """
    os.makedirs(out_dir, exist_ok=True)
    wav_path = os.path.join(out_dir, f"tts_{uuid.uuid4().hex}.wav")
    with wave.open(wav_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    try:
        return to_telegram_voice(wav_path, out_dir=out_dir)
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass
