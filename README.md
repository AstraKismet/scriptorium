# Scriptorium

[![CI](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

**English** · [繁體中文](README.zh-TW.md)

Translate documents without letting the model near the markup.

Scriptorium is a command-line localization pipeline for Markdown and plain text.
It splits a document into segments — a block for Markdown, a paragraph for prose
— masks code, links, tags and protected terms behind `⟦n⟧` placeholders, and
substitutes the translations back into the original file, leaving every byte it
did not translate as it found it. Rendered output is UTF-8; a Big5 or Shift-JIS
source keeps its characters, not its bytes. Block
syntax is never sent at all. The model translates prose, which is the part of the
job it is actually good at.

Pure Python, **zero runtime dependencies**, no compiled extensions. It runs on a
bare interpreter, in CI, inside an agent sandbox, and on a locked-down machine.

## What it looks like

Given this source:

```markdown
# Deployment Guide

The **Celurion** server requires Go 1.22 and a running instance of `postgres`.

| Option | Default | Description |
| --- | --- | --- |
| `port` | 8080 | Listening port for the HTTP server |

> Warning: never commit secrets to the repository.
```

`lx run guide.md --lang zh-TW` writes:

```markdown
# 部署指南

**Celurion** 伺服器需要 Go 1.22，以及一個執行中的 `postgres` 實例。

| 選項 | 預設值 | 說明 |
| --- | --- | --- |
| `port` | 8080 | HTTP 伺服器的監聽連接埠 |

> 警告：絕對不要把機密資訊提交到儲存庫。
```

With `Celurion` and `Go` listed in `config/dnt.txt`, this is everything the model
received for that paragraph:

```
The **⟦2⟧** server requires ⟦3⟧ 1.22 and a running instance of ⟦1⟧.
```

The placeholders are opaque: the model can move them, but it cannot translate,
drop or duplicate one, and code fills the originals back in afterwards. The
heading's `#`, the table's alignment row, the `>`, and the cells holding `port`
and `8080` were never in a segment at all — they stay in the skeleton and are
copied back byte for byte.

## Quick start

Not published to PyPI; install from source.

```bash
git clone https://github.com/AstraKismet/scriptorium.git
cd scriptorium
pip install -e .            # optional, provides the `lx` command
```

Without installing, use `python -m scriptorium` in place of `lx`.

```bash
lx init                             # config templates and state directories
lx run docs/guide.md --lang zh-TW   # the whole pipeline
lx web                              # review what came out
```

`lx run` needs a model backend, and the shipped default expects Ollama on
`localhost:11434` — see [Model backends](#model-backends). To try it with no
backend at all, use the agent path: `lx extract`, then `lx todo` for the pending
segments as JSON, translate them yourself, and `lx apply` to put them back.

`examples/walkthrough.md` runs through a full document end to end.

## How it works

**1. Split and mask.** The document becomes a list of translatable segments;
everything else stays in a skeleton. Code spans, math, URLs, link and reference
targets, footnotes, HTML tags, entities, template variables and any term in
`config/dnt.txt` are replaced with `⟦n⟧` placeholders. Block syntax — headings,
list bullets, blockquote markers, table pipes — is not masked but never leaves
the skeleton, so the model does not see it either. Supporting new inline syntax
means a new pattern in `mask.py`, not a new sentence in a prompt.

**2. Translate what is new.** Segments already in the translation memory are
offered back, and taken only if their placeholders still match the segment they
matched — a memory hit passes the same gate model output does. The rest go to a
configured model, to an agent via `lx todo` / `lx apply`, or to a person in the
review workbench. All three are equal sources and every segment records which one
produced it. Because a segment is identified by its content and the kind of block
it sits in rather than by its position, editing one paragraph of a 400-segment
document reports `reused 399 | pending 1`, and moving a whole section reports
nothing pending at all.

**3. Check mechanically.** Placeholders that went missing, figures dropped,
terminology that drifted, structure the translation broke. `lx check` exits 1 on
any error, so a build can gate on it.

**4. Render by substitution.** Translations are refilled into the original
skeleton, never re-serialized from a parse tree. This is why front matter, fenced
code, table alignment, indentation and line endings survive byte for byte — a CI
corpus of 55 deliberately awkward inputs, 35 Markdown and 20 plain text, asserts
it on Linux and Windows, from the bytes on disk to the bytes written back.

Per-segment errors compound, which is why steps 1 and 4 are code rather than
instructions. Even at a hypothetical 99.5% per segment, a 500-segment document
comes through intact 8% of the time, and a dropped table pipe or a mangled link
is exactly the damage that survives review.

A green `lx check` means the structure survived and the mechanical rules passed
on every segment except any you have **waived** — see `lx waive` below, which
reports those findings at warning severity instead of removing them. It does not
mean the translation is good; that is what review is for.

## Commands

| Command | What it does |
|---|---|
| `lx init` | scaffold config and state |
| `lx extract SRC --lang L` | parse to segments, mask markup, reuse translation memory (`--tone literary` for prose) |
| `lx extract SRC --lang L --from OLD` | carry another tracked document's translations across, holds and waivers included — what a split or a renamed file needs |
| `lx todo SRC --lang L` | pending segments as JSON, for an agent to translate |
| `lx terms SRC --lang L` | propose glossary rows from the source text (`--append` to add them) |
| `lx apply SRC --lang L --file F` | ingest translations, auto-normalize |
| `lx hold SRC --lang L --ids A,B` | keep segments out of every queue that selects work |
| `lx unhold SRC --lang L --ids A,B` | return held segments to the queues |
| `lx waive SRC --lang L --ids A,B` | stand by this wording: report the rules judgement can overrule at warn instead of failing the build. Refused on a segment `lx check` reports nothing on |
| `lx unwaive SRC --lang L --ids A,B` | put a waived segment's errors back |
| `lx translate SRC --lang L` | translate with a configured model (`--mode draft\|polish\|repair`, `--limit N`) |
| `lx check SRC --lang L` | validate; exit 1 on error (`--json` for the full report) |
| `lx repair SRC --lang L` | re-translate only failing segments (`--limit N`) |
| `lx run SRC --lang L` | the whole loop, with `--polish` for a fluency pass and `--limit N` to bound it |
| `lx render SRC --lang L -o OUT` | rebuild the target document |
| `lx blocks SRC --lang L` | the rebuilt document block by block, without writing it |
| `lx sentences SRC --lang L` | how a segment's text divides into sentences |
| `lx commit SRC --lang L` | bank approved wording in the translation memory |
| `lx web` | local review workbench |
| `lx config get\|set\|unset KEY [VALUE]` | read and write `lx.config.json` with dotted keys |
| `lx routing show\|set STAGE PROVIDER[:MODEL]` | which backend, and which model, serves each stage |
| `lx untracked` | files matching `sources` with no state yet, one row per target language |
| `lx status [--json] [--scan ROOT]` | project status; `--json` is the frozen contract in `docs/contracts/status-json.md` |
| `lx models [--provider P]` | ask a backend which models it serves |
| `lx providers` / `lx stats` | backends / coverage |

`--overwrite-human` on `translate`, `repair`, `run` and `apply` lets a model
run replace segments a person wrote. Off by default: an unattended pass runs
over whatever the queue hands it, and review is what it would overwrite — so
those segments are not even selected, and the count of what was left alone is
reported rather than dropped.

`--dry-run` on `translate`, `repair` and `run` reports the work without calling a
model; `--provider` and `--model` override the routing entry for one run.

The glossary enforces consistency, but it cannot tell you what a book's two
hundred proper nouns are before you have read it. `lx terms` proposes them:
capitalized runs the source uses somewhere other than the start of a sentence,
ranked by frequency, emitted as glossary rows with the **target column empty**.
Choosing how a name renders is judgement and stays with you — the command finds
the list, you decide the wording, and a row does nothing at all until you have.

```bash
lx terms novel.md --lang zh-TW              # to stdout, redirect and edit
lx terms novel.md --lang zh-TW --append     # add unseen ones to the glossary
```

The glossary settles what a name *is*. It says nothing about how a person sounds,
and in a novel that is most of the work. `config/style.txt` is where a project
records it:

```
The narration is close third person, past tense, anchored on Eleanor.

[Eleanor Vance, Eleanor, Miss Vance]
She says 您 to her father and to Mr Ashcombe, 你 to her sister.
Her diction is precise and a little cold; no 呢, no 嘛.

[Thomas]
Working-class, warm, elliptical. He calls Eleanor 小姐, never 您.

# A note to myself: decide whether the letters in ch.7 stay formal.
```

Lines before the first `[name]` block are the narrator's, and ride on every
request. A `[name]` block rides only on the batches whose text mentions that
name, so the cast can be as large as the book needs — a forty-character novel
does not pay forty sets of notes on every request. Lines starting with `#` never
leave the file.

Nothing inside a block is parsed. Whether to send one is a decision code can
make; how a character should sound is not, and a format with `register:` and
`address:` fields would have put the second one in the parser. The same file
reaches an agent through `lx todo`, which carries the register brief and the
notes for the segments it emitted.

## Validation rules

| Rule | Severity | Catches |
|---|---|---|
| `tags` | error | a placeholder lost, duplicated or invented; a pair inverted or crossed |
| `containment` | error | a block the translation opened and the source did not; an added table column |
| `eol` | error | a carriage return the source did not have |
| `numbers` | error | a figure in the source missing from the target — in Chinese and Japanese, from both numeral systems, so 第一章 answers `Chapter 1` |
| `missing` | error | a segment never translated |
| `escaping` | error | a raw `<`, `&` or `]]>` in an XML host — inert today, activates with EPUB |
| `glossary` | per row, `forbidden` always error | an agreed term rendered inconsistently, or a banned variant used |
| `lexicon` | error / warn | a term the target locale writes differently |
| `dnt` | warn | a protected brand or product name altered |
| `untranslated` | warn | the source copied through verbatim |
| `punct` / `spacing` | warn | width and CJK/Latin boundary problems that could not be auto-fixed |
| `length` | warn | a segment much shorter or longer than expected |
| `held` | warn | a segment a reviewer is finishing themselves; no queue selects it |
| `waived` | warn | a segment a reviewer read and stood by, so its errors report here |

Turn any of them off per project with `"checks_disabled": ["length"]`, or stand
by one segment with `lx waive`.

Every rule here is decidable by a program; anything needing human judgement lives
in the language brief or in review. `docs/decisions.md` records the entry test
and the eighteen terms it removed from the lexicon.

Punctuation width and CJK/Latin spacing are repaired by `normalize.py` on the way
in rather than reported.

## Target languages

| Locale | Language brief | Normalization | Lexicon |
|---|---|---|---|
| `zh-TW` | yes | punctuation width, CJK/Latin spacing, whitespace | yes |
| `ja` | yes | — | — |

Whitespace normalization collapses runs left by an editor, and deliberately keeps
a Markdown hard line break — two or more spaces before a line end — because
deleting one joins two lines the translation meant to keep apart.

Any other `--lang` value works and gets the structural checks, but no
locale-specific guidance or terminology rules. Adding one means a brief in
`translate.py`, a normalization profile in `config.py`, a reference file
under `skill/reference/`, and one question: does the language write cardinal
numbers in CJK numerals? Chinese and Japanese do, so `第一章` answers a source
`Chapter 1`; the reader is shared, so the answer is a subtag rather than a table.

## Model backends

Providers are declared in `lx.config.json`. `lx init` routes every stage to
`local`; a typical split once you have a paid key sends bulk drafting to a cheap
or local model and reserves a strong one for polish and repair, which work on
small batches by construction.

```json
"providers": {
  "local":    { "kind": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b-instruct", "api_key_env": "", "timeout": 300 },
  "lmstudio": { "kind": "openai", "base_url": "http://localhost:1234/v1",  "model": "local-model",         "api_key_env": "" },
  "openai":   { "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",         "api_key_env": "OPENAI_API_KEY" },
  "claude":   { "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY" }
},
"routing": { "draft": "local", "polish": "claude", "repair": "claude" }
```

A routing value may also name a model, which is how two stages share one endpoint
without a duplicate provider entry whose `base_url`, `api_key_env` and timeout are
copies that drift:

```json
"routing": {
  "draft":  { "provider": "openai", "model": "gpt-4o-mini" },
  "polish": { "provider": "openai", "model": "gpt-4o" },
  "repair": "local"
}
```

`lx routing` writes both shapes, and refuses an unknown provider at write time
rather than mid-run:

```bash
lx routing show                            # draft → openai (gpt-4o-mini)
lx routing set polish openai:gpt-4o
lx config set batch.size 10                # dotted keys, validated before the write
lx config get providers.openai.api_key_env # the variable's name, and whether it is set
```

The model resolves most-specific-first: `--model` on the command line, then the
routing entry's, then the provider's own. A `--provider` naming a different
backend drops the entry's model, because a model id belongs to the backend that
serves it.

Any OpenAI-compatible endpoint works, including fully local ones:

| Runtime | `base_url` | scaffolded as |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | `local` |
| LM Studio | `http://localhost:1234/v1` | `lmstudio` |
| llama.cpp `llama-server` | `http://localhost:8080/v1` | `llamacpp` |
| vLLM | `http://localhost:8000/v1` | — |
| LiteLLM proxy | `http://localhost:4000/v1` | — |

The request itself is deliberately plain: no `response_format`, no tools, no
streaming unless you opt in, because self-hosted runtimes tend to reject unknown
fields rather than ignore them.

`lx models` asks a backend what it serves, so a model id can be copied rather
than typed:

```bash
lx models                                  # whatever routing.draft points at
lx models --provider llamacpp
lx models --json                           # {"provider", "configured", "models"}
```

## llama.cpp, and what a router changes

`llama-server` needs no special handling — it is `kind: "openai"` like every
other OpenAI-compatible endpoint, and `lx init` scaffolds it as `llamacpp` on
port 8080. Running it in **router mode** does change four things, and all four
are measured rather than assumed. The numbers below are from build
`b9892-ee445f93d` on 2026-08-20, against a router serving sixteen models with
`max_instances: 1` and `--sleep-idle-seconds 60` on every preset.

**The `model` field selects which model runs, and the id must be exact.** A
single-model `llama-server` ignores the field; a router refuses anything it does
not know with `400 model '…' not found`. The ids are long —
`mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M` — so copy one from
`lx models` rather than typing it. A whole setup, from nothing:

```bash
lx init
lx config set providers.llamacpp.base_url http://127.0.0.1:8088/v1   # if not 8080
lx models --provider llamacpp                                        # copy an id
lx config set providers.llamacpp.model mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M
lx routing set draft llamacpp
lx routing set polish llamacpp
lx routing set repair llamacpp
lx run book/ch1.md --lang zh-TW
```

**A load blocks the caller; it is not a retryable status.** With
`models_autoload: true` the first request for a model that is not resident loads
it — and, the first time, downloads it. A second request fired 1.5 s into a
104.9 s cold start also waited 103.1 s and then answered `200`. Nothing answers
`503` or `425`, so `retries` buys nothing here and **`timeout` is the only knob**.
That is why the scaffolded `llamacpp` entry gets 600 seconds where Ollama and LM
Studio get 300. Raise it for a first run that has to fetch a large model.

**Switching models costs a reload, but less than you would expect.** Under
`max_instances: 1` only one model is resident, so pointing two stages at two
models means an unload and a load between them — measured over three rounds at
**4.8–5.0 s** for a 2.5 GB model and **6.4–6.9 s** for a 7 GB one, both already
on disk, and **5.2 s** to
wake one that had gone to sleep. That is per *stage transition*, not per batch,
so a draft/polish split costs seconds per run and is worth having. The number
scales with the file, though: two 15 GB models alternating is a different
proposition, and there one model for the whole run is the better trade.

**`batch.concurrency: 2` is right, and 4 is worse than 2.** One instance still
serves several requests at once. Four realistic batches took 4.64 s serially,
**2.85 s with two in flight** (1.63×), and 4.28 s with four — the default is the
measured optimum, not a guess. A preset carrying `--parallel 1` serializes
regardless, so check the model's own preset before raising it.

The first-ever request for a model is the outlier and it is worth stating plainly:
104.9 s, of which nearly all was a 2.5 GB HuggingFace download, not a load.

API keys are read from the environment variable named in `api_key_env` and are
never written to config, state or logs. Local servers usually need no key — leave
`api_key_env` empty and no `Authorization` header is sent. `lx providers` shows
what is configured and whether each key is present.

`lx config set` will not put a credential in the file: `api_key_env` takes the
*name* of a variable and nothing that looks like a key, a `base_url` carrying a
username or password is refused, and `providers.*.headers` — which goes onto the
wire verbatim — is not writable from the command line at all. No `lx` command
takes key material on a command line, because argv is visible in a process
listing and lands in shell history before any refusal can run.

## Translation memory

`.lx/tm.<lang>.jsonl` only ever grows, and it is the file worth committing to
version control.

`lx commit` does not bank everything. A segment `lx check` reports an **error**
on is left out, because the memory keeps the last record per key — banking a
broken wording would hide the good one already there, for every document in the
project and on every machine that pulls the file. A **held** segment is left out
too: a hold says the segment is yours to finish, and `lx commit` takes a whole
document. Both are named by id when it happens, and an `lx unhold` or a fix
followed by another `lx commit` is all either one needs.

A **waived** segment *is* banked — you read the finding and stood by the wording,
so the memory should hold it — and its line carries `"waived": true`. The waiver
itself does not travel: the next document to take that wording gets it unwaived,
`lx check` reports it there, and `lx extract` names the segment, because your
judgement about one paragraph is not a judgement about a document you have not
read.

`.lx/state.db` is working state — one SQLite database for the project. It is
regenerable only for wording you have already banked with `lx commit`, so commit
before you delete it. `.lx/reports/` is always regenerable.


## Splitting a book, or renaming a chapter

A novel arrives as one long file and you want it in chapters. Cut it up with an
editor — this tool does not write source files — and then tell each new file
where its translations are:

```bash
lx extract ch1.md --lang zh-TW --from novel.md
lx extract ch2.md --lang zh-TW --from novel.md
```

Both come back `reused N | pending 0`. Nothing is re-translated and nothing is
sent to a model. It works because a segment's identity is its *content*, never
its position or its filename, so a paragraph that moved to another file is still
the same paragraph.

`--from` reads the other document's stored state. It copies; `novel.md` keeps
everything it had, and you can point ten chapters at it. Holds, waivers, `origin`
and the placeholder map each wording was written against all come across, because
they live with the segment.

**Use `--from` rather than committing first.** `lx commit` followed by a plain
`lx extract` mostly works and quietly loses two things. The translation memory is
keyed on the source text, so a book that says the same sentence twice with two
different translations banks both and can only give one back — both chapters then
get the same wording. And `lx commit` deliberately does not bank a **held**
segment, so wording you were still working on is not there at all and comes back
untranslated. `--from` loses neither.

The same flag covers a rename: extract the new path with `--from` the old one.

Nothing checks *where* you cut. A heading left at the end of the previous file,
or a chapter with no heading, reuses every segment perfectly and passes
`lx check` — the boundaries are yours to get right, and `lx render` is where you
see them.

If you try to extract a file that is no longer there, the command says so and
tells you what still works:

```
$ lx extract novel.md --lang zh-TW
lx: novel.md is not there, and `lx extract` is the only command that reads the
    source file — this document's translations are still in .lx/state.db, so
    `lx render novel.md --lang zh-TW` and `lx check novel.md --lang zh-TW` both
    still work. If you renamed or split it, extract the new file and carry them
    across: `lx extract <new-file> --lang zh-TW --from novel.md`.
```

The old document's row stays in `.lx/state.db` after a split, so it goes on being
counted by `lx stats` and `lx status --json` until you remove it. There is no
command for that yet.

## Review workbench

```bash
lx web        # http://127.0.0.1:8787
```

Source and target side by side, placeholders highlighted, validation failures
shown as marginalia beside the segment that caused them. Translate, polish,
repair, check, preview and commit from the toolbar. A field saves when it loses
focus, and the text is normalized on the way in, including repairing placeholder
brackets a model mangled.

**One paragraph at a time, and a source that changed under you.** Each row
carries its own small button, labelled with the stage it will send — `polish` on
a segment that already has a translation, `draft` on one that does not — so
disliking a single paragraph costs one press instead of a whole run. Naming a
segment reaches it whatever state it is in, including one you have held; what it
does not do is quietly replace wording *you* wrote, which the page asks about
first, because a run that is refused at the write still calls the model and still
costs tokens. *Re-extract* reads the source file again for a document already
being tracked: unchanged paragraphs keep their translation, their provenance and
their hold, and a paragraph whose text changed comes back untranslated. It asks
first when there is something to lose, and it never changes the register the
document is frozen in — that is `lx extract --reset --tone <register>`, which has
to be told which register and is deliberately not a button.

**Backends are chosen here too, without editing a file.** The toolbar picks the
backend and — from a list the backend itself publishes — the model for the next
run; left alone, the model box sends nothing and each stage uses its own
configured model. *Backends…* adds or edits one: `kind`, `base_url`, `model`,
`api_key_env`, `timeout`, `temperature`, and which backend runs `draft`,
`polish` and `repair`. A local runtime, a server elsewhere on your network and a
cloud API are all reachable from that form.

Every rule it enforces is the CLI's. The page sends one key per request and
renders whatever `lx config set` would have said, so the two surfaces cannot
disagree about what is writable — and **no field is meant to hold an API key**:
`api_key_env` takes the *name* of an environment variable, and a value shaped
like a key is refused there and in `base_url` without being repeated back. The
honest caveat is that the *model* box stores whatever you type, a key pasted into
the wrong field included; `docs/contracts/workbench-http.md` records that as a
known divergence rather than pretending otherwise. Changing `base_url` needs an
explicit acknowledgement, because it decides where the document and the
credential are sent. A backend cannot be deleted from the browser; that is
`lx config unset` or the file.

It is a shell over the same functions the CLI calls, so there is no second
implementation to drift.

It binds to loopback, and loopback was never the whole answer: any page in your
browser can POST to a local port. So every path it is given — the source, and the
output path — is confined to the directory it was started in, and it answers only
requests from the page it served itself. `curl` and `lx` are unaffected, because
neither sends an `Origin`. Binding it to another network interface prints a
warning: it can spend money through configured providers, and the cross-origin
check degrades when there is no loopback address to compare against.

## Driving it from an agent

`skill/` packages this as a Claude Skill. `adapters/` has an `AGENTS.md` fragment
for Claude Code and Codex, and a rule file for OpenCode. All three are thin
shells over the same CLI, so fixing a validator fixes it everywhere.

An agent can drive the whole pipeline with no model configured at all: `lx todo`
emits the pending work, the agent translates in its own context, `lx apply`
ingests it. This is the normal path rather than a fallback, and translation,
review and audit can each go to a different agent.

## In CI

```yaml
- run: pip install -e ./tools/scriptorium    # or wherever you vendored it
- run: lx extract docs/guide.md --lang zh-TW
- run: lx check   docs/guide.md --lang zh-TW
```

Re-extracting first is what makes an edited source surface as pending work
instead of passing quietly. The check then fails the pull request.

## Limitations

Worth knowing before you adopt it, and all of them measured rather than guessed:

- **Markdown and plain text.** EPUB is next; DOCX and the i18n formats (JSON,
  YAML, PO) are deliberately out of scope for good. A plain-text source is read
  in whatever encoding it turns out to be in — UTF-8, Big5, GBK, Shift-JIS,
  UTF-16 — and a file whose bytes are invalid in its own encoding is refused
  rather than repaired with replacement characters. **Output is always UTF-8**,
  so a Big5 source comes back as UTF-8 with its characters intact. Byte-exact
  output in the source encoding is not offered, and for cp950 it is not
  currently possible: ten Big5 sequences, including the box-drawing characters
  that rule a chapter, have two spellings and only one survives decoding.
- **The workbench only offers what `sources` matches**, and that default is
  `docs/**/*.md`. A novel project sets it to its own glob — `["book/**/*.txt"]` —
  because a blanket `**/*.txt` would sweep up `config/dnt.txt`.
- **Emphasis markers still reach the model.** `**bold**`, `_italics_`, `~~strike~~`
  and link-text brackets are not masked yet. The direction is to finish the
  masking, not to relax the rule.
- **A wrapped block's interior line breaks are inside the segment**, along with
  the indentation that follows them — 149 of 1467 segments across this project's
  own documentation. They cannot be masked without splitting one sentence into
  several segments.
- **No fuzzy matching.** Reuse is exact: same text, same kind of block, same
  segmentation. When it lands it will be advisory and never applied
  automatically, since a fuzzy hit differs in its placeholder set by definition.
- **`escaping` is inert** until a format with an XML host arrives.
- **Prose support reaches register, context and voice — and stops there.**
  `--tone literary` selects a narrative brief and the register is part of the
  memory key, so wording banked under one cannot be served to the other; each
  request item carries the segments either side of it as read-only source; and
  `config/style.txt` holds the narrator's voice and each character's. What none
  of that gives you is continuity across a book. Nothing knows that a promise was
  made in chapter 2, and the review workbench still presents segments rather than
  a chapter you can read as prose.

## How this differs from the alternatives

Every open-source Markdown localization tool I surveyed re-renders the target
from a parse tree. [po4a](https://www.po4a.org/) rewraps paragraphs by default;
[mdpo](https://github.com/mondeja/mdpo) and
[Weblate](https://weblate.org/) render from an AST — Weblate through
[translate-toolkit](https://github.com/translate/translate), whose own
documentation says it "does not perform any checks that the translated text has
the same formatting as the source"; and the
[Okapi Framework](https://okapiframework.org/)'s Markdown filter has had an open
bug since 2018 where indented code blocks lose their leading whitespace.

That is a reasonable trade for the breadth those tools offer. po4a handles twenty
formats and two decades of Debian's documentation. Weblate gives you a translator
community, a review workflow and a hundred formats. [OmegaT](https://omegat.org/)
is a real CAT environment, with fuzzy matching, concordance search and glossary
panes.

Scriptorium handles two formats and substitutes translations back into the
original file instead, leaving every byte it did not translate as it found it.
If you need many formats or many translators, use those.
If you need the file to come back unchanged except where you translated it, that
is the whole point of this one.

## Project status

Markdown and plain text work end to end today: extract, translate, validate,
repair, render, and a translation memory that survives revisions. Working state
is SQLite, and a translation run commits each batch as it lands — an interrupted
100k-word document keeps what it had translated and resumes on the rest.

The workbench's HTTP surface is written down and versioned
(`docs/contracts/workbench-http.md`), configuration is writable from both the CLI
and the browser, and either surface can point each pipeline stage at a different
backend. Naming a *model* per stage is `lx routing set draft <provider>:<id>` or
the file — the browser writes the backend, and a model chosen there applies to
the run in front of you rather than being stored.

Queued: EPUB, which is how novels actually circulate; and a rebuilt review
workbench, which is the largest item by far and is what the frozen contract
exists to make possible.

`docs/decisions.md` records what was decided and, for each entry, the alternative
that lost.

## Development

```bash
python -m pytest -q                # 1932 tests, no network
python -m ruff check src tests
```

CI runs Python 3.9 and 3.12 on both Ubuntu and Windows; the cross-platform half
matters here, because line-ending fidelity is something this project promises.

[CONTRIBUTING.md](CONTRIBUTING.md) covers setup, the five things that will get a
change rejected, and how to report a document-fidelity bug. `AGENTS.md` is the
working agreement and holds the architectural invariants; `docs/decisions.md`
records why each is worded the way it is, and the alternative that lost.

## License

MIT — see [LICENSE](LICENSE).
