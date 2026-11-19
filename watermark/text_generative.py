"""Mode 5 - generation-time text watermark (keyed tournament sampling).

The watermark is put in while a language model writes the text, so it costs
no visible change of any kind: every token is still drawn from (a reweighted
copy of) the model's own distribution. It marks *who* the text was generated
for through the key; it carries no payload bytes. Use identify() with one key
per person to find whose copy a text is.

How it works
------------
* At every step the previous CONTEXT tokens and the key give a seed; the seed
  gives each candidate token LAYERS keyed bits g_1..g_L (0 or 1).
* The next token is drawn as the winner of a knockout tournament between
  samples of the model's distribution, where each round keeps the candidate
  with the higher g. This is computed exactly, without drawing the samples:
  each round maps p(x) to p(x) * (1 + g(x) - sum_y p(y) g(y)).
  Tokens with high g become a little more likely; the distribution stays a
  valid distribution and is unchanged when the model is certain.
* A step whose context already appeared earlier in the text is not marked,
  so repeated phrases do not repeat the same bias.
* Reading needs only the text, the tokenizer and the key: re-tokenise and
  score every first-seen (context, token) by its g bits minus 1/2, early
  rounds weighted more (they carry most of the signal). Any text not made
  with this key scores like fair coin flips, so the z-score is exact and the
  threshold follows from the false-positive rate asked for.
* The whole text is tested, and so is every window of 64, 128 and 256 scored
  tokens (Bonferroni-corrected), so a marked passage pasted into a longer
  text is still found. identify() raises the threshold with the number of
  keys so that the chance of any wrong match stays at 0.1%.

Measured (Qwen3.5 4-bit on an M5, temperature 0.8, up to 300 tokens per text).
Choosing the model (12 prompts, earlier settings: CONTEXT 4, unweighted):

    model   tok/s  found at 50 words  at 100 words  quality
    0.8B     153        9/12             10/11      often wrong content
    2B        82        7/12             12/12      default: the balance
    4B        43        3/12              7/12      best, but more certain, so less signal

Defaults on 2B (24 prompts; 4,800 wrong-key checks, 1 hit by chance; a separate
200,000-check null test gave 151 hits at false_positive=1e-3, under the 200 allowed):

    CONTEXT  100 words  10% replaced  20% replaced  20% deleted  100 inside 400  paraphrased
       2       19/23       22/24         14/24         19/24         14/24          1/24
       4       17/23       17/24          8/24         10/24         14/24          1/24

Before the window search, 100 marked words inside 400 unmarked ones were found
in 1 of 12 texts (2B). Paraphrasing by another model removes the mark.

Limits: the signal grows with the number of tokens and with how many choices
the model had. Paraphrasing, translation or heavy editing removes it. The
tokenizer used for reading must be the one used for writing.

Writing needs a model object (see MLXModel, which needs the optional
`generative` dependencies, Apple silicon only). Reading accepts any object
with encode(text) -> list[int], e.g. MLXModel or HFTokenizer.
"""
from __future__ import annotations

import hashlib
import math
from statistics import NormalDist
from typing import Generator, Iterable, Protocol, Sequence

import numpy as np

from .core import ExtractResult

MODE = "text_generative"
CONTEXT = 2             # previous tokens hashed into each step's seed (2 beat 1, 3 and 4 on edits)
LAYERS = 30             # tournament rounds (more = stronger signal, same quality)
TOP_K = 64              # candidates kept per step before the tournament
TEMPERATURE = 0.8
MAX_TOKENS = 400
FALSE_POSITIVE = 3e-5   # extract(): chance that a text not made with the key is verified
IDENTIFY_FALSE_POSITIVE = 1e-3  # identify(): chance that any of the keys is wrongly matched
WINDOWS = (64, 128, 256)        # scored-token windows searched for a marked passage in a longer text
MIN_SCORED = 8          # fewer scored tokens than this: never verified
DEFAULT_MODEL = "mlx-community/Qwen3.5-2B-4bit"

_M1, _M2, _G = np.uint64(0xBF58476D1CE4E5B9), np.uint64(0x94D049BB133111EB), np.uint64(0x9E3779B97F4A7C15)
_LAYER_SALT = (np.arange(1, LAYERS + 1, dtype=np.uint64) * np.uint64(0xD1B54A32D192ED03))
# Early rounds carry most of the signal (later rounds act on an already sharpened
# distribution); this linear weighting is within 2% of the measured optimum.
_WEIGHTS = 1.0 - 0.8 * np.arange(LAYERS) / (LAYERS - 1)
_WEIGHTS_NORM = _WEIGHTS / math.sqrt(0.25 * float(_WEIGHTS @ _WEIGHTS))  # unit variance per token under the null


