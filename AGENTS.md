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

   **The bytes are the file's own, kept once and sliced — never re-encoded.**
   `lx extract` stores the source document verbatim in `documents.source`,
   beside the `encoding` the state already records, and a position's bytes are
   `skeleton.source_map`'s query over that pair: the characters the state holds
   are consumed against a fresh decode of the bytes it holds, so the two
   outcomes are the exact span and `docio.ByteSpanMismatch`. `lx bytes` is the
   command in front of it. Nothing re-encodes, which is what makes the claim
   above true for a codec that is not injective — and cp950 is not: ten
   sequences decode to a character that re-encodes to *different* bytes, so
   `text.encode(encoding)` was never the file and the round trip that used it
   was measuring the injective cases only. `docs/decisions.md`, 2026-09-10.

   **It is a property of the document, not of a node**, and that is a
   measurement rather than a preference. 十 (`A2CC`) and 卅 (`A2CE`) are
   ordinary translatable prose, so they are always segments and never skeleton;
   in Markdown a whole BBS-era chapter merges into one segment and *none* of the
   ten reaches the skeleton at all. A representation that kept bytes on raw
   nodes would deliver nothing on either.

   Skeleton raw nodes are still stored as **BLOB, never as JSON text**, and that
   column stays unspent by this: a source file containing invalid UTF-8 —
   routine for older Big5, GBK and Shift-JIS text — cannot be written to a JSON
   state file at all, which is the *other* half, scheduled as HANDOFF-061 and
   not built. Reading such a file is still refused.

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

   **A source that spells `⟦n⟧` itself is masked first, before any host syntax.**
   That is not a pattern in the table; it is what makes the table's answers
   readable at all. Until 2026-09-10 the two were the same characters — `mask`
   wrote `⟦n⟧` into a string and read `⟦n⟧` back out of that same string — so a
   literal sitting inside another slot's original was re-substituted inside its
   own restored text, and one standing in prose survived into the masked text
   with no slot behind it, where `checks.py` failed the segment on `tags` and the
   render wrote the untranslated marker over the paragraph. 22 segments of this
   repository's own tracked Markdown, across four files, and `lx check` at exit 0
   over the first kind. Because the pre-pass lives inside `mask`, it holds at
   both sites that mask: the parsers, and `normalize.polish_rendered`, which
   masks the *rendered* text and so corrupted a translation that merely mentioned
   the token. `docs/decisions.md`, 2026-09-10.

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
   surface can show a configured value, it joins the list. It did so again on
   2026-08-20: `POST /api/config` answers with the effective value it wrote, so
   the reply is a display surface and goes through `cli.do_config_value`, which
   is `lx config get`'s own projection kept typed for a wire. The endpoint's
   *refusals* are the other half — the two fields a mispasted key lands in still
   never repeat what they refused, and the gate in front of them is handed no
   value at all, so it has none to leak. What the fields *beside* those two do
   with one is `docs/contracts/workbench-http.md` divergence (29), open.

   It was wrong a **fourth** time on 2026-09-01, and this one had been reachable
   from a terminal long before any of the three above. A `base_url` a hand-edited
   file carries userinfo in makes `urllib` raise `http.client.InvalidURL`, whose
   own message quotes the netloc it choked on — password included. That class
   descends from `HTTPException` and is **neither a `ValueError` nor an
   `OSError`**, so `urllib` never wrapped it in a `URLError`, none of
   `providers/base.py`'s masked branches applied — there were two, and this
   made a third — it never became a `ProviderError`, and `lx models` and
   `lx translate` answered a traceback
   carrying it. `GET /api/models` is what put it in front of a *browser*, and a
   probe over that endpoint is what found it — no design review did. It is masked
   in `_request` beside the other three now. Note what the first attempt at the
   guard got wrong: it caught `ValueError`, which is `InvalidURL`'s nearest
   *plausible* base and not its real one. Catch the class; never name the member.

   A rule is enforced where a field **lands**. A key may not be addressed *inside*
   something that holds one value, whether the field table says so or the merged
   configuration's own type does: without that, `providers.new.api_key_env.x`
   wrote a raw credential with no rule consulted, and `batch.size.x` replaced a
   number with a block. The rules, and the two shapes that shape alone does not
   catch, are in `docs/decisions.md`, 2026-08-12.

