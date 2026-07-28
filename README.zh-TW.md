# Scriptorium

[![CI](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[English](README.md) · **繁體中文**

出版級的文件在地化工具。模型只翻句子，其餘全部由程式決定性處理。

## 它做什麼

指定一份文件，它會跑四個步驟。

1. **切分與遮罩。** 文件被解析成可翻譯的 segment，所有標記——程式碼片段、
   URL、連結目標、表格豎線——都替換成 `⟦n⟧` placeholder。交給模型的只有散文，
   沒有任何標記可供它重排、翻譯或弄丟。
2. **只翻新的部分。** 翻譯記憶裡已有的 segment 直接重用；其餘交給設定好的模型、
   透過 `lx todo` / `lx apply` 交給 agent，或在審校工作台交給人。三種來源地位相同，
   每個 segment 都會記錄自己是由哪一種產生的。
3. **機械化檢查。** placeholder 遺失或重複、數字變動、術語違規、尚未翻譯的段落。
   只要有任何一項不過，`lx check` 就以非零狀態結束——「這份完成了嗎」有 exit code
   可回答，不必靠主觀判斷。
4. **以代入方式輸出。** 譯文被放回原文件的骨架。目標檔案永遠不是從模型輸出重建的，
   只是把空格填回去——因此管線沒有刻意更動的每一個位元組，都原樣重現。

Segment 以內容而非位置為索引鍵，所以修改原文時只會重譯真正變動的部分，
已核可的內容全部從記憶回來。你審過的東西不會付第二次代價。

結構性的工作——解析標記、保護程式碼片段、重組文件、強制術語一致、正規化標點——
都是決定性的，全部寫在 Python 裡。把這些交給語言模型在腦中處理，正是翻譯管線出事的地方：
每個節點 99.5% 的正確率，一份 500 節點的文件只有 8% 的機率毫髮無傷，
而且失敗的方式都是看不見的那一種。

無編譯型依賴。可搭配任何 OpenAI 相容端點，包含完全在本機執行的模型。

## 現況

**目前可用**（Markdown）：抽取、翻譯、驗證、修復、還原，以及一份能跨版本存活的翻譯記憶。
原文文件的逐位元組重組由一組 27 份輸入的對抗性語料庫在 CI 上把關，Linux 與 Windows 都跑——
但有一個量測到的例外：CLI 目前仍以 Python 的文字模式讀寫檔案，換行符號會在檔案邊界被正規化。
那是下一個要修的東西。

**建置中**（依序）：圍堵驗證器、有型別的 placeholder、SQLite 狀態層、
重建的審校工作台，然後是 EPUB 與純文字。

**刻意排除**：DOCX、i18n 檔案格式，以及任何需要系統 web view 的東西。
`docs/decisions.md` 記錄了每一項的理由，以及輸掉的替代方案。

## 安裝

```bash
git clone https://github.com/AstraKismet/scriptorium.git
cd scriptorium
pip install -e .          # 選用；安裝後可使用 `lx` 指令
```

不安裝也能用，把 `lx` 換成 `python -m scriptorium` 即可。

## 快速開始

```bash
lx init                                   # 建立設定範本與狀態目錄
lx run docs/guide.md --lang zh-TW         # 跑完整條管線
lx web                                    # 檢視結果
```

`lx run` 會抽取 segment、重用翻譯記憶裡已有的內容、翻譯其餘部分、驗證、
修復失敗的段落，最後寫出目標檔案——**只要還有 error 就拒絕輸出**。

## 後端

模型後端宣告在 `lx.config.json`。送往 OpenAI 相容端點的請求刻意保持樸素——
沒有 `response_format`、沒有 tools、沒有 streaming，除非你主動開啟——
因為自架的推論執行環境是**拒絕**未知欄位，而不是忽略它們。

```json
"providers": {
  "local":    { "kind": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b-instruct", "api_key_env": "", "timeout": 300 },
  "lmstudio": { "kind": "openai", "base_url": "http://localhost:1234/v1",  "model": "local-model",         "api_key_env": "" },
  "openai":   { "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",         "api_key_env": "OPENAI_API_KEY" },
  "claude":   { "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY" }
},
"routing": { "draft": "local", "polish": "claude", "repair": "claude" }
```

| 執行環境 | `base_url` |
|---|---|
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp server | `http://localhost:8080/v1` |
| vLLM | `http://localhost:8000/v1` |
| LiteLLM proxy | `http://localhost:4000/v1` |

`lx providers` 會列出已設定的後端，以及每個金鑰是否存在。

API 金鑰只從 `api_key_env` 指名的環境變數讀取，**絕不寫入設定檔、狀態或日誌**。
本機伺服器通常根本不需要金鑰——把 `api_key_env` 留空，就不會送出 `Authorization` 標頭。

routing 讓大量的初譯走便宜或本機的模型，把強模型留給 polish 與 repair 這兩個
就設計而言只處理小批次的階段。

## 指令

| | |
|---|---|
| `lx init` | 建立設定與狀態骨架 |
| `lx extract SRC --lang L` | 解析成 segment、遮罩標記、重用翻譯記憶 |
| `lx todo SRC --lang L` | 以 JSON 吐出待譯 segment，供 agent 翻譯 |
| `lx apply SRC --lang L --file F` | 收回譯文，自動正規化 |
| `lx translate SRC --lang L` | 用設定好的模型翻譯（`--mode draft\|polish\|repair`） |
| `lx check SRC --lang L` | 驗證；有 error 時離開碼為 1 |
| `lx repair SRC --lang L` | 只重譯失敗的 segment |
| `lx run SRC --lang L` | 整個迴圈；加 `--polish` 多跑一次流暢度潤稿 |
| `lx render SRC --lang L -o OUT` | 重建目標文件 |
| `lx commit SRC --lang L` | 把核可的譯法存進翻譯記憶 |
| `lx web` | 本機審校工作台 |
| `lx providers` / `lx stats` | 後端 / 覆蓋率 |

## 檢查哪些東西

`tags`（placeholder 完整性）、`glossary`（約定術語與禁用譯法）、
`numbers`（數字被吞掉或憑空出現）、`lexicon`（用詞與目標語言的慣用形式不符）、
`dnt`、`untranslated`、`punct`、`spacing`、`length`、`missing`。

`lexicon` 是一份逐語言的用詞偏好表：它把一個詞與該語言自身技術文件慣用的形式配成對，
再標出差異。它對另一種形式不作評價——在它自己的行文慣例裡那是正確的——
規則只有一條：同一份文件不應該混用兩種。

標點寬度與中英文間距是**在收回譯文時直接修正**，而不是回報——
最便宜的缺陷是那個根本不可能被引入的缺陷。

**關於結構保真，說實話。** `render` 從原始骨架重建，只代入譯文片段，
所以 segment **周圍**的 front matter、圍籬程式碼區塊、表格對齊與數學式，
是由建構本身保住的。骨架**尚未**保證的是**代入譯文之後**的文件結構：
譯文若以行首的 `1. ` 開頭、或在表格儲存格內含 `|`，就會改變區塊結構，
而且目前會通過驗證。三個圍堵驗證器是下一項正確性工作，理由記在 `docs/decisions.md`。
在它們落地之前，請把綠燈的 `lx check` 當成**必要但不充分**。

## 增量翻譯

segment 的身分是內容雜湊，不是位置。改動一份 400 個 segment 的文件裡的一段，
`extract` 會回報 `reused 399 | pending 1`。核可過的譯法不會在改版之間漂移，
搬動章節也不花任何成本。

`.lx/tm.<lang>.jsonl` 是值得進版控的資產，`.lx/` 其餘部分都可再生。

## 工作台

```bash
lx web        # http://localhost:8787
```

原文與譯文並排，placeholder 高亮，失敗以旁註形式顯示在造成它的 segment 旁。
翻譯、潤稿、修復、檢查、預覽、提交都在工具列上；編輯在失焦時儲存，
並在收回途中正規化，包含修復被模型弄壞的 placeholder 括號。

它只綁定 loopback，而且是 CLI 所呼叫的同一批函式的外殼——**沒有第二套實作**。
把它開在其他介面上會印出警告，因為它可以透過設定好的後端花錢。

## 從 agent 驅動

`skill/` 把這個專案打包成 Claude Skill。`adapters/` 有給 Claude Code 與 Codex 的
`AGENTS.md` 片段，以及給 OpenCode 的規則檔。三者都是指向同一個 CLI 的薄指標，
所以修好一個驗證器，所有地方同時生效。

agent 可以在**完全沒有設定任何模型**的情況下驅動整條管線：`lx todo` 吐出工作、
agent 在自己的 context 裡翻譯、`lx apply` 收回。這是一等路徑而非退路——
翻譯、校閱、審計可以各自交給不同的 agent。

## CI

```yaml
- run: lx extract docs/guide.md --lang zh-TW
- run: lx check   docs/guide.md --lang zh-TW
```

先重新 extract 才能讓「原文被改過」浮現成待譯工作，而不是靜靜地通過；
接著 check 會讓 pull request 失敗。

## 開發

```bash
python -m pytest -q          # 59 passed；不碰網路
python -m ruff check src tests
```

`AGENTS.md` 記載架構不變量，是權威的工作約定——`CLAUDE.md` 只是指向它的一行。
動任何結構性的東西之前先讀它。`docs/decisions.md` 記錄了每條不變量為何是現在這個寫法。

## 授權

MIT——見 [LICENSE](LICENSE)。
