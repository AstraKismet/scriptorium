# The project status contract

```text
contract_version = 1
```

What `lx status --json` emits. It is frozen here so that a separate
bookshelf-and-reader project can present a multi-project overview — which book,
how far along, in which languages, with what outstanding — without this
repository growing a UI, and so that a change to the shape becomes a deliberate
versioned act instead of a side effect of editing `cli.py`.

**This is not the workbench HTTP contract.** That one is
`docs/contracts/workbench-http.md`: a read-write surface for a review client,
spoken over HTTP by `lx web`, with its own `contract_version` on its own
schedule. This one is a read-only projection emitted by a command. Different
consumer, different red lines, and the two integers are unrelated — a client
reading one as the other would watch its contract jump for a change to a surface
it never calls. In the code they are `web.server.CONTRACT_VERSION` and
`cli.STATUS_CONTRACT_VERSION`, two constants in two modules, and a test asserts
they have not become one. The pair were misread as a single contract during
triage on 2026-07-29, which is why both documents say so out loud.

**The consumer's red line, and the whole reason this document exists.** The
bookshelf project consumes exported documents and this contract, and nothing
else: it may not read inside `.lx/` and it may not call the Python API
(`AGENTS.md`, *Not in scope*; `docs/decisions.md`, 2026-07-29). That restriction
is what keeps this project free to change its storage layer or its language
later. It only holds if this surface is complete enough that nobody is tempted
around it — so where a field is missing, this document says whether that is a
decision or a debt, and *Deliberately not in the contract* is as load-bearing as
the field tables.

**Frozen means written down, not finished.** Everything below describes the
command as it actually behaves. Where the behaviour is arguable it is recorded
under *Known divergences* rather than quietly improved, because a contract that
describes an intention is a contract nobody can implement against.

**Version 1** is the first release, landed with the command itself: there was no
`lx status` before 2026-08-19, only `lx stats`, which prints a progress bar and
is not part of any contract. Everything after this that would bump is gated the
way the sibling contract's bumps are — it becomes a work package, not a commit.
See `docs/decisions.md`, 2026-08-19.

## Versioning

`contract_version` is an integer, reported at the top level of
`lx status --json`, and declared by the fenced block at the top of this file. A
test asserts the two agree, and a second test asserts the response table below
agrees with both — the table is what an implementer reads to learn which number
they will receive, so it is a third declaration and not prose about one.

- **Additive changes do not bump it**: a new key, a new optional flag, a wider
  accepted value set, a new value in an open vocabulary.
- **Anything else bumps it**: removing or renaming a key, changing a key's type,
  changing what a value means, narrowing an accepted value set, or changing an
  exit code a documented condition produces.

*Lost:* a `--contract-version` flag that prints the integer alone, so a consumer
could gate before parsing. It loses because the consumer has to run the command
to learn anything at all, and the field is the first key of the object it already
receives — a second invocation to learn what the first one says is a round trip
bought with a flag that then has to be frozen too.

The package version (`version`) is **not** the contract version and must not be
used as one. It moves on every release, including releases that change nothing
here.

## Invocation

```
lx status [--json] [--lang TAG] [--scan ROOT] [--depth N]
```

- **`--json` is the contract.** Without it the command prints a human summary
  whose wording is not frozen and must not be parsed.
- **`--lang TAG`** reports only that target language. It filters `documents`; it
  does not filter `targets`, which is what the project is configured for rather
  than what it holds.
- **`--scan ROOT`** reports every project under `ROOT` instead of the working
  directory. See *What a project is*.
- **`--depth N`** bounds that search. Default `3`.
- **Encoding is UTF-8 on every platform**, forced onto stdout before the parser
  runs, so redirecting the output on a machine whose console is cp950 or cp1252
  does not die on the first CJK character. JSON is emitted with
  `ensure_ascii=False` and two-space indentation.
- **There is no `--out`.** The report goes to stdout and a consumer redirects it.

### Exit codes

| Code | Condition |
|---|---|
| `0` | A report was produced. **Including a report in which every project failed to be read** — see `error` — and including one whose `projects` is empty. |
| `2` | The invocation was refused before any report existed: `--scan` naming something that is not a directory, an unparseable argument. The message goes to stderr, prefixed `lx: `, and stdout carries nothing. |

