# Decision log

Short entries, newest first. Record the alternative that lost, not just the
choice that won — the reasoning is what future changes need.

## 2026-07-29 · A hard line break survives normalization, and a long one is canonicalized to two spaces

Closing HANDOFF-010. `normalize()` collapsed every run of two or more blanks, so
a Markdown hard break the translation had correctly preserved was deleted on the
way in: measured `'第一行  \n第二行'` → `'第一行\n第二行'`, two lines joined into one
on render. Invariant 5 says fix rather than report; it does not say delete
something meaningful.

**The package named one culprit and there were two.** It cited `collapse_space`,
which is where the defect was found. But the ops run in the order `config.py`
lists — `punct`, `pangu`, `collapse_space` — and `punct`'s "no space after
fullwidth punctuation" rule, `(?<=[FULLWIDTH])[ \t]+`, reaches the run first. A
zh-TW line ends in `。` far more often than not, so in practice *that* op deleted
most hard breaks, and a fix confined to `collapse_space` would have passed the
package's own acceptance test — which uses a line with no terminal punctuation —
while leaving the common case broken. The package scoped `punct` OUT; that
scoping was written believing it was uninvolved. Both are guarded now, with the
same rule and no other change to `punct`.

**Three or more spaces before a line break are canonicalized to exactly two.**
*Lost:* preserving the run verbatim. Both mean the same `<br>` in Markdown, so
neither deletes meaning, and the case is decided by what the op is for: stray
runs from an editor are exactly what `collapse_space` exists to remove, and a
five-space break is one. Preserving it verbatim keeps invisible byte-level
variation between two renderings of the same wording — one from a model, one from
`lx apply` — for no gain any reader can see. This binds the *target* only; the
source side is untouched by invariant 2a, and `tests/corpus/hard-line-breaks.md`
keeps its three-space line byte-for-byte.

**A tab run is not converted, it is removed.** CommonMark's hard break is two or
more *spaces* before the line ending, so `'a\t\t\n'` marks nothing. Turning it
into two spaces would invent a break nobody wrote — the mirror image of the
defect being fixed.

**What is still removed, because it means nothing:** a single space before a line
break, a line consisting only of blanks, and a run at the very end of a segment
(a `<br>` before a block end renders nothing). `accept()` strips segment ends
before normalize anyway; `lx apply` does not, which is why the last one stays.

**The rule is applied blind to the host format.** `normalize(text, lang, cfg)`
cannot see whether the segment lands in Markdown or plain text, and a trailing
two spaces mean nothing outside Markdown. *Lost:* threading the format through
three call sites and `translate.accept` to make the op host-aware — the wrong
price for suppressing two invisible characters, when the failure it would prevent
is cosmetic and the failure it would introduce is a deleted `<br>`.

**`pangu` was checked and needed nothing.** Both of its rules are zero-width
assertions between a CJK character and a Latin one, and neither can match across
a blank, so it cannot insert or remove a space at a line end from either
direction. Recorded because the question is not obvious from reading it and the
answer is cheap to lose.

**Verified by mutation, not by review.** Thirteen mutants of the two guards, the
line-end pass and its two predicates; twelve fail the suite. The survivor is the
`[ \t]*` inside `collapse_space`'s lookahead, which is redundant given the pass
that runs before it and is commented as such — it is there so the line is correct
read on its own.

## 2026-07-29 · The workbench confines every path it is given, and answers only its own page

Closing HANDOFF-008's three measured defects: `/api/render` wrote to any path the
caller named, `src` was unconfined on every endpoint that took it, and nothing
anywhere inspected the request's origin. The fourth defect that package recorded
— the static handler escaping its root — was closed separately on 2026-07-28 and
has its own entry below.

**A third path vector the package did not name: `lang`.** It is not a path, so it
was not on the list; it is interpolated straight into a *filename* by
`store.store_path`, `store.report_path` and `store.tm_path`, and it arrives from
the request body on `/api/extract`, `/api/check` and `/api/commit`. Measured:
`tm_path("../../../../pwn")` resolves one directory *above* the project. Closing
`src` and `out` while leaving it open would have shipped the same write primitive
under a different key. It gets a whitelist rather than the confinement helper —
letters, digits, `-` and `_` — because a language tag has a decidable shape
(invariant 4), and confining the derived path would still let a tag escape `.lx/`
into the project and collide with a source document, with the answer depending on
which of the three paths an endpoint happened to build.

