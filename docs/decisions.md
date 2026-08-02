# Decision log

Short entries, newest first. Record the alternative that lost, not just the
choice that won — the reasoning is what future changes need.

## 2026-08-02 · Plain text lands, and the format registry lands with it

Closing HANDOFF-017. A novel could not enter the pipeline at all before this: the
only parser was Markdown's and every path reached it regardless of what the file
was called. Plain text is the cheapest format that changes that, and being the
first non-Markdown one it brings the registry that was deferred out of
HANDOFF-007 to land with it. Suite 362 → 464.

**The registry is keyed by extension and the format is frozen onto the
document.** `formats.py` maps `.md`/`.markdown`/`.mdown`/`.mkd` to `markdown` and
`.txt`/`.text` to `text`; `formats.map` in configuration overrides the table and
is the only override there is. `lx extract` records `doc["format"]`, and every
later command reads it from there rather than from the path.

*Lost: a `--format` flag.* A skeleton is only readable by the parser that wrote
it, so extract and render have to agree; a flag lets one invocation disagree with
the next, and the disagreement surfaces as a corrupted render rather than as an
error. *Lost: falling back to Markdown for an unknown extension*, which is
today's behaviour and is harmless for a `.rst` and silent ruin for anything
binary. It is refused instead, with a message naming `formats.map`. Nothing in
the suite or the corpus depended on the fallback — every fixture is `.md`.

**No `STATE_VERSION` bump.** The field's own test is whether a reader of an
*older* file would be wrong, and it would not: a state file written before this
has no `format` and no `host`, and every such file is Markdown, so the defaults
are right rather than merely tolerable. *Lost: bumping to 4 anyway*, so that a
downgraded build refuses a plain-text state file instead of applying Markdown
containment rules to it. That failure is real but visible — it reports false
`containment` errors, it cannot corrupt a render, because `render` is shared and
format-independent — and the bump would cost every existing user a re-extract of
every document to protect against a build downgrade.

**`context` for plain text is the block kind, exactly as for Markdown.** Two
values, `para` and `heading`. *Lost: `None`*, which makes every context-free
segment of every format share one entry. *Lost: a paragraph index or a chapter
identifier*, which looks more precise and is the worst of the three: position in
the key drives exact reuse to zero and makes inserting one paragraph invalidate
every entry after it. Keying on the kind also means a paragraph banked from a
Markdown document answers for the same paragraph in a `.txt` — the two formats
mask differently, and `translate.accept` is what makes that safe, which is the
same reasoning that keeps the mask configuration out of the key.

**Which document shape a file is in is recorded on `host`, not on `kind`.** Plain
text arrives in three shapes and the containment rule genuinely differs between
two of them: where paragraphs are separated by blank lines a translation may
re-wrap freely, and where every line *is* a paragraph an added line is a second
paragraph. `checks.py` is given a segment and nothing else, so the shape has to
be a segment field — and it is `host` (`text` / `text-line`) rather than `kind`,
because `kind` becomes `context` and a second kind would stop one wording being
one memory entry across the two shapes for no gain.

That is also the correction to this package's first design, which put *every*
plain-text kind under a "the target may not have more lines than the source"
rule. That reinstates the line-count comparison this log rejected on 2026-07-28,
and applied to a wrapped paragraph it fails correct work at error severity: a
blank-line-mode paragraph is a maximal run of non-blank lines, so a re-wrapped
target re-parses to the same one block. The rule now applies only where an added
line provably leaves the block, which is what `_SINGLE_LINE_KINDS` always meant.

**`checks.py` gains a per-host profile table**, three questions per host: which
line shapes open a block, which kinds are inline, and which kinds may not gain a
line. Markdown's row is today's constants unchanged and its behaviour is
bit-identical. Plain text's block table is **empty** — no line of plain text
opens anything, and a line of dialogue beginning `- ` is dialogue. An
unrecognized host falls back to Markdown rather than raising: `xhtml` already
appears as a test value with no parser behind it, and a validator that took down
`lx check` over a host it had not met would be worse than one that judges most of
the document.

**Encoding detection: first success wins, and the candidate list was measured
rather than assumed.** Default `["utf-8", "shift_jis", "cp950", "gbk",
"cp1252"]`. Every entry earns its place:

- **`cp950`, not `big5`.** Python's `big5` codec rejects 裏, 碁, 恒 and 墻, which
  are ordinary in Windows-authored Traditional Chinese. Measured on this
  repository: a Big5 novel containing 裏 fails every double-byte candidate and is
  read by `cp1252` as Latin-1 gibberish — the worst outcome available, because it
  is durable. The mojibake is hashed by `store.seg_hash` and banked into
  `.lx/tm.*.jsonl`, which invariant 9 calls a source of truth. This was the
  single highest-value change in the audit and it is pinned by a test.