`lx status` deliberately has no failing exit code of its own. It is a report, and
"this project has 41 errors in it" is a successful report — the command that
exits 1 on an unhealthy document is `lx check`, and invariant 10 makes that
exit code the evidence. A consumer must not read `lx status`'s exit code as a
quality signal; it reads `totals.errors`.

## What a project is

A directory is a project when it holds **either** of these:

| Marker | Written by |
|---|---|
| `.lx/` (a directory) | `lx init`, and `store._connect` on the first state write |
| `lx.config.json` (a file) | `lx init` only |

Either alone is enough, and the *or* is the rule rather than a convenience.
`lx init` writes both. A cold `lx extract` in a bare directory writes only
`.lx/`, because `config.write_templates` never ran and `load_config` treats a
missing file as an empty override. A project configured by hand and not yet
extracted has only `lx.config.json`. Requiring both would hide the second and
third, which are exactly the two states a library is found in: one book underway,
one book set up last night.

Each marker is type-checked, not merely tested for presence — a file named `.lx`
and a directory named `lx.config.json` are neither of them a project. Which
markers matched is reported per project as `markers`, so the rule can be argued
with from evidence rather than from this paragraph.

*Lost:* `.lx/state.db` as the marker. It would mean "has work in it", and a
project with no work is still a project a bookshelf must show — showing it at 0%
is the point. It also names a file one layer deeper inside the storage this
contract exists to keep a consumer out of.

**The scan is this project's job, not the consumer's.** A consumer must not test
for these markers itself, and this table is documentation of the rule rather than
an interface: `.lx/` is inside the storage layer the red line keeps it out of, and
a consumer that learned the marker would break on the day the directory is
renamed. `lx status --scan ROOT` is the supported way to find projects, and it is
the reason the flag exists at all.

### How the search walks

- `ROOT` itself is examined, at depth 0. Pointing `--scan` at a single project
  reports that project.
- A directory identified as a project is **not descended into.** A project inside
  a project is not something this storage can express — every document identity
  is a path relative to one working directory.
- A child whose name begins with `.` is skipped. That is `.git`, `.venv` and
  every dotted cache, and it is why the walk over a real library is cheap.
- `--depth` bounds the rest. Depth `3` by default, because a library is
  `ROOT/<shelf>/<book>` about as often as it is `ROOT/<book>`.
- Symlinks are followed, and results are deduplicated by `os.path.realpath`, so a
  shelf of symlinks to one book reports it once, under the first path that
  reached it. The depth bound is what makes a cycle finite.
- A directory this process may not list is skipped in silence. A home directory
  has several and none of them is worth ending a scan for.
- Results are sorted by `path`.

*Lost:* an unbounded walk, which pointed at a home directory is a filesystem
crawl nobody asked for. *Also lost:* depth 1, which is all this package's
acceptance criterion required and which fails the first person who groups books
by shelf.

### The working-directory case

Without `--scan`, `projects` holds **exactly one entry: the working directory,
reported whether or not it carries a marker.** That is where the person is
standing, and answering "you are not in a project" with an empty array is a
puzzle rather than a result — `markers` is `[]` and the counts are zero, which
says the same thing in a shape the consumer already parses.

*Lost:* a bare project object for this case rather than a one-element array. It
reads better in a terminal and forces every consumer to write the branch.

## The response

One JSON object. Keys are stable; key **order** is not, and a consumer must not
depend on it.

> **Read *Deliberately not in the contract* before you design a screen.** Four
> things this surface has no field for, and will not grow one for by accident:
> **no timestamp** of any kind, so "recently worked on" cannot be built; **no
> reading order**, so a novel's chapters arrive in storage order; **no book
> title**, only a directory name; and **no cover art**. Each is argued below.
> They are listed here because that section is at the far end of this document
> and a consumer reading top-down reaches the field tables first — which was
> measured, on the first person to implement against this file.

| Key | Type | Meaning |
|---|---|---|
| `contract_version` | integer | The version of *this document*. `1`. |
| `version` | string | The package version, `scriptorium.__version__`. **Not** the contract version. |
| `scanned` | string \| null | The `--scan` root **exactly as it was typed**, or `null` when the report is of the working directory. Not resolved and not made absolute: it is a label saying which invocation produced this, and `projects[].path` is where the answer actually is. |
| `lang` | string \| null | The `--lang` filter, or `null`. Echoed so that an empty `documents` explains itself to a machine as well as to a person. |
| `projects` | array of *project* | Sorted by `path`. **Not capped.** Exactly one entry when `scanned` is `null`; zero or more when it is not. |

