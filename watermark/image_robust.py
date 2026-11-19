"""Mode 2 - robust image watermark (for short data such as IDs).

Two layouts, chosen at embed time; extract() recognises either one by itself.

markers=False (default): invisible
------------------------------------
Nothing visible is added. The watermark is one small key-dependent tile that
repeats across the whole picture, so every piece of the picture carries a full
copy and the repetition itself is the sync signal:

* A tile is 20 x 20 blocks of 4 x 4 cells. Each block gets a key-dependent
  balanced +/-1 cell pattern; one block in eight is a known "pilot", the rest
  carry coded bits as +pattern (1) or -pattern (0).
* The tile side is half the picture's shorter side, so a copy survives
  cropping, and the picture always holds several copies.
* The luminance change is a few grey levels, scaled by local fine texture.
  Flat areas, and flat areas next to strong edges (e.g. sky above hills), get
  much less, so the pattern hides in texture instead of showing as blotches.
* Informed embedding: the embedder reads its own result and evens out the
  blocks that the picture happens to cancel or exaggerate.
* Extraction finds the repeat period and angle from the autocorrelation
  (survives scaling, cropping, rotation and screenshots), locks the offset
  with the pilot blocks, refines the geometry patch by patch, averages all
  copies and decodes. For photos of a print or a screen it also finds the
  picture's outline (re-fitting each side as a straight line, so a corner
  hidden behind e.g. the Dock or rounded off is still recovered), straightens
  it, and nudges the corners and aspect ratio to where the pilot lines up best.
  Inputs larger than 1800 px are shrunk first. As a last resort for a small
  picture among clutter (e.g. a preview window on a busy screen), outlines are
  also built from long straight edges (any three sides visible), given a quick
  pilot score, and the best few are read from the photo's full-resolution pixels.
  The very last reader handles a tilted close-up with too few repeats for the
  autocorrelation (under about two tiles each way): the tilt comes from long
  straight edges (a screen or window border) and the tile size is swept.
  Checked on real phone photos of a laptop screen (tilted, perspective, one
  corner covered; a small preview window among other windows; a tilted close-up).
* Speed: readers run cheapest first (the tile-size sweep for tight crops comes
  last). Within one call, work that does not depend on the key (filtering,
  repeat period, outlines, folds at key-independent geometry) is memoised, and
  independent pieces run on a thread pool. extract_many() (used by
  watermark.identify) shares that work between keys and tries them in parallel.

markers=True: visible corner markers
------------------------------------
Eight small ArUco markers (OpenCV DICT_4X4_50) at the corners and edge middles
give exact geometry. Use this when copies may be photographed at a steep
angle, small and blurred - the case where the invisible layout runs out of
signal. The watermark region is a 512 x 512 canonical grid of 32 x 32 blocks.

* Drawing: the markers move only the brightness toward black / white, by
  marker_strength (default 0.4; 1 = solid), and keep the picture's colours, so
  they read as soft tinted squares rather than pasted stickers.
* Reading does not need the markers to be crisp: detection runs with wide
  threshold windows and also on a local-contrast-normalised copy; the geometry
  is fitted to all four corners of every marker found (so two markers, or three
  on one edge, are enough), with the picture's unknown aspect ratio fitted too.
  If that read fails, the geometry is refined from the data blocks themselves:
  each coded bit's blocks are scattered over the picture, so their summed
  evidence is strong only when the whole grid lines up. With no usable markers
  at all, the picture's outline gives the rough geometry instead.
* Measured on simulated phone photos (5 pictures; tilt, small, blur up to
  sigma 4, glare, a window among clutter, a side cut off; 225 photos): the old
  solid markers with the old reader read 107; now 187 at the default 0.4, 190
  at 0.6, 186 at 1.0, 175 at 0.25. With the true geometry every one of those photos decodes:
  what fails is finding the geometry, not the signal.

Key only (payload b"", invisible layout)
---------------------------------------
For leak tracing only the key matters. With an empty payload the data blocks
get key-dependent signs instead of coded bits, so the whole tile (pilots and
data) is one known pattern. The same geometry search then syncs on all 400
blocks instead of the 50 pilots (about 2.8 times the signal), and the best
peak z over the whole read decides: key_only_threshold(false_positive) comes
from a Gumbel fit (NULL_LOCATION, NULL_SCALE) of that best z on pictures not
marked with the key. identify() raises it with the number of keys.
Frame reads also decode the next strongest pilot offsets (TOP_PEAKS, down to
TOP_PEAK_MIN_Z), since at low signal the true one is often not the very top;
the frame's tag rejects wrong ones.
Threshold: on 600 pictures not marked with the key (unmarked, or marked with
another key; 450-2300 px; plain, JPEG, shrunk, screenshot, rotated, phone-style)
the best z averaged 5.42 (max 6.75), so 3e-5 needs z >= 8.87.
On 3 synthetic pictures x strength 0.5 / 0.3 / 0.2 / 0.12 x 5 levels of
shrink + blur + noise + JPEG 40 (60 copies):

    strength          0.5    0.3    0.2    0.12
    hidden ID, before  11      8      4      0
    hidden ID, now     12      9      6      0     (of 15)
    key only           15     13     10      1

Both layouts: the frame from payload.py goes through a convolutional code
(K=7, rate 1/2, punctured to 2/3 or 3/4 only when a long frame needs it), and
the coded bits are repeated over every available block. Extraction adds up
each bit's evidence and runs a soft Viterbi decoder, which copes with the
scattered cell errors of a phone photo of a screen (moire, denoising,
sharpening: about 5-12 % of cells read wrong in real tests) far better than
byte-level Reed-Solomon did. A failed read returns verified=False, never a
wrong payload (the frame is authenticated).

Profiles: "compact" is the default here (4-byte HMAC tag, short frame);
"standard" (AES-SIV, 16-byte tag) costs 16 more bytes of frame, so only short
payloads fit and at a weaker code rate. Limits: the picture's shorter side must
be at least 400 px. robust_capacity() is 16 bytes compact invisible / 12 with
markers (full rate-1/2 code); capacity() allows up to 27 / 21 at a weaker
rate (standard: 11 / 5). Shorter payloads are more robust: 3-6 bytes is the
sweet spot for photos of a screen. Validated attacks (both layouts):
JPEG down to quality 30, resizing to 35 %, Gaussian blur sigma 3, noise,
tone changes, screenshots with surrounding UI, crops of 10-20 % (one side,
corner, two sides), rotation by 7 deg, phone-style photos (perspective tilt,
blur 1.2, JPEG 80). Steeper, smaller and blurrier photos need markers=True
(see the numbers above).

Images are numpy uint8 arrays (H, W) or (H, W, 3/4) in RGB(A) order. The input
is never modified.
"""
from __future__ import annotations

import collections
import contextlib
import contextvars
import functools
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import payload as pl
from .core import CapacityError, ExtractResult

MODE = "image_robust"
MIN_SIDE = 400
MAX_WORK_SIDE = 1800                  # extract() shrinks larger inputs to this long side
CELL = 4                              # canonical px per cell
CELLS = 4                             # cells per block side
BLOCK = CELL * CELLS                  # canonical px per block side

# invisible (tiled) layout
TILE_BLOCKS = 20
TILE = TILE_BLOCKS * BLOCK            # canonical tile side (px)
TILE_N = TILE_BLOCKS * TILE_BLOCKS
PILOT_EVERY = 8
TILE_FRACTION = 0.5                   # tile side = this * shorter picture side
TILE_STRENGTH = 2.0                   # grey levels at strength=1.0, before texture masking
TILE_INFORMED_ROUNDS = 4
COEF_RANGE = (0.5, 2.0)               # informed embedding may scale a block by this much
MASK_LOW, MASK_HIGH, MASK_REF, MASK_GAMMA = 0.4, 2.2, 4.0, 0.5
SYNC_MIN_Z = 5.0
TOP_PEAKS = 12                        # frame reads: strongest pilot offsets decoded per geometry
TOP_PEAK_MIN_Z = 3.0                  # weaker offsets than this are not decoded

# key only (payload b""): no frame; the whole tile is a keyed pattern and its peak decides
FALSE_POSITIVE = 3e-5                 # extract(): chance that a picture not marked with this key is verified
IDENTIFY_FALSE_POSITIVE = 1e-3        # identify(): chance that any of the keys is wrongly matched
NULL_LOCATION, NULL_SCALE = 5.22, 0.35  # Gumbel fit: best peak z of a whole read, 600 unmarked pictures

# channel code: convolutional K=7 (171, 133 octal), soft Viterbi; punctured only when needed
CONV_K = 7
CONV_POLYS = (0o171, 0o133)
PUNCTURE = {"1/2": (1, 1), "2/3": (1, 1, 1, 0), "3/4": (1, 1, 0, 1, 1, 0)}

# marker layout
CANON = 512
GRID = CANON // BLOCK                 # 32 x 32 blocks
MIN_REPEAT = 3
MARKER_FRACTION = 0.07
MARKER_REACH = 64                     # canonical px a marker tile may cover inward
# id -> (outward corner index, canonical position as fraction of the region)
MARKERS = {0: (0, (0, 0)), 1: (1, (1, 0)), 2: (2, (1, 1)), 3: (3, (0, 1)),
           4: (0, (0.5, 0)), 5: (1, (1, 0.5)), 6: (2, (0.5, 1)), 7: (3, (0, 0.5))}
MARKER_INFORMED_ROUNDS = 6
MARKER_STRENGTH = 0.4                 # default marker opacity (brightness only; 1 = solid black / white)
MARKER_ASPECTS = tuple(np.exp(np.linspace(np.log(0.3), np.log(3.4), 241)))  # picture aspects tried by the fit
MARKER_REFINE_STEPS = (8, 4, 2, 1)    # canonical px
MARKER_REFINE_MIN = 5.0               # data score (z) a rough geometry needs before it is refined
MAX_GAIN = 4.0
_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

# Work that does not depend on the key (filtering, finding the repeat period,
# outlines, straightening, folding at a key-independent geometry) is memoised for
# the length of one extract() or extract_many() call, so trying several keys on
# one picture repeats only the key-dependent part.
_SESSION: contextvars.ContextVar[dict | None] = contextvars.ContextVar("image_robust_session", default=None)


@contextlib.contextmanager
def _session():
    if _SESSION.get() is not None:
        yield
        return
    token = _SESSION.set({})
    try:
        yield
    finally:
        _SESSION.reset(token)


def _token(x):
    if isinstance(x, np.ndarray):  # small arrays (vectors, corners, homographies) by value, images by identity
        return (x.dtype.str, x.shape, x.tobytes()) if x.size <= 16 else ("id", id(x))
    if isinstance(x, (list, tuple)):
        return tuple(_token(v) for v in x)
    return x


def _shared(fn):
    """Memoise a key-independent helper within the current session (results must not be modified)."""
    @functools.wraps(fn)
    def wrapper(*args):
        store = _SESSION.get()
        if store is None:
            return fn(*args)
        key = (fn.__name__, *map(_token, args))
        hit = store.get(key)
        if hit is None:
            with store.setdefault(("lock", key), threading.Lock()):  # one thread computes, others wait
                hit = store.get(key)
                if hit is None:
                    hit = store[key] = (fn(*args), args)  # keeping the arguments alive keeps their ids unique
        return hit[0]
    return wrapper


