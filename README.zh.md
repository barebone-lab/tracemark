# TraceMark（溯印）

> 讓每一份售出的筆記，都帶有一個看不見、但追得回的簽名。

[English](README.md) | **中文**

**CS1302A 專案文件 · Project Documentation**

**TraceMark** 是一個以 Python 編寫的數碼水印與外洩溯源模組，為補習導師、筆記賣家與小型內容創作者而設。它不能阻止別人複製檔案，但當檔案被轉售或外洩時，它能回答一個關鍵問題：**這一份副本，原本是交給誰的？**

| 項目 | 內容 |
|---|---|
| 課程 | CS1302A Introduction to Computer Programming（2026/27 Semester A） |
| 團隊 | 4 人（group85）：Wing、Kay、Billy、Amos |
| 截止 | 2026-11-22（星期日）23:59 |
| 文件版本 | v0.3（2026-11-17） |
| 實作狀態 | 五種模式已實作（套件版本 `0.1.0`）；PyPI、JupyterHub 安裝與 CLI 待完成 |
| 測試狀態 | 2026-11-16 本地：84 passed、2 skipped（131.83 秒，Python 3.12.13）；coverage 未量測；設計目標另列 |
| 授權 | 程式碼 MIT（預定）；文件 CC BY 4.0（預定） |

> **一句話**：TraceMark 為每份售出的教材嵌入看不見的買家識別碼，並在外洩後以候選密鑰比對，輸出可解釋的模式內證據分數。  
> **不是什麼**：不是 DRM、不是加密、不是自動定罪工具。  
> **現況**：五種模式已可在本機執行；PyPI、JupyterHub 與正式 CLI 仍在收尾。

---

## 文件導覽

本 README 有中、英文兩個版本；英文版是預設入口，中文版即本頁。其餘技術文件目前以繁體中文撰寫。

| 文件 | 內容 |
|---|---|
| [README.md](README.md) | English README（default） |
| **README.zh.md**（本頁） | 摘要、安裝與使用、五種模式 |
| [01-background.md](docs/01-background.md) | 背景與問題、兩個構思、文獻回顧、設計目標、參考文獻、名詞表 |
| [02-architecture-and-api.md](docs/02-architecture-and-api.md) | 系統架構、公開 API、載荷格式、未來工作 |
| [03-testing-and-evaluation.md](docs/03-testing-and-evaluation.md) | 已測範圍、攻擊矩陣、評估指標、待驗證目標 |
| [04-project-and-course.md](docs/04-project-and-course.md) | 團隊分工、時程、里程碑、風險、課程對應 |
| [05-legal-ethics-and-appendix.md](docs/05-legal-ethics-and-appendix.md) | 法律定位、倫理原則、技術限制、AI 使用聲明、致謝、授權 |
| [06-version-history.md](docs/06-version-history.md) | 版本紀錄 |

---

## 1. 摘要

在香港，DSE 筆記、補習材料與自製溫習材料長期在 Carousell 等二手平台以 PDF、圖片或印刷本形式流通。一份筆記的製作成本以大量時間計，複製一個 PDF 的成本卻接近零。當買家將同一份檔案轉售、上載到雲端或分享給朋友，原作者損失的不只是單次交易收入，還有隨之而來的價格競爭與品牌信任。

傳統做法是在 PDF 上印上「僅供 XXX 使用」的可見浮水印。這種方法有阻嚇作用，但買家可以裁剪、遮蓋、以 OCR 重新輸入，或直接截圖後再排版；一旦可見標記被移除，賣家便失去追溯線索。換言之，傳統水印只能「阻嚇」，卻無法在事後「追溯」。

**TraceMark（溯印）** 正是針對這個缺口而設計。它為每一位買家嵌入一組獨立的識別碼，並在文件外洩時，以候選密鑰比對內容，輸出「最可能的外洩版本」與模式內證據分數。模組提供四種水印方法，另加一個生成模式：

