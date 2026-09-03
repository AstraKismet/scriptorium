# The workbench HTTP contract

```text
contract_version = 3
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
server as it actually behaves, warts included. Where the behaviour is wrong, it
is recorded under *Known divergences* rather than quietly improved, because a
contract that describes an intention is a contract nobody can implement against.

**Version 2** is the workbench rebuild's M0 floor, and it moves the version
**once** for five items rather than five times: `candidates` renamed to
`untracked`, the identity label normalized, `status` derived from the target
text, an empty target refused, and a lost-update token. Each had been settled as
its own bump; taken as written the sequence was 1 → 2 → 3 → 4 → 5, and a client
is required to read this number at startup and refuse one it does not know, so
every move is a hard stop. A contract that moves five times during the build it
was frozen for has spent the property it was frozen for. Everything after this
that would bump is gated: it becomes a work package, not a commit. See
`docs/decisions.md`, 2026-08-14.

**Version 3** is the first bump through that gate, scheduled as HANDOFF-026, and
it carries **one** item: `POST /api/extract` refuses `reset: true` with no `tone`
and answers `400`. There is no additive spelling of it — the refusal narrows an
accepted value set, turns a documented `200` into a `400`, and the alternative
(keeping the frozen register under `reset`) changes what the request table's
documented default means, so it is on the bump list three ways at once. Three new
response keys landed with it — `kept`, `ambiguous` and `replaced` — and **none of
them needed the move**: a new response key is additive. They ride here because the
same section was being rewritten, and two packages editing one section is how they
collide. The gate is unchanged and still stands: the next bump is the next work
package. See `docs/decisions.md`, 2026-08-19.

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

Fourteen. Every one is listed here; a test walks the dispatch chain in
`web/server.py` and fails if the two lists disagree in either direction.

Common to all of them:

- `src` names the document. It is a path relative to the project root and the
  server accepts either separator, because `store.doc_id` normalizes `\` to `/`
  before it derives an identity.

  **Every `source` this surface hands back carries one spelling**, since version
  2: relative to `cwd`, with `/` as the separator on every platform. That is
  `docs[].source`, `untracked[].source`, `/api/doc`'s `source` and `/api/check`'s
  `source` alike, and they are comparable to each other as strings. Before
  version 2 the first three were `os.path.relpath` verbatim — `docs\guide.md` on
  Windows — beside `docs/guide.md` from the candidate scan, so one body carried
  two spellings of one identity and a client was told not to compare them. Fixing
  only the comparison would have left the condition in place and pushed a
  normalizer into every client; the label is normalized where it is read and
  where it is written instead. *Known divergences* (13), closed on its remaining
  axis.

  **One spelling is not one identity.** Two different paths can still be one
  document to this server, because `.lx/state.db` keys a row on a *flattened*
  form of the label. The rule is stated here rather than by naming a Python
  function, because a consumer may be written in another language and may not
  read inside `.lx/`: **take the path relative to `cwd`, rewrite `\` to `/`, then
  replace every character outside `A-Za-z0-9._-` with `_`.** A client does not
  have to compute it — `/api/state`'s `collisions` reports every set of paths
  that collapses to one — but a client that offers to open an arbitrary path
  needs to know the collapse exists. See (18).
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
| `contract_version` | integer | The version of *this document*. `3`. |
| `version` | string | Package version. Not the contract version. |
| `cwd` | string | `os.getcwd()`. The confinement root is `os.path.realpath` of it, which is **not always the same string** — under a junction or an 8.3 short name they differ. Treat `cwd` as a label to show a person, never as an input to a path comparison. |
| `targets` | array of string | Configured target language tags. |
| `providers` | array of *provider* | See *Shared shapes*. |
| `routing` | object | One key per `config.ROUTING_STAGES` — `draft`, `polish`, `repair` — each a *routing stage*. See *Shared shapes*. |
| `docs` | array of object | `{source, lang, total, done}`. `total` is the segment count; `done` is the count with a **non-empty** target, which since version 2 is the same set as `status == "translated"` — see *Known divergences* (14), closed. |
| `untracked` | array of object | `{source, lang}` — one entry per configured target language for each **document identity** matching the configured `sources` globs that is not already tracked *in that language*. **Not capped.** Named `candidates` before version 2, and renamed so this key, `lx untracked` and HANDOFF-203's forthcoming field spell one word. The unit is the identity and not the file: an identity appears at most once however many globs match it **and however many distinct files map to it** — `collisions` below is what that costs, said out loud. An entry no surface could act on is filtered out: the path has to be a file and its extension has to be one the format registry knows. A path *outside* the project root is not filtered, because `lx extract` can act on one and the endpoint cannot — see (20). `cli.do_untracked` decides all of it; `lx untracked` is the same list. |
| `collisions` | array of object | `{paths, offered}` — one entry per document identity that more than one path maps to. `paths` is every such path, sorted; `offered` is the one carried in `untracked`, or **`null` when no entry was offered for that identity at all** — because a tracked document already holds it, or because no target language is configured to offer anything under. Two paths the filesystem itself calls one file are **not** a collision and do not appear here. **Present and empty** on a project whose paths do not collide, which is most of them, so a client never has to tell "none" from "an older server". Purely diagnostic: a client cannot resolve a collision, a person renames a file. See (18). |

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
| `source` | string | The document's own `source`, in the one spelling — see *Common to all of them*. |
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

The rendered document, as text and as a block map, without writing it anywhere.

Backed by: `cli.do_blocks` (with `fallback=True`) and `cli.default_output`.
Equivalent to `lx render <src> --lang <lang> --fallback --out -` for `text`, and
to `lx blocks <src> --lang <lang> --fallback --json` for `blocks` and `missing`.

**Request** — `src` (required), `lang` (required), query string.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `text` | string | The rendered document, with the document's own line terminator re-imposed. |
| `blocks` | array of *block* | The same document, cut at the positions the skeleton already has. Unconditional — there is no `?blocks=` to switch it off. |
| `missing` | integer | **A count**, not a list — how many segments fell back to their source instead of a target. The list form is `blocks`: the segments this counts are exactly those whose block carries `from` other than `"target"`. |
| `default_out` | string | Where `POST /api/render` would write if given no `out`. |

`"".join(b["text"] for b in blocks)` **is** `text`, byte for byte, and the server
derives one from the other rather than rendering twice.

`blocks` roughly doubles the reply, because it reproduces the whole document a
second time. Dropping the top-level `text` is the obvious saving and it is
**candidate cargo for the next version bump**, not something to do additively.

`fallback` is hardcoded true here and is caller-controlled on `/api/render`, so
the same document previews and renders differently by default — *Known
divergences* (11). One consequence is visible in the block map: `from` is never
`"marker"` on this endpoint, because the marker branch is the one `fallback`
turns off.

Side effects: none.

---

### GET /api/models

What a configured backend says it serves, so a model id can be chosen rather
than typed. **This is the only GET on this surface that leaves the machine** —
`POST /api/translate` has always done so, and carries more: the document text as
well as the credential.

Backed by: `cli.do_models`, and `config.resolve_route` for the answer a *failed*
listing still carries. Equivalent to `lx models [--provider P] --json`, with one
deliberate difference stated under *Known divergences* (32): the command exits
non-zero when a backend cannot be reached and this endpoint answers `200`.

**Request** — `provider` (optional, query string). A provider **name** out of the
project's configuration, never an address: `providers.build` refuses a name that
is not already in the file, so this parameter selects among configured backends
and cannot carry a destination of its own. Absent, or present and empty, means
the backend `routing.draft` resolves to. `src` and `lang` are accepted and
validated if sent, then discarded.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `provider` | string | The backend that was asked, resolved through `config.resolve_route`. **It echoes the `provider` you sent even when no such backend is configured** — `resolve_route` does not validate the name, and `providers.build` is what refuses it, so an unknown name comes back here with the refusal in `error`. `""` only when the whole `routing` block is unreadable. |
| `configured` | string | The model id this project would send **today**, resolved most-specific-first — the caller's `provider`, then the routing entry's model, then the provider's own. `""` when unknown. Present on the failure path too, and that is what it is for. |
| `models` | array of *model* | Sorted by id. **Present and empty** when the listing failed. |
| `error` | string \| null | The sentence when the listing failed, `null` when it did not. **Always present**, like `POST /api/job`'s. |

**model** — `{"id": string, "status": string}`. `status` is `""` unless the
backend volunteers one; llama.cpp's router reports `unloaded` / `loading` /
`sleeping` / `loaded`, and every plain OpenAI-compatible API reports nothing.

**It answers `200` whatever happens.** A backend that is unreachable, that
answers something other than a model list, that needs a key it has not been
given, or a `routing` entry that is malformed — all of them are a `200` carrying
`error`, because the control this feeds must degrade to a free-text field
*carrying `configured`* rather than block a run. A `400` carries a sentence and
nothing else, so a client would have to resolve routing a second time to recover
`configured`, and that rule has one home. Only a malformed *request* is anything
else: the confinement rules above still answer `403`.

**The listing is advisory and gates nothing.** A backend may serve a model it
does not enumerate — a single-model `llama-server` ignores the `model` field
entirely and still answers — so a client that refused to run because
`configured` is absent from `models` would refuse a working configuration. Show
the list; do not enforce it.

**The reply is untrusted input.** Ids and statuses come from whatever `base_url`
names. Every row is filtered at the boundary — any field carrying a character in
Unicode category `Cc`, `Cf`, `Zl` or `Zp`, or longer than 120 characters, is
dropped, the list is capped at 1000 rows, and the reply is refused unread past
4 MB — and `error` has the same categories replaced with `U+FFFD`. The three are
separate controls and each was added because the other two do not cover it: the
field cap bounds one value, the row cap bounds what is serialized back, and the
byte cap bounds what is *parsed to get there*, which a hostile backend was
measured driving to roughly 910 MB on one request thread. **That filter is about control characters and
says nothing about markup**: `<`, `>`, `"` and `'` all pass it, because they are
legal in a model id. A client escapes them, or builds the DOM node rather than a
string.

Side effects: **one outbound HTTP request** to the configured backend, carrying
the `Authorization` header built from that provider's `api_key_env` when one is
set. Nothing on disk. The budget is `min(timeout, 30) s` per attempt with at
most one retry, plus at most 20 s of backoff a slow backend can ask for with
`Retry-After` — so a hung backend occupies one server thread for up to about 80
seconds. That bound is on a backend that stops *answering*; one that answers
slowly but continuously — a byte at a time — is bounded by nothing here, because
the timeout is per socket operation rather than per request. A client shows a
spinner either way. **Never a "retrying" message**: a
llama.cpp router *blocks* the caller while it loads a model rather than
answering `503`, so a slow first request is a slow request.

---

### POST /api/sentences

Where sentences begin and end, in text the client is holding. **The rule lives in
Python and the client computes nothing** — a boundary rule invented in a browser
is a second rule that `lx`, an agent and CI cannot see, which makes a
sentence-level diff impossible outside the frontend.

Backed by: `cli.do_sentences`. Equivalent to `lx sentences <src> --lang <lang>`
for a document's stored text; this endpoint takes arbitrary text instead, because
what a reviewer is editing belongs to no file yet — so the wire can be asked a
question the command cannot. *Known divergences* (30), open: the rule itself is
shared, and what the CLI lacks is a way to hand it text from a terminal.

**The highlight is stale while a reviewer types, and is recomputed on debounce or
blur.** That is the design and not a limitation of it: caret positioning, IME
composition and rendering are the frontend's, and a round trip per keystroke is
not viable. A client that calls this per keystroke has misread the contract.

**Request**

| Key | Type | Meaning |
|---|---|---|
| `texts` | array of string | Required. Each element is split independently. `[]` is legal and answers `[]`. |

Neither `src` nor `lang` is read, because what a reviewer is editing belongs to no
file. **The admission gate in front of this endpoint still binds**, and the
distinction is worth stating exactly: the gate binds by the *presence* of a field
rather than by the endpoint's name, so a `src` that escapes the project root and a
`lang` that is not a language tag are `403` here as everywhere, before this
handler runs. A `src` naming a real file is refused by nothing — it passes the
gate and is then ignored, because this endpoint opens no document, and a path
nothing opens has nothing to confine.

A `texts` that is not an array, or an element that is not a string, is `400`; the
refusal names the field and the index and **never repeats the value**, because a
reviewer's editor buffer is what lands here.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `sentences` | array of array of string | Parallel to `texts` by index. |

`"".join(sentences[i])` **is** `texts[i]`, exactly. A client walks the string with
a cursor rather than searching it — which matters because **two sentences in one
paragraph may be byte-identical**, and a client that located them by searching
would put both highlights on the first one. That is the accepted cost of
returning text; see *The sentence rule* below for the two offset forms that were
refused and why.