**The helper validates and hands back the caller's string. It never
canonicalizes.** *Lost:* returning `os.path.realpath(value)` and letting callers
pass it on, which is the obvious shape and is wrong here. Every identity in this
project is `os.path.relpath(src)` against `os.getcwd()` — `store.doc_id`,
`store_path`, `report_path`, `do_extract`'s `doc["source"]`, `default_output` —
so a different spelling of one file is a different document. Measured twice: with
the cwd reached through a junction (`mklink /J`, no elevation), and with an 8.3
short-name cwd, where `os.getcwd()` returns the short form and `realpath`
expands it. `doc_id("docs/guide.md")` is `docs_guide.md`, and
`doc_id(realpath("docs/guide.md"))` is `.._real-project_docs_guide.md`. The
second is not a crash: `/api/extract` writes one state file and `/api/doc` then
looks for another, and `default_output` formats to
`i18n/zh-TW/../real-project/docs/guide.md`, which *passes* confinement — so the
render succeeds and silently writes to the wrong directory. The 8.3 case is not
exotic: it is the GitHub Actions windows-latest layout, because `TEMP` lives
under `RUNNER~1` and pytest builds `tmp_path` from it.

**The comparison is `os.path.commonpath([root, full]) == root`, both sides
resolved, root first.** *Lost:* `str.startswith`, which matches `/proj-evil`
against `/proj`, is case-sensitive where Windows is not, and rejects every child
when the root is a drive root. *Also lost:* `pathlib.Path.is_relative_to`, which
3.9 does have but which is the same string comparison with the same blindness to
`..`. Root first because `ntpath.commonpath` lowercases before comparing and
returns the casing of its *first* argument, so the other order turns a case
difference in the candidate into a false rejection. `_static` moved onto the same
helper rather than keeping its own `startswith` idiom, and keeps its byte-identical
`forbidden` response so its existing tests measure the same thing.

**Six mechanical rules run in front of the resolution, because resolution cannot
see them.** A reserved device name, an alternate data stream, a trailing dot or
space, and a drive-relative spelling all resolve *inside* the root and still name
something other than the file the caller wrote down — measured: `{"out": "NUL"}`
answered 200 with `{"wrote": "NUL"}` while the document was discarded, and
`docs/g.md:evil` wrote bytes into a stream of a tracked source document leaving
its size and the directory listing unchanged. They are unconditional rather than
gated on `os.name`, and that costs something real: on Linux a legal filename
containing `:`, ending in `.` or a space, or whose stem is `nul`, `con`, `aux`,
`prn`, `com1`–`com9` or `lpt1`–`lpt9` cannot be reached through the workbench.
Paid on purpose, and for the reason the static-path entry below already gives —
one rule means one behaviour and one test on all four runners, and the platform
that is not the development machine is the one nobody checks. `lx` from a
terminal still reaches such a file, because the CLI does not call this.

**The CLI is deliberately not confined.** `lx render doc.md -o /tmp/out.md` is a
person typing a command. The helper lives in `cli.py` because that is where
shared logic lives (invariant 8); it is enforced at the web edge only.

**The origin control is a rejection, not a token, and absent is not the same as
wrong.** *Lost:* a CSRF token, which has to be minted, embedded and rotated, and
which buys nothing against a local process that can read it out of the served
page. Three rules: `Host` against a loopback allowlist, which is the only one
that closes DNS rebinding and the only one that works on a GET; `Sec-Fetch-Site`,
which is a forbidden header name no page can set, and where `same-site` is
refused alongside `cross-site` because a page on another loopback port is
same-site and is not us; and `Origin` by membership against all three loopback
spellings with the port included, because `serve()` opens `http://localhost:PORT`
after binding `127.0.0.1`, so the default UI's `Origin` is the *name* and never
the address. `Origin: null` is a present value, not an absent header — a
sandboxed iframe, a `data:` URL, a cross-origin redirect and an https page
posting to this http server all send it — so the test is membership and never
falsiness. A request with no `Origin` at all is accepted, because that is `curl`
and `lx`, which can read and write these files without asking anyone.

**A non-loopback bind keeps exactly the exposure it already had.** With no
loopback address to compare against, the control degrades to matching each
request's own `Host`, which does not resist rebinding because the attacker
controls both sides of that comparison. `serve()`'s existing warning now says so;
a warning that named the money and not the weakened check would be true and
incomplete.

**What this does not close, stated so it is not overstated.** Confinement bounds
the blast radius; it is not a safety property. `{"out": ".git/hooks/pre-commit"}`
is inside the project root. So is overwriting a source document. A local process
running as this user is inside the boundary by construction and a header check
cannot see the difference between it and `curl`. TOCTOU between the check and the
open is not closed and not mitigated: winning it needs write access inside the
project root, which needs exactly such a process, and that process can write
anything this user can. The absence of any `Access-Control-Allow-*` header is
load-bearing — it is why a cross-site call in `cors` mode preflights into the
existing `OPTIONS` 501 — and an absence is invisible to the next reader, so a
future `do_OPTIONS` that answers a preflight permissively silently reopens all of
this.

**Two escape hatches are recorded and not built**, per the package's own
instruction, and carried to HANDOFF-204 which owns this surface: a corpus living
outside the project root, which the confinement refuses today; and an
`--allow-origin` flag, which is the better answer for a deliberately exposed bind
than deriving the allowed origin from the request. Both are new public interface,
and neither belongs inside a closure.