class Tokenizer(Protocol):
    def encode(self, text: str) -> list[int]: ...


class LanguageModel(Tokenizer, Protocol):
    eos_ids: set[int]

    def decode(self, ids: Sequence[int]) -> str: ...

    def prompt_ids(self, prompt: str, chat: bool) -> list[int]: ...

    def logits(self, prompt_ids: Sequence[int]) -> Generator[np.ndarray, int, None]:
        """Yield next-token logits; receive the chosen token through send()."""


# ====================================================================== public
def embed(prompt: str, payload: bytes, key: bytes | str, *, model: LanguageModel, max_tokens: int = MAX_TOKENS,
          temperature: float = TEMPERATURE, top_k: int = TOP_K, chat: bool = True, seed: int | None = None) -> str:
    """Generate an answer to `prompt` with `model`, watermarked with `key`; return the text.

    payload must be empty: this mode marks who the text is for through the key alone.
    chat: wrap the prompt in the model's chat template (False continues raw text).
    seed: fixes the sampling randomness (the watermark does not depend on it).
    """
    if not isinstance(prompt, str):
        raise TypeError("prompt must be str")
    if payload:
        raise ValueError("text_generative carries no payload; pass b'' and use one key per person with identify()")
    if not 0 < temperature <= 2 or top_k < 1 or max_tokens < 1:
        raise ValueError("need 0 < temperature <= 2, top_k >= 1 and max_tokens >= 1")
    return model.decode(_generate(model, model.prompt_ids(prompt, chat), _secret(key), max_tokens,
                                  temperature, top_k, np.random.default_rng(seed)))


def extract(text: str, key: bytes | str, *, tokenizer: Tokenizer, false_positive: float = FALSE_POSITIVE) -> ExtractResult:
    """Verified when the whole text, or one passage of it, is far more key-aligned than chance.

    false_positive: the chance that a text not made with this key is verified.
    """
    return _verdict(_token_scores(_scored(tokenizer.encode(text)), _secret(key)), false_positive)


def extract_many(text: str, items: Iterable[tuple], *, tokenizer: Tokenizer,
                 false_positive: float | None = None) -> dict:
    """extract() for several (label, key) pairs, tokenising and indexing the text once.

    By default the per-key threshold rises with the number of keys, so the chance
    that any wrong key is matched stays at IDENTIFY_FALSE_POSITIVE.
    """
    items = list(items)
    if false_positive is None:
        false_positive = min(FALSE_POSITIVE, IDENTIFY_FALSE_POSITIVE / max(1, len(items)))
    scored = _scored(tokenizer.encode(text))
    return {label: _verdict(_token_scores(scored, _secret(key)), false_positive) for label, key in items}


# ====================================================================== core
def _secret(key: bytes | str) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not isinstance(key, (bytes, bytearray)) or not key:
        raise TypeError("key must be non-empty bytes or str")
    return hashlib.sha256(b"watermark/text_generative/v1\0" + bytes(key)).digest()


def _seed(secret: bytes, context: Sequence[int]) -> np.uint64:
    data = np.asarray(context, dtype="<u4").tobytes()
    return np.uint64(int.from_bytes(hashlib.blake2b(data, key=secret, digest_size=8).digest(), "little"))


def _g(seed, tokens: np.ndarray) -> np.ndarray:
    """Keyed bits, shape (LAYERS, len(tokens)): splitmix64 of seed, token and layer.
    seed is one seed for all tokens, or one seed per token."""
    z = ((tokens.astype(np.uint64) * _G) ^ np.asarray(seed, dtype=np.uint64))[None, :] ^ _LAYER_SALT[:, None]
    z = (z ^ (z >> np.uint64(30))) * _M1
    z = (z ^ (z >> np.uint64(27))) * _M2
    return ((z ^ (z >> np.uint64(31))) >> np.uint64(63)).astype(np.float64)


def _tournament(p: np.ndarray, g: np.ndarray) -> np.ndarray:
    for layer in g:
        p = p * np.maximum(1.0 + layer - p @ layer, 0.0)  # 1 - P can round below 0
    return p / p.sum()


def _generate(model, prompt_ids, secret, max_tokens, temperature, top_k, rng) -> list[int]:
    out: list[int] = []
    seen: set[tuple] = set()
    stream = model.logits(prompt_ids)
    logits = next(stream)
    history = list(prompt_ids[-CONTEXT:])
    for _ in range(max_tokens):
        logits = np.asarray(logits, dtype=np.float64).reshape(-1)
        k = min(top_k, logits.size)
        cand = np.argpartition(logits, -k)[-k:]
        p = np.exp((logits[cand] - logits[cand].max()) / temperature)
        p /= p.sum()
        context = tuple(history[-CONTEXT:])
        if len(context) == CONTEXT and context not in seen:
            seen.add(context)
            p = _tournament(p, _g(_seed(secret, context), cand))
        token = int(cand[rng.choice(k, p=p)])
        if token in model.eos_ids:
            break
        out.append(token)
        history.append(token)
        logits = stream.send(token)
    stream.close()
    return out


