# The workbench HTTP contract

```text
contract_version = 1
```

The request and response surface `lx web` speaks. It is frozen here so that a
frontend — this repository's, another repository's, or one written in another
language — can be built against something that does not move underneath it, and
so that a change to the surface becomes a deliberate versioned act instead of a
side effect of editing `web/server.py`.

**This is not `lx status --json`.** That is a separate contract for a separate
consumer — a read-only progress projection for the bookshelf-and-reader project,
scheduled as HANDOFF-203, and it will live beside this file as
`docs/contracts/status-json.md`. Different consumer, different red lines. The two
were conflated once during triage; this paragraph exists so it does not happen
again.

**Frozen means written down, not finished.** Everything below describes the
server as it actually behaves at `contract_version = 1`, warts included. Where
the behaviour is wrong, it is recorded under *Known divergences* rather than
quietly improved, because a contract that describes an intention is a contract
nobody can implement against.

> **Provenance.** *Request admission*, *Path and language confinement* and the
> security half of *Deliberately not in the contract* state a trust boundary,
> which `docs/conventions/delegated-work.md` §4 puts in the security row. They
> were written below that tier and then **re-derived at it** — from the code
> first, then compared — which returned NOT CLEARED and changed two of them. Both
> rows are in that file's §7 ledger. Read this note as what it is: those sections
> have been attacked once on purpose, which is more than the rest of this
> document can say.

## Versioning

`contract_version` is an integer, reported by `GET /api/state`, and declared by
the fenced block at the top of this file. A test asserts the two agree.

- **Additive changes do not bump it**: a new endpoint, a new response key, a new
  optional request field, a wider accepted value set.
- **Anything else bumps it**: removing or renaming a key, changing a key's type,
  changing what a value means, making an optional field mandatory, narrowing an
  accepted value set, or changing a status code a documented condition produces.

*Lost:* an `X-Scriptorium-Contract` response header on every response, which
would reach a consumer polling `/api/job` without a second request. It loses
because the set of headers this server sends is itself part of the contract (see
*Deliberately not in the contract*), and widening it to carry a version costs more
than it buys: `/api/state` is the endpoint a consumer must call first in any case,
since nothing else tells it which documents exist. *Also lost:* injecting the
field into every response body from `_send`, which would put a contract concern
into the transport layer and change nine response shapes to express one fact.

The package version (`/api/state` → `version`) is **not** the contract version and
must not be used as one. It moves on every release, including releases that change
nothing here.

## Transport

- `http://` on a loopback bind. `serve()` defaults to `127.0.0.1:8787` and prints
  a warning when bound anywhere else.
- Requests and responses are JSON. Request bodies are read as JSON; an absent or
  empty body parses as `{}`. There is no size limit on a request body.
- `protocol_version` is HTTP/1.0, so **every response closes the connection**. A
  consumer must not assume keep-alive. Raising this to HTTP/1.1 is a change that
  carries an obligation, written down in `web/server.py`'s `do_POST`.
- **Every response this server writes itself** carries exactly three headers of
  its own, on top of the standard library's `Server` and `Date`:
  `Content-Type: application/json; charset=utf-8` (or a guessed type for a static
  asset, or `text/plain` for a static refusal), `Content-Length`, and
  `Cache-Control: no-store`. No `ETag`, `Last-Modified`, `Expires`, `Pragma` or
  `Vary`. Responses are not compressed.
- **One response is not written by this server**, and it is the exception to the
  two rules above. Only `GET` and `POST` exist; every other method — `OPTIONS`,
  `PUT`, `DELETE`, `PATCH`, `HEAD` alike — is answered **501 Not Implemented** by
  the standard library's `send_error`, which never reaches this project's code.
  Measured: it carries `Content-Type: text/html;charset=utf-8`, an HTML body, no
  `Cache-Control` at all, and `Connection: close`. A client that parses every
  response as JSON breaks on it. This server never produces a 405.
- Every `/api/*` request is logged to the server's stdout as `  <METHOD> <path>`,
  including refused ones. Query strings are logged with it; request bodies are not.

## Request admission

Normative, and the whole of the access control. There is no cookie, no
`Authorization` header, no session and no token anywhere on this surface: a
loopback bind plus the three rules below is the entire model. A rebuild that
assumes some other guard exists has nothing to catch the assumption.

Applied to **every POST, whatever the path**, and to **GET under `/api/` only**.
Static GETs are deliberately open, because a top-level navigation carries
`Sec-Fetch-Site: cross-site` and gating them would refuse someone opening the
workbench from a bookmark.

Three rules, in this order. A refusal is `403` with `{"error": "<sentence>"}`.

1. **`Host`** must be one of `127.0.0.1:PORT`, `localhost:PORT`, `[::1]:PORT` —
   plus the bare names when `PORT` is 80. Two `Host` headers is a refusal. This is
   the only rule that closes DNS rebinding and the only one that works on a GET.
2. **`Sec-Fetch-Site`**, when present, must be `same-origin` or `none`.
   `same-site` is refused alongside `cross-site`: a page on another loopback port
   is same-site and is not this server. Two of them is a refusal.
3. **`Origin`**, when present, must be a member of
   `{http://127.0.0.1:PORT, http://localhost:PORT, http://[::1]:PORT}`. `http://`
   only, and the comparison is on the lowercased value. Two of them is a refusal.

**Absent is not the same as wrong, for all three.** A request with no `Origin` and
no `Sec-Fetch-Site` is accepted: that is `curl`, an editor plugin, and `lx` itself,
which can read and write these files without asking anyone. `Origin: null` is a
*present* value — a sandboxed iframe, a `data:` URL, a `file://` page, a
cross-origin redirect, an https page posting to this http server — and is refused
by membership.

