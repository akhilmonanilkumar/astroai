import os

import pytest

from guruji.crypto import DecryptError, FieldCipher, lookup_hash


def test_roundtrip_and_binding() -> None:
    c = FieldCipher(os.urandom(32))
    blob = c.encrypt("birth_date", "1990-07-15", "user-1")
    assert b"1990" not in blob
    assert c.decrypt("birth_date", blob, "user-1") == "1990-07-15"
    with pytest.raises(DecryptError):
        c.decrypt("birth_date", blob, "user-2")  # copied to another user
    with pytest.raises(DecryptError):
        c.decrypt("birth_time", blob, "user-1")  # copied to another column
    assert c.encrypt("x", "same", "u") != c.encrypt("x", "same", "u")  # random nonce
    token = c.encrypt_text("onboarding", "{}", "u")
    assert c.decrypt_text("onboarding", token, "u") == "{}"


def test_wrong_key_and_garbage() -> None:
    blob = FieldCipher(os.urandom(32)).encrypt("f", "v", "u")
    with pytest.raises(DecryptError):
        FieldCipher(os.urandom(32)).decrypt("f", blob, "u")
    with pytest.raises(DecryptError):
        FieldCipher(os.urandom(32)).decrypt("f", b"\x01short", "u")


def test_lookup_hash_is_keyed() -> None:
    assert lookup_hash(b"k" * 32, "919800000001") == lookup_hash(b"k" * 32, "919800000001")
    assert lookup_hash(b"k" * 32, "919800000001") != lookup_hash(b"j" * 32, "919800000001")