def _scored(ids: Sequence[int]) -> list[tuple[tuple, int]]:
    """(context, token) for each first-seen context with a full context inside the text."""
    seen, pairs = set(), []
    for t in range(CONTEXT, len(ids)):
        context = tuple(ids[t - CONTEXT:t])
        if context not in seen:
            seen.add(context)
            pairs.append((context, ids[t]))
    return pairs


def _token_scores(pairs, secret) -> np.ndarray:
    """Per scored token: weighted sum of (g - 1/2) over the rounds, unit variance under the null."""
    if not pairs:
        return np.zeros(0)
    seeds = np.array([_seed(secret, c) for c, _ in pairs], dtype=np.uint64)
    g = _g(seeds, np.array([t for _, t in pairs], dtype=np.int64))
    return _WEIGHTS_NORM @ (g - 0.5)


def _windows(n: int) -> list[tuple[int, int]]:
    spans = set()
    for w in WINDOWS:
        if w < n:
            spans.update((a, a + w) for a in range(0, n - w + 1, w // 4))
            spans.add((n - w, n))
    return sorted(spans)


def _z_for(p: float) -> float:
    return NormalDist().inv_cdf(1.0 - p)


def _verdict(scores: np.ndarray, false_positive: float) -> ExtractResult:
    """Whole text at half the false-positive budget, every window together at the other half."""
    if not 0 < false_positive < 0.5:
        raise ValueError("false_positive must be in (0, 0.5)")
    n = scores.size
    spans = _windows(n)
    z_full = float(scores.sum() / math.sqrt(n)) if n else 0.0
    full_min = _z_for(false_positive / 2 if spans else false_positive)
    details = {"z": round(z_full, 2), "z_min": round(full_min, 2), "tokens_scored": n}
    z_best = z_full / full_min
    if spans:
        c = np.concatenate([[0.0], np.cumsum(scores)])
        zs = np.array([(c[b] - c[a]) / math.sqrt(b - a) for a, b in spans])
        best = int(np.argmax(zs))
        window_min = _z_for(false_positive / 2 / len(spans))
        details.update(window_z=round(float(zs[best]), 2), window_z_min=round(window_min, 2),
                       window=spans[best], windows=len(spans))
        z_best = max(z_best, float(zs[best]) / window_min)
    if n < MIN_SCORED or z_best < 1.0:
        return ExtractResult.failed(MODE, "no watermark for this key", **details)
    return ExtractResult(MODE, True, min(1.0, z_best / 2), b"", details)


# ====================================================================== backends
class HFTokenizer:
    """Tokenizer only (for reading); needs `transformers`, works on any platform."""

    def __init__(self, repo: str = DEFAULT_MODEL):
        from transformers import AutoTokenizer
        self._tok = AutoTokenizer.from_pretrained(repo)

    def encode(self, text: str) -> list[int]:
        return self._tok.encode(text, add_special_tokens=False)


class MLXModel:
    """A local model through mlx-lm (Apple silicon). Downloads `repo` on first use."""

    def __init__(self, repo: str = DEFAULT_MODEL):
        from mlx_lm import load
        self.repo = repo
        self._model, self._tok = load(repo)
        self.eos_ids = set(self._tok.eos_token_ids)

    def encode(self, text: str) -> list[int]:
        return self._tok.encode(text, add_special_tokens=False)

    def decode(self, ids: Sequence[int]) -> str:
        return self._tok.decode(list(ids))

    def prompt_ids(self, prompt: str, chat: bool) -> list[int]:
        if not chat:
            return self.encode(prompt)
        messages = [{"role": "user", "content": prompt}]
        try:
            return list(self._tok.apply_chat_template(messages, add_generation_prompt=True, enable_thinking=False))
        except TypeError:
            return list(self._tok.apply_chat_template(messages, add_generation_prompt=True))

    def logits(self, prompt_ids):
        import mlx.core as mx
        from mlx_lm.models.cache import make_prompt_cache
        cache = make_prompt_cache(self._model)
        out = self._model(mx.array(list(prompt_ids))[None], cache=cache)
        while True:
            token = yield np.array(out[0, -1].astype(mx.float32))
            out = self._model(mx.array([[token]]), cache=cache)
