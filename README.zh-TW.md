# Scriptorium

[![CI](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

[English](README.md) · **繁體中文**

翻譯文件，全程不讓模型碰到任何標記語法。

Scriptorium 是一套在地化的命令列工具，吃 Markdown 和純文字。它把文件切成 segment——
Markdown 切到區塊，散文切到段落——把程式碼、連結、標籤和受保護的術語都遮成
`⟦n⟧` placeholder，翻完再填回原本的檔案，沒翻到的每個位元組都照原樣留著。輸出一律
是 UTF-8：Big5 或 Shift-JIS 的原文保住的是字，不是位元組。
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
CI 上有一組語料庫在把關，裡面收了 55 份刻意刁難的輸入（Markdown 35 份、純文字
20 份），Linux 與 Windows 都跑，從磁碟上的位元組一路驗到寫回去的位元組。

每個 segment 的誤差會一路累積，所以第一步和第四步寫成程式，而不是寫成給模型的指示。
就算每個 segment 的正確率有 99.5%，一份 500 個 segment 的文件也只有 8% 的機率
毫髮無傷；而少一根表格分隔線、壞掉一個連結，正好就是撐得過審校的那種損傷。

`lx check` 通過只代表兩件事：結構沒被破壞，機械規則在你沒有**豁免**的段落上都過了。
豁免請看下面的 `lx waive`：它把那些判定降成警告，而不是把它們拿掉。通過不保證譯文
品質好，那是審校的工作。

## 指令

| 指令 | 用途 |
|---|---|
| `lx init` | 建立設定與狀態骨架 |
| `lx extract SRC --lang L` | 解析成 segment、遮罩標記、重用翻譯記憶（小說加 `--tone literary`） |
| `lx todo SRC --lang L` | 以 JSON 吐出待譯 segment，供 agent 翻譯 |
| `lx terms SRC --lang L` | 從原文挑出候選術語、開成詞彙表的列（加 `--append` 直接寫進去） |
| `lx apply SRC --lang L --file F` | 收回譯文，自動正規化 |
| `lx hold SRC --lang L --ids A,B` | 把 segment 排除在所有挑工作的佇列之外 |
| `lx unhold SRC --lang L --ids A,B` | 讓被保留的 segment 回到佇列 |
| `lx waive SRC --lang L --ids A,B` | 為這段譯文背書：憑判斷可以推翻的規則改以 warn 回報，不再擋住 build。`lx check` 沒有報 error 的段落會被拒絕 |
| `lx unwaive SRC --lang L --ids A,B` | 把豁免收回，錯誤照舊 |
| `lx translate SRC --lang L` | 用設定好的模型翻譯（`--mode draft\|polish\|repair`、`--limit N`） |
| `lx check SRC --lang L` | 驗證；有 error 時以 1 結束（`--json` 可拿到完整報告） |
| `lx repair SRC --lang L` | 只重譯失敗的 segment（`--limit N`） |
| `lx run SRC --lang L` | 跑完整條流程；加 `--polish` 會多跑一次流暢度潤稿，加 `--limit N` 可限制每一輪的量 |
| `lx render SRC --lang L -o OUT` | 重建目標文件 |
| `lx blocks SRC --lang L` | 重建後的文件，逐區塊列出，不寫檔 |
| `lx sentences SRC --lang L` | 一個 segment 的文字如何切成句子 |
| `lx commit SRC --lang L` | 把核可的譯法存進翻譯記憶 |
| `lx web` | 本機審校工作台 |
| `lx config get\|set\|unset KEY [VALUE]` | 用點號路徑讀寫 `lx.config.json` |
| `lx routing show\|set STAGE PROVIDER[:MODEL]` | 每個階段走哪個後端、用哪個模型 |
| `lx untracked` | `sources` 掃得到卻還沒建立狀態的檔案；每個目標語言各一列 |
| `lx status [--json] [--scan ROOT]` | 專案進度；`--json` 是凍結後的契約，見 `docs/contracts/status-json.md` |
| `lx models [--provider P]` | 問後端它供應哪些模型 |
| `lx providers` / `lx stats` | 後端 / 覆蓋率 |

`translate`、`repair`、`run` 都吃 `--dry-run`，只回報會做哪些工作，不會真的呼叫模型；
`--provider` 和 `--model` 則是只改這一次執行要用的後端和模型。

詞彙表管得住一致性，卻沒辦法在你把書讀完以前，先告訴你一本書裡那兩百個專有名詞
是哪些。`lx terms` 就是來開這張清單的：把原文裡首字母大寫、而且不只出現在句首的
詞挑出來，依出現次數排序，直接寫成詞彙表的列，**譯法那一欄留空**。名字要怎麼譯是
判斷，判斷歸你——指令只負責把清單找出來，怎麼寫由你決定；沒寫進去以前，那一列
什麼都不會做。

```bash
lx terms novel.md --lang zh-TW              # 印到 stdout，導出來慢慢改
lx terms novel.md --lang zh-TW --append     # 沒收錄過的直接補進詞彙表
```

詞彙表定得下一個名字**是什麼**，定不下一個人**說起話來是什麼樣子**，而後者在小說裡
才是大宗。這件事寫在 `config/style.txt`：

```
敘事是第三人稱貼身視角，過去式，錨在 Eleanor 身上。

[Eleanor Vance, Eleanor, Miss Vance]
對父親和 Ashcombe 先生用「您」，對妹妹用「你」。
用詞精準而冷，不用「呢」「嘛」。

[Thomas]
勞工階級，溫厚，話說一半。他叫 Eleanor「小姐」，絕不用「您」。

# 寫給自己看的：第七章那幾封信要不要保持書面語，還沒決定。
```

第一個 `[名字]` 之前的段落屬於敘事者，每一次請求都帶著走。`[名字]` 區塊只跟著提到
該名字的批次走，所以角色表要多大就多大——四十個角色的長篇，不會每次請求都揹四十
份註記。`#` 開頭的行不會離開這個檔案。

區塊裡面一個字都不解析。要不要送，程式判斷得了；一個角色該是什麼聲音，程式判斷
不了，把 `register:`、`address:` 這種欄位設計進去，等於把後者塞進 parser 裡。同一份
檔案也會經由 `lx todo` 送到 agent 手上，連同語域 brief 和該批段落用得到的那幾份註記。

## 驗證規則

| 規則 | 嚴重度 | 抓什麼 |
|---|---|---|
| `tags` | error | placeholder 遺失、重複或憑空出現；成對的 placeholder 顛倒或交叉 |
| `containment` | error | 譯文開了一個原文沒有的區塊；表格被多加一欄 |
| `eol` | error | 原文沒有、譯文卻多出來的歸位字元 |
| `numbers` | error | 原文裡的數字在譯文中不見了——中文與日文兩套數字系統都算，所以「第一章」抵得上 `Chapter 1` |
| `missing` | error | segment 從未翻譯 |
| `escaping` | error | XML 類語法裡未跳脫的 `<`、`&` 或 `]]>`——目前空轉，等 EPUB 進來才生效 |
| `glossary` | 逐列設定，`forbidden` 一律 error | 約定術語譯法不一致，或用了禁用的譯法 |
| `lexicon` | error / warn | 用詞與目標語言的慣用形式不符 |
| `dnt` | warn | 受保護的品牌或產品名稱被改動 |
| `untranslated` | warn | 原文整段照抄 |
| `punct` / `spacing` | warn | 無法自動修好的標點寬度與中英文交界問題 |
| `length` | warn | 長度比預期短得多或長得多 |
| `held` | warn | 審校者正在自己收尾的段落；任何佇列都不會選到它 |
| `waived` | warn | 審校者看過並為它背書的段落，它的錯誤改在這裡回報 |

每個專案都可以自行關掉任何一條，寫成 `"checks_disabled": ["length"]` 即可；只想為
單一段落背書就用 `lx waive`。

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
再到 `skill/reference/` 放一份參考檔，另外回答一個問題：這個語言的散文寫基數詞
用不用漢字數字？中文和日文都用，所以「第一章」抵得上原文的 `Chapter 1`；讀取的
程式是共用的，所以新增語言加的是一個 subtag，不是一張表。

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

一個 routing 值也可以順便指定模型。兩個階段想共用同一個端點、只換模型時，
這樣就不必再開一份 provider——那份複本的 `base_url`、`api_key_env`、timeout
都是抄來的，遲早會各走各的：

```json
"routing": {
  "draft":  { "provider": "openai", "model": "gpt-4o-mini" },
  "polish": { "provider": "openai", "model": "gpt-4o" },
  "repair": "local"
}
```

兩種寫法 `lx routing` 都寫得出來，而且寫進去的當下就會擋掉打錯的 provider 名字，
不會拖到跑到一半才炸：

```bash
lx routing show                            # draft → openai (gpt-4o-mini)
lx routing set polish openai:gpt-4o
lx config set batch.size 10                # 點號路徑，寫入前先驗
lx config get providers.openai.api_key_env # 只印變數名稱，以及它現在有沒有值
```

模型由細到粗決定：先看命令列的 `--model`，再看 routing 那一筆自己指定的，
最後才回到 provider 本身的。如果 `--provider` 指到另一個後端，
routing 那筆的模型就不算數了——模型 id 是屬於服務它的那個後端的。

任何 OpenAI 相容端點都可以，包含完全跑在本機的：

| 執行環境 | `base_url` | 預設條目名 |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | `local` |
| LM Studio | `http://localhost:1234/v1` | `lmstudio` |
| llama.cpp `llama-server` | `http://localhost:8080/v1` | `llamacpp` |
| vLLM | `http://localhost:8000/v1` | — |
| LiteLLM proxy | `http://localhost:4000/v1` | — |

請求本身刻意保持單純：沒有 `response_format`、沒有 tools，也沒有 streaming——
除非你主動開啟。原因是自架的推論環境傾向於**拒絕**未知欄位，而不是忽略它們。

`lx models` 會去問後端它到底供應哪些模型，讓模型 id 用抄的而不是用打的：

```bash
lx models                                  # 問 routing.draft 指到的那個後端
lx models --provider llamacpp
lx models --json                           # {"provider", "configured", "models"}
```

## llama.cpp，以及 router 模式改變了什麼

`llama-server` 不需要特別待遇——它跟其他 OpenAI 相容端點一樣是 `kind: "openai"`，
`lx init` 會在 8080 埠上生出一個叫 `llamacpp` 的預設條目。但把它開成 **router 模式**
會改變四件事，而這四件都是量出來的，不是推的。以下數字量自 build `b9892-ee445f93d`，
2026-08-20，一台供應 16 個模型、`max_instances: 1`、每個 preset 都帶
`--sleep-idle-seconds 60` 的 router。

**`model` 欄位決定跑哪個模型，而且 id 必須一字不差。** 單模型的 `llama-server` 根本
不看這個欄位；router 則會對任何它不認得的值回 `400 model '…' not found`。那些 id 很長
——`mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M`——所以從 `lx models` 抄一個，
不要用打的。整套從零設起是這樣：

```bash
lx init
lx config set providers.llamacpp.base_url http://127.0.0.1:8088/v1   # 如果不是 8080
lx models --provider llamacpp                                        # 抄一個 id
lx config set providers.llamacpp.model mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M
lx routing set draft llamacpp
lx routing set polish llamacpp
lx routing set repair llamacpp
lx run book/ch1.md --lang zh-TW
```

**載入模型時它會把呼叫端擋住，而不是回一個可重試的狀態碼。** `models_autoload: true`
意味著第一次要用一個還沒常駐的模型時，它會去載——而且第一次還會先下載。實測：在一次
104.9 秒的冷啟動開始後 1.5 秒再送一個請求，那個請求也等了 103.1 秒，然後回 `200`。
沒有任何一刻回過 `503` 或 `425`，所以 `retries` 在這裡幫不上忙，**只有 `timeout` 有用**。
這就是為什麼預設的 `llamacpp` 條目給 600 秒，而 Ollama 和 LM Studio 給 300。第一次要抓
大模型的話再往上調。

**換模型要付重載成本，但比想像中便宜。** `max_instances: 1` 之下只有一個模型常駐，所以
把兩個階段指到兩個模型，中間就有一次卸載加載入——各量三輪，2.5 GB 的模型
**4.8–5.0 秒**、7 GB 的 **6.4–6.9 秒**（兩者都已經在磁碟上），把睡著的叫醒是
**5.2 秒**。這筆帳是每換一次階段付一次，不是每個批次付一次，所以初譯和潤稿分開用不同
模型，一趟下來也只多幾秒，划得來。不過這個數字會跟著檔案大小走：兩個 15 GB 的模型互相
輪替就是另一回事了，那種情況下整趟都用同一個模型比較划算。

**`batch.concurrency: 2` 是對的，而 4 比 2 還糟。** 單一個常駐模型仍然能同時服務多個
請求。四個真實大小的批次：循序 4.64 秒，**兩個同時在線 2.85 秒**（1.63 倍），四個同時
在線 4.28 秒——預設值是量出來的最佳點，不是猜的。如果某個模型的 preset 帶了
`--parallel 1`，那它無論如何都會排隊，調高之前先看一下該模型自己的 preset。

第一次拿一個模型是唯一的例外，值得講清楚：104.9 秒，其中絕大部分是從 HuggingFace 下載
2.5 GB，不是載入。

API 金鑰只從 `api_key_env` 指定的環境變數讀取，絕不寫進設定檔、狀態或記錄檔。
本機伺服器通常不需要金鑰，把 `api_key_env` 留空就不會送出 `Authorization` 標頭。
`lx providers` 會列出設定了哪些後端，以及每個金鑰在不在。

`lx config set` 不會讓金鑰進到檔案裡：`api_key_env` 只收環境變數的**名字**，
長得像金鑰的一律擋下；`base_url` 帶帳號密碼會被拒；`providers.*.headers`
是原樣送上線的東西，命令列根本不給寫。另外沒有任何 `lx` 指令會在命令列上收金鑰——
argv 在行程列表裡看得到，也會直接進 shell 記錄，那時候再拒絕已經來不及了。

## 翻譯記憶

`.lx/tm.<lang>.jsonl` 只增不改，也是唯一值得納入版本控制的檔案。

`lx commit` 不會照單全收。`lx check` 判為 **error** 的段落不會入庫：記憶庫每個
鍵只認最後一筆記錄，把壞掉的譯法存進去，等於把原本存對的那筆藏起來，而且會隨檔案
散佈到專案裡每一份文件、每一台拉過這個檔的機器。**held** 的段落也不入庫 —— hold
的意思是這段由你收尾，而 `lx commit` 一次處理整份文件。兩種情況都會列出段落 id；
`lx unhold` 之後、或把譯法修好之後再 commit 一次就好。

被 **waive** 的段落**會**入庫 —— 你看過那條判定並且為譯法背書，記憶庫本來就該留著
它 —— 而且那一行會帶 `"waived": true`。豁免本身不會跟著跑：下一份文件拿到這個譯法
時是沒有豁免的，`lx check` 會在那裡照樣回報，`lx extract` 也會把段落點名出來。你對
某一段的判斷，不是對一份你沒讀過的文件的判斷。

`.lx/state.db` 是工作狀態，整個專案共用一個 SQLite 檔。只有已經用 `lx commit`
存進記憶的譯法才重新產生得出來，所以刪掉它之前記得先 commit。`.lx/reports/`
則隨時都可以再生。

## 審校工作台

```bash
lx web        # http://127.0.0.1:8787
```

原文與譯文並排，placeholder 高亮，驗證失敗會以旁註標在對應的 segment 邊上。
翻譯、潤稿、修復、檢查、預覽、提交都在工具列上。編輯欄位失去焦點就自動存檔，
內容在收回時會先正規化，包含把模型弄壞的 placeholder 括號修回來。

**一次只重做一段，以及在你背後被改掉的原文。**每一列都有自己的小按鈕，按鈕上寫的
就是它會送出去的階段——已經有譯文的送 `polish`，還沒有的送 `draft`——所以看不順眼
某一段時，代價是按一下，而不是整份重跑。點名某個 segment 就一定送得到它，不管它處在
什麼狀態，連你自己 hold 起來的也一樣；它唯一不會偷偷做的，是覆蓋*你*親手寫的字，
那件事頁面會先問過你，因為在寫入端被擋下來的那一次，模型還是被呼叫了、token 還是花了。
*Re-extract* 會把已經在追蹤的文件的原始檔重讀一次：沒有變動的段落保留譯文、來源與
hold，內容變過的段落則變回未翻譯。有東西可能會失去時它會先問，而且它從不改動文件被
凍結的語域——那是 `lx extract --reset --tone <register>` 的事，那個指令必須被明確
告知語域，所以它刻意不是一顆按鈕。

**後端也在這裡選，不必去改檔案。**工具列上可以挑後端，以及挑這次要跑的模型——
清單是後端自己報出來的；不去動它的話，模型欄位什麼都不送，每個階段各自用自己
設定好的模型。*Backends…* 用來新增或編輯後端：`kind`、`base_url`、`model`、
`api_key_env`、`timeout`、`temperature`，以及 `draft`、`polish`、`repair`
各自要跑在哪個後端上。本機跑的、區網另一台機器上的、雲端 API，三種都能從這個表單
接起來。

它把關的每一條規則都還是 CLI 的。頁面一次只送一個 key，並把 `lx config set`
會講的那句話原樣顯示出來，兩邊因此不會對「什麼可以寫」講出不同答案——而且
**沒有任何欄位是拿來放 API key 的**：`api_key_env` 收的是環境變數的*名字*，長得像
金鑰的值在它和 `base_url` 上都會被擋下來，而且不會被覆述一次。老實說有個例外：
*模型*那一格你打什麼它就存什麼，貼錯格子的金鑰也一樣——這件事
`docs/contracts/workbench-http.md` 記成一條已知分歧，而不是假裝沒有。改 `base_url` 要另外勾一次確認，因為那決定了稿子
和金鑰被送去哪裡。後端不能從瀏覽器刪除，那是 `lx config unset` 或直接改檔案的事。

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

- **支援 Markdown 與純文字。** EPUB 排在後面；DOCX 與 i18n 檔案格式
  （JSON、YAML、PO）則是刻意永久排除。純文字原文的編碼是讀出來的，UTF-8、Big5、
  GBK、Shift-JIS、UTF-16 都認得；本身位元組就已經壞掉的檔案一律拒收，不會拿替代
  字元硬補回去。**輸出一律是 UTF-8**，Big5 原文回來會是 UTF-8，字都在。以原編碼
  輸出、位元組完全一致這件事目前不提供，對 cp950 也還做不到：有十個 Big5 序列有
  兩種寫法，解碼後只活得下來一種，其中就有拿來畫章節分隔線的框線字元。
- **工作台只列得出 `sources` 比對得到的檔案**，而預設值是 `docs/**/*.md`。
  翻小說的專案得自己改成對應的 glob，例如 `["book/**/*.txt"]`；不能直接寫
  `**/*.txt`，那會連 `config/dnt.txt` 一起掃進來。
- **強調語法還是會送到模型面前。** `**粗體**`、`_斜體_`、`~~刪除線~~`
  和連結文字的方括號目前都還沒遮罩。方向是把遮罩補完，不是放寬規則。
- **折行後的接續行還留在 segment 裡**，連同它後面的縮排。這個專案自己的文件裡，
  1467 個 segment 中有 149 個是這種情況；要遮掉它們，就得把一個句子拆成好幾個 segment。
- **沒有模糊比對。** 重用的條件是完全相同：文字一樣、所在的區塊種類一樣、
  切分方式也一樣。將來做出來也只會是參考性質，永遠不自動套用，
  因為模糊命中的 placeholder 集合本來就一定不同。
- **`escaping` 目前是空轉的**，要等到有 XML 這類語法的格式進來才會生效。
- **小說這邊做到語域、上下文和聲音，就到這裡為止。** 加上 `--tone literary`，語言
  brief 就從技術文件那份換成敘事的寫法，語域本身也進了翻譯記憶的鍵，同一句話在兩種
  語域下各自成立，不會互相蓋掉；每一則請求都帶著前後段當唯讀參照；角色和敘事者的
  聲音寫在 `config/style.txt`。這些加起來仍然給不了的是一本書的連貫：沒有東西記得
  第二章許下的承諾，審校工作台看到的也還是一段一段的 segment，不是一整章可以當成
  文章讀的東西。

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

Scriptorium 只支援兩種格式，換來的是把譯文代回原本的檔案，沒翻到的位元組一個都不動。
你如果需要很多格式或很多譯者，就用那些工具。你如果需要檔案原封不動回來、
只有你翻譯的地方變了，那正是這個工具存在的唯一理由。

## 專案現況

Markdown 與純文字目前都可以端到端跑完：抽取、翻譯、驗證、修復、輸出，
翻譯記憶也能跟著原文改版一路沿用。工作狀態存在 SQLite，翻譯每跑完一批就落一次
地——十萬字的稿子中途斷掉，翻好的都還在，接著跑剩下的就好。

工作台的 HTTP 介面已經寫成有版本的規格定下來了（`docs/contracts/workbench-http.md`），
設定從 CLI 和瀏覽器都寫得動，兩邊也都能讓每個階段指向不同的後端。要替某個階段
指定「模型」得用 `lx routing set draft <provider>:<id>` 或直接改檔案——瀏覽器寫的是
後端，在那裡挑的模型只作用於眼前這一次跑，不會存起來。

還排在後面的：EPUB，小說多半是以這個格式流通；以及重建審校工作台，那是規模最大的
一項，也正是把介面凍結成規格所要成全的事。

`docs/decisions.md` 記著每一項決策，以及當初輸掉的替代方案。

## 開發

```bash
python -m pytest -q                # 1921 tests，不碰網路
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
