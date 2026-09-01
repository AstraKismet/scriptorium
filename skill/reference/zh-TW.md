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
| 軟件 | 軟體 | | 缺省 | 預設 |
| 硬件 | 硬體 | | 賬號 | 帳號 |
| 插件 | 外掛 / 擴充套件 | | 線程 | 執行緒 |
| 屏幕 | 螢幕 | | 緩存 | 快取 |
| 網絡 | 網路 | | 標簽 | 標籤 |
| 服務器 | 伺服器 | | 端口 | 連接埠 |
| 硬盤 | 硬碟 | | 短信 | 簡訊 |
| 打印 | 列印 | | 信息 | 資訊 |
| 視頻 | 影片 | | 鼠標 | 滑鼠 |
| 兼容 | 相容 | | | |

These fail the build. `lx check` exempts the three cases where the string is
inside a longer, correct word — 電視頻道, 老鼠標本, 兼容並蓄 — and nothing else.

## Right form, but the checker can only warn

Chinese is unspaced and the check is a substring match, so these fall out of
ordinary phrases across a word boundary. They are reported at `warn`, which never
stops a build. Read the phrase before acting on one.

| Avoid | Prefer | Also matches |
|---|---|---|
| 內存 | 記憶體 | 體內存在、國內存款 |
| 激活 | 啟用 | 刺激活化、感激活動主辦單位 |
| 集成 | 整合 | 收集成果、募集成功 |
| 調試 | 除錯 | 強調試用期、協調試驗 |
| 數組 | 陣列 | 參數組合、多數組織 |
| 變量 | 變數 | 改變量、不變量 |
| 帶寬 | 頻寬 | 皮帶寬度、地帶寬廣 |
| 復用 | 重複使用 | 恢復用電、修復用具 |

## Contextual, not automatic

Each of these is *correct* Taiwanese usage in the sense on the left, so no
validator can decide them — choosing takes the sentence, which is judgement, and
[invariant 4](../../AGENTS.md) keeps judgement out of `checks.py`. They were
removed from the lexicon table on 2026-07-28 because the table was failing
correct Traditional Chinese. This section and the model's language brief are
where they live now, so read it rather than trusting a green `lx check` on them.

| Term | Correct in Taiwan when it means | Otherwise write |
|---|---|---|
| 程序 | 法律程序、議事程序 | 程式 (software) |
| 數據 | 統計數據、實驗數據 (readings) | 資料 (data) |
| 質量 | 物體的質量 (mass) | 品質 (quality) |
| 支持 | 支持這項提案 (endorsement) | 支援 (technical support) |
| 文本 | 文本分析 (a text under analysis) | 文字 (plain text) |
| 對象 | 研究對象、交往對象 | 物件 (an OOP object) |
| 函數 | 三角函數 (mathematics) | 函式 (a function in code) |
| 指針 | 時鐘的指針、羅盤指針 | 指標 (a pointer) |
| 進程 | 歷史進程、和平進程 | 行程 (an OS process) |
| 登錄 | 戶籍登錄、登錄有案 | 登入 (to log in) |
| 交互 | 交互作用、交互驗證 | 互動 (interaction) |
| 隊列 | 隊列訓練 (formation) | 佇列 (a queue) |
| 菜單 | 餐廳的菜單 | 選單 (a UI menu) |
| 默認 | 默認、默許 (to acquiesce) | 預設 (a default) |
| 音頻 | 音頻放大器 (audio frequency) | 音訊 (audio) |
| 智能 | 智能障礙、智能不足 | 智慧 / 智慧型 (intelligent) |
| 視圖 | 正視圖、俯視圖、透視圖 | 檢視 (a view) |
| 用戶 | 用戶端、電信用戶 | 使用者 (a person) |

A project whose documents never use the left-hand sense can put any of these back
mechanically, at whatever severity it wants, under `lexicon_extra` in
`lx.config.json` — `{"數據": ["資料", "error"]}`. That is the right place for it:
the judgement is the project's, not the pipeline's.

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

## Register: technical documentation

This section is one register, not the language. A document extracted with
`--tone literary` is briefed for narrative prose instead, and several rules below
reverse there — see `lx todo`'s `tone` field for which register a document is in.

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

**Which numeral system a figure takes depends on the register.** In technical
documentation, Arabic numerals throughout, and keep the source decimal separator:
a version, a port, a status code, a dosage or a configuration value is a string
the reader types back, and 五百 for `500` makes it unusable. In narrative prose
the opposite is idiomatic — 第一章, 三天, 一九八四年 are how a book written in
Chinese says those things, and 第 1 章 reads as a translation.

The `numbers` rule fails the build when a figure goes missing, which is the most
common silent defect in a translated table. Since 2026-09-02 it reads Chinese
numerals as well as Arabic ones, so a figure correctly spelled out satisfies it.
What it cannot see is a figure rendered in the *wrong* system for its register:
`HTTP 500` written 五百 passes. The paragraph above is therefore a rule for the
writer rather than a claim about what the checker enforces.
