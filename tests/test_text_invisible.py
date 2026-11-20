import watermark as wm
from watermark import text_invisible as ti

MODE = "text_invisible"
TEXT = "Hello 世界! Café ñ é 👍🏽 family 👨‍👩‍👧 flag 🇭🇰 end.\r\nSecond line here."

def test_roundtrip_keeps_visible_text(key):
    marked = wm.embed(TEXT, b"Student-123", key, mode=MODE)
    assert ti.strip(marked) == TEXT
    result = wm.extract(marked, key, mode=MODE)
    assert result.verified and result.payload == b"Student-123"

def test_never_splits_graphemes(key):
    marked = wm.embed(TEXT, b"x" * 40, key, mode=MODE)
    for ch in (ti.ZERO, ti.ONE, ti.SEP):
        assert ch + "́" not in marked
        assert ch + "‍" not in marked and "‍" + ch not in marked
        assert ch + "\U0001F3FD" not in marked
        assert "\r" + ch + "\n" not in marked

def test_rejections(key, other_key):
    marked = wm.embed(TEXT, b"x", key, mode=MODE)
    assert not wm.extract(marked, other_key, mode=MODE).verified
    assert not wm.extract(TEXT, key, mode=MODE).verified
