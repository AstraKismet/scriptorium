# Contributing

Thanks for looking. This file covers the practical side: how to run the project,
what will get a change rejected, and how to send it.

`AGENTS.md` is the authoritative working agreement and holds the architectural
invariants. This file never restates a rule from it as a second source of truth —
where the two seem to disagree, `AGENTS.md` wins and this file is the bug.

## Setup

```bash
git clone https://github.com/AstraKismet/scriptorium.git
cd scriptorium
pip install -e .          # optional; provides the `lx` command
```

There are no runtime dependencies to install, and there will never be a compiled
one. Without installing, `python -m scriptorium` works in place of `lx`.

## Before you propose a change as finished

```bash
python -m pytest -q             # 253 passed; no network, no model
python -m ruff check src tests
```

Both must be clean, and CI runs them on Python 3.9 and 3.12 across Ubuntu and
Windows. The cross-platform half is not ceremony: line-ending fidelity is
something this project promises, so a change that is correct only on one platform
is not correct.

The tests are fast and they cover the round-trip property, which is the thing
most likely to break silently.

## Five things that will get a change rejected

These are the ones outside contributors trip over. All of them are decisions with
recorded reasoning, not preferences.

1. **Never edit a file in `tests/corpus/` to make a test pass.** Each one is a
   document with a property worth protecting. If a fixture fails, either the
   parser is wrong or the fixture is not valid input — decide which, and say
   which in the commit.

2. **No compiled extensions, ever, and pure-Python dependencies must be pinned
   and vendorable.** The pipeline has to run on a bare interpreter, in CI, inside
   an agent sandbox, and on a locked-down machine. Dev-only tooling is
   unconstrained.

3. **No DOM or AST re-serialization.** The output document is rebuilt by
   substituting translations into the original skeleton, never by re-rendering a
   parse tree. This is what excludes lxml, python-docx, ebooklib and every
   Markdown renderer, and it is why formatting survives byte for byte.

4. **The model never sees markup.** Support for new syntax is a new pattern in
   `mask.py`, not a new sentence in a prompt.

5. **A validator must be mechanically decidable.** A rule belongs in `checks.py`
   only if a program can decide it without judgement. Anything needing taste goes
   in the language brief or in human review.

Add to that one absolute: **never write an API key to config, state, logs or a
test fixture.** Keys are read from the environment variable named in
`api_key_env` and from nowhere else.

## Changing something architectural

The invariants in `AGENTS.md` are decisions with losing alternatives recorded in
`docs/decisions.md`. Changing one is a deliberate act that needs a new entry
there — stating what was chosen *and what lost* — rather than a drive-by
refactor. Read the existing entry before proposing to reverse it; the alternative
you have in mind may already be in there with the reason it was not taken.

Some things are deliberately out of scope and proposals to reopen them belong in
`docs/decisions.md` as a new entry, not in a branch: DOCX, the i18n file formats
(JSON, YAML, PO), XLIFF as an internal format, TMX as the memory format,
automatic application of fuzzy matches, and any desktop shell.

## Where things live

```
src/scriptorium/
  docio.py     document read/write as bytes
  mask.py      markup protection: ⟦n⟧ placeholders, tag pairing, DNT terms
  mdparse.py   markdown -> (skeleton, segments); render() puts it back
  normalize.py deterministic repair: punctuation width, CJK/Latin spacing
  checks.py    validators; error severity fails the build
  store.py     .lx/ state, content-addressed segments, translation memory
  config.py    layered config, glossary, do-not-translate list
  translate.py batching, concurrency, JSON tolerance, per-segment retry
  providers/   openai_compat (primary), anthropic
  web/         local review workbench, a shell over cli.py
```

`cli.py` is the product. The skill, the adapters and the web UI are all callers
of it, and none of them may implement pipeline logic of its own — if a surface
needs behaviour the CLI lacks, it goes in the CLI first. Public functions
prefixed `do_` are the API other surfaces call; `cmd_` functions are argparse
handlers and stay thin.

## Sending a change

Work on a feature branch and open a pull request; `main` stays linear via squash
merge.

Commit messages are in **English**, with a Conventional Commits prefix (`feat:`,
`fix:`, `docs:`, `chore:`, `refactor:`, `test:`). The body states the problem, the
change, and the verification — the last one concretely, with the command and its
result rather than an assurance.

If your change adds or removes tests, state the new case count.

## Reporting a bug

For anything about document fidelity, **attach the input file** rather than
pasting its contents — the interesting bugs in this project are invisible in a
paste, because they are made of CRLF, a BOM, a trailing space or a non-breaking
character. If you cannot attach it, `python -c "print(open('f.md','rb').read()[:200])"`
on the relevant region is the next best thing.

Include the `lx` command you ran, the exit code, and the output of
`lx check <src> --lang <lang> --json` if the pipeline got that far.

## Translations of the documentation

Tracked documentation is in English. `README.zh-TW.md` is the one translation and
is kept in step with `README.md` — a change to one that skips the other is a
defect, not a follow-up.
