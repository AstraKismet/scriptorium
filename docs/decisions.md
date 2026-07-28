# Decision log

Short entries, newest first. Record the alternative that lost, not just the
choice that won — the reasoning is what future changes need.

## 2026-07-28 · Where a line terminator lives, and where a continuation indent lives

Both round-trip defects the review measured (see the entry below) are fixed. Two
questions had to be answered to fix them, and neither answer is obvious enough to
leave in the code alone.

**A continuation's indent belongs to the segment source, not to the skeleton.**
`- item\n    continued\n` is one segment whose source is now
`item\n    continued` — the four spaces included, and therefore visible to the
model. That reads wrong at first: the marker prefix `- ` is already a raw
skeleton node, so the indent looks like skeleton too. It cannot be. The indent
sits *after* a newline that is inside the segment, and a raw node can only be
placed before or after a whole segment, never in the middle of one. The only
shape that would make it skeleton is one segment per physical line, which is the
alternative that lost: it cuts a wrapped sentence into fragments and asks the
model to translate each one blind, to buy a representation detail no one needs.

**A lone CR is text, not a line terminator.** `parse` splits on `"\n"` and
nothing else, so `one\rtwo\r` is one segment containing a CR. CommonMark would
call that two lines. Reinterpreting it would move a segment boundary and
invalidate every memory entry keyed on the old source, in exchange for nothing at
the byte level — the file round-trips exactly either way. Byte preservation and
segmentation are separate decisions; this package owed only the first. Classic
Mac OS line endings are the only real source of such files, and they can be
reopened as their own entry if one ever turns up.

Consequences worth knowing, measured by running both parsers over the committed
contents of all 40 tracked Markdown files — 2394 segments. Round-trip failures go
from 8 files to 0. **No segment boundary moves, in any file**, and no segment
changes kind.

67 of the 2394 segment hashes change, every one of them a wrapped list item, and
every one of them a segment that was losing bytes before. The CR repair changes
**none** — moving the terminator into the skeleton leaves the source string
exactly as the old `rstrip` left it, so `tests/corpus/crlf-line-endings.md`
reports 0 of 7 hashes changed and no translation memory is invalidated by that
half at all. Files affected by the indent half: `AGENTS.md` (27 of 68),
`docs/conventions/handoff-workflow.md` (24 of 99), `README.md` and
`README.zh-TW.md` (4 of 68 each), `skill/reference/zh-TW.md` (3 of 100),
`adapters/opencode-rule.md` (2 of 15), and one fixture (3 of 3). That the
project's own documentation was the largest victim is the argument for the
corpus.

**The parser now holds the line terminator; the CLI still throws it away.**
Measured while verifying this package: `cli.py` opens source files in text mode,
so universal newlines deletes every CR before `parse` is called, and writes the
rendered file in text mode, so every `\n` becomes `os.linesep`. A CRLF document
therefore round-trips on Windows by coincidence and loses every CR on Linux, and
no mixed-terminator document survives anywhere. The parser fix is a strict
prerequisite for the I/O fix — normalizing at the boundary hides the parser
defect rather than removing it — so it landed first, and the I/O layer is
scheduled as its own package. Recorded here because a green corpus could
otherwise be read as an end-to-end guarantee that does not yet exist.

**`str.splitlines()` was not used**, though it makes the terminator handling
shorter. It also splits on `\x0b`, `\x0c`, `\x1c`–`\x1e`, `\x85`, U+2028 and
U+2029, which `str.split("\n")` does not, so the swap silently changes block
boundaries in any document containing one of them.
`tests/corpus/line-separator-control-chars.md` exists to hold that line.

## 2026-07-28 · Stack, scope and infrastructure review

Twenty-three decisions, taken from an interactive decision board after a
twelve-agent analysis: four language-ecosystem surveys (Python / Go / Rust /
TypeScript), three domain studies (multi-format round-trip fidelity, industry
CAT tooling and quality assurance, state layers for 100k-word projects), a
distribution study, and three adversarial reviews.

### What the review found, before what it decided

The adversarial pass measured four things on this repository. All four were
independently re-verified before any decision was taken, because the synthesis
itself warned that unsourced "measured" claims should be discounted a grade.

**Invariant 2 was already false.** Two round-trip defects, both confirmed:
`mdparse.py:139` uses `lines[j].strip()` on list-item continuations and eats the
indent (`- item\n    continued\n` returns as `- item\ncontinued\n`); and
`mdparse.py:40` combined with the `\n` split at line 25 drops the trailing CR of
every block in a CRLF file. Both are ordinary real inputs — wrapped list items,
and text files from outside a Unix toolchain — and Windows is the primary
platform here.

