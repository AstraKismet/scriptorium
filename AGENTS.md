# AGENTS.md

Working agreement for this repository. Read before changing code.

This file is authoritative. `CLAUDE.md` is a one-line pointer at it, so Claude
Code, Codex, Cursor and OpenCode all load the same rules.

## What this project is

**What it is for.** Translating **English novels into Traditional Chinese**. That
is the original and principal reason this project was built, and as of the
2026-07-29 review it is the use case every scope argument resolves against.
Technical documentation is a secondary use case and stays supported — its tests
stay green and its defects get fixed — but where the two compete, long-form
literary work wins.

A localization pipeline built on one architectural commitment: **the model
translates sentences; code does everything else.** Structural work — parsing,
markup protection, reassembly, terminology enforcement, punctuation
normalization — is deterministic and lives in Python. The model is called only
for the part that needs judgement.

Every design question resolves against that sentence. When adding a feature, the
first question is which side of the line it falls on. Novels do not challenge
that commitment; they shift the balance under it, making code's half smaller and
the model's half harder.

**Where it is going.** A personal translation workstation for long-form work:
quality, status tracking, source and output update propagation, text management.
Scope is **plain text, Markdown and EPUB** — plain text and EPUB are how novels
actually arrive. Translations come from three sources treated as equals — an API
model, an agent working in its own context, and a human — so a segment records
where its target came from, and review and audit are workflow stages distinct
from translation.

The consequences of the 2026-07-29 re-founding — paragraph segmentation, what the
translation memory is for, where register lives, neighbour context, and the queue
order that follows — are in `docs/decisions.md`, "Novels are the primary use
case, and the six things that follow from it".

Reasoning for all of it is in `docs/decisions.md`. Read that before proposing an
architectural change; it records the alternatives that lost.

## Invariants

These are decisions, not preferences. Changing one is a deliberate act that needs
an entry in `docs/decisions.md`, not a drive-by refactor.

1. **Portability, not dependency count.** No compiled extensions, ever. Pure
   Python dependencies must be pinned and vendorable. The pipeline has to run on
   a bare interpreter, in CI, inside an agent sandbox, and on a locked-down
   machine — those four situations are what this protects, and a C, Rust or C++
   extension breaks all four. Dev-only tooling is unconstrained.

2. **Structure is preserved by construction, never by checking.** Two layers:

   **(2a) Skeleton.** Every byte the pipeline did not deliberately change is
   reproduced as-is. **No DOM or AST re-serialization is permitted** — this is
   what excludes lxml, python-docx, ebooklib, ruamel and every Markdown renderer.
   For container formats the guarantee is: the decompressed content of unmodified
   entries is byte-identical, entry order is preserved, and an EPUB `mimetype`
   entry is first and STORED. Byte-identity of the container itself is not
   claimed, because modified entries must be recompressed.

   Skeleton raw nodes are stored as **BLOB, never as JSON text.** A source file
   containing invalid UTF-8 — routine for older Big5, GBK and Shift-JIS text —
   cannot be written to a JSON state file at all.

   The file boundary is part of this. Documents are read and written as bytes
   through `docio.py`, never through Python's text mode, because universal
   newlines deletes every CR on the way in and `os.linesep` is manufactured on
   the way out — neither of which the pipeline decided. Machine-written files
   (`config.py`, the `.lx/` JSON state) are excluded on purpose: no invariant
   claims their bytes.

   **(2b) Substitution.** Every slot carries its host syntax's escaping function
   and containment rules. A translated segment may not introduce a block-start
   sequence, must escape `&`, `<` and `]]>` inside an XML host, and must keep
   paired placeholders present and non-crossing.

   If a "did the headings survive" check ever seems necessary, (2a) has been
   broken and that is the bug. (2b) is different: it is checked, because the
   model's output is not under our control.

3. **The model never sees markup.** Anything non-translatable is masked to `⟦n⟧`
   before a request is built. New syntax support means a new pattern in
   `mask.py`, not a new instruction in a prompt.

   *Known gap, measured:* `**bold**`, `_italics_`, `~~strike~~` and link-text
   brackets currently reach the model unmasked. The direction is to finish the
   masking, not to weaken the rule.

   *Deliberate exception, not a gap:* a wrapped block's interior line breaks and
   the indentation that follows them are inside the segment, so the model does
   see them — 149 of 1467 segments across the tracked documentation. They cannot
   be masked or held in the skeleton without splitting one wrapped sentence into
   several segments. `docs/decisions.md`, 2026-07-28, "Where a line terminator
   lives", records why that alternative lost. Do not "fix" this by stripping
   them; that is the round-trip defect repaired on the same date.

   The indent is not always interior. A list item's second paragraph is
   `- item\n\n    text`, so its four spaces sit at *position 0* of the segment,
   and deleting them takes the paragraph out of the item. An indented code block
   used to arrive the same way and no longer does — it became skeleton on
   2026-08-02, which is what `mask.py` could not do for it. See
   `docs/decisions.md` of that date, "An indented code block is skeleton".
   The two containers that rule left behind — a chunk inside a blockquote, and an
   indented run of fence characters, which swallowed every paragraph after it —
   closed on 2026-08-03, same file, "A quoted chunk is skeleton".

   Because that run is inside the segment and the model may not reproduce it, the
   **blanks a segment opens and closes with are re-imposed from the source** on
   every proposal — `normalize.reseat_outer_blanks`, shared by `translate.accept`
   and `cli.do_apply`. The trailing end is the same rule for a different reason:
   `mdparse` emits one segment per blockquote line, so a hard break's two spaces
   sit at a segment's end with the newline that means them in the skeleton. See
   `docs/decisions.md`, 2026-08-03.

