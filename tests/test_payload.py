import pytest

from tracemark import payload as pl


@pytest.mark.parametrize("profile", pl.PROFILES)
def test_roundtrip_and_size(profile, key):
    frame = pl.pack(b"ID7", key, profile)
    assert len(frame) == pl.frame_size(profile, 3)
    assert pl.unpack(frame, key, profile) == b"ID7"


@pytest.mark.parametrize("profile", pl.PROFILES)
def test_wrong_key_and_tamper_rejected(profile, key, other_key):
    frame = pl.pack(b"ID7", key, profile)
    assert pl.unpack(frame, other_key, profile) is None
    bad = bytearray(frame)
    bad[-1] ^= 1
    assert pl.unpack(bytes(bad), key, profile) is None


def test_password_keys():
    frame = pl.pack(b"x", "correct horse")
    assert pl.unpack(frame, "correct horse") == b"x"
    assert pl.unpack(frame, "wrong horse") is None


def test_limits(key):
    with pytest.raises(ValueError):
        pl.pack(b"", key)
    with pytest.raises(ValueError):
        pl.pack(b"x" * 256, key, "compact")
    with pytest.raises(ValueError):
        pl.pack(b"x", key, "unknown")