**A test that asserts a file was *not* written must aim inside `tmp_path`.** The
first spelling of these tests did not, and it turned the suite red against
byte-identical correct code for a run of consecutive attempts before the cause
was found. `monkeypatch.chdir(tmp_path)` makes `tmp_path` the project, so "outside the
project" is `tmp_path.parent` — and `tmp_path.parent.parent` is pytest's shared
base directory, which pytest never cleans. One run of the *unfixed* code — a
bisect, a `git stash`, CI on the parent commit, or the measurement this package
required — leaves the escape artifact there permanently, and the test then fails
forever against a correct implementation. The fix is to nest the project root two
levels down inside `tmp_path`, so every escape target is still inside the
directory pytest owns and rotates.

**Added as invariant 11 on the same day, deliberately not scoped to the
workbench.** The rule as written binds any path the user did not type at a
terminal, whatever module receives it. *Lost:* the narrower version, "the
workbench confines every path it is given", which describes what was built and is
the obvious thing to write down. It fails on both ends. It expires the day
HANDOFF-204 rewrites the workbench, because an invariant that names an
implementation stops meaning anything when the implementation goes; and it binds
neither of the two places this defect is most likely to reappear — an EPUB entry
name, where it is called zip-slip, and `output_pattern`, which is trusted today
only because configuration is written by hand. An invariant earns its place by
constraining work that has not been written yet, which is also why it is phrased
as where a path *came from* rather than as which module reads it.

## 2026-07-29 · The memory key gains three axes, and reuse stops being a write

Executing B5 below, with the derived i18n hedge folded into the same migration
because a second one invalidates everything the first one banked. The key is now
`(content_hash, context, segmentation_version)` plus a nullable `variant`, and a
translation-memory hit is a *proposal* that goes through `translate.accept`
rather than a target written straight into the segment.

**`accept()`, not a mask fingerprint.** The IN list offered both. A fingerprint —
the do-not-translate list plus a mask-pattern version, folded into the hash —
loses on three counts. It invalidates wholesale where the gate rejects
individually: adding one DNT term would orphan every entry in the language,
including the overwhelming majority whose placeholder set that term never
touched. It models only the drift it knows about, so a new entry in
`INLINE_PATTERNS` needs a version bump that someone has to remember, while the
gate compares the actual placeholder sets and cannot be forgotten. And it puts a
config-derived value inside the key, so the same wording banked on two machines
with different DNT lists would never be one entry — which defeats the point of a
memory that travels. The gate also catches drift no fingerprint models at all: a
hand-edited memory, a target emptied by a bad merge, a `⟦2⟧` a person deleted.
*Kept from the fingerprint idea:* nothing. The key is deliberately blind to the
mask configuration, and that blindness is what the gate exists to make safe.

**Measured, both directions, and only one of them was ever visible.** Bank a
segment under `dnt=['Celurion','Acme']`, drop `Acme`, re-extract: the banked
target keeps a `⟦2⟧` the new slot map has nothing to restore, so a bare `⟦2⟧`
reached the rendered file. Add a term instead and the failure is quieter — the
target is short a placeholder, so the reused wording renders as ordinary prose
with the new term unprotected, wrong in a way no reader of the output can see.
HANDOFF-007's acceptance criterion names the second direction and the first is
the one that renders damage; both are fixtures now.

**The key is a tuple, not a digest.** `variant=None` must be indistinguishable
from the field's absence or the entire memory invalidates the moment this lands,
and a tuple of read fields makes that true by construction — `dict.get` yields
`None` for both — instead of a canonicalization rule someone must keep correct.
*Lost:* an opaque digest, which would hand a future SQLite schema one indexable
column and would read as a single value everywhere. Nothing on disk holds the
key; the memory file holds the fields it is built from, which is also what keeps
it diffable. The representation is therefore free to stay readable in a
traceback, and the requirement stops being something a test has to defend.

**For Markdown the context is the block kind, stored separately from `kind`
anyway.** It duplicates the field today and buys the format boundary: a key path
or an EPUB spine position has no `kind` to borrow, and `store` must not have to
know which format it is looking at. Measured 2026-07-28: `A shared sentence.`
hashes `649729361f3c` as a paragraph and as a blockquote, and a paragraph
translation wrapped across two lines, carried onto the blockquote, put its second
line outside the quote — reported by `containment` at the reused segment, which
is a fault location pointing at work nobody wrote.

**Unversioned records are accepted, and marked.** Every entry banked before today
has a content hash and nothing else. Refusing them would empty a user's memory on
upgrade in exchange for nothing, because the reuse they grant now goes through
the same gate as everything else; so absent means version 0, version 0 matches on
content alone, and the hit is recorded as `tm:legacy` rather than `tm` so a
reviewer can see which reuses still rest on a context-blind match. `lx commit`
rewrites the entry under the full key the first time that wording is banked
again, so the tier drains instead of lingering. A segment carrying a `variant` is
not offered the fallback — a record from before variants existed cannot be known
to be the right form. *Lost:* ignoring them, which is defensible only because
this repository's own memory is empty, and that is not true of anyone else's.

