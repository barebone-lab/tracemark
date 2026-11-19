"""Packing the user's bytes into an encrypted, authenticated frame (and back).

Every mode hides a *frame* produced here, never the raw payload. Two profiles:

standard  version(1) | length(4) | AES-SIV(payload)          overhead 21 bytes
          16-byte tag: forging without the key succeeds with probability 2**-128.
compact   length(1) | payload XOR keystream | HMAC tag(4)   overhead 5 bytes
          For low-capacity carriers (text_lexical). Payload 1..255 bytes.
          4-byte tag: a blind forgery succeeds with probability 2**-32 per try.

Both profiles are deterministic: the same key and payload give the same frame.
"""
from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

DOMAIN = b"cityu-cs1302a-watermark-v1"
PROFILES = ("standard", "compact")
HEADER_SIZE = {"standard": 5, "compact": 1}
OVERHEAD = {"standard": 21, "compact": 5}
COMPACT_TAG = 4
MAX_PAYLOAD = {"standard": 2**32 - 1, "compact": 255}


# ---------------------------------------------------------------- keys
def master_key(key: bytes | str) -> bytes:
    """Normalise a user key to 32 bytes.

    bytes of length 32 are used as-is; other bytes are hashed with HKDF
    (assumed already random); str passwords are stretched with scrypt.
    """
    if isinstance(key, str):
        if not key:
            raise ValueError("key must not be empty")
        return _stretch(key)
    if isinstance(key, (bytes, bytearray)):
        key = bytes(key)
        if not key:
            raise ValueError("key must not be empty")
        if len(key) == 32:
            return key
        return _hkdf(key, b"master", 32)
    raise TypeError("key must be bytes or str")


@lru_cache(maxsize=256)
def _stretch(password: str) -> bytes:
    return Scrypt(salt=DOMAIN, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def _hkdf(material: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=DOMAIN, info=info).derive(material)


def subkey(key: bytes | str, purpose: str, length: int = 32) -> bytes:
    """Independent key for one purpose (e.g. 'image_fragile/positions')."""
    return _hkdf(master_key(key), purpose.encode("ascii"), length)


# ---------------------------------------------------------------- frames
def frame_size(profile: str, payload_length: int) -> int:
    _check_profile(profile)
    return payload_length + OVERHEAD[profile]


def pack(payload: bytes, key: bytes | str, profile: str = "standard") -> bytes:
    _check_profile(profile)
    payload = bytes(payload)
    if not 1 <= len(payload) <= MAX_PAYLOAD[profile]:
        raise ValueError(f"payload must be 1..{MAX_PAYLOAD[profile]} bytes for profile {profile!r}")
    if profile == "standard":
        header = b"\x01" + len(payload).to_bytes(4, "big")
        return header + AESSIV(subkey(key, "payload/siv", 64)).encrypt(payload, [DOMAIN, header])
    header = bytes([len(payload)])
    ciphertext = _xor(payload, _keystream(key, header, len(payload)))
    return header + ciphertext + _compact_tag(key, header + ciphertext)


def length_from_header(header: bytes, profile: str) -> int | None:
    """Payload length announced by a frame header, or None if malformed."""
    _check_profile(profile)
    if len(header) < HEADER_SIZE[profile]:
        return None
    if profile == "standard":
        if header[0] != 1:
            return None
        length = int.from_bytes(header[1:5], "big")
    else:
        length = header[0]
    return length if length >= 1 else None


def unpack(frame: bytes, key: bytes | str, profile: str = "standard") -> bytes | None:
    """Return the payload, or None when the frame is malformed or the key is wrong."""
    length = length_from_header(frame, profile)
    if length is None or len(frame) < frame_size(profile, length):
        return None
    frame = bytes(frame[: frame_size(profile, length)])
    if profile == "standard":
        header = frame[:5]
        try:
            return AESSIV(subkey(key, "payload/siv", 64)).decrypt(frame[5:], [DOMAIN, header])
        except InvalidTag:
            return None
    header, ciphertext, tag = frame[:1], frame[1 : 1 + length], frame[1 + length :]
    if not hmac.compare_digest(tag, _compact_tag(key, header + ciphertext)):
        return None
    return _xor(ciphertext, _keystream(key, header, length))


def _keystream(key: bytes | str, header: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=DOMAIN, info=b"compact/stream" + header).derive(
        subkey(key, "payload/compact-enc")
    )


def _compact_tag(key: bytes | str, data: bytes) -> bytes:
    return hmac.new(subkey(key, "payload/compact-mac"), DOMAIN + data, hashlib.sha256).digest()[:COMPACT_TAG]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _check_profile(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError(f"unknown payload profile {profile!r}; choose from {PROFILES}")


# ---------------------------------------------------------------- bit helpers
def to_bits(data: bytes) -> list[int]:
    """Most-significant bit first."""
    return [(byte >> shift) & 1 for byte in data for shift in range(7, -1, -1)]


def from_bits(bits) -> bytes:
    bits = list(bits)
    return bytes(sum(bits[i + j] << (7 - j) for j in range(8)) for i in range(0, len(bits) - 7, 8))