- **`shift_jis` before the Chinese candidates.** It fails on the Big5 and GBK
  samples, so it costs them nothing, and without it `gbk` swallows Japanese.
- **`gbk`, not `gb18030`.** The superset argument that wins for `cp950` loses
  here: `gb18030` accepted the Big5, Shift-JIS and Latin-1 samples too.
- **`cp1252` last and still present.** A Windows-authored English novel with
  smart quotes is the commonest non-UTF-8 source there is, and dropping it
  refuses that file outright. Measured: none of its five undefined bytes can
  occur in a standard Big5 or GB2312 stream, so for those it is a *total*
  catch-all rather than a near one. Only its position keeps a Chinese novel from
  reaching it.

*Lost: refusing when more than one candidate decodes.* It is the package's own
"refuse rather than mangle" posture pushed one step too far — because `cp1252`
accepts every ordinary Big5, GBK and Shift-JIS document, "more than one
succeeded" is true of nearly every non-UTF-8 novel there is, so the rule refuses
the primary use case and deletes detection rather than making it safe. What is
left is the irreducible overlap — simplified Chinese reads as Big5, a Latin-1
European source reads as Shift-JIS — and it is **announced** rather than hidden:
`lx extract` prints the codec it chose and the paragraph shape it decided.

**A byte-order mark decides, and is kept.** It overrides the candidate list,
because it is a declaration the file makes about itself. It is not stripped: it
decodes to U+FEFF and `textparse` puts it in the skeleton as a raw node — the
whole leading *run* of them, because a doubled mark is exactly what the codec
note below produces — so the model never sees it and the same paragraph hashes
identically whether or not its file carried one. `describe` and `parse` split the
document through one helper for the same reason: a mark is not whitespace, so a
file beginning `﻿\n\n` has a non-blank first line before the mark comes out
and a blank one after, and two copies of the split disagreed about the paragraph
shape they then reported. Every mark maps to a **concrete** codec — never bare `utf-16`
or `utf-8-sig`, which write a mark of their own on top of the one in the text, so
a document round-tripping through them gains three bytes each time.

**A decode that yields a NUL is rejected**, which is how a BOM-less UTF-16 file
is refused instead of silently becoming interleaved rubbish: it is valid UTF-8
and decodes without raising. No plain-text novel contains a NUL.

**A file whose bytes are invalid in its own encoding is refused, not repaired.**
Substituting U+FFFD changes bytes invariant 2a promises to preserve, and the
damage is durable for the same reason the mojibake above is. Reading one needs
raw skeleton nodes held as bytes rather than as JSON text — measured: a surrogate
from `errors="surrogateescape"` survives `json.dumps`, and dies at the file write
with `UnicodeEncodeError: surrogates not allowed` — which is the scheduled state
layer's work. The refusal message says so.

**Rendered output is always UTF-8.** `docio.write_document` has encoded UTF-8
since it existed and nothing changes. Writing a zh-TW translation back in the
source's Big5 would raise on the first target character outside the codepage, on
a document the user has already paid a model to produce. `doc["encoding"]`
records what the source was, so the decision is reversible; nothing reads it back
today. The byte-for-byte round-trip property is therefore asserted at the
skeleton, through the detected codec, which is where `tests/corpus/` already
asserts it — and the CLI path is asserted against the source's characters
encoded as UTF-8.

**Paragraph segmentation: `auto` chooses between two shapes and names the
third.** `auto` asks whether a blank line ever *separates* two runs of text —
not whether one exists, which is true of a file whose only blank line is its last
and would join its paragraphs into one segment. It never guesses `indent`
(hard-wrapped, no blank lines, an indented first line marking each paragraph),
because the available test — some lines indented, some not — is equally true of a
one-paragraph-per-line file containing a single indented line, and guessing wrong
there joins the entire book. A project with such a novel writes one config line.
Both remaining misfires are announced on the `lx extract` line that made them.

**A block's first-line indent is skeleton; its continuation lines' indents are
not.** The first is layout the model has no use for, and `translate.accept`
strips leading whitespace off every proposal anyway. The second cannot be
skeleton at all: it sits after a newline that is *inside* the segment, and a raw
node can only go before or after a whole segment. Identical in shape and in
reasoning to a wrapped list item — 2026-07-28, "Where a continuation indent
lives".

**Recorded residuals**, none of them hidden:

- Editing `chapter_patterns` re-classifies a block between `heading` and `para`
  while its text stays byte-identical, which orphans its banked wording with no
  hash change to explain it. It is the only knob in this package with that
  property. Change it before a book is translated, or accept re-translating the
  affected titles.
- `mask.py`'s inline patterns are Markdown- and i18n-flavoured, so `$5 or $10`
  in a novel is masked as a math span and the model never sees the word between
  the figures. Per-format mask patterns are a real design question and a
  different package; this one does not touch `mask.py` beyond sharing one
  predicate.