**Prior state and the memory are both proposals, and the memory is tried second
rather than skipped.** The old code took the document's own target whenever it
had one. Now a refused carryover falls through to the memory, so good banked
wording is not lost behind a stale copy sitting in front of it. `prior_targets`
keys on `segment_key` for the same reason the memory does — the paragraph and
blockquote collision is a within-document one first — but deliberately uses *this
build's* segmentation version on both sides rather than the file's: that field
guards the memory across time, while here the source has just been re-parsed, so
a changed segmentation has already changed the segment text and the content hash
discriminates alone. Keying on the file's version would make every bump silently
discard the translations `lx extract` promises to carry, which is the one thing
that reader exists to do.

**`lx apply` deliberately stays outside the gate.** It carries a person's or an
agent's own words, and refusing those at the door with no override is worse than
reporting them at `lx check`, where a reviewer is already looking. That is also
why `accept` stayed in `translate.py` instead of moving to a module of its own:
two of the three target sources pass through it and the third is excluded on
purpose, so a module named for a universal gate would misdescribe the design.
When `apply` is brought under it — a separate decision, about whether a human's
paste is refused or reported — the split can happen then.

**State files are version 3, and `.lx/tm.*.jsonl` is not rewritten.** Segments
gained `context` and `variant`, so a version-2 file would key wrongly while
looking current, and `load_doc` refuses it with the `lx extract` message.
`prior_targets` reads one anyway — that is how extract migrates it — and reads
`kind` where `context` is absent, which is not a guess: every build that could
have written such a file produced Markdown. The memory itself is append-only,
always; anything that rewrote it in place would destroy the git diffability that
is the whole reason it is version-controlled.

## 2026-07-28 · The containment validators, and what a rule may compare against

Executing B2 below, and closing the half of invariant 2b that was measured but
not checked. Three rules join `checks.py`, all at error severity and all
`checks_disabled`-able: `containment`, `escaping`, `eol`. The five structural
cases measured 2026-07-27 went from zero errors to one error each, and
`lx check` now exits 1 on a document carrying them and 0 on the same document
with ordinary translations.

**Block starts are read with `mdparse`'s own patterns, imported rather than
copied.** Whether a line opens a block is the parser's question, and a validator
that answers it independently is a second answer that drifts on the first flavour
change — with the copy being the one nobody re-reads. This does put a
`checks -> mdparse` import in the tree; there is no cycle, and the alternative
was worse. *Lost:* a private table of block-start patterns in `checks.py`, which
reads as more host-generic and is not: it would encode one Markdown flavour in
two places instead of one.

**The two new structural rules read opposite sides of the mask, and that is the
decision, not an oversight.** `containment` reads the *unmasked* text on both
sides, because what reaches the file is what `render` writes and `render` unmasks
first — a rule reading ⟦n⟧ answers a slightly different question. `escaping`
reads the *masked* target, because everything the host's own markup contributed
is a ⟦n⟧ there and restores verbatim, so what is left is exactly what the model
wrote itself, which is the only thing that can be unescaped. Reading restored
text would flag every legitimate tag.

**Comparison against the source is positional for the first line and set-based
afterwards.** A nested list item and a nested blockquote are ordinary input whose
segments legitimately *begin* with a marker, so an absolute rule fails correct
work. But `- 譯文\n- 內層` turns one item carrying a nested list into two
siblings, which a set-based rule cannot see. A translation is free to rewrap, so
position must stop mattering after the first line. *Lost:* both pure forms.
*Also lost:* comparing line counts, which fails every legitimate rewrap.

**A heading and a table cell are inline contexts and are exempt from the
block-start rule.** `## # x` is a heading whose text begins with a hash, and
`| - x |` is a cell containing a dash; neither opens anything. Applying the rule
there would fail correct work, which is the direction the zh-TW lexicon had to be
audited for on this same date. Blockquotes are *not* exempt — a blockquote's
content is a block context — so they get the rule and the one-line rule both.

**A blank line is a blank line, including a leading or trailing one.** In a
paragraph an extra blank line is cosmetic; in a list item it ends the list. *Lost:*
a carve-out per kind. Deciding which blank lines are harmless is judgement, and
invariant 4 excludes judgement from this file — one rule that is occasionally
strict beats a ladder of exceptions that becomes untrustworthy, which is the same
reasoning that keeps `_LEXICON_UNLESS_FOLLOWED_BY` to one condition per row.

**`escaping` is absolute rather than compared against the source, alone among the
rules here.** In an XML host `<`, a bare `&` and `]]>` are never legal character
data, so there is nothing a correct source could have had; if one ever fires on a
character the source itself carried, the parser that produced the segment is the
bug and this is how it surfaces rather than how it is excused. `>` on its own is
legal and is not reported.