def _shared_lru(limit_bytes):
    """Like _shared, but keeps only the most recently used results up to limit_bytes.
    For big arrays that are cheap enough to recompute (identically) when evicted."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args):
            store = _SESSION.get()
            if store is None:
                return fn(*args)
            cache, guard = store.setdefault(("lru", fn.__name__), (collections.OrderedDict(), threading.Lock()))
            key = tuple(map(_token, args))
            with guard:
                lock = store.setdefault(("lock", fn.__name__, key), threading.Lock())
            with lock:  # one thread computes, others wait
                with guard:
                    hit = cache.get(key)
                    if hit is not None:
                        cache.move_to_end(key)
                        return hit[0]
                value = fn(*args)
                with guard:
                    cache[key] = (value, args)
                    total = sum(v[0].nbytes for v in cache.values())
                    while total > limit_bytes and len(cache) > 1:
                        _, (old, _) = cache.popitem(last=False)
                        total -= old.nbytes
                return value
        return wrapper
    return decorate


_POOL: ThreadPoolExecutor | None = None
_IN_POOL = threading.local()


def _pmap(fn, items):
    """[fn(x) for x in items], spread over a thread pool (OpenCV and numpy FFTs release
    the GIL). Results keep their order. Tasks never use the pool themselves."""
    global _POOL
    items = list(items)
    if len(items) < 2 or getattr(_IN_POOL, "active", False):
        return [fn(x) for x in items]
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=os.cpu_count() or 1, thread_name_prefix="image_robust")

    def run(job):
        context, item = job
        _IN_POOL.active = True
        try:
            return context.run(fn, item)
        finally:
            _IN_POOL.active = False
    return list(_POOL.map(run, [(contextvars.copy_context(), x) for x in items]))


_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


# ---------------------------------------------------------------- public
def capacity(*, profile: str = "compact", markers: bool = False) -> int:
    """Largest payload in bytes (at code rate 3/4). Independent of image size.
    Payloads that fit at rate 1/2 are the robust ones: see robust_capacity()."""
    return _max_payload(profile, markers, "3/4")


def robust_capacity(*, profile: str = "compact", markers: bool = False) -> int:
    """Largest payload that still gets the full-strength rate-1/2 code."""
    return _max_payload(profile, markers, "1/2")


def embed(image: np.ndarray, payload: bytes, key: bytes | str, *, strength: float = 1.0, profile: str = "compact",
          markers: bool = False, marker_strength: float = MARKER_STRENGTH) -> np.ndarray:
    """payload b"" marks the picture with the key alone (no hidden bytes, invisible
    layout only): extract() then returns payload b"" when the key matches. It
    survives far weaker signals than a hidden ID (see the module docstring).
    strength: 1.0 is the validated default; lower is less visible but less robust.
    profile: "compact" (default, 4-byte tag, far more robust) or "standard"
    (AES-SIV, 16-byte tag; its longer frame fits only short payloads at a weaker code rate).
    markers: False = invisible tiled layout; True = visible corner markers, for
    copies that will be photographed at steep angles (see module docstring).
    marker_strength: how far the markers move the brightness toward black / white
    (0 < s <= 1; 1 = solid squares). Lower is less conspicuous but found less often."""
    image = _check(image)
    if not 0 < marker_strength <= 1:
        raise ValueError("marker_strength must be in (0, 1]")
    h, w = image.shape[:2]
    if min(h, w) < MIN_SIDE:
        raise ValueError(f"image_robust needs images at least {MIN_SIDE} x {MIN_SIDE} pixels")
    if not payload:
        if markers:
            raise ValueError("key-only marking (payload b\"\") needs markers=False")
        bits = _key_only_signs(key)
    else:
        limit = capacity(profile=profile, markers=markers)
        if len(payload) > limit:
            raise CapacityError(f"payload is {len(payload)} bytes; this layout holds at most {limit} with profile {profile!r}")
        frame_bits = np.unpackbits(np.frombuffer(pl.pack(payload, key, profile), dtype=np.uint8))
        bits = _conv_encode(frame_bits, _rate_for(frame_bits.size, _budget(markers)))
    delta, box = (_embed_markers if markers else _embed_tiled)(image, bits, key, strength)
    x0, y0, x1, y1 = box
    out = image.astype(np.float32)
    if out.ndim == 2:
        out[y0:y1, x0:x1] += delta
    else:
        out[y0:y1, x0:x1, :3] += delta[..., None]
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    if markers:
        _draw_markers(out, marker_strength)
    return out


def extract(image: np.ndarray, key: bytes | str, *, profile: str = "compact",
            false_positive: float = FALSE_POSITIVE) -> ExtractResult:
    """Read a hidden frame; failing that, look for key-only marking.

    false_positive: key-only only; the chance that a picture not marked with this
    key is verified. A frame is checked by its authentication tag instead."""
    _check_false_positive(false_positive)
    with _session():
        return _extract(_check(image), key, profile, false_positive)


def extract_many(image: np.ndarray, keys, *, profile: str = "compact", false_positive: float | None = None) -> dict:
    """extract() for several (label, key) pairs at once: work that does not depend on
    the key is shared, and the keys are tried in parallel threads. By default the
    key-only threshold rises with the number of keys, so the chance that any wrong
    key is matched stays at IDENTIFY_FALSE_POSITIVE."""
    image, keys = _check(image), list(keys)
    if false_positive is None:
        false_positive = min(FALSE_POSITIVE, IDENTIFY_FALSE_POSITIVE / max(1, len(keys)))
    _check_false_positive(false_positive)
    with _session():
        if len(keys) < 2:
            return {label: _extract(image, key, profile, false_positive) for label, key in keys}
        with ThreadPoolExecutor(max_workers=min(len(keys), os.cpu_count() or 1)) as pool:
            # each task runs in its own copy of the context, all pointing at the same session
            def one(key):
                _IN_POOL.active = True  # the keys already fill the cores: no nested pool work
                try:
                    return _extract(image, key, profile, false_positive)
                finally:
                    _IN_POOL.active = False
            futures = {label: pool.submit(contextvars.copy_context().run, one, key) for label, key in keys}
            return {label: future.result() for label, future in futures.items()}


@_shared
def _prepare(full):
    """Working copy (big photos shrunk: the pattern is coarse), its luminance, and any markers."""
    image = full
    scale = MAX_WORK_SIDE / max(full.shape[:2])
    if scale < 1:
        image = cv2.resize(full, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    luma = _luma(image)
    return image, luma, _detect(np.clip(luma, 0, 255).astype(np.uint8))


def _check_false_positive(p):
    if not 0 < p < 0.5:
        raise ValueError("false_positive must be between 0 and 0.5")


def key_only_threshold(false_positive: float = FALSE_POSITIVE) -> float:
    """Peak z a key-only read must reach so that an unmarked picture passes with
    probability false_positive (Gumbel tail of the best peak over a whole read)."""
    return NULL_LOCATION - NULL_SCALE * np.log(-np.log1p(-false_positive))


def _tile_readers(image, full, luma, tiles):
    return [_read_tiled(luma, tiles, "tiles"), _read_outlines(image, luma, tiles),
            _read_line_outlines(image, full, tiles), _read_sizes(luma, tiles, "tiles"),
            _read_outline_sizes(image, luma, tiles), _read_angles(luma, tiles)]


def _extract(full, key, profile, false_positive=FALSE_POSITIVE):
    image, luma, found = _prepare(full)
    attempts = []
    tiles = _TileReader(key, profile)
    readers = [_read_markers(luma, found, key, profile)] if len(found) >= 2 else []
    tiled = _tile_readers(image, full, luma, tiles)
    readers += tiled[:2] + [_read_marker_outlines(image, luma, key, profile)] + tiled[2:]
    if len(found) < 2:
        readers.append(_read_markers(luma, found, key, profile))
    for reader in readers:
        for name, result in reader:
            attempts.append(name)
            if result is not None:
                data, info, extra = result
                return ExtractResult(MODE, True, max(0.01, info["bit_agreement"]), data,
                                     {"geometry": name, "profile": profile, **info, **extra})
    # key only: the same geometry search with the whole keyed tile as the pattern
    keyed = _TileReader(key, profile, key_only_z=key_only_threshold(false_positive))
    for reader in _tile_readers(image, full, luma, keyed):
        for name, result in reader:
            if result is not None:
                data, info, extra = result
                return ExtractResult(MODE, True, info["bit_agreement"], data,
                                     {"geometry": f"key-only {name}", "profile": profile, **info, **extra})
    return ExtractResult.failed(MODE, "no authenticated frame and no key-only mark", geometry_tried=attempts,
                                profile=profile, key_only_z=round(keyed.best_z, 2),
                                key_only_z_min=round(keyed.key_only_z, 2))


# ---------------------------------------------------------------- shared helpers
def _keystream(key, purpose: str, nbytes: int) -> np.ndarray:
    stream = Cipher(algorithms.AES(pl.subkey(key, f"{MODE}/{purpose}")), modes.CTR(b"\0" * 16)).encryptor()
    return np.frombuffer(stream.update(b"\0" * nbytes), dtype=">u4").astype(np.uint64)


def _permutation(key, purpose: str, n: int, rows: int = 1) -> np.ndarray:
    rand = _keystream(key, purpose, 4 * n * rows).reshape(rows, n)
    return np.argsort((rand << np.uint64(32)) | np.arange(n, dtype=np.uint64), axis=1, kind="stable")


def _balanced_chips(key, purpose: str, count: int) -> np.ndarray:
    """(count, 4, 4) +/-1 patterns: eight +1 and eight -1 cells each."""
    n = CELLS * CELLS
    ranks = np.argsort(_permutation(key, purpose, n, count), axis=1)
    return np.where(ranks < n // 2, 1.0, -1.0).astype(np.float32).reshape(-1, CELLS, CELLS)


def _cell_corr(canvas: np.ndarray, side_blocks: int, chips: np.ndarray) -> np.ndarray:
    cells = canvas.reshape(side_blocks, CELLS, CELL, side_blocks, CELLS, CELL).mean(axis=(2, 5)).transpose(0, 2, 1, 3)
    return (cells.reshape(-1, CELLS, CELLS) * chips).sum(axis=(1, 2))


def _budget(markers: bool) -> int:
    """Coded bits a layout can carry (each spread over one or more blocks)."""
    return len(_free_blocks()) // MIN_REPEAT if markers else TILE_N - TILE_N // PILOT_EVERY


def _coded_length(nbits: int, rate: str) -> int:
    mother = 2 * (nbits + CONV_K - 1)
    keep = np.resize(np.array(PUNCTURE[rate], dtype=bool), mother)
    return int(keep.sum())


def _rate_for(nbits: int, budget: int) -> str | None:
    """Strongest code rate whose output fits the layout (None if nothing fits)."""
    return next((r for r in PUNCTURE if _coded_length(nbits, r) <= budget), None)


def _max_payload(profile: str, markers: bool, rate: str) -> int:
    budget, length = _budget(markers), 0
    while length < pl.MAX_PAYLOAD[profile] and _coded_length(8 * pl.frame_size(profile, length + 1), rate) <= budget:
        length += 1
    return length


def _conv_tables():
    """Viterbi tables: for each next state, its two predecessor states and their output signs."""
    states = 1 << (CONV_K - 1)
    pred = np.zeros((states, 2), dtype=np.int64)
    signs = np.zeros((states, 2, 2), dtype=np.float32)
    for ns in range(states):
        bit = ns >> (CONV_K - 2)
        for x in (0, 1):
            s = ((ns << 1) & (states - 1)) | x
            reg = (bit << (CONV_K - 1)) | s
            pred[ns, x] = s
            signs[ns, x] = [1.0 if bin(reg & g).count("1") & 1 else -1.0 for g in CONV_POLYS]
    return pred, signs


_PRED, _OUT_SIGNS = _conv_tables()


def _conv_encode(bits: np.ndarray, rate: str) -> np.ndarray:
    """Info bits (0/1) -> punctured coded bits as +/-1 floats (zero tail terminates the trellis)."""
    state, out = 0, []
    for b in list(bits.astype(int)) + [0] * (CONV_K - 1):
        reg = (b << (CONV_K - 1)) | state
        out += [bin(reg & g).count("1") & 1 for g in CONV_POLYS]
        state = reg >> 1
    out = np.array(out, dtype=np.float32) * 2 - 1
    return out[np.resize(np.array(PUNCTURE[rate], dtype=bool), out.size)]


def _conv_decode(soft: np.ndarray, nbits: int, rate: str):
    """Soft Viterbi (soft > 0 means 1). Returns (bits, agreement in [-1, 1])."""
    steps = nbits + CONV_K - 1
    keep = np.resize(np.array(PUNCTURE[rate], dtype=bool), 2 * steps)
    full = np.zeros(2 * steps, dtype=np.float32)
    full[keep] = soft
    full = full.reshape(steps, 2)
    states = _PRED.shape[0]
    tail = np.arange(states) >> (CONV_K - 2) == 1       # states entered by a 1 bit
    metric = np.full(states, -np.inf)
    metric[0] = 0.0
    choice = np.zeros((steps, states), dtype=np.int8)
    for t in range(steps):
        cand = metric[_PRED] + _OUT_SIGNS @ full[t]
        choice[t] = np.argmax(cand, axis=1)
        metric = cand[np.arange(states), choice[t]]
        if t >= nbits:
            metric[tail] = -np.inf
    state, bits = 0, np.zeros(steps, dtype=np.uint8)
    for t in range(steps - 1, -1, -1):
        bits[t] = state >> (CONV_K - 2)
        state = _PRED[state, choice[t, state]]
    bits = bits[:nbits]
    agreement = float(np.dot(soft, _conv_encode(bits, rate)) / max(np.abs(soft).sum(), 1e-9))
    return bits, agreement


def _decode(soft_for, budget: int, key, profile: str):
    """Try every payload length; soft_for(ncoded) gives summed evidence per coded bit.
    The authenticated frame decides which length (if any) is genuine (shortest first)."""
    jobs = []
    for length in range(1, pl.MAX_PAYLOAD[profile] + 1):
        nbits = 8 * pl.frame_size(profile, length)
        rate = _rate_for(nbits, budget)
        if rate is None:
            break
        jobs.append((length, nbits, rate, soft_for(_coded_length(nbits, rate))))
    if not jobs:
        return None
    for (length, nbits, rate, soft), bits in zip(jobs, _conv_decode_many([(s, n, r) for _, n, r, s in jobs])):
        data = pl.unpack(np.packbits(bits).tobytes(), key, profile)
        if data is not None and len(data) == length:
            agreement = float(np.dot(soft, _conv_encode(bits, rate)) / max(np.abs(soft).sum(), 1e-9))
            return data, {"code_rate": rate, "bit_agreement": round(agreement, 3)}
    return None


def _conv_decode_many(jobs):
    """Soft Viterbi for several (soft, nbits, rate) at once (one trellis pass for all)."""
    count = len(jobs)
    steps = np.array([n + CONV_K - 1 for _, n, _ in jobs])
    nbits = np.array([n for _, n, _ in jobs])
    longest = int(steps.max())
    full = np.zeros((count, longest, 2), dtype=np.float32)
    for i, (soft, n, rate) in enumerate(jobs):
        keep = np.resize(np.array(PUNCTURE[rate], dtype=bool), 2 * steps[i])
        row = np.zeros(2 * steps[i], dtype=np.float32)
        row[keep] = soft
        full[i, :steps[i]] = row.reshape(-1, 2)
    states = _PRED.shape[0]
    tail = np.arange(states) >> (CONV_K - 2) == 1       # states entered by a 1 bit
    metric = np.full((count, states), -np.inf)
    metric[:, 0] = 0.0
    choice = np.zeros((longest, count, states), dtype=np.int8)
    branch = np.einsum("spk,ctk->tpcs", _OUT_SIGNS, full)  # (t, predecessor, job, state) branch metrics
    first, second = _PRED[:, 0], _PRED[:, 1]
    ending = (np.arange(longest)[:, None] >= nbits[None, :])[:, :, None] & tail[None, None, :]
    for t in range(longest):
        c0 = metric[:, first] + branch[t, 0]
        c1 = metric[:, second] + branch[t, 1]
        pick = c1 > c0                                  # ties keep the first, like argmax
        choice[t] = pick
        metric = np.where(pick, c1, c0)
        metric[ending[t]] = -np.inf
    # trace back all jobs together; each starts in state 0 at its own last step
    state = np.zeros(count, dtype=np.int64)
    bits = np.zeros((count, longest), dtype=np.uint8)
    jobs_idx = np.arange(count)
    for t in range(longest - 1, -1, -1):
        bits[:, t] = state >> (CONV_K - 2)
        state = np.where(t < steps, _PRED[state, choice[t, jobs_idx, state]], 0)
    return [bits[i, :nbits[i]] for i in range(count)]


def _check(image) -> np.ndarray:
    image = np.asarray(image)
    if image.dtype != np.uint8:
        raise TypeError("image must be a uint8 array")
    if image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] not in (3, 4)):
        raise ValueError("image must be shaped (H, W), (H, W, 3) or (H, W, 4)")
    return image


def _luma(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.astype(np.float32)
    return image[..., :3].astype(np.float32) @ _LUMA


# ================================================================ invisible tiled layout
def _tile_side(h: int, w: int) -> int:
    return int(round(TILE_FRACTION * min(h, w)))


def _tile_chips(key) -> np.ndarray:
    return _balanced_chips(key, "tile/chips", TILE_N)


def _tile_layout(key) -> tuple[np.ndarray, np.ndarray]:
    order = _permutation(key, "tile/layout", TILE_N)[0]
    return order[: TILE_N // PILOT_EVERY], order[TILE_N // PILOT_EVERY:]


def _key_only_signs(key) -> np.ndarray:
    """+/-1 per data block for key-only marking (the pilots stay +1)."""
    n = TILE_N - TILE_N // PILOT_EVERY
    return np.where(_keystream(key, "tile/key-only", 4 * n) & np.uint64(1), 1.0, -1.0).astype(np.float32)


def _render_tile(cells: np.ndarray) -> np.ndarray:
    """(blocks, 4, 4) cell values -> smooth, seamlessly repeating TILE x TILE pattern."""
    flat = cells.reshape(TILE_BLOCKS, TILE_BLOCKS, CELLS, CELLS).repeat(CELL, 2).repeat(CELL, 3)
    flat = flat.transpose(0, 2, 1, 3).reshape(TILE, TILE).astype(np.float32)
    return cv2.GaussianBlur(np.tile(flat, (3, 3)), (0, 0), CELL / 2)[TILE:2 * TILE, TILE:2 * TILE]


def _texture_mask(luma: np.ndarray, cell_px: float) -> np.ndarray:
    """Per-pixel gain: high in busy texture; low in flat areas and in flat areas next to lone edges."""
    fine = luma - cv2.GaussianBlur(luma, (0, 0), 1.5)
    activity = np.sqrt(cv2.GaussianBlur(fine * fine, (0, 0), max(1.0, cell_px)))
    r = max(1, int(round(1.5 * cell_px)))
    activity = cv2.erode(activity, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    activity = cv2.GaussianBlur(activity, (0, 0), max(1.0, cell_px))
    mask = np.clip((activity / MASK_REF) ** MASK_GAMMA, MASK_LOW, MASK_HIGH)
    # a picture that is smooth everywhere still needs a usable signal
    return np.clip(mask * np.clip(1.0 / mask.mean(), 1.0, 4.0), MASK_LOW, MASK_HIGH)


def _embed_tiled(image, bits, key, strength):
    h, w = image.shape[:2]
    pilot, data = _tile_layout(key)
    signs = np.zeros(TILE_N, dtype=np.float32)
    signs[pilot] = 1
    signs[data] = bits[np.arange(data.size) % bits.size]
    chips = _tile_chips(key)
    side = _tile_side(h, w)
    luma = _luma(image)
    mask = strength * TILE_STRENGTH * _texture_mask(luma, side / (TILE_BLOCKS * CELLS))
    reps = (-(-h // side), -(-w // side))

    def render(coef):
        tile = _render_tile(chips * coef[:, None, None])
        tile = cv2.resize(np.tile(tile, (3, 3)), (3 * side, 3 * side), interpolation=cv2.INTER_LINEAR)
        return mask * np.tile(tile[side:2 * side, side:2 * side], reps)[:h, :w]

    coef = signs / (np.abs(_render_tile(chips * signs[:, None, None])).mean() + 1e-9)
    base = np.abs(coef).mean()
    # Informed embedding: even out blocks the picture cancels (or over-supports).
    for _ in range(TILE_INFORMED_ROUNDS):
        margin = signs * _tile_block_corr(np.clip(luma + render(coef), 0, 255), side, chips)
        target = float(np.median(margin))
        ratio = np.clip(target / np.maximum(margin, 0.05 * target), 0.67, 1.5)
        coef = signs * np.clip(np.abs(coef) * ratio, COEF_RANGE[0] * base, COEF_RANGE[1] * base)
    return render(coef), (0, 0, w, h)


def _tile_block_corr(luma, side, chips):
    """The decoder's per-block statistic for an unattacked picture (tiles start at the top-left)."""
    a, b = np.array([side, 0.0]), np.array([0.0, side])
    folded = _fold(_tile_residual(luma, a, b), _lattice_homography(a, b, np.zeros(2)))
    return _cell_corr(folded, TILE_BLOCKS, chips)


