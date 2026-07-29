# Scriptorium

[![CI](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

**English** · [繁體中文](README.zh-TW.md)

Translate documents without letting the model near the markup.

Scriptorium is a command-line localization pipeline for Markdown. It splits a
document into sentences, masks code, links, tags and protected terms behind
`⟦n⟧` placeholders, and substitutes the translations back into the original file
byte for byte. Block syntax is never sent at all. The model translates prose,
which is the part of the job it is actually good at.

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
reused as-is. The rest go to a configured model, to an agent via
`lx todo` / `lx apply`, or to a person in the review workbench. All three are
equal sources and every segment records which one produced it. Because segment
identity is a content hash rather than a position, editing one paragraph of a
400-segment document reports `reused 399 | pending 1`.

**3. Check mechanically.** Placeholders that went missing, figures dropped,
terminology that drifted, structure the translation broke. `lx check` exits 1 on
any error, so a build can gate on it.

**4. Render by substitution.** Translations are refilled into the original
skeleton, never re-serialized from a parse tree. This is why front matter, fenced
code, table alignment, indentation and line endings survive byte for byte — a CI
corpus of 28 deliberately awkward inputs asserts it on Linux and Windows, from
the bytes on disk to the bytes written back.

Per-segment errors compound, which is why steps 1 and 4 are code rather than
instructions. Even at a hypothetical 99.5% per segment, a 500-segment document
comes through intact 8% of the time, and a dropped table pipe or a mangled link
is exactly the damage that survives review.

A green `lx check` means the structure survived and the mechanical rules passed.
It does not mean the translation is good; that is what review is for.

## Commands

| Command | What it does |
|---|---|
| `lx init` | scaffold config and state |
| `lx extract SRC --lang L` | parse to segments, mask markup, reuse translation memory |
| `lx todo SRC --lang L` | pending segments as JSON, for an agent to translate |
| `lx apply SRC --lang L --file F` | ingest translations, auto-normalize |
| `lx translate SRC --lang L` | translate with a configured model (`--mode draft\|polish\|repair`) |
| `lx check SRC --lang L` | validate; exit 1 on error (`--json` for the full report) |
| `lx repair SRC --lang L` | re-translate only failing segments |
| `lx run SRC --lang L` | the whole loop, with `--polish` for a fluency pass |
| `lx render SRC --lang L -o OUT` | rebuild the target document |
| `lx commit SRC --lang L` | bank approved wording in the translation memory |
| `lx web` | local review workbench |
| `lx providers` / `lx stats` | backends / coverage |

`--dry-run` on `translate`, `repair` and `run` reports the work without calling a
model.

## Validation rules

| Rule | Severity | Catches |
|---|---|---|
| `tags` | error | a placeholder lost, duplicated or invented; a pair inverted or crossed |
| `containment` | error | a block the translation opened and the source did not; an added table column |
| `eol` | error | a carriage return the source did not have |
| `numbers` | error | a figure in the source missing from the target |
| `missing` | error | a segment never translated |
| `escaping` | error | a raw `<`, `&` or `]]>` in an XML host — inert today, activates with EPUB |
| `glossary` | per row, `forbidden` always error | an agreed term rendered inconsistently, or a banned variant used |
| `lexicon` | error / warn | a term the target locale writes differently |
| `dnt` | warn | a protected brand or product name altered |
| `untranslated` | warn | the source copied through verbatim |
| `punct` / `spacing` | warn | width and CJK/Latin boundary problems that could not be auto-fixed |
| `length` | warn | a segment much shorter or longer than expected |

Turn any of them off per project with `"checks_disabled": ["length"]`.

Every rule here is decidable by a program; anything needing human judgement lives
in the language brief or in review. `docs/decisions.md` records the entry test
and the eighteen terms it removed from the lexicon.

Punctuation width and CJK/Latin spacing are repaired by `normalize.py` on the way
in rather than reported.

## Target languages

| Locale | Language brief | Normalization | Lexicon |
|---|---|---|---|
| `zh-TW` | yes | punctuation width, CJK/Latin spacing | yes |
| `ja` | yes | — | — |

Any other `--lang` value works and gets the structural checks, but no
locale-specific guidance or terminology rules. Adding one means a brief in
`translate.py`, a normalization profile in `config.py`, and a reference file
under `skill/reference/`.

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

Any OpenAI-compatible endpoint works, including fully local ones:

| Runtime | `base_url` |
|---|---|
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp server | `http://localhost:8080/v1` |
| vLLM | `http://localhost:8000/v1` |
| LiteLLM proxy | `http://localhost:4000/v1` |

The request itself is deliberately plain: no `response_format`, no tools, no
streaming unless you opt in, because self-hosted runtimes tend to reject unknown
fields rather than ignore them.

API keys are read from the environment variable named in `api_key_env` and are
never written to config, state or logs. Local servers usually need no key — leave
`api_key_env` empty and no `Authorization` header is sent. `lx providers` shows
what is configured and whether each key is present.

## Translation memory

`.lx/tm.<lang>.jsonl` only ever grows, and it is the file worth committing to
version control.

`.lx/docs/` is working state. It is regenerable only for wording you have already
banked with `lx commit`, so commit before you clean it. `.lx/reports/` is always
regenerable.

## Review workbench

```bash
lx web        # http://127.0.0.1:8787
```

Source and target side by side, placeholders highlighted, validation failures
shown as marginalia beside the segment that caused them. Translate, polish,
repair, check, preview and commit from the toolbar. A field saves when it loses
focus, and the text is normalized on the way in, including repairing placeholder
brackets a model mangled.

It binds to loopback, and it is a shell over the same functions the CLI calls, so
there is no second implementation to drift. Binding it to another network
interface prints a warning, because it can spend money through configured
providers.

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

- **Markdown only.** Plain text and EPUB are next; DOCX and the i18n formats
  (JSON, YAML, PO) are deliberately out of scope for good.
- **Emphasis markers still reach the model.** `**bold**`, `_italics_`, `~~strike~~`
  and link-text brackets are not masked yet. The direction is to finish the
  masking, not to relax the rule.
- **A wrapped block's interior line breaks are inside the segment**, along with
  the indentation that follows them — 79 of 2394 segments across this project's
  own documentation. They cannot be masked without splitting one sentence into
  several segments.
- **No fuzzy matching.** Only exact content-hash reuse. When it lands it will be
  advisory and never applied automatically, since a fuzzy hit differs in its
  placeholder set by definition.
- **`escaping` is inert** until a format with an XML host arrives.

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

Scriptorium handles one format and substitutes translations back into the
original bytes instead. If you need many formats or many translators, use those.
If you need the file to come back unchanged except where you translated it, that
is the whole point of this one.

## Project status

Markdown works end to end today: extract, translate, validate, repair, render,
and a translation memory that survives revisions.

Next, in order: a SQLite state layer, a rebuilt review workbench, then EPUB and
plain text — the two formats that let a whole book through the pipeline.

`docs/decisions.md` records what was decided and, for each entry, the alternative
that lost.

## Development

```bash
python -m pytest -q                # 253 passed, no network
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