### project

| Key | Type | Meaning |
|---|---|---|
| `path` | string | The project directory, absolute. `os.path.abspath`, **not** `realpath` — under a junction or a symlinked shelf this is the path the scan walked, which is the one the person recognizes. Treat it as this project's identity for the length of one report, and as a label to show; do not compare it against a path resolved another way. |
| `name` | string | The last path component: `os.path.basename` of `path` with trailing separators stripped first, falling back to `path` itself. The stripping matters only at a drive or share root, where plain `basename` is empty and this reports `C:\` or `//server/share`. **There is no project name in configuration** — nothing in `lx.config.json` or in the state carries one. This is the directory's name and a consumer that wants a nicer title has to store its own. |
| `markers` | array of string | Which of `.lx`, `lx.config.json` this directory holds, in that order. `[]` is possible for the working directory and never under `--scan`. |
| `source_lang` | string \| null | The **effective** source language: `lx.config.json` layered over this build's defaults. A project with no configuration file reports the default rather than nothing, so this is not evidence that anybody chose it. `null` when `error` is set, and when the configured value is not a string. |
| `targets` | array of string | The **effective** target language tags — what the project is *for*, which is not what it currently holds, and which falls back to this build's default the way `source_lang` does. Only strings, and only out of an actual list. `[]` when `error` is set. |
| `tone` | string \| null | The project's **effective** default register, defaulted like the two above, and type-checked but not validated — an unknown register name is reported as it stands. A document carries its own; see below. |
| `documents` | array of *document* | Every tracked (document, language) pair. **Not capped.** Ordered by the storage identity and then by language — `store.tracked`'s order, which is **not** the source path's order and is not alphabetical by `source`. Sort it yourself if the order matters. `[]` when `error` is set. |
| `untracked` | array of object | `{source, lang}` — one entry per configured target language for each document matching the project's `sources` globs that is **not already tracked in that language**. A book on the shelf that nobody has started. `cli.do_untracked` decides it, so this key, `lx untracked` and the workbench's `untracked` spell one word and mean one thing. **Not capped.** Filtered by `--lang` the way `documents` is. `[]` when `error` is set. |
| `languages` | array of *rollup* | One entry per distinct `lang` among `documents`, sorted by tag, each carrying `lang` first and then the *rollup* counters. |
| `totals` | *rollup* | The same counters over every document **in this report**, which under `--lang` is the filtered set and not the whole project — a consumer that shows it as the book's completion while filtering shows one language's progress as the whole.
 **Present and zeroed** rather than absent when there are none, so a consumer never has to tell "no documents" from "an older build". |
| `error` | string \| null | Why this project could not be read, or `null`. When it is set every count is zero and every list except `markers` is empty — `markers` is a fact about the directory rather than about the read, and it is exactly what a person diagnosing the failure wants. **Not stable, not to be parsed, and not always a well-formed sentence** — most are written for a person, and some are whatever Python said (`'list' object has no attribute 'items'`, for an `lx.config.json` holding an array). It is also the one field that can name a path inside `.lx/`; see the bullet below. A client that shows it to a reader is showing an internal. |

**One unreadable project does not end the report.** A `state.db` written by a
newer schema, an `lx.config.json` that is not JSON, a file that is not a
database, a directory that stopped being listable between the walk and the read
— each becomes this project's `error` and the rest of the library still lists.
That is the rule `GET /api/state` already follows for a malformed routing stage,
for the same reason: the offending entry is usually the one the person most needs
to be told about, and it is the least useful thing to be told by a traceback.

Note what this costs, because it is a real asymmetry: `lx extract` on a project
with a newer schema exits 2 and prints the upgrade sentence, and `lx status` on
the same project exits 0 and puts that sentence in a field. See *Known
divergences* (5).

### document