class _TileReader:
    """Key-dependent pieces the tiled decoder needs, computed once per extract()."""

    def __init__(self, key, profile, key_only_z=None):
        """key_only_z: None reads frames (the pilots are the sync pattern); a number
        looks for key-only marking instead: the whole keyed tile is the pattern, and a
        geometry whose peak reaches key_only_z is a match."""
        self.key, self.profile, self.key_only_z = key, profile, key_only_z
        self.best_z = -np.inf
        self.chips = _tile_chips(key)
        pilot, self.data = _tile_layout(key)
        signs = np.zeros(TILE_N, dtype=np.float32)
        signs[pilot] = 1
        if key_only_z is not None:
            signs[self.data] = _key_only_signs(key)
        self.pilot_spectrum = np.conj(np.fft.rfft2(_render_tile(self.chips * signs[:, None, None])))
        self.budget = _budget(False)
        self._last_read = None  # a refinement that finds nothing to fix returns the same geometry: read it once
        self._rotated = None

    def rotated_pilots(self):
        """Pilot spectra for the tile turned by 0, 90, 180, 270 degrees (cyclically).
        Correlating a fold with a turned pilot scores the same as turning the fold,
        so one transform of the fold covers all four turns."""
        if self._rotated is None:
            pilot = np.fft.irfft2(np.conj(self.pilot_spectrum), s=(TILE, TILE))
            turns = [pilot]
            for _ in range(3):
                turns.append(np.roll(turns[-1].T[:, ::-1], 1, axis=1))
            self._rotated = [np.conj(np.fft.rfft2(t)) for t in turns]
        return self._rotated

    def sync(self, residual, hom, weight=None, weighted=None):
        """(pilot z-score, homography including the pilot offset) or (-inf, None)."""
        return self.sync_spectrum(_spectrum_of(_fold(residual, hom, weight, weighted)), hom)

    def sync_spectrum(self, spectrum, hom):
        if spectrum is None:
            return -np.inf, None
        offset, z = _pilot_peak(spectrum, self.pilot_spectrum)
        shift = np.array([[1, 0, -offset[0]], [0, 1, -offset[1]], [0, 0, 1]])
        return z, shift @ hom

    def read(self, residual, hom, weight=None):
        """Decode with an aligned homography; returns (result or None, z)."""
        last = self._last_read
        if last is not None and last[1] is residual and last[3] is weight and np.array_equal(last[2], hom):
            return last[0]
        result = self._read(residual, hom, weight)
        self._last_read = (result, residual, np.array(hom, dtype=float), weight)
        return result

    def _read(self, residual, hom, weight):
        base = _fold(residual, hom, weight)
        spectrum = _spectrum_of(base)
        if spectrum is None:
            return None, -np.inf
        if self.key_only_z is not None:
            z, _ = self.sync_spectrum(spectrum, hom)
            self.best_z = max(self.best_z, z)
            if z < self.key_only_z:
                return None, z
            score = min(1.0, z / (2 * self.key_only_z))       # 0.5 at the threshold
            return (b"", {"key_only": True, "bit_agreement": round(score, 3), "z": round(float(z), 2),
                          "z_min": round(float(self.key_only_z), 2)}, {"layout": "tiles"}), z
        # the strongest pilot offset (sub-pixel), then the next strongest: at low signal
        # the right one is often not the very top; the frame's tag rejects wrong ones
        best = -np.inf
        for offset, z in [_pilot_peak(spectrum, self.pilot_spectrum)] + _other_peaks(spectrum, self.pilot_spectrum):
            best = max(best, z)
            if z < TOP_PEAK_MIN_Z:
                break
            shifted = np.array([[1, 0, -offset[0]], [0, 1, -offset[1]], [0, 0, 1]]) @ hom
            tile = _fold(residual, shifted, weight)
            if tile is None:  # the pilot offset pushed the geometry past what _fold accepts
                continue
            corr = _cell_corr(tile, TILE_BLOCKS, self.chips)
            data = self.data

            def soft(n, corr=corr):
                return np.bincount(np.arange(data.size) % n, weights=corr[data], minlength=n)

            result = _decode(soft, self.budget, self.key, self.profile)
            if result is not None:
                return (*result, {"layout": "tiles", "sync_z": round(float(z), 1)}), max(z, SYNC_MIN_Z)
        return None, best


