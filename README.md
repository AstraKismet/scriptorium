# Scriptorium

[![CI](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/AstraKismet/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

**English** · [繁體中文](README.zh-TW.md)

Publishing-grade document localization. The model translates sentences; code does
everything else.

## What it does

Point it at a document and it runs four steps.

1. **Split and mask.** The document is parsed into translatable segments, and
   every piece of markup — code spans, URLs, link targets, table pipes — is
   replaced with a `⟦n⟧` placeholder. The model is handed prose and nothing else,
   so there is no markup for it to reflow, translate or drop.
2. **Translate what is new.** Segments already in the translation memory are
   reused; the rest go to a configured model, to an agent through
   `lx todo` / `lx apply`, or to a person in the review workbench. All three are
   equal sources, and every segment records which one produced it.
3. **Check mechanically.** Dropped or duplicated placeholders, figures that
   changed, forbidden terminology, segments left untranslated. `lx check` exits
   non-zero when any of these fail, so "is this finished" has an exit code
   instead of an opinion.
4. **Render by substitution.** Translations are put back into the original
   document's skeleton. The target file is never rebuilt from the model's
   output — only refilled — so every byte the pipeline did not deliberately
   change is reproduced as it was.

Segments are keyed by their content rather than their position, so editing the
source retranslates only what actually changed and everything already approved
comes back from the memory. Nothing you have reviewed is spent twice.

Structural work — parsing markup, protecting code spans, reassembling documents,
enforcing terminology, normalizing punctuation — is deterministic and lives in
Python. Asking a language model to do it in its head is where translation
pipelines break: at 99.5% per node, a 500-node document survives 8% of the time,
and the failures are the invisible kind.

No compiled dependencies. Works with any OpenAI-compatible endpoint, including
fully local models.

## Status

Working today, for Markdown: extract, translate, validate, repair, render, and a
translation memory that survives revisions. Byte-exact reassembly of the source
document is gated in CI by an adversarial corpus of 27 inputs, on Linux and
Windows — with one measured exception: the CLI still reads and writes through
Python's text mode, so line endings are normalized at the file boundary. That is
the next thing being fixed.

Under construction, in this order: containment validators, typed placeholders, a
SQLite state layer, a rebuilt review workbench, then EPUB and plain text.

Deliberately out of scope: DOCX, the i18n file formats, and anything that needs a
system web view. `docs/decisions.md` records why, along with the alternative that
lost in each case.

## Install

```bash
git clone https://github.com/AstraKismet/scriptorium.git
cd scriptorium
pip install -e .          # optional; provides the `lx` command
```

Without installing, use `python -m scriptorium` in place of `lx`.

## Quick start

```bash
lx init                                   # config templates + state dirs
lx run docs/guide.md --lang zh-TW         # the whole pipeline
lx web                                    # review what came out
```

`lx run` extracts segments, reuses anything already in the translation memory,
translates the rest, validates, repairs what failed, and writes the target file —
refusing to render while errors remain.

## Backends

Providers are declared in `lx.config.json`. The request sent to an
OpenAI-compatible endpoint is deliberately plain — no `response_format`, tools,
or streaming unless you opt in — because self-hosted runtimes reject unknown
fields rather than ignoring them.

```json
"providers": {
  "local":    { "kind": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b-instruct", "api_key_env": "", "timeout": 300 },
  "lmstudio": { "kind": "openai", "base_url": "http://localhost:1234/v1",  "model": "local-model",         "api_key_env": "" },
  "openai":   { "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",         "api_key_env": "OPENAI_API_KEY" },
  "claude":   { "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6", "api_key_env": "ANTHROPIC_API_KEY" }
},
"routing": { "draft": "local", "polish": "claude", "repair": "claude" }
```

| Runtime | `base_url` |
|---|---|
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp server | `http://localhost:8080/v1` |
| vLLM | `http://localhost:8000/v1` |
| LiteLLM proxy | `http://localhost:4000/v1` |

`lx providers` lists what is configured and whether each key is present.

API keys are read from the environment named in `api_key_env` and are never
written to config, state, or logs. Local servers usually want no key at all —
leave `api_key_env` empty and no `Authorization` header is sent.

Routing sends bulk drafting to a cheap or local model and reserves a strong one
for the polish and repair passes, which operate on small batches by construction.

## Commands

| | |
|---|---|
| `lx init` | scaffold config and state |
| `lx extract SRC --lang L` | parse to segments, mask markup, reuse translation memory |
| `lx todo SRC --lang L` | pending segments as JSON, for an agent to translate |
| `lx apply SRC --lang L --file F` | ingest translations, auto-normalize |
| `lx translate SRC --lang L` | translate with a configured model (`--mode draft\|polish\|repair`) |
| `lx check SRC --lang L` | validate; exit 1 on error |
| `lx repair SRC --lang L` | re-translate only failing segments |
| `lx run SRC --lang L` | the whole loop, with `--polish` for a fluency pass |
| `lx render SRC --lang L -o OUT` | rebuild the target document |
| `lx commit SRC --lang L` | bank approved wording in the translation memory |
| `lx web` | local review workbench |
| `lx providers` / `lx stats` | backends / coverage |

## What gets checked

`tags` (placeholder integrity), `glossary` (agreed terms and forbidden variants),
`numbers` (figures dropped or invented), `lexicon` (a term the target locale
writes differently), `dnt`, `untranslated`, `punct`, `spacing`, `length`,
`missing`.

`lexicon` is a per-locale preference table: it pairs a term with the form that
locale's own technical documentation uses, and flags the difference. It carries
no judgement about the other form, which is correct in the conventions it comes
from — the rule is only that one document should not mix them.

Punctuation width and CJK/Latin spacing are corrected on ingest rather than
reported — the cheapest defect is the one that cannot be introduced.

**On structural fidelity, honestly.** `render` rebuilds from the original
skeleton and substitutes only translated spans, so front matter, fenced code,
table alignment and math around a segment survive by construction. What the
skeleton does *not* yet guarantee is the structure of the document after
substitution: a translation containing a line-initial `1. `, or a `|` inside a
table cell, changes the block structure and currently passes validation. Three
containment validators are the next correctness work, and the reasoning is in
`docs/decisions.md`. Until they land, treat a green `lx check` as necessary and
not sufficient.

## Incremental translation

Segment identity is a content hash, not a position. Edit one paragraph of a
400-segment document and `extract` reports `reused 399 | pending 1`. Approved
wording does not drift between revisions, and moving a section costs nothing.

`.lx/tm.<lang>.jsonl` is the asset worth committing to version control. The rest
of `.lx/` is regenerable.

## The workbench

```bash
lx web        # http://localhost:8787
```

Source and target side by side, placeholders highlighted, failures shown as
marginalia against the segment that caused them. Translate, polish, repair,
check, preview, and commit from the toolbar; edits save on blur and are
normalized on the way in, including repairing placeholder brackets a model
mangled.

It binds to loopback and is a shell over the same functions the CLI calls — there
is no second implementation. Exposing it on another interface prints a warning,
because it can spend money through configured providers.

## Using it from an agent

`skill/` packages this as a Claude Skill. `adapters/` has an `AGENTS.md` fragment
for Claude Code and Codex, and a rule file for OpenCode. All three are thin
pointers at the same CLI, so a fix to a validator lands everywhere at once.

Agents can drive the pipeline without any model configured at all: `lx todo`
emits work, the agent translates in its own context, `lx apply` ingests it. This
is a first-class path, not a fallback — translation, review and audit can each be
delegated to a different agent.

## CI

```yaml
- run: lx extract docs/guide.md --lang zh-TW
- run: lx check   docs/guide.md --lang zh-TW
```

Re-extracting first is what makes a source edit surface as pending work rather
than pass quietly; the check then fails the pull request.

## Development

```bash
python -m pytest -q          # 59 passed; no network
python -m ruff check src tests
```

`AGENTS.md` holds the architectural invariants and is the authoritative working
agreement — `CLAUDE.md` is a one-line pointer at it. Read it before changing
anything structural. `docs/decisions.md` records why each invariant is worded the
way it is.

## License

MIT — see [LICENSE](LICENSE).