| Key | Type | Meaning |
|---|---|---|
| `source` | string | The document's path relative to the project directory, `/`-separated on every platform. This is the document's identity here and the string the other surfaces call `src`. |
| `lang` | string | The target language tag, **as it was typed at `lx extract`**. It is not validated, not normalized and not case-folded anywhere on this path — `zh_tw` and `Klingon` are both accepted and both appear here — and it may therefore disagree with the project's `targets`. Build a language switcher from `languages[]`, never from `targets`: `--lang` matches this string exactly, so filtering by a configured tag that no document was extracted under returns nothing. A new value is additive and does not bump; a reader dispatching font, script or text direction on it needs a fallback. |
| `format` | string \| null | `markdown` or `text` today. Frozen onto the document at extract, because a skeleton is only readable by the parser that wrote it. The set is **open** — a new format is additive and a consumer must not treat this list as closed. |
| `tone` | string \| null | The register **this document** was extracted in, which may differ from the project's `tone` if the configuration changed afterwards. It is part of the translation-memory key, which is why it is a document-level fact and not a segment one. |
| `output` | string \| null | **Where `lx render` writes this document**, relative to the project directory, from the project's own `output_pattern`. This is how a reader gets from a status entry to the translated text; nothing else on this surface says where it lives. It is a *prediction*, not an observation — the file may not exist yet, and this surface does not stat it. `null` when the configured pattern cannot be formatted. **A consumer must confine this path before opening it**: `output_pattern` is hand-written configuration and this is the result of interpolating it, so `../../elsewhere/{path}` is a pattern somebody can write. |
| `state_version` | integer | The content version of the stored state. A document whose value is **higher** than the running build's `store.STATE_VERSION` is one this build cannot fully read; it still appears here with its counts, because a listing that hid it would be the one place the person could not find out. |
| `segments` | integer | How many segments the document has. |
| `translated` | integer | How many have a **non-empty target after stripping**. That is the same rule `store._segment` derives a segment's `status` from — and it is **not** the rule `lx check`'s report or the workbench's `docs[].done` use, both of which count any target at all. On a document holding a whitespace-only target the numbers differ; see *Known divergences* (3). |
| `pending` | integer | `segments - translated`. |
| `held` | integer | How many carry `review == "held"` — no queue that selects work will take them. **A held segment is also counted in `translated`** — the two are not disjoint, while `translated + pending` is still `segments`. That containment comes from the **hold control**, which refuses a segment with no target, and not from anything on this surface: the two counts are computed independently here, so a writer that ever produced a held row with an empty target would break it silently. Do not compute "translated and not held" as `translated - held` without allowing for that. |
| `waived` | integer | How many carry a **waiver**: a reviewer read what `lx check` reports on that wording and stood by it, so the rules judgement can overrule are reported at `warn` on it instead of failing the build. Counted from the live segments, like `held` and unlike `errors` — a waiver is state, not a finding, so this number is current whether or not `lx check` has run since. **Not disjoint from anything**: a waived segment is also counted in `translated`, may also be `held`, and its own issues still appear in the `check` counts, under `warnings`. ⚠️ **Read it beside `errors`.** `errors: 0` on a document with `waived: 0` means no mechanical rule fired; `errors: 0` with `waived: 3` means three segments carry issues a person decided to stand by. The pair is the only honest reading, and this field exists so that the pair is available at all. |
| `check` | *check* \| null | The last `lx check`'s counts, or `null` when nobody has checked this document. |

### check

The counts from `.lx/reports/<document>.<lang>.json`, which is a **rebuildable
artifact and not state** (invariant 9). Every value here is a projection of a
file that may be older than the document it describes.

| Key | Type | Meaning |
|---|---|---|
| `errors` | integer | Error-severity issues at the time of that check. |
| `warnings` | integer | Warning-severity issues at the time of that check. |
| `stale` | boolean | **One-way.** `true` means the report definitely no longer describes this document, because the segment count or the translated count has moved since it was written. `false` means only that those two numbers still agree — a sentence rewritten in place moves neither, and there is no timestamp anywhere to settle it. |

`null` is not zero. A document nobody has checked has `check: null`, and a
consumer that drew it as a clean one would be reporting a quality claim the
project has never made — which is invariant 10 in the form this surface can break
it. The `checked` counter in every *rollup* exists so that the distinction
survives summation.

**And `errors: 0` with `stale: false` is not a pass claim either.** That is the
same breach through the second door, and it is the more dangerous one because it
looks like an answer. A target edited in place — a sentence rewritten, a
placeholder lost — moves neither the segment count nor the translated count, so
this surface goes on reporting a green check that a fresh `lx check` now fails.
Reproduced while freezing this contract: `lx check` green, one existing target
rewritten to contain a bare `⟦7⟧`, `lx status` still `errors: 0, stale: false`,
`lx check` exit 1.

Invariant 10 is exactly the rule this breaks — *the exit code is the evidence* —
so state the reading plainly: **nothing on this surface is a claim that a
document passes.** It reports what the last check found, whenever that was. A
client that shows a green light is showing history, and the only thing that
makes it current is a new `lx check` and its exit code.