The answer depends on the project's `terms.abbreviations` — the same list
`lx terms` reads. A project that adds a word to it changes both answers together.

Side effects: none.

---

### POST /api/extract

Parse a source document into skeleton and segments, carrying over what can be
carried over.

Backed by: `cli.do_extract`. Equivalent to
`lx extract <src> --lang <lang> [--tone T] [--reset]`, where `--tone` stops being
optional as soon as `--reset` is present — see the warning below.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `tone` | **when `reset` is true** | string \| null | the document's frozen register | Required, non-blank, with `reset`. See the warning below. |
| `reset` | no | boolean | `false` | Discards carryover, the existing state row, **and the frozen register**. ⚠️ **Any truthy JSON value is a reset** — not only `true`. That is the rule, and the examples below it are not the definition: `1`, `"yes"`, `[]`-with-a-member, `{}`-with-a-key **and the string `"false"`** are every one of them a reset that discards this document's translations, because a non-empty string is truthy. Only `false`, `null`, `0` and the empty string, array and object are not. See *Known divergences* (28). |

⚠️ **`reset: true` with no `tone` is refused with a `400`, and that refusal is the
whole of version 3.** A re-extract that names no `tone` keeps the register frozen
onto the document; `reset: true` reads no prior row at all — by design, since the
row may be one this build cannot read — so it has no register to keep. Until
version 3 it refroze silently to the configured `tone`, else `technical`. The
register is a field of the translation-memory key, so a `literary` novel
re-extracted with `{"reset": true}` and no `tone` came back as `technical` and the
next `POST /api/commit` banked the whole book under the wrong register, with
nothing in the reply to say so. Measured 2026-08-13; refused since 2026-08-19.
Blank counts as absent — `""` and `"   "` are refused too, because the register
normalizer folds them onto the default and they would land on the same defect.

**A client offering "re-extract from source" must decide the register and send
it.** The instruction this paragraph carried until version 3 — "send the
document's current `tone`" — is withdrawn rather than softened, on two grounds.
Nothing validates a register value: an unrecognized one is accepted and silently
selects the default brief, so a client that guesses is not refused, it is given
the wrong register. And the two surfaces carrying the current register,
`GET /api/doc` and `lx todo`, both refuse a state row from a newer build — which
is precisely the case `reset` exists for, so in that case the current register
cannot be read at all. Where the row *is* readable, `GET /api/doc`'s `tone` is
still the right thing to show the person choosing.

The refusal is `cli.do_extract`'s, not this endpoint's, so `lx`, an agent and
every future client are covered by one sentence rather than by a rule saying a
client must remember something.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `segments` | integer | Segments the parse produced. |
| `reused` | integer | Targets carried over from prior state or the memory **and accepted**. A stored target the acceptance path refused is carried over too, since 2026-08-17, and counts in `rejected` rather than here. |
| `rejected` | integer | Segments where **every** proposal was refused by the acceptance path — a banked wording no longer fits the segment it matched. It counts **segments, not refusals**, and a refusal with an accepted proposal behind it is not one: that segment counts in `reused` and is named in `replaced`. Corrected here on 2026-08-19 — the value has never changed, but this row said "carryover or memory hits refused", which reads as a count of refusals and is `0` on the very case `replaced` was added to report. What *did* change, on 2026-08-17, is that a refusal no longer **deletes** this document's own stored target. See below. |
| `kept` | array of string | Segment ids whose stored target the acceptance path refused and this endpoint **kept anyway**, with its `origin` and its `review`. They come back `status: "translated"` holding wording that fails validation, and `POST /api/doc` carries the error on the segment itself — so this array is a convenience, not the only way to find them. *Known divergences* (24), closed. |
| `ambiguous` | array of string | Segment ids the position diff could not place, which took the last stored wording under their key instead: a new occurrence of a sentence the document already had, a paragraph that moved, or a member of a run of identical paragraphs that changed size. **Which stored wording belongs to which position is not established for these** — check their `origin`. Nothing else on this surface reports it. **Not capped:** past the alignment work budget the diff is skipped for the whole document and every carried segment lands here, which on a novel that is one sentence repeated is every segment in it. *Known divergences* (26), open. |
| `replaced` | array of string | Segment ids where a translation-memory hit was accepted **over wording this document was already holding** — the stored target no longer fit the re-parsed segment and a banked one did. Since 2026-09-01 the wording that gives way is always a machine's (`llm:*`, `tm`, `tm:legacy`); what a person or an agent wrote is kept instead and named in `kept`. So the sentence is not gone — the memory still holds it — but the segment's `origin` is now `tm`, which is worth a reviewer's eye. Nothing else on this surface reports it, and unlike `kept` there is no error to find it by: the segment is `translated` and passes every validator. *Known divergences* (27), **closed**. The array narrowed and the key did not move, exactly as `rejected` did not on 2026-08-17: it means what the run did. |
| `waived_source` | array of string | Segment ids that took a banked wording whose memory line carries `"waived": true` — a reviewer waived it where it was committed. **The waiver did not travel**: the segment arrives unwaived, so `lx check` reports the issue here and this reader decides for themselves. Named because nothing else on this surface would say so — the segment comes back `translated` and, like `replaced`, there is no error to find it by until the check runs. Present and empty when it did not happen. |

**Carrying over is position-aware, and a refusal does not delete.** Two rules
landed on 2026-08-17 and neither moves a key:

- A stored target the acceptance path refuses is **kept**, with its `origin` and
  its `review`. The segment comes back `status: "translated"` carrying wording
  that will fail validation, and `POST /api/doc` shows the placeholder error on
  the segment itself — which is where a client is already looking.
  ⚠️ **The counts do not distinguish this from a refusal that carried nothing.**
  `rejected` covers both, and `reused` deliberately does not count a kept target,
  so the two integers cannot be told apart. `kept` names the ids, and
  `POST /api/doc` carries the error on the segment. *Known divergences* (24),
  closed.
- Two segments whose source text, kind, variant and register are identical are
  told apart by **where they sit in the document** rather than sharing one entry.
  The prior document's key sequence and the fresh one are diffed, and the
  matching blocks decide; what the diff cannot place — a sentence that moved, or a
  *new* occurrence of one the document already had — falls back to the last
  stored wording under that key, without its hold. *Known divergences* (25),
  closed. What is still open is (26), a run of identical paragraphs that changed
  size, and (27), a memory hit answering over wording this document was already
  holding. Both are **named** on this reply since version 3, in `ambiguous` and
  `replaced`.

**The three arrays are segment ids, and they are present and empty when nothing
happened** — a client never has to tell "none" from "an older server", which is
the rule `collisions` already follows on `GET /api/state`. `kept` and `replaced`
are mutually exclusive: one is what happens when every proposal was refused, the
other when a later one was accepted. `ambiguous` is orthogonal to both — it says
the diff could not place the wording, not what became of it — so a segment can
appear in it *and* in one of the other two, and a client that renders them as
three disjoint buckets will double-count. All three name something `lx extract`
has printed since 2026-08-17 and this surface could not say; none of them is new
behaviour.

**What a register change costs is deliberately not on this reply.**
`cli.do_extract` counts it too — the register the document was in, the one it is
in now, and how many translations do not cross — and `lx extract` prints it. It
is not projected because it would arrive too late to be of use: the document's
row is already written by the time a client reads this body, while a control that
changes the register has to **ask first and say what is lost**. Both numbers that
question needs are on the wire before the call — `GET /api/doc` carries the
document's frozen `tone`, and `GET /api/state`'s `docs[].done` counts the targets
a register change drops. It would also be absent on every `reset: true` request,
since `reset` reads no prior row and so detects no change, which is the one
spelling of "start over in another register". Adding it later is additive if that
ever stops being the right answer.

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
| `base` | no | object | `{segment_id: token}` — the `token` this client was shown for that segment. Optional, and **per id**: an id present here is written only if the stored target still hashes to this value, an id absent from it is written unconditionally. Omitting it entirely is exactly the pre-version-2 behaviour. |

**Response**

| Key | Type | Meaning |
|---|---|---|
| `applied` | integer | Ids that matched a segment and were written. Always equal to the size of `stored`. |
| `unknown` | array of string | Ids with no matching segment. They are ignored rather than refused. |
| `stored` | object | `{segment_id: {text, token}}` for every id that was written — the text **as stored**, after normalization and reseating, and its new token. A client does not have to re-read the document to find out what it now holds, which on a five-thousand-segment novel is the difference between a save and a refetch of the whole book. |
| `conflicts` | object | `{segment_id: {text, token}}` for every id refused because its `base` token did not match. The text and token are the **current stored** ones, so a client has something authoritative to present a merge against. Empty when nothing conflicted. |

A target is normalized, has its placeholders repaired, and has the blank run at
each end **re-imposed from the source** before it is stored — with one asymmetry:
an indent the reviewer *added* is kept, so a zh-TW paragraph can begin with the
U+3000 pair the language wants even though the English source has no leading run.
Wording is never *refused* — a person's words are reported at `lx check`, not
rejected at the door.

⚠️ **An empty or all-blank target is refused**, for the whole request, with `400`
and a sentence naming `lx translate --ids`. Nothing in the payload is written,
including the ids that were fine: a workbench save carries every dirty segment at
once, so a partial write would leave a reviewer's other edits half-applied with
no way for the page to say which. The predicate is `str.strip()`, the same one
`checks.py`'s `missing` rule uses. This was a legal target before version 2 and
produced `status: "translated"` with an empty `target` — a segment marked done
with nothing in it, removed from the draft queue by the act of being cleared.
*Known divergences* (14), closed. Only ids that name a segment are tested; an id
that names none is ignored, which is what `unknown` already means here.

**Lost updates are detected, and only when the client opts in.** `base` is the
mechanism and the token is `sha1(target)[:12]` — derived from the text rather
than kept as a revision counter, so two writes producing the same wording are not
reported as a conflict, and so it costs no column and no state version. A
conflict is a `200` with the id in `conflicts`, never a status code: one request
carries a hundred segments and a status cannot say which of them lost. A client
that sends no `base` is last-write-wins exactly as before. *Known divergences*
(17), closed for a client that opts in.

The comparison happens **inside the write**, as a conditional update in one
statement, and that is normative rather than an implementation note: a check
against a snapshot read earlier, followed by an unconditional write, passes for
both of two writers whose reads land before either write, and tells the loser it
succeeded. This surface runs one thread per request, so that is an ordinary
interleaving and not a rare one. It was the first version of this feature and was
measured, not argued.

**A malformed payload is refused, not interpreted.** A target that is not a
string, and a `base` that is not an object, are each a `400` naming what was
wrong, and nothing in the request is written. `base` matters most: sent as a
string it would otherwise be *silently ignored*, so a client that asked for the
check would not get it and would not be told.

Side effects: updates the touched segment rows in `.lx/state.db`, and nothing
else. A refused request writes nothing at all.

---

### POST /api/hold

Hold segments out of every queue that selects work, or return them to it.

Backed by: `cli.do_hold`. Equivalent to `lx hold` / `lx unhold`.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `ids` | no | array of string | `[]` | An id naming no segment is ignored, not refused. An empty list is a no-op. |
| `held` | no | boolean | `true` | `false` lifts the hold. |

**Response**

| Key | Type | Meaning |
|---|---|---|
| `applied` | integer | Segments whose `review` field this request changed or re-affirmed. |
| `unknown` | array of string | Ids with no matching segment. |

**A hold is a `review` value, not a `status` value**, and its vocabulary is
closed — `held` is the only member today, and a closed set rather than a boolean
because `approved` is a workflow stage this project has promised since it made
review distinct from translation.

⚠️ **Holding requires a non-empty target**, whole-request and before anything is
written, with a `400` naming `lx translate --ids`. A hold on an untranslated
segment would say "leave this one to me" about a segment nobody has written yet
*and* remove it from the draft queue by a route that queue cannot see — the same
shape as the empty target `/api/save` refuses. Lifting a hold carries no such
condition: undoing something must never be harder than doing it.

**What a hold does and does not do.** It removes the segment from every predicate
that selects work — the draft queue, the repair set, the polish set — through one
shared helper, `translate.failing_segments` included, which is status-blind and
would otherwise hand a held segment back to the model on every repair round.
It does **not** stop a person editing it: `/api/save` writes a held segment
normally and the hold survives, because the two answer different questions and a
save that quietly released a hold would return the segment to the model's queue
at the moment its wording changed. And it does **not** exempt an id named
explicitly — `ids` on `/api/translate` still selects a held segment, because that
is a person pointing at one rather than a queue sweeping. Lifting is this
endpoint's own act and never a side effect of another.

`checks.py` reports a held segment at **warn** severity, so `lx check` still
exits 0 and a held segment never blocks a render. It is disable-able like any
other rule.

Side effects: updates the `review` field of the named segment rows in
`.lx/state.db`, and nothing else — not `target`, not `status`.