7. **The provider request stays minimal.** No `response_format`, tools, or
   streaming unless the project opts in per-provider. Self-hosted runtimes reject
   unknown fields rather than ignoring them, and local support is a requirement.

   This is about the **chat completion body**, which is exactly
   `{model, messages, temperature, max_tokens, stream}` and is pinned by two
   tests. `Provider.list_models` — `GET {base_url}/models`, added 2026-08-20 —
   is a different endpoint and adds no field to that body. It is **advisory and
   gates nothing**: a single-model `llama-server` ignores the `model` field
   entirely and still answers, so a listing that became a check would refuse a
   working configuration on the strength of an endpoint this world treats as
   optional. It was, until 2026-09-06, the one place where text from a remote
   server reached a terminal; `Provider._vectors` is a second, and
   `openai_compat`'s two shape refusals were a third and fourth all along. What
   survives of the sentence is the rule rather than the count: any of them can
   put a backend's own bytes in front of a reader,
   so an id carrying a control character — or a line separator, or
   an over-long field — is dropped at the boundary rather than escaped at the
   print, and the drop is what protects **both** the listing and `--json`.
   `json.dumps(ensure_ascii=False)` was assumed to cover the second and does
   not: it escapes C0 and passes C1, `Cf` and `U+2028`/`U+2029` straight
   through. Measured 2026-08-20 by the adversarial pass over the change that
   introduced this.

   **`Provider.embed` is the third endpoint and takes the same shape**, added
   2026-09-06 for `lx audit`: `POST {base_url}/embeddings` with a body of
   exactly `{model, input}`, advisory, gating nothing, and pinned by a test of
   its own. `encoding_format` is the field this rule most has to refuse — a
   `base64` reply is a different shape, so every check on the way in reads
   something that is not there. It goes through its own door into the transport
   rather than through `_post`: an embeddings reply carries `prompt_tokens` and
   **no** `completion_tokens`, so counting it would make `_UsageTotals` report a
   run's replies climbing while the reported count stayed at zero. The reply is
   untrusted like every other, and refused *whole* rather than row by row —
   `_listing` drops a bad row because a model nobody could select costs nothing,
   while a dropped vector removes a record from a comparison and a record that
   was not compared reads exactly like one that came back clean. That is also
   where 2026-08-20's flip condition landed: `POST /embeddings` on a
   `llama-server` answers **200** with a bare array, and a strict reader is what
   makes it loud. `docs/decisions.md`, 2026-09-06.

   **Reading the reply is free; asking for more of it is not.** Since 2026-09-02
   a completion's `usage` object is read off the response and reported as what
   the run cost. That adds no field to the body, and the two tests pinning it are
   unedited — a third asserts the same five keys *while* usage is being
   collected. `stream_options: {"include_usage": true}`, and any `usage` or
   `metadata` request field a hosted API offers, is a **request** field and is
   refused by this invariant whatever it would buy. A backend that reports
   nothing reports nothing, and the run says so rather than asking for it. The
   reply is untrusted like any other: only integers that pass
   `Provider._token_count` are ever formatted, so no part of a remote `usage`
   object reaches a terminal as text. `docs/decisions.md`, 2026-09-02.

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
   laundering each other's `origin`. (26) and (27) were appended by that work.
   (26) is **open** — a run of identical paragraphs that changed size is still
   told apart by nothing — and (27) **closed on 2026-09-01**: a memory hit
   answers over this document's own wording only when that wording is a machine
   draft, so origin precedence no longer has a path that rewrites the field it
   compares. Both were **named on both surfaces** from 2026-08-19 — `lx extract`
   prints the segments it happened to and `POST /api/extract` returns them —
   which closed their reporting half and neither of the entries. (28) was
   appended the same day by the adversarial pass over that work and is **open**:
   `POST /api/extract` type-checks neither `reset` nor `tone`, so the *string*
   `"false"` is a reset that discards a document's translations. It is recorded
   rather than repaired because refusing it narrows an accepted value set, and
   that bumps. (30) was appended on 2026-08-21 and is **open**, and it is this
   invariant's without being its usual shape: `POST /api/sentences` splits
   arbitrary text and `lx sentences` splits only a document's stored text, so the
   wire can be asked a question the command cannot — but no logic lives in the
   server, because both call `cli.do_sentences` and that function has taken a
   list of arbitrary strings since it was written. What the CLI lacks is a way to
   hand it one from a terminal, which is a flag and a scheduled item. (31) was
   appended on 2026-09-01 by the package that closed (27) and the silent half of
   (24)'s recorded cost — a stored wording carrying a `⟦n⟧` the segment has no
   slot for wrote that token into a rendered file with `missing` counting none of
   it — and **closed on 2026-09-03** by the bump to **4**. Its own text was wrong
   twice, which is the part worth carrying forward: it called the defect loud
   because every case it had found was an `lx check` error, and a renumbering
   that leaves the two id multisets the same size is not — `lx check` exited 0
   over a file carrying the token. An enumeration read as a definition, for the
   sixth recorded time.
   The same day, `POST /api/commit` stopped being the one endpoint with no
   `cli.do_*` behind it: what may be banked stopped being "has a target", and a
   policy with two homes is what this invariant exists to stop.
   (32) and (33) were appended on 2026-09-01 by `GET /api/models`, the fourteenth
   endpoint and the only **GET** that leaves the machine. Not the only endpoint:
   `POST /api/translate` has always reached a backend and carries the document
   text with the credential, which is a strictly larger exposure. What is new is
   that a *read* does it. Both are **open** and neither is leaked logic.
   (32) is the shape this invariant usually catches, arriving honestly: the wire
   answers `200` with `error` where `lx models` exits 2, because the endpoint
   feeds a control that must degrade rather than block and a terminal has no such
   control — the listing itself is `cli.do_models` on both surfaces, and what
   lives only in the server is the degradation policy. (33) is not this
   invariant's at all and is recorded there because that is where a reader will
   look: `urllib` keeps `Authorization` across a redirect to another host, so a
   backend answering `302` moves the credential. It predates the endpoint and
   lands in the transport every completion shares, so it is its own package.
   Two divergences on one endpoint is also the argument for the endpoint's own
   section being written before it shipped rather than after.
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
   same section was being rewritten. It moved to **4** on 2026-09-03, the second
   bump through the gate and scheduled as HANDOFF-036, carrying **two** items
   that are one change seen twice: `missing` counts a segment with no *usable*
   target, and `from` reports `marker` or `source` for a wording the render
   refuses to substitute. Every further bump is gated behind a work package
   rather than a commit, and that gate is unchanged.

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

    **A rule audited for one kind of false positive is not an audited rule.** That
    repair reached the two it was aimed at and not the one beside them: on
    2026-09-02 `numbers` was measured reporting 52 of 110 labelled pairs, every
    one of them on a Chinese or Japanese target and against 57 correct ones,
    because an ASCII-digit multiset cannot see that 第一章 is how Chinese writes
    "Chapter 1". Every chapter
    heading in every novel, at error severity, since before the 2026-07-28 audit.
    Repaired the same day; `docs/decisions.md`, 2026-09-02.

    **Since 2026-09-03 the exit code carries one qualifier, and it is the whole
    of what changed:** a green `lx check` claims that every mechanical rule
    passed on every segment *except those a reviewer has waived*, where the rules
    judgement can overrule are reported at `warn` instead. It is not a hole in
    the sentence above and it is deliberately not a silencer. Nothing is removed
    from the report — the finding keeps its rule name and its message, moves to
    `warn`, and a `waived` warning names the segment — so `errors: 0` with
    `waived: 0` still means what it always meant, and the pair is available on
    every surface: `lx check --json`, `POST /api/check`, and `lx status --json`,
    whose consumer may not read inside `.lx/` and would otherwise have no way to
    tell the two apart. **What a waiver cannot reach is the half a reviewer
    cannot be right about**: whether an issue is waivable is decided beside its
    severity where the finding is made, and it is false for every rule that
    reports the substituted *bytes* are malformed rather than that the wording
    may be wrong — invariant 2b's own half. There is no list of waivable rule
    names: the argument is required at each `add()` call site, so a rule added
    later cannot inherit an answer by omission, and a test asserts that by `ast`.
    `docs/decisions.md`, 2026-09-03.

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
    is called zip-slip; and `output_pattern`, which was trusted only because
    configuration was written by hand. **That moment arrived on 2026-08-20**, and
    of the two branches this invariant offered — confine the pattern at render
    time, or do not make it writable — the second was taken: `POST /api/config`
    admits thirteen key patterns and no path-valued key is among them.
    `config.PATH_VALUED_KEYS` therefore still inherits the terminal-trust
    exception through `lx config set` and through nothing else, and the first
    branch stays available at the price its own decision entry records.

    *Why it is an invariant rather than a note about the workbench:* the version
    scoped to the web surface would have expired the day HANDOFF-204 rewrote it,
    and would have bound neither of the other two. Measured cases and the losing
    alternatives are in `docs/decisions.md`, 2026-07-29.

## Layout

Current:

