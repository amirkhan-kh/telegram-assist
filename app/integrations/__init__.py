"""Thin wrappers around optional third-party SDKs (Anthropic, ElevenLabs, Google).

Every accessor imports its heavy/optional SDK lazily inside the function body so
this package — and the modules that import it — stay importable even when a given
library is not installed or its API key/credentials are absent.
"""