- The workbench lists only what `sources` matches, and the default is
  `docs/**/*.md`. A novel project sets its own glob. *Lost: widening the
  default* — a blanket `**/*.txt` sweeps up `config/dnt.txt`, and inventing a
  `book/` convention is a convention nobody asked for.
- `normalize`'s `collapse_space` still eats a uniformly-indented block's
  continuation indentation, so verse, epigraphs and quoted letters lose their
  shape on translation. Plain text is what makes that defect load-bearing rather
  than cosmetic; it has its own package.
- A bare Roman-numeral chapter heading (`XVII`) has letters in it, so it becomes
  a segment with nothing to translate. It reports `untranslated` at warn.

## 2026-08-02 · Terminology is discovered by suppressing sentence-initial capitals, and the target column stays empty

Closing HANDOFF-016, which implements the second half of D3 below: the glossary
already enforces name consistency and cannot tell you what a book's two hundred
proper nouns are. `lx terms <src> --lang <lang>` proposes them.

**A candidate is a maximal run of capitalized tokens joined by exactly one
space.** *Lost:* also emitting each word of a longer run as its own candidate,
which turns `Ashcombe Hall` into three rows and a two-hundred-name novel into six
hundred; a name that also stands alone is already its own run wherever it does.
*Lost, and this one is not cosmetic:* joining a run across any whitespace, so a
line break inside a wrapped paragraph would not split `Ashcombe Hall`. The
glossary matches on the **literal source string** — `check_segment` §4 and
`translate._glossary_hints` both search for it with the same word-boundary regex
— so a run joined across a newline proposes a row that can never fire. Two rows
that work beat one that cannot, and the wrapped case is instead covered by the
two single-token runs it produces.

A word is letters through Latin Extended-B, not ASCII: `René`, `Müller` and
`Françoise` are ordinary in an English novel, and an ASCII class cut the first to
`Ren` and split the second into `M` and a fragment the tool could not see. That
is a *wrong* answer rather than a missing one, which is the worse kind. A run's
last token is stripped of a possessive — `Ashcombe's carriage` and `Ashcombe
walked` are one name, and counting them separately splits a minor character's
evidence across two rows that each fall under the threshold.

**The suppression rule, which is the part most likely to be tuned later.** A
token is *sentence-initial*, and therefore evidence of nothing, when it is the
first token of its segment, or when the text between it and the previous token
ends in an opening quote, or contains one of `.!?…`. Three exceptions, in the
order they fire:

1. **A quote counts only when it is adjacent to the token.** `He said, "The door
   is locked."` must suppress `The` — the comma is not a terminator and nothing
   else would catch it, and dialogue openings are constant in a novel. But `"` is
   also a *closing* quote, and `"Run," Ashcombe said.` is the commonest line in a
   book; reading that `"` as an opening would suppress the one name in the
   sentence. Position separates them: an opening quote is the last character
   before the token, a closing one has whitespace after it. Adjacency is also
   what lets `'` and `’` stay in the set despite being apostrophes far more often
   than quotes — a possessive apostrophe is followed by a space, so `the Smiths'
   Manor` never reaches the rule, while British single-quoted dialogue does and
   is suppressed like its double-quoted twin.
2. **A `!` or `?` kept inside a closing quote does not end the attribution's
   sentence.** `"Run!" Ashcombe said.` is how English punctuates it — the mark
   stays inside the quote and the attribution continues — so without this a
   character attributed only that way has no mid-sentence occurrence anywhere.
   A full stop is excluded deliberately: English writes a *comma* when an
   attribution follows, so `"Run." Ashcombe left.` really is two sentences, and
   stripping the stop would suppress a genuine sentence opener for nothing. The
   residual false positive is `"Run!" She turned away.`, and it is accepted —
   telling it from the attribution case needs a table of attribution verbs, which
   is judgement.
3. **A full stop after a configured abbreviation does not end a sentence.**
   `Mr. Darcy` is the case: without it a character named only after an honorific
   has no mid-sentence occurrence anywhere and is never proposed.

Sentence position is per *token*, not per run: a run whose head opens a sentence
also records its tail. `Then Ashcombe spoke.` is one run, `Then Ashcombe`, and
without the tail the maximal-run rule swallows the one occurrence of `Ashcombe`
that was genuinely mid-sentence — so a name after `Then`, `But` or `And` loses
evidence it actually had.

Two candidates are dropped outright. An **honorific in the configured
abbreviation list** is not a proper noun and is the one word guaranteed to occur
mid-sentence — `said Mr. Darcy` — so left in, it outranks every real name in the
ranking it was added to help. A **single character** is dropped for a harder
reason: a glossary row `J` fires on every segment containing a bare J, so it is
not enforceable terminology under any wording a person could give it.

