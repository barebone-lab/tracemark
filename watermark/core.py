"""Shared result types and errors used by every watermark mode."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MODES = ("image_fragile", "image_robust", "text_invisible", "text_lexical", "text_generative")


class WatermarkError(Exception):
    """Base class for every error raised by this package."""


class CapacityError(WatermarkError):
    """The content cannot hold the requested payload. Nothing is truncated."""


class UnsupportedModeError(WatermarkError):
    """The requested mode does not exist or is not implemented yet."""


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of one extraction attempt.

    verified: True only when the authentication tag matched the key.
    score:    0.0 when not verified; otherwise 1.0 minus the share of the
              error-correction margin that was used (1.0 = no errors seen).
    payload:  the hidden bytes, or None when not verified.
    """

    mode: str
    verified: bool
    score: float
    payload: bytes | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def failed(mode: str, reason: str, **details: Any) -> "ExtractResult":
        return ExtractResult(mode=mode, verified=False, score=0.0, payload=None, details={"reason": reason, **details})


@dataclass(frozen=True)
class IdentifyResult:
    """Outcome of comparing one content against several candidate keys.

    status: "match"     exactly one key verified;
            "none"      no key verified;
            "ambiguous" more than one key verified (should not happen with
                        distinct keys; treat as suspicious).
    best:   label of the best-scoring verified key, or None.
    results: label -> ExtractResult for every candidate.
    """

    status: str
    best: Any
    results: dict[Any, ExtractResult]


def margin_score(corrected: int, correctable: int) -> float:
    """Map 'errors corrected out of the correctable budget' to a score in (0, 1]."""
    if correctable <= 0:
        return 1.0 if corrected == 0 else 0.0
    return max(0.0, 1.0 - corrected / (correctable + 1))