On a **non-loopback bind** the gate does not merely narrow, it changes shape:
**rule 1 is skipped entirely** — there is no trustworthy set of names to compare a
`Host` against — and rule 3 falls back to comparing `Origin` against the request's
own `Host`. Rule 2 is unchanged. Since rule 1 was the only one that closes DNS
rebinding, and an attacker who controls the name controls both sides of the
degraded comparison, **a non-loopback bind keeps exactly the exposure it already
had**. `serve()` says so when it binds.

Reasoning and the losing alternatives — a CSRF token, in particular — are in
`docs/decisions.md`, 2026-07-29, "The workbench confines every path it is given".

## Path and language confinement

Invariant 11, and it is normative for anyone adding an endpoint. **Read the third
bullet before adding one**: two fields are structurally protected and every other
one is not.

- **`src`** is passed through `cli.confined_path` before anything opens it: both
  sides resolved with `os.path.realpath`, compared with `os.path.commonpath`,
  **rejected rather than clamped**, and the caller's own string handed back rather
  than a canonicalized one. Six mechanical rules run in front of the resolution —
  a NUL, a drive-relative spelling, a drive or share root, an alternate data
  stream, a trailing dot or space, a reserved device name — because resolution
  cannot see any of them, and they are unconditional rather than gated on the
  platform. A refusal is `403`.
- **`lang`** is passed through `cli.language_tag`: letters, digits, `-` and `_`
  only, matched against the whole value, and a non-string is refused as the
  non-string it is. It is not a path but a filename *component*, interpolated into
  `.lx/` filenames. A refusal is `403`.
- **Those two, and only those two, are checked structurally.** Both run at the top
  of `_get` and `_post`, before the path is dispatched on, so they bind **by
  presence of the field rather than by endpoint name** and an endpoint added later
  cannot skip them by being new. Two consequences a consumer will meet:
  `GET /api/state?src=../../etc/passwd` is a `403` even though `/api/state` never
  reads `src`, and so is a malformed `lang` on `POST /api/job`.

  **`out` is not one of them.** `POST /api/render` confines it *inside its own
  branch*, after dispatch — so the protection is real for that endpoint and is
  **not** inherited by anything else. `GET /api/preview?out=…` is not refused; it
  is simply ignored. Any new endpoint that takes a path-valued field under any
  name other than `src` gets no check at all unless its author writes one. This
  is the single most important sentence in this section for HANDOFF-204.
- Presence is spelled differently on the two verbs, and the asymmetry is
  deliberate: `_get` tests `is not None` because a query string cannot carry a JSON
  null, `_post` tests `"key" in body` because `{"lang": null}` must reach the
  validator and be refused.
- **`out` is confined on truthiness, not presence.** `{"out": ""}` means "use the
  default output path" and is not refused. Every non-empty value is confined.
- The default output path itself — `cli.default_output`, formatted from
  `output_pattern` — is **not** confined, because configuration is written by hand
  today. See *Known divergences* (10).

## Endpoints

Ten. Every one is listed here; a test walks the dispatch chain in
`web/server.py` and fails if the two lists disagree in either direction.

Common to all of them:

- `src` names the document. It is a path relative to the project root and the
  server accepts either separator, because `store.doc_id` normalizes `\` to `/`
  before it derives an identity. **The spelling the server hands back is not
  normalized.** `docs[].source`, `/api/doc`'s `source` and `/api/check`'s `source`
  are `os.path.relpath` verbatim, which on Windows is `docs\guide.md`, while
  `candidates[].source` in the same `/api/state` body is `docs/guide.md`. A client
  that compares those two strings is comparing two spellings of one file, **so do
  not compare them.** `candidates[].source` is a value to hand back to
  `POST /api/extract`; `docs[].source` is how a tracked document names itself.

  The server's own comparison between the two was the live half of this and is
  fixed (*Known divergences* (13), closed). A client that genuinely must match one
  to the other has to reproduce the server's document identity, and the rule is
  stated here rather than by naming a Python function, because a consumer of this
  contract may be written in another language and may not read inside `.lx/`:
  **take the path relative to `cwd`, rewrite `\` to `/`, then replace every
  character outside `A-Za-z0-9._-` with `_`.** Two paths that produce one string
  are one document to this server — see (18). That a client should have to know
  any of this is why the label itself is normalized at the next version bump.
- `lang` is a language tag such as `zh-TW`.
- Unless stated otherwise, success is `200`.

---

### GET /api/state

Everything a client needs to draw itself before it knows anything. **This is the
bootstrap endpoint** and the only one that needs no document.

Backed by: no single `cli.do_*`. It composes `config.load_config`,
`store.tracked` (the same read `lx stats` makes), `providers.available` (the
function `lx providers` calls), `config.resolve_route` per stage (what
`lx routing show` prints), and `cli.do_untracked` (what `lx untracked` emits).
Every piece now has a CLI equivalent — *Known divergences* (1), closed
2026-08-14. The `store.tracked` result is read once and handed to
`cli.do_untracked`, which is why that function takes it as a parameter.

**Request** — no fields. `src` and `lang` are accepted and validated if sent, and
then discarded.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `contract_version` | integer | The version of *this document*. `1`. |
| `version` | string | Package version. Not the contract version. |
| `cwd` | string | `os.getcwd()`. The confinement root is `os.path.realpath` of it, which is **not always the same string** — under a junction or an 8.3 short name they differ. Treat `cwd` as a label to show a person, never as an input to a path comparison. |
| `targets` | array of string | Configured target language tags. |
| `providers` | array of *provider* | See *Shared shapes*. |
| `routing` | object | One key per `config.ROUTING_STAGES` — `draft`, `polish`, `repair` — each a *routing stage*. See *Shared shapes*. |
| `docs` | array of object | `{source, lang, total, done}`. `total` is the segment count; `done` is the count with a **non-empty** target, which is not the same predicate as `status == "translated"` — see *Known divergences* (14). |
| `candidates` | array of object | `{source, lang}` — one entry per configured target language for each **document identity** matching the configured `sources` globs that is not already tracked *in that language*. **Not capped.** The unit is the identity and not the file: an identity appears at most once however many globs match it **and however many distinct files map to it** — see (18), which is what that costs. The subtraction is on the same identity, so a separator spelling cannot make a tracked document reappear. `cli.do_untracked` decides all of it; `lx untracked` is the same list. The key is renamed to `untracked` at the next version bump. |

`routing` is a **resolved projection**, not the configured value. The configured
value has two legal spellings on purpose (a provider name, or
`{"provider", "model"}`) and a consumer must not have to know both. Render this;
never read `cfg["routing"]`.

`providers[].base_url` is likewise a projection: it has passed through
`config.printable_url`, which strips userinfo and replaces a query string with
`…`. **It is not the URL to call.** It is the identity function on a URL carrying
neither, so a client that used it would work on ordinary configurations and break
on exactly the credential-bearing ones. Nothing on this surface emits an API key,
and `key_env` is the *name* of an environment variable.

A malformed `routing` **stage** is reported inside the projection, so one bad
stage cannot take the document list down with it. A malformed `providers` block
has no such treatment: it raises, and the whole endpoint becomes a `400` with
nothing in it — no `docs`, no `cwd`, no `contract_version`. See *Known
divergences* (15).

Side effects: none.

---

### GET /api/doc

One document, every segment, with validation issues attached per segment. This is
the endpoint a review pane is built on.

Backed by: `cli.do_check`, called with `persist=False`. The whole response —
segments included — is built from what that call returns; the `store.load_doc`
call above it in the source is dead, see *Known divergences* (16). There is no CLI
command that emits this shape; `lx todo` is the nearest and is not equivalent.

**Request** — `src` (required), `lang` (required), both in the query string. A
missing one is `400` with a sentence naming it.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `source` | string | The document's own `source`, unnormalized — see *Common to all of them*. |
| `lang` | string | |
| `tone` | string | The register frozen onto the document at extract. |
| `report` | object | `{segments, translated, errors, warnings, by_rule}` — **a narrowed subset** of `do_check`'s report. It does not carry `issues`; those are attached per segment instead. |
| `segments` | array of *segment* | See *Shared shapes*. |

`report` here and the body of `POST /api/check` both come from `do_check` and are
**not the same shape**. Do not write one client type for both.

Side effects: none. `persist=False` is what makes that true, and it is the only
difference between this call and `/api/check`'s.

---

### GET /api/preview

The rendered document, as text, without writing it anywhere.

Backed by: `cli.do_render` (with `fallback=True`) and `cli.default_output`.
Equivalent to `lx render <src> --lang <lang> --fallback --out -`.

**Request** — `src` (required), `lang` (required), query string.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `text` | string | The rendered document, with the document's own line terminator re-imposed. |
| `missing` | integer | **A count**, not a list — how many segments fell back to their source instead of a target. |
| `default_out` | string | Where `POST /api/render` would write if given no `out`. |

`fallback` is hardcoded true here and is caller-controlled on `/api/render`, so
the same document previews and renders differently by default — *Known
divergences* (11).

Side effects: none.

---

### POST /api/extract

Parse a source document into skeleton and segments, carrying over what can be
carried over.

Backed by: `cli.do_extract`. Equivalent to
`lx extract <src> --lang <lang> [--tone T] [--reset]`.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `tone` | no | string \| null | the document's frozen register, **unless `reset` is true** | See the warning below. |
| `reset` | no | boolean | `false` | Discards carryover, the existing state row, **and the frozen register**. |

⚠️ **`reset` and `tone` interact, and getting it wrong poisons the translation
memory.** A re-extract that names no `tone` keeps the register frozen onto the
document — *except* under `reset: true`, which does not read the old row at all
and therefore refreezes the register to `tone` if given, else the configured
`tone`, else `technical`. The register is a field of the translation-memory key,
so a `literary` novel re-extracted with `{"reset": true}` and no `tone` comes back
as `technical`, and the next `POST /api/commit` banks the whole book under the
wrong register. Measured 2026-08-13. **A client offering "re-extract from source"
must send the document's current `tone` alongside `reset`, or ask first.**

**Response**

| Key | Type | Meaning |
|---|---|---|
| `segments` | integer | Segments the parse produced. |
| `reused` | integer | Targets carried over from prior state or the memory. |
| `rejected` | integer | Carryover or memory hits **refused** by the acceptance path — a banked wording no longer fits the segment it matched. |

Side effects: writes the document's row in `.lx/state.db`.

---

### POST /api/save

Write reviewed targets. This is what a save button calls.

Backed by: `cli.do_apply`, with `origin` hardcoded to `"human"`. Equivalent to
`lx apply --origin human`.

**Request**

| Key | Required | Type | Notes |
|---|---|---|---|
| `src` | yes | string | confined |
| `lang` | yes | string | whitelisted |
| `targets` | yes | object | `{segment_id: text}`. |

**Response**

| Key | Type | Meaning |
|---|---|---|
| `applied` | integer | Ids that matched a segment and were written. |
| `unknown` | array of string | Ids with no matching segment. They are ignored rather than refused. |

A target is normalized, has its placeholders repaired, and has the blank run at
each end **re-imposed from the source** before it is stored — with one asymmetry:
an indent the reviewer *added* is kept, so a zh-TW paragraph can begin with the
U+3000 pair the language wants even though the English source has no leading run.
It is never *refused* — a person's words are reported at `lx check`, not rejected
at the door. An empty string is a legal target and produces
`status: "translated"` with an empty `target`; see *Known divergences* (14).

**There is no concurrency control of any kind.** No version token, no `If-Match`,
no conflict status. Two clients saving the same segment is last-write-wins, and a
`/api/translate` job running against the same document writes the segments it
finishes — per batch, and again at the end — over whatever a reviewer typed in the
meantime, with `200` on both requests. *Known divergences* (17).

Side effects: updates the touched segment rows in `.lx/state.db`, and nothing else.

---

### POST /api/check

Run the validators and persist the result.

Backed by: `cli.do_check`. Equivalent to `lx check --json`, minus the exit code.

**Request** — `src` (required), `lang` (required).

**Response**

| Key | Type | Meaning |
|---|---|---|
| `source` | string | Unnormalized — see *Common to all of them*. |
| `lang` | string | The `lang` that was sent, not one re-read from the document. |
| `segments` | integer | |
| `translated` | integer | Segments with a **non-empty** target. |
| `errors` | integer | Issues at severity `error`. |
| `warnings` | integer | Every other issue, whatever its severity string. |
| `by_rule` | object | Rule name → count. |
| `issues` | array of *issue* | **Flat**, not grouped by segment. |

Side effects: **writes.** Each segment's issue list goes back into
`.lx/state.db`, and the full report is written to
`.lx/reports/<doc_id>.<lang>.json`.

---

### POST /api/translate

Start a translation run. Returns immediately; the run happens on a background
thread and is polled through `/api/job`.

Backed by: no `cli.do_*` — there is none. The server composes
`translate.translate_segments`, `store.save_targets` per batch, and `cli.do_apply`
at the end, which is structurally what `cli._translate` does. Segment selection
uses `cli.pending_segments` and `translate.failing_segments`. One selection
decision lives only here; see *Known divergences* (2).

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `mode` | no | string | `"draft"` | Selects segments *and* names the routing stage. See the selection table below. |
| `ids` | no | array of string | — | **When present and non-empty, overrides `mode`'s selection entirely.** An empty array is falsy and falls through to `mode`. |
| `provider` | no | string | the routing table's answer | An unknown name fails *inside the job*, not on this request. |
| `batch` | no | integer | `batch.size` from config, else 25 | |
| `concurrency` | no | integer | `batch.concurrency` from config, else 2 | |

What each `mode` selects, which the response's `total` is a count of:

| `mode` | Selects |
|---|---|
| `"draft"` | Segments whose `status` is `pending`. |
| `"polish"` | Segments that have a target **and** whose `kind` is `para`, `quote` or `list`. A translated `heading` or `cell` is silently excluded. |
| `"repair"` | Segments a fresh check rejects with at least one **error**-severity issue. Warnings do not qualify. |
| anything else | As `"draft"`. The value is still forwarded as the routing stage, where an unconfigured stage name falls back to the `draft` entry. |

There is no `model` field. The CLI has `--model`; this endpoint cannot override
the model id, only the provider. *Known divergences* (3).

**Response**

| Key | Type | Meaning |
|---|---|---|
| `id` | string | The job id, to be polled through `/api/job`. |
| `total` | integer | Segments selected, fixed at creation. `0` is a legal answer and means the run does nothing. |

**`200` does not mean the translation succeeded.** It means the job was accepted.
Everything after that is reported through `/api/job` and nowhere else.

Side effects: spawns a daemon thread that calls the configured provider over the
network, writes accepted results to `.lx/state.db` **per batch** — so a workbench
closed mid-run does not discard an hour of model time — and finally applies them.

---

### POST /api/job

Poll a run.

Backed by: no `cli.do_*`, and none is possible. `_JOBS` is in-process, in-memory
state; the CLI runs a translation synchronously and has no job concept at all.
This is a real gap in the CLI, and it is structural rather than an oversight — see
*Known divergences* (4).

**Request** — `{"id": string}`, required.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `id` | string | |
| `done` | boolean | The thread has finished, successfully or not. |
| `total` | integer | Segments selected at creation. |
| `applied` | integer | Segments applied by the **final** apply. It stays `0` on the failure path even though completed batches were already written — read the warning below. |
| `log` | array of string | Progress lines. Free text, not stable, and not to be parsed. The first line names the provider, its model and its `base_url` — in `config.printable_url` form, like every other surface that shows one. |
| `failures` | array | Each entry is a **two-element array** `[segment_id, reason]`. Empty on the failure path. |
| `error` | string \| null | `str(exception)` if the run raised. |

**An unknown id answers `200` with `{"error": "no such job"}`** — a body with that
one key and none of the seven above, not `404` and not `400`. A *failed* job is
also `200`; failure is visible only in the body. *Known divergences* (5).

⚠️ **`applied: 0` with a non-null `error` does not mean nothing was written.**
Accepted batches are committed as they land, so a run that dies partway has
already changed the document while reporting `applied: 0` and `failures: []`. A
client must re-fetch `/api/doc` after *any* terminal state, not only a successful
one.

Job state does not survive a server restart, and there is no way to cancel a
running job.

Side effects: none.

---

### POST /api/render

Render the document and write it to a file.

Backed by: `cli.do_render`, `cli.default_output` and `docio.write_document` — the
same three-call sequence as `lx render`.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `out` | no | string | `default_output(...)` | Confined when non-empty, **inside this endpoint** — see *Path and language confinement*. `""` means "use the default". |
| `fallback` | no | boolean | `false` | |

**Response**

| Key | Type | Meaning |
|---|---|---|
| `wrote` | string | The path actually written — the confined `out`, or the default. |
| `missing` | integer | A **count** of segments rendered from their source instead of a target. |

The file is written as UTF-8 whatever encoding the source was detected in, which
is `lx render`'s behaviour too.

Side effects: **writes a document file.** This is the only endpoint on this
surface that writes anything outside `.lx/`.

---

### POST /api/commit

Bank the document's approved wordings into the translation memory.

Backed by: no `cli.do_*` — `cmd_commit` is itself three inline `store` calls, so
there is no shared function for the server to call. It makes the same calls, in
the same order, and the two are equivalent by inspection rather than by
construction. Equivalent to `lx commit`.

**Request** — `src` (required), `lang` (required).

**Response**

| Key | Type | Meaning |
|---|---|---|
| `committed` | integer | The count of memory lines that were **new** — not the count of translated segments. Committing an unchanged document twice returns `0` the second time, and that is correct. |

Side effects: appends to `.lx/tm.<lang>.jsonl`. Never overwrites.

## Shared shapes

**segment** — an element of `GET /api/doc`'s `segments`.

| Key | Type | Notes |
|---|---|---|
| `id` | string | `s0001`, per document, sequential. |
| `kind` | string | `para`, `heading`, `list`, `quote`, `cell`. Plain text emits only `para` and `heading`. |
| `status` | string | `pending` or `translated`. **It means "a target was written", not "a target exists"** — saving an empty string sets it. Every count in this contract (`report.translated`, `docs[].done`) uses the other predicate, a non-empty target. Do not compute progress from `status`. |
| `origin` | string \| null | Where the target came from: `human`, `agent`, `llm:<mode>` (where `<mode>` is whatever the request sent), `carryover`, `tm`, `tm:legacy`, or `null` when there is none. |
| `source` | string | **The masked text** — placeholders as `⟦n⟧`, not the raw source. Note the name: `lx todo --json` calls the same thing `text`. |
| `target` | string | `""` when absent, never `null`. |
| `issues` | array of *issue* | Only this segment's. |

**issue**

| Key | Type | Notes |
|---|---|---|
| `seg` | string | Segment id. |
| `rule` | string | One of `containment`, `dnt`, `eol`, `escaping`, `glossary`, `length`, `lexicon`, `missing`, `numbers`, `punct`, `spacing`, `tags`, `untranslated`. |
| `severity` | string | `error` or `warn` for every rule the code decides. The `glossary` rule passes column four of `config/glossary.csv` through unvalidated, and `lexicon_extra` does the same, so a hand-edited configuration can put any string here. Anything that is not exactly `error` is counted as a warning. |
| `message` | string | Human-readable. Not stable; do not parse it. |

The rule set is expected to grow. A new rule name is additive and does not bump
the contract version; a consumer must not treat the list as closed, and must not
crash on a `severity` outside the two it knows.

**provider** — an element of `GET /api/state`'s `providers`.

| Key | Type | Notes |
|---|---|---|
| `name` | string | |
| `kind` | string | `openai`, `openai-compatible`, `anthropic`. Echoed from configuration without validation, so a hand-edited file can produce another string; `build()` refuses it later. |
| `model` | string | The provider's own default. |
| `base_url` | string | **Printable form.** Not the URL to call. |
| `needs_key` | boolean | Whether an `api_key_env` is configured. |
| `key_present` | boolean | Whether that variable is set in the server's environment. `true` when no key is needed. |
| `key_env` | string | The variable's **name**. Never its value. |

**routing stage** — a value of `GET /api/state`'s `routing`.

`{"provider": string, "model": string}` when the stage resolves, or
`{"provider": "", "model": "", "error": string}` when its configured entry is
malformed. A malformed stage is reported rather than raised, because this is the
endpoint that draws the whole page and one bad stage must not take the document
list down with it.

## Static assets

Not part of `/api/*`, and stated because a rebuilt frontend replaces this half.

- `GET /` serves `index.html`. Any other path resolves under the package's
  `static/` directory.
- The path is percent-decoded first, backslashes are rewritten to `/`, and the
  result is confined to the static root by the same `cli.confined_path`. A refusal
  is `403` with the plain-text body `forbidden` — not JSON.
- **An unknown path is `404` with the plain-text body `not found`.** It is
  deliberately *not* `index.html` with a `200`. There is one page and no
  client-side router; answering a typo with a success made every mistake render as
  a blank application and made a traversal attempt look as though it had been
  served. A rebuild that adds client-side routes must decide this explicitly
  rather than reaching for the reflex SPA fallback.
- The `Content-Type` is guessed from the extension by `mimetypes` and always has
  `; charset=utf-8` appended, whatever the type.
- Static GETs are not gated by *Request admission*.

## Errors

Every error body **this server writes** is `{"error": "<sentence>"}` — one key, a
string, intended to be shown to a person. The two exceptions are the static
refusals, which are plain text, and the `501`, which is the standard library's
HTML error page.

| Status | Meaning |
|---|---|
| `400` | The request was malformed, or the work failed. This is the catch-all: a JSON parse failure, a missing mandatory field, an unknown endpoint under `/api/`, no state for the document, a state file this build refuses to read, a provider misconfiguration met synchronously. |
| `403` | A control refused the request: the admission gate, `confined_path`, or `language_tag`. |
| `404` | A missing **static** file. `/api/*` never produces a 404 — an unknown `/api/` path is a `400`. |
| `501` | Any method other than GET or POST. Not JSON — see *Transport*. |

There is no error code, no error type and no machine-readable discriminator. A
client distinguishes causes by status and by the endpoint it called, never by
parsing the sentence. Sentences are written to be read and are **not stable**.

Adding a discriminator is additive and would not bump the contract version.

## Deliberately not in the contract

Things that are true because something is **absent**. Each is invisible in a diff,
so each is written down; a rebuild that "restores" any of them reopens what its
absence closes.

- **No `Access-Control-Allow-*` header is emitted anywhere.** This is
  load-bearing, not an omission, and it does two separate things: it forces a
  *non-simple* cross-site call — every JSON POST — to preflight, where the `501`
  ends it; and it makes any response unreadable to a cross-origin script.

  **It does not stop a simple cross-site POST**, which never preflights and
  arrives at the handler. What stops that one is the admission gate's
  `Sec-Fetch-Site` and `Origin` rules, and nothing else — so the gate is the
  primary control here and the missing header is the second layer, not the
  reverse. Written out because the shorter version ("the preflight is why it
  fails") reads as though the gate were redundant, and the gate is what prevents
  the side effect.
- **No `do_OPTIONS`.** A preflight gets the standard library's `501`. If one is
  ever added, it must run through the same admission gate `do_POST` uses — a
  method whose only job is to report capabilities is exactly the one somebody adds
  without threading it through a check.
- **No GET acquires a side effect**, because a GET carries no `Origin` and the
  gate therefore has one rule fewer to work with. `/api/doc` calls `do_check` with
  `persist=False` for this reason and no other.

  One honest exception: opening `.lx/state.db` at all runs
  `PRAGMA journal_mode=WAL`, which creates the `-wal` and `-shm` sidecar files,
  and runs the schema migration if the database was written by an older build. A
  GET against an out-of-date `state.db` therefore *can* execute migrating SQL.
  That is a property of every connection this project opens, not of the GET, and
  it touches no document content — but "no GET writes anything" would be a false
  sentence, so it is not the sentence written here.
- **No way to cancel a running job.** `/api/translate` starts a daemon thread with
  no cancellation flag and there is no endpoint that could set one. A run that
  takes an hour and spends money takes an hour and spends money. A Stop control is
  the first thing a reviewer asks for on a surface like this, and building one
  means adding an endpoint and a check inside the worker — it is not a frontend
  concern, and HANDOFF-204 cannot deliver it alone.
- **No credential ever appears on this surface, and that includes free text.** An
  API key is read from the environment and sent only to its own provider; it is
  never stored, never logged and never in a response. A `base_url` is shown in
  `config.printable_url` form — userinfo stripped, query replaced — everywhere it
  can be seen, which is `providers[].base_url` *and* the `log` and `error` fields
  of `/api/job`, where a provider's own description and its transport failures
  land. The last two were raw until 2026-08-13; a URL is where a credential hides
  in something nobody thinks of as a credential.
- **No authentication of any kind.** No cookie, no `Authorization`, no token, no
  session. Loopback plus the admission gate is the entire model.
- **No concurrency control.** No version token, no `If-Match`, no conflict status,
  no locking. See `POST /api/save`.
- **No caching semantics.** `no-store` and nothing else. A client must not build
  conditional requests.
- **No pagination, anywhere, and no list on this surface is capped.** `/api/doc`
  returns every segment of the document in one response, and it is expected to be
  large. `candidates` was silently capped at 200 until 2026-08-14; the cap is
  gone, and a window over it was examined and refused — with an offset it is this
  bullet's pagination under another name, and it could not reduce the work
  `/api/state` does in any case, because the glob and the full segment load both
  happen before a slice exists to take. A *filter* is the thing to reach for if
  measurement ever shows a need.
- **No streaming and no server-sent events.** `/api/job` is polled.
- **No redirects.** No `3xx` is ever emitted.
- **No request body size limit.** The only cap in the file is on the *refused*
  request drain path, and it exists to keep the socket clean rather than to bound
  a request.

## Reserved

Named so that HANDOFF-204 does not have to guess, and so that the day one of these
lands nobody has to re-derive whether it was a break.

- **A corpus outside the project root.** Today there is exactly **one**
  confinement root, it is the directory `lx web` was started in, and `/api/state`
  reports it as `cwd`. Whether the escape hatch is a config key, a CLI flag or a
  second root is undecided and is recorded in `docs/decisions.md`, 2026-07-29, and
  in HANDOFF-204. **A second root is a contract version bump**, because a consumer
  that read `cwd` as *the* root would then be showing an incomplete picture — a
  meaning change, not an addition.

  **And the mechanism is constrained before the interface is chosen.** `src` is
  the document's identity, and that identity is `os.path.relpath(src)` against the
  cwd, flattened by `store.doc_id`. A path outside the root spells as `../…`,
  which `confined_path` refuses today and which `doc_id` would flatten into
  colliding `.._…` names — the failure already measured under a junction and an
  8.3 cwd. Whatever the escape hatch turns out to be, it has to answer "what is
  this document's identity" first. Do not design a client that assumes one root,
  and do not design one that assumes there will only ever be one.
- **`--allow-origin` for a deliberately exposed bind.** A launch flag, not a
  payload field. **Whether it bumps the contract version is deliberately left
  open, and the reason is the point of this entry.**

  *An earlier draft of this document said it would not bump, because it "changes
  no request or response shape". That sentence is withdrawn.* It classified a
  **trust-boundary** change as a wider accepted value set, which is the additive
  row of the versioning rule above, and the two are not the same axis. Anyone
  implementing the flag from that sentence would have been pre-authorized to
  reopen the load-bearing absence three bullets up.

  Two things it must not be read as permission to do:

  1. **Widening the accepted `Origin` set does not fix what the flag is for.** On
     a non-loopback bind, rule 1 — the `Host` allowlist — is skipped entirely and
     rule 3 degrades to comparing `Origin` against the request's own `Host`. Rule
     1 is the only one that closes DNS rebinding, so an `Origin` allowlist leaves
     rebinding exactly as open as it is now while looking like a fix. The flag has
     to supply the trustworthy set of *names* that cannot be derived off loopback,
     and feed **rule 1** from it.
  2. **Emitting `Access-Control-Allow-*`, or answering a preflight with a
     `do_OPTIONS`, is a different change and a breaking one.** A cross-origin
     browser page cannot read any response from this server without an
     `Access-Control-Allow-Origin` header, so a gate-only widening does not make
     one work — which means the obvious next step for anyone who ships the flag
     and finds their page still broken is exactly the step that reopens
     everything. If it is ever taken: it changes response shape, it bumps the
     contract version, and `do_OPTIONS` runs through the same admission gate
     `do_POST` uses, or it must not be written at all.

  Stated at this length because this contract makes the admission gate normative,
  and a second implementation written from this document has nothing else to go
  on.
- **Configuration over HTTP.** `lx config set` and `lx routing set` exist
  (`cli.do_config_get`, `cli.do_config_set`, `cli.do_config_unset`,
  `cli.do_routing_set`) and no endpoint calls them. The settings surface belongs
  to HANDOFF-204. Three conditions bind whatever adds it, and all three are
  invariant 6:
  1. no field may ever accept an API key;
  2. **`providers.*.headers` is not writable at all** — a header value reaches the
     backend verbatim, so a form that exposes it re-opens exactly what the CLI's
     refusal closes, including under a deeper key such as
     `providers.x.headers.Authorization`;
  3. `config.PATH_VALUED_KEYS` must **either** be confined at use time —
     `output_pattern` on the *result* of formatting it, never on the pattern —
     **or** not be writable over HTTP at all.

## Known divergences

(1) to (17) were measured while freezing this contract, at
`contract_version = 1`, and none was fixed by the package that wrote them down:
that one wrote what is true and versioned it, and each fix is a change that needs
its own decision. Later entries carry their own date. They are recorded in this
tracked file rather than in a work package because packages are deleted.

**This list only grows.** A divergence closed later is marked `Closed` in place
and keeps its number; a new one is appended. The numbers are referenced from
`AGENTS.md`, from work packages and from the decision record, so renumbering
would silently repoint every one of them — which means the section is a history
and its length is not a count of what is outstanding. Read the entries.

Numbers (2) and (3) are the ones HANDOFF-204 must still **decide**, not merely
inherit.

1. **Closed 2026-08-14.** *`/api/state`'s `candidates` had no CLI equivalent.*
   `_scan_sources` globbed the configured `sources` patterns and subtracted what
   was tracked; nothing in `cli.py` did this — it did not import `glob` at all.
   Under invariant 8 that was behaviour living only in the server. `lx untracked`
   is the command it now stands in front of, `cli.do_untracked` decides the list
   for both surfaces, and the silent 200-entry cap went with it.
2. **`/api/translate`'s `mode: "repair"` means `lx repair`, not
   `lx translate --mode repair`.** The endpoint selects failing segments;
   `lx translate --mode repair` selects pending ones, because `cmd_translate` has
   no repair branch. Two CLI commands disagree and the server silently picked one.
   Whichever way it is settled, it is settled in one place, not in the server.
3. **`/api/translate` cannot name a model.** `translate.translate_segments` takes
   `model`, `lx translate --model` forwards it, and the endpoint does not. A
   settings surface that can route a stage to a model but cannot run one is
   half a feature.
4. **`/api/job` is a genuine CLI gap.** Structural rather than accidental: a
   browser request cannot block for the minutes-to-hours a run takes, and a
   terminal invocation can. Recorded so it is not mistaken for leaked logic.
   Whatever replaces it still owes an id that does not depend on `len(_JOBS)` and
   a retention rule — see HANDOFF-204, which carries both.
5. **`/api/job` reports failure as `200`.** An unknown id answers
   `{"error": "no such job"}` at `200`; a job that raised answers `200` with a
   non-null `error`. Status alone never distinguishes a successful poll from a
   failed run.
6. **Two required fields raise a bare `KeyError`.** `/api/save`'s `targets` and
   `/api/job`'s `id` are read by direct subscript, so the 400 body is literally
   `{"error": "'targets'"}` — the repr of a `KeyError`, not a sentence. `_require`
   exists and produces a good message; no POST endpoint calls it.
7. **A missing `src` or `lang` on a POST fails downstream.** `_require` guards the
   two GET endpoints only. On a POST, `None` travels into `load_doc` or
   `store.doc_id` and surfaces as whatever exception Python happens to raise — a
   `TypeError` from `os.path.relpath(None)`, or a `sqlite3.IntegrityError`. Still
   a `400`, but the message is an accident and differs per endpoint.
8. **`/api/doc` calls the masked source `source`; `lx todo --json` calls it
   `text`.** One thing, two names, across two surfaces of the same product.
9. **The job id is minted outside the lock.** `f"job{len(_JOBS) + 1}"` is computed
   before `_JOB_LOCK` is taken, so two simultaneous requests can mint the same id
   and the second overwrites the first's state. `_JOBS` is never pruned. Both are
   already carried by HANDOFF-204.
10. **`default_output` is never confined.** When `/api/render` is given no `out`,
    the path comes from `output_pattern` in the project's configuration, which is
    trusted because it is hand-edited. Invariant 11 names this: the moment
    anything writes configuration over HTTP, that trust is gone.
11. **`/api/preview` hardcodes `fallback=true`; `/api/render` defaults it to
    `false`.** The same document previews with its untranslated segments showing
    source text and renders with them showing a marker.
12. **A POST body is read with no size cap.** `Content-Length` bytes are read
    whole. The cap that exists — 1 MiB — is only on the *refused*-request drain
    path, and exists to keep the socket clean rather than to bound a request.
13. **Closed 2026-08-14, on the separator axis only — see (19) for the other
    one.** *`candidates` never stopped listing a tracked document, on Windows.*
    Reproduced 2026-08-13: extract `docs/guide.md`, then
    `GET /api/state` still returned it under `candidates`. `_scan_sources` built
    its "already seen" set from `docs[].source`, which is `os.path.relpath`
    verbatim — `docs\guide.md` — and built each candidate key with
    `.replace(os.sep, "/")` — `docs/guide.md`. The two never matched, so the
    subtraction was a no-op on any platform whose separator is not `/`: green on
    Linux, wrong on the development machine. Both sides now go through
    `store.doc_id`, which is what the state database keys a document on, so the
    comparison is the project's own identity rather than a rule this list
    invented. **The wire still carries two spellings** — see *Common to all of
    them* — because normalizing the label a client reads changes what a value
    means, and that waits for the next version bump.
14. **`status: "translated"` does not mean the segment has text.** `do_apply` sets
    it for every id in the payload without testing the text, so saving an empty
    string produces `{status: "translated", target: "", origin: "human"}`, while
    `report.translated` and `docs[].done` both count non-empty targets. A progress
    bar computed from `status` disagrees with the two counters in the same
    response.
15. **A malformed `providers` block takes the whole bootstrap endpoint down.**
    `_routing_state` degrades per stage and reports the error inside the
    projection; `providers.available` has no equivalent and raises, so
    `/api/state` becomes a `400` with nothing in it. Since configuration is not
    writable over HTTP, a client in that state has no way to recover and nothing
    to draw.
16. **`/api/doc` reads the document twice and discards the first read.**
    `load_doc(src, lang)` is called and its result is immediately shadowed by
    `do_check`'s own return, which loaded the same row again. Harmless and
    measurable: two SQLite reads per request on the endpoint a review pane calls
    most.
17. **Nothing detects a lost update.** Two clients, or one client and a running
    translation job, write the same segment with no version token and no conflict
    status. On the surface whose entire purpose is human review, a background job
    can overwrite a reviewer's sentence and report `200` to both.

Measured 2026-08-14, by the adversarial pass over the change that closed (1) and
(13). The first is that change's own cost and the other three are older; all four
are on `candidates`, which is what a pass aimed at one list finds.

18. **A `candidates` entry is an identity, so a distinct file can be permanently
    invisible.** The identity flattens every character outside `A-Za-z0-9._-`, so
    `docs/guide.md` and a root-level `docs_guide.md` are one string — and
    `books/第一章.md` and `books/第二章.md` are both `books____.md`. Reproduced with
    nothing tracked at all: two real files matching one glob, one entry in
    `candidates` and in `lx untracked`, no diagnostic. The suppression is faithful
    to storage — `.lx/state.db` keys a document row on that identity, so extracting
    the second would overwrite the first — but the file is then absent from the one
    list whose job is to say what there is. The old code listed both and lost the
    state instead. **Neither surface says which path it collapsed**, and that is
    the part worth fixing: it needs a response field, so it belongs to a version
    that is already changing this key.
19. **`candidates` still lists a tracked document when the two spellings differ in
    case.** The other half of (13), on the axis that fix held constant, and older
    than it. The identity is case-sensitive; NTFS is not. Reproduced on the
    development machine: one file `docs/Guide.md`, then
    `lx extract docs/guide.md --lang zh-TW` succeeds and `lx stats` shows it, and
    `lx untracked` still offers `docs/Guide.md`. Two identities,
    `docs_Guide.md` and `docs_guide.md`; one file. Case-folding the identity is not
    the fix — it would merge two genuinely distinct documents on a case-sensitive
    filesystem — so this waits for the identity to be structural rather than a
    flattened string, which the *Reserved* section already schedules.
20. **`candidates` can carry an entry no endpoint will accept.** The list is a
    glob over `sources` and nothing else: it is not filtered to files, to
    extensions the format registry knows, or to paths inside the confinement root.
    Measured: `sources: ["book/**/*"]` listed a directory and a `.jpg`, and
    `POST /api/extract` answered `400` for both — "has no format this project knows
    how to read"; `sources: ["../shelf/*.md"]` listed a path outside the project
    and `POST /api/extract` answered `403`. Filtering the list narrows what the key
    means, so it is a version decision rather than a patch. The CLI has the same
    list and the same closing line offering `lx extract`, which is where a person
    meets it first.
21. **`candidates[].source` reaches the shipped page's DOM through an unescaped
    HTML attribute.** `static/index.html` builds `data-src="${c.source}"` by string
    concatenation; its `esc()` handles `&`, `<` and `>` and not the quote, and it
    is applied to the visible text only. `docs[].source` is built the same way. No
    `Content-Security-Policy` is sent — see *Transport*, which lists every header
    this server emits. A filename is not always the user's own on a surface whose
    corpus is downloaded novels, and a POSIX filename may contain `"`. The page has
    unauthenticated access to every endpoint here, including one that spends money.
    The static assets are *What is not frozen*'s last bullet and HANDOFF-204
    replaces them wholesale, so this is recorded rather than patched — a rebuild
    that escapes attributes by construction closes it, and one that string-builds
    markup inherits it.

## What is not frozen

Freezing the contract does not freeze the implementation. Free to change without
touching this document, and deliberately so:

- Which Python functions implement any of it, how `web/server.py` is structured,
  and whether it stays Python at all — that survivability is the point of writing
  this down. The `cli.do_*` names in each endpoint's *Backed by* line are the
  exception: they are frozen as the statement of invariant 8's seam, and a test
  fails when the server imports one this document does not name.
- The wording of every `error` sentence and every `log` line.
- The validator rule set, which is expected to grow.
- `.lx/` in its entirety: the SQLite schema, the state version, the report files.
  No consumer of this contract may read inside `.lx/`.
- The static assets, which HANDOFF-204 replaces wholesale.

Reasoning for the contract itself, and the alternatives that lost, is in
`docs/decisions.md`, 2026-08-13.
