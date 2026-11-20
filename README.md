# TraceMark

> Every set of notes you sell carries an invisible signature that can be traced back to its buyer.

**English** | [中文](README.zh.md)

**CS1302A Project Documentation**

**TraceMark** is a Python watermarking and leak-tracing module for tutors, notes sellers, and small content creators. It cannot stop someone from copying a file, but when a copy is resold or leaked, it can answer one crucial question: **who was this copy originally issued to?**

| Item | Detail |
|---|---|
| Course | CS1302A Introduction to Computer Programming (2026/27 Semester A) |
| Team | 4 members (group85): Wing, Kay, Billy, Amos |
| Deadline | 22 November 2026 (Sunday), 23:59 |
| Document version | v0.3 (17 November 2026) |
| Implementation status | All five modes implemented (package version `0.1.0`); PyPI, JupyterHub install, and CLI still pending |
| Test status | 2026-11-16 local run: 84 passed, 2 skipped (131.83 s, Python 3.12.13); coverage not measured; design targets listed separately |
| Licence | Code MIT (planned); documentation CC BY 4.0 (planned) |

> **In one sentence:** TraceMark embeds an invisible buyer identifier into each copy of a teaching file and, after a leak, compares the file against candidate keys to produce a mode-specific score that must be checked against transaction records.  
> **What it is not:** A DRM / encryption / automatic conviction tool.  
> **Current state:** all five modes run locally; PyPI, JupyterHub, and the formal CLI are still being finished.

---

## Documentation map

This README is available in both English and Chinese. The English version is the default entry point; the Chinese version is available at [README.zh.md](README.zh.md). The remaining technical documents are written in Traditional Chinese.

| Document | Content |
|---|---|
| **README.md** (this page) | Summary, installation and usage, five modes |
| [README.zh.md](README.zh.md) | Chinese README |
| [01-background.md](docs/01-background.md) | Background and problem, two ideas, literature review, design goals, references, glossary |
| [02-architecture-and-api.md](docs/02-architecture-and-api.md) | System architecture, public API, payload format, future work |
| [03-testing-and-evaluation.md](docs/03-testing-and-evaluation.md) | What has been tested, attack matrix, evaluation metrics, targets still to verify |
| [04-project-and-course.md](docs/04-project-and-course.md) | Team roles, schedule, milestones, risks, course mapping |
| [05-legal-ethics-and-appendix.md](docs/05-legal-ethics-and-appendix.md) | Legal position, ethics, technical limits, AI-use statement, acknowledgements, licence |
| [06-version-history.md](docs/06-version-history.md) | Version history |

---

## 1. Summary

In Hong Kong, DSE notes, tutoring materials, and self-made revision notes have long circulated on second-hand platforms such as Carousell as PDFs, images, or printed copies. Producing a set of notes costs many hours; copying a PDF costs almost nothing. When a buyer resells the same file, uploads it to cloud storage, or shares it with friends, the original author loses not only one sale but also the pricing power and trust built around the material.

The traditional defence is a visible watermark such as “For the use of XXX only” printed on the PDF. It deters some buyers, but it can be cropped, covered, retyped after OCR, or removed by reformatting a screenshot. Once the visible mark is gone, the seller has no thread to follow. In other words, a visible watermark can deter but cannot trace.

**TraceMark** is designed to combat the lack of a paper trail. It embeds a separate identifier for each buyer and, when a file leaks, compares the content against candidate keys to report the most likely leaked version and a similarity score. The module provides four watermarking methods plus one generative mode:

1. **Fragile image mode** (`image_fragile`): modifies the least significant bit (LSB) of pixel channels. Capacity is high and the change is invisible; it is suitable for hiding data and verifying an extracted payload, but it does not localise tampering.
2. **Robust image mode** (`image_robust`): adds a key-dependent balance pattern to luminance, with pilot-block synchronisation, convolutional coding, and soft Viterbi decoding. It targets compression, cropping, rotation, and screenshots.
3. **Invisible text mode** (`text_invisible`): encodes bits with U+200B/U+2060 and marks frame boundaries with U+2063; a 64-byte payload adds 682 invisible characters.
4. **Lexical text mode** (`text_lexical`): carries signals through English synonym substitutions, using Reed–Solomon coding and equation solving; it requires candidates or a provider.
5. **Generative text mode** (`text_generative`): adds a statistical watermark during model generation; extraction and identification require the same tokenizer and target the Apple silicon MLX backend.