**A candidate is proposed when its total count reaches `min_count` *and* at least
one occurrence is not sentence-initial.** *Lost:* requiring `min_count`
mid-sentence occurrences, which is the tighter and more obvious rule. A character
name leads sentences constantly, so a name seen forty times with one mid-sentence
occurrence is a real name and would have been dropped. The bias is deliberate and
one-directional: the output is a list a person edits, so a spare row costs one
keystroke and a missing one costs the discovery the command exists for.

**Measured, 2026-08-02, on this repository's own tracked Markdown**, holding
`min_count` at 2 and everything else in this entry fixed, and varying only the
sentence-initial rule:

| Document | candidates without the rule | with it | suppressed |
|---|---|---|---|
| `README.md` | 26 | 14 | 46% |
| `AGENTS.md` | 35 | 11 | 68% |
| `docs/decisions.md` | 97 | 54 | 44% |
| `tests/corpus/long-manual.md` | 28 | 1 | 96% |

On `AGENTS.md` the eleven survivors are exactly `API CLI EPUB English HTTP JSON
Markdown Python Skeleton UI XLIFF`, and what the rule removes is `An Anything Do
Documents Every If It Never New No Not Nothing Novels Read That The They This
Three What When Where Why Working`. `long-manual.md` keeps one term, `CPU`, which
is correct — it is generated boilerplate with no proper nouns in it.

The two false positives are worth naming, because both come from the same shape
and it is the shape the rule is weakest on. `Skeleton` survives `AGENTS.md`
because of `**(2a) Skeleton.**`: the gap in front of it is `") "`, which holds no
terminator, so the label reads as mid-sentence. `The` survives `decisions.md` on
exactly 2 of its 150-odd occurrences, both of the form `**D3 · The translation
memory…**`, where the separator `·` sits where a full stop would. A structural
marker standing in for a sentence boundary is what these two documents are full
of and what a novel does not have — they are documents *about* prose rather than
prose. `The` also shows the recall bias's price plainly: two positions out of a
hundred and fifty were enough to promote it, which is the same generosity that
keeps a real name seen forty times with one mid-sentence occurrence.

**Be exact about what was measured.** The corpus is documentation; the
repository contains no novel. The two exceptions above — the quote-position rule
and the abbreviation rule — were therefore measured on constructed sentences of
the shapes a novel has, which are fixtures in `tests/test_terms.py`, not on a
book. The first book to go through the pipeline is what will tune `min_count`
and the abbreviation list, and both are configuration under `"terms"` in
`lx.config.json` for exactly that reason: a heuristic is judgement, and invariant
4 keeps a fixed table of judgement out of the deterministic half.

**The target column is left empty, and that is the line the command does not
cross.** Which characters render `Ashcombe` as 灰岸 rather than 阿什科姆 is
judgement in a person's hands; a command that invented the target would have
moved it into `checks.py`'s *input*, which is invariant 4 violated one step
upstream where nobody would look for it. So the command finds the list and a
person decides the wording.

That made an unfilled row's inertness a property rather than a coincidence, and
one half of it was missing. `check_segment` §4 already skipped a row whose target
is falsy, but `translate._glossary_hints` did not — it would have put
`Ashcombe -> ` in the required-terminology block, telling the model to render the
name as nothing, and `cli.cmd_todo` would have sent an agent the same instruction
as `{"term": "Ashcombe", "use": ""}`. Both now skip an empty target, so
`lx terms --append` is safe to run against a project already in flight: nothing
changes until someone writes a rendering in.

**`--append` writes to `cfg["glossary"]`, and invariant 11 is not applied to
it.** That is a decision, not an oversight. The path is read out of a
configuration file, which the invariant names as untrusted, and this is the first
thing in the tree that *writes* to such a path — but it is exempt on the
invariant's own stated ground, that configuration is written by hand, and
confining it now would be worse than useless twice over: `load_glossary` reads
the same path unconfined, so the command could read a glossary it refused to
append to, and a project legitimately sharing `../shared/glossary.csv` between
two books would break with no decision recorded. The exemption ends the moment
configuration becomes writable over HTTP. HANDOFF-206 carries that; so does this
entry, which is what the note in the deleted package pointed at.

The write itself concatenates the existing bytes and appends, through a temporary
file and `os.replace`. "Never rewrite or reorder an existing row" therefore holds
by construction rather than by care, and the one way a pure append can still
destroy a row — a hand-edited file whose last line has no terminator, where the
first appended row would be glued onto it — is closed explicitly. Appended rows
carry the terminator the file already had rather than the platform's, because a
hand-maintained glossary saved as CRLF must not come back with both.

The read and the write are under one guard, and it covers the *operation* rather
than the cause. Measured on CI, 2026-08-02: a read-only `config/glossary.csv`
raises on Windows and not on Linux, because POSIX `os.replace` asks the
directory's permissions and not the file's. A guard written against the cause
would therefore have been a guard that fires on one platform, and a test written
against it passed on `windows-latest` and failed on both Ubuntu runners — which
is how this was found.

