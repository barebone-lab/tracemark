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

