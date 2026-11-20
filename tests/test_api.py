import os
import numpy as np
import pytest
import tracemark as wm

def test_identify_finds_recipient():
    keys = {"alice": os.urandom(32), "bob": os.urandom(32), "carol": os.urandom(32)}
    leak = wm.embed("Confidential memo. Do not share.", b"memo-42", keys["bob"], mode="text_invisible")
    result = wm.identify(leak, keys, mode="text_invisible")
    assert result.status == "match" and result.best == "bob"
    assert result.results["bob"].payload == b"memo-42"

def test_identify_none(key, other_key):
    leak = wm.embed("Some text here.", b"x", os.urandom(32), mode="text_invisible")
    assert wm.identify(leak, [key, other_key], mode="text_invisible").status == "none"

def test_unknown_mode_and_payload_type(key):
    with pytest.raises(wm.UnsupportedModeError):
        wm.embed("text", b"x", key, mode="nope")
    with pytest.raises(TypeError):
        wm.embed("text", "not bytes", key, mode="text_invisible")
