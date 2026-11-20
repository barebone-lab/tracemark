import tracemark as wm
from tracemark import text_invisible as ti

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

def test_partial_copy_with_several_copies(key):
    text = "The quick brown fox jumps over the lazy dog. " * 60
    marked = wm.embed(text, b"ID7", key, mode=MODE, copies=3)
    assert wm.extract(marked[: len(marked) // 2], key, mode=MODE).payload == b"ID7"

def test_reembed_replaces_old_mark(key):
    first = wm.embed(TEXT, b"old", key, mode=MODE)
    assert wm.extract(wm.embed(first, b"new", key, mode=MODE), key, mode=MODE).payload == b"new"
