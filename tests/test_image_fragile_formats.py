import numpy as np
import pytest

import tracemark as wm

rng = np.random.default_rng(1)
MODE = "image_fragile"


def test_rgba_alpha_untouched_and_grayscale(key):
    rgba = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
    marked = wm.embed(rgba, b"alpha", key, mode=MODE)
    assert np.array_equal(marked[..., 3], rgba[..., 3])
    assert wm.extract(marked, key, mode=MODE).payload == b"alpha"
    gray = rng.integers(0, 256, (64, 64), dtype=np.uint8)
    assert wm.extract(wm.embed(gray, b"g", key, mode=MODE), key, mode=MODE).payload == b"g"


def test_rejects_non_uint8(key):
    with pytest.raises(TypeError):
        wm.embed(np.zeros((8, 8), dtype=np.float32), b"x", key, mode=MODE)
