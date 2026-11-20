"""Low-level bit and sample helpers for the fragile image watermark.

This module deliberately contains no public watermark API.  It owns the
key-dependent sample order and the conversion between packed frame bytes and
the least-significant bits of image samples, so the public mode module can stay
focused on payload framing and result types.
"""
from __future__ import annotations

import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import payload as pl

ORDER_CONTEXT = "image_fragile/positions"


def check_image(image) -> np.ndarray:
    """Return *image* as an array, rejecting unsupported dtypes/shapes."""
    image = np.asarray(image)
    if image.dtype != np.uint8:
        raise TypeError("image must be a uint8 array")
    if image.ndim not in (2, 3):
        raise ValueError("image must be shaped (H, W) or (H, W, C)")
    return image


def check_bpc(bpc: int) -> None:
    """Validate the number of embedded bits per channel."""
    if bpc not in (1, 2):
        raise ValueError("bits_per_channel must be 1 or 2")


def sample_view(image: np.ndarray, use_alpha: bool) -> np.ndarray:
    """Return the view whose samples carry the watermark.

    RGBA images keep their alpha channel untouched unless *use_alpha* is true.
    """
    if image.ndim == 3 and image.shape[2] == 4 and not use_alpha:
        return image[..., :3]
    return image


def sample_order(key, total: int, count: int) -> np.ndarray:
    """First *count* sample indices of a key-dependent permutation of range(total).

    Sort keys are 32 random bits from AES-CTR with the index in the low bits, so
    every key is unique and the order is identical on every platform.
    """
    stream = Cipher(algorithms.AES(pl.subkey(key, ORDER_CONTEXT)), modes.CTR(b"\0" * 16)).encryptor()
    rand = np.frombuffer(stream.update(b"\0" * (4 * total)), dtype=">u4").astype(np.uint64)
    sort_keys = (rand << np.uint64(32)) | np.arange(total, dtype=np.uint64)
    if count < total:
        head = np.argpartition(sort_keys, count)[:count]
    else:
        head = np.arange(total)
    return head[np.argsort(sort_keys[head], kind="stable")]


def read_bits(flat: np.ndarray, key, bpc: int, nbytes: int) -> bytes | None:
    """Read *nbytes* frame bytes from the low *bpc* bits of *flat* samples."""
    needed = -(-nbytes * 8 // bpc)
    if needed > flat.size:
        return None
    values = flat[sample_order(key, flat.size, needed)] & ((1 << bpc) - 1)
    bits = ((values[:, None] >> np.arange(bpc - 1, -1, -1, dtype=np.uint8)) & 1).reshape(-1)[: nbytes * 8]
    return np.packbits(bits.astype(np.uint8)).tobytes()
