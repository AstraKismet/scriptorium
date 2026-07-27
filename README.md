# Scriptorium

Publishing-grade document localization. The model translates sentences; code does
everything else.

Structural work — parsing markup, protecting code spans, reassembling documents,
enforcing terminology, normalizing punctuation — is deterministic and lives in
Python. Asking a language model to do it in its head is where translation
pipelines break: at 99.5% per node, a 500-node document survives 8% of the time,
and the failures are the invisible kind.

No runtime dependencies. Works with any OpenAI-compatible endpoint, including
fully local models.

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

Structural fidelity is absent from that list because it cannot fail. `render`
rebuilds from the original skeleton and substitutes only translated spans, so
front matter, fenced code, table alignment, and math survive by construction.

Punctuation width and CJK/Latin spacing are corrected on ingest rather than
reported — the cheapest defect is the one that cannot be introduced.

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

`skill/` packages this as a Claude Skill. `adapters/` has an `AGENTS.md` for
Claude Code and Codex, and a rule file for OpenCode. All three are thin pointers
at the same CLI, so a fix to a validator lands everywhere at once.

Agents can drive the pipeline without any model configured at all: `lx todo`
emits work, the agent translates in its own context, `lx apply` ingests it.

## CI

```yaml
- run: lx extract docs/guide.md --lang zh-TW
- run: lx check   docs/guide.md --lang zh-TW
```

A source edit surfaces as pending work and the check fails the pull request.
`examples/ci.yml` has a complete job.

## Development

```bash
python -m pytest -q          # 38 tests, no network
python -m ruff check src tests
```

`CLAUDE.md` holds the architectural invariants. Read it before changing anything
structural.

## License

MIT
