"""One-off local setup scripts (run with ``python -m scripts.<name>``).

These are interactive helpers you run on your own machine during setup, never
from the running app:

* ``generate_session`` — log in once and print a Telethon ``StringSession`` for
  the userbot (set it as ``TELETHON_SESSION`` in ``.env``).
* ``clone_voice`` — clone your voice from local audio samples via ElevenLabs and
  print the resulting ``voice_id`` (set it as ``ELEVENLABS_VOICE_ID``).
"""