**The host is read from `seg["host"]` and nothing writes it yet.** Markdown
declares no escaping requirement, so the table has no live row until EPUB lands.
Reading a key no writer emits looks like dead code and is the cheap half of the
prerequisite: a format that emits XHTML segments sets `host` on them and the rule
starts working with no change in `checks.py`. *Lost:* putting the host in
config, which is project-level while an EPUB has several hosts in one book.
*Also lost:* adding the key to Markdown segments now, which is a schema change
with no reader and would cost a `state_version` bump for nothing.

**The `eol` rule is one-directional and is not to be widened.** A target that
invents a carriage return its source did not have is decidable and is an error. A
target that *drops* one cannot be flagged, because a translation may rewrap and
comparing break counts fails the legitimate case.

Be exact about what that buys, because the first draft of this entry was not.
The rule fires when the *segment source* carries no CR, which is every segment of
every uniform document — LF sources never had one, and on a uniform CRLF document
`split_terminator` and `emit_seg` keep it out. So a model inventing a control
character in an ordinary document is now caught, and that is the win. It does
**not** touch the mixed-terminator residual recorded above under "Where a line
terminator lives": there the CR is already in the source, so `"\r" not in src` is
false and the rule is inert — measured on `tests/corpus/crlf-mixed-terminators.md`,
where CRLF kept, LF only and a bare CR added all still report zero structural
issues. Catching that needs comparing CR *position*, which a translation is free
to change by rewrapping, so it is not decidable and invariant 4 excludes it. The
residual stands whole; only the population it applies to was ever small.

**Deliberately not covered**, so it is not mistaken for an oversight: a target
that *builds* a table, which needs a two-line lookahead for a separator row and
is a different shape of rule; and automatic repair, because a structural
violation in a translation is a meaning question and silently rewriting a
translator's line break is worse than failing (invariant 5 yields here, as B2
already recorded).

**The false-positive guard is a sweep, not a list.** Every segment of every
`tests/corpus/` fixture, translated to itself, must produce zero structural
issues — nested lists, lazy continuations, alignment-padded tables, CRLF and
CR-only terminators and the 112k-character manual, without anyone having to think
of each case. Per-rule fixtures come in must-fire and must-not-fire pairs on top
of that.

**Invariant 10's caveat comes off.** Both halves are now discharged: the lexicon
audit earlier today, and the structural half here. A green `lx check` is evidence
again — necessary *and* sufficient for what the exit code claims. What it does
not claim is unchanged: it says the structure survived and the mechanical rules
passed, never that the translation is good.

## 2026-07-28 · Placeholder slots become typed records, and the state file gains a version

Executing B3 below. A slot was `{"1": "<b>"}`; it is now

```json
{"1": {"original": "<b>", "role": "open", "pair_id": "p1", "can_reorder": false}}
```

with `role` in `open` / `close` / `standalone` and `pair_id` null unless the slot
is half of a pair. The token the model sees is unchanged — still a bare integer —
so this extends the 2026-07 entry on ⟦n⟧ rather than reversing it.

**Pairing is a stack, and unbalanced markup is ordinary input.** An unclosed
`<br>`, a stray `<` in prose, a `</i>` whose partner is in the next block: these
are what real documents contain, so nothing raises and nothing guesses. Anything
unmatched stays standalone, which is also its state at creation, so there is no
cleanup path to get wrong. The stack is searched *downwards* for a matching name
rather than read at its top, because `<b><br>x</b>` is otherwise shadowed by the
`<br>` and records no pair at all. *Lost:* a void-element table (`br`, `img`, …)
to keep those off the stack. It would be a second place to be wrong about HTML,
and the general rule already covers the case that motivated it.

`pair_id` is named for the opening slot — `p1` for a pair opened by slot 1 — so
pairs read in document order and the id is stable. *Lost:* XLIFF's spelling,
where `ec/@startRef` is the start code's own id. Borrowing the semantics is the
point of B8, but an id that is sometimes a slot id and sometimes not invites
`slots[pair_id]`, which returns the opening record and looks correct in every
test that has one pair in it.

`can_reorder` is derivable from `role` today and stored anyway. It records that a
standalone slot may be repositioned freely against every other placeholder and a
pair member may not — the pair as a unit still moves — and a format with a
standalone code that must not move (XLIFF spells it `canReorder="no"`) is the
case it exists for.

*Lost:* a `kind` field carrying the pattern name that produced the slot. It is
free at mask time and the escaping validator will want something like it, but the
2026-07 entry refused to encode type for the same reason — nothing downstream
needs it yet — and the argument that bought `variant` early in B5 does not
transfer: that one avoids a second **memory** migration, which invalidates
everything, while document state is regenerable and re-extracting is cheap by
construction.

**The old shape is refused, not upgraded in place.** `.lx/docs/*.json` carries
`state_version: 2`; a file without it fails with a message naming
`lx extract <src> --lang <lang>`, which rebuilds it and carries the translations
over by content hash. *Lost:* reading both shapes. It is a few lines and it costs
nothing at the door, which is the problem — an upgraded-in-place document would
load cleanly with every pair silently reading as standalone, so `⟦2⟧粗體⟦1⟧`
would keep passing in a file that looks current. That is the defect the records
exist to remove. `store.tracked` deliberately stays version-independent: it only
counts segments for `stats` and the workbench list, so a file waiting to be
re-extracted should appear rather than take the listing down.

