"""General-purpose digital watermarking: hide bytes in images or text, then read them back.

Modes: image_fragile, image_robust, text_invisible, text_lexical, text_generative.
"""
from .api import embed, extract, identify
from .core import CapacityError, ExtractResult, IdentifyResult, UnsupportedModeError, WatermarkError

__all__ = ["embed", "extract", "identify", "ExtractResult", "IdentifyResult",
           "WatermarkError", "CapacityError", "UnsupportedModeError"]
