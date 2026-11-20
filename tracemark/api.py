"""One entry point for every mode: embed, extract, identify.

    from tracemark import embed, extract, identify
    marked = embed(image, b"Student-123", key, mode="image_fragile")
    result = extract(marked, key, mode="image_fragile")   # ExtractResult
    who = identify(leaked, {"alice": k1, "bob": k2}, mode="text_invisible")

Content is always passed and returned in memory (numpy arrays for images,
str for text); nothing is read from or written to disk. Extra keyword
arguments go to the chosen mode (see each module's embed/extract).
"""
from __future__ import annotations

from importlib import import_module
from typing import Any, Iterable, Mapping

from .core import MODES, ExtractResult, IdentifyResult, UnsupportedModeError

_MODULES = {
    "image_fragile": ".image_fragile",
    "text_invisible": ".text_invisible",
    "text_lexical": ".text_lexical",
    "image_robust": ".image_robust",
    "text_generative": ".text_generative",
}


def _module(mode: str):
    if mode not in MODES:
        raise UnsupportedModeError(f"unknown mode {mode!r}; choose from {MODES}")
    if mode not in _MODULES:
        raise UnsupportedModeError(f"mode {mode!r} is not implemented yet")
    return import_module(_MODULES[mode], __package__)


def embed(content: Any, payload: bytes, key: bytes | str, *, mode: str, **options) -> Any:
    """Return a watermarked copy of `content`. The input is not modified."""
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes; encode text or images to bytes first")
    return _module(mode).embed(content, bytes(payload), key, **options)


def extract(content: Any, key: bytes | str, *, mode: str, **options) -> ExtractResult:
    return _module(mode).extract(content, key, **options)


def identify(content: Any, keys: Mapping[Any, bytes | str] | Iterable[bytes | str], *, mode: str,
             **options) -> IdentifyResult:
    """Try every candidate key; report which one (if any) the content belongs to.

    keys: {label: key} or a plain list (labels are then the list positions).
    """
    items = list(keys.items()) if isinstance(keys, Mapping) else list(enumerate(keys))
    many = getattr(_module(mode), "extract_many", None)  # shares key-independent work between keys
    results: dict[Any, ExtractResult] = many(content, items, **options) if many else \
        {label: extract(content, k, mode=mode, **options) for label, k in items}
    verified = [label for label, r in results.items() if r.verified]
    if not verified:
        return IdentifyResult("none", None, results)
    best = max(verified, key=lambda label: results[label].score)
    return IdentifyResult("match" if len(verified) == 1 else "ambiguous", best, results)
