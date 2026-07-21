from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENCRYPTION_ENV_VAR = "MCPCLIENT_ENCRYPTION_KEY"
ENVELOPE_VERSION = 1
ALGORITHM = "AES-256-GCM"
KEYRING_SERVICE = "mcp-client-microsoft-foundry"
KEYRING_ACCOUNT = "foundry-settings-master-key-v1"
_AAD_PREFIX = b"mcp-client-microsoft-foundry:foundry-settings:api-key:v1\0"


class SecretProtectionError(RuntimeError):
    """Raised when a settings secret cannot be securely encrypted or decrypted."""


class MasterKeyProvider(Protocol):
    def get_key(self, *, create: bool) -> bytes: ...


@dataclass(slots=True)
class PlatformMasterKeyProvider:
    """Loads a 256-bit key from an environment secret or native OS keyring.

    Environment-provided keys take precedence on every platform. Headless and
    non-Windows/macOS processes require the environment key and fail closed.
    """

    environment_variable: str = ENCRYPTION_ENV_VAR
    service_name: str = KEYRING_SERVICE
    account_name: str = KEYRING_ACCOUNT

    def get_key(self, *, create: bool) -> bytes:
        encoded = os.environ.get(self.environment_variable)
        if encoded:
            return decode_master_key(encoded)

        if os.environ.get("MCPCLIENT_HEADLESS") == "1" or sys.platform not in {"win32", "darwin"}:
            raise SecretProtectionError(
                f"{self.environment_variable} must contain a URL-safe base64-encoded 32-byte key "
                "when API key authentication is used in a headless or container environment."
            )

        try:
            import keyring
            from keyring.errors import KeyringError
        except ImportError as exc:
            raise SecretProtectionError(
                "The keyring package is required to protect API keys on Windows and macOS."
            ) from exc

        try:
            backend = keyring.get_keyring()
            backend_module = type(backend).__module__
            expected_prefix = "keyring.backends.Windows" if sys.platform == "win32" else "keyring.backends.macOS"
            if not backend_module.startswith(expected_prefix):
                raise SecretProtectionError(
                    f"A native {'Windows Credential Manager' if sys.platform == 'win32' else 'macOS Keychain'} "
                    f"backend is required; active keyring backend is {backend_module}."
                )
            stored = keyring.get_password(self.service_name, self.account_name)
            if stored:
                return decode_master_key(stored)
            if not create:
                raise SecretProtectionError(
                    "The native master key is missing. Restore the original OS credential or re-enter the API key."
                )
            key = secrets.token_bytes(32)
            keyring.set_password(self.service_name, self.account_name, encode_master_key(key))
            return key
        except SecretProtectionError:
            raise
        except KeyringError as exc:
            raise SecretProtectionError(f"Native keyring access failed: {exc}") from exc
        except Exception as exc:
            raise SecretProtectionError(f"Native keyring access failed: {exc}") from exc


def encode_master_key(key: bytes) -> str:
    if len(key) != 32:
        raise SecretProtectionError("The encryption master key must contain exactly 32 bytes.")
    return base64.urlsafe_b64encode(key).decode("ascii")


def decode_master_key(value: str) -> bytes:
    encoded = value.strip()
    if not encoded or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", encoded):
        raise SecretProtectionError(
            f"{ENCRYPTION_ENV_VAR} must be URL-safe base64 encoding of exactly 32 bytes."
        )
    try:
        padded = encoded + ("=" * (-len(encoded) % 4))
        key = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise SecretProtectionError(
            f"{ENCRYPTION_ENV_VAR} must be URL-safe base64 encoding of exactly 32 bytes."
        ) from exc
    if len(key) != 32:
        raise SecretProtectionError(
            f"{ENCRYPTION_ENV_VAR} must decode to exactly 32 bytes; got {len(key)}."
        )
    return key


def generate_master_key() -> str:
    """Return a new environment-ready URL-safe base64 key."""
    return encode_master_key(secrets.token_bytes(32))


class SecretProtector:
    def __init__(self, key_provider: MasterKeyProvider | None = None):
        self.key_provider = key_provider or PlatformMasterKeyProvider()

    def encrypt(self, plaintext: str, *, context: bytes = b"") -> dict[str, Any]:
        key = _validate_master_key(self.key_provider.get_key(create=True))
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), _aad(context))
        return {
            "version": ENVELOPE_VERSION,
            "algorithm": ALGORITHM,
            "keyId": hashlib.sha256(key).hexdigest()[:16],
            "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        }

    def decrypt(self, envelope: Mapping[str, Any], *, context: bytes = b"") -> str:
        if set(envelope) != {"version", "algorithm", "keyId", "nonce", "ciphertext"}:
            raise SecretProtectionError("Invalid encrypted API key envelope fields.")
        if envelope.get("version") != ENVELOPE_VERSION or envelope.get("algorithm") != ALGORITHM:
            raise SecretProtectionError("Unsupported encrypted API key envelope.")
        key = _validate_master_key(self.key_provider.get_key(create=False))
        expected_key_id = hashlib.sha256(key).hexdigest()[:16]
        if envelope.get("keyId") != expected_key_id:
            raise SecretProtectionError(
                "The configured encryption key does not match this settings file. Restore the original key."
            )
        try:
            nonce = base64.b64decode(str(envelope["nonce"]).encode("ascii"), altchars=b"-_", validate=True)
            if len(nonce) != 12:
                raise ValueError("AES-GCM nonce must contain 12 bytes.")
            ciphertext = base64.b64decode(
                str(envelope["ciphertext"]).encode("ascii"), altchars=b"-_", validate=True
            )
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(context))
            return plaintext.decode("utf-8")
        except (KeyError, ValueError, UnicodeDecodeError, InvalidTag) as exc:
            raise SecretProtectionError(
                "The encrypted API key is invalid, corrupted, or was encrypted with a different key."
            ) from exc


def _validate_master_key(key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32:
        raise SecretProtectionError("The encryption master key must contain exactly 32 bytes.")
    return key


def _aad(context: bytes) -> bytes:
    if not isinstance(context, bytes):
        raise SecretProtectionError("The encryption context must be bytes.")
    return _AAD_PREFIX + context


__all__ = [
    "ALGORITHM",
    "ENCRYPTION_ENV_VAR",
    "PlatformMasterKeyProvider",
    "SecretProtectionError",
    "SecretProtector",
    "decode_master_key",
    "encode_master_key",
    "generate_master_key",
]