**The two directions are not symmetrical, and treating them as one place to
check is how the first version of this was wrong.** An *older* file only misleads
a reader, so `load_doc` refuses it while `prior_targets` — the read that lets
extract migrate it — does not. A *newer* file holds fields this build cannot
represent, and every write is a whole-file rewrite, so anything that could save
over it must stop first. Measured on the first shape of this change: `lx check`
refused a state file marked version 3 while `lx extract` and `lx run` rewrote it
as version 2 and exited 0, dropping the unknown fields. Both readers now refuse
that direction, and the message names `lx extract --reset` — which skips the read
and so overwrites it deliberately, which is what the flag means.

**Validation.** Pair order and non-crossing join placeholder integrity under the
existing `tags` rule at error severity, so `checks_disabled` treats them as one
thing. Standalone slots keep multiset semantics — moving a URL is ordinary.
Deliberately not checked: a pair that stops *containing* another without crossing
it, because reassociating emphasis is a translator's decision and a rule against
it would fail correct work. Validator messages name slot ids and never slot
contents: `translate._user_message` feeds `issues` back to the model as
`problems`, so a message quoting `<b>` would show it markup, against invariant 3.

**The prompt is unchanged, deliberately.** `_BASE_RULES` tells the model to move
a placeholder wherever the target grammar needs it, which stays true — a pair
moves as a unit. It says nothing about the two halves' order, and the package
that added the records requires the model-facing payload to stay as it was, so
this is recorded rather than fixed: if the `tags` failure rate on documents with
paired markup turns out to be dominated by inverted pairs, one clause about
opening and closing placeholders is the cheap repair, and it needs no field from
the slot record to say it.

`test_reordered_placeholder_is_fine` asserted free reordering as a property. It
was rewritten rather than deleted — standalone may reorder, pairs may not invert
or cross — because the half that is still true is worth keeping asserted.

This discharges one of the four validators HANDOFF-006 carries. The invariant 10
caveat stands until the other three land — which they did later the same day; see
the containment-validators entry at the top of this file.

## 2026-07-28 · The zh-TW lexicon audit: what invariant 4 excludes from a substring table

Executing B4 below. The table held **45 rows, not the 42 recorded there** — a
miscount, corrected here rather than silently. Every row was read against
invariant 4 and sorted three ways: 19 stay at error, 8 drop to warn, 18 leave.

**Two questions decide a row, and they are the reusable part.** *Does the string
carry a standard zh-TW sense of its own?* If it does, the row is judgement —
物體的質量 is mass, 法律程序 is a legal procedure, 三角函數 is mathematics — and
it goes to the language brief and `skill/reference/zh-TW.md` instead. *Can it
fall out of an ordinary zh-TW phrase across a word boundary?* Chinese is unspaced
and the match is a plain substring, so 電視頻道 contains 視頻 and 參數組合
contains 數組. That row may report but must not fail a build, so it is warn.

The second question is the one the earlier reading missed. It is not about
meaning at all — 內存 and 數組 are never Taiwanese forms — which is why they were
sitting at error while 體內存在抗體 and 參數組合太多 would have stopped a build.

**The eighteen that left** are 程序, 數據, 質量, 支持, 文本 (the five measured
false positives) plus 對象, 函數, 指針, 進程, 登錄, 交互, 隊列, 菜單, 默認, 音頻,
智能, 視圖, 用戶. **The seven demoted** are 內存, 激活, 集成, 調試, 數組, 變量,
帶寬, joining 復用, which was already warn.

**One tightening mechanism, deliberately tiny.** A row may name a closed set of
continuations it does not fire before: `視頻` skips 頻道/頻率/頻寬/頻譜/頻段, and
`鼠標`, `兼容` skip 標本 and 兼容並蓄. Three rows use it. The discipline is one
condition per row over a closed set — B4 said 視頻 and 鼠標 stay at error and this
is how they do so without failing on 有線電視頻道.

*Lost:* demoting the whole table to warn. The entry test is per row; 軟件 has no
Taiwanese sense and no collision, and a rule that cannot stop a build is a rule
the pipeline could have left to the prompt. *Lost:* letting a row carry a list of
longer words that are exempt. The collisions are productive patterns, not fixed
words — 電視頻道, 公視頻道, 有線電視頻道 are all different strings while 頻X is a
closed set of about six words, so the guard belongs on the continuation, and a
list would have to grow forever, which is the heuristic ladder that makes a
validator untrustworthy in the other direction. *Lost:* deleting the eighteen
outright. The signal is real, it just needs a reader; a project that knows its own
domain restores any of them through `lexicon_extra`, where the judgement is its
own to make.

*Not reopened:* word segmentation, which would decide every one of these
properly and needs a compiled extension (invariant 1); and the OpenCC table B4
already refused.