---

### POST /api/waive

Stand by a segment's wording: report the rules a reviewer can overrule at `warn`
on it, instead of failing the build. Or take that back.

Backed by: `cli.do_waive`. Equivalent to `lx waive` / `lx unwaive`.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `ids` | no | array of string | `[]` | An id naming no segment is ignored, not refused. An empty list is a no-op. |
| `waived` | no | boolean | `true` | `false` lifts the waiver. A non-boolean is a `400`; a `null` would read as `false` and *lift* one. |

**Response**

| Key | Type | Meaning |
|---|---|---|
| `applied` | integer | Segments whose waiver this request actually changed. A no-op is not counted, so lifting a waiver nobody placed answers `0`. |
| `unknown` | array of string | Ids with no matching segment. |

**A waiver is its own segment field, not a `review` value.** `review` holds one
string, so a waiver stored there would overwrite a hold: measured 2026-09-03,
`review` went `held` → `waived`, `checks.is_held` went false, and the segment
returned to the queues the hold had taken it out of. The two states are
independent and a segment may carry both.

**It downgrades and never silences.** Every issue stays in `issues`, stays in
`by_rule`, and keeps its message; what moves is severity, from `error` to `warn`,
and only for issues a reviewer's judgement can settle. A `waived` warning names
the segment on the same reply, so a client can always tell a waived segment from
an ordinary one. The exit code of `lx check` follows the severities, so this is
what lets a document with a genuinely-unfixable segment finish.

⚠️ **What a waiver cannot reach.** Whether an issue may be waived is decided
where it is raised, beside its severity, and it is `false` for every rule that
reports the substituted *bytes* are malformed rather than that the wording may be
wrong: `containment`, `escaping`, `eol`, the placeholder **pair** messages of
`tags`, and a `tags` multiset mismatch that either carries an id the segment has
no slot for or drops exactly one half of a pair. Those stay `error` on a waived
segment and still fail the build. There is no list of waivable rule names
anywhere in the code — the answer is a required argument at each call site, so a
rule added later cannot inherit one by omission.

⚠️ **Waiving requires a non-empty target**, whole-request and before anything is
written, with a `400` naming `lx translate --ids` — the rule `/api/hold` follows.
A waiver on an untranslated segment would answer a report nobody has read about
wording nobody has written. Lifting carries no such condition.

**A waiver is pinned to the wording, structurally.** Any write that changes the
target drops it: `store.save_targets` unconditionally, `store.save_segments` when
the stored target actually moved. So a re-translation, a reviewer's edit and a
memory hit each lift it, while `lx run` — which re-extracts every time — keeps
it, because a carryover is the same wording at the same position. A carryover
that could not establish a position drops it, the rule a hold already follows.

**A waived wording is banked.** `lx commit` gates on `checks.check_segment` at
error severity, which a waiver has moved, so a waived segment passes that gate
like any other — and its memory line carries `"waived": true`. The waiver itself
does **not** travel: a segment that takes such a line comes back unwaived, is
reported by `lx check` where it landed, and is named in `waived_source` on
`POST /api/extract`. One reviewer's judgement about one position is not a
judgement about a document they have never seen.

Side effects: updates the `waived` key inside the named segment rows in
`.lx/state.db`, and nothing else — not `target`, not `status`, not `review`.

---

### POST /api/check

Run the validators and persist the result.

Backed by: `cli.do_check`. Equivalent to `lx check --json`, minus the exit code.

**Request** — `src` (required), `lang` (required).

**Response**

| Key | Type | Meaning |
|---|---|---|
| `source` | string | In the one spelling — see *Common to all of them*. |
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

Backed by: `cli.do_select` for which segments the run works on, and
`cli.do_translate` for the run itself — including the per-batch write, which used
to be assembled here. What is left in the server is the job table: the id, the
progress log and the polling, which is the part a browser genuinely needs and a
terminal does not. See *Known divergences* (4) for why that part has no CLI
equivalent, and (2) for the selection copy this replaced.

Before version 2 it also ran `cli.do_apply` over every result once more at the
end. That wrote nothing `save_targets` had not already written — `translate.accept`
normalizes, repairs and reseats before either sees the text, and both set the same
`status` and `origin` and clear the same issues — so its only effect was to rewrite
every segment of the run over whatever a reviewer had edited meanwhile. It is
gone, which shrinks that window from the whole run to one batch. `lx translate`
lost the same sweep in the same change: leaving it on the CLI would have made the
surface invariant 8 calls the product the riskier of the two.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `src` | yes | string | — | confined |
| `lang` | yes | string | — | whitelisted |
| `mode` | no | string | `"draft"` | Selects segments *and* names the routing stage. See the selection table below. |
| `ids` | no | array of string | — | **When present and non-empty, overrides `mode`'s selection entirely.** An empty array is falsy and falls through to `mode`. |
| `provider` | no | string | the routing table's answer | An unknown name fails *inside the job*, not on this request. |
| `model` | no | string | the routing entry's model, else the provider's own | The model id for this run only. Most specific first, exactly as `lx translate --model`: this value, then the routing entry's, then the provider's. A `provider` naming a **different** backend drops the entry's model, because a model id belongs to the backend that serves it — this field survives that, because it was named for this run and for this provider. |
| `batch` | no | integer | `batch.size` from config, else 25 | |
| `concurrency` | no | integer | `batch.concurrency` from config, else 2 | |
| `limit` | no | integer ≥ 0 | `0` | **The most segments this run may send to the model.** `0`, `false`, `null` and an absent key are one value and mean the whole selection. Applied after `mode` has chosen and after the held and origin-precedence exclusions have run, so a run of segments nobody may translate cannot eat the bound. It takes the **front** of the selection and does not advance — see the note below the mode table. **Not applied when `ids` is present and non-empty**, because naming ids is a person pointing at segments; the value is still *checked*, so a malformed one is refused even on a request that would not have used it. Every other value — a string, a float, `true`, a negative, an array, an object — is refused with a `400`, and **no job is started**. `true` and `-5` are named because both are silent in Python: `isinstance(True, int)` is true, and `list[:-5]` is *everything except the last five*. |
| `overwrite_human` | no | boolean | `false` | Let this run replace segments whose stored `origin` is `human`. Off by default — see *Origin precedence* below. |

What each `mode` selects, which the response's `total` is a count of:

| `mode` | Selects |
|---|---|
| `"draft"` | Segments whose `status` is `pending`. |
| `"polish"` | Segments that have a target **and** whose `kind` is `para`, `quote` or `list`. A translated `heading` or `cell` is silently excluded. |
| `"repair"` | Segments a fresh check rejects with at least one **error**-severity issue. Warnings do not qualify. |
| anything else | As `"draft"`. The value is still forwarded as the routing stage, where an unconfigured stage name falls back to the `draft` entry. |

`limit` bounds **every row of this table**, and it did not before 2026-09-02:
until then it reached only the `draft`/pending branch, so a bound was silently
inert on `polish` and on `repair` and could not be expressed on this surface at
all. The bound is one cap applied once to whatever the row selected — not a
per-mode rule — so `{"mode": "polish", "limit": 20}` and
`{"mode": "repair", "limit": 20}` each mean twenty segments. The `ids` row above
is the one exception and states it. `docs/decisions.md`, 2026-09-02.

⚠️ **`limit` bounds spend, not progress, and a client must not label it "the
next N".** It takes the front of the selection, and whether a later run gets
different segments depends on whether working on them *changes what the mode
selects*. For `draft` it does — a translated segment leaves the pending queue —
so repeated bounded runs walk the document. For `polish` it does not: a polished
segment is still translated prose and is selected again, so three consecutive
`{"mode": "polish", "limit": 3}` runs send the **same three segments** and bill
for each. Measured 2026-09-02 against a live backend. `repair` sits between the
two: a segment that is repaired leaves the failing set, one that keeps failing
does not. A client wanting specific segments sends `ids`.

This table is `cli.do_select`, and since 2026-08-15 it is the CLI's answer too.
`lx translate --mode repair` used to select *pending* segments, because
`cmd_translate` had no repair branch at all — two surfaces of one product
answering the same question differently, with the server silently picking one.
The CLI was aligned to this table rather than the reverse, so nothing here moved:
the mirror settlement bumps the version and turns a Repair button into "translate
the rest of the book". *Known divergences* (2), closed. The `ids`-outranks-`mode`
rule in the row above was part of the same disagreement — the CLI tested `mode ==
"polish"` first and silently ignored `--ids`.

**Response**

| Key | Type | Meaning |
|---|---|---|
| `id` | string | The job id, to be polled through `/api/job`. |
| `total` | integer | Segments selected, fixed at creation, and **after `limit` has capped them** — so it is what the run will actually work on and never what it would have without the bound. `0` is a legal answer and means the run does nothing. This surface does not say how many segments were left behind: the number would cost a second selection pass, and for `mode: "repair"` that pass runs every validator over every segment. Re-read `GET /api/doc` after the job reaches a terminal state, which this contract already requires. |
| `route` | object | `{provider, model}` — what this run will actually dispatch to, resolved by the same `config.resolve_route` `/api/state`'s `routing` projection uses. `model` is `""` when neither the request, the routing entry nor the provider names one. A malformed `routing` block answers `{provider: "", model: "", error: "…"}` here rather than failing the request, because this endpoint's documented behaviour is that a routing problem surfaces *inside the job*; resolving eagerly to report the answer must not quietly convert that into a `400`. |

**Why the readback exists.** Without it the only place the answer appears is the
job's first `log` line, which this contract forbids parsing — so a workbench
could not tell a reviewer which model produced the wording in front of them.
*Known divergences* (3), closed.

**Origin precedence.** Since 2026-08-15 a write from this run — origin `llm:*` —
**does not land on a segment whose stored `origin` is `human`** unless
`overwrite_human` is sent. The refused ids come back in `/api/job`'s `refused`,
not dropped in silence. The comparison happens **inside the write**, against the
origin on disk at that moment rather than one read when the run started, for the
same reason the lost-update token's does.

Only `llm:*` is guarded. This project treats an API model, an agent in its own
context and a person as three equal sources of a translation, so an `agent` write
is a peer's and is not restricted; what this stops is the *unattended* pass,
which runs over whatever the queue hands it. `/api/save` writes `human` and is
therefore never the refused writer.

This narrows a sentence under *Deliberately not in the contract* — every
`/api/translate` job was last-write-wins, silently — and narrows it rather than
reversing it: a run still overwrites `agent`, `carryover`, `tm` and its own
earlier output with no token and no check. It does not bump the version. No key
was removed or renamed, no type changed, no status code moved, and `applied`
still means "segments written"; a version-2 client sees a smaller number for a
document it has reviewed and re-reads `/api/doc` after any terminal state, which
this contract already requires of it.

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
| `applied` | integer | Segments **written**, accumulated per batch as they land. It moves during the run and it is right on the failure path. Before version 2 it counted what a final apply touched and therefore stayed `0` whenever the run raised, while completed batches had already changed the document. |
| `log` | array of string | Progress lines. Free text, not stable, and not to be parsed. The first line names the provider, its model and its `base_url` — in `config.printable_url` form, like every other surface that shows one. |
| `failures` | array | Each entry is a **two-element array** `[segment_id, reason]`. Empty on the failure path. |
| `refused` | array of string | Segments this run left alone because a person had written them — see *Origin precedence* on `/api/translate`. Accumulated per batch as they land, like `applied`, so it moves during the run rather than appearing at the end. Empty unless the guard fired. |
| `error` | string \| null | `str(exception)` if the run raised. |
| `usage` | object | What the backend said this run cost, in tokens: `{"prompt": integer, "completion": integer, "total": integer, "replies": integer, "reported": integer}`. **Always present**, with every field `0` until a completion reply arrives — like `GET /api/models`'s fields, and for the same reason: a client reads five integers on every answer rather than branching on a null. `total` is `prompt + completion`, **computed here and never read from the reply**, so it means the same thing on every backend; a gateway's own `total_tokens` may count cached or reasoning tokens that are in neither of the other two. `replies` counts completion responses whose body was parsed; `reported` counts how many of those carried a usage object this project could read. **`total` is a floor, not a cost, unless `reported == replies`** — and `replies: 0` with `reported: 0` means no model was called at all, which is not the same as a run that cost nothing. A backend that omits `usage`, or sends anything but a non-negative integer of at most 10^12 for either field, is counted in `replies` and not in `reported`; a reply whose two fields do not *both* read counts as reporting nothing, so one good half can never move a total the run then calls complete. Written once when the run reaches a terminal state, including the failure path — an interrupted run has already spent what it spent. |

**An id with no record answers `200` with a body carrying `error` and nothing
else** — that one key and none of the nine above, not `404` and not `400`. A
*failed* job is also `200`; failure is visible only in the body. *Known
divergences* (5).