```
src/scriptorium/
  docio.py       document read/write as bytes; encoding detection; text mode never
                 touches a user document; `byte_spans`, which answers where a
                 run of characters came from in the file
  formats.py     the format registry: extension -> parser, and each format's knobs
  mask.py        markup protection: ⟦n⟧ slot records, tag pairing, DNT terms, bracket
                 repair, and the pipeline's own token space — a source that spells
                 ⟦n⟧ is masked before any host syntax, so every token downstream is
                 one this module made
  mdparse.py     markdown -> (skeleton nodes, segments)
  textparse.py   plain text -> the same pair; encoding, paragraph and chapter heuristics
  skeleton.py    walk(): the one iteration of doc["nodes"]. render_blocks() is
                 its block map and render() that map's join, for every format at
                 once; source_map() is the second consumer — which bytes of the
                 file each position came from
  sentences.py   where one sentence ends and the next begins — text, never
                 offsets; the rule the reading view is given rather than computes
  normalize.py   deterministic repair: punctuation width, CJK/Latin spacing
  checks.py      validators; error severity fails the build. Invariant 2b lives
                 here: block-start containment, host escaping, placeholder pairs
  audit.py       whether a stored translation belongs to the source it is filed
                 under — the one question `checks.py` cannot ask, because
                 answering it needs a network service and a threshold
  renderings.py  where one source term was written more than one way — the other
                 question `checks.py` cannot ask, because the evidence is the
                 whole book rather than one segment
  suggest.py     near matches from the memory, for the sentence an exact key
                 lookup misses by one word; pure stdlib, shows and never applies
  store.py       .lx/state.db (document state, SQLite), the translation-memory
                 key, the memory itself (.lx/tm.*.jsonl, still JSONL and tracked)
  config.py      layered config, glossary, do-not-translate list, style sheet;
                 dotted-key addressing, the atomic config writer, and
                 `resolve_route` — the one answer to "which backend, which model"
  translate.py   batching, concurrency, JSON tolerance, per-segment retry, and
                 `misattributed` — whether a reply answers the request it was sent
  providers/     openai_compat (primary), anthropic; base holds transport + retry
  web/           the workbench's HTTP surface, a shell over cli.py; static/ is
                 studio/web/'s committed build output and is not edited by hand
studio/web/      the workbench itself. React 19 + Zustand + virtua under Vite;
                 contract.ts is the shared type definition, api.ts the client,
                 store.ts the cold state, drafts.ts the text that must not enter it
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

`studio/web/` is the first thing on that side of the line, since 2026-09-09, and
it arrived without moving any Python: the frontend is a separate source tree with
its own toolchain, and `tests/test_import_boundary.py` never walks it because it
is outside `src/scriptorium/`. What it does not do is make the engine depend on
Node — see the invariant below.

## Commands

```bash
python -m pytest -q                 # 2297 collected, no network. Four are
                                    #   conditional on three different things, so
                                    #   which two skip is a property of the machine
                                    #   AND the account: one is POSIX-only, one
                                    #   needs the privilege to create a directory
                                    #   symlink, and a pair runs only where the
                                    #   filesystem folds case. Compare the collected
                                    #   count, never `N skipped`.
python -m ruff check src tests
python -m scriptorium --help        # or `lx` after `pip install -e .`

cd studio/web && npm ci             # only to CHANGE the workbench; `lx web`
npm run typecheck                   #   needs none of this, because the build
npm test                            #   is committed. 27 tests, jsdom, no network
npm run build                       # writes src/scriptorium/web/static/ — commit it

lx run docs/guide.md --lang zh-TW   # extract -> translate -> check -> repair -> render
lx run book/ch1.md --lang zh-TW --limit 50    # at most 50 segments per pass; run it again to continue
lx extract book/ch1.md --lang zh-TW --from book/whole.md   # carry a split or renamed file's translations across
lx glossary get                     # the terminology rows this project enforces
lx glossary set Ashcombe 灰岸       # decide a rendering, or change one
lx renderings --lang zh-TW          # which names this book renders inconsistently
lx style book/ch1.md --lang zh-TW   # what the model is told about this book's voice
lx suggest book/ch1.md --lang zh-TW # near matches from the memory, advisory
lx renderings --lang zh-TW --term Ashcombe   # every segment naming it, with its target
lx waive book/ch1.md --lang zh-TW --ids s0042   # stand by this wording: its errors report at warn
lx models --provider llamacpp       # ask a backend which models it serves
lx audit --lang zh-TW               # stored wordings that look filed under another source
lx audit book/ch1.md --lang zh-TW   # the same question of one document's segments
lx segments book/ch1.md --lang zh-TW          # every stored segment, source beside target
lx blocks docs/guide.md --lang zh-TW --json   # the rendered document, block by block
lx bytes book/ch1.md --lang zh-TW   # where in the source file each position came from
lx sentences docs/guide.md --lang zh-TW       # where its sentences begin and end
lx web                              # review workbench on 127.0.0.1:8787
lx status --json                    # the frozen project-status contract
lx status --scan ~/books            # every project under a root
```

Run tests before proposing a change as finished. They are fast and cover the
round-trip property, which is the thing most likely to break silently. That
property is exercised by `tests/corpus/` for Markdown and `tests/corpus-text/`
for plain text — one input file per property, read as bytes and substituted back
into the skeleton without going through `render()`. That is `identity_roundtrip`
and it is deliberately narrow: a failure through `render` could be a masking
defect rather than a skeleton one. The corpus is swept through `render` as well,
by `test_every_corpus_segment_reseated_by_accept_still_renders_the_file` and by
`test_docio.py` — worth knowing, because on 2026-09-10 a package concluded from
the sentence above that the render-going property did not exist and scheduled
building it. It did exist; what was missing was a fixture. The plain-text corpus is
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
- **A named next package is read, not remembered.** Every statement of what comes
  next — in a report, in a package, in an answer — is derived from the `priority`
  and `blocked-by` of the files as they stand at that moment. A directory listing
  carries neither field, and its id order is not the pickup order. A `package:`
  blocker sorts before the package that names it, or the field stops meaning
  anything; where it does not, decide it rather than carrying it.
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
- **The workbench's build is committed, and `src/scriptorium/web/static/` is
  generated.** Source in `studio/web/`, output here, tracked. That is the only
  arrangement where the product does not become conditional on a toolchain
  invariant 1 spent its argument on not needing — a bare interpreter, CI, an
  agent sandbox and a locked-down machine all get a working workbench with no
  Node. Committed output has one failure mode and it is silent, so CI rebuilds
  and compares: `npm run build` is part of any change under `studio/web/`, and
  `tests/test_studio_contract.py` asserts the page asks for a bundle that is
  beside it and that the output stayed **flat** — a hashed `assets/` subdirectory
  buys nothing on a transport that forbids caching, and cost a wheel once.

  Two things the frontend may not do, and both are the same rule the CLI follows.
  **No pipeline logic**: not the sentence boundary, not the style-block matcher,
  not routing resolution, not which segments a mode selects. And **no second copy
  of a server rule** — the settings screens send one key per request and render
  the refusal, so `cli.writable_key` stays the only answer to what is writable.

- **Hot state never enters the store, and the target field is uncontrolled.**
  `studio/web/src/drafts.ts` holds the text a reviewer is typing, outside React,
  because a store that re-renders the segment list on a keystroke is a
  virtualized list for nothing. The `<textarea>` takes `defaultValue` and is read
  from the DOM: React's open IME defect is `#3926` — not `#8683`, which closed in
  2018 — and its destructive half is React writing `node.value` back
  mid-composition, which a controlled input re-opens. Every Enter-shaped shortcut
  is guarded on `isComposing` for the other half of the same problem: Enter is
  the candidate-confirmation key in every Chinese input method, and this project
  exists to write Chinese. The one place that writes `node.value` is the effect
  that adopts a stored target, and it is guarded three ways — an unsaved edit
  wins, a focused field is never written into, and identical text is left alone.

