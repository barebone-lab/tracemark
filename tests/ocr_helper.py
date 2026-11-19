"""Render text to page images and read it back with Tesseract (test helper only)."""
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
TIMES = "/System/Library/Fonts/Supplemental/Times New Roman.ttf"


def available(font=ARIAL) -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None and Path(font).exists()


def ocr_roundtrip(text: str, font: str = ARIAL, pixels: int = 30) -> str:
    """Same layout as the validation runs: 2000x2600 px pages at 200 dpi, Tesseract --psm 6."""
    from PIL import Image, ImageDraw, ImageFont

    face = ImageFont.truetype(font, pixels)
    lines = textwrap.wrap(" ".join(text.split()), width=115, break_long_words=False, break_on_hyphens=False)
    per_page = (2600 - 160) // 42
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(0, len(lines), per_page):
            page = Image.new("RGB", (2000, 2600), "white")
            draw = ImageDraw.Draw(page)
            for j, line in enumerate(lines[i:i + per_page]):
                draw.text((80, 80 + j * 42), line, font=face, fill="black")
            png = Path(tmp) / f"p{i}.png"
            page.save(png, dpi=(200, 200))
            out.append(subprocess.run(["tesseract", str(png), "stdout", "-l", "eng", "--dpi", "200", "--psm", "6"],
                                      capture_output=True, text=True, check=True).stdout)
    return "\n".join(out)