1. **圖像脆弱模式**（`image_fragile`）：改動像素通道的最低有效位元（LSB），容量大、不可見，適合隱藏資料與驗證提取到的載荷；不提供竄改定位。
2. **圖像穩健模式**（`image_robust`）：在亮度中加入密鑰決定的平衡圖案，配合 pilot 區塊同步、卷積碼與 soft Viterbi 解碼，目標是抵抗壓縮、裁切、旋轉與截圖。
3. **文字零寬模式**（`text_invisible`）：以 U+200B／U+2060 編碼位元、U+2063 標示 frame 邊界；64 bytes 載荷會新增 682 個不可見字元。
4. **文字選詞模式**（`text_lexical`）：以英文同義詞替換承載訊號，使用 Reed–Solomon 與方程求解；需要候選替換或 provider。
5. **文字生成模式**（`text_generative`）：在模型生成文字時加入統計水印；需要相同 tokenizer 才能提取與識別，主要針對 Apple silicon 的 MLX backend。

五種模式共用同一套 `embed`、`extract`、`identify` 介面。`identify` 會回傳 `match`、`none` 或 `ambiguous`，而不是一個「有幾成機會是某人」的分數。TraceMark 的定位不是 DRM，也不是加密工具，而是**事後追溯**：它不能阻止複製，但能在外洩發生後，為賣家提供可與交易紀錄互相印證的技術線索。

本文件集說明問題背景、兩個構思的取捨、相關文獻、系統設計、測試結果、課程對應與限制。**所有效能數字除非明確標示為已測結果，否則都是設計目標。**

---

## 快速開始：安裝、匯入與使用

> **狀態**：PyPI 發布仍待完成；JupyterHub 安裝仍待驗證。以下以本機安裝為主。套件名稱是 `watermark`，Python 版本要求 `>=3.10`。

### 安裝

| 方式 | 指令 | 狀態 |
|---|---|---|
| PyPI | `pip install watermark` | 本專案尚未發布；請待發布公告 |
| 本地原始碼 | `git clone https://github.com/barebone-lab/tracemark.git`<br>`cd tracemark`<br>`python -m pip install .` | 可用 |
| 開發模式 | `python -m pip install -e .` | 可用 |
| 生成模式（可選） | `python -m pip install '.[generative]'` | 需要 Apple silicon 與 MLX；首次使用會下載模型 |

基礎依賴為 `numpy>=1.24`、`cryptography>=42`、`reedsolo>=1.7,<2`、`opencv-python-headless>=5.0.0.93`。生成模式的 optional dependency 是 `mlx-lm>=0.31.3`。

### 匯入

```python
from watermark import embed, extract, identify
```

### 基本用法

以下範例可直接運行；`content` 是記憶體中的文字，`payload` 是 `bytes`，`key` 是非空 `bytes` 或 `str`。

