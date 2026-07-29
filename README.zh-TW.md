# Scriptorium

[![CI](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[English](README.md) · **繁體中文**

翻譯文件，全程不讓模型碰到任何標記語法。

Scriptorium 是一套 Markdown 在地化的命令列工具。它把文件切成句子，把程式碼、連結、
標籤和受保護的術語都遮成 `⟦n⟧` placeholder，翻完再逐位元組填回原本的檔案。
區塊語法根本不會送出去。模型只負責翻文句本身，那才是它真正擅長的事。

純 Python，**沒有任何執行期相依套件**，也沒有編譯型擴充。裸的直譯器、CI、
agent sandbox、受限制的機器，都跑得起來。

## 實際長什麼樣

拿這份原文來看：

```markdown
# Deployment Guide

The **Celurion** server requires Go 1.22 and a running instance of `postgres`.

| Option | Default | Description |
| --- | --- | --- |
| `port` | 8080 | Listening port for the HTTP server |

> Warning: never commit secrets to the repository.
```

執行 `lx run guide.md --lang zh-TW`，會得到：

```markdown
# 部署指南

**Celurion** 伺服器需要 Go 1.22，以及一個執行中的 `postgres` 實例。

| 選項 | 預設值 | 說明 |
| --- | --- | --- |
| `port` | 8080 | HTTP 伺服器的監聽連接埠 |

> 警告：絕對不要把機密資訊提交到儲存庫。
```

只要 `config/dnt.txt` 裡列了 `Celurion` 和 `Go`，那一段真正送進模型的就只有這一行：

```
The **⟦2⟧** server requires ⟦3⟧ 1.22 and a running instance of ⟦1⟧.
```

placeholder 對模型來說是看不懂內容的符號：它搬得動，卻翻不了、丟不掉，也複製不出
第二個，事後由程式把原值填回去。標題的 `#`、表格的對齊列、引用區塊的 `>`，
還有放著 `port` 和 `8080` 的那兩格，從頭到尾都沒有進過任何 segment；
它們留在骨架裡，由程式逐位元組原樣抄回。

## 快速開始

尚未發布到 PyPI，請從原始碼安裝。

```bash
git clone https://github.com/AstraKismet/scriptorium.git
cd scriptorium
pip install -e .            # 選用，裝了就有 `lx` 指令
```

不安裝也可以用，把 `lx` 換成 `python -m scriptorium` 即可。

```bash
lx init                             # 建立設定範本與狀態目錄
lx run docs/guide.md --lang zh-TW   # 跑完整條流程
lx web                              # 檢視結果
```

`lx run` 需要一個模型後端，而預設值指向 `localhost:11434` 上的 Ollama，
詳見[模型後端](#模型後端)。如果手邊完全沒有後端，可以走 agent 路線：先 `lx extract`，
再用 `lx todo` 把待譯的 segment 以 JSON 吐出來，自己翻完之後用 `lx apply` 收回去。

`examples/walkthrough.md` 有一份完整走過一遍的範例。

## 運作方式

**一、切分與遮罩。** 文件會拆成一份可翻譯的 segment 清單，其餘的一切都留在骨架裡。
程式碼片段、數學式、URL、連結與參照目標、註腳、HTML 標籤、HTML 實體、模板變數，
還有 `config/dnt.txt` 裡的每一個術語，都換成 `⟦n⟧` placeholder。區塊語法——標題、
清單符號、引用區塊的標記、表格的 `|`——雖然不遮罩，但從來沒有離開過骨架，
所以模型一樣看不到。要支援新的行內語法，做法是在 `mask.py` 裡加一條 pattern，
而不是在 prompt 裡多寫一句話。

**二、只翻新的。** 翻譯記憶裡已經有的 segment 會拿出來重用，但不是直接寫進去：
記憶給的譯文要過與模型輸出同一道關卡，placeholder 對得上才收，對不上就退回待翻。
其餘的交給設定好的模型、透過 `lx todo` / `lx apply` 交給 agent，或在審校工作台交給人。
三種來源地位相同，每個 segment 都記著自己是由哪一種產生的。segment 的識別依據是內容
加上所在的區塊種類，而不是位置；改動一份有 400 個 segment 的文件裡的一段，
工具回報的是 `reused 399 | pending 1`，整節搬動則一段都不用重翻。

**三、機械化檢查。** 會抓出這些狀況：placeholder 不見了、數字被吞掉、術語走鐘、
譯文把結構弄壞。只要有 error，`lx check` 就以 1 結束，讓建置流程可以拿它當關卡。

**四、以填回的方式輸出。** 譯文填回原本的骨架，絕不從語法樹重新序列化。正因為如此，
front matter、圍欄式程式碼區塊、表格對齊、縮排、換行字元才能逐位元組活下來。
CI 上有一組語料庫在把關，裡面收了 28 份刻意刁難的輸入，Linux 與 Windows 都跑，
從磁碟上的位元組一路驗到寫回去的位元組。

每個 segment 的誤差會一路累積，所以第一步和第四步寫成程式，而不是寫成給模型的指示。
就算每個 segment 的正確率有 99.5%，一份 500 個 segment 的文件也只有 8% 的機率
毫髮無傷；而少一根表格分隔線、壞掉一個連結，正好就是撐得過審校的那種損傷。

`lx check` 通過只代表兩件事：結構沒被破壞，機械規則也都過了。它不保證譯文品質好，
那是審校的工作。

## 指令

| 指令 | 用途 |
|---|---|
| `lx init` | 建立設定與狀態骨架 |
| `lx extract SRC --lang L` | 解析成 segment、遮罩標記、重用翻譯記憶 |
| `lx todo SRC --lang L` | 以 JSON 吐出待譯 segment，供 agent 翻譯 |
| `lx apply SRC --lang L --file F` | 收回譯文，自動正規化 |
| `lx translate SRC --lang L` | 用設定好的模型翻譯（`--mode draft\|polish\|repair`） |
| `lx check SRC --lang L` | 驗證；有 error 時以 1 結束（`--json` 可拿到完整報告） |
| `lx repair SRC --lang L` | 只重譯失敗的 segment |
| `lx run SRC --lang L` | 跑完整條流程；加 `--polish` 會多跑一次流暢度潤稿 |
| `lx render SRC --lang L -o OUT` | 重建目標文件 |
| `lx commit SRC --lang L` | 把核可的譯法存進翻譯記憶 |
| `lx web` | 本機審校工作台 |
| `lx providers` / `lx stats` | 後端 / 覆蓋率 |

`translate`、`repair`、`run` 都吃 `--dry-run`，只回報會做哪些工作，不會真的呼叫模型。

## 驗證規則

| 規則 | 嚴重度 | 抓什麼 |
|---|---|---|
| `tags` | error | placeholder 遺失、重複或憑空出現；成對的 placeholder 顛倒或交叉 |
| `containment` | error | 譯文開了一個原文沒有的區塊；表格被多加一欄 |
| `eol` | error | 原文沒有、譯文卻多出來的歸位字元 |
| `numbers` | error | 原文裡的數字在譯文中不見了 |
| `missing` | error | segment 從未翻譯 |
| `escaping` | error | XML 類語法裡未跳脫的 `<`、`&` 或 `]]>`——目前空轉，等 EPUB 進來才生效 |
| `glossary` | 逐列設定，`forbidden` 一律 error | 約定術語譯法不一致，或用了禁用的譯法 |
| `lexicon` | error / warn | 用詞與目標語言的慣用形式不符 |
| `dnt` | warn | 受保護的品牌或產品名稱被改動 |
| `untranslated` | warn | 原文整段照抄 |
| `punct` / `spacing` | warn | 無法自動修好的標點寬度與中英文交界問題 |
| `length` | warn | 長度比預期短得多或長得多 |

每個專案都可以自行關掉任何一條，寫成 `"checks_disabled": ["length"]` 即可。

這裡的每一條規則，程式都能自己決定，不需要人的判斷；凡是要靠人判斷的，
都寫在語言 brief 或交給審校。`docs/decisions.md` 記著這張表的入場條件，
以及當初被剔除的十八個詞。

標點寬度與中英文間距在收回譯文時就由 `normalize.py` 直接修好，不另外回報。

## 目標語言

| 語言 | 語言 brief | 正規化 | 用詞表 |
|---|---|---|---|
| `zh-TW` | 有 | 標點寬度、中英文間距、空白 | 有 |
| `ja` | 有 | — | — |

空白這一項會清掉編輯器留下的連續空格，但行尾兩個以上的空格會留著：那在 Markdown 裡
是一個換行，清掉等於把譯者刻意分開的兩行併回一行。

其他 `--lang` 值一樣可以跑，結構檢查照常生效，只是沒有該語言專屬的指引和術語規則。
要新增一個語言，需要在 `translate.py` 加一份 brief、在 `config.py` 加一組正規化設定，
再到 `skill/reference/` 放一份參考檔。

## 模型後端

模型後端在 `lx.config.json` 裡宣告。`lx init` 會把每個階段都指向 `local`；
等你手上有付費金鑰之後，常見的分法是讓大量初譯走便宜或本機的模型，
把強模型留給 polish 和 repair——這兩個階段本來就只處理小批次。

```json
"providers": {
  "local":    { "kind": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b-instruct", "api_key_env": "", "timeout": 300 },
  "lmstudio": { "kind": "openai", "base_url": "http://localhost:1234/v1",  "model": "local-model",         "api_key_env": "" },
  "openai":   { "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",         "api_key_env": "OPENAI_API_KEY" },
  "claude":   { "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY" }
},
"routing": { "draft": "local", "polish": "claude", "repair": "claude" }
```

任何 OpenAI 相容端點都可以，包含完全跑在本機的：

| 執行環境 | `base_url` |
|---|---|
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp server | `http://localhost:8080/v1` |
| vLLM | `http://localhost:8000/v1` |
| LiteLLM proxy | `http://localhost:4000/v1` |

請求本身刻意保持單純：沒有 `response_format`、沒有 tools，也沒有 streaming——
除非你主動開啟。原因是自架的推論環境傾向於**拒絕**未知欄位，而不是忽略它們。

API 金鑰只從 `api_key_env` 指定的環境變數讀取，絕不寫進設定檔、狀態或記錄檔。
本機伺服器通常不需要金鑰，把 `api_key_env` 留空就不會送出 `Authorization` 標頭。
`lx providers` 會列出設定了哪些後端，以及每個金鑰在不在。

## 翻譯記憶

`.lx/tm.<lang>.jsonl` 只增不改，也是唯一值得納入版本控制的檔案。

`.lx/docs/` 是工作狀態。只有已經用 `lx commit` 存進記憶的譯法才重新產生得出來，
所以清掉它之前記得先 commit。`.lx/reports/` 則隨時都可以再生。

## 審校工作台

```bash
lx web        # http://127.0.0.1:8787
```

原文與譯文並排，placeholder 高亮，驗證失敗會以旁註標在對應的 segment 邊上。
翻譯、潤稿、修復、檢查、預覽、提交都在工具列上。編輯欄位失去焦點就自動存檔，
內容在收回時會先正規化，包含把模型弄壞的 placeholder 括號修回來。

它只是一層外殼，底下呼叫的和 CLI 是同一批函式，不存在第二套會走鐘的實作。

它綁在 loopback 上，但光是這樣從來就不夠：瀏覽器裡的任何一個頁面都能往本機的
埠送 POST。所以它收到的每一個路徑——來源檔和輸出路徑——都會被限制在啟動時所在的
目錄裡，而且它只回應自己送出去的那個頁面發來的請求。`curl` 和 `lx` 不受影響，
因為兩者都不送 `Origin`。綁到其他網路介面時會印出警告：它會透過設定好的後端花掉
你的錢，而且少了 loopback 位址可以比對，跨來源檢查也會跟著退化。

## 從 agent 驅動

`skill/` 把這個專案打包成 Claude Skill。`adapters/` 裡有給 Claude Code 與 Codex 的
`AGENTS.md` 片段，以及給 OpenCode 的規則檔。三者都只是薄薄一層外殼，
底下指向同一套 CLI，所以修好一個驗證器就等於到處都修好了。

agent 可以在完全沒有設定任何模型的情況下驅動整條管線：`lx todo` 吐出待譯的工作、
agent 在自己的 context 裡翻譯、`lx apply` 收回來。這是正規做法，不是備案；
翻譯、審校、審計也可以各自交給不同的 agent。

## 在 CI 裡

```yaml
- run: pip install -e ./tools/scriptorium    # 或你 vendor 進去的位置
- run: lx extract docs/guide.md --lang zh-TW
- run: lx check   docs/guide.md --lang zh-TW
```

先重新 extract，改過的原文才會變成待譯工作，而不是悄悄通過檢查；
接著 check 會讓 pull request 失敗。

## 已知限制

採用之前值得先知道，而且以下每一項都是實際量測出來的，不是猜的：

- **只支援 Markdown。** 純文字與 EPUB 排在後面；DOCX 與 i18n 檔案格式
  （JSON、YAML、PO）則是刻意永久排除。
- **強調語法還是會送到模型面前。** `**粗體**`、`_斜體_`、`~~刪除線~~`
  和連結文字的方括號目前都還沒遮罩。方向是把遮罩補完，不是放寬規則。
- **折行後的接續行還留在 segment 裡**，連同它後面的縮排。這個專案自己的文件裡，
  2394 個 segment 中有 79 個是這種情況；要遮掉它們，就得把一個句子拆成好幾個 segment。
- **沒有模糊比對。** 重用的條件是完全相同：文字一樣、所在的區塊種類一樣、
  切分方式也一樣。將來做出來也只會是參考性質，永遠不自動套用，
  因為模糊命中的 placeholder 集合本來就一定不同。
- **`escaping` 目前是空轉的**，要等到有 XML 這類語法的格式進來才會生效。
- **是照文件寫的，不是照小說寫的。** 語言 brief 裡就是這樣寫的——`zh-TW` 那份直接
  要求「技術文件語感」，還交代標題要名詞化。而且每一段送進模型時，帶的只有自己的
  文字和區塊種類，前後段都不在旁邊。roadmap 上排的是結構和規模；文學語感、段與段
  之間的連貫、以及記下某個角色怎麼說話的地方，三樣都還沒有。

## 和其他工具的差別

我查過的開源 Markdown 在地化工具，沒有例外，都是從語法樹把目標文件重新算出來。
[po4a](https://www.po4a.org/) 預設會替段落重新斷行；
[mdpo](https://github.com/mondeja/mdpo) 和 [Weblate](https://weblate.org/) 都從 AST
重新算——Weblate 走的是 [translate-toolkit](https://github.com/translate/translate)，
而它自己的文件就寫著「不會檢查譯文是否保持原文的格式」；
[Okapi Framework](https://okapiframework.org/) 的 Markdown filter 更有一個 2018 年開到
現在的 bug：縮排式程式碼區塊會掉開頭的空白。

這對它們來說是划算的交換，因為換到的是廣度。po4a 支援二十種格式，翻了 Debian 的
文件二十年。Weblate 給你譯者社群、審校流程和上百種格式。
[OmegaT](https://omegat.org/) 是真正的 CAT 工作環境，有模糊比對、索引檢索和術語窗格。

Scriptorium 只支援一種格式，換來的是把譯文代回原本的位元組。
你如果需要很多格式或很多譯者，就用那些工具。你如果需要檔案原封不動回來、
只有你翻譯的地方變了，那正是這個工具存在的唯一理由。

## 專案現況

Markdown 目前已經可以端到端跑完：抽取、翻譯、驗證、修復、輸出，
翻譯記憶也能跟著原文改版一路沿用。

正在做的是 SQLite 狀態層。十萬字的稿子目前每存一段就要重寫整份狀態檔，改完之後
中斷可以從那一段接著跑。

後面大致依這個順序排：把工作台的 HTTP 介面寫成有版本的 schema 定下來；EPUB 與純文字
這兩種格式，有了它們一整本書才進得了管線；設定改成可以用 CLI 寫，並讓兩個階段指向
不同的模型；最後是重建審校工作台，那是規模最大的一項，介面沒定案就動不了。

`docs/decisions.md` 記著每一項決策，以及當初輸掉的替代方案。

## 開發

```bash
python -m pytest -q                # 326 passed，不碰網路
python -m ruff check src tests
```

CI 會在 Ubuntu 和 Windows 上各跑一次 Python 3.9 與 3.12。跨平台這件事在這裡不是形式，
因為換行字元的保真度正是這個專案對外的承諾之一。

[CONTRIBUTING.md](CONTRIBUTING.md) 寫了環境設定、五件會讓修改被退回的事，
以及文件保真度的問題該怎麼回報（該附原始檔而不是貼內容）。`AGENTS.md` 是這個專案的
工作約定，架構不變量都寫在裡面；`docs/decisions.md` 記著每條不變量為何是現在這個寫法，
以及當初輸掉的替代方案。

## 授權

MIT，見 [LICENSE](LICENSE)。
