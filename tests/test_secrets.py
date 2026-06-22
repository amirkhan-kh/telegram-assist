"""SecretService tests — Fernet round-trip, overwrite, delete, encryption-at-rest."""

from __future__ import annotations

from app.repositories import secret_repo


async def test_secret_round_trip(registry):
    svc = registry.secret_service
    assert svc.available() is True

    await svc.set("google_oauth_refresh_token", "1//top-secret-token")
    assert await svc.get("google_oauth_refresh_token") == "1//top-secret-token"


async def test_secret_is_encrypted_at_rest(registry):
    svc = registry.secret_service
    await svc.set("api_key", "plaintext-value")

    # The stored bytes must not contain the plaintext.
    async with registry.session() as session:
        row = await secret_repo.get(session, "api_key")
    assert row is not None
    assert b"plaintext-value" not in row.value_encrypted


async def test_secret_overwrite_and_delete(registry):
    svc = registry.secret_service
    await svc.set("k", "v1")
    await svc.set("k", "v2")
    assert await svc.get("k") == "v2"

    assert await svc.delete("k") is True
    assert await svc.get("k") is None
    assert await svc.delete("k") is False  # already gone


async def test_missing_secret_returns_none(registry):
    assert await registry.secret_service.get("does-not-exist") is None


async def test_unavailable_without_key(registry):
    # Force the "no key" path without mutating the shared session settings.
    from types import SimpleNamespace

    from app.services.secret_service import SecretService

    no_key = registry.settings.model_copy(update={"secrets_enc_key": ""})
    svc = SecretService(SimpleNamespace(settings=no_key))
    assert svc.available() is False
    assert await svc.get("anything") is None