A corrupt or unreadable report reads as a missing one. The alternative is a
library that will not list on account of a file that one command regenerates.

### rollup

The shape of `languages[]` and of `totals`. A language rollup carries `lang`
first; `totals` does not carry it at all.

| Key | Type | Meaning |
|---|---|---|
| `lang` | string | *Language rollups only.* The target language tag. |
| `documents` | integer | How many (document, language) pairs this rollup covers. |
| `checked` | integer | How many of them have a `check` at all. |
| `segments` | integer | Sum. |
| `translated` | integer | Sum. |
| `pending` | integer | Sum. |
| `held` | integer | Sum. |
| `waived` | integer | Sum. |
| `errors` | integer | Summed over the `checked` documents only. |
| `warnings` | integer | Summed over the `checked` documents only. |

`checked` is not decoration. Summing `errors` without it is misleading in the one
direction that matters: a document nobody has checked contributes zero and reads
exactly like a clean one, so "0 errors" across a project nobody has checked is
indistinguishable from a project that passes. *"3 errors across 5 of 7 checked"*
is the smallest honest statement of quality this surface can make.

`waived` is there for the same reason and against the other door. A project can
reach `errors: 0` because every rule passed, or because a person read the ones
that did not and stood by the wording; those are different claims and the exit
code alone stopped being able to tell them apart on 2026-09-03. Summing it makes
the second visible without asking a consumer to look inside `.lx/`, which this
contract forbids it. *"0 errors, 4 waived, across 7 of 7 checked"* is what an
honest library card says about a finished book.

## Deliberately not in the contract

Things that are true because something is **absent**. Each is invisible in a
diff, so each is written down; a change that "adds" any of them is reopening
something its absence closes.

- **No timestamp of any kind, anywhere.** Not "last translated", not "last
  opened", not "last activity" — which the work package that scheduled this
  contract listed as a field worth having. It is absent because there is nothing
  honest to put in it, and that was measured rather than assumed: no column in
  the `documents`, `nodes` or `segments` tables records a time, no key written
  into a document's `meta` or a segment's `body` does, and a translation-memory
  record has no time field either.

  And every filesystem proxy is **moved by reading**. Opening `.lx/state.db` at
  all runs `PRAGMA journal_mode=WAL`, and closing the last connection to a WAL
  database checkpoints it, which rewrites the main file — so a `last_activity`
  read off that mtime would be advanced by `lx status` itself, and a library
  scanned twice would report every book as having just been worked on. A field
  whose value is changed by the act of reading it is worse than an absent one,
  because the absence is visible and the lie is not.

  A real `updated_at` is a schema change, which is a `SCHEMA_VERSION` decision
  and a `store.py` edit — a shared seam this package was explicitly scoped out
  of. It is *Reserved* below and scheduled.

- **No credential, because none of the fields one is configured in is read.**
  No provider, no `base_url`, no `api_key_env`, no `headers`, no routing. This
  surface reports progress, not configuration, and the three configuration values
  it does carry are `source_lang`, `targets` and `tone`.

  **That is a statement about which fields are read, and not a promise about
  their contents.** `lx.config.json` is hand-edited and nothing stops somebody
  typing a secret into a field that is not for one; this surface does not mask
  strings and is not the place to start. The guarantee is the narrower one that
  actually holds: `providers` and `routing` are never read, so the fields where a
  credential really is configured cannot reach here, and there is no URL-shaped
  value on this surface for `config.printable_url` to have been forgotten on.
  Stated this way since 2026-08-19, because the sentence it replaces — "none of
  which can hold a secret" — was refuted by the security pass in one hand-edit,
  and an over-absolute claim is exactly how the next reader is taught not to look.

  Each of the three is **type-checked on the way out** to what the tables above
  declare. A non-string `source_lang` or `tone` becomes `null`; `targets` takes
  only the strings out of an actual list, so a `"targets": "zh-TW"` — the likeliest
  typo there is — reports none rather than five languages named after its letters.

  Written as an absence on purpose. Invariant 6's lesson of 2026-08-13 is that a
  display surface is any place a value can be read, not the list of places that
  look like a report — two surfaces were found printing a raw `base_url` because
  the enumerated list had been read as the definition. The cheapest way for a new
  surface not to join that list is to carry nothing that would put it there, and
  that is a decision this document freezes rather than a gap. Adding a provider
  block here would be additive under the versioning rule above and **is not**:
  it would need `config.printable_url` on every URL-shaped value first, and it
  should be argued for in `docs/decisions.md` before it is argued for in code.