def _other_peaks(spectrum, pilot_spectrum, count=TOP_PEAKS - 1, gap=6):
    """Further pilot offsets (integer, at least `gap` px from each other and from the
    top one), strongest first, with their z-scores."""
    xc = np.fft.irfft2(spectrum * pilot_spectrum, s=(TILE, TILE))
    z = ((xc - xc.mean()) / (xc.std() + 1e-9)).astype(np.float32)
    local = z == cv2.dilate(z, np.ones((2 * gap + 1, 2 * gap + 1), np.float32))
    ys, xs = np.nonzero(local)
    order = np.argsort(-z[ys, xs])[1:count + 1]          # [0] is the top peak, already tried
    return [(np.array([xs[i], ys[i]], dtype=float), float(z[ys[i], xs[i]])) for i in order]


def _read_tiled(luma, reader, label):
    """Yield (attempt name, decode result or None)."""
    h, w = luma.shape
    side = float(_tile_side(h, w))
    lattices = [(np.array([side, 0.0]), np.array([0.0, side]))]  # uncropped, unrotated
    for a, b in _find_lattices(luma):
        # tiles are square, so one well-found vector also gives the other (helps tight crops)
        for pair in ((a, b), (a, np.array([-a[1], a[0]])), (np.array([-b[1], b[0]]), b)):
            if not any(_same_lattice(*pair, c, d, 0.004) and _same_lattice(c, d, *pair, 0.004) for c, d in lattices):
                lattices.append(pair)
    for a, b in lattices:
        found = yield from _read_lattice(luma, reader, label, a, b, upright=False)
        if found:
            return


def _read_sizes(luma, reader, label):
    """Last resort for an upright copy cropped on its shorter side (down to about one
    tile): the tile is bigger than the guess. Sweep its size, read the best few.
    The filter barely depends on the exact size, so filter in coarse steps and
    score fine steps against each filtered copy."""
    sweep = [(reader.sync_spectrum(_spectrum_of(folded), hom)[0], a, b)
             for a, b, hom, folded in _size_folds(luma)]
    for _, a, b in sorted(sweep, key=lambda t: -t[0])[:4]:
        found = yield from _read_lattice(luma, reader, label, a, b, upright=True)
        if found:
            return


@_shared
def _size_folds(luma):
    """Key-independent part of _read_sizes: (a, b, homography, folded tile) per candidate size.
    Only the folded tiles are kept, not the filtered copies."""
    h, w = luma.shape
    side = float(_tile_side(h, w))
    top = min(h, w) / side

    def sizes_near(coarse):
        residual = _tile_residual.__wrapped__(luma, np.array([side * coarse, 0.0]), np.array([0.0, side * coarse]))
        out = []
        for f in np.arange(coarse - 0.05, min(coarse + 0.05, top + 0.005), 0.01):
            a, b = np.array([side * f, 0.0]), np.array([0.0, side * f])
            hom = _lattice_homography(a, b, np.zeros(2))
            out.append((a, b, hom, _fold(residual, hom)))
        return out
    return [t for group in _pmap(sizes_near, np.arange(1.06, top + 0.05, 0.1)) for t in group]


def _read_outline_sizes(image, luma, reader):
    """The size sweep for each outline's straightened picture (after every cheaper reader)."""
    for i, quad in enumerate(_outlines(image)):
        found = yield from _read_sizes_found(_straighten(luma, quad), reader, f"outline{i}")
        if found:
            return


def _read_angles(luma, reader, label="angles", keep=4):
    """Last resort for a tilted close-up showing under about two tiles each way (too few
    repeats for the lattice search): take the tilt from long straight edges (a screen or
    window border), sweep the tile size, and read the best few."""
    candidates = _angle_folds(luma)
    if not candidates:
        return
    pilots = reader.rotated_pilots()
    scored = []
    for i, (_, _, folded) in enumerate(candidates):
        spectrum = np.fft.rfft2(folded.astype(np.float32))
        scored.append((max(_pilot_peak(spectrum, p)[1] for p in pilots), i))
    scored.sort(reverse=True)
    for _, i in scored[:keep]:
        a, b, _ = candidates[i]
        found = yield from _read_lattice(luma, reader, label, a, b, upright=False)
        if found:
            return


ANGLE_SIZES = (0.3, 1.6, 0.03)          # tile side / shorter work side: from, to, log step
ANGLE_OFFSETS = (-0.6, 0.0, 0.6)        # degrees around each edge direction