- **The frontend reads `contract_version` at startup and refuses a number it does
  not know.** That refusal is the entire reason the field exists. `contract.ts`
  pins it, and three guards chain so the pin cannot drift: type-level assertions
  fail `tsc` when its `RESPONSE_KEYS` and the interfaces disagree,
  `tests/test_studio_contract.py` compares that same object against the contract
  document endpoint by endpoint, and `tests/test_contract.py` compares the
  document against a live reply. Interface ≡ array ≡ document ≡ server.

- **A run is followed, not owned, and one run at a time is a data rule.** Two
  `llm:*` writes to one segment are last-write-wins with no token and no check,
  and nothing on the wire lists running jobs — so the page keeps the job id in
  `sessionStorage` and picks the run up again after a reload, or the guard dies
  with the page. A failed poll is retried rather than treated as the end: HTTP/1.0
  closes every connection and an occasional failure is ordinary. There is no
  Stop, because there is no endpoint that could cancel one; the control says
  "stop following" and says what that does not do.

- **Everything the virtualized ledger does depends on the browser producing
  frames.** `virtua` measures with a `ResizeObserver`, and those are delivered as
  part of the rendering steps — so a tab that is hidden, occluded or in the
  background delivers none, the list measures a viewport of zero and renders no
  rows, with no error anywhere. That is the platform, not a defect to guard
  against in a page nobody is looking at. It is written down because from the
  inside it is indistinguishable from a bug in this project's own code, and it
  cost an afternoon on 2026-09-09: `document.visibilityState` is the tell, and a
  forced screenshot is what makes an automated browser paint.

- **The document's nodes have one walk, and since 2026-09-10 it is
  `skeleton.walk`.** `render_blocks` is its block map and `render` that map's
  join; `formats.Format` carries a `render_blocks` slot beside `render`, and a
  test reads every module in `src/` with `ast` to assert the node list is looped
  over exactly once and read nowhere outside `skeleton.py` and `store.py` — by
  syntax rather than by text, because the first spelling of that guard matched a
  literal string and a rename defeated it.

  **The rule was never "one question", it is "one order".** There are two
  consumers now — `render_blocks` answers what the document says at a position,
  `source_map` which bytes of the file it came from — and both are legitimate;
  what must not happen is two iterations that come to disagree about which node
  is the fourth. A generator makes that unreachable rather than checked, which
  is why the guard now also pins the loop to *inside* `walk` by function name.
  Two of the guard's own blind spots closed in the same edit and both had been
  open since it was written: `doc.get("nodes", [])` is a read a subscript check
  cannot see — `store.save_doc` had been doing it all along, so the allowlist
  and the test's own docstring had silently disagreed — and a loop over a bare
  local named `nodes` is how a post-parse pass would have walked the list a
  second time invisibly. **`cli.do_render` goes through
  `fmt.render` and `cli.do_blocks` through `fmt.render_blocks`, on purpose**:
  written as the join of the other, `Format.render` was dead code and every test
  comparing them compared a value to itself.
  Two walks are two answers to "what does this document say at this position",
  both right the day they are written; the reading view would be reading the one
  nobody writes files from. A block carries **text, never an integer span** — a
  CRLF document shifts every offset because the terminator is re-imposed at
  render, and Python counts code points where JavaScript counts UTF-16 code
  units, so one character outside the BMP desynchronizes them silently. The
  terminator is re-imposed through `docio.apply_terminator_parts`, because the
  blanket `\r?\n` substitution does **not** distribute over a concatenation.

  A stored target is unmasked against **the map its `⟦n⟧` mean** — `target_slots`
  where a re-parse moved the numbering out from under a kept wording, the
  segment's own `slots` otherwise. `store.prior_targets` and `store.tm_record`
  already read it first; this was the one reader of a stored target that did not,
  and the cost was measured on 2026-09-01: a `config/dnt.txt` edit that *swapped*
  one protected term for another rendered the wrong entity with `lx check` green,
  `missing` 0 and `from` `"target"`, because the placeholder ids were equal on
  both sides and every gate here compares ids. `checks.py`'s `numbering` rule
  reports the segment at warn, since the wording still does not speak the
  numbering its source has.

  **A reader that only shows the two texts is a reader of that map too**, which
  is the shape 2026-09-08 added: `lx segments` prints the masked source beside
  the stored target, and on a segment carrying `target_slots` their `⟦n⟧` mean
  different maps, so the two line up on the page and not in the document. It
  does not unmask; it carries the fact, as a `stranded` row that the terminal
  form marks and that names the remedy `do_commit` already names — re-word the
  segment. Anything that puts a stored target next to anything numbered has to
  answer this question, whether or not it substitutes.
- **A per-segment projection names both texts, because `source` alone means two
  different things here.** `do_renderings`' rows and `GET /api/doc` call the
  *masked* text `source`; `do_suggest`'s and `do_audit`'s call the *raw* text
  `source`. Neither is wrong — one matched against the masked text, the other
  compares prose — and a new projection that picks a side silently changes what
  a column holds. `do_segments` carries both under `store.load_doc`'s own key
  names, `source` and `masked`, so the mapping between the commands is
  decidable rather than remembered. The same care applies to `text`, which
  already means the masked source in `lx todo` and the rendered, unmasked,
  polished string in `lx blocks`: it is not available for a third meaning.