**Not done, and named so it is not mistaken for an oversight.** Non-English
source is refused with a message rather than answered: the whole rule is English
capitalization, so on Chinese or Japanese the command would report success and
propose nothing, which is a wrong answer wearing a right one's exit code.
Multi-word mining beyond capitalized runs is not attempted. Neither `store.py`
nor `checks.py` was touched; D3 settled the first and the enforcement path in the
second was already measured.

Two defects were raised in review, confirmed, and deliberately left, because
fixing either *here* would make this one command look like it had a guarantee the
project does not give. **Two concurrent `--append` runs lose one writer's rows**
— a read-modify-write with no lock — which is precisely what `store.save_doc` and
`config.dump_json` also do, through the same `path + ".tmp"` and `os.replace`.
Serializing one writer and not the others buys nothing and hides the shape.
**A hand-edited `"terms": "oops"` in `lx.config.json` crashes with an
`AttributeError`** rather than a message; so does `"batch": 25`, and so would
every other mistyped key, because nothing in `config.py` validates a type. An
`isinstance` guard on this one key would read as "configuration is checked". Both
belong to a config-schema and a state-locking package respectively, not to this
one.

## 2026-07-29 · The register is a real axis, and the translation memory can see it

Closing HANDOFF-013, which implements D4 of the entry below. The knob was already
there and was being contradicted by its own prompt: `_system_prompt` returned
`f"{base}\n\n{brief}"`, and `_LANG_BRIEFS["zh-TW"]` ended, unconditionally, with
*"Write technical documentation register: neutral-formal, subject usually
dropped, 請 for instructions, active voice rather than 被. Nominalize headings."*
The last thing the model read overrode `Tone: literary.` two paragraphs above it.

**The brief is keyed by `(language, register)`, and the terminology is one string
between two register-specific halves.** Each entry is a `(head, rules)` pair —
what the target variety is, and how that register is written — with
`_LANG_TERMS[language]` placed between them. *Lost:* one brief per register with
the vocabulary copied into each. The zh-TW list is the output of the 2026-07-28
invariant-4 audit, and two copies of it drift apart silently; the drift only
surfaces in a translation months later, which is the same shape of failure the
audit itself was fixing. *Also lost:* making the terminology the whole shared
part, with each register contributing only a trailing paragraph. The brief's
opening sentence names the variety — "as used in Taiwanese technical
documentation" — and that sentence is precisely what has to change for a novel,
so the register contributes a head as well as a tail.

Two things inside the shared block did change, and only these: "use the
vocabulary that documentation uses" is now "use the vocabulary Taiwan uses", and
the first list was re-wrapped so no `X not Y` pair straddles a line break. The
first is not cosmetic — telling a novel to use the vocabulary documentation uses
is this same defect one level down. Every term, every sense-split and every
parenthetical is unchanged.

**`tone` is threaded as a parameter; it is not copied onto each segment.**
`segment_key`, `tm_lookup` and `tm_record` take it, and `tm_records` and
`prior_targets` read it off the document they are already holding. *Lost:* a
`tone` field on every segment, which needs no threading at all and would leave
`prior_targets` untouched. It loses on three counts, in order of weight: the
register is a document-level fact, and a document-level fact does not live inside
a segment — the rule `doc["eol"]` already follows; it is a state-file schema
change, so it costs a `STATE_VERSION` bump and a forced re-extract for every
existing project; and the migration has a trap, because a version-3 file has no
per-segment `tone`, so every carryover would miss on the upgrade unless
`prior_targets` fell back to the document's value — which is the threading,
reached by a longer road. `STATE_VERSION` stays 3, and the criterion is met: the
state file gains no field, so no state file this build writes would be misread by
the build before it, and none it reads has changed shape.

The *memory* file is a separate question, and the answer is weaker on purpose. It
has no version and never had one — it is append-only JSONL that people hand-edit
— so an older build reads a record carrying `tone` and simply ignores the field,
which means it would serve a literary wording to a documentation document. That
is the standing property of every field this file has ever gained, `context` and
`segmentation_version` included, and it is the price of a memory that stays
readable and mergeable. Downgrading a build is what is unguarded here, not
upgrading one.

**The default register is the key's null**, so `technical`, `null` and the
field's absence are one value. Every entry banked before this landed keeps
answering **a document in the default register**, in both tiers, by construction
rather than by a migration — which is the case that would otherwise have been a
whole-memory invalidation, since that is what every documentation project is.
Two things it does not claim. A document that already carried `--tone literary`
before this landed now misses those same entries; that is deliberate, and its own
state file still carries its translations, because carryover keys on the stored
register on both sides. And "the old tier is documentation register" is true of
everything a *model* produced — the brief ended in the documentation-register
sentence whatever `tone` had been set to — but not of wording that arrived
through `lx apply`, which was never briefed by anything. Such an entry sits in
the default tier because nothing recorded its register, not because it is known
to be in that one.

