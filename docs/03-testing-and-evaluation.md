# 測試與評估

> 本文件收錄原 README 第 9 節，並依 2026-11-16 的實際測試結果更新。**已測結果與設計目標分開列示；未量測的項目不會寫成已達成。**

> 文件導覽：[中文 README](../README.zh.md) · [English README](../README.md) · [背景](01-background.md) · [架構與 API](02-architecture-and-api.md) · [測試](03-testing-and-evaluation.md) · [專案與課程](04-project-and-course.md) · [法律與附錄](05-legal-ethics-and-appendix.md) · [版本](06-version-history.md)

---

## 9. 測試與評估

### 9.1 目前已測範圍

2026-11-16，在 Python 3.12.13 的本機環境執行：

```bash
env -u RUN_DEEPSEEK -u WATERMARK_LLM uv run --locked pytest -q
```

結果：**84 passed, 2 skipped，131.83 秒**。

兩個 skipped 分別是：

1. 需要外部 DeepSeek API 的 `text_lexical` 測試（未設定 `RUN_DEEPSEEK`）。
2. 需要真實 MLX 模型的 `text_generative` 測試（未設定 `WATERMARK_LLM`）。

生成模式的測試使用 fake model，驗證 API、載荷流程與統計判斷。當時的 `text_lexical` 實驗曾以兩款字體渲染同一份英文樣本，經 Tesseract OCR 後成功提取水印。這項實驗已從目前的測試套件移除。

測試模組與實際覆蓋範圍：

| 測試檔 | 主要內容 |
|---|---|
| `tests/test_api.py` | `embed`／`extract`／`identify` 的介面、模式分派、回傳型別 |
| `tests/test_payload.py` | `standard`／`compact` 格式、認證失敗、容量與 profile |
| `tests/test_image_fragile.py` | LSB 嵌入／提取、alpha 保留、容量邊界 |
| `tests/test_image_robust.py` | 合成圖的 JPEG、縮放、旋轉、裁切、截圖與模擬翻拍 |
| `tests/test_text_invisible.py` | U+200B／U+2060／U+2063 編碼、複製貼上、正規化 |
| `tests/test_text_lexical.py` | 英文候選替換、key-only、容量樣本 |
| `tests/test_text_generative.py` | fake model 的生成、提取、識別與 tokenizer 不一致 |

**尚未量測**：整體測試覆蓋率（`pytest-cov`）、大規模 PSNR／SSIM、BER 統計、top-1 準確率、false positive rate。這些項目列在 9.5，狀態為「待驗證」。

### 9.2 攻擊矩陣

**表 6：圖像攻擊**

| 攻擊 | 參數 | 適用模式 | 目前狀態 | 指標 |
|---|---|---|---|---|
| JPEG 壓縮 | q=30／50 | `image_robust` | 已納入測試（合成圖） | 提取成敗、BER（未統計） |
| 縮放 | 0.5× | `image_robust` | 已納入測試（合成圖） | 提取成敗 |
| 旋轉 | 7° | `image_robust` | 已納入測試（合成圖） | 提取成敗 |
| 裁切 | 部分裁切 | `image_robust` | 已納入測試（合成圖） | 提取成敗 |
| 截圖 | 模擬截圖 | `image_robust` | 已納入測試（合成圖） | 提取成敗 |
| 手機翻拍 | 模擬翻拍 | `image_robust` | 已納入測試（合成圖） | 提取成敗 |
| 無損複製 | PNG 原檔 | `image_fragile` | 已測 | 位元完全還原 |
| JPEG／縮放／裁切 | — | `image_fragile` | 預期失敗（設計如此） | 提取失敗 |

表中的「已納入測試」表示測試套件有對應案例，不代表所有圖像、所有參數都成功。正式報告應以同一腳本輸出每張圖、每個參數的結果，而不是只寫「通過」。

**表 7：文字攻擊**

| 攻擊 | 適用模式 | 目前狀態 | 指標 |
|---|---|---|---|
| 複製貼上 | `text_invisible`、`text_lexical` | 已測（單元） | 提取成功率 |
| NFC／NFKC 正規化 | `text_invisible` | 已測；示例仍可提取 | 提取成功率 |
| 零寬字元過濾／平台清洗 | `text_invisible` | 未做大規模平台測試 | 失敗時應回報原因 |
| OCR（英文圖片） | `text_lexical` | 早期實驗測過同一樣本的兩款字體 | 提取成敗 |
| 人手重新輸入 | `text_lexical` | 未測 | top-1 準確率 |
| 改寫／翻譯／統一用詞 | `text_lexical` | 未測 | top-1 準確率 |

`text_invisible` 的穩健性邊界應如實描述：複製貼上通常保留；OCR、重新打字、平台過濾會移除。NFC／NFKC 正規化本身不一定移除，但不同平台可能有額外清洗，不能宣稱「所有正規化都安全」。

### 9.3 評估指標