```python
from watermark import embed, extract, identify

keys = {"buyer-001": "demo-key-001", "buyer-002": "demo-key-002"}
text = "這是一份教材，請妥善保存。" * 30
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

圖片輸入是 NumPy `uint8` 陣列（RGB／RGBA 或灰階）；`embed` 直接回傳新的圖片陣列或文字，原輸入不變。API 不開檔、不存檔，檔案讀寫由呼叫者負責。

### 模式與選項

| `mode` | `content` | 嵌入所需資料／參數 | 預設 profile |
|---|---|---|---|
| `image_fragile` | 圖片陣列 | 非空 `payload`；可選 `bits_per_channel=1` 或 `2`、`use_alpha=False` | `standard` |
| `image_robust` | 圖片陣列 | 短 `payload` 或 `b''`；可選 `strength=1.0`、`markers=False`、`marker_strength=0.4` | `compact` |
| `text_invisible` | 文字 | 非空 `payload`；可選 `copies=1` | `standard` |
| `text_lexical` | 英文文字 | `payload` 或 `b''`；必須提供 `provider=` 或 `candidates=` 其中一個 | `compact` |
| `text_generative` | 生成提示文字 | `payload=b''`、`model=`；可選 `max_tokens=400`、`temperature=0.8`、`top_k=64`、`seed=` | 不使用載荷 frame |

`image_robust` 與 `text_lexical` 的 `payload=b''` 是 key-only：只留下密鑰痕跡，提取成功回傳 `payload=b''`，不能還原另一段隱藏資料。`image_robust` 的 key-only 要求 `markers=False`。

### 回傳值與例外

- `ExtractResult`：`mode`、`verified`、`score`、`payload`、`details`。
- 錯密鑰或無水印通常回傳 `verified=False`、`payload=None`、`score=0`；原因在 `details`。
- `IdentifyResult`：`status`、`best`、`results`。`status` 為 `match`（恰好一個 key 通過）、`none`（全部失敗）或 `ambiguous`（多個 key 通過）。
- `best` 是通過者中分數最高的標籤；全部失敗時為 `None`。`results` 是候選標籤對應 `ExtractResult` 的字典，保持候選順序，不是排序列表。
- 例外：`WatermarkError`，以及子類 `CapacityError`、`UnsupportedModeError`；不合法參數亦可能觸發 `ValueError`／`TypeError`。

`score` 是模式內的品質／證據指標，不是「有幾成機會屬於某人」。有載荷時以認證標籤驗證；key-only／生成模式的 `verified=True` 表示通過統計門檻。

### 目前限制

- PyPI 尚未發布；JupyterHub 安裝與正式 CLI 尚未完成。CLI 指令目前不提供，請使用 Python API。
- v1 支援圖像（PNG／BMP 等可轉為 NumPy 陣列的格式）與文字；PDF、影片、音訊未支援。
- 水印不是 DRM，也不是加密；識別分數是線索，不是判決。
- `text_lexical` 目前只支援英文 tokenizer 與詞表；中文同義詞列未來擴充。
- `text_generative` 需要模型與 tokenizer；第一次使用 MLX backend 會下載模型。它不是 AI 生成內容偵測器。

---

## 7. 五種模式

### 7.1 圖像脆弱模式（`image_fragile`）

**原理**：將載荷位元寫入每個像素通道的最低有效位元（LSB）。改動幅度最多只有幾個最低有效位元，肉眼幾乎不可見。嵌入前以密鑰派生偽隨機置換，把位元分散到整幅圖；提取時以同一密鑰還原順序。每個通道可選 1 或 2 bits；RGBA 預設保留 alpha。

**容量**：1024×1024 RGB、`standard` profile、1 bit／channel 的淨容量為 **393,195 bytes**（已扣除 21-byte 載荷開銷）。

**穩健性**：只適用於無損格式與完全相同的副本；JPEG、縮放、裁剪、截圖都會破壞。

**用途**：隱藏資料、驗證提取到的載荷。**不提供竄改定位**：目前沒有座標／熱圖，也沒有對整幅載體建立完整性認證；成功提取不代表整幅圖像未被修改。

**文獻**：van Schyndel et al. (1994); Yeung & Mintzer (1997)。

### 7.2 圖像穩健模式（`image_robust`）

**原理**：在圖像亮度中加入密鑰決定的平衡圖案，重複平鋪；pilot 區塊作同步，透過自相關與圖像輪廓估計旋轉、縮放與透視幾何。載荷使用卷積碼、重複訊號及 soft Viterbi 解碼；有載荷時以認證標籤驗證。

**標記**：預設 `markers=False`，沒有可見角標。`markers=True` 會加入八個 ArUco 標記，位於四角及四邊中點，供較困難的翻拍使用；可見程度由 `marker_strength` 控制。

**容量**：compact 預設無標記布局最大 **27 bytes**；採完整 rate-1/2 糾錯時最多 **16 bytes**。標記布局分別為 **21／12 bytes**。較長載荷會使用較弱糾錯率，穩健性亦會下降。

**限制**：嵌入要求圖片短邊至少 **400 px**。裁剪、旋轉、壓縮與翻拍的成功率受圖像、載荷及失真程度影響，應以已測案例界定範圍，不能宣稱「任意攻擊都可提取」。

**文獻**：Cox et al. (1997); Ge et al. (2023); Pramila et al. (2008)。

### 7.3 文字零寬模式（`text_invisible`）

**原理**：U+200B 表示 0；U+2060 表示 1；U+2063 標示每份 frame 的邊界。每個資料字元是 1 bit，插入點按安全文字間隙分配，避免拆開組合字元、emoji 等。密鑰用於載荷保護，並非打亂插入位置。

**容量**：`standard` profile 可藏 **64 bytes**；單份副本新增 `(64+21)*8+2 = 682` 個不可見字元。

**穩健性**：複製貼上通常會保留；OCR、重新打字或平台過濾會移除不可見訊號。NFC／NFKC 正規化本身不等同平台過濾；本次示例在正規化後仍可提取。

**倫理**：零寬字元也可被用於隱藏惡意指令或繞過審查；本專案只支援防禦性用途，並在文件中揭露技術原理。

**文獻**：Ishtiaq et al. (2025); Kaushik & Bhardwaj (2021)。

### 7.4 文字選詞模式（`text_lexical`）

**原理**：現有 tokenizer 及詞表針對英文；中文同義詞例子列為未來擴充。嵌入需要已批准的候選替換：`candidates=` 是 `Substitution(start, end, original, replacement)` 的序列，位置是原文字串索引，類別從 `watermark.text_lexical` 匯入。另一個選項是 `provider=`，提供 `propose(text)` 回傳候選；現成 `DeepSeekProvider` 可用 `DEEPSEEK_API_KEY`，會將句子送至 DeepSeek 產生並雙重審查替換，因此涉及外部 API 與文意審閱限制。

**提取**：只需文字、相同公開詞表與密鑰；不依賴 AI 回覆或原文。編碼使用錨點、局部位址、Reed–Solomon 及方程求解，而不是「每個詞對獨立承載 1 bit、按匹配比例識別」的簡化描述。

**容量（特定樣本結果，不是通用保證）**：現有 7,000 英文 words 測試樣本可藏 **3 bytes**；其 3,000 words 前綴在指定設定下容量不足；2,000 words 的 key-only 樣本可辨認來源，但不能藏同一個 3-byte ID。

**限制**：OCR 可讀性取決於實際詞彙能否保留；改寫、翻譯、錯詞／漏詞可導致失敗。AI 候選審查亦不保證文意完全不變。`return_details=True` 時嵌入回傳 `(marked_text, details)`，包含實際換詞紀錄；預設直接回傳文字。

**文獻**：Atallah et al. (2001); Lu et al. (2009); Stamatatos (2009)。

### 7.5 文字生成模式（`text_generative`）

**原理**：在模型生成文字時加入統計水印，讓輸出帶有可辨認來源的訊號。`payload=b''`，不使用載荷 frame；`model=` 必須提供 tokenizer、`decode`、`prompt_ids`、`logits` 與 `eos_ids` 介面。現成實作為 `watermark.text_generative.MLXModel`，針對 Apple silicon；首次使用會下載模型。

**提取與識別**：需要 `tokenizer=`，且須與生成時相同；`verified=True` 表示通過統計門檻，而不是解出一段固定載荷。

**定位**：這是一個「生成時加水印」的模式，不是 AI 生成內容偵測器；它不判斷一段文字是否由 AI 生成，也不宣稱符合 EU AI Act 第 50 條。

**文獻**：Kirchenbauer et al. (2023); Fernandez et al. (2023)。

### 7.6 模式比較

**表 5：五種模式的取捨**

| 模式 | 載體 | 容量 | 不可見性 | 穩健性 | 主要攻擊 | 實作難度 |
|---|---|---|---:|---|---|---|
| `image_fragile` | 圖像 | 高（393,195 bytes @1024²） | 高 | 低 | 壓縮、縮放、裁剪 | 低 |
| `image_robust` | 圖像 | 低（16–27 bytes） | 高 | 中 | 幾何變換、強壓縮、截圖、翻拍 | 高 |
| `text_invisible` | 文字 | 中（64 bytes／682 字元） | 高（但可被檢測） | 中 | 正規化、平台過濾、OCR | 低 |
| `text_lexical` | 英文文字 | 低（7,000 words 樣本 3 bytes） | 高 | 低—中 | 改寫、翻譯、錯詞、漏詞 | 中—高 |
| `text_generative` | 模型生成文字 | 無固定載荷 | 不適用 | 中 | 更換模型／解碼設定 | 高 |

五種模式不是互斥的。實務上可同時使用：以穩健圖像水印承載買家編號，以零寬字元承載交易序號，以選詞水印提供額外的文字線索。這種「多層水印」提高移除成本，但也增加對內容的改動；組合設定留作未來評估，目前不宣稱已測成效。

---

## 文件集說明

- 本頁是中文 README；英文版是 [README.md](README.md)，也是 GitHub 的預設入口。
- 其餘 6 份技術文件由上面的「文件導覽」進入；目前以繁體中文撰寫。
- 文件版本：v0.3（2026-11-17）。實作資料截至 2026-11-16；測試結果與待驗證目標分開列於 [03-testing-and-evaluation.md](docs/03-testing-and-evaluation.md)。