Half of the invariant 10 caveat is now discharged. The other half is the
containment validators, and until those land a green exit code is still necessary
and not sufficient — they landed later the same day, and the caveat came off with
them; see the entry at the top of this file.

## 2026-07-28 · Three edge decisions: static-path confinement, retry timing, and the bytes we write for ourselves

Taken while fixing five measured defects. The fixes themselves need no record —
they are in the history — but three of the answers were choices with a losing
alternative, and one measurement contradicts what was written down.

**A static path is decided after resolution, and a backslash is a separator
everywhere.** The workbench's guard normalized with `posixpath` and rejected a
leading `..`, which is a decision taken on a string before the filesystem has a
say. `posixpath` does not treat `\` as a separator and Windows' `open` does, so
`GET /x\..\..\..\..\..\pyproject.toml` returned this repository's
`pyproject.toml` with a 200 — measured, 1305 bytes. The guard is now percent-
decoding, then `\` rewritten to `/` on every platform, then `realpath` of the
join compared against `realpath` of the root.

*Lost:* rejecting backslashes only on Windows, where they are exploitable. It
halves the test surface and leaves Linux and Windows running different rules,
so the platform that is not the development machine is the one nobody checks.
*Also lost:* keeping the unknown-path fallback that served `index.html` with a
200, on the theory that a client-side router may want it later. There is one
page and no router; the cost today is that a typo renders as a blank
application and a traversal that gets through looks like an ordinary success.
Unknown static paths are 404. The rule if a router ever arrives: fall back on
the router's own prefix, not on everything.

Bounding, stated so it is not overstated: the server binds `127.0.0.1`, so the
reachable set was local processes running as this user, never the network.

**`Retry-After` is honoured as a number; its HTTP-date spelling is not.** A
hosted API returning 429 knows its own window and an exponential guess is
strictly worse information. *Lost:* parsing the date form. It needs a date
parser and a clock-skew policy to save a caller a few seconds, and falling
through to our own backoff is only slower, never wrong. The backoff also gained
jitter inside the existing 20-second ceiling, because a batch runs several
requests concurrently against one server and without jitter they all retry in
the same instant — the burst that caused the failure, arriving again on schedule.

**Files this project writes for itself get LF, as a choice rather than an
invariant.** Invariant 2a deliberately excludes them, and `docio` exists because
2a claims user documents only. The reason to pin them anyway is different:
`lx init` scaffolds `lx.config.json`, `config/glossary.csv` and `config/dnt.txt`
into someone else's repository, and `dump_json` writes `.lx/` state a project may
track — so the platform-default meant one command producing two different trees
and a whole-file diff the first time two machines shared a project. *Lost:*
leaving them alone on the grounds that no invariant claims them, which was the
position taken by omission when the byte-level I/O work shipped. It is correct
about the invariant and wrong about the consequence.

**A correction to a measured claim.** The queue recorded the ~1s in
`test_unreachable_server_gives_actionable_message` as the cost of the retry
loop sleeping after its final attempt. On Windows that second is the *connect*
timeout: `127.0.0.1:1` is refused instantly on Linux but times out here. The
spurious sleep was real and is fixed, but a timing test that connects to a dead
port measures the platform rather than the fix, so the one that guards it now
uses a mock server answering 503 at once.

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

**The parser held the line terminator; the CLI threw it away. Now fixed.**
Measured while verifying this package: `cli.py` opened source files in text mode,
so universal newlines deleted every CR before `parse` was called, and wrote the
rendered file in text mode, so every `\n` became `os.linesep`. A CRLF document
therefore round-tripped on Windows by coincidence and lost every CR on Linux, and
no mixed-terminator document survived anywhere. The parser fix was a strict
prerequisite — normalizing at the boundary hides the parser defect rather than
removing it — so it landed first, and the I/O layer followed as its own package.

**Outcome.** Documents are read and written as bytes through `docio.py`.
Measured end to end on the real CLI, on Windows: a CRLF source renders CRLF, an
LF source renders LF, and each is byte-identical to its input. All 27 corpus
fixtures survive `read → extract → render(fallback) → write`, and the suite goes
from 59 tests to 124. `store.append_tm` gained `newline="\n"`, so the memory log
stops contradicting the `*.jsonl text eol=lf` rule in `.gitattributes`.
`config.py` and the `.lx/` JSON state were deliberately left in text mode: they
are files this project writes for itself and no invariant claims their bytes.

**A document's terminator lives in the document's state, not in its segments.**
This is the question the I/O fix forced, because byte-exact reading is what first
lets a CR reach a segment source: `parse` splits on `"\n"`, so a wrapped
paragraph in a CRLF file yields `'line one\r\nline two'`. `split_terminator`
classifies a document as uniform-CRLF, and if it is, hands `parse` LF text and
records `doc["eol"]`; `do_render` re-imposes it with one blanket substitution.

Three measurements decided it, and each rules out the cheaper alternative of
simply accepting the CR:

- *No check can tell whether the model got it right.* Five replies for one
  wrapped segment — CRLF kept, LF only, lines joined, a break added, a bare CR —
  all produced zero errors from `check_segment` and none was collected by
  `failing_segments`, so the repair loop is structurally blind to a wrong one.
  Invariant 4 admits a rule only if a program can decide it, and the only
  candidate — comparing break counts — rejects the legitimate case where a
  translation rewraps. A green `lx check` would have covered a document with
  mixed terminators.
- *The human path cannot preserve one either.* The workbench edits segments in an
  HTML `textarea`, whose value has CRLF collapsed to LF by the parser before a
  reviewer types anything. Two of the three sources `AGENTS.md` treats as equals
  therefore cannot round-trip a CR even in principle.
- *Accepting it is what splits the translation memory.* The same sentence hashed
  `8fcdf9940052` under CRLF and `c788218aac8a` under LF, so a Windows copy and a
  Unix copy of one document shared nothing. Normalizing produces the LF hash —
  which is exactly what text-mode reads produced all along, so no existing `.lx/`
  state or memory entry moves. Doing nothing was the option that would have
  broken continuity, by minting CR-bearing hashes that never existed before.

The losing alternatives. **Masking the CR as a placeholder** renumbers every
do-not-translate slot in the segment, because `mask` runs its inline patterns
before the DNT list; the hash does not move, so `do_extract` carries a prior
target onto a segment where the same `⟦n⟧` now means something else. **Making
the segment source LF-canonical and recording the terminators on the node**
fixes the hash too, but breaks `identity_roundtrip` for 3 of 27 fixtures — the
skeleton alone would no longer reproduce the file, which is the property that
test exists to state — and it commits the node schema before HANDOFF-202 has
written one. **Re-imposing per segment at render** covers the mixed case, but
keeps the CR in `source`, so it keeps the memory split.

**Recorded residual, not a hidden one:** a document whose terminators are
*already* inconsistent keeps today's verbatim behaviour and still hands the model
a CR. One fixture of 27, roughly one tracked file in 76. Handling it needs a
per-segment mechanism, which is the wrong price for that frequency; the
containment validators own it. `test_mixed_terminators_keep_todays_behaviour`
asserts the residual so it cannot quietly change. The containment validators
later the same day did **not** close it, contrary to what this line first said:
their `eol` rule fires only when the segment source carries no CR, and here it
does, so the rule is inert on exactly this document. What it does close is the
neighbouring case — a model inventing a CR in an ordinary document — which is the
larger population but not this one.

Two smaller consequences worth knowing. `normalize`'s trailing-whitespace rule
is `re.sub(r"[ \t]+$", "", out, flags=re.M)`, and in multiline mode `$` anchors
immediately before `\n` — an intervening `\r` blocks it, so on a CRLF document
invariant 5's deterministic fixer was silently going dark. Keeping the CR out of
the segment fixes that for free. And `lx render --out -` now writes through
`sys.stdout.buffer`, because a document on stdout should no more be newline- or
codec-translated than one on disk; the diagnostics printed by `lx todo` and
`lx check` have the encoding half of that problem and not the newline half, so
HANDOFF-003 still owns reconfiguring the stream for them.

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
product lines, not four features — Okapi's OpenXML filter has a defect history
going back at least a decade and still accrues structural ones. Doing all four is
79–145 days, which at a solo pace guarantees four lines each 60 percent finished.
DOCX and the three i18n formats are deferred indefinitely: not "later", but "not
unless someone pays for it".

*Citation corrected 2026-07-29, the decision unchanged.* The original wording was
"under development for fifteen years and still has open content-loss bugs", and
the issue it rested on — #458, *OpenXML: Text runs containing multiple text
fragments + tabs lose content on merge*, opened 2015-05-09 — is **closed**. That
is the kind of claim the first reader who checks it discovers, so it is replaced
with issues verified open from the GitLab API on 2026-07-29:

- **#1341**, *OpenXML Filter: DOCX: document corruption on processing texbox with
  a hyperlink*, opened 2024-01-31, still open, labelled `bug`.
- **#1200**, *OpenXml filter adds tags around nnbsp by default*, opened
  2023-03-06, still open, labelled `bug` — the reporter states it leaves roughly
  30% of segments untranslated in existing projects.

The project moved off Bitbucket and SourceForge; the live tracker is
<https://gitlab.com/okapiframework/Okapi>, and the old
`github.com/okapiframework/okapi` path 404s.

**The stronger citation for invariant 2a is a different issue entirely**, and it
is worth knowing because it is in *our* format rather than DOCX: **#704**,
*Markdown filter drop spaces in code block*, opened 2018-04-03, still open, last
touched 2024-10-05, labelled `bug`. Fenced blocks lose the leading spaces of
continuation lines, and indented code blocks lose content-critical leading
whitespace. Eight years unfixed, in the most widely deployed open-source filter
framework in the industry, on the one format this project supports — which
removes the "well, DOCX is famously horrible" escape hatch from any argument
about re-serialization.

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