- **No segment text, ever.** Not a source string, not a target string, not an
  issue message, not an excerpt. A status is counts. A consumer that needs the
  words is asking for the workbench contract or for an exported document, and
  routing it here instead would put every sentence of an unpublished translation
  into whatever reads this.

- **No path inside `.lx/` in any structured field.** Not a key, and not a value
  a client renders as data: `.lx/reports/` is read to produce `check` and is
  never named, and `.lx` appears only as a discovery `marker`. That is the
  storage-independence the red line buys.

  **`error` is the exception, and it is a real one.** It carries whatever the
  failure said, and some of those sentences name the file — a `state.db` from a
  newer schema reports ".lx\state.db was written by a newer
  scriptorium …", naming the path twice. The absence was written here as an
  absolute until 2026-08-19, and the adversarial pass refuted it with one
  `PRAGMA`. Sanitizing the message would cost the only thing in it that tells a
  person what to do, so the claim is narrowed instead: **a client must treat
  `error` as opaque internal text**, show it as a diagnostic rather than as data,
  and build nothing on the paths inside it.

- **No pagination, and no list here is capped.** Every project under the root,
  every document of every project. A library of a thousand books is one object,
  and that is expected. A *filter* is the thing to reach for if measurement ever
  shows a need — `--lang` is the one filter that exists — and a cap is not, for
  the reason the sibling contract gives: a silently truncated list reads as a
  complete one.