*Lost:* keying on the register always and adding a second, register-blind lookup
for the old entries, the way `tm_lookup` already does for `segmentation_version`.
It costs one extra lookup, which is nothing, and it hands a documentation-era
wording to a novel, which is the one failure this axis was added to prevent. The
same reasoning extends the existing `tm:legacy` tier: a document in a non-default
register is not offered it at all. That is the rule `variant` already had, one
step stronger — a pre-variant record cannot be *known* to be the right form,
while a pre-register record is known to be the wrong register.

**The collapse runs inside `tm_key`, not at its call sites.** `tm_key`'s
docstring argues that the key is a tuple of read fields *because* a
canonicalization rule is something someone has to keep correct, and `variant=None`
is then indistinguishable from absence by construction. `tone` cannot have that
property: its null is a string the caller is holding, so `"technical"` must
compare equal to absent and no amount of `dict.get` makes it. A collapse is
therefore unavoidable, and the choice is only where. Inside `tm_key` it is in one
place that no caller can route around; at the four call sites it is exactly the
rule someone has to keep correct. The other three fields still pass through raw.

**Carryover is keyed on the register the state file was written in.**
`prior_targets` uses *this* build's `SEGMENTATION_VERSION` on both sides — that
was decided on 2026-07-29 and the reason is that a changed segmentation changes
the segment text, so the content hash discriminates on its own. That argument
does not transfer to the register, which leaves the source byte-identical, so the
register is read from the file instead. A document re-extracted into another
register therefore carries nothing over and every segment returns to pending.

That is the intended result and the alternative is far worse: register-blind
carryover leaves documentation wording in a document that now says
`tone: literary`, and `lx commit` then banks all of it under the literary key —
one re-translation, against permanent memory poisoning. The honest cost is that
uncommitted drafts in the old register are lost from the state file, which is why
`lx extract --tone` now says so in its help.

`prior_targets(src, lang)` became `prior_doc(src, lang)` plus
`prior_targets(doc)` so that extract can read the register out of the same parse
it reads the translations from; the alternative was a second full read of a file
that is a whole book. The earlier entries below that describe `prior_targets`
reading the file are describing `prior_doc` after this date.

**The register is sticky.** `do_extract` takes an explicit `--tone`, else the
stored `doc["tone"]`, else config, else the default. Before this it re-froze the
configured default on every extract, which was harmless while the register only
reached the `Tone:` line and is not harmless now: a forgotten `--tone` would take
every carryover and every memory hit with it. `--reset` is the exception and
deliberately so — it does not read the state file at all, because it has to work
on one this build cannot read. `lx extract` prints `| tone X` when the register
is not the default, for the same reason it prints refused hits only when there
were any.

**Case and padding do not split a register.** `config.canonical_tone` strips and
lowercases for both readers — the brief selection and the key — so `--tone
Literary` and `--tone literary` are one register and one set of banked wording.
The user's own string still reaches the model on the `Tone:` line, so the field
keeps saying everything it could say before.

**Verified by mutation, not by review.** Fourteen mutants, one per guard this
change added; all fourteen turn the suite red. Removing the default-register
collapse from `key_tone` is caught by seven tests including
`test_legacy_tm_survives_tone_for_a_document_in_the_default_register`; dropping
`tone` from the key tuple by `test_tone_in_memory_key_keeps_two_registers_apart`;
letting the unversioned tier reach a novel by
`test_a_literary_document_is_not_offered_the_unversioned_tier`; making carryover
register-blind and removing the stickiness both by
`test_the_register_is_frozen_on_the_document_and_a_later_extract_keeps_it`;
selecting the default brief for every register by
`test_register_brief_replaces_the_documentation_rules_for_a_novel`; and giving
the terminology to one register only by
`test_brief_terminology_shared_between_the_registers`. No survivors, so nothing
here is redundant or untested.

**What an adversarial review pass then found, and what it did not.** Five
independent lenses over the diff, three refuters told to default to refuting.
One finding survived the panel and four more were kept over its verdict, because
a panel told to default to refuting is a filter, not an authority, and because
each of the four was decidable by reading or had already been measured:

- The zh-TW literary paragraph said *"Never 請 for an imperative"* while ending
  *"a character keeps their own diction and level of formality wherever they
  speak."* In a novel imperatives live almost entirely inside 「」, where 請坐 and
  請進 are ordinary published Taiwanese, so one sentence deleted what the other
  preserved. The scope line asked for no 請-*for-instructions*, which is a
  documentation habit, not a prohibition. Now: 請 belongs to a character who is
  speaking, never to the narrator and never as an instruction to the reader.