- **How much of a document goes to the model in one run is `limit`, and it is one
  cap applied once.** Since 2026-09-02 it bounds every branch of `do_select`
  except an explicitly named `ids` — it reached the pending branch alone before
  that, so a bound was silently inert on `polish` and on `repair` and the wire
  could not express one at all. The cap runs **after** the held and
  origin-precedence exclusions, which is the rule `pending_segments` already
  stated for holds: a run of segments no model may write must not eat a
  `--limit 20` and hand back four. `cli.checked_limit` is the one rule for what a
  bound may be — not a `bool` (`isinstance(True, int)` is true), not negative
  (`out[:-5]` is *everything except the last five*) — and it is checked before
  `ids` short-circuits and applied after, because shape and precedence are two
  questions and a field a client got wrong should not wait for the day they stop
  sending `ids`. A bounded `lx run` needs no rule about rendering: the gate that
  refuses to render while errors remain already answers it, since the work a
  bound left undone is a `missing` error. What it does need is the repair loop
  narrowed to the ids that run itself sent — otherwise round one translates the
  whole remainder — and that narrowing applies **only** under `--limit`, so an
  unbounded run still repairs a carryover wording it did not write.

  **The bound is on spend, not on progress, and nothing may say "the next N".**
  It takes the front of the selection; whether running again reaches different
  segments depends on whether the work *changes what the mode selects*. `draft`
  drains its queue, so it does. `polish` does not — a polished segment is still
  translated prose — so three bounded polish runs ask for the same three
  segments and bill for each, measured 2026-09-02. Three sentences shipped
  reading it as progress and were false on two of the four modes: "run the same
  command again for the rest", a workbench control labelled "Next 25", and
  `lx run` claiming "the rest of the document is still untranslated" whenever
  the flag was set — that one measured saying it to a document **12 of 12
  translated**, whose errors were on a segment a person wrote. A message that
  names the wrong cause is worse than the general one it replaces. `lx run`'s
  refusal now **asks the draft queue** whether anything is left rather than
  assuming from the flag. See `docs/decisions.md`, 2026-09-02.
- **A reply that does not answer the request is thrown away whole, and the object
  judged is the reply.** `translate.misattributed` since 2026-09-04, because a
  model can return every id that was asked for, none extra, and put each answer
  under the *neighbour's* id — measured at 13.3% of segments and 47% of batches
  against the backend this project's maintainer runs. Nothing downstream could
  see it: `accept` refuses a placeholder mismatch and 273 of 273 segments of a
  novel carry no placeholders, so invariant 2b's gate is inert on exactly the
  material the project exists for.

  **Whole reply, never part of one.** A cascade is contiguous but where it starts
  is not decidable from what a reply carries — an extra id names a segment nobody
  asked about, a missing one is at the tail, and a length outlier falls where two
  neighbouring sources differ most, which is systematically late. A partial
  refusal keeps the cascade's head, banks it through `on_batch`, and pays for the
  retries after it anyway. The refusal is spelled `mapping = {}`, which is the
  discard an unparsable reply already took, so every segment falls to the
  per-segment `retry_one` that has always existed and the loop, the return tuple
  and both `contract_version`s are untouched.

  **A neighbour outside the request now gets no field at all**, which reverses
  the operative half of 2026-08-02's D5. An inlined neighbour had no id of its
  own; measured, the model translates it and the answer takes the *first real
  id*. `retry_one` inlined **both** sides, so the branch that exists to rescue a
  segment was the likeliest place in the system to corrupt one — 9 of 25
  single-segment rescues came back carrying a neighbour's translation. Interior
  references by id stay.

  The length arm is `checks.length_ratio`'s band asked at a different moment, and
  it is that function rather than a copy of its arithmetic — a policy with two
  homes is what invariant 8 exists to stop, and the forty-character floor is part
  of the policy. Three arms and no fourth: a `missing_ids` arm and a
  batch-calibrated length arm were both built, scored and removed for adding
  nothing. See `docs/decisions.md`, 2026-09-04.
- **A repair may change a reply's syntax; it may not take content out of it.**
  `translate.parse_reply` returns `(mapping, how)` since 2026-09-07, `how` being
  `clean` or `repaired`. It used to accept valid JSON and nothing else, so one
  stray comma cost the whole batch a request per segment — twenty-five where one
  would have done — and HANDOFF-050 measured about 10% of replies arriving that
  way from the backend this project's maintainer runs. Invariant 5: two of the
  three measured shapes are correctable deterministically, so they are
  corrected.

  **Two rungs, least-edited first, and both are lossless.** A trailing comma
  removed, raw controls inside strings escaped; each is a string-aware character
  scan, each hands its result to `json.loads`, and every answer the model wrote
  survives into what the standard library agreed to read. A reply neither can
  reach still raises, and `run_batch` re-asks its segments one at a time as it
  always has — the refusal is reached by **two** paths, no brace-delimited object
  and no repair that worked, and each needs its own test: replacing the second
  with `return {}, "clean"` passed 823 of them.

  **A scan and not a substitution, and that is not tidiness.**
  `re.sub(r",(\s*[}\]])", r"\1", body)` is global, so a reply carrying a real
  trailing comma *and* the sequence `, }` inside a translated value parses after
  the edit and delivers the sentence with a character taken out of it — valid
  JSON, matching placeholders, `lx check` green. `_FENCE` is the same rule and
  was a live defect: as `re.M` it anchored `^` at every line start, including the
  ones a raw newline inside a value creates, and deleted a translated code
  block's fence lines out of the middle of a sentence. Harmless while the
  mangled reply then failed to parse, and a silently shortened translation the
  moment a repair could read it. It is anchored to the whole reply now.

  **A third rung was built, measured and refused**, and it is the one
  HANDOFF-050 asked for by name: reading the finished `"key": "value"` pairs out
  with a regex, as `research/handoff-046/tolerant.py` does. It recovers shape 3 —
  a reply cut off mid-value — and it is lossy by construction, which is where all
  four of its measured defects came from. Two decide it. **What it drops from a
  truncated reply is the trailing id, and that id is the only evidence
  `misattributed` has for the drift shape**: whole, such a reply is refused and
  every segment re-asked; truncated, the extra id is exactly what the regex
  misses, all three arms pass, and four misfiled wordings are banked with
  `lx check` at exit 0. And a value no JSON parser would read has to be guessed
  at, which deleted three backslashes from `C:\Users\me\Documents` and wrote a
  raw U+0008 into a delivered file — against source the pipeline really does hand
  the model, since `mask.py` has no backslash pattern and 45 of the 3884 segments
  this repository's own documentation parses into carry one. So shape 3 still
  costs a request per segment; `handoff/90-later/HANDOFF-210` carries it, and any
  future attempt must be lossless or hand what it dropped to `misattributed`.

  The counts reach the reader the way `discarded` does — a line per reply and a
  summary at the end, over `progress`, which the contract declares free text.
  The denominator is replies rather than batches because `retry_one` sends one
  too, and the count is taken **before** the parse with a second counter for the
  replies no repair could reach: counting after put the most malformed reply
  there is into neither half of the sentence, and silenced the sentence entirely
  on the run that most needs it. `docs/decisions.md`, 2026-09-07.