- **No writes, with one honest exception.** `lx status` creates no directory, no
  database and no file: `store.tracked` opens the state with `create=False`, so a
  directory with no `.lx/` is left without one. The exception is that opening an
  *existing* `state.db` runs `PRAGMA journal_mode=WAL`, which creates the `-wal`
  and `-shm` sidecars, and runs the schema migration if the database was written
  by an older build. That is a property of every connection this project opens
  rather than of this command, and it touches no document content — but "`lx
  status` writes nothing" would be a false sentence, so it is not the sentence
  written here.

- **No network, and no model is ever called.** This command cannot spend money.

- **No project title from configuration**, because none exists. `name` is a
  directory basename. See *Reserved*.

- **No way to tell whether a source document changed on disk** since it was
  extracted. There is no content hash of the source here and `stale` is about the
  check report, not about the document. `lx untracked` answers a neighbouring
  question and is not part of this contract.

- **No reading order.** `documents` arrives in `store.tracked`'s order, which is
  by storage identity — `doc_id`, every non-alphanumeric character flattened —
  and then by language. That is neither the source path's order nor anything a
  reader wants: sorting by `source` yourself puts `ch10` before `ch2`, and for a
  novel the order of the documents *is* the book. **The order is the consumer's
  problem today and this project does not supply one.** An EPUB spine, when that
  format lands, is the first real answer and is *Reserved*; until then a reader
  needs its own ordering, kept on its own side.

- **No stable identity for a project.** `path` is where the directory was when
  the report ran. Move or rename it and every count survives — document identity
  is relative to the project directory, not absolute — while `path` and `name`
  both change, so a consumer keying its covers, titles and reading positions on
  `path` loses all of them and gains a new book at 0%. **There is no alternative
  field, and this is a present-tense fact rather than a future risk**: the
  consumer is being told not to depend on the only identity it has. Keep your own
  identifier and offer the person a way to re-point it.

- **No book title and no cover art.** `name` is a directory basename; nothing in
  configuration or in the state carries either, and this surface will not invent
  them. A bookshelf stores both on its own side, keyed by the identity it does
  not have — see the bullet above, which is why these two are written together.

- **No `collisions`.** `cli.do_untracked` also reports the document identities
  that more than one path maps to — `books/第一章.md` and `books/第二章.md` flatten
  to one identity, which is a whole Chinese-titled library collapsing to one row
  — and the workbench surface carries them. They are **not** here, because a
  consumer of this contract cannot act on one: resolving a collision is renaming
  a file, and this surface is read-only by design. `lx untracked` prints them and
  is the place to look. Recorded rather than omitted silently, because the
  absence means `untracked` can under-report: two books that collide are offered
  as one.

- **No exit code that varies with the report.** See *Invocation*.

## Reserved

Named so that the next package does not have to guess, and so that the day one of
these lands nobody has to re-derive whether it was a break.

- **A real `updated_at`.** Per (document, language), written where the targets
  are. It needs a `SCHEMA_VERSION` move and an edit to `store.py`, and it closes
  the largest deliberate hole in this contract. **Adding the field is additive**
  and would not bump this version; a consumer must not assume its absence is
  permanent.

- **An `origin` breakdown** — how many segments came from a person, an agent, a
  model, the memory or a carryover. Every value is already loaded to produce the
  counts above and was left out to keep version 1 small. Additive.

- **A project title.** If a `name` or `title` key is ever added to
  `lx.config.json`, `name` here starts reflecting it. That is a **meaning
  change** to an existing field and bumps this version; the alternative, a
  separate `title` key beside `name`, is additive and is the likelier shape.

- **A reading order.** The EPUB format layer carries a spine, which is a real
  order rather than a guess from filenames, and it is the first thing that could
  fill this hole. Adding a `position` or reordering `documents` by it is
  additive on the key and a **meaning change** to the array's order, which this
  document promises nothing about today — so the array is safe to reorder and a
  consumer must not have depended on it either way.

- **A stable project identifier.** Anything durable enough to survive a rename
  has to be written into the project, which is a `store.py` or a configuration
  change. Additive when it lands.

- **`--scan` on the HTTP surface.** It is not there today. If it ever is, the
  root arrives in a request and goes through `cli.confined_path` before anything
  stats it — the CLI flag is exempt because invariant 11's named exception is a
  person typing a command, and that exemption does not survive the move.

- **A second confinement root.** The sibling contract reserves this and the
  reservation binds here too: today a project *is* a working directory, and this
  contract's `path` is the only identity there is. Do not build a consumer that
  assumes one project has one path forever.

## Known divergences

Measured, reproduced, and recorded rather than repaired — each because repairing
it belongs to a package this one is not. Numbering is append-only; a closed entry
is marked `Closed` in place and keeps its number.

1. **The scan reads a project by changing the process working directory.**
   Everything in this project resolves against `os.getcwd()` — `store.db_path`,
   `store.doc_id`, `config.load_config` — so reading a project means standing in
   it. `do_status` captures the original directory once, moves per project, and
   restores in a `finally`. This is correct for a CLI command and is **not
   thread-safe**: an in-process caller that runs `do_status` on one thread while
   another touches the filesystem would see the move. `lx web` does not call it
   today. The right shape is a root threaded through `store.py`, which is a
   shared-seam edit scheduled separately.

2. **`check` is read from a rebuildable artifact and `stale` is one-way.** A
   document edited without changing its segment or translated count reports
   `stale: false` over a report that no longer describes it. A timestamp would
   settle it; see *Deliberately not in the contract*. The honest reading of
   `stale: false` is "the two counts still agree", and this document says so
   rather than promising freshness.

3. **This surface and `GET /api/state` spell the same two numbers differently,
   and one of them counts differently too.** `documents[].segments` /
   `.translated` here are `docs[].total` / `.done` there, over the same
   `store.tracked` read. The names are not being aligned: renaming either is a
   bump on that contract, and the two consumers are different enough that one
   vocabulary was never going to fit both.

   **The predicates genuinely differ**, which this entry denied until
   2026-08-19, when the adversarial pass put both surfaces on one project at one
   moment and got `done: 1` against `translated: 0`. There are three counters of
   "translated" in the tree and only this one strips: `cli.do_check` writes its
   report with `s.get("target")` and `web/server.py` computes `done` the same
   way, so a document holding a **whitespace-only target** is done to them and
   pending here. This surface is the one that agrees with `store._segment`, which
   derives a segment's `status` from a stripped target — so the divergence is the
   other two's, and closing it means editing `web/server.py` and a report shape
   the workbench contract freezes. Recorded rather than repaired for exactly that
   reason.

   The population is narrow but real: `do_apply` refuses a target that is empty
   after stripping, so such a row arrives from a build that did not, or from a
   writer that is not this CLI. It is the same population `store._segment`'s
   recompute-on-read exists for. It also made `check.stale` **permanently true**
   here — a report one second old, with no re-check able to clear it — until the
   comparison was moved into the report's own arithmetic. See the *check* table.

4. **A read can run a schema migration.** Listing a library of projects written
   by an older build upgrades every one of their databases. Nothing is lost — the
   migration is the same one every command runs — but a person who expected
   `lx status` to be inert has had files rewritten. It is the WAL exception's
   larger sibling and it is the reason that bullet is written the way it is.

5. **`error` swallows a refusal the rest of the CLI exits 2 for.** A `state.db`
   written by a newer schema stops `lx extract` with exit 2 and a sentence, and
   produces exit 0 with that sentence in a field here. That is deliberate for
   `--scan`, where one bad project must not take the library down, and it is
   inherited by the working-directory case for shape uniformity rather than
   because anyone argued it was better there. A consumer must therefore check
   `error` per project and must not read exit 0 as "everything was readable".

6. **`markers` is emitted, and the *What a project is* section tells consumers
   not to use it.** It is there for a person diagnosing why a directory did or
   did not appear, and it names two paths inside the storage layer to a surface
   whose red line is about not naming them. The alternative — a bare boolean —
   loses the diagnostic exactly when it is wanted, which is when the rule and the
   person disagree. Recorded because it is the one field here that a consumer
   could build a dependency on that this document has asked it not to.

7. **A document row with no stored source path fails its whole project.** The
   storage layer tolerates it — `store._meta` guards its own normalization with
   `if doc.get("source")` — and this surface cannot, because everything it does
   with the value treats it as a path. Until 2026-08-19 it reached
   `os.path.relpath(None)` and ended the entire command in a `TypeError` that
   `main` does not catch, producing **no report at all** and taking every healthy
   project in the scan down with it; the docstring above the function said
   "Never raises" the whole time, because the `try` covered the configuration
   read and stopped one line short of the projection. Now it is a refusal with a
   sentence, scoped to the one project.

   The two alternatives both lose. Listing the document with `source: null`
   breaks the type this contract declares for it, and a consumer written to the
   table would be the thing that crashes. Skipping the row silently hides state
   corruption behind a count that quietly does not add up. Found by the
   security-tier pass, which is also what found that the `try` was one line too
   short — the *shape* of the defect, not the row that reached it, is the part
   worth remembering.

8. **An entry that fails is rebuilt, not annotated.** A failure part-way through
   a project's projection would otherwise leave a document list that stops
   wherever the exception happened and totals that never ran, while `error` said
   the counts were zero. The failing branch returns a fresh empty entry, which is
   what makes this document's "every count is zero and every list except
   `markers` is empty" true whichever line raised rather than only for the early
   ones. `markers` is rebuilt with the entry and stays populated on purpose —
   the sentence carried no exception for it until 2026-08-19, when the
   consumer's-eye pass read it literally and found the field that contradicts
   it.

## What is not frozen

Freezing the contract does not freeze the implementation. Free to change without
touching this document, and deliberately so:

- Which Python functions implement it, how `cli.py` is structured, and whether it
  stays Python at all. `cli.do_status` is the exception: it is frozen as the
  statement of invariant 8's seam, the way each endpoint's *Backed by* line is in
  the sibling contract.
- **`lx stats`.** It prints progress bars, it is not in this contract, and it
  must not be parsed. It computes nothing of its own — since 2026-08-19 it reads
  `do_status`, so the two commands cannot come to disagree about one project's
  counts, which they already had: it counted a target of three spaces as
  translated where every other counter here strips first.

  **It does not inherit this contract's exit code, and that is the point of
  saying so here.** `lx status` answers 0 on a project it could not read because
  it has an `error` field and a `--scan` that must survive one bad book;
  `lx stats` has neither, so it prints the sentence to **stderr** and exits
  **2**, exactly as it did before the rewire. For one commit it did not, and the
  caller that noticed is `.github/workflows/ci.yml`, whose smoke step ends in a
  bare `lx stats` under `set -euo pipefail` — the exit code is the whole
  assertion there, and it went green on a database that could not be opened.
  Recorded in a contract `lx stats` is not part of because the rewire is what
  put the two commands in one place, and the next person to change one will be
  reading this file.

  It also passes `checks=False`, so it opens no report it would not print. That
  keyword is an implementation detail and never reaches this surface.
- The human, non-`--json` output of `lx status`, in its entirety.
- Every `error` sentence.
- The validator rule set behind `check`'s numbers, which is expected to grow.
- `.lx/` in its entirety: the SQLite schema, the state version, the report files,
  the translation memory. No consumer of this contract may read inside it.

Reasoning for the contract itself, and the alternatives that lost, is in
`docs/decisions.md`, 2026-08-19.