4. **Checks are mechanically decidable.** A rule belongs in `checks.py` only if a
   program can decide it without judgement. Anything requiring taste goes in the
   prompt or in human review. Context-dependent vocabulary is judgement — that is
   why the locale lexicon is being narrowed rather than expanded.

5. **Fix rather than report where possible.** If a defect can be corrected
   deterministically, `normalize.py` corrects it on ingest. Reporting a fixable
   defect wastes the reviewer's attention.

6. **Credentials come from the environment only.** Never write an API key to
   config, state, or logs. `providers/base.py` reads `api_key_env` and nothing
   else.

   Since 2026-08-12 the invariant is also held from the writing side, because
   `lx config set` exists: `api_key_env` takes the *name* of a variable and
   refuses anything shaped like a key, a `base_url` carrying userinfo **or a
   query string** is refused, and `providers.*.headers` — sent to the backend
   verbatim — is not writable from the command line at all. **A refusal on any of
   those never echoes the value**, and no `lx` command takes key material on a
   command line, because argv is in a process listing and in shell history before
   a refusal can run. Every display surface — `lx config get`, `lx providers`,
   `/api/state` — shares `config.printable_url`, or two commands disagree about
   what is printable over one value.

   **A display surface is any place a value can be read, not the list of places
   that look like a report.** Two were missing from the list above and were found
   on 2026-08-13, by the security-tier pass over the frozen workbench contract:
   `Provider.describe()`, whose line is the first thing `lx translate` prints and
   the first entry of `POST /api/job`'s `log`; and the transport failure message,
   which reaches the same job's `error`. Both interpolated the raw `base_url`, so
   a hand-edited `https://user:SECRET@host/v1` was masked by `lx providers` and
   printed in full by the run beside it. Both go through `printable_url` now. The
   enumerated list is a symptom of the rule and never its definition — when a new
   surface can show a configured value, it joins the list.

   A rule is enforced where a field **lands**. A key may not be addressed *inside*
   something that holds one value, whether the field table says so or the merged
   configuration's own type does: without that, `providers.new.api_key_env.x`
   wrote a raw credential with no rule consulted, and `batch.size.x` replaced a
   number with a block. The rules, and the two shapes that shape alone does not
   catch, are in `docs/decisions.md`, 2026-08-12.

7. **The provider request stays minimal.** No `response_format`, tools, or
   streaming unless the project opts in per-provider. Self-hosted runtimes reject
   unknown fields rather than ignoring them, and local support is a requirement.