- **Where a sentence ends is `sentences.py`, and nowhere else.** Not `checks.py`
  (invariant 4 — it is not decidable without judgement), not the frontend, and
  not the translation-memory key or `store.SEGMENTATION_VERSION`, which a test
  pins as values. The answer is an ordered array of **strings** whose
  concatenation is the input exactly; both offset forms were refused on
  2026-08-17. `lx sentences` and `POST /api/sentences` exist because the rule
  lives in Python precisely so that `lx`, an agent and CI can see it — a rule with
  no command in front of it cannot be. Its known failures are listed in the module
  and in the contract rather than left to be discovered, and **which side of
  invariant 4 a failure falls on is what decides whether it is repaired or
  admitted**: the five marks that open and close with the same glyph are told
  apart by what follows the run, mechanically, and Chinese dialogue attribution
  is not, because telling an attribution verb from an ordinary one needs a verb
  table. See `docs/decisions.md`, 2026-08-21.
- **Whether a stored translation belongs to the source it is filed under is
  `audit.py`, and it may never become a gate.** It is the mirror of the sentence
  rule above: `sentences.py` exists because a rule with no command in front of it
  cannot be seen, and this exists because a rule that needs a network service and
  a threshold cannot be in `checks.py` at all — invariant 10 says `lx check`'s
  exit code is the evidence, and an exit code that depends on a machine being up
  is not evidence of anything. `translate.misattributed` refused a similarity
  test of its own for the same reason and said so in its docstring; what buys
  this one its threshold is that it only ever *reports*. So `lx audit` exits 0
  whenever it ran and 2 whenever it could not, findings never move the exit code,
  and there is no `--strict`.

  **It examines the wordings anything reads, not the lines of the file.**
  `store.load_tm` keeps the last record per key and a repair here is an *append*,
  so a superseded line is dead and cannot be made to go away — an audit over the
  lines would name records nobody reads and go on naming them after the reviewer
  had fixed everything. Measured on the maintainer's own file: 17 lines carry a
  misattributed wording and **2** are what a command would read.

  **A report never says clean, and says what it did not look at.** Two blind
  spots are structural — a target that translates none of the sources in the
  store, and one whose true source is byte-identical to the one it is filed under
  — and the count of records it could not compare is printed beside the count it
  flagged. A record that was not compared is not a record that came back clean,
  and the same asymmetry decides how a malformed reply is treated:
  `Provider._vectors` refuses one whole where `_listing` drops a row. See
  `docs/decisions.md`, 2026-09-06.
