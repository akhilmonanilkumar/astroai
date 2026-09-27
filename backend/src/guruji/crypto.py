"""App-level encryption for personal fields (DPDP) and keyed lookup hashes.

- Each field (birth date, place, name, ...) gets its own AES-256-GCM key, derived with
  HKDF from one master key. The user id and field name are bound in as associated data,
  so a ciphertext copied to another user or column fails to decrypt.
- Phone numbers are found by HMAC-SHA256 under a separate key, never stored in clear.

Blob layout: version byte (key generation) | 12-byte nonce | ciphertext+tag.
"""

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = 1
_NONCE = 12


class DecryptError(ValueError):
    pass


def decode_key(b64: str) -> bytes:
    key = base64.b64decode(b64)
    if len(key) != 32:
        raise ValueError("encryption keys must be 32 bytes, base64-encoded")
    return key


class FieldCipher:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._master = master_key

    @lru_cache(maxsize=32)  # noqa: B019 - one cipher per field for the process lifetime
    def _aead(self, field: str) -> AESGCM:
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"field:{field}".encode())
        return AESGCM(hkdf.derive(self._master))

    def encrypt(self, field: str, value: str, user_id: str) -> bytes:
        nonce = os.urandom(_NONCE)
        aad = f"{field}:{user_id}".encode()
        return bytes([_VERSION]) + nonce + self._aead(field).encrypt(nonce, value.encode(), aad)

    def decrypt(self, field: str, blob: bytes, user_id: str) -> str:
        if len(blob) < 1 + _NONCE + 16 or blob[0] != _VERSION:
            raise DecryptError(f"bad {field} ciphertext")
        nonce, ct = blob[1 : 1 + _NONCE], blob[1 + _NONCE :]
        try:
            plain = self._aead(field).decrypt(nonce, ct, f"{field}:{user_id}".encode())
        except Exception as e:  # cryptography raises InvalidTag
            raise DecryptError(f"cannot decrypt {field}") from e
        return plain.decode()

    # For values kept inside JSON columns.
    def encrypt_text(self, field: str, value: str, user_id: str) -> str:
        return base64.b64encode(self.encrypt(field, value, user_id)).decode()

    def decrypt_text(self, field: str, token: str, user_id: str) -> str:
        return self.decrypt(field, base64.b64decode(token), user_id)


def lookup_hash(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