| 指標 | 定義 | 方向 | 用途 |
|---|---|---|---|
| PSNR | `10 log10(MAX² / MSE)` | 越高越好 | 圖像失真；目標 ≥ 40 dB |
| SSIM | 結構相似度（Wang et al., 2004） | 越高越好 | 圖像感知品質；目標 ≥ 0.98 |
| BER | 錯誤位元／總位元 | 越低越好 | 穩健模式；目標 ≤ 5% |
| NC | 正規化相關 | 越接近 1 越好 | 與原始載荷的相似度 |
| Top-1 | 正確候選排第一的比例 | 越高越好 | `identify`；目標 ≥ 90% |
| FPR | null keys 被誤判為有效的比例 | 越低越好 | 目標 < 1% |
| Coverage | 測試覆蓋率 | 越高越好 | 目標 ≥ 80% |

`ExtractResult.score` 不是上述指標的統一換算，也不是「屬於某人的機率」。它是模式內部的品質／證據指標：`image_fragile`／`text_invisible` 成功為 1；`text_lexical` 反映糾錯餘量；`image_robust` 反映解碼一致度；key-only／生成模式用統計證據。文件與簡報不得把 `score` 寫成準確率或定罪機率。

### 9.4 實驗設計

目前已做的：

- **圖像**：以合成圖為主，加入 JPEG q=30／50、0.5× 縮放、7° 旋轉、部分裁切、模擬截圖與模擬翻拍。測試目的是找出失敗邊界，不是產生漂亮的平均數。
- **文字**：以團隊自行撰寫的英文段落與兩款字體的圖片樣本為主；`text_lexical` 的 7,000／3,000／2,000 words 樣本用於容量與 key-only 的行為，不是通用容量保證。
- **候選密鑰**：`identify` 測試使用多個候選，檢查 `match`／`none`／`ambiguous` 與 `results` 的順序。
- **環境**：Python 3.12.13、`uv run --locked`；外部 API 與真實 MLX 模型在預設測試中跳過，避免把網絡或模型下載當成測試前提。

尚未做的：

- 30 張 CC0 圖像、10 份文件掃描、20 份中文 + 10 份英文文本的統計實驗；這是原規劃，尚未執行。
- 1 個正確密鑰 + 9 個干擾密鑰、重複 100 次的 top-1 與 FPR 量測。
- 真實外洩樣本或用戶研究；目前沒有這類資料，因此不宣稱真實場域的成功率。
- 手機翻拍與列印—掃描的大規模測試；目前只有模擬案例。

### 9.5 驗收標準

**表 8：v1 驗收標準（已測 vs 待驗證）**

| 項目 | 目標 | 量測方式 | 狀態 |
|---|---|---|---|
| 單元／整合測試 | 全部通過 | `pytest -q` | 已測：84 passed、2 skipped（2026-11-16） |
| 本地安裝 | `python -m pip install .` 成功 | 乾淨虛擬環境 | 已測（本機） |
| PyPI 安裝 | `pip install tracemark` 成功 | 公開發布後 | 待完成（尚未發布） |
| JupyterHub 安裝 | 無依賴衝突並可 demo | 課程環境 | 待完成 |
| `image_fragile` 無損 round-trip | 位元完全還原 | 單元測試 | 已測 |
| `image_robust` JPEG q=30／50 | 可提取 | 合成圖測試 | 已納入；成功率未統計 |
| `image_robust` 0.5×／7°／裁切／截圖／翻拍 | 可提取 | 合成圖測試 | 已納入；成功率未統計 |
| `text_invisible` 64 bytes | 682 個不可見字元內可提取 | 單元測試 | 已測 |
| `text_lexical` 7,000 words → 3 bytes | 可嵌入並提取 | 樣本測試 | 已測樣本 |
| `text_lexical` key-only 2,000 words | 可辨識 | 樣本測試 | 已測樣本 |
| `text_generative` fake model | API 與統計流程正確 | 單元測試 | 已測；真實模型待測 |
| PSNR ≥ 40 dB | 圖像品質 | 原圖 vs 標記圖 | 待量測 |
| SSIM ≥ 0.98 | 圖像品質 | 原圖 vs 標記圖 | 待量測 |
| BER ≤ 5% | 穩健模式 | 提取位元 | 待量測 |
| Top-1 ≥ 90% | `identify` | 10 候選、100 次 | 待量測 |
| FPR < 1% | null keys | 1000 次 null 試驗 | 待量測 |
| Coverage ≥ 80% | 測試覆蓋率 | `pytest-cov` | 待量測 |
| CLI | 可用指令 | 終端實測 | 尚未提供 |

### 9.6 效度限制

- **合成圖偏誤**：合成圖的紋理與真實筆記頁面不同；真實掃描、手機翻拍、補習社排版可能更難。
- **OCR 樣本偏誤**：兩款字體與 Tesseract 的結果不能推廣到所有字體、語言與解析度。
- **小樣本**：容量樣本只有特定英文段落，不能當作通用容量公式。
- **無真實外洩資料**：沒有真實買家、真實轉售或真實法律案例；所有「追溯」描述都是設計目標與技術能力，不是已證實的場域成效。
- **未量測 coverage**：因此不能說「測試覆蓋率達 80%」。
- **外部依賴**：DeepSeek API 與 MLX 模型在預設測試中跳過；它們的行為可能隨版本、網絡與模型變化。

這些限制應與結果一併閱讀，而不是放在附錄後被忽略。