- **A glossary row is edited one line at a time, and every other byte stays.**
  `lx glossary get|set|unset` is the editor and `config.glossary_rows` is the one
  parser both it and `load_glossary` read through — the reader and the writer may
  not disagree about which physical line is which row. A write splices the named
  comma-fields into the line's own text rather than rebuilding it from the parsed
  row, which is what keeps a person's spacing, a comment, the order, a line's own
  terminator and a fifth comma-field the parse drops; a canonical rewrite loses
  all of those and **the post-condition guard cannot see that it did**, because
  the guard compares the parse. That guard earns its place elsewhere: the header
  is recognized at raw line index 0 and nowhere else, so removing a line
  renumbers every line after it, and in a headerless file deleting line 0 can
  promote a row into the header's position and silently remove two.

  A row is addressed by its source term, case-insensitively, and **two rows
  naming one term is refused rather than resolved** — no reader takes the first,
  both `checks.check_segment` and `translate._glossary_hints` loop over all of
  them, so two rows are one term with two answers and both are enforced. What the
  format cannot hold is refused before anything is written and named in the
  refusal: a comma, a line separator (including the six `str.splitlines` breaks on
  and Python's line iterator does not), a value of nothing but whitespace, and a
  severity outside `{error, warn}` — the last one closing a live hole, since
  nothing validates severity on the way in and all three comparison sites test
  against the literal `error`.

  **Editing a row invalidates nothing, and it is not a repair.** The memory key
  knows nothing about the glossary, so `.lx/tm.*.jsonl` is byte-identical after a
  write and `lx extract --reset` hands the old wording straight back. What the
  edit does is arm `checks.py`'s glossary rule; the correction is banked by
  `lx commit`, which wins on read because `store.load_tm` keeps the last record
  per key. See `docs/decisions.md`, 2026-09-07.
- **Where one source term was written more than one way is `renderings.py`, it
  infers, and it may never move an exit code.** `lx audit`'s rule for the same
  reason — invariant 10 makes `lx check`'s exit code the evidence and a number
  produced by a threshold is not evidence — so `lx renderings` exits 0 whenever
  it ran, prints its own floors, and has no `--strict`.

  It is **three layers and none of them subsumes another**: the sweep says which
  names are worth looking at, `--term NAME` lists every segment naming one beside
  its target with nothing inferred at all, and `lx glossary set` turns the
  decision into `checks.py`'s mechanical adjudication. The existing glossary rule
  is not the answer on its own — measured 2026-09-07, with the targets filled in
  it finds every drift, but it requires the decision first, never says what the
  *other* rendering was, never lists the segments that comply, and reports a
  legitimate pronoun as an error at `error` severity.

  Two rules the sweep is built on and neither is a knob: a rendering needs two
  supporting segments, and a delta more than one term makes is the target
  language's grammar rather than anybody's rendering — `的` extends every Chinese
  name in a book, `特` extends `Marchmont` and nothing else, so the corpus answers
  the question and **no table of particles is written down for any language**.
  What it finds reliably is two renderings with nothing in common; where they
  share text it usually reports nothing, which is the trade that took a
  1200-segment book from 22 false findings per thirty names to one. See
  `docs/decisions.md`, 2026-09-07.
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
  a literal the source spells, then inline matches, then terms — and a markup
  slot's id is a pure function of the source text. The first of those three is
  2026-09-10's and it does not weaken the conclusion, because the pre-pass reads
  the source and nothing else: what the sentence protects against is
  `config/dnt.txt` moving underneath a banked wording, and terms are still last.
  `docs/decisions.md`, 2026-08-17 and 2026-09-10.

  **A refusal does not delete what the segment already held.** Since 2026-08-17,
  and this is the line between the two: the gate answers whether wording may be
  *written into* a segment as a translation, and `lx extract` had been reading it
  as licence to delete what was there. The refused wording stays with its
  `origin` and its `review`, the segment comes back `translated` and failing, and
  `lx check` reports it — the rule below for a person's words, applied to the
  path that was destroying them. The cost was that `lx render` on a document
  `lx check` has failed wrote the stale `⟦n⟧` into the output; `lx run` refuses
  to render at all. Since 2026-09-03 it does not: `mask.unrenderable` is asked in
  `skeleton.render_blocks`, so a kept wording whose substitution would be
  malformed renders the untranslated marker and is counted in `missing`, while a
  kept wording that substitutes cleanly still writes the words their author
  wrote. **The refusal is at the render and never at the store** — deleting the
  wording is what 2026-08-17 rejected and this does not reopen it.
  `docs/decisions.md`, 2026-08-17 and 2026-09-03, and
  `docs/contracts/workbench-http.md` divergence (24) and (31).

  **Which stored entry a re-parsed segment inherits is decided by position**, in
  the one place that has one — the document's own prior state, never the memory
  key. `store.Carryover.align` **diffs the stored key sequence against the fresh
  one** and takes the matching blocks; what it cannot establish keeps the wording
  the diff paired it with, without its hold or its waiver, and is named by
  `lx extract`. Only a segment the diff paired with nothing falls back to the last
  stored wording under that key. Without this a document holding one sentence
  twice held one entry for two positions and the last row read filled both,
  carrying its `origin`: divergence (25), and the hole under origin precedence
  that needed no race.

  **What is established is decided per matched pair, and the guard never decides
  what the answer is.** A pair is established when the run of equal keys it sits
  in has the same length on both sides and the pair sits at the same offset inside
  it; anything else is delivered anyway and named. Both halves were wrong until
  2026-09-08: the test compared a `Counter` over the *whole document*, and it ran
  only where every element of the matching block carried one key — which a block
  spanning an anchor never does, so the guard was inert on any document with
  unique prose in it and scored equal to having none. Making it truthful while it
  still refused *into* the key fallback was measured to be a net regression, which
  is why the two changes are one change: refusing that way collapsed a run of
  distinct wordings onto the last one and delivered the rest to nobody.
  `docs/decisions.md`, 2026-09-08.

  **A memory hit answers over this document's own wording only when that wording
  is a machine draft.** `store.is_regenerable_origin` — `llm:*`, `tm`,
  `tm:legacy` — and it enumerates what may be replaced, never what is protected,
  so `carryover` (a body older than the `origin` field) and anything a later
  build invents are kept. Invariant 9's line on an ordering question: a machine
  draft is regenerable and a person's sentence is not, and the memory still holds
  what it replaced. Divergence (27), closed 2026-09-01; before it, a `human`
  segment came back as `tm` and stopped being covered by origin precedence, with
  no collision and no race.

  **A carryover can be read out of another document, and that is what a split or
  a rename needs.** `lx extract NEW --lang L --from OLD` points
  `store.prior_targets` at `OLD`'s state instead of `NEW`'s; everything below
  that line is unchanged, so the wording still goes through `translate.accept`
  and the alignment is still `Carryover.align`'s diff. It exists because the
  route this project used to recommend — `lx commit`, then split, then extract —
  is **lossy in two independent ways**, measured 2026-09-04. The memory key
  carries no position and no `doc_id` and `store.load_tm` keeps the *last* record
  per key, so a book holding one paragraph twice with two different translations
  banks two lines, reads back one, and both halves come back with the same
  wording. And `lx commit` refuses a held segment by design, so a wording a
  reviewer was still working on is not banked at all and returns as `pending`
  with `lx check` at exit 1. The carryover loses neither: it is a diff over a
  position sequence, and `review`, `waived`, `origin` and `target_slots` all ride
  in the segment `body`. Four refusals guard it, all decidable before anything is
  read — it is not `--reset`'s companion, it may not name the document being
  extracted, the named document must have state in this language, and the two
  registers must agree, because `prior_targets` freezes the stored register into
  its keys and a mismatch carries nothing while printing `reused 0`. The register
  is therefore resolved from the source document when neither `--tone` nor the
  target's own state answers, which is what makes the ordinary case — a
  `literary` novel in a project still configured `technical` — work at all.
  `docs/decisions.md`, 2026-09-04.

  Two simpler spellings were built and both were wrong — by segment id, which a
  single insertion defeats and a deletion turns into laundering, and by ordinal
  within a key's run, whose size check compared translated rows against parsed
  segments. If a third is ever proposed, the measurement is in
  `docs/decisions.md`, 2026-08-17: twelve edit shapes, scored position by
  position. What the diff still cannot do is recorded as (26) and is reported
  rather than silent; the memory answering over the document's own wording was
  (27) and closed on 2026-09-01.

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
- **What `lx commit` may bank is what `lx check` does not call an error**, per
  segment, and the gate is `checks.check_segment` itself rather than a rule of
  the commit path's own. `.lx/tm.*.jsonl` is a source of truth, it is tracked in
  git, and `store.load_tm` keeps the **last** record per key — so a broken
  wording does not merely add a useless line, it hides the good one already under
  that key and a third document then finds nothing. Measured 2026-09-01, which is
  also what refuted the two cheaper shapes: a placeholder-multiset test in
  `store.tm_records` is satisfied by a swapped pair (`⟦2⟧粗體⟦1⟧` renders
  `</b>粗體<b>` and banks cleanly), and refusing the whole commit on a failing
  document means banking a chapter waits for the book. One rule and one home is
  also what makes `checks_disabled` bind here without a second exception list.
  `cli.do_commit` is the seam both surfaces call, and it exists because this
  decision is what made "three inline `store` calls on each side" untenable.

  **Nor is a wording that speaks a numbering the document has moved on from** —
  one carrying `target_slots`. It renders correctly and `lx check` reports it at
  *warn*, which is precisely why the error gate cannot see it; the memory is read
  by every document in this project under the numbering the project has now, and
  banked, such a wording shadows a correct record under the same key. A third
  list beside `refused` and `held`, because the remedy differs: re-word the
  segment. Found by the adversarial pass over the first version of the gate, which
  is also where the conditional half of the `target_slots` strip came from — see
  `docs/decisions.md`, 2026-09-01.

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

  Since 2026-09-01 a hold also keeps wording **out of the translation memory**,
  and that is the same rule rather than a second one: `lx commit` and
  `POST /api/commit` take a whole document with no per-segment selection, so they
  are batch acts even though a person types one, and the hold is the only thing
  in the request that can say "not this one". Nothing is lost — `tm_records`
  re-derives from the live segment, so an unhold makes the wording eligible for
  the very next commit — and the skipped ids are named on both surfaces. The one
  measured cost is that a held-and-never-committed wording really is gone after
  `lx extract --reset`, which is what that command's message already says.
- **A waiver is a `waived` boolean on the segment body, not a second `review`
  value**, with `lx waive` / `lx unwaive` and `POST /api/waive`. That is a
  measurement and not a preference: `review` holds one string, so a waiver stored
  there takes it from `held` to `waived`, `checks.is_held` goes false, and
  `checks.workable` hands the segment back to the queues the hold took it out of.
  Reproduced 2026-09-03. A second `review` value had already lost three times on
  other grounds — `docs/decisions.md` 2026-08-17 and 2026-09-02, and
  `docs/contracts/workbench-http.md` divergence (24) — and this is the fourth,
  arriving by measurement. Like `review` it costs no `SCHEMA_VERSION` and no
  `STATE_VERSION`, and the two states compose: a segment may be both.

  **It downgrades and never silences.** The finding keeps its rule name and its
  message and moves to `warn`; a `waived` warning names the segment beside it.
  One expression in `check_segment` arms both, so a project that puts `waived` in
  `checks_disabled` turns the feature off rather than keeping the downgrade and
  losing the line that reports it — measured 2026-09-03, that pair produced
  `errors: 0` and `waived: 0` on a document with three capped errors.

  **What may be waived is decided where the finding is made**, as a *required*
  fourth argument to `check_segment`'s `add` — omitting it is a `TypeError`, so
  there is no list of rule names anywhere and a rule added later cannot inherit
  an answer by omission. The property: an issue is waivable when a reviewer's
  judgement can overrule it, and not when it reports the substituted *bytes* are
  malformed, which is invariant 2b's half and not a matter of opinion. It is
  therefore per **instance** and not per rule — `tags` is the case that forces
  it, since one rule name covers a wording that dropped a slot, whose bytes are
  well formed, and one that dropped half a pair, whose bytes are an unclosed tag
  that `pair_problems` explicitly declines to report.

  **The question is asked of the tag text, never of `pair_id`**, and that is the
  correction the adversarial pass forced. `mask` pairs markup within a segment,
  so a `<div>` opened in one paragraph and closed in the next leaves both halves
  `standalone` with no `pair_id` — and the first predicate called that waivable.
  Measured 2026-09-03 on an ordinary Markdown file: `lx check` exited 0 over a
  document that rendered an unclosed `<span>`. `mask.unrenderable` reads the tag
  text through `mask._is_tag_original`, which is `mask.tag_shape` minus the
  autolink it misreads as an open `https` element — made public for this so the
  two answers cannot differ. The same
  pass found the mirror: refusing every `extra` id made `lx run` permanently
  decline a *correct* document whose Chinese repeats a code span, which renders
  legally — so what is refused is an id the render's own map cannot resolve, an
  id whose original is a tag, a lost tag half, and a pair the wording inverted or
  crossed, and nothing else.

  **The predicate is the render's, and that is why it is in `mask.py`.** Since
  the same day `skeleton.render_blocks` asks the identical call on the identical
  segment to decide whether a stored wording may be written into a document at
  all, so the set a reviewer may not overrule and the set a delivered file may
  not carry are one set by construction rather than two lists that agree today.
  It could not live in `checks.py`: `skeleton` → `checks` → `mdparse` →
  `skeleton` is a cycle that raises `ImportError` from every entry point, and
  `mask.target_map` is already there for this reason. Its domain is what the
  placeholder substitution does and nothing wider — `containment` and `escaping`
  are unwaivable too and are still written into the file, which is stated here
  because "the render refuses what a reviewer cannot waive" is the sentence a
  reader will otherwise reconstruct, and it is not true.

  It is pinned to the wording twice over. The stored value is the **token of the
  target** it was granted on, compared at `store._segment` on the way out, so a
  waiver cannot outlive the sentence a reviewer read even under a build that does
  not know the field — a stale hold over-restricts and a stale waiver would move
  the exit code, which is why this one could not be left to a writer. Beside it
  the writers still drop the key outright: `store.save_targets` on every write,
  `store.save_segments` when the target actually moved. A carryover keeps it,
  because `lx run` re-extracts on every invocation; the diff-fallback branch
  drops it, the rule a hold already follows. And the write is a
  **compare-and-swap** on the target the reviewer read: a batch landing in
  between used to leave the flag on wording nobody had seen.

  **A waiver answers a finding and is not a mark of approval.** `do_waive`
  refuses a segment `lx check` reports no error on, whole-request, evaluated with
  the flag forced off so re-affirming one is not refused by the state it put
  there. Left open it put a line in the tracked memory claiming a reviewer had
  overruled a rule that never fired.

  **A waived wording is banked, and its memory line says so.** `lx commit`'s gate
  *is* `check_segment` at error severity, so a waiver takes the segment through
  the gate that already exists — one rule, one home, and no fourth refusal list.
  The line carries `"waived": true`, and **the mark belongs to the wording**:
  `tm_records` keeps it on a later commit of the same target even from a document
  with no waiver of its own, which is what stops it being erased one hop
  downstream and what stops a toggled flag appending duplicate lines to a source
  of truth. The waiver itself does **not** travel: the receiving segment comes
  back unwaived, `lx check` reports it there *where the waived rule fires there
  too*, and `lx extract` names it in `waived_source` on both surfaces. One
  reviewer's judgement about one position is not a judgement about a document
  they have never seen. Measured while building it: a waived wording that dropped
  a placeholder never reaches a second document at all, because `translate.accept`
  refuses it on the multiset — so this path carries `lexicon`, `glossary`,
  `numbers` and the advisory rules, and nothing structural.
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
  `skill/reference/`. One question beyond that list since 2026-09-02: does the
  language write cardinal numbers in CJK numerals? That decides
  `checks._CJK_NUMERAL_LANGS` and nothing else — the numeral reader itself is
  shared, so a language adds a subtag rather than a table.
- New format support: implement `parse(text, dnt, opts) -> (nodes, segments)` and
  register it in `formats.py`. `render` is shared from `skeleton.py` and knows
  nothing about syntax; a format supplies only its untranslated marker, its
  encoding candidates and its config defaults. Do not fork the pipeline. Lookup is
  by extension, overridden by `formats.map` in config and by nothing else — the
  format is frozen onto the document as `doc["format"]` at extract, because a
  skeleton is only readable by the parser that wrote it. An unknown extension is
  refused rather than guessed. The registry serves formats whose document is one
  decoded string; a container format — EPUB — widens it rather than squeezing in.

  **A format's `encodings` is a default and not a constraint**, which is worth
  saying because a sentence claiming otherwise was in this file until
  2026-09-10. `formats.options()` merges a project's `formats.<name>` block over
  the registry's defaults, so one line of `lx.config.json` puts Markdown on
  cp950 — measured, `lx extract` exits 0 and reads it. "Markdown is UTF-8" is
  therefore a fact about the shipped default, and anything that has to hold for
  every document belongs above the registry, in `docio.py`, where it cannot be
  true of one parser and false of the other.
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
