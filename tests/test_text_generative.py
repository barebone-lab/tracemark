"""Mode 5 tests with a small fake language model (no download, no GPU).

A real-model check runs only when WATERMARK_LLM names an mlx-lm model.
"""
import hashlib
import os

import numpy as np
import pytest

import watermark as wm
from watermark import text_generative as tg

MODE = "text_generative"
VOCAB = 400


class FakeModel:
    """Tokens are words 'w<id>'. Next-token logits depend on the last two tokens;
    every fourth context is nearly certain, the rest have a few plausible choices."""

    eos_ids = {0}

    def encode(self, text):
        return [int(w[1:]) for w in text.split()]

    def decode(self, ids):
        return " ".join(f"w{i}" for i in ids)

    def prompt_ids(self, prompt, chat):
        return self.encode(prompt)

    def _logits(self, ids):
        h = int.from_bytes(hashlib.sha256(str(ids[-2:]).encode()).digest()[:8], "little")
        rng = np.random.default_rng(h)
        logits = rng.normal(0, 1.0, VOCAB) - 6.0
        logits[0] = -30.0                                   # no early end
        top = rng.choice(np.arange(1, VOCAB), 8 if h % 4 else 1, replace=False)
        logits[top] = rng.normal(2.0, 0.6, top.size) + (6.0 if h % 4 == 0 else 0)
        return logits

    def logits(self, prompt_ids):
        ids = list(prompt_ids)
        while True:
            ids.append((yield self._logits(ids)))


@pytest.fixture(scope="module")
def model():
    return FakeModel()


def write(model, key, seed, n=200):
    return wm.embed("w5 w9 w14", b"", key, mode=MODE, model=model, max_tokens=n, seed=seed)


def test_round_trip_and_wrong_key(model):
    text = write(model, "alice", 1)
    good = wm.extract(text, "alice", mode=MODE, tokenizer=model)
    bad = wm.extract(text, "bob", mode=MODE, tokenizer=model)
    assert good.verified and good.payload == b"" and good.details["z"] > 8
    assert not bad.verified and abs(bad.details["z"]) < 4.0


def test_identify_picks_the_right_person(model):
    keys = {f"student{i}": f"key-{i}" for i in range(20)}
    for i in (0, 7, 19):
        text = write(model, keys[f"student{i}"], i)
        who = wm.identify(text, keys, mode=MODE, tokenizer=model)
        assert who.status == "match" and who.best == f"student{i}"


def test_unmarked_text_matches_no_one(model):
    rng = np.random.default_rng(0)
    for trial in range(5):
        ids = rng.integers(1, VOCAB, 300).tolist()
        who = wm.identify(model.decode(ids), {i: f"key-{i}" for i in range(50)}, mode=MODE, tokenizer=model)
        assert who.status == "none"


def test_survives_partial_edits(model):
    words = write(model, "alice", 4, n=400).split()
    found = 0
    for seed in range(5):
        rng = np.random.default_rng(seed)
        kept = [w if rng.random() > 0.2 else f"w{rng.integers(1, VOCAB)}" for w in words]   # 20% replaced
        excerpt = " ".join(kept[60:300])                                                      # and cut
        found += wm.extract(excerpt, "alice", mode=MODE, tokenizer=model).verified
    assert found >= 4


def test_mixed_into_other_text(model):
    rng = np.random.default_rng(2)
    filler = " ".join(f"w{i}" for i in rng.integers(1, VOCAB, 150))
    text = filler + " " + write(model, "alice", 5, n=150) + " " + filler
    assert wm.extract(text, "alice", mode=MODE, tokenizer=model).verified


def test_too_short_is_never_verified(model):
    text = write(model, "alice", 6, n=tg.MIN_SCORED)
    r = wm.extract(text, "alice", mode=MODE, tokenizer=model)
    assert not r.verified and r.details["tokens_scored"] < tg.MIN_SCORED


def test_null_is_calibrated(model):
    """Random keys on one marked text: z should look standard normal."""
    text = write(model, "alice", 7, n=300)
    zs = np.array([wm.extract(text, f"other-{i}", mode=MODE, tokenizer=model).details["z"] for i in range(300)])
    assert abs(zs.mean()) < 0.25 and 0.8 < zs.std() < 1.2 and zs.max() < 4.0


def test_tournament_keeps_the_model_distribution_on_average():
    """Averaged over keys the tournament is exactly the model's distribution, and it
    never picks a token the model ruled out."""
    p = np.array([0.6, 0.25, 0.1, 0.05, 0.0])
    rng = np.random.default_rng(0)
    runs = np.array([tg._tournament(p, rng.integers(0, 2, (tg.LAYERS, p.size)).astype(float)) for _ in range(20000)])
    assert np.all(runs[:, -1] == 0)
    assert np.allclose(runs.mean(0), p, atol=0.01)


def test_bad_arguments(model):
    with pytest.raises(ValueError):
        wm.embed("w1 w2", b"x", "k", mode=MODE, model=model)
    with pytest.raises(ValueError):
        wm.embed("w1 w2", b"", "k", mode=MODE, model=model, temperature=0)
    with pytest.raises(TypeError):
        wm.extract("w1 w2", "", mode=MODE, tokenizer=model)


@pytest.mark.skipif(not os.environ.get("WATERMARK_LLM"), reason="set WATERMARK_LLM to an mlx-lm model to run")
def test_real_model():
    llm = tg.MLXModel(os.environ["WATERMARK_LLM"])
    text = wm.embed("Write a short paragraph about how bees find flowers.", b"", "alice", mode=MODE,
                    model=llm, max_tokens=200, seed=0)
    assert wm.identify(text, {"alice": "alice", "bob": "bob"}, mode=MODE, tokenizer=llm).best == "alice"


def test_marked_passage_found_inside_long_unmarked_text(model):
    rng = np.random.default_rng(8)
    filler = lambda n: " ".join(f"w{i}" for i in rng.integers(1, VOCAB, n))
    text = filler(500) + " " + write(model, "alice", 9, n=90) + " " + filler(500)
    r = wm.extract(text, "alice", mode=MODE, tokenizer=model)
    assert r.verified and r.details["window_z"] >= r.details["window_z_min"]
    assert r.details["z"] < r.details["z_min"]          # the whole-text average alone would miss it


def test_identify_threshold_rises_with_more_keys(model):
    text = write(model, "alice", 10, n=200)
    few = tg.extract_many(text, [("a", "alice")], tokenizer=model)["a"]
    many = tg.extract_many(text, [(i, f"k{i}") for i in range(999)] + [("a", "alice")], tokenizer=model)["a"]
    assert few.verified and many.verified and many.details["z_min"] > few.details["z_min"]


def test_windows_keep_the_false_positive_rate(model):
    """Unmarked long texts against many keys: the window search must not add false matches."""
    rng = np.random.default_rng(11)
    hits = 0
    for trial in range(20):
        text = " ".join(f"w{i}" for i in rng.integers(1, VOCAB, 1200))
        hits += sum(r.verified for r in tg.extract_many(text, [(i, f"k{i}") for i in range(100)],
                                                        tokenizer=model, false_positive=1e-2).values())
    assert hits <= 50          # expected at most 20 at a 1% per-key rate (Bonferroni is conservative)