8. **The CLI is the product.** The skill, the adapters, and the web UI are all
   callers of `cli.py`. Nothing may implement pipeline logic of its own — if the
   web UI needs behaviour the CLI lacks, add it to the CLI first. The frontend
   talks plain JSON over HTTP and shares one type definition file; **typed RPC
   frameworks are excluded**, because their value comes from the same force that
   pulls logic into the server.

   Since 2026-08-13 that HTTP surface is **frozen and versioned**:
   `docs/contracts/workbench-http.md` is the contract, `/api/state` reports its
   `contract_version`, and `tests/test_contract.py` fails when the document and
   the server disagree about which endpoints exist, which `cli.do_*` each stands
   in front of, or what the surface deliberately does not carry. Changing the
   surface is now an edit to two files and a version decision. The freeze
   describes what is true rather than what should be: seventeen measured
   divergences were recorded in the contract's own *Known divergences* section
   rather than fixed there, and four of them were this invariant's — two where
   the server had behaviour the CLI lacked, two where the two surfaces answer the
   same question differently. Two of the seventeen were live defects reproduced
   on the wire while the contract was being written, which is the argument for
   having written it: `docs/decisions.md`, 2026-08-13.

   Those numbers are a history and not an inventory. A divergence closed since is
   marked `Closed` in place and keeps its number, and new ones are appended, so
   the list only grows — read the section rather than this paragraph for what is
   outstanding. (1) and (13) closed on 2026-08-14, which leaves one of the two
   server-only behaviours: (4), the job endpoint, which the contract argues is a
   structural CLI gap rather than leaked logic — its two named debts, an id that
   does not depend on `len(_JOBS)` and a retention rule, were paid on 2026-08-15
   as (9). (2) and (3) closed on
   2026-08-15, additively and with no version move: the segment-selection rule is
   `cli.do_select` now and both surfaces call it — **the CLI was aligned to the
   wire**, because the mirror settlement bumps — and the endpoint gained the
   `model` the CLI already had, plus a readback of the route it resolved, since
   the only other place that answer appeared was a log line the contract forbids
   parsing. The run itself moved with the selection: `cli.do_translate` is the
   one copy of it, so the per-batch write the two surfaces had each assembled
   cannot drift apart again. (22) and (23) were appended on 2026-08-16, both
   closed: `POST /api/check` had carried a whole stale snapshot back over newer
   text, and nothing on the surface could say "leave this segment to me". (24)
   and (25) were appended beside them and both **closed on 2026-08-17**, in
   `POST /api/extract`: a stored target the acceptance path refuses is kept
   rather than deleted, and two segments whose source text is byte-identical are
   told apart by position instead of collapsing onto one carryover entry and
   laundering each other's `origin`. (26) and (27) were appended by that work and
   are **open** — a run of identical paragraphs that changed size is still told
   apart by nothing, and a memory hit still answers over wording the document was
   holding, taking its `origin` with it. Read the second before relying on origin
   precedence: it is the remaining path that rewrites the field the rule
   compares. Both are **named on both surfaces** since 2026-08-19 — `lx extract`
   prints the segments it happened to and `POST /api/extract` returns them —
   which closes their reporting half and neither of the entries. (28) was
   appended the same day by the adversarial pass over that work and is **open**:
   `POST /api/extract` type-checks neither `reset` nor `tone`, so the *string*
   `"false"` is a reset that discards a document's translations. It is recorded
   rather than repaired because refusing it narrows an accepted value set, and
   that bumps.
   `contract_version` moved to **2** on 2026-08-14, once, carrying five items: the `candidates` → `untracked` rename,
   the identity label normalized, `status` derived from the target text, an empty
   target refused, and a lost-update token. It closed (13)'s wire half, (14), (17)
   for a client that opts in, (19) and (21), and decided (18) and (20). It moved
   to **3** on 2026-08-19, the first bump through the gate and scheduled as a work
   package, carrying **one** item and no more: `POST /api/extract` refuses
   `reset: true` with no `tone` and answers 400, because a reset reads no prior
   row and so cannot keep the register the document was frozen in — it refroze
   silently to the configured default, and the register is a field of the memory
   key. The three arrays that landed beside it — `kept`, `ambiguous`, `replaced`
   — are new response keys and did not need the move; they rode along because the
   same section was being rewritten. Every further bump is gated behind a work
   package rather than a commit, and that gate is unchanged.

   **A second contract froze on 2026-08-19, and it is this invariant's rather
   than the workbench's**: `docs/contracts/status-json.md` covers
   `lx status --json`, the surface the bookshelf-and-reader project consumes.
   Its consumer may not read inside `.lx/` and may not call the Python API, so
   the completeness bar is higher than the HTTP contract's — anything missing
   from it is a reason for somebody to go around it. It freezes `cli.do_status`
   as its seam the way each endpoint's *Backed by* line does, and its
   `contract_version` is a **separate integer** from the workbench's, on a
   separate schedule; a test asserts the two constants have not become one. The
   gate is the same: a bump is a work package, not a commit.

9. **Nothing regenerable is a source of truth.** Working state (SQLite) and
   approved wording (`.lx/tm.*.jsonl`) are sources of truth. JSON over HTTP is a
   projection. Rendered documents and any XLIFF export are rebuildable artifacts.

10. **Never claim a translation passed without a green `lx check`.** The exit
    code is the evidence.

    It was measured, on 2026-07-27, to be unreliable in both directions: it
    passed five structural-damage cases and failed five correct Traditional
    Chinese sentences. Both halves were repaired on 2026-07-28 — the zh-TW table
    audited against invariant 4, and the containment validators added at error
    severity — and all ten are fixtures now. Be exact about what the exit code
    claims even so: that the structure survived and the mechanical rules passed,
    never that the translation is good.