All five modes share the same `embed`, `extract`, and `identify` interface. `identify` returns `match`, `none`, or `ambiguous`, not a “probability that this person leaked the file”. TraceMark is positioned as **post-hoc tracing**, not DRM or encryption: it cannot prevent copying, but after a leak it can give the seller a technical lead that can be checked against transaction records.

This documentation set covers the problem background, the trade-off between two initial ideas, related work, system design, test results, course mapping, and limitations. **Unless a number is explicitly labelled as a measured result, it is a design target.**

---

## Quick start: install, import, and use

> **Status:** the PyPI release is still pending, and the JupyterHub install has not been verified. The instructions below focus on a local install. The package name is `watermark`, and Python `>=3.10` is required.

### Install

| Method | Command | Status |
|---|---|---|
| PyPI | `pip install watermark` | Not published by this project yet; wait for the release announcement |
| Local source | `git clone https://github.com/barebone-lab/tracemark.git`<br>`cd tracemark`<br>`python -m pip install .` | Available |
| Development mode | `python -m pip install -e .` | Available |
| Generative mode (optional) | `python -m pip install '.[generative]'` | Requires Apple silicon and MLX; downloads a model on first use |

Base dependencies are `numpy>=1.24`, `cryptography>=42`, `reedsolo>=1.7,<2`, and `opencv-python-headless>=5.0.0.93`. The generative optional dependency is `mlx-lm>=0.31.3`.

### Import

```python
from watermark import embed, extract, identify
```

### Basic usage

The following example runs as written. `content` is text in memory, `payload` is `bytes`, and `key` is a non-empty `bytes` or `str`.

```python
from watermark import embed, extract, identify

keys = {"buyer-001": "demo-key-001", "buyer-002": "demo-key-002"}
text = "This is a set of teaching notes. " * 20
marked = embed(
    text,
    b"BUYER-001",
    keys["buyer-001"],
    mode="text_invisible",
)
result = extract(marked, keys["buyer-001"], mode="text_invisible")
print(result.verified, result.payload)  # True, b'BUYER-001'

who = identify(marked, keys, mode="text_invisible")
print(who.status, who.best)  # match, buyer-001
```

Image input is a NumPy `uint8` array (RGB, RGBA, or grayscale); `embed` returns a new image array or string and leaves the input unchanged. The API does not open or save files; file I/O is the caller's responsibility.

### Modes and options

| `mode` | `content` | Required data / options | Default profile |
|---|---|---|---|
| `image_fragile` | Image array | Non-empty `payload`; optional `bits_per_channel=1` or `2`, `use_alpha=False` | `standard` |
| `image_robust` | Image array | Short `payload` or `b''`; optional `strength=1.0`, `markers=False`, `marker_strength=0.4` | `compact` |
| `text_invisible` | Text | Non-empty `payload`; optional `copies=1` | `standard` |
| `text_lexical` | English text | `payload` or `b''`; exactly one of `provider=` or `candidates=` is required | `compact` |
| `text_generative` | Generation prompt | `payload=b''`, `model=`; optional `max_tokens=400`, `temperature=0.8`, `top_k=64`, `seed=` | No payload frame |

For `image_robust` and `text_lexical`, `payload=b''` means key-only: only a key-derived trace is left, a successful extraction returns `payload=b''`, and no separate hidden data can be recovered. Key-only `image_robust` requires `markers=False`.

### Return values and exceptions

