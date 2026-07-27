# CLAUDE.md

Working agreement for this repository. Read before changing code.

## What this project is

A localization pipeline built on one architectural commitment: **the model
translates sentences; code does everything else.** Structural work — parsing,
markup protection, reassembly, terminology enforcement, punctuation
normalization — is deterministic and lives in Python. The model is called only
for the part that needs judgement.

Every design question resolves against that sentence. When adding a feature, the
first question is which side of the line it falls on.

## Invariants

These are decisions, not preferences. Changing one is a deliberate act that
needs a note in `docs/decisions.md`, not a drive-by refactor.

1. **No runtime dependencies.** `pip install` must never be required to run the
   pipeline. It has to work from a bare Python in CI, in an agent sandbox, and on
   a locked-down machine. Dev-only tools (pytest, ruff) are fine.
2. **Structure is preserved by construction, never by checking.** `parse()`
   produces a skeleton that reproduces the source byte-for-byte once segment
   values are substituted. If a "did the headings survive" check ever seems
   necessary, the skeleton has been broken and that is the bug.
3. **The model never sees markup.** Anything non-translatable is masked to `⟦n⟧`
   before a request is built. New syntax support means a new pattern in
   `mask.py`, not a new instruction in a prompt.
4. **Checks are mechanically decidable.** A rule belongs in `checks.py` only if a
   program can decide it without judgement. Anything requiring taste goes in the
   prompt or in human review.
5. **Fix rather than report where possible.** If a defect can be corrected
   deterministically, `normalize.py` corrects it on ingest. Reporting a fixable
   defect wastes the reviewer's attention.
6. **Credentials come from the environment only.** Never write an API key to
   config, state, or logs. `providers/base.py` reads `api_key_env` and nothing
   else.
7. **The provider request stays minimal.** No `response_format`, tools, or
   streaming unless the project opts in per-provider. Self-hosted runtimes reject
   unknown fields rather than ignoring them, and local support is a requirement,
   not a nice-to-have.
8. **The CLI is the product.** The skill, the adapters, and the web UI are all
   callers of `cli.py`. Nothing may implement pipeline logic of its own — if the
   web UI needs behaviour the CLI lacks, add it to the CLI first.
9. **Never claim a translation passed without a green `lx check`.** The exit code
   is the evidence.

## Layout

```
src/scriptorium/
  mask.py        markup protection: ⟦n⟧ placeholders, DNT terms, repair of mangled brackets
  mdparse.py     markdown -> (skeleton nodes, segments); render() puts it back
  normalize.py   deterministic repair: punctuation width, CJK/Latin spacing
  checks.py      validators; error severity fails the build
  store.py       .lx/ state, content-addressed segments, translation memory
  config.py      layered config, glossary, do-not-translate list
  translate.py   batching, concurrency, JSON tolerance, per-segment retry
  providers/     openai_compat (primary), anthropic; base holds transport + retry
  web/           local review workbench, a shell over cli.py
skill/           Claude Skill packaging (SKILL.md + reference/)
adapters/        AGENTS.md and OpenCode rule, both thin pointers
```

## Commands

```bash
python -m pytest -q                 # 38 tests, no network
python -m ruff check src tests
python -m scriptorium --help        # or `lx` after `pip install -e .`

lx run docs/guide.md --lang zh-TW   # extract -> translate -> check -> repair -> render
lx web                              # review workbench on 127.0.0.1:8787
```

Run tests before proposing a change as finished. They are fast and cover the
round-trip property, which is the thing most likely to break silently.

## Conventions

- Public functions in `cli.py` prefixed `do_` are the API other surfaces call;
  `cmd_` functions are argparse handlers and should stay thin.
- Segment ids are per-document and sequential; the translation memory key is the
  content hash. Never key the memory on position.
- New language support: add a brief to `_LANG_BRIEFS` in `translate.py`, a
  normalization profile in `config.py`, and a reference file under
  `skill/reference/`.
- New format support: implement parse/render producing the same
  `(nodes, segments)` shape. Do not fork the pipeline.
- Tests use no network. Providers are exercised against a mock HTTP server in
  `tests/test_provider.py` — extend it rather than mocking `urlopen`.

## Style

Comments explain why, not what. Where a decision looks arbitrary, say what the
alternative was and why it lost. Error messages tell the reader what to do next
— `providers/base.py` is the reference for tone.

## Not in scope

Machine translation quality benchmarking, a hosted service, a plugin system, and
a GUI beyond the local review workbench. Proposals for these belong in an issue,
not a branch.