11. **An untrusted path is confined before it is opened.** Any path the user did
    not type at a terminal — one that arrived in an HTTP request, was read out of
    a configuration file, or is an entry name inside a container — goes through
    `cli.confined_path` before anything opens it: both sides resolved with
    `os.path.realpath`, compared with `os.path.commonpath`, **rejected rather
    than clamped**, and the caller's own string handed back rather than a
    canonicalized one. A CLI argument is the named exception, because
    `lx render doc.md -o /tmp/out.md` is a person typing a command.

    This is a rule about where a path *came from*, not about which module is
    reading it. Three places it already binds and one of them is not written yet:
    every endpoint in `web/server.py`; an EPUB entry name, where the same defect
    is called zip-slip; and `output_pattern`, which is trusted today only because
    configuration is written by hand — the moment anything writes configuration
    over HTTP, that trust is gone and the pattern is confined at render time or
    it is not writable over HTTP.

    *Why it is an invariant rather than a note about the workbench:* the version
    scoped to the web surface would have expired the day HANDOFF-204 rewrote it,
    and would have bound neither of the other two. Measured cases and the losing
    alternatives are in `docs/decisions.md`, 2026-07-29.

## Layout

Current:

```
src/scriptorium/
  docio.py       document read/write as bytes; encoding detection; text mode never
                 touches a user document
  formats.py     the format registry: extension -> parser, and each format's knobs
  mask.py        markup protection: ⟦n⟧ slot records, tag pairing, DNT terms, bracket repair
  mdparse.py     markdown -> (skeleton nodes, segments)
  textparse.py   plain text -> the same pair; encoding, paragraph and chapter heuristics
  skeleton.py    render(): nodes + targets -> document, for every format at once
  normalize.py   deterministic repair: punctuation width, CJK/Latin spacing
  checks.py      validators; error severity fails the build. Invariant 2b lives
                 here: block-start containment, host escaping, placeholder pairs
  store.py       .lx/state.db (document state, SQLite), the translation-memory
                 key, the memory itself (.lx/tm.*.jsonl, still JSONL and tracked)
  config.py      layered config, glossary, do-not-translate list, style sheet;
                 dotted-key addressing, the atomic config writer, and
                 `resolve_route` — the one answer to "which backend, which model"
  translate.py   batching, concurrency, JSON tolerance, per-segment retry
  providers/     openai_compat (primary), anthropic; base holds transport + retry
  web/           local review workbench, a shell over cli.py
skill/           Claude Skill packaging (SKILL.md + reference/)
adapters/        AGENTS.md fragment and OpenCode rule, both thin pointers
docs/            decisions.md (the record), conventions/, contracts/,
                 windows-setup.md
handoff/         work package queue — gitignored, see below
```

Target, once the restructure package lands: one repository, two internal
packages. `core/` is the engine and CLI — the artifact another repository can
vendor into `tools/`. `studio/` is the workstation. The boundary is drawn now
because drawing it early is nearly free.

## Commands

```bash
python -m pytest -q                 # 1206 tests; no network (one is POSIX-only,
                                    #   one runs only where the filesystem folds case)
python -m ruff check src tests
python -m scriptorium --help        # or `lx` after `pip install -e .`

lx run docs/guide.md --lang zh-TW   # extract -> translate -> check -> repair -> render
lx web                              # review workbench on 127.0.0.1:8787
lx status --json                    # the frozen project-status contract
lx status --scan ~/books            # every project under a root
```

Run tests before proposing a change as finished. They are fast and cover the
round-trip property, which is the thing most likely to break silently. That
property is exercised by `tests/corpus/` for Markdown and `tests/corpus-text/`
for plain text — one input file per property, read as bytes and substituted back
into the skeleton without going through `render()`. The plain-text corpus is
compared as bytes *through its detected encoding*, because for that format the
encoding is part of the format. Each directory holds one syntax and a test
asserts it: a `.txt` dropped into `tests/corpus/` would be read by the wrong
parser and still pass.
Every fixture passes; `KNOWN_BROKEN` in `tests/test_pipeline.py` is empty and
should stay that way. An entry there marks a measured defect with a repair
scheduled, is `xfail(strict=True)`, and turns the suite red once the defect is
fixed — which is how the entry gets removed in the same commit as the fix.
**No fixture is ever edited to make a test pass** — if one fails, either the
parser is wrong or the fixture is not valid input.

## Handoff work packages

Cross-session scheduling. **One handoff file is one scheduled work package**; a
new session is pointed at a file, executes it, and deletes it on completion.
Location `handoff/`, gitignored — queue state is not versioned. The convention is
versioned: this section plus `docs/conventions/handoff-workflow.md`.

This section is authoritative for the red lines and the pickup rule; that
document is authoritative for reasoning, lifecycle and failure modes. They must
agree.

```
handoff/
  00-inbox/    newly scheduled, not yet ordered
  10-now/      current milestone   (ids 001–099)
  90-later/    committed, not imminent (ids 201–299)
```

**Pickup rule.** Lowest folder lexicographically → lowest `priority` (1 highest,
ties by id) → **skip anything whose `blocked-by` is not fully cleared**. Start a
session with "Execute `handoff/<folder>/HANDOFF-xxx`" or "Take the next
executable handoff".

**Red lines.**

- **Claim before working.** Set `status: in-progress (<date>)` first, or two
  sessions take the same package.