Since 2026-08-15 there are **two such answers**, and a client that distinguishes
them tells its reader two different things. An id at or below the high-water mark
of ids this process has minted has *existed*: its run finished and its record was
dropped, and the sentence says so. Anything else — a higher number, a spelling
that is not a job id, a request after a restart — is `{"error": "no such job"}`,
unchanged. The `error` sentences themselves are not frozen (see *What is not
frozen*); what is stated here is that the shape of both is a `200` with one key.

**Retention.** The most recent **50 finished** jobs are kept. A job that is not
done is never evicted, whatever finishes around it, because its record is the
only way its client learns the outcome. "Most recent" is by **completion**, not
by start: under start order an hour-long chapter minted first would become the
eviction candidate the instant it completed, and its client would poll once, be
told the record was gone, and never find out whether the run succeeded. Ids are a
monotonic sequence and are never reused, which is what makes the high-water mark
above meaningful — and is why the sequence is a counter rather than `len(_JOBS)`,
since a length goes backwards the moment anything is evicted. *Known divergences*
(9), closed.

⚠️ **A non-null `error` does not mean nothing was written.** Accepted batches are
committed as they land, so a run that dies partway has already changed the
document — `applied` now says how much of it, `usage` says what it cost, and
`failures` is still `[]` on that path. A client must re-fetch `/api/doc` after
*any* terminal state, not only a successful one.

`usage` is the one field here that is **not** accumulated as the run goes: it is
written once, when the run ends. So a client polling a job that is not `done`
reads zeros and must not draw a cost from them — where `applied` and `refused`
are meaningful mid-run and are documented that way. The counters live on the
provider object for the length of the run, which is why an interrupted run still
reports them and why a poll before the end cannot see them.

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

Backed by: `cli.do_commit`. Equivalent to `lx commit`. Until 2026-09-01 this was
three inline `store` calls on each surface — "equivalent by inspection rather
than by construction", which was tolerable only while the answer to *what may be
banked* was "everything with a target". It stopped being that on the same day,
and a policy with two homes is what invariant 8 exists to stop.

**Request** — `src` (required), `lang` (required).

**Response**

| Key | Type | Meaning |
|---|---|---|
| `committed` | integer | The count of memory lines that were **new** — not the count of translated segments. Committing an unchanged document twice returns `0` the second time, and that is correct. |
| `refused` | array of string | Segment ids not banked because `lx check` reports an **error** on them. `.lx/tm.<lang>.jsonl` keeps the *last* record per key, so banking a broken wording does not merely add a useless line — it hides the good one already banked under the same key, and a third document then finds nothing. Added 2026-09-01; before it, every non-empty target was banked. |
| `stranded` | array of string | Segment ids not banked because their wording speaks a numbering this document has moved on from — they carry `target_slots`, they render as they were written, and `lx check` reports them at **warn** on the `numbering` rule. That severity is why `refused` does not catch them, and they need their own row for the same reason `kept` does: the remedy is to re-word the segment against the source as it stands, not to fix an error. Banked, such a wording shadows a correct record under the same key and the next document finds nothing usable — measured 2026-09-01. |
| `held` | array of string | Segment ids not banked because they are `held`. A hold is the reviewer's own declaration that the segment is theirs to finish, and this endpoint takes a whole document with no per-segment selection, so the hold is the only thing in the request that can say "not this one". Nothing is lost: an unhold makes the wording eligible for the very next commit. Checked **first**, so a segment that is also stranded or failing appears only here — "unhold it" is the remedy that comes before the others. |

All three arrays are new keys and therefore additive; `contract_version` did not
move for them. What *did* narrow is which segments `committed` counts, and that is a
change to what this endpoint **does** rather than to what a documented value
means — the reading `replaced` and `rejected` are already written under on
`POST /api/extract`.

Side effects: appends to `.lx/tm.<lang>.jsonl`. Never overwrites.

---

### POST /api/config

Write or remove **one** configuration key, from a closed allowlist.

Backed by: `cli.writable_key` for which keys this surface may write at all,
`cli.do_config_set` and `cli.do_routing_set` for a write, `cli.do_config_unset`
for a removal, and `cli.do_config_value` for the readback. Equivalent to
`lx config set` / `lx config unset` / `lx routing set`, **narrowed** — the CLI
writes every key and this writes thirteen patterns.

**Request**

| Key | Required | Type | Default | Notes |
|---|---|---|---|---|
| `key` | yes | string | — | A dotted key. Must match one of the patterns below, or the request is `403`. |
| `value` | for a write | any | — | Presence is `"value" in body`, so a JSON `null` **is** a value — `providers.<name>.api_key_env` takes one to mean this backend needs no key. Leaving the field out is a different thing and is `400`. |
| `unset` | no | boolean | `false` | Removes the key. Must be the JSON boolean; sending it together with `value` is `400`. |
| `confirm_base_url` | for `providers.*.base_url` | boolean | `false` | Must be `true` to write **or remove** a `base_url`. Must be the JSON boolean. |

**Response**

| Key | Type | Meaning |
|---|---|---|
| `key` | string | The key that was written or removed. Echoed; a key name is not a value. |
| `value` | any \| null | The **effective** value afterwards — the merged configuration, not the file — through the same projection `lx config get` prints. `null` means the key now has no value at all. **For a `routing.*` key this is the raw entry**, which is a provider name *or* `{"provider", "model"}`; render `routing` below instead, which is resolved. |
| `providers` | array of *provider* | As `GET /api/state`. |
| `routing` | object | As `GET /api/state`: every stage **resolved**. |

Side effects: rewrites `lx.config.json` in the directory `lx web` was started in.
A refused request writes nothing and leaves the file byte for byte.

**What may be written.** Thirteen patterns, where `*` stands for exactly one
segment:

```text
providers.*.kind          providers.*.timeout        batch.size
providers.*.base_url      providers.*.temperature    batch.concurrency
providers.*.api_key_env   providers.*.max_tokens     batch.max_repair_rounds
providers.*.model         providers.*.retries        batch.context
routing.*
```

Everything else is `403`, and the list is closed rather than filtered: a key is
refused by **not being on it**, so nothing has to be foreseen to be excluded.
Four consequences worth stating, because each is a question somebody will ask:

- **Every path-valued key is refused** — `glossary`, `dnt`, `style`,
  `output_pattern` — and so is `sources`, which is not in
  `config.PATH_VALUED_KEYS` but feeds a glob directly. `output_pattern` is the
  one with teeth: see divergence (10), where `POST /api/render` given no `out`
  writes to a path formatted from it with confinement deliberately skipped.
- **`providers.*.headers` is refused at any depth**, including
  `providers.x.headers.Authorization`. A header value reaches the backend
  verbatim.
- **A whole block is never writable.** `providers`, `providers.<name>`,
  `routing` and `batch` are all `403`, so a value can never land at a key other
  than the one addressed. This is what makes an allowlist keyed on the string
  somebody sent sufficient here, where on the CLI it would not be: every
  admitted key has its own single-value rule, so the descent into a block that
  `lx config set providers.x '{"api_key_env": …}'` needs is never reached. A
  consequence: a provider whose *name* contains a dot cannot be edited through
  this endpoint at all, because no dotted key spells it and the block form that
  would is refused. Use `lx` or edit the file.
- **A key with no field rule is not on the list**, which is most of the
  configuration — `targets`, `tone`, `source_lang`, `formats.map`,
  `lexicon_extra`, `checks_disabled` and the `roots` key the *Reserved* section
  schedules. Adding one is not a line of plumbing: it is the decision to give up
  the paragraph above.

**One key per request.** There is no batch form, for the same reason: a payload
carrying several keys is a block write wearing a different hat. A settings form
sends one request per field, and the server serializes them — two requests
arriving together would otherwise each read the file, set one key and write it
back, so the second would silently revert the first.

⚠️ **`providers.*.base_url` needs `confirm_base_url: true`, on a write and on a
removal alike.** It decides where the document under translation is sent, and
the credential named by that provider's `api_key_env` is read from the server's
environment and sent with it — so changing it silently is a credential redirect
rather than a preference. Removal counts because removal changes it too:
dropping a key that shadowed a shipped provider's `base_url` restores the
factory URL, and dropping a *user-created* provider's leaves the spec without
one, at which point the request falls back to a hardcoded
`http://localhost:11434/v1`. The acknowledgement is keyed on where the write
**lands**, not on the verb.

It is a `400` and not a `403`, deliberately: `403` on this endpoint means "this
key is never writable, whatever you send", and a client that could not tell the
two apart could not decide between asking the person and giving up. See *Errors*
— the sentence is for a person, and the status is what a client switches on.

**No credential is writable, and none is readable.** `api_key_env` takes the
**name** of an environment variable; a value shaped like a key is `400` and the
refusal does not repeat it. `providers[].key_env` and `key_present` are how a
client shows what is configured and whether the variable is set.

**There is no `GET /api/config`.** `/api/state` already projects what a settings
screen draws — `providers` and the resolved `routing` — and a second read
surface is a second thing to keep in step. It projected everything a form needs
**except** `timeout`, `temperature`, `max_tokens` and `retries`, which this
endpoint's `value` answered only after a write; since 2026-09-01 the *provider*
shape carries those four as well, so the exception is closed and the argument
against a second read surface no longer has one.

## Shared shapes

**segment** — an element of `GET /api/doc`'s `segments`.

| Key | Type | Notes |
|---|---|---|
| `id` | string | `s0001`, per document, sequential. |
| `kind` | string | `para`, `heading`, `list`, `quote`, `cell`. Plain text emits only `para` and `heading`. |
| `status` | string | `pending` or `translated`, **derived from the target text** since version 2 — on the way in *and* on the way out, so a row an older build left inconsistent reads back repaired rather than staying wrong forever. It agrees with every count in this contract (`report.translated`, `docs[].done`), which all test a non-empty target. Before version 2 it meant "a target was written", so saving an empty string set it and a progress bar computed from it disagreed with the two counters in the same response. It is also the draft queue's selection predicate, not only a display. |
| `origin` | string \| null | Where the target came from: `human`, `agent`, `llm:<mode>` (where `<mode>` is whatever the request sent), `carryover`, `tm`, `tm:legacy`, or `null` when there is none. |
| `source` | string | **The masked text** — placeholders as `⟦n⟧`, not the raw source. Note the name: `lx todo --json` calls the same thing `text`. |
| `target` | string | `""` when absent, never `null`. |
| `review` | string \| null | The review state, from a **closed** vocabulary: `held`, or `null`. Always present rather than omitted when absent, so a client does not have to tell "not held" from "an older server". `held` means no queue that selects work will take this segment — see `POST /api/hold`, which is the only thing that sets or clears it. |
| `waived` | boolean | Whether a reviewer has waived this segment's wording, so that the rules judgement can overrule are reported at `warn` on it instead of failing the build. Always present rather than omitted, the rule `review` follows. **Its own field and not a `review` value**, so a segment can be both held and waived — `review` holds one string, and a waiver written there would delete a hold. Set and cleared only by `POST /api/waive`, and dropped by any write that changes the target. |
| `token` | string | What `POST /api/save`'s `base` takes for this segment: `sha1(target)[:12]`, where an absent target hashes as `""`. Opaque — a client stores it and hands it back, and must not compute or compare it beyond equality. |
| `issues` | array of *issue* | Only this segment's. |

**issue**

| Key | Type | Notes |
|---|---|---|
| `seg` | string | Segment id. |
| `rule` | string | One of `bare_term`, `containment`, `dnt`, `eol`, `escaping`, `glossary`, `held`, `length`, `lexicon`, `missing`, `numbering`, `numbers`, `punct`, `spacing`, `tags`, `untranslated`, `waived`. |
| `severity` | string | `error` or `warn` for every rule the code decides. The `glossary` rule passes column four of `config/glossary.csv` through unvalidated, and `lexicon_extra` does the same, so a hand-edited configuration can put any string here. Anything that is not exactly `error` is counted as a warning. |
| `message` | string | Human-readable. Not stable; do not parse it. |

The rule set is expected to grow. A new rule name is additive and does not bump
the contract version; a consumer must not treat the list as closed, and must not
crash on a `severity` outside the two it knows. `held` was added on 2026-08-15
under exactly that rule, at **warn** severity, so a held segment never blocks a
render — a severity that failed `lx check` would make lifting every hold the only
way to finish a book. `bare_term` (2026-08-17) and `numbering` (2026-09-01) were
added the same way and are warn for the same reason; a test now derives this list
from `checks.py` with `ast`, because it had drifted — `bare_term` was missing from
it for a fortnight and nothing could tell.

**block** — an element of `GET /api/preview`'s `blocks`.

Documented here rather than inside the endpoint's own *Response* section on
purpose: the test that compares each endpoint's documented keys against a live
reply harvests every `| \`key\` |` row between `**Response**` and
`Side effects:`, so a nested table there would silently join `/api/preview`'s
top-level key set.

