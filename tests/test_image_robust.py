"""Mode 2 tests on a synthetic photo-like image with simulated attacks (no external files)."""
import cv2
import numpy as np
import pytest

import watermark as wm
from watermark import image_robust as ir
from watermark.core import CapacityError

MODE = "image_robust"
PAYLOAD = b"ID7"


def _synthetic(h=720, w=1000, seed=3):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    base = 120 + 60 * np.sin(x / 90) * np.cos(y / 70)
    noise = cv2.GaussianBlur(rng.normal(0, 40, (h, w)).astype(np.float32), (0, 0), 3)
    img = np.stack([base + noise, base * 0.9 + noise * 0.8 + 20, 200 - base * 0.5 + noise], -1)
    return np.clip(img, 0, 255).astype(np.uint8)


def jpeg(img, q):
    ok, buf = cv2.imencode(".jpg", img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)[..., ::-1]


def crop(img, left=0.0, top=0.0, right=0.0, bottom=0.0):
    h, w = img.shape[:2]
    return img[int(top * h):h - int(bottom * h), int(left * w):w - int(right * w)]


def screenshot(img):
    small = cv2.resize(img, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    canvas = np.full((small.shape[0] + 120, small.shape[1] + 200, 3), 235, np.uint8)
    canvas[80:80 + small.shape[0], 100:100 + small.shape[1]] = small
    return canvas


def rotate(img, deg):
    h, w = img.shape[:2]
    big = np.full((h + 200, w + 200, 3), 128, np.uint8)
    big[100:100 + h, 100:100 + w] = img
    m = cv2.getRotationMatrix2D(((w + 200) / 2, (h + 200) / 2), deg, 1.0)
    return cv2.warpAffine(big, m, (w + 200, h + 200), borderValue=(128, 128, 128))


def phone(img, tilt=0.15, scale=0.6, blur=1.2, quality=80):
    h, w = img.shape[:2]
    W, H = int(w * scale * 1.4), int(h * scale * 1.4)
    cx, cy, ww, hh = W / 2, H / 2, w * scale, h * scale
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[cx - ww / 2 + tilt * ww, cy - hh / 2], [cx + ww / 2 - tilt * ww * 0.3, cy - hh / 2 + tilt * hh * 0.5],
                      [cx + ww / 2, cy + hh / 2], [cx - ww / 2, cy + hh / 2 - tilt * hh * 0.3]])
    bg = np.full((H, W, 3), (90, 100, 110), np.uint8)
    out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (W, H), dst=bg,
                              borderMode=cv2.BORDER_TRANSPARENT)
    out = cv2.GaussianBlur(out, (0, 0), blur)
    out = np.clip(out + np.random.default_rng(0).normal(0, 3, out.shape), 0, 255).astype(np.uint8)
    return jpeg(out, quality)


