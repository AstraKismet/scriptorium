# Traditional Chinese (zh-TW) style playbook

Read this before translating into zh-TW. Most defects in Chinese output are not
mistranslations — they are register and convention errors that a fluent reader in
Taiwan notices in the first sentence.

## The single biggest risk

Training data for Chinese is dominated by conventions other than Taiwan's, so a
model's default technical vocabulary usually is not the one zh-TW documentation
uses. Converting characters is not localizing: `軟件` written in traditional
characters is still not the form a reader in Taiwan expects. The `lexicon` rule
in `lx check` catches common cases mechanically, but it only knows the words in
its table. When a term feels borrowed, check it against the pairs below and
against how Taiwanese vendor documentation writes it.

Neither column is wrong in itself — each is standard where it comes from. The
left column is simply not the form this target locale uses.

| Avoid | Prefer | | Avoid | Prefer |
|---|---|---|---|---|
| 軟件 | 軟體 | | 數組 | 陣列 |
| 硬件 | 硬體 | | 對象 | 物件 |
| 插件 | 外掛 / 擴充套件 | | 函數 | 函式 |
| 視頻 | 影片 | | 變量 | 變數 |
| 音頻 | 音訊 | | 指針 | 指標 |
| 屏幕 | 螢幕 | | 線程 | 執行緒 |
| 網絡 | 網路 | | 進程 | 行程 |
| 服務器 | 伺服器 | | 緩存 | 快取 |
| 內存 | 記憶體 | | 隊列 | 佇列 |
| 硬盤 | 硬碟 | | 帶寬 | 頻寬 |
| 數據 | 資料 | | 端口 | 連接埠 |
| 信息 | 資訊 | | 調試 | 除錯 |
| 默認 / 缺省 | 預設 | | 兼容 | 相容 |
| 鼠標 | 滑鼠 | | 集成 | 整合 |
| 菜單 | 選單 | | 交互 | 互動 |
| 打印 | 列印 | | 激活 | 啟用 |
| 登錄 | 登入 | | 標簽 | 標籤 |
| 賬號 | 帳號 | | 短信 | 簡訊 |

Contextual, not automatic: `質量` is correct in physics but should be `品質` for
quality; `程序` is fine for a legal or operational procedure but should be `程式`
for software; `支持` works for endorsement but `支援` for technical support;
`用戶` is understood but `使用者` reads better in product copy.

## Punctuation

Full-width inside Chinese runs: `，。！？；：、`, with `「」` for quotation and
`『』` nested. Use `（）` for parentheses containing Chinese. Ellipsis is `……`
(two characters); dash is `——`.

Keep half-width punctuation inside code, URLs, version numbers, and quoted English.
Since those arrive masked as `⟦n⟧`, this happens for free.

`lx apply` normalizes width and strips spaces around full-width marks
automatically. Do not spend drafting effort on it.

## Spacing

Put a space between Chinese characters and adjacent Latin letters or digits:
`使用 Go 1.22 建置`, not `使用Go1.22建置`. No space between Chinese and full-width
punctuation. `lx` applies this at both `apply` and `render` time, including across
restored placeholders.

## Register

Technical documentation in Taiwan reads as neutral-formal. Concretely:

- Use `請` for user instructions rather than bare imperatives, and do not repeat
  `您` in every sentence — once near the top, then drop the pronoun entirely.
- Prefer dropping the subject over translating every English "you" and "we".
- More than two `的` in one clause means the sentence needs restructuring.
- Passive `被` is far rarer than English passive; convert to active or
  topic-comment word order.
- Nominalize headings instead of translating them as sentences
  (`Getting Started` → `快速開始`, not `如何開始使用`).

## Terms usually left in English

Product names, protocol names, language and framework names, CLI flags, HTTP verbs,
file extensions, and acronyms with no settled Chinese form (`API`, `SDK`, `CI/CD`,
`OAuth`). Put these in `config/dnt.txt` so they are masked before the model sees
them — a protected term cannot drift.

For a term with a settled Chinese form that should still be bilingual on first use,
write `快取（cache）` and add a glossary row so the rendering stays consistent.

## Numbers and units

Arabic numerals throughout; never convert to Chinese numerals. Keep the source
decimal separator. The `numbers` rule fails the build when a figure goes missing,
which is the most common silent defect in translated tables.