@_shared
def _angle_folds(luma):
    """Key-independent part of _read_angles: (a, b, folded tile as float16) per tilt and size."""
    angles = _edge_angles(luma)
    if not angles:
        return []
    lo, hi, step = ANGLE_SIZES
    sizes = min(luma.shape) * lo * (1 + step) ** np.arange(int(np.log(hi / lo) / np.log(1 + step)) + 1)
    groups = {}
    for s in sizes:  # the filter barely depends on the exact size: one filtered copy per 10%
        groups.setdefault(int(np.log(s / sizes[0]) / np.log(1.1)), []).append(s)
    thetas = [np.radians(base + d) for base in angles for d in ANGLE_OFFSETS]

    def fold_group(group):
        mid = group[len(group) // 2]
        residual = _tile_residual.__wrapped__(luma, np.array([mid, 0.0]), np.array([0.0, mid]))
        out = []
        for s in group:
            for th in thetas:
                a = s * np.array([np.cos(th), np.sin(th)])
                b = s * np.array([-np.sin(th), np.cos(th)])
                folded = _fold(residual, _lattice_homography(a, b, np.zeros(2)))
                if folded is not None:
                    out.append((a, b, folded.astype(np.float16)))
        return out
    return [t for group in _pmap(fold_group, list(groups.values())) for t in group]


def _edge_angles(luma, keep=2):
    """Main directions (degrees, modulo 90) of long straight edges, strongest first."""
    f = min(1.0, 900 / max(luma.shape))
    small = cv2.resize(luma, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else luma
    segs = cv2.createLineSegmentDetector().detect(np.clip(small, 0, 255).astype(np.uint8))[0]
    if segs is None:
        return []
    segs = segs.reshape(-1, 4).astype(np.float64)
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    long = length > 0.05 * max(small.shape)
    if not long.any():
        return []
    angle = np.degrees(np.arctan2(segs[long, 3] - segs[long, 1], segs[long, 2] - segs[long, 0])) % 90
    bins = 360  # quarter degrees, wrapping at 90
    hist = np.bincount(np.round(angle * 4).astype(int) % bins, weights=length[long], minlength=bins)
    hist = np.convolve(np.r_[hist[-2:], hist, hist[:2]], np.ones(5), "valid")
    out = []
    for i in np.argsort(-hist):
        if len(out) == keep or hist[i] < 0.25 * hist.max():
            break
        if all(min(abs(i - j), bins - abs(i - j)) > 8 for j in out):
            out.append(int(i))
    return [i / 4 for i in out]


def _read_sizes_found(luma, reader, label):
    for name, result in _read_sizes(luma, reader, label):
        yield name, result
        if result is not None:
            return True
    return False


def _read_lattice(luma, reader, label, a, b, upright):
    """Yield attempts for one candidate lattice; returns True once decoded."""
    trials = [reader.sync_spectrum(spectrum, hom) for spectrum, hom in _orientations(_base_fold(luma, a, b), a, b, upright)]
    if not any(t[1] is not None for t in trials):
        return False
    residual = _tile_residual(luma, a, b)
    trials = sorted((t for t in trials if t[1] is not None), key=lambda t: -t[0])
    for _, hom in trials[:2]:
        for refined in (False, True):
            result, z = reader.read(residual, _refine(residual, hom, reader.pilot_spectrum) if refined else hom)
            if z < SYNC_MIN_Z:
                continue
            yield f"{label}({np.linalg.norm(a):.0f}px{', refined' if refined else ''})", result
            if result is not None:
                return True
    return False


@_shared
def _base_fold(luma, a, b):
    """The tile folded along lattice a, b (key-independent, so shared between keys)."""
    return _fold(_tile_residual(luma, a, b), _lattice_homography(a, b, np.zeros(2)))


def _orientations(folded, a, b, upright):
    """(folded spectrum, homography) for each way the tile's axes may lie along a, b.

    The lattice gives the axes only up to order and sign. Swapping them transposes
    the folded tile and flipping one mirrors it (exactly, cyclically), so one fold
    serves all eight."""
    if folded is None:
        return []
    out = []
    for swap in ((False,) if upright else (False, True)):
        u, v = (b, a) if swap else (a, b)
        base = folded.T if swap else folded
        for su, sv in (((1, 1),) if upright else ((1, 1), (1, -1), (-1, 1), (-1, -1))):
            tile = base
            if su < 0:
                tile = np.roll(tile[:, ::-1], 1, axis=1)
            if sv < 0:
                tile = np.roll(tile[::-1], 1, axis=0)
            out.append((np.fft.rfft2(tile), _lattice_homography(su * u, sv * v, np.zeros(2))))
    return out


def _read_outlines(image, luma, reader):
    """Photos of a print or screen: find the picture's outline, then align its corners precisely.

    A detected outline is rarely exact (rounded corners, things covering an edge),
    so the four corners are nudged to wherever the pilot pattern lines up best.
    """
    for i, quad in enumerate(_outlines(image)):
        found = False
        for name, result in _read_tiled(_straighten(luma, quad), reader, f"outline{i}"):
            yield name, result
            found = found or result is not None
        if found:
            return
        polished = _polish_outline(luma, quad, reader)
        if polished is not None:
            residual, hom, weight = polished
            for refined in (False, True):
                result, z = reader.read(residual, _refine(residual, hom, reader.pilot_spectrum, weight=weight)
                                        if refined else hom, weight)
                yield f"outline{i}(polished{', refined' if refined else ''})", result
                if result is not None:
                    return


def _read_line_outlines(image, full, reader, polish=3):
    """Last resort for a small picture among clutter (a preview window on a busy screen):
    outlines built from long straight edges, read from the photo's own pixels.

    Any three sides of a candidate must be clearly visible. Every candidate gets a
    quick pilot score; only the best few are polished and decoded.
    """
    def prescore(candidate):
        quad, folded, a, b = candidate
        z = max((reader.sync_spectrum(spectrum, hom)[0] for spectrum, hom in _orientations(folded, a, b, False)),
                default=-np.inf)
        return z, quad
    scored = _pmap(prescore, _line_candidates(image, full))
    scored.sort(key=lambda t: -t[0])
    for i, (_, quad) in enumerate(scored[:polish]):
        polished = _polish_outline(None, None, reader, setup=_line_polish_setup(full, quad))
        if polished is None:
            continue
        residual, hom, weight = polished
        for refined in (False, True):
            result, z = reader.read(residual, _refine(residual, hom, reader.pilot_spectrum, weight=weight)
                                    if refined else hom, weight)
            yield f"lines{i}(polished{', refined' if refined else ''})", result
            if result is not None:
                return


@_shared
def _line_candidates(image, full):
    """Key-independent part of _read_line_outlines: each straight-edge outline (in
    full-resolution coordinates) and its straightened picture folded into a tile.
    Only the folded tiles are kept, not the large intermediate images."""
    rgb = image[..., :3] if image.ndim == 3 else image
    f = min(1.0, 900 / max(image.shape[:2]))
    small = cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else rgb
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY) if small.ndim == 3 else small
    quads = _line_quads(gray)
    if not quads:
        return []
    full_luma = _full_luma(full)
    up = full.shape[0] / gray.shape[0]

    def prepare(quad):
        quad = (quad * up).astype(np.float32)
        view, local = _local_view(full_luma, quad)
        straight = _straighten.__wrapped__(view, local)
        side = float(_tile_side(*straight.shape))
        a, b = np.array([side, 0.0]), np.array([0.0, side])
        return quad, _fold(_tile_residual.__wrapped__(straight, a, b), _lattice_homography(a, b, np.zeros(2))), a, b
    return _pmap(prepare, quads)


_full_luma = _shared(_luma)


@_shared
def _line_polish_setup(full, quad):
    """_polish_setup for a straight-edge outline, from the photo's own pixels around it."""
    view, local = _local_view(_full_luma(full), quad)
    return _polish_setup.__wrapped__(view, local)


def _local_view(luma, quad, pad=0.05):
    """The outline's surroundings, shrunk only as far as MAX_WORK_SIDE needs, and the outline in them."""
    lo, hi = quad.min(0), quad.max(0)
    margin = pad * float((hi - lo).max())
    x0, y0 = np.maximum(np.floor(lo - margin), 0).astype(int)
    x1, y1 = np.minimum(np.ceil(hi + margin), [luma.shape[1], luma.shape[0]]).astype(int)
    view = luma[y0:y1, x0:x1]
    f = min(1.0, MAX_WORK_SIDE / max(view.shape))
    if f < 1:
        view = cv2.resize(view, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return view, ((quad - np.float32([x0, y0])) * f).astype(np.float32)


COMMON_ASPECTS = (1.0, 5 / 4, 4 / 3, 3 / 2, 16 / 10, 16 / 9, 2.0, 21 / 9)


def _polish_outline(luma, quad, reader, steps=(0.012, 0.006, 0.003), setup=None):
    """Fit the picture's true aspect ratio and corners by maximising the pilot score."""
    residual, corners, inside, width, height = _polish_setup(luma, quad) if setup is None else setup
    weight = inside.astype(np.float32)
    weighted = residual * weight  # every score below folds this same product

    def target_for(aspect):
        # the tile is TILE_FRACTION of the shorter side, so the picture spans 1/TILE_FRACTION tiles that way
        short = TILE / TILE_FRACTION
        w, h = (short * aspect, short) if aspect >= 1 else (short, short / aspect)
        return np.float32([[0, 0], [w, 0], [w, h], [0, h]])

    def score(c, target, shared=False):
        hom = cv2.getPerspectiveTransform(c, target)
        if shared:  # key-independent geometry: keyed on the long-lived mask, not this call's float copy
            return reader.sync_spectrum(_mask_fold_spectrum(residual, hom, inside), hom)[0]
        return reader.sync(residual, hom, weight, weighted=weighted)[0]

    # 1) the outline's measured aspect ratio is a few per cent off under perspective: sweep it
    measured = width / height
    aspects = [measured * (1 + 0.01 * k) for k in range(-8, 9)]
    for common in COMMON_ASPECTS:
        for a in (common, 1 / common):
            if abs(a / measured - 1) < 0.15:
                aspects.append(a)
    best, target = max(zip(_pmap(lambda a: score(corners, target_for(a), True), aspects), range(len(aspects))))
    target = target_for(aspects[target])
    if best < SYNC_MIN_Z - 1:
        return None  # nothing like a pilot nearby: not our picture
    # 2) nudge the corners (coordinate ascent, shrinking steps)
    diagonal = float(np.hypot(width, height))
    for step in steps:
        for _ in range(4):
            improved = False
            for k in range(4):
                for axis in range(2):
                    for sign in (1, -1):
                        trial = corners.copy()
                        trial[k, axis] += sign * step * diagonal
                        z = score(trial, target)
                        if z > best:
                            best, corners, improved = z, trial, True
                            break
            if not improved:
                break
    return residual, cv2.getPerspectiveTransform(corners, target), weight


@_shared
def _polish_setup(luma, quad):
    """The outline's bounding box: filtered residual, corners in it, and a mask of the inside."""
    width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    pad = 0.03 * max(width, height)
    x0, y0 = np.maximum(np.floor(quad.min(0) - pad), 0).astype(int)
    x1, y1 = np.minimum(np.ceil(quad.max(0) + pad), [luma.shape[1], luma.shape[0]]).astype(int)
    tile_px = TILE_FRACTION * min(width, height)
    residual = _tile_residual.__wrapped__(luma[y0:y1, x0:x1], np.array([tile_px, 0.0]), np.array([0.0, tile_px]))
    corners = quad - np.float32([x0, y0])
    inner = corners + 0.03 * (corners.mean(0) - corners)
    inside = np.zeros(residual.shape, dtype=np.uint8)  # kept small; 0/1 exactly as float32 later
    cv2.fillConvexPoly(inside, np.round(inner).astype(np.int32), 1)
    return residual, corners, inside, width, height


@_shared_lru(96 << 20)
def _tile_residual(luma, a, b):
    side = max(np.linalg.norm(a), np.linalg.norm(b))
    cell_px = side / (TILE_BLOCKS * CELLS)
    residual = _whitened(luma, 0.0, 1.5 * cell_px)
    scale = side / TILE
    return residual if scale <= 1.2 else cv2.GaussianBlur(residual, (0, 0), 0.5 * scale)


def _whitened(luma, inner, outer):
    low = cv2.GaussianBlur(luma, (0, 0), outer)
    band = (cv2.GaussianBlur(luma, (0, 0), inner) if inner > 0 else luma) - low
    return band / (_smooth(np.abs(band), 4 * outer) + 1.0)


def _smooth(image, sigma):
    """Wide Gaussian blur for slowly varying maps (local contrast): blur a k-times
    smaller copy and scale back up. Much faster than a 6-sigma-wide kernel."""
    k = int(sigma // 4)
    if k < 2:
        return cv2.GaussianBlur(image, (0, 0), sigma)
    h, w = image.shape
    hh, ww = -(-h // k) * k, -(-w // k) * k
    padded = cv2.copyMakeBorder(image, 0, hh - h, 0, ww - w, cv2.BORDER_REFLECT)
    small = cv2.resize(padded, (ww // k, hh // k), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), np.sqrt(sigma ** 2 - (k * k - 1) / 12) / k)
    return cv2.resize(small, (ww, hh), interpolation=cv2.INTER_LINEAR)[:h, :w]


def _lattice_homography(a, b, offset):
    """Image px -> tile-plane px, for tile axes a, b (image px) and pilot offset."""
    hom = np.eye(3)
    hom[:2, :2] = np.linalg.inv(np.array([[a[0], b[0]], [a[1], b[1]]]) / TILE)
    hom[:2, 2] = -offset
    return hom


def _fold(residual, hom, weight=None, weighted=None):
    """Average every tile copy (via homography image -> tile plane) onto one TILE x TILE tile.
    weighted: residual * weight, if the caller already has it (the polish reuses one pair)."""
    h, w = residual.shape
    corners = cv2.perspectiveTransform(np.float32([[[0, 0], [w, 0], [0, h], [w, h]]]), hom)[0]
    if not np.all(np.isfinite(corners)) or not np.all(np.isfinite(hom)):
        return None  # degenerate geometry (e.g. a wrong outline guess)
    u0, v0 = np.floor(corners.min(0) / TILE).astype(int)
    u1, v1 = np.ceil(corners.max(0) / TILE).astype(int)
    nu, nv = int(u1 - u0), int(v1 - v0)
    if nu < 1 or nv < 1 or nu * nv > 400:
        return None
    shift = np.array([[1, 0, -u0 * TILE], [0, 1, -v0 * TILE], [0, 0, 1]], dtype=float) @ hom
    if weight is None:  # same sums as weight = 1 everywhere, without making and multiplying by it
        weighted, weight = residual, _ones(residual.shape)
    else:
        weighted = residual * weight if weighted is None else weighted
    size = (nu * TILE, nv * TILE)
    total = cv2.warpPerspective(weighted, shift, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    count = cv2.warpPerspective(weight, shift, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    total = total.reshape(nv, TILE, nu, TILE).sum(axis=(0, 2))
    count = count.reshape(nv, TILE, nu, TILE).sum(axis=(0, 2))
    return total / np.maximum(count, 1e-3)


@functools.lru_cache(maxsize=8)
def _ones(shape):
    ones = np.ones(shape, dtype=np.float32)
    ones.flags.writeable = False
    return ones


def _spectrum_of(folded):
    return None if folded is None else np.fft.rfft2(folded)


@_shared
def _mask_fold_spectrum(residual, hom, inside):
    return _spectrum_of(_fold(residual, hom, inside.astype(np.float32)))


def _pilot_peak(spectrum, pilot_spectrum):
    """Cyclic offset of the pilot pattern in a folded tile (given as its rfft2; sub-pixel) and its z-score."""
    product = spectrum * pilot_spectrum
    xc = np.fft.irfft2(product, s=(TILE, TILE))
    iy, ix = np.unravel_index(np.argmax(xc), xc.shape)
    # mean and spread straight from the spectrum (Parseval), saving two passes over xc
    n = TILE * TILE
    power = np.abs(product) ** 2
    energy = (2 * power.sum() - power[:, 0].sum() - power[:, -1].sum()) / n / n
    mean = product[0, 0].real / n
    z = float((xc[iy, ix] - mean) / (np.sqrt(max(energy - mean * mean, 0.0)) + 1e-9))

    def vertex(m1, c0, p1):
        d = m1 - 2 * c0 + p1
        return 0.0 if d >= 0 else 0.5 * (m1 - p1) / d

    th, tw = xc.shape
    dy = vertex(xc[(iy - 1) % th, ix], xc[iy, ix], xc[(iy + 1) % th, ix])
    dx = vertex(xc[iy, (ix - 1) % tw], xc[iy, ix], xc[iy, (ix + 1) % tw])
    return np.array([ix + dx, iy + dy]), z


def _refine(residual, hom, pilot_spectrum, grid=(4, 3), rounds=2, weight=None):
    """Measure the pilot offset patch by patch and fit a homography (perspective, lattice error)."""
    h, w = residual.shape
    for _ in range(rounds):
        inv = np.linalg.inv(hom)
        side = np.sqrt(abs(np.linalg.det(inv[:2, :2]))) * TILE if abs(inv[2, :2]).max() < 1e-6 else 0.5 * min(h, w)

        def measure(centre, hom=hom, side=side):
            cx, cy = centre
            # fold only the patch (same sums as masking the whole residual, far less work)
            y0, y1 = int(max(0, cy - 0.8 * side)), int(min(h, cy + 0.8 * side))
            x0, x1 = int(max(0, cx - 0.8 * side)), int(min(w, cx + 0.8 * side))
            patch = None
            if weight is not None:
                patch = weight[y0:y1, x0:x1]
                if patch.sum() < 0.02 * h * w:
                    return None
            to_patch = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], dtype=float)
            folded = _fold(residual[y0:y1, x0:x1], hom @ to_patch, patch)
            if folded is None:
                return None
            offset, z = _pilot_peak(np.fft.rfft2(folded), pilot_spectrum)
            if z < SYNC_MIN_Z:
                return None
            drift = (offset + TILE / 2) % TILE - TILE / 2
            return [cx, cy], cv2.perspectiveTransform(np.float32([[[cx, cy]]]), hom)[0, 0] - drift

        centres = [(cx, cy) for cy in np.linspace(0, h, grid[1] + 2)[1:-1] for cx in np.linspace(0, w, grid[0] + 2)[1:-1]]
        hits = [m for m in _pmap(measure, centres) if m is not None]
        src, dst = [m[0] for m in hits], [m[1] for m in hits]
        if len(src) < 4:
            return hom
        fitted, _ = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 0.05 * TILE)
        if fitted is None:
            return hom
        hom = fitted
    return hom


@_shared
def _find_lattices(luma, work=1024, keep=3):
    """Candidate repeat lattices (two vectors, image px) from the autocorrelation, best first."""
    h, w = luma.shape
    f = min(1.0, work / max(h, w))
    small = cv2.resize(luma, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else luma
    band = np.clip(_whitened(small, 0.7, 3.0), -3, 3)
    hh, ww = band.shape
    window = np.outer(np.hanning(hh), np.hanning(ww)).astype(np.float32)
    shape = (2 * hh, 2 * ww)
    ac = np.fft.fftshift(np.fft.irfft2(np.abs(np.fft.rfft2(band * window, s=shape)) ** 2, s=shape))
    overlap = np.fft.fftshift(np.fft.irfft2(np.abs(np.fft.rfft2(window, s=shape)) ** 2, s=shape))
    ac = (ac / np.maximum(overlap, 0.05 * overlap.max())).astype(np.float32)
    # a lattice peak is sharp in every direction; ridges from straight edges are not
    ridge = ac - np.maximum(cv2.blur(ac, (25, 1)), cv2.blur(ac, (1, 25)))
    ac = ridge / (cv2.GaussianBlur(np.abs(ridge), (0, 0), 12) + 1e-9)
    cy, cx = hh, ww
    yy, xx = np.mgrid[0:2 * hh, 0:2 * ww]
    allowed = (np.abs(yy - cy) <= 0.75 * hh) & (np.abs(xx - cx) <= 0.75 * ww) & (np.hypot(yy - cy, xx - cx) >= 40 * f)
    peaks = _peaks(np.where(allowed, ac, -np.inf).astype(np.float32), 40)
    local = cv2.dilate(np.where(allowed, ac, 0).astype(np.float32), np.ones((3, 3), np.float32))

    def strength_at(v):
        x, y = int(round(cx + v[0])), int(round(cy + v[1]))
        return float(local[y, x]) if 0 <= y < 2 * hh and 0 <= x < 2 * ww and allowed[y, x] else None

    vectors = []
    for x, y, _ in peaks:
        v = np.array([x, y])
        if not any(np.linalg.norm(v + u) < 3 for u in vectors):
            vectors.append(v)
    scored = []
    for i, a in enumerate(vectors):
        for b in vectors[i + 1:]:
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if abs(a[0] * b[1] - a[1] * b[0]) / (na * nb) < 0.5 or max(na, nb) > 2.5 * min(na, nb):
                continue
            values = [strength_at(n * a + m * b) for n in range(-2, 3) for m in range(-2, 3) if (n, m) != (0, 0)]
            values = [v for v in values if v is not None]
            if len(values) >= 4:
                a2, b2 = _reduce(a, b)
                scored.append((float(np.mean(values)) * np.sqrt(len(values)), a2 / f, b2 / f))
    scored.sort(key=lambda t: -t[0])
    out = []
    for _, a, b in scored:
        if not any(_same_lattice(a, b, c, d) for c, d in out):
            out.append((a, b))
        if len(out) == keep:
            break
    return out


def _peaks(ac, n):
    """Strongest local maxima (x, y offsets from the centre, value), sub-pixel refined."""
    h, w = ac.shape
    cy, cx = h // 2, w // 2
    ys, xs = np.nonzero((ac == cv2.dilate(ac, np.ones((9, 9), np.float32))) & np.isfinite(ac))
    values = ac[ys, xs]
    out = []
    for i in np.argsort(values)[::-1][:n]:
        y, x = ys[i], xs[i]
        dx = dy = 0.0
        if 1 <= y < h - 1 and 1 <= x < w - 1:
            if np.isfinite(ac[y - 1, x] + ac[y + 1, x]):
                dy = 0.5 * (ac[y - 1, x] - ac[y + 1, x]) / (ac[y - 1, x] - 2 * ac[y, x] + ac[y + 1, x] - 1e-12)
            if np.isfinite(ac[y, x - 1] + ac[y, x + 1]):
                dx = 0.5 * (ac[y, x - 1] - ac[y, x + 1]) / (ac[y, x - 1] - 2 * ac[y, x] + ac[y, x + 1] - 1e-12)
        out.append((x - cx + float(np.clip(dx, -1, 1)), y - cy + float(np.clip(dy, -1, 1)), float(values[i])))
    return out


def _reduce(a, b):
    """Lagrange-Gauss reduction: the shortest basis of the same lattice."""
    a, b = a.astype(float), b.astype(float)
    if a @ a > b @ b:
        a, b = b, a
    while True:
        b = b - round((a @ b) / (a @ a)) * a
        if b @ b >= a @ a:
            return a, b
        a, b = b, a


def _same_lattice(a, b, c, d, tol=0.05):
    basis = np.array([a, b]).T
    if abs(np.linalg.det(basis)) < 1e-9:
        return False
    coords = np.linalg.solve(basis, np.array([c, d]).T)
    return bool(np.all(np.abs(coords - np.round(coords)) < tol))


@_shared
def _outlines(image, max_quads=4):
    """Candidate picture outlines (TL, TR, BR, BL) in photos of a print or a screen."""
    rgb = image[..., :3] if image.ndim == 3 else np.dstack([image] * 3)
    h, w = rgb.shape[:2]
    f = min(1.0, 600 / max(h, w))
    small = cv2.GaussianBlur(cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA).astype(np.float32), (0, 0), 1.5)
    sh, sw = small.shape[:2]
    min_area = 0.15 * sh * sw
    b = max(2, int(0.03 * min(sh, sw)))
    frame = np.concatenate([small[:b].reshape(-1, 3), small[-b:].reshape(-1, 3),
                            small[:, :b].reshape(-1, 3), small[:, -b:].reshape(-1, 3)])
    found = []
    # 1) differs from the background colour seen along the photo's edge
    background = np.median(frame, axis=0)
    spread = np.median(np.abs(frame - background).sum(1)) + 1
    distance = np.abs(small - background).sum(2)
    for t in (3, 6, 12):
        mask = cv2.morphologyEx((distance > t * spread + 12).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        found.append(_quad_from_mask(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)), min_area))
    gray = cv2.cvtColor(np.clip(small, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    # 2) the outermost closed edge
    for lo, hi in ((20, 60), (40, 120)):
        edges = cv2.morphologyEx(cv2.Canny(gray, lo, hi), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            fill = np.zeros_like(gray)
            cv2.drawContours(fill, [max(contours, key=cv2.contourArea)], -1, 1, -1)
            found.append(_quad_from_mask(fill, min_area))
    # 3) large edge contours whose hull is a quadrilateral
    for lo, hi in ((30, 90), (15, 45)):
        contours, _ = cv2.findContours(cv2.dilate(cv2.Canny(gray, lo, hi), np.ones((3, 3), np.uint8)),
                                       cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:4]:
            if cv2.contourArea(contour) < min_area:
                break
            found.append(_quad_from_hull(cv2.convexHull(contour)))
    whole = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    gray_full = _luma(image)
    candidates = []
    for quad in found:
        if quad is not None:
            fitted = _fit_edges(gray_full, quad / f)
            candidates += [quad / f] + ([fitted] if fitted is not None else [])
    out = []
    for quad in candidates:
        if not _plausible_quad(quad, h, w):
            continue
        if np.abs(quad - whole).max() < 0.03 * max(h, w):
            continue  # the outline is the frame itself: nothing to straighten
        if not any(np.abs(quad - o).max() < 0.01 * max(h, w) for o in out):
            out.append(quad)
    return out[:max_quads]


def _line_quads(gray, keep=80, max_lines=40):
    """Quadrilaterals (TL, TR, BR, BL) whose sides lie on long straight edges.

    Collinear segments are merged into lines; two roughly parallel pairs of lines
    meeting at a wide angle make a quad, kept if at least three of its sides are
    mostly covered by detected segments. Best supported first.
    """
    h, w = gray.shape
    size = max(h, w)
    segs = cv2.createLineSegmentDetector().detect(gray)[0]
    if segs is None:
        return []
    segs = segs.reshape(-1, 4).astype(np.float64)
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    long = length > 0.03 * size
    order = np.argsort(-length[long])
    lines = []  # [point, direction, normal, offset, intervals along direction]
    for (x0, y0, x1, y1), ln in zip(segs[long][order], length[long][order]):
        p0, p1 = np.array([x0, y0]), np.array([x1, y1])
        d = (p1 - p0) / ln
        if d[0] < 0 or (d[0] == 0 and d[1] < 0):
            d = -d
        normal = np.array([-d[1], d[0]])
        mid = (p0 + p1) / 2
        for line in lines:
            if abs(line[1] @ d) > np.cos(np.radians(2.5)) and abs(line[2] @ mid - line[3]) < 0.006 * size:
                line[4].append(sorted((line[1] @ (p0 - line[0]), line[1] @ (p1 - line[0]))))
                break
        else:
            lines.append([mid, d, normal, normal @ mid, [[-ln / 2, ln / 2]]])

    def covered(intervals, lo=-np.inf, hi=np.inf):
        total, end = 0.0, -np.inf
        for a, b in sorted(intervals):
            a, b = max(a, end, lo), min(b, hi)
            if b > a:
                total, end = total + b - a, b
        return total

    lines = sorted(lines, key=lambda line: -covered(line[4]))[:max_lines]
    angle = np.array([np.arctan2(line[1][1], line[1][0]) for line in lines])

    def apart(i, j):
        d = abs(angle[i] - angle[j]) % np.pi
        return min(d, np.pi - d)

    @functools.lru_cache(maxsize=None)  # the same pairs of lines meet in many candidate quads
    def meet(i, j):
        a, b = lines[i], lines[j]
        m = np.array([a[1], -b[1]]).T
        if abs(np.linalg.det(m)) < 1e-6:
            return None
        t = np.linalg.solve(m, b[0] - a[0])
        return a[0] + t[0] * a[1], t[0], t[1]

    n = len(lines)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)
             if apart(i, j) < np.radians(25) and abs(lines[i][2] @ lines[j][0] - lines[i][3]) > 0.08 * min(h, w)]
    found = []
    for p, (i, j) in enumerate(pairs):
        for k, l in pairs[p + 1:]:
            if len({i, j, k, l}) < 4 or apart(i, k) < np.radians(50):
                continue
            order = (i, k, j, l)  # sides in order around the quad
            sides = [lines[x] for x in order]
            corners, spans = [], [[0.0, 0.0] for _ in range(4)]
            for s in range(4):
                hit = meet(order[s], order[(s + 1) % 4])
                if hit is None:
                    break
                corners.append(hit[0])
                spans[s][1], spans[(s + 1) % 4][0] = hit[1], hit[2]
            if len(corners) < 4:
                continue
            quad = np.array(corners)
            if (quad < -0.02 * size).any() or (quad[:, 0] > 1.02 * w).any() or (quad[:, 1] > 1.02 * h).any():
                continue
            if not 0.02 * h * w < cv2.contourArea(quad.astype(np.float32)) < 0.9 * h * w:
                continue
            edges = np.roll(quad, -1, axis=0) - quad
            turns = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
            if not ((turns > 0).all() or (turns < 0).all()):
                continue
            support = sorted(covered(sides[s][4], min(spans[s]), max(spans[s])) / max(abs(spans[s][1] - spans[s][0]), 1e-9)
                             for s in range(4))
            if support[1] < 0.5:  # three sides must be mostly visible
                continue
            found.append((sum(support[1:]) + 0.5 * support[0], quad))
    out = []
    for _, quad in sorted(found, key=lambda t: -t[0]):
        s, d = quad.sum(1), np.diff(quad, axis=1).ravel()
        quad = np.float32([quad[np.argmin(s)], quad[np.argmin(d)], quad[np.argmax(s)], quad[np.argmax(d)]])
        if not any(np.abs(quad - o).max() < 0.02 * size for o in out):
            out.append(quad)
        if len(out) == keep:
            break
    return out


def _plausible_quad(quad, h, w):
    """A usable outline: finite, convex, not tiny, and not far outside the photo."""
    quad = np.asarray(quad, dtype=np.float64)
    if quad.shape != (4, 2) or not np.all(np.isfinite(quad)):
        return False
    margin = 0.25 * max(h, w)
    if (quad < -margin).any() or (quad[:, 0] > w + margin).any() or (quad[:, 1] > h + margin).any():
        return False
    sides = np.linalg.norm(quad - np.roll(quad, -1, axis=0), axis=1)
    if sides.min() < 64:
        return False
    edges = np.roll(quad, -1, axis=0) - quad
    turns = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
    return bool((turns > 0).all() or (turns < 0).all())


def _fit_edges(gray, quad, band=0.06, samples=80, seed=0):
    """Re-fit each side of an outline as a straight line through the strongest edge
    near it, then intersect neighbouring sides. Recovers corners that are hidden or
    rounded, as long as most of each side is visible."""
    gy, gx = np.gradient(cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 2.0))
    h, w = gray.shape
    rng = np.random.default_rng(seed)
    lines = []
    for k in range(4):
        p0, p1 = quad[k].astype(np.float64), quad[(k + 1) % 4].astype(np.float64)
        length = np.linalg.norm(p1 - p0)
        if length < 20:
            return None
        along = (p1 - p0) / length
        normal = np.array([-along[1], along[0]])
        reach = band * length
        offsets = np.arange(-reach, reach + 1)
        points = []
        for t in np.linspace(0.05, 0.95, samples):
            line = p0 + t * (p1 - p0) + offsets[:, None] * normal
            xi, yi = np.round(line[:, 0]).astype(int), np.round(line[:, 1]).astype(int)
            ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
            if ok.sum() < 3:
                continue
            strength = np.abs(gx[yi[ok], xi[ok]] * normal[0] + gy[yi[ok], xi[ok]] * normal[1])
            points.append(line[ok][int(np.argmax(strength))])
        if len(points) < 10:
            return None
        points = np.array(points)
        tol = max(1.5, 0.004 * length)
        best = None
        for _ in range(200):  # RANSAC: the true edge is the line most points agree on
            i, j = rng.choice(len(points), 2, replace=False)
            d = points[j] - points[i]
            if np.linalg.norm(d) < 1e-6:
                continue
            n = np.array([-d[1], d[0]]) / np.linalg.norm(d)
            inliers = np.abs((points - points[i]) @ n) < tol
            if best is None or inliers.sum() > best.sum():
                best = inliers
        if best is None or best.sum() < 0.3 * len(points):
            return None
        vx, vy, x0, y0 = cv2.fitLine(points[best].astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    corners = []
    for k in range(4):  # corner k joins side k-1 and side k
        (pa, da), (pb, db) = lines[k - 1], lines[k]
        m = np.array([da, -db]).T
        if abs(np.linalg.det(m)) < 1e-6:
            return None
        ta = np.linalg.solve(m, pb - pa)[0]
        corners.append(pa + ta * da)
    corners = np.float32(corners)
    if np.abs(corners - quad).max() > 3 * band * np.linalg.norm(quad[2] - quad[0]):
        return None
    return corners


def _quad_from_mask(mask, min_area):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[k, cv2.CC_STAT_AREA] < min_area:
        return None
    contours, _ = cv2.findContours((labels == k).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return _quad_from_hull(cv2.convexHull(max(contours, key=cv2.contourArea)))


def _quad_from_hull(hull):
    for eps in (0.01, 0.02, 0.04, 0.06):
        approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
            s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
            return np.float32([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]])
    return None


@_shared
def _straighten(luma, quad):
    width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    target = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    return cv2.warpPerspective(luma, cv2.getPerspectiveTransform(quad, target), (int(round(width)), int(round(height))),
                               flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


# ================================================================ visible marker layout
def _embed_markers(image, bits, key, strength):
    h, w = image.shape[:2]
    blocks = _block_order(key)
    repeat = len(blocks) // bits.size
    signs = np.zeros(GRID * GRID, dtype=np.float32)
    used = blocks[: repeat * bits.size]
    signs[used] = bits[np.arange(used.size) % bits.size]
    chips = _marker_chips(key) * signs[:, None, None]

    x0, y0, x1, y1 = _region(h, w)
    region = _luma(image)[y0:y1, x0:x1]
    sigma = max(1.0, 2.0 * (x1 - x0) / GRID / CELLS)
    mean = cv2.GaussianBlur(region, (0, 0), sigma)
    local_std = np.sqrt(np.maximum(cv2.GaussianBlur(region * region, (0, 0), sigma) - mean * mean, 0))
    mask = strength * np.clip(local_std / 8.0, 0.5, 2.0)  # texture masking: stronger where busy
    interp = cv2.INTER_LINEAR if (x1 - x0) >= CANON else cv2.INTER_AREA

    # Informed embedding: read our own result and boost only the blocks the host image cancels.
    gain = np.ones(GRID * GRID, dtype=np.float32)
    norm = threshold = bit_threshold = None
    delta = None
    for _ in range(MARKER_INFORMED_ROUNDS):
        canon = _render_canon(chips * gain[:, None, None])
        norm = norm or float(np.abs(canon).mean()) + 1e-9
        delta = mask * cv2.resize(canon / norm, (x1 - x0, y1 - y0), interpolation=interp)
        trial = np.clip(region + delta, 0, 255)
        margin = signs[used] * _canon_corr(cv2.resize(trial, (CANON, CANON), interpolation=cv2.INTER_AREA), key)[used]
        bit_of = np.arange(used.size) % bits.size
        bit_margin = np.bincount(bit_of, weights=margin, minlength=bits.size)
        threshold = threshold or 0.75 * float(np.median(margin))
        bit_threshold = bit_threshold or 0.75 * float(np.median(bit_margin))
        # boost weak blocks, and every block of a bit whose summed evidence is weak
        weak = used[(margin < threshold) | (bit_margin[bit_of] < bit_threshold)]
        if weak.size == 0:
            break
        gain[weak] = np.minimum(gain[weak] * 1.5, MAX_GAIN)
    return delta, (x0, y0, x1, y1)


def _read_markers(luma, found, key, profile):
    """Geometry from the markers found (all four corners of each; two markers are enough),
    corrected from the data blocks themselves when the first read fails."""
    if len(found) >= 2:
        matrix = _marker_homography(found)
        if matrix is not None:
            done = yield from _read_marker_geometry(luma, matrix, key, profile, f"markers({len(found)})")
            if done:
                return
    # an unattacked picture whose markers were not found: the region is where it always is
    h, w = luma.shape
    x0, y0, x1, y1 = _region(h, w)
    canon = cv2.resize(luma[y0:y1, x0:x1], (CANON, CANON), interpolation=cv2.INTER_AREA)
    yield "markers-inset", _marker_decode(_canon_corr(canon, key), key, profile)


def _read_marker_outlines(image, luma, key, profile):
    """Marker layout without usable markers (too faint, blurred or cut off): take the
    picture's outline as rough geometry and let the data blocks pull it into place."""
    for i, quad in enumerate(_outlines(image)[:2]):
        done = yield from _read_marker_geometry(luma, _outline_to_canon(quad), key, profile, f"marker-outline{i}")
        if done:
            return


def _read_marker_geometry(luma, matrix, key, profile, label):
    """Read one rough photo -> canonical geometry; if that fails, refine it and read again."""
    canon, valid = _warp(luma, matrix)
    result = _marker_decode(_canon_corr(canon, key) * valid, key, profile)
    yield label, result
    if result is not None:
        return True
    refined = _refine_markers(luma, matrix, key, profile)
    if refined is None:
        return False
    canon, valid = _warp(luma, refined)
    result = _marker_decode(_canon_corr(canon, key) * valid, key, profile)
    yield f"{label}(refined)", result
    return result is not None


def _marker_decode(corr, key, profile):
    blocks = _block_order(key)

    def soft(n):
        used = blocks[: (blocks.size // n) * n]
        return np.bincount(np.arange(used.size) % n, weights=corr[used], minlength=n)
    result = _decode(soft, _budget(True), key, profile)
    return None if result is None else (*result, {"layout": "markers"})


def _marker_homography(found):
    """Photo -> canonical homography from every corner of every marker found.

    A marker is square in the picture, so its shape on the canonical grid depends on
    the picture's aspect ratio, which the photo alone does not tell: try a range and
    keep the one whose corners fit best."""
    ids = sorted(found)
    src = np.concatenate([found[i] for i in ids]).astype(np.float32)
    best = None
    for aspect in MARKER_ASPECTS:
        dst = np.concatenate([_marker_canon_corners(i, aspect) for i in ids])
        matrix = cv2.findHomography(src, dst, 0)[0]
        if matrix is None:
            continue
        err = float(((cv2.perspectiveTransform(src[None], matrix)[0] - dst) ** 2).sum(1).mean())
        if best is None or err < best[0]:
            best = (err, matrix)
    return None if best is None else best[1]


def _marker_canon_corners(marker_id, aspect):
    """Canonical positions of a marker's corners (its own TL, TR, BR, BL) for a picture of this aspect."""
    f = MARKER_FRACTION  # marker side = f * shorter side; region = picture less a quiet margin of side / 4
    if aspect >= 1:
        mw, mh = f * CANON / (aspect - f / 2), f * CANON / (1 - f / 2)
    else:
        mw, mh = f * CANON / (1 - f / 2), f * aspect * CANON / (1 - f * aspect / 2)
    corner, (fx, fy) = MARKERS[marker_id]
    tl = np.array([fx * CANON - (mw if corner in (1, 2) else 0), fy * CANON - (mh if corner in (2, 3) else 0)])
    return np.float32([tl, tl + [mw, 0], tl + [mw, mh], tl + [0, mh]])


def _outline_to_canon(quad):
    """Photo -> canonical homography if `quad` is the picture's outline (the region sits a quiet margin inside)."""
    width = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    height = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    quiet = MARKER_FRACTION / 4 * min(width, height)
    sx, sy = CANON / (width - 2 * quiet), CANON / (height - 2 * quiet)
    lo, hix, hiy = -quiet, CANON + quiet * sx, CANON + quiet * sy
    dst = np.float32([[lo * sx, lo * sy], [hix, lo * sy], [hix, hiy], [lo * sx, hiy]])
    return cv2.getPerspectiveTransform(quad.astype(np.float32), dst)


def _refine_markers(luma, matrix, key, profile, steps=MARKER_REFINE_STEPS):
    """Nudge where the canonical corners fall in the photo to maximise the data score.

    The score adds each coded bit's blocks (scattered over the whole picture) before
    taking the magnitude, so it is strong only when the whole grid lines up; it needs
    a start within a few canonical px, which the markers or the outline give.
    None if the start shows no data at all (wrong key, or nothing to align)."""
    chips = _marker_chips(key)
    blocks = _block_order(key)
    lengths = _marker_lengths(profile)
    square = np.float32([[0, 0], [CANON, 0], [CANON, CANON], [0, CANON]])
    photo = cv2.perspectiveTransform(square[None], np.linalg.inv(matrix))[0]

    def score(delta):
        trial = cv2.getPerspectiveTransform(photo, square + delta)
        canon, valid = _warp(luma, trial)
        return _marker_score(_canon_corr(canon, key, chips) * valid, blocks, lengths)
    delta = np.zeros((4, 2), np.float32)
    best = score(delta)
    if best < MARKER_REFINE_MIN:
        return None
    for step in steps:
        for _ in range(6):
            improved = False
            for k in range(4):
                for axis in range(2):
                    for sign in (1, -1):
                        trial = delta.copy()
                        trial[k, axis] += sign * step
                        z = score(trial)
                        if z > best:
                            best, delta, improved = z, trial, True
                            break
            if not improved:
                break
    return cv2.getPerspectiveTransform(photo, square + delta)


@functools.lru_cache(maxsize=4)
def _marker_lengths(profile):
    """Coded lengths a marker-layout frame can have (one per payload length that fits)."""
    budget, out = _budget(True), set()
    for length in range(1, pl.MAX_PAYLOAD[profile] + 1):
        nbits = 8 * pl.frame_size(profile, length)
        rate = _rate_for(nbits, budget)
        if rate is None:
            break
        out.add(_coded_length(nbits, rate))
    return tuple(sorted(out))


def _marker_score(corr, blocks, lengths):
    """z-score of the summed per-bit evidence magnitude against what blocks with random
    signs would give: for a bit whose blocks hold c_i, |sum c_i| has mean sqrt(2/pi) * r
    and variance (1 - 2/pi) * r^2, with r^2 = sum c_i^2 (best over the possible lengths)."""
    best = -np.inf
    for n in lengths:
        m = (blocks.size // n) * n
        index = np.arange(m) % n
        c = corr[blocks[:m]]
        total = np.abs(np.bincount(index, weights=c, minlength=n))
        power = np.bincount(index, weights=c * c, minlength=n)
        mean = np.sqrt(2 / np.pi) * np.sqrt(power).sum()
        spread = np.sqrt((1 - 2 / np.pi) * power.sum()) + 1e-9
        best = max(best, (total.sum() - mean) / spread)
    return best


def _free_blocks() -> np.ndarray:
    """Blocks not covered by any marker tile (identical for every image size)."""
    covered = np.zeros((GRID, GRID), dtype=bool)
    for corner, (fx, fy) in MARKERS.values():
        px, py = fx * CANON, fy * CANON
        right, down = corner in (0, 3), corner in (0, 1)
        xa, xb = (px - BLOCK, px + MARKER_REACH) if right else (px - MARKER_REACH, px + BLOCK)
        ya, yb = (py - BLOCK, py + MARKER_REACH) if down else (py - MARKER_REACH, py + BLOCK)
        c0, c1 = max(0, int(xa // BLOCK)), min(GRID, int(-(-xb // BLOCK)))
        r0, r1 = max(0, int(ya // BLOCK)), min(GRID, int(-(-yb // BLOCK)))
        covered[r0:r1, c0:c1] = True
    return np.flatnonzero(~covered.ravel())


def _block_order(key) -> np.ndarray:
    free = _free_blocks()
    return free[_permutation(key, "blocks", free.size)[0]]


def _marker_chips(key) -> np.ndarray:
    return _balanced_chips(key, "chips", GRID * GRID)


def _render_canon(chips: np.ndarray) -> np.ndarray:
    """(blocks, 4, 4) cell values -> smooth 512 x 512 canonical pattern."""
    canon = chips.reshape(GRID, GRID, CELLS, CELLS).repeat(CELL, axis=2).repeat(CELL, axis=3)
    canon = canon.transpose(0, 2, 1, 3).reshape(CANON, CANON).astype(np.float32)
    return cv2.GaussianBlur(canon, (0, 0), CELL / 2)  # soft cell edges are far less visible


def _canon_corr(canon: np.ndarray, key, chips=None) -> np.ndarray:
    canon = canon.astype(np.float32)
    residual = canon - cv2.GaussianBlur(canon, (0, 0), 1.5 * CELL)
    residual /= cv2.GaussianBlur(np.abs(residual), (0, 0), 2.0 * BLOCK) + 1.0  # whiten host texture
    return _cell_corr(residual, GRID, _marker_chips(key) if chips is None else chips)


def _marker_geometry(h: int, w: int) -> tuple[int, int]:
    size = max(32, round(MARKER_FRACTION * min(h, w)))
    return size, max(4, size // 4)


def _region(h: int, w: int) -> tuple[int, int, int, int]:
    """Watermark region = rectangle spanned by the markers' outer corners."""
    _, quiet = _marker_geometry(h, w)
    return quiet, quiet, w - quiet, h - quiet


def _draw_markers(out: np.ndarray, strength: float) -> None:
    """Draw the markers by moving only the brightness toward black / white by `strength`
    (1 = solid), keeping the picture's colours: far less conspicuous than pasted squares."""
    h, w = out.shape[:2]
    size, quiet = _marker_geometry(h, w)
    x0, y0, x1, y1 = _region(h, w)
    pad = size + 2 * quiet
    for marker_id, (corner, (fx, fy)) in MARKERS.items():
        px, py = round(x0 + fx * (x1 - x0)), round(y0 + fy * (y1 - y0))
        mx = px if corner in (0, 3) else px - size      # marker's own top-left pixel
        my = py if corner in (0, 1) else py - size
        tile = np.full((pad, pad), 255, dtype=np.float32)
        tile[quiet:quiet + size, quiet:quiet + size] = cv2.aruco.generateImageMarker(_DICT, marker_id, size)
        x, y = mx - quiet, my - quiet
        patch = out[y:y + pad, x:x + pad].astype(np.float32)
        if patch.ndim == 2:
            patch += strength * (tile - patch)
        else:
            patch[..., :3] += (strength * (tile - patch[..., :3] @ _LUMA))[..., None]
        out[y:y + pad, x:x + pad] = np.clip(np.rint(patch), 0, 255).astype(np.uint8)


def _detect(gray: np.ndarray) -> dict[int, np.ndarray]:
    """All four corners (the marker's own TL, TR, BR, BL) of each marker found.

    Tuned for faint, blurred markers: wider adaptive-threshold windows, a lower contrast
    floor, looser corner fitting, and a second pass on a local-contrast-normalised copy;
    retried upscaled for small photos."""
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.adaptiveThreshWinSizeMax = 63
    params.adaptiveThreshWinSizeStep = 6
    params.adaptiveThreshConstant = 4
    params.polygonalApproxAccuracyRate = 0.06
    params.minOtsuStdDev = 2.0
    detector = cv2.aruco.ArucoDetector(_DICT, params)
    best: dict[int, np.ndarray] = {}
    sources = (gray, _local_contrast(gray))  # normalised once, at the photo's own size
    for scale in (1, 2, 3):
        found = {}
        for source in sources:
            img = source
            if scale > 1:
                img = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                img = cv2.addWeighted(img, 1.8, cv2.GaussianBlur(img, (0, 0), 1.5 * scale), -0.8, 0)  # unsharp mask
            corners, ids, _ = detector.detectMarkers(img)
            for quad, marker_id in zip(corners, ids.ravel() if ids is not None else []):
                marker_id = int(marker_id)
                if marker_id in MARKERS and marker_id not in found:
                    found[marker_id] = quad.reshape(4, 2) / scale
        if len(found) > len(best):
            best = found
        if len(best) == len(MARKERS):
            break
    return best


def _local_contrast(gray: np.ndarray) -> np.ndarray:
    """Brightness normalised by its neighbourhood, so a faint marker looks like a strong one."""
    f = gray.astype(np.float32)
    sigma = max(gray.shape) / 40
    mean = _smooth(f, sigma)
    spread = np.sqrt(np.maximum(_smooth((f - mean) ** 2, sigma), 0)) + 2.0
    return np.clip(128 + 50 * (f - mean) / spread, 0, 255).astype(np.uint8)


def _warp(luma: np.ndarray, matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Perspective-warp to the canonical grid (oversampling first to avoid aliasing).

    Also returns which blocks lie mostly inside the given picture; blocks that were
    cropped away are treated as missing instead of as wrong evidence.
    """
    corners = cv2.perspectiveTransform(np.float32([[[0, 0], [CANON, 0], [CANON, CANON], [0, CANON]]]), np.linalg.inv(matrix))[0]
    span = max(np.linalg.norm(corners[1] - corners[0]), np.linalg.norm(corners[3] - corners[0]))
    k = int(np.clip(np.ceil(span / CANON), 1, 4))
    scaled = np.diag([k, k, 1.0]) @ matrix
    big = cv2.warpPerspective(luma, scaled, (CANON * k, CANON * k), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    canon = big if k == 1 else cv2.resize(big, (CANON, CANON), interpolation=cv2.INTER_AREA)
    inside = cv2.warpPerspective(np.ones_like(luma), matrix, (CANON, CANON), flags=cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    valid = inside.reshape(GRID, BLOCK, GRID, BLOCK).mean(axis=(1, 3)).ravel() > 0.9
    return canon, valid
