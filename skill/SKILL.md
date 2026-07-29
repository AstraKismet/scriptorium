---
name: scriptorium-localization
description: Publishing-grade translation and localization of documents, docs sites, and app strings using a deterministic mask-translate-verify pipeline with translation memory and a glossary. Use this skill whenever the user asks to translate, localize, internationalize, or produce a multi-language version of any file or project — including "translate this README", "make a zh-TW version of the docs", "i18n this app", "check my translation", or updating an already-translated document after the source changed. Also use it when the user asks about translation quality, terminology consistency, or which regional term variant a target locale expects, even if they do not use the word "localization".
---

# Scriptorium

A translation pipeline built on one rule: **the model translates sentences, code does everything else.**

Structural work — parsing markup, protecting code spans, re-assembling documents, checking terminology, normalizing punctuation — is deterministic. Doing it in the model's head is where publishing-grade pipelines break: a 500-node document at 99% per-node accuracy fails five times. The `scriptorium` package owns all of it, and the CLI is the only entry point.

## The loop

```bash
LX="python3 -m scriptorium"                 # or `lx` if the package is installed

$LX init                                    # once per project
$LX extract docs/guide.md --lang zh-TW      # parse, mask, pull from translation memory
$LX todo    docs/guide.md --lang zh-TW      # -> JSON of pending segments
#   ... translate the segments (see "Translating" below) ...
$LX apply   docs/guide.md --lang zh-TW --file draft.json
$LX check   docs/guide.md --lang zh-TW      # exit 1 on any error
#   ... repair only the flagged segments, re-apply, re-check ...
$LX render  docs/guide.md --lang zh-TW -o i18n/zh-TW/guide.md
$LX commit  docs/guide.md --lang zh-TW      # bank approved segments in the TM
```

Run `check` until it exits 0 before rendering. `check` writes `.lx/reports/<doc>.<lang>.json`; that file is the localization report — never write one by hand, and never claim a document passed without a green exit code.

## Translating

`todo` returns segments that look like this:

```json
{ "id": "s0007",
  "kind": "para",
  "text": "Set the ⟦1⟧ variable, then run ⟦2⟧ to rebuild the index.",
  "glossary": [{"term": "index", "use": "索引"}] }
```

`⟦n⟧` stands for a code span, URL, math block, template variable, or protected brand name. Treat it as an opaque noun: **copy it exactly, move it wherever the target grammar wants it, never invent one, never drop one.** Everything else in `text` is prose you can translate freely.

Return a JSON object mapping id to translated text, then feed it to `apply`. Batch 20–40 segments per turn so surrounding context stays visible; long documents stay resumable because state lives in `.lx/`, not in the conversation.

Two passes beat one when quality matters:

1. **Draft** — accurate, complete, placeholders intact. Speed model is fine here.
2. **Polish** — re-read the drafted segments as a block and rewrite anything that reads like translationese. Strong model, and only over prose segments; headings, table cells, and UI strings rarely need it.

Skip the polish pass for changelogs, API reference tables, and other reference material where literal is correct.

## Repairing

`check` writes the failures back onto each segment, so `todo --all` re-emits flagged segments with a `fix` field explaining what went wrong. Re-translate **only those segments** — never re-run the whole document, which loses approved work and burns tokens.

If `check` reports `tags` errors repeatedly on the same segment, the placeholder is probably being swallowed by surrounding punctuation. Translate that segment with the placeholder isolated by spaces, then let `apply`'s normalizer close the gaps.

## What check enforces

| Rule | Severity | Catches |
|---|---|---|
| `tags` | error | placeholder lost, duplicated, or invented; a pair inverted or crossed |
| `containment` | error | the translation opens a block the source did not, or adds a table column |
| `escaping` | error | a character the host syntax cannot hold, left unescaped |
| `eol` | error | a carriage return the source did not have |
| `glossary` | configurable | agreed term rendered inconsistently, or a forbidden variant used |
| `numbers` | error | a figure in the source missing from the target |
| `missing` | error | segment never translated |
| `lexicon` | error/warn | a term the target locale writes differently |
| `dnt` | warn | protected brand or product name altered |
| `untranslated` | warn | source copied through verbatim |
| `punct` / `spacing` | warn | width and CJK/Latin boundary problems `apply` could not auto-fix |
| `length` | warn | truncated or padded segment |

Structure splits in two, and only the second half can fail. The document *around* a segment is preserved by construction — `render` rebuilds from the original skeleton and substitutes only the translated spans, so headings, list nesting, table columns, front matter and fenced code cannot regress. What the translation itself does once substituted between them is the `containment` rule's business, and it can.

In practice that means: do not begin a translation with `1. `, `- `, `#`, or `> ` unless the source line began that way; do not put a line break in a heading, a table cell or a blockquote; do not add a blank line; and do not add a `|` inside a table cell. If the target genuinely needs a list, the source is a list and the segmentation already reflects it.

## Setting up a project

`lx init` writes `lx.config.json`, `config/glossary.csv`, and `config/dnt.txt`. Before a first real run, fill in the glossary and the do-not-translate list — most quality complaints are terminology complaints, and both files are cheap to populate by skimming the source for repeated domain nouns. Templates and field meanings are in `config/` alongside this skill.

Wire `lx check` into CI so translations cannot regress silently — re-extract first, so a source edit surfaces as pending work rather than passing quietly:

```yaml
- run: lx extract docs/guide.md --lang zh-TW
- run: lx check   docs/guide.md --lang zh-TW   # exit 1 on any error
```

## Reference files

Read these when the situation calls for them, not upfront:

- `reference/zh-TW.md` — Traditional Chinese style: register, spacing, punctuation, term pairs, and the traps that make output read as character-converted rather than written for the locale. Read before any zh-TW work. Its register section covers technical documentation; prose is a different register, selected with `lx extract --tone literary` and reported in `lx todo`'s `tone` field.
- `reference/pipeline.md` — full command reference, state layout, batching and model-routing guidance, resuming interrupted jobs.
- `reference/formats.md` — extending past Markdown: JSON/YAML string catalogs, gettext PO, MDX, HTML. Read before translating anything that is not a `.md` file.
- `reference/critique.md` — the design rationale, and the failure modes of prompt-only translation rules. Read when someone asks why the pipeline is shaped this way, or wants to modify it.

## Letting a model do the drafting

When the project has a backend configured, the whole loop collapses to one command:

```bash
$LX run docs/guide.md --lang zh-TW --polish
```

That extracts, translates with the routed provider, checks, repairs only the failing segments, and renders — refusing to render while errors remain. `$LX providers` shows what is configured. Backends are any OpenAI-compatible endpoint, including local ones (Ollama, LM Studio, llama.cpp, vLLM), so drafting can run offline and cost nothing.

Prefer `run` when a backend exists and the document is long. Prefer the manual `todo`/`apply` loop when translating in your own context gives better results — short documents, marketing copy, anything where you have conversational context the batch prompt would not carry.

`$LX web` opens a local review workbench for the human: source and target side by side, failures shown against the segment that caused them. Suggest it when handing off for review.

## Portability

Stdlib-only Python 3.9+ with no install step, so the same pipeline runs from a Claude Skill, an `AGENTS.md` project rule, an OpenCode rule, a Makefile, or CI with nothing changed. `adapters/` has drop-in files for each host. The CLI is the product; the skill is a thin wrapper over it.
