"""Mode 1 - fragile image watermark (least-significant bits).

The frame's bits replace the lowest bit(s) of colour samples, visited in a
key-dependent order, so each sample changes by at most 1 (or 3 with two bits).
Large capacity, but only an exact copy of the pixels survives: re-saving as
JPEG, resizing or screenshots with scaling destroy it.

Images are numpy uint8 arrays shaped (H, W) or (H, W, C). The input is never
modified; a new array is returned.
"""
from __future__ import annotations

import numpy as np

from . import payload as pl
from ._fragile_bits import check_bpc, check_image, read_bits, sample_order, sample_view
from .core import CapacityError, ExtractResult

MODE = "image_fragile"


def capacity(image: np.ndarray, *, bits_per_channel: int = 1, profile: str = "standard", use_alpha: bool = False) -> int:
    """Largest payload (bytes) this image can hold with the given settings."""
    samples = sample_view(np.asarray(image), use_alpha).size
    return max(0, samples * bits_per_channel // 8 - pl.OVERHEAD[profile])


def embed(image: np.ndarray, payload: bytes, key: bytes | str, *, bits_per_channel: int = 1,
          profile: str = "standard", use_alpha: bool = False) -> np.ndarray:
    image = check_image(image)
    check_bpc(bits_per_channel)
    out = image.copy()
    view = sample_view(out, use_alpha)
    flat = view.reshape(-1).copy()
    bits = np.unpackbits(np.frombuffer(pl.pack(payload, key, profile), dtype=np.uint8))
    needed = -(-bits.size // bits_per_channel)
    if needed > flat.size:
        raise CapacityError(f"frame needs {bits.size} bits; image holds {flat.size * bits_per_channel}")
    bits = np.concatenate([bits, np.zeros(needed * bits_per_channel - bits.size, dtype=np.uint8)])
    chunks = bits.reshape(needed, bits_per_channel)
    values = (chunks << np.arange(bits_per_channel - 1, -1, -1, dtype=np.uint8)).sum(axis=1).astype(np.uint8)
    positions = sample_order(key, flat.size, needed)
    mask = np.uint8(0xFF ^ ((1 << bits_per_channel) - 1))
    flat[positions] = (flat[positions] & mask) | values
    view[...] = flat.reshape(view.shape)
    return out


def extract(image: np.ndarray, key: bytes | str, *, bits_per_channel: int | None = None,
            profile: str = "standard", use_alpha: bool = False) -> ExtractResult:
    image = check_image(image)
    flat = sample_view(image, use_alpha).reshape(-1)
    tried = []
    for bpc in ([bits_per_channel] if bits_per_channel else [1, 2]):
        check_bpc(bpc)
        header = read_bits(flat, key, bpc, pl.HEADER_SIZE[profile])
        length = pl.length_from_header(header, profile) if header else None
        if length is None or pl.frame_size(profile, length) * 8 > flat.size * bpc:
            tried.append(bpc)
            continue
        data = pl.unpack(read_bits(flat, key, bpc, pl.frame_size(profile, length)), key, profile)
        if data is not None:
            return ExtractResult(MODE, True, 1.0, data, {"bits_per_channel": bpc, "profile": profile})
        tried.append(bpc)
    return ExtractResult.failed(MODE, "no authenticated frame", bits_per_channel_tried=tried, profile=profile)
