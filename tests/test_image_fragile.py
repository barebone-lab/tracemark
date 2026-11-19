import numpy as np
import pytest

import watermark as wm
from watermark import image_fragile as fr

rng = np.random.default_rng(1)
MODE = "image_fragile"


@pytest.fixture
def image():
    return rng.integers(0, 256, (512, 512, 3), dtype=np.uint8)


def test_roundtrip_large_payload(image, key):
    secret = rng.integers(0, 256, 50_000, dtype=np.uint8).tobytes()
    before = image.copy()
    marked = wm.embed(image, secret, key, mode=MODE)
    assert np.array_equal(image, before), "input must not be modified"
    assert int(np.abs(marked.astype(int) - image).max()) <= 1
    result = wm.extract(marked, key, mode=MODE)
    assert result.verified and result.payload == secret and result.score == 1.0


def test_rejections(image, key, other_key):
    marked = wm.embed(image, b"ID7", key, mode=MODE)
    assert not wm.extract(marked, other_key, mode=MODE).verified
    assert not wm.extract(image, key, mode=MODE).verified


def test_capacity_exact_and_exceeded(image, key):
    cap = fr.capacity(image)
    assert wm.extract(wm.embed(image, b"x" * cap, key, mode=MODE), key, mode=MODE).payload == b"x" * cap
    with pytest.raises(wm.CapacityError):
        wm.embed(image, b"x" * (cap + 1), key, mode=MODE)


def test_hide_whole_image_two_bits(image, key):
    small = rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)
    result = wm.extract(wm.embed(image, small.tobytes(), key, mode=MODE, bits_per_channel=2), key, mode=MODE)
    assert result.verified and result.details["bits_per_channel"] == 2
    assert np.array_equal(np.frombuffer(result.payload, np.uint8).reshape(small.shape), small)