**The skeleton guarantees the bytes around a segment, not the structure of the
document after substitution.** Five cases confirmed, every one of them reported
by `check` as zero errors and zero warnings: a cell translation containing `|`
grows a third column; a paragraph translation starting `1. ` becomes an ordered
list; a translation containing `\n# ` invents a heading; a heading translation
containing a blank line splits into two blocks; a blockquote translation
containing a newline leaks its second half. On Markdown this is a layout
regression. On XHTML and OOXML it produces files that do not open, because
`render()` performs no escaping of slot values at all.

**The lexicon rejects correct Traditional Chinese.** Five confirmed false
positives, one of them at error severity — `分析這批數據` fails the build, and
`程序` (法律程序), `質量` (物體的質量), `支持`, `文本` all warn. So the exit code
is unreliable in both directions: it passes structural damage and fails correct
prose.

**"38 tests" is not a specification.** pytest collects 38 cases from 26 test
functions, and the one that matters — `test_skeleton_reproduces_source_exactly`
— has seven inputs, six of them single-line toy strings, which is precisely why
it never caught either defect above.

The consequence that drove the largest decisions: every rewrite route relies on
golden-file comparison against current Python output as its safety net, and that
corpus does not exist.

### A. Direction

**A1 · Language: keep Python at the core, develop the frontend separately in
TypeScript, and defer the language decision.** Splitting the work by
person-days showed 79–145 days that are language-independent against a rewrite
increment of +2 to +75. The stated core need — "translation assistance taken to
its limit" — falls entirely in the language-independent part. The rewrite
increment buys distribution and types; types cost about one day of TypedDict,
and distribution has two consumers whose ideal formats are opposites.

*Lost:* a Go rewrite (lowest rewrite increment, genuine cross-compilation, and
the stack is already proven in a sibling project) — deferred rather than
rejected, see the re-evaluation schedule below. A Rust rewrite is removed from
the list outright: its benefits overlap Go's entirely at roughly twice the cost,
and its YAML story is broken (serde_yaml is deprecated and no fork preserves
comments). A TypeScript rewrite is rejected because UTF-16 indexing would
silently shift the existing offset, mask and hash arithmetic on CJK extension
blocks and emoji.

**A2 · Scope: TXT, Markdown and EPUB only.** The four text types are four
product lines, not four features — Okapi's OpenXML filter has been under
development for fifteen years and still has open content-loss bugs. Doing all
four is 79–145 days, which at a solo pace guarantees four lines each 60 percent
finished. DOCX and the three i18n formats are deferred indefinitely: not "later",
but "not unless someone pays for it".

**A3 · One repository, two internal packages.** The requirement behind "let
another repo vendor this into `tools/`" is a small stable *artifact*, not a
second repository. A monorepo produces that artifact while keeping cross-cutting
changes atomic. Directory boundaries are drawn now because drawing them early is
nearly free and extracting them later is not; `git subtree` can still split the
history if the core interface ever freezes.

*Lost:* two repositories now. The cost is continuous version coordination — a
field added to a segment in the core forces a matching change in the studio,
across two CI setups, two READMEs and two release streams — and the interface is
about to change substantially, which is the worst possible moment to pay it.

