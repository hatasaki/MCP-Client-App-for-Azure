from __future__ import annotations

import base64
from dataclasses import dataclass, field

import pytest

from app.secret_protection import (
    ENCRYPTION_ENV_VAR,
    PlatformMasterKeyProvider,
    SecretProtectionError,
    SecretProtector,
    decode_master_key,
    encode_master_key,
    generate_master_key,
)


@dataclass
class FixedKeyProvider:
    key: bytes
    calls: list[bool] = field(default_factory=list)

    def get_key(self, *, create: bool) -> bytes:
        self.calls.append(create)
        return self.key


def test_aes_gcm_roundtrip_uses_random_nonce_and_hides_plaintext():
    provider = FixedKeyProvider(b"a" * 32)
    protector = SecretProtector(provider)

    first = protector.encrypt("top-secret-api-key")
    second = protector.encrypt("top-secret-api-key")

    assert first["algorithm"] == "AES-256-GCM"
    assert first["nonce"] != second["nonce"]
    assert first["ciphertext"] != second["ciphertext"]
    assert "top-secret-api-key" not in str(first)
    assert protector.decrypt(first) == "top-secret-api-key"
    assert provider.calls == [True, True, False]


def test_tampered_ciphertext_is_rejected():
    protector = SecretProtector(FixedKeyProvider(b"b" * 32))
    envelope = protector.encrypt("secret")
    ciphertext = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
    ciphertext[-1] ^= 1
    envelope["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode("ascii")

    with pytest.raises(SecretProtectionError, match="invalid, corrupted"):
        protector.decrypt(envelope)


def test_wrong_master_key_is_rejected_before_decryption():
    envelope = SecretProtector(FixedKeyProvider(b"c" * 32)).encrypt("secret")

    with pytest.raises(SecretProtectionError, match="does not match"):
        SecretProtector(FixedKeyProvider(b"d" * 32)).decrypt(envelope)


def test_encryption_context_is_authenticated():
    protector = SecretProtector(FixedKeyProvider(b"h" * 32))
    envelope = protector.encrypt("secret", context=b"endpoint-a")

    assert protector.decrypt(envelope, context=b"endpoint-a") == "secret"
    with pytest.raises(SecretProtectionError, match="invalid, corrupted"):
        protector.decrypt(envelope, context=b"endpoint-b")


def test_invalid_envelope_and_provider_key_length_fail_closed():
    protector = SecretProtector(FixedKeyProvider(b"e" * 32))

    with pytest.raises(SecretProtectionError, match="Invalid encrypted"):
        protector.decrypt({"version": 1, "algorithm": "AES-256-GCM"})
    envelope = protector.encrypt("secret")
    envelope["version"] = 99
    with pytest.raises(SecretProtectionError, match="Unsupported"):
        protector.decrypt(envelope)
    with pytest.raises(SecretProtectionError, match="exactly 32 bytes"):
        SecretProtector(FixedKeyProvider(b"short")).encrypt("secret")


def test_master_key_encoding_accepts_padded_and_unpadded_urlsafe_values():
    key = bytes(range(32))
    encoded = encode_master_key(key)

    assert decode_master_key(encoded) == key
    assert decode_master_key(encoded.rstrip("=")) == key
    assert decode_master_key(generate_master_key()) != key


@pytest.mark.parametrize("value", ["", "not base64!", "+" * 43 + "=", encode_master_key(b"f" * 32)[:-4]])
def test_invalid_environment_master_keys_are_rejected(value: str):
    with pytest.raises(SecretProtectionError):
        decode_master_key(value)


def test_headless_environment_requires_external_master_key(monkeypatch):
    monkeypatch.setenv("MCPCLIENT_HEADLESS", "1")
    monkeypatch.delenv(ENCRYPTION_ENV_VAR, raising=False)

    with pytest.raises(SecretProtectionError, match=ENCRYPTION_ENV_VAR):
        PlatformMasterKeyProvider().get_key(create=True)


def test_environment_master_key_takes_precedence_in_headless_mode(monkeypatch):
    key = b"g" * 32
    monkeypatch.setenv("MCPCLIENT_HEADLESS", "1")
    monkeypatch.setenv(ENCRYPTION_ENV_VAR, encode_master_key(key))

    provider = PlatformMasterKeyProvider()
    assert provider.get_key(create=True) == key
    assert provider.get_key(create=False) == key
