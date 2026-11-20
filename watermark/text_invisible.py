"""Mode 3 - invisible-character text watermark.

The frame is written with zero-width characters placed between visible
characters, so it travels with copy and paste:

    U+200B ZERO WIDTH SPACE  -> bit 0
    U+2060 WORD JOINER       -> bit 1
    U+2063 INVISIBLE SEPARATOR marks the start and end of each copy

Characters are never inserted inside a grapheme (before combining marks,
around zero-width joiners, inside flag pairs or skin-tone sequences, or
between CR and LF). Retyping, OCR and many "plain text" sanitisers remove
these characters; that is a known limit of this mode.
"""
from __future__ import annotations

import unicodedata

from . import payload as pl
from .core import CapacityError, ExtractResult

MODE = "text_invisible"
ZERO, ONE, SEP = "​", "⁠", "⁣"
ALPHABET = {ZERO: 0, ONE: 1}
_ZWJ = "‍"

def strip(text: str) -> str:
    """Remove every character this mode uses (e.g. to clear an old watermark)"""
    return text.translate({ord(c): None for c in (ZERO, ONE, SEP)})
    
def embed(text: str, payload: bytes, key: bytes | str, *, copies: int = 1, profile: str = "standard",
          strip_existing: bool = True) -> str:
    """Insert `copies` complete frames, each spread over its own stretch of the text

    Several copies make it so that a partial copy-paste can still carry one whole frame
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if copies < 1:
        raise ValueError("copies must be at least 1")
    if strip_existing:
        text = strip(text)
    gaps = _gaps(text)
    if len(gaps) < copies:
        raise CapacityError(f"text has {len(gaps)} insertion points; {copies} copies requested")
    symbols = SEP + "".join(ONE if b else ZERO for b in pl.to_bits(pl.pack(payload, key, profile))) + SEP
    inserts: dict[int, list[str]] = {}
    for c in range(copies):
        group = gaps[c * len(gaps) // copies : (c + 1) * len(gaps) // copies]
        for j, ch in enumerate(symbols):
            inserts.setdefault(group[j * len(group) // len(symbols)], []).append(ch)
    out = []
    for i, ch in enumerate(text):
        out.extend(inserts.get(i, ()))
        out.append(ch)
    out.extend(inserts.get(len(text), ()))
    return "".join(out)