| Key | Type | Notes |
|---|---|---|
| `id` | string \| null | The *segment* id this block renders, or `null` for a run of skeleton the pipeline did not translate. `null` **is** the discriminator — there is no `type` tag — and it is spelled the same way `GET /api/doc`'s segment `id` is, so the two join on it. |
| `kind` | string \| null | The segment's block kind, the same enum as *segment*'s `kind`. `null` for skeleton. Without it a reading view cannot typeset a chapter opening as a chapter opening: `# Chapter One` is a skeleton run of `# ` beside a `heading` segment, and the alternative is a client parsing Markdown out of the neighbouring skeleton text. |
| `from` | string \| null | Which branch of the render produced `text`: `target` (the stored translation), `source` (the masked source, unmasked, because the caller asked for a fallback), or `marker` (the format's untranslated marker). `null` for skeleton. It names the branch outright rather than leaving a client to reconstruct it from `status`, the `fallback` it asked for and the marker string, and it is the key that lets `missing` stay an integer. **Where the two answers actually part company is a row an older build left behind**, and the sentence used to imply a reachable state: the render branches on a *truthy* target while `status` is derived from a *stripped* one, so a target of three spaces renders its own text, reports `pending`, and is not counted by `missing` — but no writer produces one now. `do_apply` refuses a blank target at the door (2026-08-14) and `translate.accept` refuses it as an empty translation, so a state file written before those is the way that row is reached. |
| `text` | string | What this position contributes to the rendered document, with the document's line terminator already re-imposed — so `"".join(b["text"])` is `/api/preview`'s `text`. It is neither `segment.source` (masked) nor `segment.target` (stored masked): placeholders are gone and real markup is back — **against the map the wording's ids mean**, which for an ordinary segment is its own and for one a re-parse stranded is the map it was written in. Before 2026-09-01 it was always the segment's own, so a stranded wording rendered the wrong original with nothing reporting it; the field's meaning did not change and this sentence says which map so that a reader does not have to assume the other one. **It may be empty**, rarely; nothing here promises otherwise. |

`status`, `origin`, `review`, `token` and `issues` are deliberately **absent**. All
five are already frozen on *segment* and a client joins them on `id`; carrying
them twice is one field on two endpoints, which is the drift invariant 8 names
and which divergences (2) and (3) already cost once.

**Offsets are deliberately absent, and this is a decision rather than an
omission.** A block carries its text, never a start and end position in
`/api/preview`'s `text`. Integer offsets are wrong twice over and neither error
shows on the LF-only ASCII fixtures a test reaches for first: a CRLF document
shifts every one of them, because the document's terminator is re-imposed at
render rather than held in the skeleton; and Python counts **code points** where
JavaScript counts **UTF-16 code units**, so a single character outside the BMP —
routine in Chinese names — desynchronizes the two silently, with no error
anywhere. A later proposal to "just send offsets" is a decision to reopen, with
an entry in `docs/decisions.md`.

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
| `timeout` | number \| null | Seconds. |
| `temperature` | number \| null | |
| `max_tokens` | number \| null | |
| `retries` | number \| null | |
| `error` | string | **Present only when this provider's block cannot be read** — the shape the *routing stage* below already had. The other eleven keys are still there and still hold values of the type above, so a client can render the row either way; what it must not do is treat a row carrying `error` as configured. A spec whose `api_key_env` is unreadable reports `needs_key: true` and `key_present: false` rather than the "no key needed" pair, which would be a green light nobody earned. Added by the change that closed (15). |

The four numeric knobs were added on 2026-09-01 so that a settings form can
prefill them; they are additive and did not bump. **Their `null` is a value and
not a failure**: it means the key is absent from the spec, so the transport's own
default applies — 120 s, 0.2, 4096, 3 — and a client must render a blank rather
than write those numbers into a box, or pressing Save pins an inherited value.
What is reported is what a run will actually use: a string a `float`/`int`
accepts is *coerced*, exactly as `Provider.__init__` coerces it, because
`"timeout": "300"` translates perfectly today and calling it unreadable would be
a false accusation on a working configuration. A value no coercion accepts is
`null` **and** named in `error`, because that one really cannot be built.

**routing stage** — a value of `GET /api/state`'s `routing`.

`{"provider": string, "model": string}` when the stage resolves, or
`{"provider": "", "model": "", "error": string}` when its configured entry is
malformed. A malformed stage is reported rather than raised, because this is the
endpoint that draws the whole page and one bad stage must not take the document
list down with it.

## The sentence rule

`POST /api/sentences` and `lx sentences` answer with **text**, in order, and the
concatenation of the answer is the input exactly. Two forms were put to the
maintainer on 2026-08-17 and both were refused: offsets in UTF-16 code units, and
offsets in Python code points. They lose to the same hazard the block map's own
offsets lose to, which is the argument for stating it once and applying it twice.

What the rule decides, normatively:

- A run of `。！？.!?` ends a sentence, unless what follows says it did not — a
  lower-case letter continues, and a mark that cannot begin a line in Chinese
  typesetting (`，、；：,;:` and the bracket-shaped closers) means the sentence is
  still open. `他嘴裡念著「快跑！」，腳下卻沒動。` is one sentence.
- A run of `…⋯` alone is weaker: it ends a sentence only before an opening mark,
  before a capital, or at the end of the text. `「我不知道……」她輕聲說。` is one
  sentence and `一、二、三……十。完了。` is two.
- A lone ASCII `.` ends a sentence only when whitespace or the end of the text
  follows it — which is the whole of what keeps `3.14` and `example.com` in one
  piece — and only when the word in front of it is neither a configured
  abbreviation nor a lone capital. `J. R. R. Tolkien` and `U.S.` stay whole.
- Closing marks are pulled in, so `」` and `”` stay with the sentence they close.
  Each of those has a different opening twin, so finding one after a full stop
  settles what it is doing there and it is taken with no further test.
- **A mark that opens and closes with the same glyph is taken only when the run
  of them is followed by whitespace or by the end of the text.** `"`, `'`, `*`,
  `_` and `~` are the five — the last three because emphasis reaches a segment
  unmasked — and the run is taken whole or not at all, or `**She stayed.**` would
  end after the first asterisk of a pair. Until 2026-08-21 they were taken
  unconditionally; English never noticed, because a space follows the stop and the
  question never arose, and Traditional Chinese always did, because no space
  follows `。`: `他走了。**她留下。**` came back as `他走了。**` and `她留下。**`.
- **A `⟦n⟧` run is an atom**: no boundary falls inside one or between two adjacent
  members, and a run glued to a terminator belongs to the sentence that ended.
- Whitespace after a boundary belongs to the sentence that ended, which is what
  makes the concatenation exact without anyone deciding where a run of blanks
  "really" goes. `""` answers `[]`; every other input answers at least one
  element.

**Known failures, stated rather than discovered.** An abbreviation that genuinely
ends a sentence keeps it open (`He turned onto Main St. Then he stopped.`). A
sentence-final lower-case opener merges (`The bell rang. iPhone screens lit up.`).
An enumerated run reads as sentences (`1. First item. 2. Second item.`). In
*rendered* text a full stop inside restored markup — a URL, a code span — can take
a boundary, because the rule is handed text and not slots; masked text does not
have the problem, since a placeholder is an atom.

**Chinese dialogue attribution over-splits**, and it is the one that matters most
here, because the primary use case is a novel. `「站住！」他喊。沒有人停下。` comes
back as three pieces and `他喊。` is half a sentence. The continuation test after a
strong run is `str.islower` on the word that follows, which is `False` for every
Chinese character — so English is protected by it and Chinese cannot be, on the
same construction. Telling an attribution verb from an ordinary one needs a verb
table, and that is judgement rather than a rule a program decides, so it is
admitted here rather than repaired. The *comma* form is already correct:
`他嘴裡念著「快跑！」，腳下卻沒動。` stays whole, because `，` cannot begin a line.

The rule is **not** a validator and never will be: invariant 4 admits a rule to
`checks.py` only when a program decides it without judgement, and this one does
not.

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
| `403` | A control refused the request: the admission gate, `confined_path`, `language_tag`, or — on `POST /api/config` only — the configuration allowlist. On that endpoint `403` means the key is never writable over HTTP whatever the value, and everything a caller can fix by changing the payload is a `400`, the acknowledgement `providers.*.base_url` needs included. |
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

  **A second honest exception, since 2026-09-01: a GET that leaves the
  machine.** `GET /api/models` opens an outbound request to whatever `base_url`
  names and sends the `Authorization` header built from that provider's
  `api_key_env` with it. Nothing on disk changes, and the sentence above stays
  true of *project state* — but a GET on this surface is no longer confined to
  the project directory, and a rebuild reading the absolute form would conclude
  that it is. What bounds it: the destination comes only from the project's own
  configuration, because `providers.build` refuses a `?provider=` that is not
  already in the file, so the parameter chooses among configured backends and
  cannot supply an address; the effect is a bounded read against a backend the
  person configured, costing no tokens; and the reply is unreadable to a
  cross-origin script, because no `Access-Control-Allow-*` header is emitted
  anywhere. What is *not* bounded away is request amplification — a page that
  gets past the admission gate can make the workbench ask its own backend for a
  listing, and each such request occupies a thread for up to about 80 seconds.
  `POST` was the alternative and would have gained the `Origin` rule; it lost
  because a listing is a read and HANDOFF-035 specifies the method. See
  `docs/decisions.md`, 2026-09-01.
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
- **No locking, and no server-side merge.** Version 2 added a per-segment token
  and a conflict report, and stopped there deliberately: nothing is locked,
  nothing is queued behind anything, and the server never merges two versions of
  a sentence. A conflict is handed back with the current text for the client to
  present, and it is the client that decides what to do with it. Of two writers
  sending the same token, exactly one write lands and the other is told; neither
  waits for the other. A writer that sends no `base` is still last-write-wins.

  **Narrowed, not reversed, on 2026-08-15.** That last sentence used to end
  "— including every `/api/translate` job — is still last-write-wins, silently".
  Two of its three words are still true and one is not: an `llm:*` write no
  longer lands on a segment whose stored `origin` is `human`, and the ids it
  left alone come back in `/api/job`'s `refused` rather than nowhere. Everything
  else is unchanged — a run still overwrites `agent`, `carryover`, `tm` and its
  own earlier output with no token and no check, and a person saving over a
  person is still last-write-wins unless they sent a `base`. Recorded here at
  length because this bullet is the load-bearing kind: somebody implementing a
  second client reads it as the whole of what protects a reviewer's sentence, and
  it now protects slightly more than it did.
- **No caching semantics.** `no-store` and nothing else. A client must not build
  conditional requests.
- **No pagination, anywhere, and no list on this surface is capped.** `/api/doc`
  returns every segment of the document in one response, and it is expected to be
  large. `untracked` was silently capped at 200 until 2026-08-14; the cap is
  gone, and a window over it was examined and refused — with an offset it is this
  bullet's pagination under another name, and it could not reduce the work
  `/api/state` does in any case, because the glob and the full segment load both
  happen before a slice exists to take. A *filter* is the thing to reach for if
  measurement ever shows a need, and version 2 applied the one filter that is not
  a cap: an entry no surface could act on is not offered as work. See (20).
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
- **Configuration over HTTP. Landed 2026-08-20 as `POST /api/config`** — read
  that section rather than this entry, which is kept for the three conditions it
  set and how each was met. All three are invariant 6, and the endpoint answers
  each by *refusing* rather than by guarding:
  1. no field may ever accept an API key — `api_key_env` takes a name, and the
     validator that refuses a key-shaped value is `cli.py`'s, reached rather than
     restated;
  2. **`providers.*.headers` is not writable at all**, at any depth — it is not
     on the allowlist, and neither is the block form that would carry it;
  3. `config.PATH_VALUED_KEYS` had to be **either** confined at use time
     — `output_pattern` on the *result* of formatting it, never on the pattern —
     **or** not writable at all. It is not writable. Confining the formatted
     result inside `/api/render` was the larger of the two trust surfaces and
     bought one configuration key; `docs/decisions.md`, 2026-08-20, records it
     as the losing option, so reopening it is an entry there rather than a
     branch. Divergence (10) therefore stands, and stands for the reason it
     always did: `output_pattern` is still hand-edited only.

  What the entry did **not** foresee, and what the endpoint's section states, is
  that the three conditions are all about a *write*: `cli.do_config_unset`
  consults no rule at all, so the allowlist governs removal as well, and a
  removal is how `base_url` changes without anyone setting it.

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

(2) and (3) were the two HANDOFF-204 had to **decide** rather than merely
inherit; both were decided and closed on 2026-08-15, additively. (18) to (21)
were decided at version 2 and each entry says how.

1. **Closed 2026-08-14.** *`/api/state`'s `candidates` had no CLI equivalent.*
   `_scan_sources` globbed the configured `sources` patterns and subtracted what
   was tracked; nothing in `cli.py` did this — it did not import `glob` at all.
   Under invariant 8 that was behaviour living only in the server. `lx untracked`
   is the command it now stands in front of, `cli.do_untracked` decides the list
   for both surfaces, and the silent 200-entry cap went with it.
2. **Closed 2026-08-15.** *`/api/translate`'s `mode: "repair"` meant `lx repair`,
   not `lx translate --mode repair`.* The endpoint selected failing segments;
   `lx translate --mode repair` selected pending ones, because `cmd_translate`
   had no repair branch. Two CLI commands disagreed and the server silently
   picked one. Settled the way this section said it had to be — **in one place,
   not in the server**: `cli.do_select` is that place, and `cmd_translate`,
   `cmd_repair`, `cmd_run` and this endpoint all call it. **The CLI was aligned
   to the wire**, so no key and no meaning on this surface moved; the mirror
   settlement was refused because it bumps the version and makes a Repair button
   select the untranslated remainder of the book. Two smaller copies went with
   it: the polish predicate, which existed twice in `cli.py` byte for byte and
   once more in the server, and `cmd_run`'s inline spelling of the draft queue.
   The `ids`-before-`mode` order was part of the same defect — the CLI tested
   `mode == "polish"` first, so `lx translate --mode polish --ids s3` ignored
   `--ids` while the endpoint honoured it.
3. **Closed 2026-08-15.** *`/api/translate` could not name a model.*
   `translate.translate_segments` takes `model`, `lx translate --model` forwards
   it, and the endpoint did not — a settings surface that can route a stage to a
   model but cannot run one is half a feature. The endpoint takes an optional
   `model` now **and reports the resolved route back**, because the readback is
   the half that makes it usable: the only other place the answer appeared was a
   `log` line this contract forbids parsing. Both are additive, so the version
   did not move. One call to `config.resolve_route`, shared with `/api/state`'s
   projection — a second site resolving this independently is how the workbench
   and the CLI come to describe different runs.
4. **`/api/job` is a genuine CLI gap.** Structural rather than accidental: a
   browser request cannot block for the minutes-to-hours a run takes, and a
   terminal invocation can. Recorded so it is not mistaken for leaked logic, and
   **still open as a gap** — deliberately, because it is the only part of this
   endpoint's neighbourhood that is not pipeline logic. The two debts it named
   were paid on 2026-08-15: the id no longer depends on `len(_JOBS)` (9), and
   there is a retention rule, stated under the endpoint. What is left here is the
   job table, the progress log and the polling; selecting the segments and
   running the model became `cli.do_select` and `cli.do_translate` — see (2).
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
9. **Closed 2026-08-15.** *The job id was minted outside the lock, and `_JOBS`
   was never pruned.* `f"job{len(_JOBS) + 1}"` was computed before `_JOB_LOCK`
   was taken, so two simultaneous requests could mint the same id and the second
   would overwrite the first's state — a client polling the id it was handed
   would be watching someone else's run. The id is minted **and** inserted under
   one acquisition now. The two halves turned out to be one defect rather than
   two: a length goes backwards the moment anything is evicted, so `len(_JOBS)`
   would have reissued a live id the day pruning landed, with no race needed at
   all. A monotonic counter fixes both and buys the high-water mark the endpoint
   now answers "this finished and was dropped" from. Retention is stated under
   the endpoint. **Not closed:** the concurrent-mint half is asserted by a
   twenty-thread test that is honest about being the weaker of the two — under
   the GIL it rarely interleaves where it matters, and the mutant that restored
   the old expression was caught by the id-reuse test instead. Both are kept;
   only one of them is watching the lock.
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
13. **Closed 2026-08-14 on the separator axis; the wire half closed at version 2;
    the case axis closed with (19).** *`candidates` never stopped listing a
    tracked document, on Windows.*
    Reproduced 2026-08-13: extract `docs/guide.md`, then
    `GET /api/state` still returned it under `candidates`. `_scan_sources` built
    its "already seen" set from `docs[].source`, which is `os.path.relpath`
    verbatim — `docs\guide.md` — and built each candidate key with
    `.replace(os.sep, "/")` — `docs/guide.md`. The two never matched, so the
    subtraction was a no-op on any platform whose separator is not `/`: green on
    Linux, wrong on the development machine. Both sides now go through
    `store.doc_id`, which is what the state database keys a document on, so the
    comparison is the project's own identity rather than a rule this list
    invented. The wire carried two spellings until version 2, which normalized the
    label in `store.doc_label` where it is read and where it is written — see
    *Common to all of them*. Fixing only the comparison had left the condition in
    place and would have pushed a normalizer into every client.
14. **Closed at version 2.** *`status: "translated"` did not mean the segment has
    text.* `do_apply` set it for every id in the payload without testing the text,
    so saving an empty string produced
    `{status: "translated", target: "", origin: "human"}` while
    `report.translated` and `docs[].done` both counted non-empty targets — a
    progress bar computed from `status` disagreed with two counters in the same
    response, and, worse, `status` is the draft queue's selection predicate, so
    clearing a segment took it out of the queue that would have redone it. Closed
    from both ends rather than by adding a third counter: `do_apply` and
    `store.save_targets` derive `status` from the text, and an empty target is
    refused at the door. Those are write-time guards and they say nothing about a
    row already on disk, which is the population this exists for — so `status` is
    recomputed from the target on **read** as well, the way the identity label
    beside it already was. Found by the adversarial pass over the first version of
    this fix, which had made one of the two self-healing and not the other.
15. **Closed 2026-08-20, and the entry was understating it.** *A malformed
    `providers` block takes the whole bootstrap endpoint down.* `_routing_state`
    degraded per stage and reported the error inside the projection;
    `providers.available` had no equivalent and raised, so `/api/state` became a
    `400` with nothing in it — and since configuration was not writable over
    HTTP, a client in that state had no way to recover and nothing to draw.

    **What the entry got wrong is the "`_routing_state` degrades" half.** It does
    — per *stage* — but only for the errors `config.route_entry` raises. A
    `providers` value that is a **truthy non-block**, which one hand-edited
    `"providers": ["local"]` produces, reached `config.resolve_route`'s
    `(cfg.get("providers") or {}).get(name)` and raised `AttributeError`, which
    is not the `ConfigError` `_stage_route` catches. So both projections fell
    together on that shape and the entry named only one of them. The falsy
    spellings — `[]`, `{}`, `null` — were always absorbed by the `or {}`, which
    is why this survived being written down.

    Closed on both sides, and reported rather than raised in each: `resolve_route`
    refuses a non-block `providers` by name, so every stage carries the sentence
    the way a malformed entry already did; and `available()` degrades **per
    provider**, adding `error` to the row it cannot read — see the *provider*
    shape. A `providers` value that is not a block at all lists nothing, because
    there is no row to hang a sentence on and the routing projection beside it is
    already carrying three copies of the explanation. `lx providers` prints the
    same sentence under the row, or the CLI would answer a hand-edited mistake
    with a line of padding and a plausible "no key needed".

    **It was not the optional tidy-up its work package called it.**
    `POST /api/config`'s reply carries the `providers` projection, so an
    unrepaired `available()` would have failed *after* the write had landed —
    `do_config_set` writes, then the reply is assembled, then a broken neighbour
    raises and the caller is told `400` about a change that is on disk. The
    endpoint that exists to repair a broken configuration would have been the one
    endpoint that could not run on one.
16. **`/api/doc` reads the document twice and discards the first read.**
    `load_doc(src, lang)` is called and its result is immediately shadowed by
    `do_check`'s own return, which loaded the same row again. Harmless and
    measurable: two SQLite reads per request on the endpoint a review pane calls
    most.
17. **Closed at version 2 for a client that opts in.** *Nothing detected a lost
    update.* Two clients, or one client and a running translation job, wrote the
    same segment with no version token and no conflict status: on the surface
    whose entire purpose is human review, a background job could overwrite a
    reviewer's sentence and report `200` to both. `POST /api/save` takes a `base`
    token per id now and reports refusals in the `conflicts` map, with the
    comparison made **inside the write** — the first version compared against a
    snapshot read in an earlier transaction and then wrote unconditionally, which
    two threads defeated on the first attempt, both being told they had succeeded.
    The redundant final apply is gone from the job **and from `lx translate`**,
    which shrinks that window from the whole run to one batch on both surfaces;
    removing it from the job alone had left the CLI, which invariant 8 calls the
    product, carrying the larger exposure. **What is not closed:** a writer that
    sends no `base` is still last-write-wins, and `/api/translate` is such a
    writer by construction — the model's output is not based on a token. The
    reviewer's side is what the token protects.

Measured 2026-08-14, by the adversarial pass over the change that closed (1) and
(13). The first is that change's own cost and the other three are older; all four
are on `candidates`, which is what a pass aimed at one list finds.

18. **Decided at version 2: the suppression stays and stops being silent.** *An
    entry is an identity, so a distinct file could be permanently invisible.* The
    identity flattens every character outside `A-Za-z0-9._-`, so `docs/guide.md`
    and a root-level `docs_guide.md` are one string — and `books/第一章.md` and
    `books/第二章.md` are both `books____.md`, which is a whole Chinese-titled
    library collapsing to one row in the use case this project exists for.
    Reproduced with nothing tracked at all: two real files matching one glob, one
    entry, no diagnostic. The suppression is faithful to storage — `.lx/state.db`
    keys a document row on that identity, so extracting the second would overwrite
    the first — and the old code, which listed both, lost the state instead. What
    was wrong is that neither surface said which path it had collapsed.
    `/api/state`'s `collisions` and `lx untracked`'s own warning block say it now,
    including the case that produced no entry at all: a file whose identity a
    *tracked* document already holds. `offered` is `null` for that case **and**
    when no target language is configured, so it means "nothing was offered here"
    rather than "this is tracked". The collision itself is `doc_id`'s and is
    answered by the structural identity the *Reserved* section schedules.
19. **Closed at version 2.** *A tracked document was still listed when the two
    spellings differed in case.* The other half of (13), on the axis that fix held
    constant, and older than it. The identity is case-sensitive; NTFS is not.
    Reproduced on the development machine: one file `docs/Guide.md`, then
    `lx extract docs/guide.md --lang zh-TW` succeeds and `lx stats` shows it, and
    `lx untracked` still offered `docs/Guide.md`. Two identities, `docs_Guide.md`
    and `docs_guide.md`; one file. Case-folding the identity is **not** the fix —
    it would merge two genuinely distinct documents on a case-sensitive filesystem
    — so the subtraction is made on `os.path.normcase` of the identity, which
    lowercases on Windows and is the **identity function on POSIX**: the
    platform's own answer rather than a rule this list invented. It folds the
    tracked side and the candidate side alike, so two spellings of one file
    reaching the list through two `sources` entries are offered once too.
    *Lost:* `os.path.normcase(os.path.realpath(p))`, which additionally folds 8.3
    short names, junctions and symlinks; it was written first and measured out at
    **463 ms per 2000 tracked documents, 4.7x the read it rides beside**, for
    coverage on axes nothing had asked about. **What is not closed:** `doc_id` is
    still case-sensitive, so `lx extract` on each spelling still opens two state
    rows for one file, and a symlink or an 8.3 spelling still reaches one file two
    ways. That is the identity's defect and waits with (18).
20. **Decided at version 2: two of its three axes filtered, the third kept.** *An
    entry no endpoint would accept.* The list was a glob over `sources` and
    nothing else — not filtered to files, to extensions the format registry knows,
    or to paths inside the confinement root. Measured: `sources: ["book/**/*"]`
    listed a directory and a `.jpg`, and `POST /api/extract` answered `400` for
    both — "has no format this project knows how to read". Those two are filtered
    now: both surfaces refuse them, so they were never work. The third axis is
    **deliberately not filtered**: `sources: ["../shelf/*.md"]` lists a path
    outside the project, `POST /api/extract` answers `403` — and `lx extract
    ../shelf/book.md` **succeeds**, because a CLI argument is invariant 11's named
    exception. Filtering it would take a row out of the list that the product's
    own primary surface can act on, which invariant 8 does not allow a web
    concern to do. It waits for `roots`, when an outside path stops being a
    colliding identity.
21. **Closed at version 2, in the page that is still being replaced.**
    *`candidates[].source` reached the shipped page's DOM through an unescaped
    HTML attribute.* `static/index.html` built `data-src="${c.source}"` by string
    concatenation; its `esc()` handled `&`, `<` and `>` and not the quote, and was
    applied to the visible text only. No `Content-Security-Policy` is sent — see
    *Transport*, which lists every header this server emits — and the page has
    unauthenticated access to every endpoint here, including one that spends
    money. A filename is not always the user's own on a surface whose corpus is
    downloaded novels, and a POSIX filename may contain `"`. Patched rather than
    left for the rebuild, because the rename touched those exact lines anyway:
    `esc()` covers `"` and `'` now and **every** interpolation goes through it.
    That last word is the finding: this entry's own list of unescaped sites was
    short by one — `data-lang` went through nothing at all — which is why the fix
    is a rule rather than a patch to the named sites. The rebuild must escape by
    construction; one that string-builds markup inherits this.

Measured 2026-08-15, while closing (2), (3) and (9). Appended rather than folded
into those entries because each is a separate condition, and because a divergence
found *and* fixed in one change is still worth a number — the next person to ask
"what has this surface been wrong about" reads this list, not a commit log.

22. **Closed 2026-08-15.** *`POST /api/check` carried a whole stale snapshot back
    over newer text.* The third writer none of (1) to (21) names, and the largest
    of the three. `do_check` read the document, walked every segment attaching
    `issues`, and wrote **all of them** back — `target`, `status` and `origin`
    included — so a target saved through `/api/save`, or banked by a running
    `/api/translate` job, between that read and that write was silently replaced
    by the copy the check had loaded. `/api/doc` calls `do_check` on every
    request, which is what made the window ordinary rather than rare; it passes
    `persist=False` and so was never the writer, but `POST /api/check` and every
    `lx check` were. Closed with the compare-and-swap `save_segments` already had
    for `do_apply`: a row that moved is skipped, and skipping it loses nothing,
    because the issues describe wording that is no longer there and the next
    check recomputes them against what is.

    **Closed twice, and the first attempt is the interesting one.** That
    compare-and-swap keys on the `target` **column** while the statement it
    guards writes the whole `body` blob — where `origin`, `review` and `issues`
    live. So it always passed for a segment whose only change was a hold or an
    origin claim, and the stale body went over it: `POST /api/hold` answered
    `applied: 1` and a check already in flight put the pre-hold `review` back,
    and an `origin` rolled from `human` to `tm`, which is how a segment stops
    being covered by *Origin precedence* at all. Found by an adversarial pass the
    same day. `do_check` is not a whole-row writer any more — it writes the one
    key it decides, through the narrow writer `POST /api/hold` already used.
23. **Closed 2026-08-15.** *Nothing on this surface could say "leave this segment
    to me".* There was no review state on a segment at all: `status` had two
    values derived from the text, `origin` recorded provenance only, and a
    reviewer part-way through a difficult paragraph had no way to keep the next
    `/api/translate` run off it. `POST /api/hold` and the `review` field are the
    answer, and the exclusion is one shared helper applied at every predicate
    that selects work rather than a condition copied into each.

    **A hold survives `POST /api/extract`** as of 2026-08-16 — it rides with the
    wording it was placed on, through the same carryover that moves the target
    and the origin. It does *not* survive `reset: true`, which reads no prior
    state at all. Until 2026-08-17 it also did not survive a carryover the
    acceptance path refused, "because there is then no wording left to hold" —
    an argument that was true only because the refusal deleted the wording.
    Closing (24) removed the deletion, so a hold now rides with wording the
    acceptance path refused, and the segment comes back held, `translated` and
    failing `POST /api/check`. It is still dropped when another proposal takes
    the segment, because the wording it was placed on is gone then. The first
    version of
    this shipped without any of that: `POST /api/extract` lifted every hold in
    the document and said nothing, which the "re-extract from source" control
    this contract tells a client to offer would have done on every press.

Measured 2026-08-16 by the adversarial pass over the change that closed (22) and
(23). Both are older than that change and neither is fixed here; both are about
`POST /api/extract`, which is the endpoint least like the rest of this surface —
it is the only one that rebuilds a document rather than editing it.

24. **Closed 2026-08-17.** *A stored target that no longer fits is deleted, and
    the reply does not say which.* `POST /api/extract` carries prior wording over
    through the acceptance path, and a carryover it refused — the measured case
    is a `config/dnt.txt` edit moving the mask configuration under banked
    wording, so the placeholder set no longer matches — left the segment with
    **no target at all**. A sentence a person wrote was gone. `rejected` counted
    it, but `rejected` also counts a refused *memory* hit, and until 2026-08-16
    the CLI printed only "stale memory hit(s) refused", which names the memory
    rather than the wording it had just deleted.

    Closed by keeping it. **`lx extract` does not delete wording this document
    already holds**: the target stays with its `origin` and its `review`, the
    segment comes back `translated` and failing, and `POST /api/check` reports
    the placeholder mismatch at *error* severity. The acceptance path answers
    "may this wording be written into a segment as a translation", and until
    now `do_extract` also read it as "and if not, delete what is there" — two
    questions, of which only the first is the gate's. This is the rule `lx apply`
    already states for wording somebody wrote: reported at `lx check`, not
    rejected at the door.

    *Lost:* keeping it and marking it with a second `review` value, which spends
    a vocabulary this contract advertises as closed on a state the check already
    reports, and which would then have to be excluded from `checks.workable` or
    become a second hold. *Lost:* deleting it and naming the ids on both
    surfaces, which answers "which sentence did I lose" with a list instead of
    with the sentence. *Not done, and the entry above was wrong about why:* a
    `kept` array on this reply. A new response key is **additive** — see
    *Versioning* — so it never needed a version decision, and the sentence
    claiming it did was steering the choice. It is unnecessary rather than
    forbidden: nothing is deleted, and the segments that came back failing carry
    their errors on `POST /api/doc`. **Landed at version 3**, 2026-08-19, for a
    client that wants them without a second call — additively, as this paragraph
    says, and only because that bump was rewriting this endpoint's section
    anyway.

    **Narrowed the same day.** A stored wording whose placeholders were merely
    *renumbered* — by an edit to `config/dnt.txt`, or by the numbering fix of
    2026-08-17 — is now **repaired** rather than refused: the map it was written
    against is pinned beside it, and `mask.reseat` moves it into the segment's
    current numbering. So this entry's population is what is left after that: a
    wording no seating can place, the ambiguous case, where a term occurs a
    different number of times in the wording than the segment has slots for it.
    Those are kept, exactly as below.

    **The cost, measured and worse than it first looked.** `mask.unmask` leaves
    an unknown `⟦n⟧` verbatim and `cli.do_render` runs no check, so `lx render` on
    a document that fails `lx check` writes the kept target into the output where
    it used to write the untranslated source. The first version of this entry said
    that cost was a visible stale placeholder. It is larger: **dropping a
    do-not-translate term renumbers the ones that survive it**, so the kept
    target's remaining `⟦n⟧` resolve against a different slot map and the rendered
    sentence can name the *wrong entity* — measured 2026-08-17, `Gamma、Beta` where
    the person had written `Gamma、Alpha` — with nothing visibly broken about it,
    and `missing` falling from 1 to 0 because the segment now has a target.
    `lx run` never gets there, since it refuses to render while errors remain, and
    a person's `lx apply` could already produce exactly that target.

    **The wrong-entity half is closed, 2026-09-01, and it was not closed the way
    this paragraph expected.** Treating a mismatching target as missing — the
    repair named here — compares placeholder id *multisets*, and the wrong-entity
    case has **equal** multisets by construction: measured that day, a
    `config/dnt.txt` edit that swaps one protected term for another (`Alpha, Beta`
    → `Alpha, met` over `Alpha met Beta.`) renders `Alpha 遇見 met。` where the
    reviewer wrote `Beta`, with `lx check` reporting **0 errors and 0 warnings**,
    `missing` `0` and `from` `"target"`. The gate would never have fired on it.
    What closes it is that `cli.do_extract` already pins the map a kept wording's
    ids mean, as `target_slots`, and `store.prior_targets` and `store.tm_record`
    both read it first — `skeleton.render_blocks` was the one reader of a stored
    target that did not. It does now, so the bytes are the reviewer's own words,
    and a `numbering` rule reports the segment at warn because the source has
    moved under the wording. Deterministic, so invariant 5 corrects rather than
    reports; additive, so nothing here moved. The stale-`target_slots` defect
    found beside it — `store.save_segments` did not clear the field the way
    `store.save_targets` does, so an `lx apply` that *fixed* such a segment left
    the old map on it and `store.tm_record` banked `slots` naming originals the
    wording's ids do not mean — was repaired in the same change.

    **What is left of this cost is the loud half**, and it still stands: a
    hand-typed `⟦99⟧`, or a kept wording no seating can place, renders a bare
    placeholder into the file. Every one of those is an `lx check` error today
    and `lx run` refuses to render, so the exposure is `lx render`,
    `lx run --force` and `POST /api/render`. Closing it by construction is
    **HANDOFF-036**, which owns the version decision: `missing` is documented as
    a count of segments with **no target** and `from`'s `target` branch as the
    one a stored translation takes, so counting "no *usable* target" changes what
    two documented values mean and bumps.
25. **Closed 2026-08-17.** *One identity, two positions: duplicate source text
    collapses.* Carryover was keyed on the translation-memory key alone, so two
    segments whose source text is byte-identical shared one entry and the last
    row read won. Measured with a document whose first and third paragraphs are
    both `Yes.`: the human target on the first was replaced by the machine draft
    from the third **and its `origin` was laundered to `llm:draft`**, and the
    reverse order laundered a machine draft into `human`, which locks the model
    out of it permanently. It predated *Origin precedence* and is what made it
    evadable: the guard compares the origin on disk, and this is the path that
    rewrites the origin on disk.

    Closed with position, in the one place that has any: the document's own prior
    state. `store.prior_targets` reads the document's whole key sequence and
    `store.Carryover.align` **diffs it against the fresh one**, taking the
    matching blocks as the answer; what the diff cannot place falls back to the
    last stored wording under that key, which is the rule that carried everything
    before. The **translation memory is untouched**: its key stays blind to
    position, which is what keeps one wording one entry across documents and
    machines.

    *Lost:* putting position into `tm_key`, which invalidates every memory file
    and is refused by `AGENTS.md` as a convention. *Lost:* refusing a document
    with a collision, which is safe and useless on a novel with repeated
    dialogue. *Lost, and measured:* carrying by segment id, which is what the
    package proposed, and carrying by a member's ordinal within its key's run,
    which was the first implementation of it. Both were shipped to a review and
    both were wrong. Ids are sequential over translatable blocks, so inserting one
    paragraph shifts every id after it and an id rule answers for the segments
    before the insertion and nothing else; worse, on a *deletion* it hands the row
    that used to sit at an id to whatever sentence sits there now, which moved a
    person's wording — and `origin: human` — onto a machine's position. The
    ordinal rule guarded itself with a count that compared the translated rows on
    one side against every parsed segment on the other, so a document with an
    untranslated duplicate slid every wording by one. Measured against the rule
    they replaced, across twelve edit shapes, position by position: a run of forty
    identical paragraphs with one insertion before it carries 41/41 under the
    diff, 2/41 under the old rule, and 4/41 under the id rule that was meant to
    fix it; ten lines of dialogue interleaved with narration carry 21/21 to 24/24
    under the diff against 12/21 to 15/24, for one to four inserted paragraphs.
    The diff is at or above the old rule on every shape, including the two nothing
    can align, which are equal and are now reported.

Appended 2026-08-17 by the package that closed (24) and (25), and by the design
pass over it. Neither was a regression: (26) is what position cannot reach and
(27) predated all of this. (26) is open; (27) closed on 2026-09-01.

26. **A sentence the document already had, written again, is still told apart by
    nothing.** The diff answers wherever the text around a repeated line places
    it. Two shapes are left where nothing does. A **new occurrence** of a sentence
    the document already holds matches no unused position, so it takes the last
    stored wording under that key — the old rule's answer, and possibly another
    position's — though it no longer takes that position's *hold* with it. And a
    **run of identical paragraphs that changed size**, where every element of the
    matching block carries the same key, has no anchor at all: the diff would
    place it at the first offset that fits, which is a coin toss, so those blocks
    are refused and their members fall to the same fallback. Both are named by
    `lx extract`, and by `POST /api/extract`'s `ambiguous` since version 3 —
    which closed the reporting gap (24) had, without closing this entry: naming
    a segment nothing can place is not placing it. The residue is bounded — every candidate wording is
    a translation of the same source sentence — but the `origin` that rides along
    is not, so a machine may end up locked out of a position a person never wrote.
    `Carryover.align` also has a work budget (`store.ALIGN_BUDGET`): over it the
    diff is skipped entirely and every segment falls back, because
    `SequenceMatcher` is quadratic on a sequence that is one element repeated —
    measured 2026-08-17: 2.0 s at five thousand identical paragraphs, 14.2 s at
    twelve thousand, against 8 ms for a realistic novel.
27. **Closed 2026-09-01.** *A memory hit answers over wording this document was
    already holding.* `cli.do_extract` offered two proposals in order — this document's own stored
    target, then a translation-memory hit — and takes the first the acceptance
    path accepts. So a stored target that no longer fits *with a banked wording
    behind it that does* is replaced rather than kept: (24)'s rule covers the
    case where every proposal is refused, and this is the case where one is not.
    The sentence is gone and its `origin` with it — a `human` segment comes back
    as `tm`, which is a provenance nobody claimed and, more to the point, is not
    the one *Origin precedence* protects, so the next unattended run may
    overwrite it. Needs no collision and no race. It is **reported** since
    2026-08-17 — `lx extract` names the ids, and `POST /api/extract`'s `replaced`
    does since version 3, which is the only way to see this one at all: the
    segment comes back `translated` and passes every validator, so unlike (24)
    there is no error to find it by. Otherwise unchanged, because which of the
    two should win is a decision — and the
    argument on record for the memory, "a good banked wording should not be lost
    to a stale one sitting in front of it", was written when the refused wording
    was going to be deleted either way, which stopped being true the same day.

    **Closed by letting only a machine draft give way**, decided 2026-08-17 and
    shipped 2026-09-01. A stored target whose `origin` is `llm:*`, `tm` or
    `tm:legacy` may be replaced by a memory hit the acceptance path accepts;
    wording written by a person or an agent is kept — it wins over the memory even
    when the acceptance path refused it — and is reported at `lx check` like any
    other kept wording. That is invariant 9's line, a machine draft is
    regenerable and a person's sentence is not, applied to an ordering question
    rather than to a storage one. `store.is_regenerable_origin` is the predicate
    and it lives beside `store.is_model_origin`, which reads the same field for
    the write guard; `cli.do_extract` skips the *lookup* rather than dropping the
    candidate, since a hit nothing may use is a read nobody asked for.

    **It enumerates what may be replaced, never what is protected.** `carryover`
    — what `store.prior_targets` calls a body written before the `origin` field
    existed, and a reachable state, not a hypothetical — is nobody's *known*
    prose, and neither is an origin a later build invents. Both are kept. The
    decision's own accepted cost is one repair call for a machine draft that no
    longer gets out of the way; being wrong in the other direction costs a
    sentence somebody wrote, replaced with nothing printed.

    *Cost, accepted with the decision:* a broken machine draft no longer gives way
    to a banked wording that fits, so that segment costs one repair call. *Lost:*
    "the document's own wording always wins", which keeps a stale machine draft in
    front of a good banked one for no gain, since the draft is regenerable.
    *Lost:* keeping today's ordering, which is the defect.

Appended 2026-08-19 by the adversarial pass over `contract_version = 3`. Open,
and older than that change — both halves are identical at its parent commit.

28. **`POST /api/extract` type-checks neither `reset` nor `tone`, and one of
    them destroys work.** `reset` is read for truthiness, so **`{"reset":
    "false"}` is a reset**: a non-empty string is truthy, the document's
    translations go, and nothing in the request looked wrong. `{"reset": 1}` and
    `{"reset": "no"}` are the same. This surface already refuses exactly this
    shape one endpoint over — `POST /api/hold` rejects a non-boolean `held`
    because "`null` would read as false and *release* a hold, which is the
    opposite of the default" — so the rule exists here and this endpoint is not
    holding it. The other half is quieter: a truthy non-string `tone` is frozen
    onto the document verbatim, so `{"tone": {"a": 1}}` makes `GET /api/doc`
    answer a `tone` this document's own *Response* table declares to be a
    `string`, and it selects the default register's brief, since an unrecognized
    register silently falls back. The blank spellings are covered — `""` and
    `"   "` are refused by the version 3 rule — and the falsy non-strings are
    covered incidentally, because `{}`, `[]`, `0` and `false` are all blank once
    the guard stringifies them. What is left is the truthy end of both fields.

    **Not fixed here, and the reason is the gate rather than the cost.**
    Refusing a value the endpoint accepts today narrows an accepted value set and
    turns a documented `200` into a `400`, which bumps — and `contract_version 3`
    was a scheduled package whose scope was one item, with an explicit rule that
    an item arriving mid-flight goes into the next one. So this is written down
    rather than quietly repaired, which is what this section is for. The repair,
    when it is scheduled: `do_extract` refuses a `reset` that is not a boolean
    and a `tone` that is not a string or `null`, in `do_extract` rather than at
    the endpoint, for the reason the version 3 refusal lives there.

Appended 2026-08-20 by the security-tier pass over `POST /api/config`. Open, and
older than that endpoint — every path below is reachable from `lx config set`
today. What the endpoint changes is who can reach them.

29. **Outside `api_key_env` and `base_url`, a mispasted credential is repeated
    back or written down.** This project's no-echo doctrine is scoped, in writing
    and on purpose, to the two fields a key lands in — `_field_base_url`'s own
    docstring says "this field sits directly above `api_key_env` in every
    provider block, so it is one of the two a mispasted key lands in". The fields
    *beside* those two have the ordinary treatment, and three of them are on this
    endpoint's allowlist. Measured 2026-08-20 against a key-shaped string:

    - `providers.*.kind` answers ``providers.x.kind = 'sk-…' is not a backend
      this build has``;
    - `providers.*.timeout`, `.temperature`, `.max_tokens`, `.retries` and every
      `batch.*` key answer ``… — got 'sk-…'`` out of `_as_number`;
    - `routing.*` answers ``unknown provider 'sk-…'`` for the segment before the
      first colon;
    - and `providers.*.model` does not refuse at all. `_as_text` accepts any
      non-empty string, so a key typed into the model box is **written into
      `lx.config.json`**, which is a file `lx init` scaffolds into a repository.

    Before this endpoint the audience for those sentences was a terminal, which
    invariant 11 calls the trusted case. Now a page renders them, so the string
    reaches the DOM of whatever a person has open. The exposure is bounded — the
    value goes back to the caller that sent it, over loopback, and request bodies
    are never logged — but "the value appears nowhere in the response body" is
    true of `api_key_env` and false of these.

    **Two further reachable paths, appended 2026-09-01 by the package that put a
    model control and a backend editor on the page.** Neither is a new class and
    neither is repaired here; both are recorded because that package is what
    makes them reachable by clicking rather than by typing a dotted key.

    - `GET /api/models`'s `?provider=` reaches the same `unknown provider '…'`
      echo through `providers.build`, **and the bound stated above does not hold
      for it**: this value is in a query string, and *Transport* says query
      strings are logged to the server's stdout while request bodies are not. So
      a value mispasted here also lands in the scrollback of the terminal running
      `lx web`, which outlives a response body. Its size is bounded by the 64 KB
      request line `http.server` accepts.
    - The toolbar's **model** box rides on `POST /api/translate` as `model`,
      which `providers.build` puts into `Provider.describe()`, which is the first
      line of the job log — so it comes back through `POST /api/job`'s `log` and
      is rendered into the page. It is not stored; the editor's model box is the
      one that reaches `lx.config.json`, and that is the path recorded above.

    **Recorded rather than repaired, because the repair is a decision and not a
    patch.** Widening the doctrine to every field means either dropping the value
    from refusals that are more useful with it — `kind` has three legal values
    and naming the rejected one is most of the message — or teaching those fields
    the key-shape heuristic, which `_field_api_key_env` documents as refusing
    "long and lower-case" and which would therefore refuse
    `mradermacher/translategemma-12b-it-i1-GGUF:Q4_K_M`, a real model id on the
    maintainer's own backend. Both are `docs/decisions.md` entries. The narrower
    half — `model` accepting and storing a key — has no such tension and is the
    part to schedule first.

Appended 2026-08-21 by the adversarial pass over the block map and the sentence
rule. Open.

30. **`POST /api/sentences` answers a question `lx sentences` cannot be asked.**
    The endpoint splits arbitrary text; the command splits a document's stored
    text and takes no other input. **This is not invariant 8's usual shape and
    the difference matters**: no pipeline logic lives in the server here, because
    both surfaces call `cli.do_sentences(texts, cfg)` and that function has taken
    a list of arbitrary strings from the day it was written. What the CLI lacks
    is a way to hand it one from a terminal.

    It was considered and **not** dismissed as inherent, which was the other
    available reading. The argument for inherent is that a reviewer mid-edit
    holds text belonging to no file and a terminal does not — but a terminal
    holds such text routinely, through a pipe or a flag, so the asymmetry is a
    missing affordance rather than a property of the two surfaces. And the module
    that owns the rule says why it is worth closing: `sentences.py`'s docstring
    puts the rule in Python "precisely so that `lx`, an agent and CI can see it",
    and CI checking the boundary rule on a shape that is not yet a document
    cannot use `lx sentences` today.

    The repair is one flag — `lx sentences --text` or a `-` that reads stdin —
    and it is recorded rather than taken because the package that found it was a
    repair package and adding an interface is construction. Nothing about the
    wire moves when it lands: `Backed by` already names `cli.do_sentences`.

Appended 2026-09-01 by the package that closed (27) and the wrong-entity half of
(24)'s cost. Open, and older than that change.

31. **A rendered document can still carry a bare placeholder.** `mask.unmask`
    returns an id it has no slot for verbatim, and `skeleton.render_blocks` takes
    the target branch whenever the target is truthy, so a stored wording carrying
    a `⟦n⟧` this segment has no slot for writes that token into the file.
    Reachable two ways, both measured 2026-09-01: `lx apply` — or
    `POST /api/save` — with a hand-typed id the segment does not have, since a
    person's words are deliberately not refused at the door; and a kept wording
    (see (24)) no seating can place, where a term occurs a different number of
    times in the wording than the segment has slots for it. `missing` counts
    neither, `from` reports `"target"` for both, and `GET /api/preview` shows the
    token to the reviewer as if it were prose.

    **It is loud, and it is already reported.** Every case is a `tags` error, so
    `lx check` exits non-zero and `lx run` refuses to render — exposure is
    `lx render`, `lx run --force` and `POST /api/render`. That is why it is
    recorded here rather than repaired beside (27): the *silent* half of the same
    cost, a placeholder resolving to the wrong original, is the one that closed
    on 2026-09-01, and it closed by reading `target_slots` rather than by any
    gate. This half needs a gate, and a gate is a version decision: `missing` is
    documented as a count of segments with **no target** and `from`'s `target`
    branch as the one a stored translation takes, so counting "no *usable*
    target" changes what two documented values mean. Scheduled as **HANDOFF-036**,
    which owns the bump.

Appended 2026-09-01 by the package that added `GET /api/models`. Both open.

32. **The wire degrades where the command exits.** `GET /api/models` answers
    `200` with `error` when a backend cannot be reached, still carrying
    `provider` and `configured`; `lx models` against the same backend exits 2
    and prints one sentence to stderr, `--json` or not — the flag never gets to
    run, so there is no JSON at all rather than a three-key object without an
    `error`. One question, two shapes and two behaviours — divergence (8)'s
    family, in a stronger form.

    **It is not leaked logic**, which is the distinction that decides whether it
    has to be repaired: the listing itself is `cli.do_models` on both surfaces
    and there is no second implementation of anything. What lives only in
    `web/server.py` is the *degradation policy* — catch, resolve the route
    anyway, answer 200 — and it lives there because it exists to serve a control
    that must not block, which a terminal does not have. A command that answered
    0 and printed nothing when the server was down would be the worse artifact:
    an exit code is how CI and an agent find out.

    The repair, if it is ever wanted, is a `cli.do_models_report(cfg, provider)`
    returning the four-key dict that both surfaces call, with `cmd_models`
    keeping its non-zero exit by reading `error` rather than by catching. That is
    an addition to the CLI and a `Backed by` line, not a version move.

33. **A listing follows a redirect with the credential attached.** `Provider.
    _request` uses `urllib.request.urlopen` with the stock opener, and CPython's
    `HTTPRedirectHandler` strips `Content-*` on a redirect and **keeps
    `Authorization`**, including across a change of host. So a backend that
    answers `302` — or a man in the middle on a plaintext `base_url` — moves the
    `Bearer` token built from `api_key_env` to an address the person never
    configured.

    Older than this endpoint and not introduced by it: the same opener carries
    every completion. It is recorded here rather than under invariant 6's list
    because `GET /api/models` is the first **GET** on which it happens — a
    request that acquires no state, carries no `Origin`, and still sends the
    credential outbound. It is *not* the first browser gesture that does: the
    Translate button has since the workbench existed, which is what makes this
    divergence older and broader than the endpoint that got it a number. Not repaired here because the fix lands in the shared transport that
    every completion also uses — either a redirect handler that drops
    `Authorization` when the host changes, or refusing redirects on a listing,
    which has no legitimate reason to follow one — and changing what every
    request in the project does is its own package with its own tests. Found by
    the security-tier pass over this endpoint's design, 2026-09-01.

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
