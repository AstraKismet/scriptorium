# Walkthrough

Runs against `examples/sample.md`, which contains front matter, a fenced code block,
a table, an ordered list, a blockquote, an inline math span, a link, and a template
variable — one of each thing a naive pipeline breaks.

```bash
cd /tmp/demo && cp -r <skill-dir>/config . && cp config/lx.config.json .
cp <skill-dir>/examples/sample.md .
printf 'Celurion\nGo\n' >> config/dnt.txt

LX="python3 -m scriptorium"                 # or `lx` if the package is installed
$LX extract sample.md --lang zh-TW
#   segments 14 | reused 0 | pending 14
```

`todo` emits the work order. Note that no Markdown reaches the model:

```json
{ "id": "s0002", "kind": "para",
  "text": "The **⟦3⟧** server requires ⟦4⟧ 1.22 and a running instance of ⟦1⟧.\nInstall dependencies with ⟦2⟧, then run the migration.",
  "glossary": [{"term": "server", "use": "伺服器"}] }
```

Now feed it a deliberately flawed draft — wrong locale term, dropped placeholder,
mangled bracket style, half-width punctuation, missing spacing, altered version
number:

```json
{"s0002": "**⟦3⟧** 服務器需要 ⟦4⟧ 1.21 以及一個正在執行的 ⟦1⟧ 實例。\n使用 ⟦2⟧ 安裝相依套件,然後執行資料庫遷移。",
 "s0004": "啟動前請設定 ⟦1⟧ 變數。詳情請參閱[參考文件]。",
 "s0008": "HTTP伺服器的監聽連接埠",
 "s0010": "從【1】複製儲存庫。",
 "s0013": "警告:切勿將機密資訊提交至倉庫。"}
```

`apply` silently fixes the four mechanical defects — `,` → `，`, `:` → `：`,
`HTTP伺服器` → `HTTP 伺服器`, `【1】` → `⟦1⟧` — and `check` reports what is left:

```
sample.md [zh-TW]  14/14 translated  6 error(s)  0 warning(s)
  error s0002 glossary  'server' should render as '伺服器'
  error s0002 glossary  forbidden rendering '服務器' for 'server'
  error s0002 lexicon   zh-TW writes this as '伺服器', not '服務器'
  error s0002 numbers   numbers absent from target: ['1.22']
  error s0004 tags      placeholder mismatch lost=['2'] extra=[]
  error s0013 glossary  forbidden rendering '倉庫' for 'repository'
```

Every planted defect caught, exit code 1. Fix only those three segments, re-apply,
and `check` goes green. `render` then reproduces the document:

```markdown
---
title: Deployment Guide
version: 2
---

# 部署指南

**Celurion** 伺服器需要 Go 1.22 以及一個正在執行的 `postgres` 實例。
使用 `go mod download` 安裝相依套件，然後執行資料庫遷移。

| 選項 | 預設值 | 說明 |
| --- | --- | --- |
| `port` | 8080 | HTTP 伺服器的監聽連接埠 |
| `workers` | 4 | 背景工作者數量 |

1. 從 GitHub Actions 複製儲存庫。
2. 執行 `make build` 以產生執行檔。
3. 使用 {{deploy_target}} 管線部署到正式環境。

```python
def hello():
    print("do not translate me")
```

公式 $E = mc^2$ 應保持原樣。
```

Front matter, code block, table alignment row, math, URL, and template variable are
byte-identical to the source — not because the model was careful, but because it
never saw them.

## Incremental update

Add a paragraph to the source and re-extract:

```
sample.md [zh-TW] -> .lx/state.db
  segments 15 | reused 14 | pending 1
```

One segment of work. This is the property that matters on a document that goes
through many revisions.
