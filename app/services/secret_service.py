"""SecretService — a Fernet-encrypted key/value store over the ``secrets`` table.

Sensitive values acquired at runtime (e.g. a Google OAuth refresh token obtained
through a consent flow) can be persisted encrypted instead of living in plaintext
env vars. Values are encrypted with ``SECRETS_ENC_KEY`` (a Fernet key) before they
touch the database and decrypted on read.

The Fernet cipher is built lazily and defensively: a missing or malformed key
makes :meth:`available` return ``False`` so callers degrade gracefully rather
than crashing at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.logging_conf import get_logger
from app.repositories import secret_repo

if TYPE_CHECKING:
    from app.registry import ServiceRegistry

logger = get_logger(__name__)


class SecretService:
    """Encrypt/decrypt named secrets using the configured Fernet key."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self._fernet: Any | None = None
        self._built = False

    def _get_fernet(self) -> Any | None:
        """Build (once) the Fernet cipher, or ``None`` if the key is missing/bad."""
        if not self._built:
            self._built = True
            key = self.registry.settings.secrets_enc_key
            if key:
                try:
                    from cryptography.fernet import Fernet

                    self._fernet = Fernet(
                        key.encode() if isinstance(key, str) else key
                    )
                except Exception as exc:  # noqa: BLE001 - invalid key -> disabled
                    logger.warning("secret.key.invalid", error=str(exc))
                    self._fernet = None
            else:
                logger.info("secret.disabled", reason="no_key")
        return self._fernet

    def available(self) -> bool:
        """True when a valid Fernet key is configured."""
        return self._get_fernet() is not None

    async def set(self, name: str, value: str) -> None:
        """Encrypt and persist ``value`` under ``name``."""
        fernet = self._get_fernet()
        if fernet is None:
            raise RuntimeError(
                "SECRETS_ENC_KEY sozlanmagan yoki noto'g'ri — maxfiy ma'lumotni "
                "saqlab bo'lmadi."
            )
        token = fernet.encrypt(value.encode())
        async with self.registry.session() as session:
            await secret_repo.upsert(session, name=name, value_encrypted=token)
        logger.info("secret.set", name=name)

    async def get(self, name: str) -> str | None:
        """Return the decrypted value for ``name``, or ``None`` if absent/undecryptable."""
        fernet = self._get_fernet()
        if fernet is None:
            return None
        async with self.registry.session() as session:
            row = await secret_repo.get(session, name)
        if row is None:
            return None
        try:
            return fernet.decrypt(row.value_encrypted).decode()
        except Exception as exc:  # noqa: BLE001 - wrong key / corrupt value
            logger.warning("secret.decrypt.failed", name=name, error=str(exc))
            return None

    async def delete(self, name: str) -> bool:
        """Remove the secret named ``name``; return True if it existed."""
        async with self.registry.session() as session:
            removed = await secret_repo.delete(session, name)
        if removed:
            logger.info("secret.deleted", name=name)
        return removed