- **A package must be self-contained.** The executing session has no memory of
  the one that wrote it. Distil decisions, contracts and red lines into the
  package. `90-later/` may use pointers, but completing the distillation is part
  of promoting a package, not a follow-up.
- **Acceptance criteria state a command and its expected exit code.** Prose is
  not an acceptance criterion.
- **Done means deleted, and deleting is the deadline.** Delete on passing;
  anything still uncertain goes into the next package. Everything the package
  deferred to another one must already be written *into* that package, and any
  neighbouring package this work made stale must already be corrected — a
  deferral that exists only in the deleted file's OUT list did not happen.
- **Decisions reach `docs/` before the package is deleted.** Packages are
  deleted; `docs/decisions.md` is not.
- **Ids are never reused**, not even for a cancelled package.
- **Never put a convention, guide or ledger in `handoff/`** — the `HANDOFF-*`
  glob will mistake it for a package, and the directory is outside version
  control.
- **A closing report ends with the handover, not with the result.** Two named
  sections, always, even when both are empty: **what is left undone**, split into
  what this package owed and what it uncovered, each with the package id or
  `docs/` entry it now lives in and anything still waiting on the maintainer; and
  **whether to continue in this session or a new one**, with the reason and — if
  a new one — the exact opening line to paste. Without them the maintainer has to
  ask both questions every time, and the answer they get is reconstructed after
  the fact rather than recorded while the work was still in view. Reporting
  "done" is not a handover; a queue this convention exists to keep moving needs
  to say where it moved to.

`blocked-by` kinds: `user:`, `package: HANDOFF-xxx` (clears automatically when
that package is deleted), `data:`, `design:`, `external:`.

Labels: `core`, `formats`, `quality`, `store`, `provider`, `cli`, `web`, `infra`,
`docs`, `review-backlog`.

## Delegated work

When work is split across more than one worker — a second person, a second
session, or several assistant processes at once — `docs/conventions/delegated-work.md`
governs. Three rules from it are red lines and are repeated here so they are not
missed: **shared seams** (`mask.py`, `checks.py`, `mdparse.py`, `store.py`) are
edited by one worker in one place, never concurrently; **a brief carries its own
context**, because a delegated worker substitutes a plausible guess for every
decision that was not distilled into it; and **work produced below the capability
tier its category requires is marked at the output and logged**, never absorbed
silently. That file also holds the downgrade ledger.

A fourth, added 2026-08-02 after it cost a session's uncommitted work: **a worker
that writes files runs in its own git worktree, and the shared checkout is
committed-clean before it is dispatched.** Reverting an edit is
`git checkout -- <file>`, which discards a human's unstaged changes alongside the
worker's own — it cannot tell them apart. Read-only delegation is exempt and is
most of what happens here, which is exactly why the case surprises people.

A fifth, added 2026-08-03 after two consecutive packages: **a sweep is blind to
the axis it does not vary, so record the axes beside the number and hand the
claim to an adversarial pass.** Scaling inside the dimensions you chose never
reaches one that is absent, and a large count reads like proof. HANDOFF-018 swept
37224 documents, reported 0, and review found four regressions on the one axis
held constant; HANDOFF-019 swept 441 across five named axes, reported the
trailing side harmless, and twenty cases on the axis it held constant found six
structural shapes. Its adversarial pass then found two regressions the *repaired*
code had introduced — one of them a validator silently blinded by the repair —
which neither the sweep nor a green mutation run could see, because both were
aimed at the code the package had thought about. `docs/conventions/delegated-work.md`
§6.7 has both halves — reviewing someone else's measurement, and distrusting your
own.

## Git and commits

- Remote is `github-astrakismet:AstraKismet/scriptorium.git`. **The alias is not
  optional** — the default SSH key on the development machine resolves to a
  different account that is not an organization member, and the push is rejected.
  Verify with `ssh -T github-astrakismet`.