- `ExtractResult`: `mode`, `verified`, `score`, `payload`, `details`.
- A wrong key or no watermark usually returns `verified=False`, `payload=None`, `score=0`; the reason is in `details`.
- `IdentifyResult`: `status`, `best`, `results`. `status` is `match` (exactly one key passes), `none` (all fail), or `ambiguous` (more than one key passes).
- `best` is the label with the highest score among passing candidates, or `None` if all fail. `results` is a dict from candidate label to `ExtractResult`, preserving candidate order; it is not a sorted list.
- Exceptions: `WatermarkError`, with subclasses `CapacityError` and `UnsupportedModeError`; invalid arguments may also raise `ValueError` or `TypeError`.

`score` is a within-mode quality/evidence indicator, not a probability that a person leaked the file. When a payload is present, verification uses an authentication tag; for key-only and generative modes, `verified=True` means the statistical threshold was met.

### Current limitations

- PyPI has not yet been published; the JupyterHub install and formal CLI remain unfinished. CLI commands are not available yet; use the Python API.
- v1 supports images (formats that can be converted to NumPy arrays, such as PNG/BMP) and text; PDF, video, and audio are not supported.
- A watermark is neither DRM nor encryption; an identification score is a lead, not a verdict.
- `text_lexical` currently supports only an English tokenizer and lexicon; Chinese synonym support may be available in the future.
- `text_generative` requires a model and tokenizer; the MLX backend downloads a model on first use. It is not an AI content detector.

---

## 7. The five modes

### 7.1 Fragile image mode (`image_fragile`)

**Principle:** Payload bits are written into the least significant bit (LSB) of each pixel channel. The change is at most a few least-significant-bit levels and is almost invisible. Before embedding, a key-derived pseudorandom permutation spreads the bits across the image; extraction reverses the permutation with the same key. Each channel can carry one or two bits; RGBA preserves alpha by default.

**Capacity:** For a 1024×1024 RGB image with `standard` profile and one bit per channel, net capacity is **393,195 bytes** (after the 21-byte payload overhead).

**Robustness:** Suitable only for lossless formats and identical copies; JPEGs, scaling, cropping, and screenshots destroy the payload.

**Purpose:** To conceal data and verify a certain payload. It does **not** provide tamper localisation: there is no coordinate or heat map, and no integrity authentication of the whole carrier; a successful extraction does not prove that the image is unmodified.

**References:** van Schyndel et al. (1994); Yeung & Mintzer (1997).

### 7.2 Robust image mode (`image_robust`)

**Principle:** a key-dependent balance pattern is added to image luminance and tiled repeatedly. Pilot blocks provide synchronisation; autocorrelation and image contours estimate rotation, scale, and perspective. The payload uses a convolutional code, repetition, and soft Viterbi decoding; when a payload is present, an authentication tag verifies it.

**Markers:** `markers=False` by default, with no visible corner marks. `markers=True` adds eight ArUco markers at the four corners and four edge midpoints for harder reproduction cases; visibility is controlled by `marker_strength`.

**Capacity:** the default marker-free compact layout holds up to **27 bytes**; with full rate-1/2 error correction it holds up to **16 bytes**. Marker layouts hold **21/12 bytes** respectively. Longer payloads use weaker error correction and become less robust.

**Limits:** embedding requires the image's shorter side to be at least **400 px**. Success under cropping, rotation, compression, and reproduction depends on the image, payload, and distortion; report only measured cases and do not claim that “any attack can be recovered”.

**References:** Cox et al. (1997); Ge et al. (2023); Pramila et al. (2008).

### 7.3 Invisible text mode (`text_invisible`)

**Principle:** U+200B represents 0, U+2060 represents 1, and U+2063 marks the boundary of each frame. Each data character carries one bit; insertion points are allocated at safe text gaps so that combining characters and emoji are not split. The key protects the payload; it does not scramble insertion positions.

**Capacity:** the `standard` profile holds **64 bytes**; one copy adds `(64+21)*8+2 = 682` invisible characters.

**Robustness:** copy-paste usually preserves the characters; OCR, retyping, or platform filtering removes them. NFC/NFKC normalisation alone is not the same as platform filtering; the example still extracted after normalisation.

**Ethics:** zero-width characters can also hide malicious instructions or bypass review. This project supports defensive use only and discloses the technique in its documentation.