ATTACKS = {
    "none": lambda x: x,
    "jpeg50": lambda x: jpeg(x, 50),
    "jpeg30": lambda x: jpeg(x, 30),
    "resize0.5": lambda x: cv2.resize(x, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
    "blur2": lambda x: cv2.GaussianBlur(x, (0, 0), 2),
    "tone": lambda x: np.clip(x.astype(np.float32) * 0.8 + 30, 0, 255).astype(np.uint8),
    "screenshot+jpeg60": lambda x: jpeg(screenshot(x), 60),
    "crop_right10%": lambda x: crop(x, right=0.10),
    "crop_bottom20%": lambda x: crop(x, bottom=0.20),
    "crop_corner": lambda x: crop(x, left=0.12, top=0.12),
    "crop_two_sides": lambda x: crop(x, left=0.1, top=0.1),
    "rotate7": lambda x: rotate(x, 7),
    "phone": phone,
}


@pytest.fixture(scope="module")
def image():
    return _synthetic()


@pytest.fixture(scope="module")
def marked(image, fixed_key):
    before = image.copy()
    out = wm.embed(image, PAYLOAD, fixed_key, mode=MODE)
    assert np.array_equal(image, before), "input must not be modified"
    return out


def test_quality(image, marked):
    pad = int(0.11 * min(image.shape[:2])) + 2
    inner = (slice(pad, -pad), slice(pad, -pad))
    mse = np.mean((marked[inner].astype(float) - image[inner]) ** 2)
    assert 10 * np.log10(255 ** 2 / mse) > 36


@pytest.mark.parametrize("attack", list(ATTACKS))
def test_survives(marked, fixed_key, attack):
    result = wm.extract(ATTACKS[attack](marked), fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD, result.details
    assert 0 < result.score <= 1 and result.details["layout"] == "tiles"


def test_rejections(image, marked, fixed_key, other_key):
    assert not wm.extract(marked, other_key, mode=MODE).verified
    assert not wm.extract(image, fixed_key, mode=MODE).verified


def test_identify(marked, fixed_key, key, other_key):
    who = wm.identify(jpeg(marked, 60), {"a": key, "owner": fixed_key, "b": other_key}, mode=MODE)
    assert who.status == "match" and who.best == "owner"


def test_compact_profile_and_grayscale(fixed_key):
    gray = cv2.cvtColor(_synthetic(600, 600, seed=5), cv2.COLOR_RGB2GRAY)
    secret = b"compact-id-0042"
    out = wm.embed(gray, secret, fixed_key, mode=MODE, profile="compact")
    assert out.shape == gray.shape
    result = wm.extract(jpeg(np.dstack([out] * 3), 60), fixed_key, mode=MODE, profile="compact")
    assert result.verified and result.payload == secret


def test_markers_layout_survives_steep_blurry_photo(image, fixed_key):
    marked = wm.embed(image, PAYLOAD, fixed_key, mode=MODE, markers=True)
    photo = phone(marked, tilt=0.2, scale=0.6, blur=1.8, quality=70)
    result = wm.extract(photo, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD and result.details["layout"] == "markers"


def test_faint_markers_survive_tilted_blurry_photo(image, fixed_key):
    marked = wm.embed(image, PAYLOAD, fixed_key, mode=MODE, markers=True, marker_strength=0.25)
    photo = phone(marked, tilt=0.2, scale=0.6, blur=1.8, quality=70)
    result = wm.extract(photo, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD and result.details["layout"] == "markers"


def test_markers_on_one_line_are_enough(image, fixed_key):
    """Only the bottom three markers (on one line) are visible: every corner of each
    marker, plus a fit of the picture's aspect, still gives the whole geometry."""
    marked = wm.embed(image, PAYLOAD, fixed_key, mode=MODE, markers=True)
    photo = phone(marked, tilt=0.15, scale=0.6, blur=1.2, quality=80).copy()
    photo[: int(0.55 * photo.shape[0])] = 128
    found = ir._detect(cv2.cvtColor(photo, cv2.COLOR_RGB2GRAY))
    assert sorted(found) == [2, 3, 6]  # bottom-right, bottom-left, bottom-middle
    result = wm.extract(photo, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD and result.details["geometry"].startswith("markers(3)")


def test_marker_layout_without_markers_uses_outline(image, fixed_key, monkeypatch):
    """No marker found at all (too faint, blurred or covered): the picture's outline gives
    rough geometry and the data blocks pull it into place."""
    marked = wm.embed(image, PAYLOAD, fixed_key, mode=MODE, markers=True)
    photo = phone(marked, tilt=0.15, scale=0.6, blur=2.5, quality=75)
    monkeypatch.setattr(ir, "_detect", lambda gray: {})
    result = wm.extract(photo, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD and result.details["geometry"].startswith("marker-outline")


def test_marker_strength_limits(image, fixed_key):
    for bad in (0, -0.5, 1.5):
        with pytest.raises(ValueError):
            wm.embed(image, PAYLOAD, fixed_key, mode=MODE, markers=True, marker_strength=bad)


def test_invisible_layout_adds_no_markers(marked):
    assert ir._detect(cv2.cvtColor(marked, cv2.COLOR_RGB2GRAY)) == {}


def test_limits(image, fixed_key):
    assert ir.capacity() == 27 and ir.robust_capacity() == 16
    assert ir.capacity(profile="standard") == 11 and ir.robust_capacity(profile="standard") == 0
    assert ir.capacity(markers=True) == 21 and ir.robust_capacity(markers=True) == 12
    with pytest.raises(CapacityError):
        wm.embed(image, b"x" * (ir.capacity() + 1), fixed_key, mode=MODE)
    with pytest.raises((CapacityError, ValueError)):
        wm.embed(image[:300, :300], PAYLOAD, fixed_key, mode=MODE)


def test_photo_with_hidden_corner(marked, fixed_key):
    """Like a screen photo where the Dock covers one corner of the picture."""
    photo = phone(marked, tilt=0.12, scale=0.7).copy()
    h, w = photo.shape[:2]
    photo[int(0.7 * h):int(0.9 * h), int(0.7 * w):int(0.9 * w)] = 20  # covers the picture's corner
    result = wm.extract(photo, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD, result.details


def test_standard_profile_still_works(image, fixed_key):
    out = wm.embed(image, b"ID7", fixed_key, mode=MODE, profile="standard")
    result = wm.extract(jpeg(out, 70), fixed_key, mode=MODE, profile="standard")
    assert result.verified and result.payload == b"ID7" and result.details["code_rate"] == "2/3"
    assert not wm.extract(out, fixed_key, mode=MODE).verified  # profiles are not interchangeable


@pytest.mark.parametrize("rate", list(ir.PUNCTURE))
def test_conv_code_round_trip(rate):
    rng = np.random.default_rng(1)
    bits = rng.integers(0, 2, 120).astype(np.uint8)
    coded = ir._conv_encode(bits, rate)
    decoded, agreement = ir._conv_decode(coded + rng.normal(0, 0.5, coded.size), bits.size, rate)
    assert np.array_equal(decoded, bits) and agreement > 0.8


def test_code_survives_screen_photo_cell_errors():
    """Group test photos of a laptop screen read 88-95 % of cells correctly; the old
    Reed-Solomon format failed on all of them. 12 % random cell errors must decode."""
    rng = np.random.default_rng(2)
    nbits = 8 * 11                       # compact frame of a 6-byte payload
    ok = 0
    for _ in range(20):
        bits = rng.integers(0, 2, nbits).astype(np.uint8)
        coded = ir._conv_encode(bits, "1/2")
        cells = coded[np.arange(ir._budget(False)) % coded.size]
        cells = cells * np.where(rng.random(cells.size) < 0.12, -1, 1) * rng.uniform(0.3, 1.7, cells.size)
        soft = np.bincount(np.arange(cells.size) % coded.size, weights=cells, minlength=coded.size)
        ok += np.array_equal(ir._conv_decode(soft, nbits, "1/2")[0], bits)
    assert ok >= 19


def test_small_tilted_picture_among_clutter(marked, fixed_key):
    """Like a preview window on a busy screen: the picture fills ~10 % of a big photo
    full of other rectangles, so only the straight-edge outline search finds it."""
    rng = np.random.default_rng(1)
    H, W = 3600, 2700
    scene = cv2.GaussianBlur(rng.normal(60, 25, (H, W, 3)).astype(np.float32), (0, 0), 4)
    for _ in range(25):
        x, y = rng.integers(0, W - 300), rng.integers(0, H - 300)
        w, h = rng.integers(80, 700, 2)
        cv2.rectangle(scene, (int(x), int(y)), (int(x + w), int(y + h)), tuple(float(c) for c in rng.integers(20, 230, 3)), -1)
    scene = np.clip(scene, 0, 255).astype(np.uint8)
    h, w = marked.shape[:2]
    corners = np.float32([[1150, 1450], [2250, 1700], [2050, 2500], [1000, 2250]])
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), corners)
    photo = cv2.warpPerspective(marked, m, (W, H), dst=scene, borderMode=cv2.BORDER_TRANSPARENT)
    photo = jpeg(np.clip(cv2.GaussianBlur(photo, (0, 0), 1.0) + rng.normal(0, 2, photo.shape), 0, 255).astype(np.uint8), 85)
    result = wm.extract(photo, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD and result.details["geometry"].startswith("lines"), result.details


@pytest.mark.parametrize("deg", [28, -20])
def test_tilted_close_up(marked, fixed_key, deg):
    """A tilted, zoomed-in photo of one corner of a picture on a dark screen: about one
    and a half tiles each way, too few repeats to find the lattice; the edge gives the tilt."""
    h, w = marked.shape[:2]
    view_w, view_h = 820, 640
    m = cv2.getRotationMatrix2D((w, 0), deg, 1.5)
    m[:, 2] += np.array([view_w * 0.8, view_h * 0.15]) - np.array([w, 0])
    bg = np.full((view_h, view_w, 3), 25, np.uint8)
    shot = jpeg(cv2.warpAffine(marked, m, (view_w, view_h), dst=bg, borderMode=cv2.BORDER_TRANSPARENT), 85)
    result = wm.extract(shot, fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD, result.details
    assert result.details["geometry"].startswith("angles")


def faint_copy(img, scale=0.3):
    """A small, blurred, noisy, heavily compressed copy (weak signal, easy geometry)."""
    small = cv2.GaussianBlur(cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), (0, 0), 0.8)
    return jpeg(np.clip(small + np.random.default_rng(3).normal(0, 4, small.shape), 0, 255).astype(np.uint8), 40)


@pytest.fixture(scope="module")
def key_only(image, fixed_key):
    return wm.embed(image, b"", fixed_key, mode=MODE, strength=0.3)


def test_key_only_round_trip(key_only, fixed_key, other_key):
    result = wm.extract(jpeg(key_only, 60), fixed_key, mode=MODE)
    assert result.verified and result.payload == b"" and result.details["key_only"], result.details
    assert result.details["z"] >= result.details["z_min"] and 0.5 <= result.score <= 1
    assert not wm.extract(key_only, other_key, mode=MODE).verified


def test_key_only_reads_what_a_hidden_id_cannot(image, key_only, fixed_key):
    with_id = wm.embed(image, PAYLOAD, fixed_key, mode=MODE, strength=0.3)
    assert not wm.extract(faint_copy(with_id), fixed_key, mode=MODE).verified
    assert wm.extract(faint_copy(key_only), fixed_key, mode=MODE).verified


def test_key_only_identify(key_only, fixed_key, key, other_key):
    who = wm.identify(faint_copy(key_only, 0.45), {"a": key, "owner": fixed_key, "b": other_key}, mode=MODE)
    assert who.status == "match" and who.best == "owner"


def test_key_only_does_not_match_unmarked_or_other_marks(image, marked, fixed_key, other_key):
    for picture, k in ((image, fixed_key), (marked, other_key), (faint_copy(image), fixed_key)):
        result = wm.extract(picture, k, mode=MODE)
        assert not result.verified and result.details["key_only_z"] < result.details["key_only_z_min"]


def test_key_only_limits(image, fixed_key):
    with pytest.raises(ValueError):
        wm.embed(image, b"", fixed_key, mode=MODE, markers=True)
    assert ir.key_only_threshold(1e-6) > ir.key_only_threshold(1e-3) > ir.NULL_LOCATION


def test_crop_of_about_one_tile(marked, fixed_key):
    """A screenshot of a small part of the picture, shrunk: barely one whole tile left."""
    side = ir._tile_side(*marked.shape[:2])
    part = marked[150:150 + int(1.05 * side), 300:300 + int(1.03 * side)]
    result = wm.extract(cv2.resize(part, None, fx=0.78, fy=0.78, interpolation=cv2.INTER_AREA), fixed_key, mode=MODE)
    assert result.verified and result.payload == PAYLOAD, result.details