- **The register was resolved in two places.** `translate_segments` read
  `doc.get("tone") or cfg.get("tone")` while `store.tm_records` read
  `doc["tone"]` alone. Measured on a state file with no `tone` beside a config
  saying `literary`: the model was briefed literary and the wording was banked in
  the default tier — this entry's own failure, arriving through a divergent
  fallback rather than through the key. The document is now the sole authority
  for its own register; the config decides it once, at extract.
- `_LANG_BRIEFS` keyed the default register by the literal `"technical"` while
  `_brief` fell back through `DEFAULT_TONE`, which is the drift `DEFAULT_TONE`
  was introduced to remove.
- Two sentences in this entry over-claimed and are corrected above.

*Not taken, and each recorded where it will be picked up rather than here:* the
agent path (`lx todo`) still carries no register brief — real, but a new field on
a contract HANDOFF-203 and HANDOFF-207 will freeze, and written into HANDOFF-015,
which owns what voice instructions reach a prompt; and the workbench still cannot
show or set a register, written into HANDOFF-204. *Rejected outright:* that the
agent path is made worse by this change — before it, agent-produced literary
wording was banked with no register at all and was served to every document,
including documentation ones, so confining it to the literary tier is strictly an
improvement.

**What HANDOFF-202 inherits.** A segment's identity is five columns, and the
fifth is absent-when-default: in SQL the register column holds `NULL` for the
default register and the string otherwise, which is the same question `variant`
already poses about `NULL != NULL` in a unique index, now posed twice.

## 2026-07-29 · Novels are the primary use case, and the six things that follow from it

The maintainer stated on 2026-07-29 that translating English novels into
Traditional Chinese was the original and principal reason this project was built.
Nothing in the tracked repository said so, and — decisively — none of the
consequences had propagated. The language briefs, the request payload, the
segmentation, the memory design and the package priorities were all settled for
technical documentation, which is the only thing the project had ever been
pointed at. Closing HANDOFF-012, which re-derived them and deliberately wrote no
code; every consequence below is scheduled as its own package.

**D1 · Literary long-form is primary. Technical documentation is secondary and
stays supported.** Said in "What this project is", because that section is what
every future scope argument resolves against. *Lost:* naming both as equal
first-class use cases. "Equal" is not a decision — `AGENTS.md` already said
"long-form work", which was compatible with novels and propagated not one
consequence, and D2 through D6 would each have stayed open under it. Secondary
means the documentation path keeps its tests green and its defects fixed, not
that it is left to rot; what it loses is the tie-break when two features compete.

**D2 · Prose stays segmented at the paragraph.** Measured: a four-sentence
narration paragraph parses to exactly one segment, `kind=para`, `context=para`.
*Lost:* sentence-level segmentation. It would buy some memory reuse and a finer
review unit, at the cost of intra-paragraph flow — and flow is the deliverable
for prose, the one thing no downstream stage can recover. English prose
sentence-splitting is also a heuristic, and heuristics on dialogue, quotes,
abbreviations and em-dashes are judgement, which invariant 4 keeps out of the
deterministic half. *Also lost:* making it configurable per project, which pays
for two segmentations that can never share a memory — they differ in
`segmentation_version` by construction — in exchange for an option this entry
recommends against. The honest cost of the choice is that a long narration
paragraph is a large editing unit in review. The mitigation is sentence-level
highlighting *inside* a segment in the rebuilt workbench (HANDOFF-204), which
leaves the unit of record alone.

**D3 · The translation memory is unchanged, and name consistency was never its
job.** `store.tm_key` hashes the *whole segment*, and for prose a segment is a
whole paragraph, so exact reuse on a novel approaches zero. That is a fact about
the key's shape, not a measurement — do not cite `tests/corpus/long-manual.md`
for it, whose 75.1% duplicate rate measures its own generator; its first
paragraph says it is a fixture.

The memory is nevertheless not inert, and that is why it is not being narrowed:
it still makes documentation revision cheap, still makes any re-run idempotent,
and still makes an EPUB's three copies of a chapter title — `<h1>`, `nav.xhtml`,
`toc.ncx` — agree for free.

Name and terminology consistency belongs to the glossary, and the difference is
enforcement, measured 2026-07-29 against `check_segment`. With the row
`Ashcombe,灰岸,阿什科姆;艾什康,error`: the term is injected into the request as
required terminology *before* the model translates, and a target that drifts to
阿什科姆 forty chapters later produces two issues at error severity — `lx check`
exits 1. With no row, the same drift produces no issues at all and `lx check`
exits 0. *Lost:* a sub-segment name index inside the memory. It duplicates what
the glossary already does; it has no alignment information, so which characters
of a Chinese target correspond to which English substring can only be guessed;
and a guess is a fuzzy match, which this project has already decided is advisory
and never applied automatically. Its ceiling is therefore a suggestion beside a
green build. It would also be built from nothing — `fuzzy` appears nowhere in
`src/` — and built on `store.py`, a shared seam.