**References:** Ishtiaq et al. (2025); Kaushik & Bhardwaj (2021).

### 7.4 Lexical text mode (`text_lexical`)

**Principle:** The current tokenizer and lexicon are tailor-made for English; support for Chinese synonyms will soon be added. Embedding requires approved substitution candidates: `candidates=` is a sequence of `Substitution(start, end, original, replacement)`, where positions are indices into the original string and the class is imported from `watermark.text_lexical`. Alternatively, `provider=` supplies `propose(text)` to return candidates; the provided `DeepSeekProvider` uses `DEEPSEEK_API_KEY`, sends sentences to DeepSeek, and double-checks the substitutions, which involves an external API and meaning-review limits.

**Extraction:** Only the text, the same public lexicon, and the key are needed; it does not depend on an AI reply or the original document. Encoding uses anchors, local addresses, Reed–Solomon coding, and equation solving, rather than the simplified description “each word pair independently carries one bit and identification uses the match ratio”.

**Capacity (measured on a specific sample, not a general guarantee):** The current 7,000-English-word sample can hide **3 bytes**; its 3,000-word prefix is insufficient under the specified setting; the 2,000-word key-only sample can identify the source but cannot hide the same 3-byte ID.

**Limits:** OCR readability depends on whether the actual vocabulary survives; rewriting, translation, and typo/omission changes can cause failure. AI candidate review does not guarantee that meaning is unchanged. With `return_details=True`, embedding returns `(marked_text, details)`, including the actual substitutions; by default it returns the marked text directly.

**References:** Atallah et al. (2001); Lu et al. (2009); Stamatatos (2009).

### 7.5 Generative text mode (`text_generative`)

**Principle:** a statistical watermark is added while the model generates text, giving the output a traceable signal. `payload=b''`; no payload frame is used. `model=` must provide a tokenizer, `decode`, `prompt_ids`, `logits`, and `eos_ids`. The provided implementation is `watermark.text_generative.MLXModel` for Apple silicon; it downloads a model on first use.

**Extraction and identification:** `tokenizer=` is required and must match the one used during generation; `verified=True` means the statistical threshold was met, not that a fixed payload was decoded.

**Positioning:** This is a watermark generation tool, not an AI content detector. It does not decide whether a piece of text was generated by AI, and it does not claim compliance with Article 50 of the EU AI Act.

**References:** Kirchenbauer et al. (2023); Fernandez et al. (2023).

### 7.6 Comparison

**Table 5: trade-offs between the five modes**

| Mode | Carrier | Capacity | Invisibility | Robustness | Main attacks | Implementation effort |
|---|---|---|---:|---|---|---|
| `image_fragile` | Image | High (393,195 bytes @1024²) | High | Low | Compression, scaling, cropping | Low |
| `image_robust` | Image | Low (16–27 bytes) | High | Medium | Geometric transforms, strong compression, screenshots, reproduction | High |
| `text_invisible` | Text | Medium (64 bytes / 682 characters) | High (but detectable) | Medium | Normalisation, platform filtering, OCR | Low |
| `text_lexical` | English text | Low (3 bytes in the 7,000-word sample) | High | Low–medium | Rewriting, translation, typos, omissions | Medium–high |
| `text_generative` | Model-generated text | No fixed payload | Not applicable | Medium | Changing model/decoding settings | High |

The five modes are not mutually exclusive. In practice they can be combined: a robust image watermark can carry a buyer number, invisible characters can carry a transaction number, and a lexical watermark can provide an additional text lead. Layering raises the cost of removal but also changes more of the content; combined settings remain a design option for future evaluation.

---

## About this documentation set

- This page is the English README and the default GitHub entry point; the Chinese version is available at [README.zh.md](README.zh.md).
- The other six technical documents are linked from the documentation map above and are written in Traditional Chinese.
- Document version: v0.3 (17 November 2026). Implementation facts are current to 16 November 2026; measured results and unverified targets are reported separately in [03-testing-and-evaluation.md](docs/03-testing-and-evaluation.md).