- Feature branch → pull request → squash merge. `main` stays linear.
- Commit messages in **English**, with a Conventional Commits prefix
  (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`). The body states the
  problem, the change, and the verification. `Co-Authored-By` trailers are kept —
  they record which model, which is information that varies.
- Releases are GitHub Releases on `v*` tags. Not published to PyPI.
- Never write an API key anywhere. `.env` and `*.local.*` are ignored; keep it
  that way.

## Conventions

- Public functions in `cli.py` prefixed `do_` are the API other surfaces call;
  `cmd_` functions are argparse handlers and should stay thin.
- Invariant 11's mechanics, which the invariant itself does not fix. The helpers
  live in `cli.py` (invariant 8) and are called from the surface that receives the
  path, never from the CLI. They apply **by presence of the field, not by endpoint
  name**, so one added later cannot skip the check by being new. Why the helper
  returns the caller's string rather than the resolved one: every document
  identity here is `os.path.relpath(src)` against `os.getcwd()`, so a resolved
  path silently becomes a second document — measured under a junction and under
  an 8.3 short-name cwd. `lang` gets `cli.language_tag` instead, because it is not
  a path but a filename *component* that `report_path` and `tm_path` interpolate;
  a language tag has a decidable shape, so a whitelist refuses every separator by
  construction. It stayed a whitelist after document state moved into SQLite, where
  `lang` is a column value: two of the three paths it feeds are still files, and a
  check that narrows as storage changes is a check nobody can rely on.
- A document's line terminator is a document-level fact, held in `doc["eol"]` and
  re-imposed once at render — never carried inside a segment, where the model and
  the reviewer would both have to reproduce a control character neither can be
  checked on. A state file without the key means `"\n"`. Documents whose
  terminators are already mixed are the recorded exception and pass through
  verbatim; see `docs/decisions.md`, 2026-07-28.
- Segment ids are per-document and sequential. The translation memory key is
  `(content_hash, context, segmentation_version)` plus a nullable `variant` and
  the register — never position. `variant=null` must hash identically to the
  field's absence, or the entire memory invalidates; it is a tuple of read fields
  for exactly that reason, so the property holds by construction rather than by a
  canonicalizer. `context` is gettext's `msgctxt`; for Markdown and for plain text
  it is the block kind, and it is stored beside `kind` rather than derived from it, because a key
  path or a spine position has no `kind` to borrow. A record with no
  `segmentation_version` predates the field, matches on content alone, and is
  marked `tm:legacy`.
- The register is `doc["tone"]`, threaded into the key as a parameter and never
  stored on a segment — the rule `doc["eol"]` follows, and for the same reason.
  It is the one key field whose null is a *string*: the default register,
  `null`, and the field's absence are one value, which is what keeps every entry
  banked before the axis existed answering. That collapse cannot hold by
  construction the way `variant`'s does, so it lives inside `tm_key` where no
  caller can skip it. A document in a non-default register is not offered the
  `tm:legacy` tier at all. See `docs/decisions.md`, 2026-07-29.
- Translation memory hits go through the same acceptance path as model output.
  Writing a target directly is how a stale mask configuration renders a bare
  `⟦2⟧`. The key is deliberately blind to the mask configuration — that is what
  keeps one wording one entry across machines — so `translate.accept` is what
  makes the blindness safe. Carryover from a document's own prior state is a
  proposal on the same terms, and the memory is tried when it is refused.

  **A wording is repaired into the numbering it has to speak in, before it is
  judged.** `mask.reseat` unmasks a proposal against the map its placeholders
  were written in and seats the segment's current originals back in **by
  content** — never by a second call to `mask`, which numbers by position and so
  silently swaps two code spans a translation reordered. `translate.accept` takes
  that map as `slots=`. The map itself is pinned to the *wording*, in the
  segment's `body` as `target_slots`, because `save_doc` rewrites `slots` from
  the fresh parse on every extract and a rule that reads provenance off the
  segment is a guard that fires exactly once. **A memory line carries its own map
  too**, as `slots` — the originals in id order — so a hit is repaired by the same
  function; a line banked before that field existed is offered only where a
  renumbering could not have moved it, which is decidable because `mask` numbers
  inline matches before terms and a markup slot's id is a pure function of the
  source text. `docs/decisions.md`, 2026-08-17.

  **A refusal does not delete what the segment already held.** Since 2026-08-17,
  and this is the line between the two: the gate answers whether wording may be
  *written into* a segment as a translation, and `lx extract` had been reading it
  as licence to delete what was there. The refused wording stays with its
  `origin` and its `review`, the segment comes back `translated` and failing, and
  `lx check` reports it — the rule below for a person's words, applied to the
  path that was destroying them. The cost is that `lx render` on a document
  `lx check` has failed writes the stale `⟦n⟧` into the output; `lx run` refuses
  to render at all. `docs/decisions.md`, 2026-08-17, and
  `docs/contracts/workbench-http.md` divergence (24).

  **Which stored entry a re-parsed segment inherits is decided by position**, in
  the one place that has one — the document's own prior state, never the memory
  key. `store.Carryover.align` **diffs the stored key sequence against the fresh
  one** and takes the matching blocks; what it cannot place falls back to the
  last stored wording under that key, without its hold, and is named by
  `lx extract`. Without this a document holding one sentence twice held one entry
  for two positions and the last row read filled both, carrying its `origin`:
  divergence (25), and the hole under origin precedence that needed no race.

  Two simpler spellings were built and both were wrong — by segment id, which a
  single insertion defeats and a deletion turns into laundering, and by ordinal
  within a key's run, whose size check compared translated rows against parsed
  segments. If a third is ever proposed, the measurement is in
  `docs/decisions.md`, 2026-08-17: twelve edit shapes, scored position by
  position. What the diff still cannot do is recorded as (26), the memory
  answering over the document's own wording as (27), and both are reported rather
  than silent.

  `lx apply` is the deliberate exception, and only for *refusal*: a person's words
  are reported at `lx check`, not rejected at the door. **An empty target is not
  words.** Since 2026-08-14 `do_apply` refuses one, for the whole request, and
  names `lx translate --ids` instead — the exception protects *content* from a
  mechanical rule, and an empty string is the absence of content. Left storable it
  combines with status-derived-from-text and origin precedence into a segment
  every run selects, no writer may write and `lx check` can never pass; refusing
  at the door makes that unreachable rather than guarded against in three
  predicates. The refusal lives in `do_apply` rather than at the endpoint so that
  the CLI cannot walk around it. It shares
  `reseat_outer_blanks` all the same, because a run of blanks at a segment's edge
  belongs to the host syntax rather than to whichever of the three sources wrote
  the target — closing that half on 2026-08-03 was what stopped one document
  rendering differently depending on who translated it.
- **An `llm:*` write does not land on a segment whose stored `origin` is
  `human`.** Since 2026-08-15, and enforced inside `store.save_targets` and
  `store.save_segments` rather than at the call sites, because all three writers
  — `cli.do_apply`, the per-batch commit, and `do_check`'s persist path — pass
  through those two. The comparison is made **inside the write**, against the
  origin on disk at that moment, for the reason the lost-update token was
  rewritten on 2026-08-14. The refused ids are returned rather than dropped: a
  run reporting "translated 40" while having skipped four is a report nobody can
  act on. `over_human` is the opt-out, spelled `--overwrite-human` on the CLI and
  `overwrite_human` on the wire.

  It singles out one of the three equal sources on purpose. An `agent` write is a
  peer's own words and is unguarded, as is a person over a person; what this stops
  is the *unattended* pass, which runs over whatever the queue hands it.

  **Selection knows the rule too**, since 2026-08-16 and not before: `do_select`
  drops what the write would refuse, from every branch except an explicitly named
  `ids`. Without that the guard was the only line of defence and the queue could
  not see it — `lx repair` paid a model for a segment it then refused and exited
  0 with the error count unmoved, and `lx translate --mode polish` on a reviewed
  novel selected the whole book and applied none of it. A rule enforced at the
  write and invisible to the queue is a rule that costs money on every run.

  The guard's read and its write share **one transaction**, and that needs saying
  because it is not what the code looks like: Python's `sqlite3` defers `BEGIN`
  to the first statement that *writes*, so every read-then-write in `store.py`
  ran its read in autocommit until `store._begin_write` was added. A second `lx`
  process drove a human target through that window and the run reported that it
  had refused nothing.
- **A hold is a `review` field with a closed vocabulary**, spelled `held`, with
  `lx hold` / `lx unhold` and `POST /api/hold`. It lives in the segment's `body`
  JSON the way `origin` does, so it cost no `SCHEMA_VERSION` and no
  `STATE_VERSION`. Three rules hold it together and each closes something:
  **holding requires a non-empty target**, which is what makes it compose with
  status-derived-from-text instead of fighting it; the exclusion from work
  selection is added **once, in `checks.workable`**, and applied at every
  predicate that selects work — `translate.failing_segments` included, which is
  status-blind and would otherwise feed a held segment back to the model on every
  repair round; and **lifting is the hold control's own act**, never a side
  effect of a save, so `do_apply` carries the field through untouched.

  `checks.py` reports a held segment at **warn**, never error: a severity that
  failed the build would make lifting every hold the only way to finish a book.
  And an explicitly named id still reaches a held segment — holding says no
  *queue* may take it, and `do_apply`'s own refusal message tells a reviewer to
  run `lx translate --ids <id>`, which a hold swallowing it would make false.
- The project style sheet (`config/style.txt`) says how *this book* sounds, where
  the register brief says how the target language's prose is written. Its two
  halves are injected differently and that is the design, not an accident: the
  preamble is document-static and goes in the system prompt after the brief, so
  that string stays byte-identical for every request of a run; a `[name]` block
  is per-batch and goes in the user message beside the required terminology,
  where per-batch content already lives. **Nothing inside a block is parsed** —
  deciding whether to send one is mechanical, deciding what good narration sounds
  like is judgement, and invariant 4 is the line between them. A fielded format
  was the alternative and it puts the second one inside `config.py`. Selection is
  against the whole batch rather than each segment, because a batch is a scene.
  See `docs/decisions.md`, 2026-08-02.
- One matcher answers "does this text contain this name" — `translate.mentions`,
  used by the glossary hints, the style sheet and `lx todo` alike. It had grown
  three copies before the style sheet would have made a fourth, and one of them
  was untested. Its boundary class reaches past ASCII on purpose: with
  `[A-Za-z]`, `Ana` matches inside `Anaïs`.
- New language support: add a `(language, register)` entry to `_LANG_BRIEFS` in
  `translate.py` for each register, with the register-independent terminology in
  `_LANG_TERMS` — one string shared, never a copy per register — plus a
  normalization profile in `config.py` and a reference file under
  `skill/reference/`.
- New format support: implement `parse(text, dnt, opts) -> (nodes, segments)` and
  register it in `formats.py`. `render` is shared from `skeleton.py` and knows
  nothing about syntax; a format supplies only its untranslated marker, its
  encoding candidates and its config defaults. Do not fork the pipeline. Lookup is
  by extension, overridden by `formats.map` in config and by nothing else — the
  format is frozen onto the document as `doc["format"]` at extract, because a
  skeleton is only readable by the parser that wrote it. An unknown extension is
  refused rather than guessed. The registry serves formats whose document is one
  decoded string; a container format — EPUB — widens it rather than squeezing in.
- A slot is a record — `original` / `role` / `pair_id` / `can_reorder` — and each
  document row carries `state_version`, which `store.py` refuses to read when it
  is older than the build. That is the *content* version, and it is separate from
  `PRAGMA user_version` (`SCHEMA_VERSION`), the database's own shape: a newer
  content version is escapable with `lx extract --reset --tone <register>` on the
  one document — the register has to be named because the reset does not read the
  row it would have come from — and a newer schema is not escapable at all and is
  refused at the connection. See
  `docs/decisions.md`, 2026-08-02. A format whose markup pairs must emit those
  records from its own masking step; entering a segment without them is what
  multiplies the "green but broken" rate with every format added. The model still
  sees a bare `⟦n⟧`: the type lives beside the slot map, never inside the token.
- A `routing` value is a provider name or `{"provider", "model"}`, and the bare
  string is never migrated to the object form — every configuration on disk uses
  it. One function answers which backend and which model a stage uses,
  `config.resolve_route`, and `translate.py`, `cli.py` and `web/server.py` all
  call it rather than reading `cfg["routing"]`: three sites resolving this
  independently is how the workbench and the CLI come to describe different runs.
  Most specific first — `--model`, the entry's model, the provider's — and a
  `--provider` naming a *different* backend drops the entry's model, because a
  model id belongs to the backend that serves it. An absent stage still falls
  back to `draft`; a present but malformed entry is refused rather than rerouted.
  `config.ROUTING_STAGES` is the one list of stages, read by `--mode`'s choices
  and by `lx routing set` alike. See `docs/decisions.md`, 2026-08-12.
- `lx config set` validates before it writes, so a refusal leaves the file byte
  for byte. It edits the *raw* file rather than the merged configuration, which
  is what lets a key from a newer build survive an older build's write and keeps
  the file holding only what somebody chose. A rule is applied where a field
  **lands**, never where it was addressed — writing a JSON block must not walk
  around the rule that owns a leaf inside it, the same guarded-by-presence rule
  `web/server.py` follows for `src` and `lang`.
- Fuzzy matches are advisory. **They are never applied automatically** — a fuzzy
  hit differs in its placeholder set by definition.
- Tests use no network. Providers are exercised against a mock HTTP server in
  `tests/test_provider.py` — extend it rather than mocking `urlopen`.
- All tracked documentation is in English. `README.zh-TW.md` is the one
  translation, kept in step with `README.md`.

## Style

Comments explain why, not what. Where a decision looks arbitrary, say what the
alternative was and why it lost. Error messages tell the reader what to do next —
`providers/base.py` is the reference for tone.

## Not in scope

Machine translation quality benchmarking, a hosted service, a plugin system, and
any UI beyond the local review workbench.

Deferred indefinitely, recorded so they do not creep back: DOCX; the i18n formats
(JSON, YAML, PO); ODT; XLIFF as an internal format; TMX as the memory format;
a full ICU MessageFormat parser; automatic application of fuzzy matches; and
desktop shells such as Tauri, Wails or Electron.

A graphical bookshelf and reader is a **separate project**. It consumes exported
documents and the `lx status --json` contract, and nothing else — it may not read
inside `.lx/` and may not call the Python API, so that this project stays free to
change its storage layer or its language.

That contract exists since 2026-08-19: `docs/contracts/status-json.md`, at
`contract_version = 1`. **Project discovery is on this side of the line** —
`lx status --scan ROOT` returns the projects under a root, because a consumer
told to look for `.lx/` would have been handed the one thing the restriction
withholds. What the surface deliberately does not carry is written down in it and
is as load-bearing as the field tables: no timestamp of any kind, because none
exists in the state and every filesystem proxy for one is moved by the act of
reading it; no provider, `base_url` or `api_key_env`, so invariant 6 is held here
by carrying nothing rather than by masking something; and no segment text at all.
See `docs/decisions.md`, 2026-08-19.

Proposals to reopen any of these belong in `docs/decisions.md` as a new entry,
not in a branch.