What the glossary genuinely does not do is *discovery*: it requires knowing the
two hundred proper nouns of an unread novel in advance. That half becomes a
terminology-extraction command (HANDOFF-016), not a memory feature, because
ranking capitalized token runs by frequency is mechanically decidable and
invariant 4 puts it in code, and because its output feeds the enforcement path
that already exists.

**D4 · Register selects the language brief through the existing `tone` field, and
`tone` joins the memory key.** The axis was already there and was being
contradicted by its own prompt: `config.py` defaults `tone` to `technical`,
`lx extract --tone` freezes it into `doc["tone"]`, and `_BASE_RULES` interpolates
`Tone: {tone}.` — but `_system_prompt` returns `f"{base}\n\n{brief}"`, and
`_LANG_BRIEFS["zh-TW"]` ends with an unconditional *"Write technical
documentation register: neutral-formal, subject usually dropped, 請 for
instructions, active voice rather than 被. Nominalize headings."* The last thing
the model reads overrides the knob. So `_LANG_BRIEFS` becomes keyed by
`(language, register)` with the terminology paragraph shared and only the
register paragraph varying, selected by `doc["tone"]`; an unrecognized value
falls back to today's behaviour, so `tone` stays free text for the `Tone:` line.

`tone` also enters the translation-memory key, because a register that changes
the target and cannot be seen by the key is a silent overwrite. Absent must
compare equal to absent by the same rule `record_key` already applies to
`segmentation_version`. *Lost:* leaving the key alone. The per-project memory
path (`.lx/tm.{lang}.jsonl`, relative to the working directory) does keep a
novels project apart from a documentation project — but `tone` is
*per-document*, so mixing two registers inside one project costs nothing and
fails silently. *Lost:* a second `register` axis beside `tone`, which is a
near-synonym that leaves the first one broken. *Lost:* reusing `variant`, which
contradicts its documented purpose, is per-segment where register is
per-document, and has a measurable side effect — `store.py` does not offer the
fallback to a segment carrying a variant, so every segment of a literary document
would lose it.

**D5 · Each request item carries its neighbours as read-only source, and the
retry path carries them in full.** Measured: `_user_message` builds
`{"id", "kind", "text"}` and nothing marks two items adjacent; `retry_one` sends
a single segment *alone*, and retry is the path a hard sentence actually takes.
Inside a batch the neighbours are already present as other items, so they are
referenced by id and only the two batch edges carry full text — about two extra
segments per batch of 25. On retry there is no batch to borrow from, so the
neighbours are sent in full; that is the path that needs them most.

Neighbours are source text, so invariant 3 is untouched — the model already sees
the source. **Neighbour context does not enter the memory key.** This is the same
reasoning as the mask configuration: the key is deliberately blind to what
produced a proposal, and `translate.accept` is what makes the blindness safe.
*Lost:* feeding the model its own preceding *target*. With `batch.concurrency`
defaulting to 2 over a `ThreadPoolExecutor`, the preceding target is not
guaranteed to exist yet; making it exist means translating prose serially, which
costs the whole concurrency. *Lost:* a rolling chapter summary, which is
generated content — a second model call whose errors propagate silently into
every segment under it — and unbounded in length.

**D6 · The queue is re-ordered on the shortest path to one real book, translated
end to end.** `10-now/` becomes: 013 literary register → 016 terminology
extraction → 017 plain text → 202 resumability → 014 neighbour context → 015
style sheet → 011 continuation indent → 206 per-stage routing. `90-later/` keeps
207 (which gates two others) → 204 workbench → 201 core/studio split → 205 EPUB
→ 203 `lx status --json`.

*Lost:* durability first, putting 202 ahead of everything. Losing a run is the
worse failure, but the first book will be translated in the wrong register
whatever the state layer does, and 013 is small. *Lost:* pulling EPUB forward
with plain text. EPUB is where novels actually arrive, but it is estimated at
8–15 days against plain text's 2–4, and it would push the first end-to-end
verification far out. 201 and 203 move back because they serve a consuming
repository and the separate bookshelf project respectively — neither serves
translation, which is what D1 now ranks by.

**What does not change.** *The model translates sentences; code does everything
else* still holds. Novels do not challenge it; they shift the balance, making
code's half smaller and the model's half harder. Invariant 4 survives untouched —
register, tone and flow are judgement and stay out of `checks.py`. Invariant 10
survives and matters more: on a novel the gap between "passes" and "reads well"
is the whole job, and a green `lx check` still claims only that the structure
survived and the mechanical rules passed. Invariants 1, 2, 6, 7, 8, 9 and 11 are
untouched. This was a re-prioritization, not an architecture change.

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