**A4 · Invariant 1 becomes "no compiled extensions; pure-Python dependencies
must be pinned and vendorable".** What the invariant protects is portability
across a bare interpreter, CI, an agent sandbox and a locked-down machine — not
the dependency count. The new wording keeps all four, still excludes the things
that actually cost portability (lxml's C, pydantic-core's Rust, rapidfuzz's C++),
and lifts a pointless restriction on pure-Python packages.

*Lost:* literal zero dependencies. Worth recording that the current state is
also the easiest state to package, so this was closer than it looks; the deciding
factor was that A2 removes the i18n formats that would have forced the most
self-written parsing.

**A5 · Invariant 2 splits in two.** (2a) *Skeleton layer*: every byte the
pipeline did not deliberately change is reproduced as-is, and no DOM or AST
re-serialization is permitted. For container formats the wording is "the
decompressed content of unmodified entries is byte-identical, entry order is
preserved, and an EPUB mimetype entry is first and STORED" — byte-identity of the
container itself is not achievable, since modified entries must be recompressed
and zlib levels 6 and 9 differ. (2b) *Substitution layer*: every slot carries the
host syntax's escaping function and containment rules.

The single sentence (2a) is what excludes lxml, python-docx, ebooklib, ruamel,
remark-stringify and mdformat, on a stated and checkable ground.

*Lost:* keeping one absolute sentence and delegating containment to the prompt.
That leaves all five structural-damage cases in place, and the project's own
position is that structure is guaranteed by construction rather than by asking.

**A6 · Review workbench: Vite plus a component framework, transport is plain
JSON with one shared type definition.** This is the largest single item at 25–40
days and the only one the stated core need names directly. Thousands of segments
need virtualized scrolling, keyboard navigation, synchronized dual-pane scrolling
and correct IME behaviour, none of which a single vanilla file reaches. Freezing
the CLI/HTTP contract first makes the frontend investment survive any later
backend language.

*Lost:* typed RPC frameworks. Their value comes from the same force that pulls
logic into the server layer, and resisting that force is why invariant 8 exists.
A shared type file plus plain JSON takes nine tenths of the benefit without it.

**A7 · No overview UI; freeze a machine-readable project-status contract
instead.** An overview is a presentation problem, not a translation-assistance
problem. `lx status --json` plus a project-discovery convention, carrying a
`contract_version`, lets the CLI answer the question today and lets a separate
project answer it graphically later without this one changing shape. Glossaries
shared across a series are handled by pointing a project's config at an external
glossary file, not by introducing a workspace tier.

**A8 · Two distribution artifacts, and the binary does not drive the language
choice.** Source distribution and `uv tool install` serve agent sandboxes, CI,
vendored subdirectories and anyone comfortable in a terminal — lowest cost, built
first. A packaged installer serves everyone else, built second. The weighting
matters because EV certificates stopped bypassing SmartScreen immediately in
2024, so a clean first-run experience is no longer purchasable at any price for a
niche tool.

### B. Correctness foundations

**B1 · Build an adversarial round-trip corpus and gate on it (4–8 days).** It
must cover CRLF, BOM, nested and lazy continuations, indented code blocks, HTML
blocks, alignment-padded tables, hard line breaks, real EPUB XHTML and
Word-generated document.xml. This is the safety net every rewrite route assumes
already exists, and it is the first thing a solo schedule drops, so it is gated.

**B2 · Add three containment validators at error severity (2–4 days).** A
translation may not introduce a block-start sequence (line-initial `#`, `1. `,
`- `, `> `, a `|` inside a cell, a blank line); inside an XML host it must escape
`&`, `<` and `]]>`; paired placeholders must be present and must not cross. All
three are mechanically decidable, so invariant 4 is satisfied.

**B3 · Placeholders become typed records: role (open / close / standalone),
pair_id, can_reorder (2–3 days).** ⟦n⟧ is currently a flat unpaired slot map —
`<b>bold</b>` masks to `⟦1⟧bold⟦2⟧`, comparison is by multiset, and reordering is
defined as legal by an existing test, so `⟦2⟧粗體⟦1⟧` passes with zero issues and
renders `</b>粗體<b>`. Paired formats must not enter a segment before this lands;
doing it in the other order multiplies the "green but broken" rate with every
format added. This extends, and does not reverse, the 2026-07 decision on ⟦n⟧: the
placeholder is still an opaque integer to the model, and the type lives beside
the slot map rather than inside the token.

**B4 · Repair the existing 42 lexicon rows for decidability and severity before
adding any.** Context-dependent words (程序 / 質量 / 支持 / 文本 / 數據) leave
`checks.py` for the prompt or for human review; genuinely unambiguous ones
(軟件 / 硬件 / 視頻 / 屏幕 / 鼠標) stay at error.

*Lost:* expanding with an OpenCC table. One-to-many simplified-to-traditional
mappings are not mechanically decidable by definition, which is exactly what
invariant 4 excludes.

**B5 · TM key becomes (content_hash, context, segmentation_version), and TM hits
go through the same acceptance path as model output.** Today a TM hit is written
straight to target and marked translated, bypassing `accept()`; and `seg_hash` is
independent of the do-not-translate list, so the same sentence under two DNT
configurations produces different slot counts under one hash. Changing a DNT
entry therefore renders a bare `⟦2⟧`, caught only by a downstream check —
safety by inspection rather than by construction, which is the opposite of
invariant 2.

**B6 · Fuzzy matching is advisory only, and never applied automatically
(6–12 days).** A fuzzy hit differs in its placeholder set by definition, so
automatic application either trips the tag validator or, with validators
disabled, corrupts output silently. Also recorded so it is not rediscovered:
SQLite's FTS5 is available in the standard library, but its default tokenizer
does not segment Chinese at all — a trigram tokenizer works but carries no
industry fuzzy-percentage semantics, so thresholds, placeholder-mismatch
penalties and subsegment matching are a quality sub-project, not an import.

**B7 · `.lx/docs` moves to SQLite; the translation memory stays append-only
JSONL.** The document state is regenerable and already ignored, so moving it is
free, and it removes the whole-file rewrite amplification while making resumable
translation a per-segment commit. The memory stays JSONL because its git
diffability is the reason it is versioned at all — see the 2026-07 entry below —
and a binary blob produces unresolvable conflicts exactly in the
two-machines-or-two-branches case this tool is for. The standard library's
`sqlite3` and a `PRAGMA user_version` migration chain are sufficient; no ORM.

**B8 · Borrow XLIFF's inline-code model; do not adopt XLIFF as the internal
format; provide `lx export --xliff`.** The specification explicitly places
merge-back outside its scope and leaves the skeleton format unstandardized, so
the hard part is not solved by adopting it, and a single-user workstation has no
interchange partner to gain from it. The inline-code semantics — pairing,
reorderability, plain-text equivalence — are a free data-model reference for B3,
which is half a day of design work rather than 8–15 days of conformance.

**B9 · EPUB support covers body XHTML, OPF metadata, and all three copies of the
table of contents (8–15 days).** Body-only leaves untranslated chapter titles in
the navigation, which is not a partial feature but a broken artifact. Recorded
hazards: the mimetype entry must be first and STORED or Kindle refuses the file;
`dc:language` is rewritten rather than translated while manifest and spine hrefs
and ids are untouchable; and Japanese ruby must be masked as a whole
`<ruby>…</ruby>` subtree, because masking its tags individually sends the reading
to the model as translatable text. The existing content-hash memory makes the
three title copies agree for free.

### C. Configuration and process

**C1 · Public.** The package was written for public release throughout — MIT,
a clone URL already in the README, Homepage and Issues already in the project
metadata — and Actions has no minute limit on public repositories, so the
existing matrix runs as-is.

**C2 · GitHub Releases only, on `v*` tags.** No external account setup; PyPI
remains one additional job away if it is ever wanted.

**C3 · Feature branch, pull request, squash merge; commit messages in English
with a Conventional Commits prefix; `Co-Authored-By` retained.** English departs
deliberately from the sibling repositories' Traditional Chinese convention,
because this repository is public and outward-facing. The trailer is retained on
the ground that it records *which model*, which is information that varies, and
which is the lightweight form of the model-provenance discipline used elsewhere.

*Lost:* dropping the trailer and disclosing once at project level. That is
cleaner per commit but leaves no in-repository basis for asking which model wrote
a given change.

**C4 · CI stays at two operating systems by two Python versions, and the
no-op `translations` job is repaired.** Windows must remain in the matrix
because the CRLF defect above is Windows-specific. The `translations` job
currently reads a root `lx.config.json` that does not exist; the resulting
`FileNotFoundError` occurs inside a command substitution where `set -e` does not
fire, so the loop iterates zero times and the job exits 0 having verified
nothing.

**C5 · MIT, copyright held by `AstraKismet`.** The organization is the
publishing entity; the individual identity stays in the package authors field and
in commit authorship.

**C6 · Keep `skill/` and `adapters/`, repair their dead paths, delete
`examples/ci.yml`.** The agent entry points serve the decision that translation,
review and audit can be delegated to an agent, and a plain source tree is the
best distribution format for that consumer. `examples/ci.yml` belongs to the
downstream-PR-gate positioning, which is no longer the product. Repaired
alongside: `tools/scriptorium/bin/lx.py` is referenced in six files and has not
existed since the package moved to `src/scriptorium/`, and `adapters/README.md`
advertises a `make translate` target that was never written.

### Derived decisions

**Hedge for i18n, without implementing any of it (≈1–1.5 days).** B5 already buys
the expensive part — `msgctxt` is exactly the `context` component of the new
memory key — and B3 buys interpolation protection. What remains is a nullable
`variant` discriminator on segment identity and on the memory key, and making the
format contract an explicit registry rather than a documented convention. The
`variant` field is added during the B5 migration specifically to avoid a second
migration: a later key change invalidates the entire memory, and `variant=null`
must hash identically to the field's absence or it invalidates it immediately. No
i18n parser and no plural logic are written.

**Skeleton raw nodes are stored as BLOB, never as JSON text.** Measured: a Big5
text file containing two invalid bytes cannot be written to a UTF-8 JSON state
file at all (`UnicodeEncodeError: surrogates not allowed`), while SQLite stores
and returns the bytes unchanged, and base64 costs 33 percent and all
readability. Since A2 puts plain text in scope and older Chinese and Japanese
sources are frequently Big5, GBK or Shift-JIS — sometimes mixed or damaged — the
(2a) requirement to reproduce untouched bytes is only satisfiable by a binary
column.

**Four layers, and nothing regenerable is a source of truth.** Working state is
SQLite and approved wording is JSONL; both are sources of truth. JSON over HTTP
is a *projection* for the workbench and for agents. The target document and any
XLIFF export are *rebuildable artifacts*. Storage and presentation are separate
concerns and are not permitted to merge.

**A graphical bookshelf is a separate project, and it is the same project as the
reader.** It consumes exported documents and the `lx status --json` contract, and
nothing else — it may not read inside `.lx/` and may not call the Python API, so
that this project stays free to change its storage layer or its language. It does
overview, progress, covers and reading; it does no translation, review or
validation.

**The Go re-evaluation is scheduled, not indefinite.** It is reconsidered when
all three hold: the CLI/HTTP contract has been stable for three months; the
EPUB and text format layers have a golden corpus built from real sources; and at
least three people other than the maintainer have actually been blocked at
installation. Recorded because goldmark is a healthy project — the four-month-old
caveat in the survey applies specifically to full node position information
including inline nodes, not to the library.

### Deferred indefinitely, recorded so they do not creep back

DOCX (the difficulty is run alignment, not fidelity, and character correspondence
between English and Chinese does not exist — the industry answer is to expose run
boundaries as paired inline tags for a human to place); the three i18n formats;
ODT; XLIFF as an internal format; TMX as the memory format (frozen at 1.4b from
2005, worth an export at most); a full ICU MessageFormat parser; automatic
application of fuzzy matches; and desktop shells such as Tauri, Wails or Electron
— each of which requires a system web view and so destroys the download-and-run
property that would be their only reason for existing.

## 2026-07-28 · Per-stage backend selection is written by the CLI; its settings surface belongs to the rebuilt workbench

The data model already routes each pipeline stage to its own backend —
`routing` maps `draft` / `polish` / `repair` to a provider name — but nothing can
write it. `lx providers` lists, `--provider` overrides one run, and the workbench
dropdown selects a backend for one run; changing the project's own configuration
means hand-editing `lx.config.json`. Three decisions follow.

**Writing configuration is a CLI capability, and it comes first.** Invariant 8
is not satisfied by a form that writes JSON from the browser. `lx config` and
`lx routing` land before any interface renders them, which also means the
capability is usable from a terminal, from CI and from an agent — the three
callers that will never have a browser.

**`routing` values gain an optional model override.** Today `model` belongs to
the provider, so "same endpoint, different model per stage" requires a duplicate
provider entry whose only differing field is `model` — the entry then also
duplicates `base_url`, `api_key_env` and the timeout, and the copies drift. A
routing value becomes either a provider name or `{provider, model}`. The bare
string stays valid, because every existing configuration uses it.

**The visual settings surface waits for the rebuilt workbench (A6).** The
current one is a 369-line shell that A6 already schedules for replacement;
building a settings panel into it spends the work twice. It waits for the second
reason too: a browser-writable configuration endpoint hands any page in the
user's browser the ability to repoint `base_url` at a host it chooses, and the
document being translated is what would then be sent there. That endpoint is not
written until the origin and path hardening exists.

*Lost:* shipping the panel in the current shell to get the visual selection
sooner. The throwaway cost was small, but it puts a configuration-writing HTTP
endpoint in place before the hardening that makes it safe, and that ordering is
the part that does not come back.

Scheduled as HANDOFF-206.

## 2026-07-28 · Delegation rules split into a tracked mechanism and a per-machine binding

An orchestration convention developed on another project was offered for adoption
here. It is good material and most of it generalizes, but it is written almost
entirely in tool vocabulary — worker types, model tier names, script pitfalls —
which the process-tool-names decision below forbids in a tracked file, while the
mirror rule forbids a convention from living only in a gitignored one. Adopting
it verbatim in either place breaks one rule or the other.

Split instead. `docs/conventions/delegated-work.md` is tracked and states each
rule by the *work* it governs: what may not be delegated, what a brief must
carry, which categories of work require the highest capability tier, and that a
shortfall is marked and logged rather than absorbed. `.claude/orchestration.md`
is per-machine and binds those categories to the tooling installed here. The
tracked file wins on disagreement, and nothing may live only in the binding.

Adapted rather than copied: the source project's hazard list is its own — asset
manifests, comment language, its rendering architecture. The hazards here are
this repository's: a corpus fixture edited to make a test pass, a slot value
written without host escaping, a judgement rule pushed into `checks.py`, a
dependency added. Its lane-parallel policy was dropped outright, because the
queue here is mostly sequential by `blocked-by` and its packages converge on the
same four modules.

Kept without change, because it is right and general: retrieval has a capability
*ceiling* as well as a floor; the security tier is reserved for security work
alone; the review gate for anything feeding a decision is before the decision,
since a missing option is invisible from inside the option set; and re-review of
a downgraded artifact means re-deriving it, not reading it over and agreeing.
Recording which model produced what is consistent with C3, which keeps
`Co-Authored-By` trailers for the same reason.

## 2026-07-28 · Process-tool names stay out of the tracked process docs

An audit asked whether any available agent skill was missing from the working
agreement. What came back was a proposed `## Process tooling` section in
`AGENTS.md`, naming the ceremonies, advisors and read-only reviewers installed on
the development machine. Rejected on three grounds.

**Audience.** `AGENTS.md` exists so that Claude Code, Codex, Cursor and OpenCode
load the same rules. The tools in question are Claude Code skills. A section
naming them is inert for three of the four readers.

**Measured rot.** Five skills were disabled on 2026-07-27 as unused. The comment
in `.claude/dev-rituals.config.json` still named one of them — `design-doc-scribe`
— the next day. A list no CI can check went stale in under twenty-four hours; a
tracked one would rot the same way and be harder to notice.

**The gap was not real.** What the audit read as unwired is already recorded, in
the form that survives a tool being renamed. `.gitignore` governs the artifacts —
`graphify-out/`, `decision-board*.html` — without naming the tools that emit
them, and `docs/conventions/handoff-workflow.md` §5 already states the mechanism
each `blocked-by` kind clears by. Writing `decision-ceremony` next to `design:`
would create a second tracked copy of one rule, in two files that are required to
agree.

The rule this settles: **record the artifact and the mechanism, never the tool.**
A per-machine file may name a tool; a tracked file may not.

`.claude/dev-rituals.config.json` is gitignored — it carries an absolute
`memoryRoot` — so it is a mirror and never a source. Measured the same day: of
its eight keys, only `handoffDir`, `memoryRoot` and `docAuthor` are read by any
installed skill. The remaining five are pointers at tracked originals, and where
they disagree with `AGENTS.md`, `AGENTS.md` is right.

## 2026-07 · Placeholders use ⟦n⟧ rather than [[TYPE_NNN]]

The original design used typed, per-type-numbered tags like `[[PROTECTED_CODE_001]]`.
Three problems: the words inside are translatable-looking and models occasionally
localize them; per-type counters make the id space ambiguous when reordering; and
square brackets collide with Markdown link syntax.

`⟦n⟧` is a single sequential integer in brackets that essentially never occur in
prose, so validation is exact-multiset comparison and mangling is repairable.
The type is not encoded because nothing downstream needs it — the slot map holds
the original content.

## 2026-07 · Normalization happens on ingest, not as a check

Punctuation width and CJK/Latin spacing were initially reported as warnings. Every
warning costs reviewer attention, and both are fully decidable, so they are now
corrected in `apply` and only reported when something survives correction (which
means the segment is unusual and worth a look).

Rejected alternative: instructing the model to get them right. That spends the
model's attention on work a regex does perfectly.

## 2026-07 · Provider requests stay minimal by default

`response_format: json_object` improves reliability on hosted APIs but is rejected
outright by several self-hosted runtimes rather than ignored. Since local
deployment is a requirement, the default request omits it and JSON is obtained by
prompt plus a tolerant parser. Projects on hosted APIs can opt in per provider
with `"json_mode": true`.

## 2026-07 · Repair loop stops when a round changes nothing

Three fixed rounds burned tokens on models that returned identical output each
time. The loop now compares the failing set's targets between rounds and stops
when they match, which converts a silent waste into an actionable message.

## 2026-07 · Translation memory is committed to version control

`.lx/docs/` and `.lx/reports/` are regenerable and gitignored. `.lx/tm.*.jsonl` is
not: it is the accumulated approved wording, and losing it means retranslating
work that a human already reviewed.
