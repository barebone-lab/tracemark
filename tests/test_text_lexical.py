import json
import os
from pathlib import Path

import pytest

import tracemark as wm
from tracemark.text_lexical import WORD, Lexicon, Substitution

MODE = "text_lexical"
FIXTURE = json.loads((Path(__file__).parent / "data/lexical_fixture.json").read_text(encoding="utf-8"))
TEXT = FIXTURE["text"]
SUBS = [Substitution(*s) for s in FIXTURE["substitutions"]]


def prefix(words):
    ms = list(WORD.finditer(TEXT))
    end = ms[words - 1].end()
    return TEXT[:end] + "\n", [s for s in SUBS if s.end <= end]


@pytest.fixture(scope="module")
def marked(fixed_key):
    return wm.embed(TEXT, b"ID7", fixed_key, mode=MODE, candidates=SUBS, return_details=True)


def test_default_lexicon():
    lex = Lexicon.default()
    assert lex.version == "lexicon_v1" and lex.size == 9707


def test_embed_extract(marked, fixed_key):
    text, info = marked
    assert info["parity"] >= 4 and 0 < len(info["changes"]) <= 100
    result = wm.extract(text, fixed_key, mode=MODE)
    assert result.verified and result.payload == b"ID7" and result.score == 1.0


def test_unmarked_and_wrong_keys(marked, fixed_key):
    text, _ = marked
    assert not wm.extract(TEXT, fixed_key, mode=MODE).verified
    assert not any(wm.extract(text, os.urandom(32), mode=MODE).verified for _ in range(30))


def test_retyped_survives(marked, fixed_key):
    retyped = " ".join(WORD.findall(marked[0])).upper()
    assert wm.extract(retyped, fixed_key, mode=MODE).payload == b"ID7"


def test_short_text_reports_capacity(fixed_key):
    text, subs = prefix(3000)
    with pytest.raises(wm.CapacityError):
        wm.embed(text, b"ID7", fixed_key, mode=MODE, candidates=subs)


def test_candidates_must_match_text(fixed_key):
    bogus = [Substitution(0, 3, "xyz", "abc")]
    with pytest.raises(wm.CapacityError):
        wm.embed(TEXT, b"ID7", fixed_key, mode=MODE, candidates=bogus)
    with pytest.raises(ValueError):
        wm.embed(TEXT, b"ID7", fixed_key, mode=MODE)


@pytest.mark.skipif(not os.environ.get("RUN_DEEPSEEK"), reason="set RUN_DEEPSEEK=1 and DEEPSEEK_API_KEY to call the API")
def test_deepseek_provider_live():
    from tracemark.text_lexical import DeepSeekProvider
    text, _ = prefix(300)
    subs = DeepSeekProvider().propose(text)
    assert all(text[s.start:s.end] == s.original for s in subs)


# ---------------------------------------------------------------- key only (payload b"")
@pytest.fixture(scope="module")
def key_only():
    text, subs = prefix(2000)
    keys = {f"student{i}": f"key-{i}" for i in range(30)}
    marked, info = wm.embed(text, b"", keys["student7"], mode=MODE, candidates=subs, return_details=True)
    return text, marked, info, keys


def test_key_only_needs_far_less_text(key_only, fixed_key):
    text, marked, info, keys = key_only
    assert info["key_only"] and info["checks"] >= 20 and 0 < len(info["changes"]) <= 40
    r = wm.extract(marked, keys["student7"], mode=MODE)
    assert r.verified and r.payload == b"" and r.details["agree"] == r.details["checks"]
    with pytest.raises(wm.CapacityError):        # the same text cannot hold even a 3-byte ID
        wm.embed(text, b"ID7", fixed_key, mode=MODE, candidates=prefix(2000)[1])


def test_key_only_identifies_the_student(key_only):
    text, marked, _, keys = key_only
    who = wm.identify(marked, keys, mode=MODE)
    assert who.status == "match" and who.best == "student7"
    assert wm.identify(text, keys, mode=MODE).status == "none"


def test_key_only_survives_retyping(key_only):
    _, marked, _, keys = key_only
    retyped = " ".join(WORD.findall(marked)).upper()
    assert wm.extract(retyped, keys["student7"], mode=MODE).verified


def test_key_only_null_is_calibrated(key_only):
    """Random keys on an unmarked text: the reported p must not be too small too often."""
    from tracemark.text_lexical import _KeyOnly
    text = key_only[0]
    ps = [_KeyOnly(Lexicon.default(), os.urandom(32)).read(text)["p_adjusted"] for _ in range(400)]
    assert sum(p <= 0.05 for p in ps) <= 40 and min(ps) > 1e-4


def test_key_only_short_text_reports_capacity():
    text, subs = prefix(1000)
    with pytest.raises(wm.CapacityError):
        wm.embed(text, b"", "k", mode=MODE, candidates=subs)
