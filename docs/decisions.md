# Decision log

Short entries, newest first. Record the alternative that lost, not just the
choice that won — the reasoning is what future changes need.

## 2026-08-03 · Which tools the naming rule governs, and an ignored directory that is not free to rebuild

The 2026-07-28 entry below settled that a tracked file may not name a tool. An
audit ran that rule against the working tree rather than against the entry, and
found the rule true, the entry's evidence false, and the subject ambiguous enough
that both readings are actionable — which is the state in which a rule is not
enforced but merely remembered.

**The evidence sentence was false about the file it cites.** That entry offers
"`.gitignore` governs the artifacts … without naming the tools that emit them" as
proof that no gap existed. The comment directly above the ignored path read
"regenerate with" and then the tool's name, as a runnable command. It was added
2026-07-27, one day *before* the rule; the commit that added the neighbouring
scan-scope block on 2026-08-03 quotes the rule in its own message, paraphrases its
new comment to "a local analysis tool" to comply, and leaves the literal violation
three lines above untouched. Nothing mechanical checks this, so the rule reaches
only text someone is currently writing.

**The subject had two live referents.** Three lines earlier the same file carries
the identical construction for this project's own CLI — "rendered output —
regenerate with `lx render`". `AGENTS.md`, `CLAUDE.md`, `adapters/` and
`docs/windows-setup.md` name four assistant tools by name, the last with a whole
setup section. And the 2026-07-28 entry itself names two ceremonies in its own
prose while stating that a tracked file may not. Read as "any tool", the rule
condemns all of that; read as "process tooling", it condemns one comment. Both
readings are actionable, so one of the two protections had quietly evaporated.

### The boundary

Three categories, and the rule governs one of them.

1. **This project's own interface** — `lx`, `python -m scriptorium`, `make`. Must
   be named, everywhere, in every tracked file. It is the product; a document that
   cannot say `lx render` is not documentation.
2. **Host integrations this project supports** — the assistants that load
   `AGENTS.md`, and anything `adapters/` targets. Must be named, because being
   loadable by a named host is the whole reason those files exist, and a reader
   setting one up needs its name.
3. **Process tooling on one developer's machine** — ceremonies, advisors, local
   analysis tools. **This is what the rule governs.** None of the four readers of
   `AGENTS.md` can act on it, it is not installed in CI or on anyone else's
   checkout, and it rots: five of these were disabled as unused on 2026-07-27 and
   a gitignored comment still named one of them the next day.

The recorded exception: **this log may name a category-3 tool inside a dated
entry**, because a decision about a specific tool cannot be recorded without
identifying it, and an entry is a fact about one day rather than an instruction.
A rule, a checklist, or a comment that tells the reader to run something may not —
that is the form that goes stale while still being obeyed. This entry is written
without naming one, to show the constraint is not onerous.

*Alternative that lost:* a grep lint over tracked files for known tool names. It
fails on its first run against every category-2 name, so it can only exist after
the narrowing above — and once narrowed, deciding whether a name is category 2 or
3 is judgement, which invariant 4 keeps out of mechanical checks. The honest
enforcement is that the boundary is now written down and short enough to apply.

### The ignored directory that is not free to rebuild

Same audit, second finding, and it is why the `.gitignore` comment changed rather
than merely losing a word. The analysis output directory is ignored, so git holds
no copy of it — and it contains 62 already-paid extraction entries whose cache is
deliberately unversioned by the tool precisely because re-extraction is billed.
Regenerating it is not restoring it: two runs over the identical corpus with the
identical prompt, two days apart, produced 86.6% and 4.1% coverage of the
explanatory layer, the second costing 25% more input. The tool's own backup is
written *inside* the directory it protects, is disabled by an environment
variable, and is overwritten in place by a second run on the same day.

"Regenerate with …" therefore stated a cost that does not exist. The comment now
states the artifact and the property — not free to rebuild, copy it out before
deleting — which is the same shape the rule asks for and happens also to be true.

*Alternative that lost:* tracking the cache. It is 1.3 MB of machine-specific,
content-hash-keyed JSON that no other checkout can use, and invariant 9 puts an
analysis artifact on the rebuildable side of the line. What it needs is a copy,
not a history, so the copy lives outside the working tree and the per-machine
notes record where.

### Why no commit-time automation was installed

The obvious mechanism — the analysis tool's own hook installer — is refused, and
the reason is worth recording so it is not proposed again. It takes no arguments
and does three things unconditionally; the third appends a merge-driver line to
this repository's **`.gitattributes`**, after the rule whose own comment reads
"Last rule wins, which is why this sits at the end". That rule is the round-trip
corpus protection invariant 2a depends on, and it is a tracked file. Separately,
the rebuild it installs takes no file lock on Windows — the lock helper falls
through when `fcntl` is unavailable — while this repo deliberately runs delegated
writers in parallel worktrees, which is exactly the concurrent case.

What replaced it is a **query-time check rather than a commit-time rebuild**: the
graph stamps the commit it was built from, nothing in the tool ever compares that
to `HEAD`, and a per-machine script now does, with an exit code. Mechanical,
falsifiable after the fact, and it starts no background writer. Measured when it
was written: the graph on this machine was 20 commits behind, missing 67 of 165
module-level definitions in `src/`, with three modules carrying no node at all.

## 2026-08-03 · A link reference definition is decided by the whole line, and it may not interrupt a paragraph

Closing HANDOFF-022, the rule HANDOFF-021 measured and deliberately left open.
`DEF_RE` decided that a line was a definition on the strength of `[label]:`
alone. CommonMark decides on the whole line — the destination and the optional
title both have to parse — and when they do not, the line is an ordinary
**paragraph**.

Every disagreement cost text twice. The line itself went into the skeleton
untranslated, and because the branch closed the paragraph, the indented line
under it became an indented code block and went with it. Two blocks for one wrong
answer, and `lx check` exits 0 through both.

The shape that matters most is not a documentation edge case. `[Ana]: Hello
there, she said.` is a link reference definition to `DEF_RE` and a paragraph to
CommonMark — so a line of bracketed dialogue, in the use case this project exists
for, was never translated and nothing said so.

### The rule that decides a destination

Written as a scanner rather than a class, because a class cannot do it — the
alternative HANDOFF-021 threw away closed four of six measured rows and left the
two a person actually types. After `[label]:` and its run of spaces or tabs:

- a **destination**, in two forms and with no fallback between them. Angle:
  `<…>`, which admits a space and admits control characters, and refuses a line
  ending or an unescaped `<`. Bare: a run holding no ASCII space and no ASCII
  control character, whose parentheses balance and whose backslash escapes what
  follows it. CommonMark's bare form "does not start with `<`", so `[x]: <url`
  is a paragraph rather than a definition whose destination is `<url`.
- an optional **title**, `"…"`, `'…'` or `(…)`, separated from the destination by
  spaces or tabs. A `(` inside a parenthesized title refuses the *title* rather
  than nesting.
- spaces or tabs, then end of line.

Three characters in that rule are counter-intuitive and all three are measured
against markdown-it-py rather than read off the spec. **NUL is a legal
destination character** — markdown-it replaces U+0000 with U+FFFD before parsing,
so refusing it would move a line into the skeleton that CommonMark keeps out.
**U+3000 and U+00A0 are legal destination characters too**, so `[x]:　/url` is a
definition whose destination begins with an ideographic space, and `[x]: /url　`
is one whose destination ends with it. And **a backslash does not escape a
space**: it ends the destination *at the backslash*, which is what makes
`[x]: a\ "t"` a paragraph.

*Lost:* implementing this against the CommonMark spec's prose. The spec and
markdown-it disagree, and where they do the measurement is the reference this
repository has actually used since 2026-08-02.

The post-colon run in `DEF_RE` stops being an equivalent mutant here and becomes
**load-bearing**. Its only consumer used to be `not m.group(2).strip()`; it is
now the input to the destination parser, and `\s*` would consume the form feed in
`[x]:\x0c/url` and read a definition where CommonMark reads a paragraph. The old
"equivalent mutant" note is replaced rather than kept — an equivalence a later
change invalidates is worse than no note.

### Where the two references disagree, this parser refuses

markdown-it trims the whole reference before parsing it; the spec does not. They
therefore disagree in **both** directions about a trailing run of whitespace:

- `[x]: /url "t"　` and `[x]: /url\x0c` are definitions to markdown-it and
  paragraphs to the spec;
- `[x]:　` is a paragraph to markdown-it and a definition to the spec, whose
  destination is one ideographic space.

This parser refuses all four, which lands on the spec's answer for the first pair
and markdown-it's for the second. That is not a compromise: it is the one rule
this area has, that where the references cannot agree what the line is, the text
stays translatable. Measured exhaustively over 20096 candidate lines — 44
destinations × 32 titles × 7 separators × 8 post-colon runs × 8 trailing runs —
**0 lines this parser calls a definition and CommonMark calls a paragraph**, and
every one of the 1657 in the other direction explained by that trim and nothing
else. The classifier for "explained" applies markdown-it's own `.trim()` first
and asks whether the two then agree, rather than pattern-matching the input,
because a second cause hiding behind the first is exactly what a crude classifier
misses.

**One defect in the rule was found by reading it, not by measuring it**, and it
is the reason the title axis is 32 spellings rather than 22. A `(` inside a
parenthesized title refuses that title; the first spelling applied that to all
three delimiters, so `[x]: /url "a (b) c"` — an ordinary sentence in an ordinary
title — stopped being a definition. Every one of the 22 title spellings agreed
with markdown-it, because not one of them put a parenthesis inside a quoted
title. The sweep, the mutation pass and the corpus were all blind to it, and it
took re-reading the function against the rule it implements.

### A definition that spans two lines is not read as one

Both the destination and the title may sit on the line below, and `mdparse` reads
one line at a time. Deciding this was in scope precisely because leaving it
implicit is how a gap survives a package.

**Refusing to look wins, and the reason is the primary use case.** Reading the
second line means `[Ana]:\nHello` takes *both* lines into the skeleton, because
`Hello` is a valid bare destination — a one-word line of dialogue lost in
silence. Refusing hands the second line of a genuine two-line definition to the
model as prose, which is visible: it appears in the workbench, in `lx todo`, and
in the rendered document. Measured 2026-08-03: markdown-it reads
`[x]: /url\n"a title"` as one definition where this reads a definition plus a
paragraph, and reads `[x]:\n/url` as one where this reads a two-line paragraph.
864 lines of the sweep's permissive column are this decision and nothing else.

*Lost:* the lookahead. It is not merely riskier, it inverts the rule this whole
area is built on — it makes the parser more confident exactly where it has less
information.

### `checks.py` takes the rule, not the pattern

One question, one answer, which is already why that module imports `mdparse`'s
patterns instead of restating them. So the table entry becomes
`opens_a_link_definition` and the table's entries become **callables** rather
than patterns — six regexes and one function is a table whose reader has to check
which.

Narrowing **sharpens** the check rather than weakening it. The question it asks
is "does this target line disappear from the render", and only a well-formed
definition disappears; a target of `[安娜]: 你好 世界` is an ordinary paragraph and
was being reported at error severity, which is the failing-correct-work direction
this repository treats as the more expensive one. What it gives up is a
definition the model spreads over two lines, which `_block_start` cannot see
because it reads one line at a time — the same blind spot the parser has, and the
same place it would have to be fixed.

*Lost:* keeping the wide pattern in `checks.py` and the narrow rule in the
parser. It buys the two-line case and pays with every near miss, and it splits
one question into two answers that drift.

**The corpus segment-line claim, re-measured.** Of 2251 segment lines, the rule
names **2** where the pattern names 10, and the eight it drops are every near
miss in this package's own fixture. The two are `　[not-a-ref]: /url`, which
`_block_start` lstrips into a match though the parser reads a paragraph, and
`[lazy]: /url`, which is well formed and is prose only because of the rule below
— a rule `_block_start` has no line above to apply. `_block_start`'s
monotonicity survives: lstripping can still only make a match appear, checked
across 120 spellings.

### `not lazy`, the half the tail parser exposed

**A link reference definition cannot interrupt a paragraph.** With the tail
decided, that was the whole of the remaining loss column: `> quoted\n[x]: /url`
is the quote's lazy continuation and renders as the literal text `[x]: /url`, and
this parser was putting it in the skeleton. **7228 lines** across the two sweep
runs, present at the parent, and the fix is one clause on the branch —
`_interrupts_a_paragraph` already says nothing about definitions, and this is
that fact spelled at the branch that needed to hear it.

It was taken during execution rather than deferred. The package's IN list says
"where it is not a definition, the line stays translatable", and under CommonMark
a line that would interrupt a paragraph is not one. It is not the same trade as
the ` {0,3}` decision above, which is about a line CommonMark *does* call a
definition: here CommonMark calls it prose, so no reference exists to be broken
by a translated label.

### Neither version number is bumped, re-derived a third time

Across the sweep's 112896 generated documents: **159840** segments identical in
text *and* kind, 11992 no longer emitted, 51120 newly emitted, and **0 with the
same text under a different kind**. That last number is the whole argument, for
the reason HANDOFF-018 and HANDOFF-021 both recorded — the memory key is the
content hash plus the context, so identical text keeps its key and changed text
misses by construction, and only the third class could answer with wording cut
for a different sentence. Bumping `SEGMENTATION_VERSION` would discard a novel's
whole accumulated wording to detect a change that cannot produce a wrong answer.
`STATE_VERSION` stays at 3: an old row is stale, not unreadable.

On the repository's own 63 Markdown documents the only one that moves is this
package's own fixture, from 7 segments to 15 — 3754 identical in text and kind
across the rest, and 0 with the same text under a different kind there either.

### Verification, and the axes the sweep varied

`tests/corpus/` cannot see any of this: it substitutes each segment's *source*
back into the skeleton, so a block that stopped being translated round-trips
perfectly. The evidence is a differential sweep against markdown-it-py over
**112896 generated documents**, comparing three answers per document — what the
parent segmented, what the new parser segments, and which lines markdown-it puts
inside an inline token, which is its answer to "is this prose". **0 regressions,
0 round-trip failures**, and the loss column down from 54444 lines to 1872.

The axes, written down beside the number because a sweep is blind to the axis it
does not vary: the tail after the colon in 28 spellings, definitions and near
misses alike; the label in four; the block above in twelve, including that
block's own line shapes; the block below in twelve, at every indent that changes
the answer; the container in nine, from the margin to five columns to a tab to a
quote to two depths of list item, with the block below carrying the container's
own prefix; and the terminator in three, LF, CRLF and one CRLF line inside an LF
document — the last because `docio.split_terminator` normalizes a uniform CRLF
file before `parse` sees it, so mixed is the shape that actually reaches the
branch.

**The 1872 that remain are one shape and it is not this rule.** A definition
directly under a table, with no blank line, is absorbed by markdown-it's GFM
table body as another *row*, where this parser's table loop stops at the first
line without a `|`. Both references call the line prose — CommonMark core, with
no table extension at all, reads the whole thing as one paragraph — so it is a
genuine loss, and it belongs to the question of where a table body ends. Pinned
at the parent identically. It is `handoff/00-inbox/HANDOFF-023`.

**The permissive column is two decisions and no defect.** At the margin it is
1404 lines: 864 the two-line definition above, 540 the line below one of those
table rows. In the container half it is 5060 and **identical at the parent**, so
none of it is this change.

### The mutation pass, and the one equivalent guard

**26 of 27 killed**, each by a test that names the property. The harness runs a
green baseline first and refuses to report without one, and it puts the mutated
parser into a **git worktree's own `src/`** rather than on `PYTHONPATH` — the
trap HANDOFF-021 recorded, where `tests/test_pipeline.py`'s own `sys.path.insert`
makes a PYTHONPATH run measure the wrong build.

Seven guards were **untested until it said so**, and each got a row that names
what it is for: a destination that closes a parenthesis it never opened while
balancing overall (`/u)r(l` — every earlier row left the final depth non-zero
too, so the guard was invisible); all three of the angle form's refusals; a
parenthesized title whose nested `(` closes at the line's end, since `(a (b) c)`
alone is refused by the junk after it rather than by the rule; the escape inside
a title; the run of spaces *between* a destination and its title after the angle
form; and the run *after* a title, which no other row had.

The pass is also where a mutation harness shows its limit, and the limit is the
one this repository keeps re-learning: **a mutant cannot find the guard nobody
wrote.** "Let a parenthesized title nest" was killed at the first run, while the
same line refusing a parenthesis inside a *quoted* title went unnoticed by every
mutant, because no test and no generated document held one. It is a mutant now.

The survivor is an **equivalent guard**, not a hole, and it is labelled at the
line: `j == 0` in the destination scanner cannot fire visibly, because `DEF_RE`'s
group 1 is greedy over `[ \t]*`, so a tail that stops the scan at 0 always has a
non-blank character at position 0 and the caller's separator test refuses the
line anyway.

### The adversarial pass, on the axes the sweep held constant

Every document in the sweep had exactly **one** candidate line, always ended with
a terminator, and never sat inside a container that swallows lines without
reaching the branch. A second pass varied all three: the sequence of three
candidate lines drawn from six kinds, two separators, six wrappers (a fence,
front matter, a table, a list item, after a fence, none) and four endings
including a CR-only terminator and no terminator at all. 10368 documents, **0
regressions and 0 round-trip failures**. Every one of the 3032 remaining losses
is front matter, which this project holds in the skeleton on purpose and which
CommonMark has no notion of.

### What is deferred, and where it is written

Two packages, both written before this one was deleted, because a deferral that
lives only in a deleted file's OUT list did not happen.

- **`HANDOFF-023`** — a table body's last row. The shape above, plus the
  measurement that only the definition spelling loses text today.
- **`HANDOFF-024`** — the label half. `[ ]: /url` and `[　]: /url` are paragraphs
  to CommonMark and definitions to `DEF_RE`, because the pattern asks only that
  the label be non-empty; and `[a\]b]: /url` is a definition to CommonMark and a
  paragraph here. HANDOFF-022 put labels out of scope and this measures what that
  costs.

## 2026-08-03 · A backtick in an info string is not a fence, and an indent is measured in columns

Closing HANDOFF-021, the two shapes HANDOFF-020's adversarial review measured and
deliberately left out of it. Neither is a regression from that package — both
fail identically at `c86363b` and at `e3399d8` — and each costs an **entire
document**, silently: `lx check` exits 0 and nothing says the text was never
translated.

**Defect A, a backtick fence whose info string carries a backtick.** CommonMark
forbids it — the run would otherwise be ambiguous with an inline code span — so
```` ```js` ```` is an ordinary paragraph. `FENCE_RE` read it as a fence, found
no closing marker, and with no list open the containment bound is vacuous, so the
run reached end of file: `parse()` returned **zero segments** for a
three-paragraph document. A **tilde** fence carries no such restriction and had
to keep working; that asymmetry is the longer risk of the repair, because a rule
applied to both spellings hands every tilde-fenced code block to the model.

The rule is spelled *in* the pattern — `` `{3,}[^`]*|~{3,}.* `` — rather than as
a helper beside it. *Lost:* a `_fence_open(line)` predicate, which reads better
and is wrong here: `checks.py` imports `FENCE_RE` to ask the same question about
a translated *target*, and a second spelling of one rule is the copy nobody
re-reads when a flavour detail changes. That is already the stated reason
`checks.py` imports these patterns instead of restating them.

### Which of the four `\s` indent classes narrowed, and which did not

HANDOFF-020 narrowed `FENCE_RE`, `QUOTE_RE` and `LIST_RE`. `HEADING_RE`,
`SETEXT_RE`, `HR_RE` and `DEF_RE` still spelled their indent `\s`, and all four
decide whether a paragraph stays open — which is what decides whether the
four-column line below becomes an indented code block. Two separate mistakes
lived in the one class, and the audit measured both against markdown-it-py:
**74, 34, 147 and 36 loss shapes** across the four patterns, 0 after.

*A tab is four columns, not one character.* `\s{0,3}` counted `\t# 標題` as a
one-character indent and read a heading; at column 0 any tab already reaches four
columns, so a character count is exact **only for spaces**. The leading run
becomes ` {0,3}`. *Lost:* `[ \t]{0,3}`, which is the same bug spelled more
carefully; and moving the bound into the loop the way `FENCE_RE`'s is, which
would *widen* the patterns to match a heading indented into a list item — more
skeleton, the wrong direction, and out of this package's scope.

*`\s` is not "spaces or tabs".* It reaches U+3000, U+00A0, a form feed, a
vertical tab, U+2028, U+2029, U+205F and U+2003. U+3000 is *the* zh-TW paragraph
indent, so `　# 標題`, `#　標題`, `===　` and `***　` were block starts where
CommonMark reads three paragraphs, and each one took the indented line below it
out of translation. Every run that decides a block start becomes `[ \t]`,
including the ones that measure no column: **whether the line is a marker at all
is a block-start decision**, and every block start closes a paragraph. That is
the reasoning HANDOFF-021 asked for and it cuts the other way once —

*`HEADING_RE`'s closing `#` run is the class left alone.* It decides where the
*segment* is cut and never whether a block starts, so no spelling of it can move
a line into the skeleton — the whole heading is a segment either way. Narrowing
it would only move `　#` out of the raw node and into the segment, which changes
what the model is asked to translate rather than fixing a defect.

### The neighbour a repair blinds, asked rather than assumed

A line that stops being a heading becomes a **paragraph segment whose source
begins with `#`**, where before its `　# ` sat in the skeleton and no translation
could reach it. So the structure stopped being safe by construction, and the
question is what replaced that. Two mechanisms, and which one applies depends on
where the U+3000 is — measured, because this is the failure HANDOFF-020's
adversarial pass found twice.

At **position 0** the run is the segment's place in the document's structure, so
`normalize.reseat_outer_blanks` re-imposes it from the source on every proposal;
a target that half-widths or drops the `　` gets it back, and the rendered line is
still a paragraph. That works here only because that function uses bare
`str.strip()`, which covers U+3000 and U+00A0 without enumerating them — a
property its docstring already claimed and this is the second caller to depend
on. **Between the hashes and the text** there is nothing to re-impose, so the
*check* has to catch it: `containment_problems` reports "the target opens a
heading; the source does not" at error severity, because `checks.py` reads
`mdparse`'s own patterns rather than a copy. Both halves are pinned by a test.

*`DEF_RE`'s leading run stays unbounded,* `[ \t]*` and not ` {0,3}`. The column is
already enforced one branch earlier: a line four columns past its container's
floor has been taken by the chunk branch before this pattern is reached, and
every indent that does reach it is inside a container where the definition is
legitimate. *Lost:* ` {0,3}`, which is CommonMark's letter and makes
`-    item\n\n     [x]: /url` a translatable segment — a link definition in front
of the model, whose reference breaks if the label comes back translated.

### A narrowed class dropped the carriage return, and the sweep's own metric hid it

`parse` splits on `"\n"` alone, so in a CRLF document **every line still carries
the CR of its own terminator** — the reason `emit_seg` moves a trailing CR run
into the skeleton. `\s*$` swallowed that CR by accident; `[ \t]*$` cannot. So
`Title\r\n=====\r\n` stopped being a setext heading and became a two-line
paragraph handed to the model with its underline inside it, and every thematic
break in such a document went the same way. `SETEXT_RE` and `HR_RE` end
`[ \t]*\r*$`; no other pattern here needs it, because every other one ends in
`.*` or `\s*` and absorbs the CR already.

One more claim in `checks.py` was re-derived and did not survive it. Above
`DEF_RE` it said no segment line can ever be a link reference definition, "by
construction — `mdparse` folds a source link definition into a raw node",
measured at 0 of 2154 corpus segment lines. **Both halves are wrong.** The
construction never held: `DEF_RE` is not one of the paragraph loop's stop
conditions, so `para\n[x]: /url` already put a definition line inside a paragraph
segment before any of this. And the number moved to 1 of 2222 — a line in this
package's own fixture, which `_block_start` lstrips into a match though the
parser reads it as a paragraph. The rule is free of false positives for a
different reason, now written at the line: **symmetry**, not construction. Both
sides of every comparison come through `_block_start`, so a source that answers
"link reference definition" licenses a target that answers the same.

*Lost:* `[ \t\r]*$`, which also matches ` \r \r`. The CRs are the terminator and
sit at the very end — a *run* rather than one, because `text\r\r\n` is what a
twice-applied LF-to-CRLF conversion produces — and a CR anywhere else on the line
is text in this project, where refusing the match keeps the text translatable.

Two things about this are worth more than the fix. **`docio.split_terminator`
normalizes a uniform CRLF document to LF before `parse` sees it**, so the
reachable cases are mixed terminators, CR-only documents, and every direct
`parse` caller — `tests/corpus/` among them. And **the sweep's regression metric
reported 0 while this sat in a diagnostic count nobody was watching**, with 789
green tests unable to see it. The number that matters is rarely the number being
reported.

### The differential reference must enable the table extension

`mdparse` implements GFM tables — it emits `cell` segments for them —
and `MarkdownIt("commonmark")` does not. A reference without the table rule reads
a table as one long paragraph, so **every question about the line after a table
gets the opposite answer.** The first sweep of this change used the table-less
preset and reported **84 regressions**, all one shape: a table, a tab-led setext
underline, an indented line. With `MarkdownIt("commonmark").enable("table")` the
same 84 documents show the new parser agreeing with the reference and the parent
accidentally disagreeing. All 84 evaporated.

This is a methodology decision, not a detail of this package: a differential
sweep compares two parsers, and if they do not implement the same block set the
comparison is measuring the difference in block sets. Every future sweep enables
the table rule, and any finding that depends on what follows a table is quoted
with the preset it came from.

The residual is recorded rather than fixed. After a table `mdparse` ends the
block at the first line without a `|`, where GFM keeps absorbing rows; the
narrowings make more lines reach that divergence, against the instances Defect
A's repair removes. Net **+76** on a diagnostic count of 1040, and the closer
rules took 49 off that figure on their own — the metric moved three times as the
package was repaired, which is the argument for watching it at all. It is the
permissive direction: text stays translatable, and it costs a visible translated
code block rather than a silent loss.

### The closing marker had all three of CommonMark's closer rules missing

The adversarial pass found this on the axis the sweep never varied — the
**sequence** of fence markers in one document — and it is the most expensive
thing in the package. Narrowing the *opener* left the closing search untouched,
and that search accepted as a closer any line whose start matched
`^\s*` plus three of the opener's character. CommonMark requires three things of
a closer and all three were absent: the same character, **at least as long as the
opener**, and **no info string**.

The length half predates this package and is the root cause. ```` ````markdown ````
wrapping a ```` ``` ```` example — *the* idiom for documenting Markdown — was
"closed" by the inner marker that is its own content, and the real closer was
then read as a fresh opener with nothing to close it, swallowing the rest of the
file. 2770 of 75810 generated documents, identical before and after.

The info-string half is what turned that from latent into a regression. Once
```` ```js` ```` stopped being an *opener*, the fence above it still closed on
that line, every later marker re-paired one step over, and the last one ran to
end of file. 796 of 75810, and the shape a person actually writes: a manual page
with two code samples where the first carries a stray backtick. **The change was
a large net win on the same corpus — 9304 documents fixed against 744 broken —
and the 744 were silent losses, which is the direction this package exists to
refuse.**

Two more fell out of repairing it, and both are worth the ink because neither was
reachable before. **A marker is a run of one character:** ``[`~]+`` reads
```` ```~~~ ```` as a six-character marker nothing can close, which is the same
run-to-end-of-file failure arriving through the repair — 1024 of 342528, caught
by re-running the harness that found the original rather than by the suite, which
stayed green. And **a marker at the margin cannot close a fence inside a list
item:** the container's-end rule now applies only where the fence sits *below*
the item's content column, `ind < floor`, which is the case HANDOFF-020 wrote it
for. Without that guard a bare closer outside the item was claimed by a fence
inside it, and the next marker ran away.

### A block that interrupts a paragraph closes a list item, lazily or not

The margin rule that clears `list_col` is guarded by `not lazy`, because a lazy
continuation at the margin is still inside the item. Narrowing `HR_RE` made
`***　` a lazy continuation instead of a break, so a blockquote below it no longer
cleared the item's content column, and a four-column fence marker two lines later
was read as *inside* an item CommonMark had closed — taking two lines of ordinary
Chinese prose into the skeleton, with no code anywhere in the document.

`not lazy` is now `not lazy or _interrupts_a_paragraph(line)`, and the helper
names exactly the blocks CommonMark lets interrupt a paragraph: a blockquote, a
fence, an ATX heading, a thematic break. *Lost:* adding a setext underline to
that list, which is precisely the block that **cannot** interrupt a paragraph;
and clearing unconditionally, which gives back the lazy-continuation case
HANDOFF-018 measured at 2778 markers of prose.

### Neither version number is bumped, re-derived rather than carried over

Measured over **40284 generated documents** across the axes below: **36757**
segments identical in text *and* kind, **17061** no longer emitted, **18472**
newly emitted, and **0 with the same text under a different kind**. That last
number is the whole argument, and it is the same one HANDOFF-020 turned on. The
memory key is the content hash plus the context: identical text keeps its key and
deserves its banked wording, changed text gets a new hash and misses by
construction, and there is no third case where a stale record could answer with
wording cut for a different sentence. Bumping `SEGMENTATION_VERSION` would
discard every entry in every project's memory — a novel's whole accumulated
wording — to detect a change that cannot produce a wrong answer. `STATE_VERSION`
likewise stays at 3: an old row is stale, not unreadable, and `store.py` refuses
only a newer one.

The large `segments_vanished` number is the arithmetic of merging, not evidence
of loss: a marker line that stops being a marker joins the paragraph above it, so
that short source string never recurs while its bytes stay inside a larger
segment. **The regression definition is therefore line coverage, not source
strings** — does the byte range the parent put in a segment still sit inside
*some* segment? Re-derived here rather than taken on trust: a source-string
multiset diff over the same sweep reports **16283** apparent losses, essentially
all of them that artifact, against 0 by coverage.

On the corpus the change moves **one** of the 31 pre-existing fixtures, and the
first draft of this entry said none — which was true until the closer rules were
repaired. `fences-and-unclosed.md` goes from 3 segments to 1, because it holds a
fence inside a longer fence on purpose and that body stops being translated.
markdown-it-py agrees with the 1. The 1572 segments of the 112k manual do not
move, and neither does anything else.

Independently, all 61 real Markdown documents in the repository were diffed and 6
moved, **0 of them losing a line the reference calls prose**: the two fixtures
this package adds, that corpus fixture, HANDOFF-021's own file, `walkthrough.md`,
and **this one**. Writing the defect down as a worked example is enough to
trigger it — under the parent parser the ```` ```js` ```` above opens a fence
that closes nowhere, and 38 segments of this entry and the one below it stop
being translatable. The repair demonstrating itself on the document that records
it is the clearest evidence in the package.

`examples/walkthrough.md` is the one that needed editing rather than measuring.
It wraps a ```` ```markdown ```` sample around a ```` ```python ```` example with
markers of equal length, so every CommonMark renderer — and now this parser —
closes the outer fence on the inner one. The document was wrong and is now
```` ````markdown ````; the repair is what made it visible.

### Verification, and the axes the sweep varied

`tests/corpus/` cannot see either defect — it substitutes each segment's *source*
back into the skeleton, so a block that stopped being translated round-trips
perfectly and a block handed to the model round-trips perfectly too. The evidence
is on the segment set and on the target side.

The sweep varied: 48 marker variants (fence character, run length and info-string
shape; heading level and closing run; setext spelling; three thematic breaks;
a definition with and without a destination); 18 leading-whitespace values; 6
inner and trailing values; 11 blocks above; 6 blocks below; three terminators;
and a trailing newline or none. **0 regressions, 0 round-trip failures.**

It held constant, and this is written down because a sweep is blind to the axis
it does not vary: no arm crossed the full marker detail against block-above and
block-below; U+2028, U+2029, U+205F, U+2003, the vertical tab and every mixture
appeared as *leading* whitespace only; `dnt` was empty throughout; no inline
markup appeared in any body text; nesting stopped at two containers; and every
document was 0–4 lines of the same canonical text.

**The claim, not the sweep, then went to five adversarial passes, and they found
four regressions the 0 could not see.** Every one lived on a held-constant axis,
and the two that matter name it exactly: the *sequence* of fence markers in one
document, which needs three markers and a reclassified line among them, and
*distance* — a document of 0–4 lines cannot hold a construct far enough below the
state it sets. Both are repaired above and pinned by rows that were each checked
by removing the guard again. The corrected parser was then re-measured on all
four adversarial harnesses — **449578 documents, 0 regressions** — and on the
original sweep, which stayed at 0.

That is the fourth consecutive package where a sweep reported zero and review
found something, and this time the sweep was the author's own. The lesson has
stopped being "run a wider sweep" and is now simply: **a number is evidence about
the axes it varied, and the adversarial pass is not optional.**

**Verified by mutation as well**, because a sweep only sees the code it was
pointed at. Twelve guards were removed one at a time on a copy of the tree, with
a green 795-passed baseline first and a timeout on each — a 2026-08-02 mutant
made `parse` loop for 56 minutes. **Eleven of twelve are killed**, and by tests
that name the property rather than by one catch-all: the two fixture segment
counts kill the fence rule and three of the four indent classes, the two CRLF
rows kill the `\r*` runs, and the marker fixture kills both trailing classes.

The twelfth is an **equivalent mutant**, not an untested guard, and the
difference matters. `DEF_RE`'s post-colon class has one consumer,
`not m.group(2).strip()`, and `str.strip()` removes exactly what `\s*` would have
eaten and two characters more — so wherever the group boundary falls the answer
is identical, and the branch emits the whole line raw regardless. Widening it
back changed nothing across 27648 documents varying the leading run, sixteen
spellings of the post-colon run, the destination, and the blocks above and below.
It stays narrowed for symmetry, with that written at the line so nobody hunts for
the test. Two classes the change deliberately did *not* touch were mutated too:
`HEADING_RE`'s closing hash run survives, as it should, and bounding `DEF_RE`'s
leading run to ` {0,3}` is **killed** by the new fixture — the deliberate
non-narrowing is pinned rather than merely argued.

**A verification trap worth more than this package.**
`tests/test_pipeline.py` begins with `sys.path.insert(0, ".../src")`, so a run
with `PYTHONPATH` pointed at another copy of the package still imports the parser
from `src/`. "The new tests pass against the parent build" was measured that way
and meant nothing. Checking that a rule turns the suite red is done by putting
the other parser into `src/` **in its own git worktree**.

### What is deferred, and why it was not half-fixed

> Closed by HANDOFF-022 on 2026-08-03, the same day; the entry at the top of this
> file carries the rule. Kept as written because what it measured, and why it
> refused to half-close, is the record.

`DEF_RE` still calls `[x]: /url not a title`, `[x]: /u rl` and `[x]:\x0c/url`
link reference definitions where CommonMark reads paragraphs, and each costs the
line plus the indented line under it. A character class rejecting the ASCII
control characters alone was written, measured and thrown away: it closes four of
six measured rows and leaves the two a person actually types, while the comment
at the line would read as though the rule were handled. **A rule that looks
handled and is not is worse than one written down.** Deciding it properly means
parsing a destination and an optional title, and a definition may span two lines
— `handoff/00-inbox/HANDOFF-022` carries the whole rule. The comment in
`mdparse.py` also corrects the claim that used to sit there, "every case it would
catch fails in the safe direction", which this audit measured false.

## 2026-08-03 · A quoted chunk is skeleton, and a fence's indentation is bounded by its container

Closing HANDOFF-020, the two containers HANDOFF-018 left behind. Both were
measured at `ade9fa9` rather than guessed at, and they fail in opposite
directions — which is the whole reason they were one package.

**Defect A, a blockquote's interior, was not measured at all.**
`> intro\n>\n>     def x():\n>         return 1` emitted `    def x():` and
`        return 1` as segments and asked the model to translate Python.
HANDOFF-019 had already stopped this damaging the *structure* — the four spaces
are re-imposed from the source now, so the block still renders as
`<blockquote><pre><code>` — which moved the failure from silent structural damage
to a translated code block that looks right. Easier to see, no less wrong, and
still billed for.

**Defect B, an indented fence run, lost translatable text outright.** `FENCE_RE`
was `^(\s*)(`{3,}|~{3,})`, so a four-column backtick run was claimed by the fence
branch, and with no closing fence that branch consumed to end of file.
`Para.\n\n    ```\n    code\nOrdinary prose.` yielded exactly one segment. Every
paragraph after the run became skeleton and was never translated. Pre-existing:
the fence branch predates HANDOFF-018, which deliberately placed its own branch
after it rather than change behaviour it had not measured.

### A fence's indentation is bounded by `code_floor`, not by three columns

CommonMark says "up to three spaces of indentation", and taking that literally —
`^[ \t]{0,3}` in the pattern — is wrong inside a container, where the bound is
three columns past the item's *content column*. `  - item\n\n    ```\n    code\n
    ```` is an ordinary fence indented four columns, and an absolute cap turns
its body into a translatable segment. So the bound is `ind < code_floor`, which
is `list_col + 3` inside an item and 3 at the margin: the number the parser
already computes, and CommonMark's rule exactly. *Lost:* bounding the pattern
itself, for the reason above; and testing the floor before the fence branch
instead of guarding it, which fixes the non-lazy case only — with a paragraph
open the code branch declines on `lazy` and the fence branch would take the line
back, losing the prose again.

**The closing search keeps its unbounded `\s*`, and that is a decision.** The two
are not the same question. Bounding the opening indent leaves more text
translatable; bounding the closing one runs every fence further and turns more of
the document into skeleton — the direction this parser refuses. It also cannot be
bounded correctly here, because a closing fence's indent is measured after its
container's prefix and this parser never strips one.

**An unclosed run is bounded by its container, and three spellings of that were
measured wrong first.** Nothing closes it, so its extent is a guess, and
CommonMark ends such a fence where its container ends.
`- item\n\n      ```\n    ```\n\ntext` swallowed `text` in 84 generated shapes
until the bound existed. *First spelling, lost:* bound by the fence's own indent
— a ` ``` ` one column in at the margin legitimately holds content at column 0,
and that handed 1158 markers of it to the model. *Second spelling, lost:* bound
by `list_col` whenever one is open — a fence at the margin under an open item is
not inside it, and CommonMark runs it to end of file; 1373 markers of
`- item\n```\n\ntext`. `list_col` cannot be read on its own here because the
margin rule declines to close an item on a `lazy` line, and a fence is not a lazy
continuation: it may interrupt a paragraph. *Third spelling, lost to adversarial
review:* `ind >= list_col` as a test of whether the fence is inside the item.
`list_col` is deliberately the item's whole prefix and is therefore **larger**
than CommonMark's content column — for the code floor that is conservative,
because too high only keeps text translatable, but read as containment it
inverts. `- [ ] item` puts `list_col` at 6 while the item's content starts at 2,
so a fence indented 2 was judged *outside* the item, took the margin's bound of
0, and swallowed the rest of the document: the exact failure this branch exists
to remove, in 400 shapes. The surviving bound is
`0 if list_col is None else min(list_col, ind)`. Taking the smaller of the two is
right in both directions, because a lower floor only ever runs the fence further,
so where the two disagree the conservative answer is the one that stops sooner.

### A blockquote gets a real content column, tracked the way `list_col` is

The quote branch measures `m.group(2)` — the content after the marker — against a
floor, with two pieces of interior state beside it. `quote_para` is the
lazy-continuation half and is the whole difference between the two shapes above:
`> intro\n>     def x():` is one paragraph, `> intro\n>\n>     def x():` is a code
block, and the bare `>` between them is all that separates them. `quote_list_col`
is the content column of a list item opened *inside* the quote, so
`> - item\n>\n>     second paragraph` stays prose while
`> - item\n>\n>       def deep():` does not. A quoted chunk moves into the
skeleton with its `>` marker, because the branch's usual split — marker raw,
content segment — is precisely what handed the model Python.

Four smaller rules came out of measurement rather than design.

1. **A code block opens no paragraph.** `quote_para = filled and not quoted_code`.
   Reading it as `filled` alone moved the *first* line of a chunk into the
   skeleton and left every line under it a segment — half a repair, which renders
   as code with its body translated and is worse than none.
2. **The interior state resets on a real blank line and on nothing else.** A
   blank line closes the blockquote, so both answers go back to their opening
   values; a line that is blank only to `str.strip()` is content, hence a lazy
   continuation, and `> a\n　\n>     x` is still one paragraph. A heading or a
   table does close a blockquote, and leaving the state alone across one only
   ever keeps text translatable.
3. **A quoted block at the marker's own column closes a quoted list item**, the
   document level's margin rule one container down, with `quote_para` standing in
   for `lazy`. Without it one `> - item` anywhere keeps every later quoted chunk
   in the document translatable.
4. **A tab stop is absolute in the line.** `_indent_columns` gained a `col`
   origin: `> \tdef x():` has a tab starting at column 2, which advances to 4 and
   is therefore *two* columns of indent. Measuring the content string on its own
   scores that tab as four and calls the line code — 2276 generated shapes, every
   one of them a paragraph to CommonMark. `_columns` needed the same origin and
   did not get it until adversarial review: a list marker inside a quote also
   starts at the quote's content column, so `> 1.\t` is six columns of marker and
   measuring the prefix alone scores four, dropping the floor by two. 42 shapes,
   every one a list prefix containing a tab, one line below the fix for the same
   bug.
5. **A quoted line another branch consumed still updates the state.** The list
   branch's continuation loop does not stop at a blockquote marker — the
   paragraph branch's copy does — so `-\titem\n   > intro\n>     chunk` reads the
   quoted line inside the item, and `quote_para` was still at its opening value
   when the chunk arrived. The opening value is the dangerous one, and it cost
   `chunk` in 40 shapes. Recorded where the line is consumed rather than by
   teaching the loop to stop: stopping would re-cut every list item that contains
   a blockquote, which is a segmentation change and a different package's
   decision.

*Lost:* a container stack — stripping the `>` prefixes off a run of quoted lines
and re-running the block dispatch on the content. It is the answer that gets
every case right, and it costs the segmentation: `mdparse` emits one segment per
quoted line, and changing that re-cuts every quoted segment in every project's
memory and changes their `kind` and `context` as well. That is a re-founding, not
a defect repair. *Also lost:* refusing to open a quoted chunk whenever any list
is open inside the quote, the conservative stand-in for `quote_list_col`. It
never turns prose into skeleton, but the real column is exactly as decidable —
`_columns` of the marker prefix, the same expression the document level uses —
and the sweep measures it.

**Three divergences from CommonMark are inherited rather than invented, and all
are conservative.** A quoted list item's floor is its whole prefix, checkbox
included; a bare `>` still does not close the paragraph for the *document*-level
code branch; and a chunk inside a *nested* quote stays translatable, because this
parser strips one `>` and measures the rest. All three keep text translatable,
which is the trade this file has recorded twice already.

### `\s` is not the indent class, in three patterns now

`FENCE_RE`, `QUOTE_RE` and `LIST_RE`'s leading run all narrowed to `[ \t]`.
Python's `\s` reaches U+3000, U+00A0, a form feed and U+2028, none of which
CommonMark counts — and U+3000 is *the* zh-TW paragraph indent, so this is the
third and fourth time the same character class has had to be narrowed here
(`_indent_columns` on 2026-08-02, `normalize` on HANDOFF-011's adversarial
review). `　``` ` was read as a fence and took its whole block into the skeleton;
`>　` was read as the marker plus its optional space, leaving empty content,
which closes the quote's paragraph and makes the line below it code; `　>` moved
the quote's content column, because `_columns` scores U+3000 as one column,
putting an ordinary paragraph four columns in; and `　- item` opened a list item
CommonMark reads as an ordinary paragraph. All four turn prose into skeleton.

`LIST_RE` is the one that needed a second argument for keeping it. The defect
adversarial review found through it — the list branch swallowing the quote line
below a phantom item, so the quote's state was never set — is *also* fixed by
rule 5 above, so narrowing the class is no longer load-bearing for any
regression. It is kept because it is separately right and measured so: with the
class widened back, the sweep still reports 0 regressions but 706 markers of code
stop being skeleton and 162 more reach the model. Only the *leading* run
narrows — the runs after the marker are not measured against a column, and
narrowing those would change what counts as a list marker rather than where its
content begins.

### Neither version number is bumped, re-derived rather than carried over

HANDOFF-018 settled that a change altering no surviving segment's text needs
neither, and this change **does** alter some — Defect B's recovered prose is a new
segment, and the narrowed classes merge a `　``` ` line into the paragraph above
it. So the rule was re-derived instead of assumed, and the measurement is the one
that matters rather than the one that is easy: across 158855 generated documents,
264023 segments are identical in text *and* kind, 40812 are no longer emitted,
45968 are newly emitted, and **0 have the same text under a different kind**. That
last number is the whole argument. The memory key is the content hash plus the
context: identical text keeps its key and deserves its banked wording, changed
text gets a new hash and misses by construction, and there is no third case where
a stale record could answer with wording cut for a different sentence. Bumping
`SEGMENTATION_VERSION` would discard every entry in every project's memory — a
novel's whole accumulated wording — to detect a change that cannot produce a
wrong answer. `STATE_VERSION` likewise stays at 3: an old row is stale, not
unreadable, and `store.py` refuses only a newer one.

On the corpus itself the change moves **nothing**: all 27 pre-existing fixtures,
including the 1572 segments of the 112k manual, segment identically before and
after. Every difference is inside the two fixtures this package added.

### Verification, and the axes the sweep varied

`tests/corpus/` cannot see either defect — it substitutes each segment's *source*
back into the skeleton, so a block that stopped being translated round-trips
perfectly and a block handed to the model round-trips perfectly too. The evidence
is therefore elsewhere.

A differential sweep against markdown-it-py over **158855 generated documents**,
comparing three answers per input: what the parent segmented, what the new parser
segments, and what CommonMark calls code. **0 regressions**, round-trip
byte-exact on every document, and code reaching the model down from 48933 markers
to 29602. The axes, written down beside the number because a sweep is blind to
the axis it does not vary: the block above the chunk *including that block's own
line shapes*; the chunk's indent over 0–16 columns plus tab spellings and three
non-indent whitespace characters; the chunk's first line in nine spellings; what
follows it in six; whether it sits inside a quote; the quote marker's own
spelling and the columns after it in twenty; the quote lines above it in
twenty-six, including list markers spelled with a tab; a lead whose quoted line
another branch consumes; an unclosed fence's opener and closer at every indent
below thirteen; and the terminator classes.

**1905 markers of code newly reach the model, in two families, and both are the
recorded cost of decisions above rather than new defects.** The larger family is
a chunk four columns in directly after a construct that leaves a paragraph open —
a blockquote whose last line was a bare `>`, or an empty link definition. That is
code to CommonMark, and this parser keeps it translatable because it has no
container stack (2026-08-02). The parent got the *fence-spelled* version right
only by accident: an unclosed fence ran to end of file and happened to coincide
with a code block running to end of file. Measured directly —
`> Quote\n>\n    def x():\n    trailing` is a segment in the parent too, so the
divergence now applies uniformly to both spellings, which is the honest state.
The smaller family is `min`'s own cost: an unclosed fence indented between one
column and the item's prefix width has its bound raised above the item's real
content column and stops sooner than CommonMark ends it.

A mutation pass over **37 guards, of which 33 turn the suite red**, each with the
test that caught it. The harness runs a green baseline first and refuses to report
without one: the first run reported all 26 killed by the same test in half a
second, because the tree it built was missing `examples/` and the mutants were
never reached. Four survivors, every one measured equivalent over the whole sweep. `filled` in the
quoted-code condition is measured equivalent over 158543 documents — every
spelling of a blank quote line in eight contexts plus the whole sweep, comparing
segment sources, kinds and skeleton bytes, zero differences — and is labelled at
the line. `j = i + 1` in the chunk loop is the hang guard 2026-08-02 already
records; it is equivalent while the two conditions agree, and saying so out loud
is its entire purpose.

### Adversarial review found four regressions the sweep could not, again

The sweep reported 0 over 83451 documents. Review found four, every one of them
prose becoming skeleton, every one on an axis that sweep held constant — the
third package running in which a large count read like proof and was not.

1. A list marker inside a quote measured from column 0 instead of the quote's
   content column, so a tab in the marker dropped the floor. The sweep spelled a
   quoted list only as `- `, `- [ ] ` and `1. `.
2. `LIST_RE`'s `\s*` making `　- item` a list, whose continuation loop then ate
   the quote line below it and left the quote's state unset. The sweep never
   produced a document where one branch consumed another's line.
3. The same eaten line where a list genuinely exists — `-\titem` — which the
   character-class narrowing does not reach, and which rule 5 above answers.
4. `ind >= list_col` as a containment test, inverting because `list_col`
   overshoots. The sweep's indents stopped at 8, so a checkbox item's
   `code_floor` of 10 was never reached and the branch was never entered.

The sweep now carries all four axes. Each finding was
re-derived here before being believed — run against the parent, the new parser
and markdown-it-py — and one of the review's own rows was wrong in the other
direction: `> -\titem` puts the item's content two columns past the quote, so
eight columns *is* code there, and the origin bug was the safe direction for that
spelling. It is pinned in both directions now.

### The second round changed the design rather than patching it

A session limit killed four of the five lenses in the first round. Re-run against
the repaired code, they found **eight more regressions**, all prose becoming
skeleton, and together they said something the individual fixes did not: the
quote branch was re-implementing block parsing one container down without a
container stack, and each new edge case was another way to be wrong about a
column nobody could compute.

Six of the eight lived in `quote_list_col`'s arithmetic — a tab in the marker
measured from the wrong origin, a tab *after* the marker measuring prefix and
content from different origins, a bare `> -` that `LIST_RE` does not match at
all, and a quoted list marker on a line some other branch had already eaten. So
the column is gone. **`quote_list` is a boolean**: while any list is open inside
the quote, nothing in it is code. That gives up
`> - item\n>\n>       def deep():` — a repair not made, costing a visible
translated code block — to remove a family of ways to lose text silently. The
seventh, `prose\n    >     def x():`, is answered by requiring the quote's own
marker at column 0: an indented `>` under an open paragraph is not a blockquote
but a lazy continuation, and this parser does not get to guess which.

The eighth is the one worth generalizing. **Four branches read a line carrying a
`>` marker and only one of them is the quote branch** — the list branch's
continuation loop stops at a list, heading or fence but not at a blockquote; the
table loop takes every consecutive line holding a `|`; and the fence branch takes
everything up to its closing marker. A quoted line any of them swallows never
reaches the quote branch, so the interior state keeps its opening value, and the
opening value is the one that turns the next quoted line into skeleton. Three of
the four had the hole. It is now one function, `_quote_state`, called from all
four, because the next branch to grow a swallowing loop will forget too.

**The unclosed-fence bound took six spellings, and the last three came from
review.** After `min(list_col, ind)` (above) came bounding the *search* and not
only the unclosed fallback — because once the indent gate moved which line is the
opener, the leftover markers re-paired across the container's end and swallowed
everything between them. That bound then cut off genuine closers, so the line
that ends the container is now tested as this fence's own closing marker before
the fence is called unclosed: otherwise the marker is re-read as a fresh opener,
and an opener at the margin with nothing to close it reaches end of file. And the
gate itself needed the same containment question the floor did — a marker below
the item's content column is judged at the document's floor, not the item's.
Each spelling was measured, each fixed the previous one's family, and each is
pinned by a row: 84, 1158, 1373, 400, 52, 77 and 33 shapes in turn.

The final shape of the bound is **two** estimates of the item's content column
pointing opposite ways, because `list_col` is the item's whole prefix and
CommonMark's content column can be anywhere between one past the marker and
there. Whether the fence is *inside* the item is asked against the lower bound,
since judging "outside" wrongly selects the margin's floor and swallows the
document; where to *stop* is the upper bound, since a floor below the real
content column runs past the item's end.

Two defects the round found are **pre-existing and out of scope**, recorded so
they are not rediscovered: a backtick fence whose info string contains a backtick
(`` ```js` ``) is not a fence in CommonMark and swallows the whole document here,
in the parent as well; and an indented chunk under a heading that itself follows
a U+3000 line is skeleton in both builds. Neither is a regression and both cost a
document silently.

**One claim in this repository's own documentation was wrong and is now
measured.** Invariant 3 said "79 of 2394 segments across the tracked
documentation" carry an interior line break followed by indentation. Measured
today: **149 of 1460**. It drifted with the documentation rather than with this
change — and the way it surfaced is worth keeping, because the entry you are
reading is what exposed it. Under the *parent* parser, `docs/decisions.md` with
this entry in it yields **9 segments**; under the repaired one, 451. The prose
describing Defect B contains an indented run of backticks, so the old parser
swallowed the decision log from that line to the end of the file.

Two `tests/corpus/` fixtures, `blockquote-indented-code.md` and
`indented-fence-run.md`, neither shape having existed in the corpus. Both hold
prose indented exactly as far as their code, which is what makes them worth
having and also why the test names the two halves rather than deriving them: a
mechanical filter that could separate them would be the parser. The test asserts
each named line is still in the file, so a fixture edit fails loudly instead of
quietly measuring nothing.

And a target-side test, because the round trip cannot distinguish "stopped being
translated" from "handed to the model": every segment is translated to something
other than itself and the file rendered, after which the skeleton must come out
byte for byte and the segments must be gone.

## 2026-08-03 · The blanks a segment opens and closes with belong to the source, not to the translator

Closing HANDOFF-019. `translate.accept` did `repair_placeholders(text).strip()`
before it normalized. HANDOFF-018 left that alone on a stated premise — once an
indented code block is skeleton, no segment starts with an indent and the strip
has nothing to damage — and the premise is **false**, which is why 019 exists as
a package rather than as a line in 018's entry. A list item's second paragraph is
`- item\n\n    text`, and `mdparse` keeps those four spaces at position 0 of the
segment on purpose: an indent that follows a newline inside the source cannot be
held by a raw node, because a raw node can only sit before or after a *whole*
segment (2026-07-28, "Where a line terminator lives"). Deleting them takes the
paragraph out of the item.

**Measured before it was designed.** 441 generated documents against
markdown-it-py, varying format (`mdparse`, `textparse`), container (paragraph at
the margin, list second and third paragraph in eight spellings, blockquote line,
blockquote inside a list, ATX and setext heading, table cell, link definition,
thematic break, empty list item, fence, front matter, a chunk carrying a text
CR), blank character (space, tab, U+3000, U+00A0, form feed, vertical tab,
U+2028, U+2029, newline, and five mixes), width (1–8 columns) and which end.
**76 of the 441 changed what the document is** rather than how it looks — sixteen
distinct container shapes, where HANDOFF-019's own table listed four.

Two facts from that sweep shaped the rest. `textparse` contributed **zero**
leading-run segments, because it lifts a first line's indent into the skeleton
(`emit_raw(indent)`) — the stronger answer, available to it and not to `mdparse`,
and the reason a model that helpfully adds a U+3000 paragraph indent to a zh-TW
target still has it removed. And no `tests/corpus/` fixture had such a segment at
all, so nothing in the suite could see any of it;
`tests/corpus/list-item-second-paragraph.md` is the file that closes that, built
on the lazy-continuation shape HANDOFF-018's sweep was blind to so that one file
serves the round trip as well.

**What `accept` may delete, and what it may not.** The rule is *re-imposition*,
not preservation: the target is stripped at both ends and then reseated in the
runs the source has (`normalize.reseat_outer_blanks`). So a model that dropped
the indent gets it back — invariant 5, fix rather than report — and a model that
padded a segment with no run of its own still loses the padding, which is the
reason the strip exists at all. The parallel is `doc["eol"]`: a fact about where
the text sits in the document, applied once, never carried inside a segment where
the model and the reviewer would both have to reproduce something invisible.

*Lost:* preserving only a run the target already has. It is the weaker half of
the same idea and it loses the likeliest case — a model asked to translate an
indented paragraph answers with a sentence, not with a sentence wearing four
spaces. *Also lost:* moving the indent into the skeleton, which HANDOFF-019 put
out of scope and 2026-07-28 already settled; and narrowing the preserved run to
`" \t"`, which would miss U+3000 and U+00A0 — `str.strip()` deletes both, and the
set restored has to be the set deleted or the rule is a different rule at the
edges. `mdparse._indent_columns` counts only space and tab because CommonMark
does; that is a different question, and conflating the two is the trap here.

**The trailing side is not redundant, and the measurement that says so is on an
axis the first sweep held constant.** HANDOFF-019 suspected the trailing half was
already covered by `normalize`, which keeps a Markdown hard break and drops a run
that means nothing (2026-07-29). Over those 441 documents it looked that way:
eleven segments carried a trailing run and not one was structural. Every one of
them, though, was a segment whose *following* line was inside it — a paragraph
keeps its continuations. `mdparse` emits **one segment per blockquote line**, so
`> first  \n> second` puts the two spaces of a hard break at the *end* of a
segment with the newline that gives them meaning outside it, in the skeleton. The
unconditional `rstrip` deleted the `<br>`. Twenty hand-built cases on that axis
found six structural shapes the first sweep could not have contained. The
trailing half is therefore the same rule as the leading half, for a different
reason, and both are now reseated.

**The order is load-bearing, and it is the second thing the trailing side
decided.** `reseat_outer_blanks` runs **after** `normalize`, not before.
`collapse_space` ends in `[ \t]+\Z` and is in zh-TW's default op list, so a
trailing run handed to `normalize` is deleted again and the fix would be inert
for the project's primary language. Running `normalize` on the stripped sentence
is also exactly what it received before this change, so nothing about the ops
moved underneath it and HANDOFF-011's line-start protection is untouched — that
one is about a run *inside* the body, and the two are complementary rather than
overlapping.

**The `do_apply` asymmetry closed for whitespace and stayed open for
acceptance.** `cli.do_apply` never stripped, so the same document rendered
differently depending on which of the three equal sources produced its target,
and a reviewer retyping a paragraph in the workbench's textarea does not reliably
reproduce the four spaces that keep it inside its list item. It now shares
`reseat_outer_blanks` and nothing else. What it deliberately still does *not*
share is refusal: a placeholder set that does not match is reported at `lx check`
rather than rejected at the door, which is the whole of the 2026-07-29 decision,
and both halves are now pinned by tests so closing the second one needs a
decision rather than an edit.

**Adversarial review found two regressions this package introduced, and both are
in it.** Four independent lenses — callers, degenerate ends, validators, and the
novel use case with real zh-TW prose instead of identity translations — then
eight verifiers whose job was to refute what the lenses claimed.

1. **A reseated lead blinded three of the seven block-start rules.** Three
   patterns in `checks.py` cap the indent they will match, because CommonMark
   spells them `\s{0,3}`: a heading, a thematic break and a setext underline. A
   list item's second paragraph *sits* four columns in, so from the moment its
   target carried the source's four spaces, `'    # 標題'` matched nothing.
   `lx check` printed `0 error(s)` and exited 0 while markdown-it-py rendered an
   `<h1>` where the source had a `<p>` — 36 of 172 (marker, indent, hazard)
   combinations, silent. That is invariant 10's exit code claiming something
   untrue, arriving because the fix moved the input to a validator without
   moving the validator. `_block_start` now lstrips the line, which is safe in
   the must-not-fire direction because every pattern in the table is anchored
   `^\s*` or `^\s{0,3}`: removing leading blanks can only make a match appear,
   and both sides of every comparison come through the same function.
2. **The `do_apply` asymmetry was closed too far.** Two U+3000 at the head of a
   paragraph is standard Traditional Chinese typography; an English source has no
   leading run for it to be reseated from, so the strict form deleted it from
   every paragraph a reviewer typed — and after it, no surface anywhere in the
   pipeline could produce an indented Chinese paragraph. Neither reporting nor
   refusing, which is the only thing 2026-07-29 permits. `do_apply` now passes
   `keep_added_indent=True`: where the source has **no** run, the target keeps its
   own. It is the leading end only, because a trailing run is invisible in both
   hosts unless a line follows it in the skeleton, where it is structure the
   source did not have and `lx check`'s to report.

*The recorded cost of 2:* where the source **does** have a lead, the source still
wins, so a person writing eight spaces into a four-space segment — an indented
code block inside the list item — gets four. That run is what keeps the paragraph
inside the item at all, and a paragraph's translation turning into a code block
is the larger of the two failures.

**Three further claims were refuted, and one of them reversed a suspicion worth
recording.** The trailing run is re-imposed *verbatim* while an interior hard
break is canonicalized to two spaces (2026-07-29), which reads as one document
answering the same question twice. It is not: the two runs have different
provenance. The interior run is the translator's and the canonicalization is
about their output; the segment-final run is the **source's**, and reproducing it
is invariant 2a's direction. Measured against markdown-it-py over one space,
two, three, five and a tab: reseating verbatim matches the source's rendering 5
of 5, and canonicalizing would have *deleted* the single space and the tab —
bytes the source had, that `_line_end_blanks` drops because it is judging a
translator's line ending rather than restoring a source's. Also refuted: that
banking the lead inside the approved wording harms the memory — the key already
carries the indent, since the indent is part of the source, so an entry never
crosses containers and reuse reseats it anyway. Keying the memory on the stripped
source would make one wording one entry across containers and was **not taken**:
it rewrites every existing record for bytes nothing reads, and it is a
memory-format decision rather than this package's.

**The mutation pass found three things review had not.** Removing each guard on a
copy of the tree, seventeen mutants: six survived the first run. Two were *mutually
redundant strips* — `do_apply` had grown one of its own and `reseat_outer_blanks`
already had one — measured equivalent over 327600 combinations (every subset of
the zh-TW ops × 26 body shapes × 15 leading runs × 15 trailing runs × 7 source
shapes, zero differences), so `do_apply`'s was deleted and one place owns the
rule. Three were untested rather than redundant: the blank-target guard, which
only `lx apply` can reach and which is what keeps a cleared segment rendering the
untranslated marker instead of four truthy spaces; the blank-source guard, where
`lead` and `trail` would be the *same run* and the answer is wrong rather than
merely absent; and the trailing end's character class, which had quietly narrowed
to ASCII with nothing to say so. One is equivalent by construction and is left in
the harness list, labelled, so the next reader does not re-derive it. The four
mutants added for the two repairs above — the lstrip removed, `keep_added_indent`
dropped, widened to override a source's own lead, and widened to the trailing
end — are all killed.

**`textparse` gains nothing from the leading half, and that is the right
outcome.** Across 32 parses — eight document shapes × four `paragraph_mode`
values — plain text produces **zero** segments with a leading run, because it
lifts a first line's indent into the skeleton. The lead half is inert there by
construction rather than by accident: where an indent can be taken out of the
segment entirely, taking it out is the stronger answer, and `mdparse` cannot,
because a raw node has nowhere to sit. The consequence is that for a `.txt`
novel — the format novels arrive in — `lx apply` is the *only* path that can put
a paragraph indent in, which is exactly what `keep_added_indent` exists for.

## 2026-08-02 · An indented code block is skeleton, and the rule that decides it is state rather than a column

Closing HANDOFF-018. `mdparse` had no branch for an indented code block, so a
chunk indented four spaces fell through to the paragraph branch. Two spellings of
one construct were treated oppositely — a fenced block is skeleton, an indented
one was a segment — and the model was asked to translate Python.

**The part that reached disk quietly.** The four spaces that *make* it code sat
at position 0 of the segment, where `translate.accept` does
`repair_placeholders(text).strip()`. Reuse comes through `accept` too, so a
target a person applied with its indent intact was banked *with* the indent and
handed back *without* it. Reproduced end to end: `lx apply` → `lx commit` →
delete `.lx/state.db` → `lx extract` reported `reused=2 rejected=0` and the
stored target had lost its four spaces. The key is the source hash and is
untouched by that, and `tm_records` only rewrites when the stored target differs
from the banked one, so the two never converged. `lx check` exited 0 the whole
way. Confirmed against a real CommonMark render (markdown-it-py):
`'    def x():\n        return 1'` is `<pre><code>`, and the same text with its
leading indent removed is a `<p>` with the body reflowed. A document changed its
rendered structure merely by having its state rebuilt, and nothing in the exit
code said so — which is invariant 10's warning arriving from a new direction.

**The rule is CommonMark's, and the permissive direction is worse than the
defect.** A four-space test is the obvious implementation and it is wrong twice
over. An indented chunk is code only where a paragraph could not be continued
lazily, and only past the content column of any list item it sits inside; getting
either wrong the permissive way turns ordinary prose into skeleton and stops
translating it altogether, silently, where the defect being repaired at least
left the text visible to a reviewer. So the conservative direction is the one
this parser takes wherever it cannot know.

**Lazy continuation had to be state, not position.** The first implementation
claimed the rule held "by position": the paragraph branch consumes its own
indented continuations, so `text\n    more text` never offers that second line to
the block dispatch. That claim is false, and a differential sweep against
markdown-it-py found it — 606 of 25344 generated documents turned prose into
skeleton. The paragraph branch stops at anything that *looks* like a block start
**at any indent**, so `text\n    - like a list` hands its second line straight to
the dispatch while CommonMark is still inside one paragraph; and a blockquote is
emitted one line at a time, so `> quoted\n    lazy line` does the same. `mdparse`
now carries `para_open`, set by the three branches that leave a paragraph open —
quote, list, paragraph — and cleared by every other block start by saying
nothing. Re-measured at the final sweep size, 37224 documents: 2778 markers of
prose would become skeleton without it, and 0 do with it.

**A sweep is blind to the axis it does not vary, and this one had four.** The
37224-shape sweep varied the block *above* the chunk, the indent, the chunk body
and the trailing block — and never varied the *shape of that block's own lines*.
Adversarial review found four regressions living in that gap, every one of them
ordinary hand-written Markdown, every one silent: `lx check` green, `render`
reporting `missing=0`, and English prose reaching a zh-TW document.

1. **A list item whose text wraps to the left margin.** The continuation line is
   at column 0, so it looked like a block that closes the item — and it is not,
   it is still inside it. `- item wraps and\ncontinues here\n\n    second para`
   measured the second paragraph against four columns instead of six.
2. **A line that is blank to Python and not to CommonMark.** `str.strip()`
   answers True for U+3000, U+00A0, U+2028, a form feed and five more; CommonMark's
   blank line holds nothing but spaces and tabs. U+3000 is the zh-TW paragraph
   indent and U+00A0 is what a paste from EPUB leaves, so such a line is ordinary
   material here rather than a curiosity. The first fix kept a paragraph open
   across one; the widened sweep refuted that too, in 1482 documents — such a
   line is *content*, so it **opens** a paragraph even after a heading closed
   everything before it.
3. **`=====` or `--` with nothing above it.** It underlines nothing, so
   CommonMark reads it as paragraph text and the indented line below as its lazy
   continuation. A thematic break, and a real underline, both still close.
4. **A link definition with no destination.** `[x]:` is a paragraph. Only the
   empty destination is answered; deciding whether a non-empty one is well-formed
   is a parser this file does not have, and every case that leaves uncaught fails
   in the safe direction.

A fifth came from sweeping the *terminator* classes separately, which the
generated sweep also did not vary: **a CR-only document is one line to
`str.split("\n")**, because this parser treats a lone CR as text rather than as
a terminator (2026-07-28, below). `'    def x():\rprose\r'` therefore put the
entire file into the skeleton. A CR at the *end* of a line is exempt — that is
the CRLF a mixed-terminator document arrived with, and those lines are still
code.

The lesson is the one `docs/conventions/delegated-work.md` §6 already records
about measurements: a large count across a shape set missing one dimension reads
like proof and is not. The sweep is now 57024 documents with those axes in it,
and reports 0.

**Two divergences from CommonMark are deliberate, and both are conservative.**

*A bare `>` does not close the paragraph for this purpose.* CommonMark says it
does, so `> q\n>\n    y` is a code block. Reading it that way was implemented,
measured, and reverted: it fixed 285 generated shapes and turned prose into
skeleton in 57, because `> q\n>\n    > x` is still inside the quote where
`> q\n>\n    y` is not. This parser has no container stack and cannot measure an
indent against a blockquote's content column, so it refuses to open a code block
after any quote line at all.

*A list item's floor is its whole prefix, checkbox included.* CommonMark's
content column stops after the marker; `_columns(prefix)` is never smaller than
that, so the threshold it produces is never too low, and too low is the only
direction that costs a translation.

Both leave text translatable that CommonMark would call code. That is the status
quo, not a regression, and it is the trade this whole entry is about.

**Tabs.** One tab is four columns, not one character. `_columns` expands to
CommonMark's four-column stops, and `_indent_columns` counts only a space and a
tab: `str.lstrip()` would also eat U+3000, U+00A0 and a form feed, and U+3000 is
*the* zh-TW paragraph indent — counting it would turn ordinary translated prose
into a code block. This is the same character class HANDOFF-011's adversarial
review had to add to `normalize`, arriving at a second op for the same reason.

**Neither version number is bumped, and that is a decision.** *Lost:* bumping
`SEGMENTATION_VERSION`, which the letter of its docstring invites — this change
does alter one segment's text, where a chunk sat directly above unindented prose
and used to be swallowed into one paragraph. What that field buys is that a stale
record stops answering instead of answering with wording cut for a different
sentence, and the key is the *content hash*: a segment whose text changed gets a
new hash and misses the memory by construction, while every surviving segment
keeps its text and deserves its banked wording. Bumping would discard every entry
in every project's memory — a novel's whole accumulated wording — to detect a
change that cannot produce a wrong answer. `STATE_VERSION` likewise stays at 3:
an old document row is stale, not unreadable, and `store.py` refuses only a
*newer* one.

Measured rather than argued, on a project extracted by the old build and then
opened by the new one: re-extract reports `reused=2 rejected=0` and both
surviving targets carry over. Be exact about the byte claim, because the first
draft of this entry was not: the rendered bytes are identical **when the code
segment's target equalled its source**, which is the undamaged case. A document
whose code block was genuinely mistranslated renders differently afterwards —
the code reverts to its source bytes. That is the repair, not a regression, and
it is the one thing a re-extract is allowed to change.

The record banked for the old code segment stays in the log, and it is **not**
unreachable — the first draft of this entry claimed it was, and adversarial
review refuted it in one shape. A four-column chunk under a `- ` item is
deliberately kept translatable, so `- item\n\n    def indented():…` still emits a
segment whose source is byte-identical to the old code segment's, and the memory
answers it. That is a content-keyed memory doing exactly its job — same
characters, same wording — and it is why the version decision above survives the
correction rather than being undone by it.

**Verification.** `tests/corpus/` cannot see any of this — it substitutes each
segment's *source* back into the skeleton, so a block that stopped being
translated round-trips perfectly. So the evidence is elsewhere: the corpus
segmentation diffed before and after (2 of 1680 segments changed, both the code
blocks); the differential sweep above, 57024 documents; a hard-coded segment
count per fixture including 1572 for the 112k manual; and a mutation sweep over
30 guards of which 29 turn the suite red. The single survivor is
`lines[j].strip()` inside the chunk loop, established redundant — a blank line
either indents past the floor, in which case it joins a raw node that `emit_raw`
concatenates anyway, or it does not and the column test stops the chunk — and
kept with that reasoning written at the line.

**The chunk loop starts at `i + 1`, and the mutation harness is what found out
why.** It hung for 56 minutes on a mutant that removed the carriage-return guard
from the *opening* condition while leaving it on the continuation: the loop then
exited with `j == i`, `i = j` advanced nothing, and `parse` spun forever. The
real code never reaches that state, because the two conditions agree on line `i`
— but the agreement is invisible and load-bearing, and the failure it guards
against is an unresponsive parser on a user's document rather than a wrong
answer. Starting at `i + 1` says out loud that line `i` has already qualified,
and makes the loop advance by construction. Two further guards became untested
the moment it changed, because the opening line's copy of each test had been
standing in for the continuation's; both now have a row of their own. This is the
mutation-survivor rule from `docs/conventions/delegated-work.md` §6 arriving from
a new direction — a *timeout*, not a red suite, was the signal, so the harness
grew one.

**What this did not fix, measured while closing it.** HANDOFF-018 left
`translate.accept`'s `.strip()` alone on the stated grounds that "once the block
is skeleton, no segment starts with an indent by construction". That premise is
false. A list item's second paragraph is `- item\n\n    text`, and `mdparse` puts
those four spaces at the front of the segment: stripped, the paragraph leaves the
list item — `<li><p>item</p><p>text</p></li>` becomes `<li>item</li>` plus a
sibling `<p>`. The ordered and nested spellings do the same, and `>     x` inside
a blockquote is the original defect one container down. `lx apply` does not
strip, so the same document behaves differently depending on whether a model or a
person produced the target. No corpus fixture has such a segment at all. Handed
to HANDOFF-019 rather than absorbed here.

## 2026-08-02 · A continuation indent survives normalization verbatim, and the same op scoped out twice was guilty twice

Closing HANDOFF-011, the other end of the line from HANDOFF-010. `collapse_space`
rewrote every run of blanks that *begins* a line down to one space — the
indentation invariant 3 records as living inside the segment, because a raw node
can only sit before or after a whole one. Measured across the corpus with
`DEFAULT_CONFIG` for zh-TW: eleven segments in six files, every indent whatever
its width arriving as a single space.

**It stayed invisible because the round-trip fixtures cannot see it.**
`tests/corpus/` and `tests/corpus-text/` substitute each segment's *source* back
into the skeleton and never call `normalize`, deliberately, so that a failure
there is a skeleton defect and not a masking one. Everything this op does to a
*target* was therefore unmeasured. The new tests are the target side of the same
files.

**Verbatim, not canonicalized.** *Lost:* the answer HANDOFF-010 gave one line
earlier — two spaces and five before a line break mean the same `<br>`, so the
surplus is editor noise and goes. That reasoning does not transfer, and the
render says why: indent widths are not interchangeable. Four spaces are an
indented code block where one space is a paragraph, and inside a list item the
indent is the whole of what keeps a continuation inside the item.

**Confirmed against a real CommonMark render** — markdown-it-py, as measurement
tooling rather than a dependency — because the package asked for the severity to
be established rather than assumed, and it is not uniform. For a prose
continuation the damage is cosmetic: lazy continuation makes `- item\n    x` and
`- item\n x` the same paragraph. For a continuation that could open a block it is
structural: `- outer\n    - nested` renders as a nested list and `- outer\n -
nested` as two siblings, and the same split happens for `#`, `>` and `1.`. For an
indented code block it is total — `<pre><code>` becomes `<p>`, and the code is
reflowed prose.

**`punct` was scoped OUT on a true statement that does not imply what it was read
to imply, for the second package running.** The OUT list says both of `punct`'s
whitespace rules need fullwidth punctuation *adjacent* to the run, and they do.
But `[ \t]+(?=[FULLWIDTH])` needs it adjacent on the **right**, so a continuation
line that *opens* on 「, （, —— or …… matches — and `punct` runs first and deletes
the run outright, where `collapse_space` only shortens it. That is not a corner:
a zh-TW verse block, epigraph or quoted letter opens its lines on exactly those
characters. Measured 2026-08-02. The package also told its executor to verify
rather than trust the claim, which is the only reason this was caught; HANDOFF-010
recorded the identical mis-scoping of the identical op at the other end of the
line. The rule that follows: **an OUT clause resting on a factual claim is a
scoping hypothesis, not a boundary.**

**The other rule of `punct` is deliberately left unguarded.** Its lookbehind
already demands fullwidth punctuation immediately before the run, and the
character before a run that begins a line is a newline, so it cannot reach one.
A guard there would be one no test could ever turn red — and the mutation pass
confirms it: adding it is the one mutant of eleven that survives.

**Position 0 of a segment is indentation too, and the case is measured rather
than invented.** The package expected the opposite, having measured that no
corpus segment starts with a run because `mdparse` holds the marker and the first
line's indent in the skeleton. That is true of a list item and false of the
corpus: `mdparse` has no indented-code-block branch at all, so
`tests/corpus/indented-code-block.md` arrives as an ordinary paragraph whose
source *starts* with the four spaces that make it code, and again with a tab.
`translate.accept` strips a model's leading whitespace before normalize is
reached, but `cli.do_apply` — a person's or an agent's own words — does not, and
neither does the workbench behind it.

*Left standing, and scheduled as HANDOFF-018:* that an indented code block is a
translatable segment at all. The model is asked to translate Python, and
`accept`'s strip removes the opening indent whatever `normalize` does. This
package guards the ops; it does not fix the parser, and the two are separable.

The reviewers measured the consequence further than the package had, and the
extra distance is the part worth recording: because reuse also comes through
`accept`, a target a *person* applied with its indent intact is banked with the
indent and handed back without it. Reproduced end to end — `lx apply`, `lx
commit`, drop `state.db`, `lx extract` — the reused target renders at column 0
where the applied one rendered as a code block, the memory key is untouched so it
never self-heals, and `lx check` exits 0 with no errors and no warnings. One
wording normalizes two ways depending on which path delivered it, and a document
can change its rendered structure merely by having its state rebuilt.

**The guard's class is the whole design, and it took two measurements to settle
at `[\S\r]`.** The first version was `(?<=[^\n])`, which is satisfied one
character *into* the indent: the engine starts the match on the second space and
a four-space indent came out as two. That bought `[^ \t\n]` — and adversarial
review of *that* found the second half. The run these ops match is `[ \t]+`; the
indent a translator writes need not be. A zh-TW paragraph indent is U+3000, which
`textparse._blank` and `_indented` both already know, and a mixed `　` + spaces
indent — what a paste from a PDF, or a model padding a Chinese line to its
source's column, produces — kept its U+3000 and lost every ASCII space behind it.
`\S` covers U+3000, U+00A0, the form feed `textparse` separates chapters on, and
U+2028/U+2029.

*The price, stated rather than left to be discovered:* `'a　  b'` — an ideographic
space used *between words*, followed by ASCII blanks — no longer collapses. One
character of context cannot distinguish that from an indent, and the two failures
are not the same size: a surplus space is invisible where a deleted indent
reflows a code block into prose. Pinned by a test so the trade is deliberate.

**A lone CR does not start a line**, so `\r` is added back to the class. That is
`docio.split_terminator`'s classification — a lone CR is a character in a
sentence — and the same one `_LINE_END_BLANKS_RE` makes with its `\r?\n`
lookahead. A CRLF continuation is covered either way, because the character
before the indent is then the `\n`.

*Residual, measured and left:* `_line_end_blanks`, the handler, does not share
that classification — its `at_line_start` test is `m.string[m.start() - 1] in
"\r\n"` — so `'甲\r  \r\n乙'` loses a hard break the new guard would call
interior. Pre-existing, HANDOFF-010's, and a mixed-terminator document is the
recorded exception either way (2026-07-28). Widening it here would be revisiting
a settled behaviour to fix a case that has no reported instance; it is written
down instead.

**The trailing `[ \t]+\Z` strip stays unguarded.** A run that begins the
segment's last line with nothing after it indents nothing, which is the same
judgement `_line_end_blanks` makes about a line that is only blanks. Guarding it
would make `'item\n  '` keep two invisible trailing spaces.

**The two passes compose rather than overlap.** A run that both begins and ends a
line is a blanks-only line, and `_line_end_blanks` empties it before the interior
collapse runs; what reaches the guarded pass begins a line and has something
after it that the indent is indenting.

**`pangu` was checked again and needed nothing again.** Both rules are zero-width
assertions between a CJK character and a Latin one; neither can match across a
blank, from either direction.

**Verified by mutation, and then attacked.** Twelve mutants — the two new guards,
the guard's character class in three directions, the guard added where it is
deliberately absent, and the four HANDOFF-010 guards it composes with. Eleven
fail the suite. The survivor is the redundant guard named above, and it is
commented as such at the point of output.

The mutation pass also found a real gap in an older guard: `[ \t]+\Z` could be
changed to `[ \t]+$` — which in Python also matches before a final newline,
deleting a hard break — with the whole suite still green, because no test fed
normalize a target ending in `'  \n'`. `lx apply` does not strip, so that target
is reachable. Two rows close it.

What mutation could not find, four independent reviewers attacking the finished
change did. Between them they swept some 600,000 synthesized strings plus every
segment of both corpora against the pre-change code: **zero behaviour differences
that are not a run of blanks at a line start** — nothing that legitimately
collapsed has stopped collapsing — and all eleven new parametrized rows fail on
the parent commit, so none is vacuous. The U+3000 half of the class above is what
they found that mutation structurally could not: a mutant can only weaken a guard
that exists, and this was a case the guard never covered.

## 2026-08-02 · A project's voice is a sheet of prose, and a character's half of it rides only where their name does

Closing HANDOFF-015, which is option C of the triaged HANDOFF-208 and decision D6
of the 2026-07-29 re-founding. HANDOFF-013 made the register real and its brief
ends *"a character keeps their own diction and level of formality wherever they
speak"* — which nothing in the project could act on, because which character says
您 and which says 你 is a fact about one book. `config/style.txt` is where a
project says it.

**The format is prose with named blocks, and nothing inside a block is parsed.**
Lines before the first `[name]` header are the preamble; a `[Eleanor Vance,
Eleanor]` header opens a block that answers to any of its names; `#` at the start
of a line is a note to the person reading the book and never leaves the file. The
header is the whole of the structure.

*Lost:* a flat file passed through verbatim, with no structure at all. It is the
smallest possible implementation and it was the recommendation until the option
set turned out to be incomplete. What it cannot do is send a character's notes
only where they are relevant, so a novel with forty named characters pays forty
notes on all eighty requests of the book, and the model reads thirty-eight
irrelevant ones every time.

*Lost, and this is the one worth recording carefully:* a **fielded** format —
`address:`, `register:`, `notes:` under each name, parsed into records and
rendered back into prose. It was rejected before the block form was found, on the
argument that its structure bought nothing: the only thing per-character data
enables in a UI is showing a character's rules beside the paragraph where they
speak, and *who is speaking* is judgement, which invariant 4 keeps out of code
and which this package puts out of scope. That argument was right about fields
and wrong about structure. The question a per-batch selection actually asks is
not "who is speaking" but "**does this text contain this name**" — which is
mechanically decidable, and which `translate._glossary_hints` has been answering
since before any of this. The glossary is already a per-entity, human-authored,
machine-selected store; a character's address form is per-entity data keyed on a
name. So the block form keeps the selection and drops the fields, and with the
fields goes the part that would have put an opinion about voice inside
`config.py`.

*Also lost:* extending `config/glossary.csv` with a voice column. `load_glossary`
splits on `,` with no quoting, so the first sentence containing a comma is
unreadable — and it would put an unenforceable judgement in the table
`checks.py` enforces, which is the confusion invariant 4 exists to prevent.
*Also lost:* holding the sheet in SQLite. It is authored, not derived; authored
things in this project live in `config/` as hand-edited files, diffable and
mergeable, and the store would take both.

**The preamble goes in the system prompt; matched blocks go in the user
message.** This deviates from HANDOFF-015's IN list, which said the sheet is
injected into the system prompt after the language brief, and the deviation is
the entry's reason for existing. The split is not stylistic: static-per-document
content belongs where the register brief is, and per-batch content belongs where
`Required terminology for this batch` already is. Three things follow, and each
is what decided it.

- `_system_prompt` is assembled **once** per run and closed over by `run_batch`
  and `retry_one` alike. Keeping the batch-varying half out of it means that
  string is byte-identical for all eighty requests of a book, so a local
  runtime's prefix cache has something to reuse — the default provider is
  Ollama on `localhost:11434`, and prefix reuse across requests is the whole
  reason the assembly was hoisted in the first place. `translate.py` is not in a
  position to measure that and does not claim a number; the property it
  guarantees is that the string does not change, which
  `test_the_system_prompt_is_identical_across_every_batch` asserts.
- `retry_one` needs no second assembly, and its payload of one selects its own
  notes: a retried segment is briefed on the characters *it* names, narrower
  than the batch that failed, for free.
- The user message is where the enforced half already lives, so voice takes the
  outer position and terminology stays closest to the payload — `checks.py`
  validates terminology afterwards and it is the half that must not be pushed
  away from the text it governs.

*Lost:* putting matched blocks in the system prompt too and rebuilding it per
batch. It complies with the package's IN line literally and gives the notes
system-message weight, which is the one real argument for it. Against: it undoes
the hoist HANDOFF-014 had just stabilized, doubles the assembly onto the retry
path, and forfeits the identical-prefix property. The weight argument has a
mitigation already proven in this tree — an imperative head, exactly as
`Required terminology for this batch:` has, on the path `checks.py` enforces
downstream.

**Selection is against the batch, not the segment.** A batch is twenty-five
consecutive paragraphs — a scene — and a character active in a scene is named
somewhere in it even though most individual paragraphs of their dialogue are not.
Per-segment matching was the alternative and it loses precisely the dialogue the
feature exists for. `lx todo` selects against its whole emitted set for the same
reason, which is also what keeps the agent path and the model path briefed
identically.

The honest residue: a scene whose speaker is named only in the paragraph *before*
the batch begins gets no note. The preamble is what covers a rule that must never
be missed, and the limits below are sized on the assumption that anything
load-bearing lives there. Widening the haystack to the inlined neighbours
HANDOFF-014 attaches is a strict improvement and was left out on purpose: it
makes the selection depend on the batch boundary in a second way, and nothing
measured says it is needed yet.

**Two limits, 2000 characters of preamble and 800 per block, and no cap on how
many blocks one request may carry.** Measured 2026-08-02 against a batch of 25
paragraphs at ~285 characters — the dimensions `translate.py` already states for
a 100k-word novel, 2,000 segments over some eighty requests. Baseline request
12,021 characters:

| | request | ratio |
|---|---|---|
| no style sheet | 12,021 | 1.00x |
| preamble at the limit | 14,149 | 1.18x |
| preamble + 3 blocks at the limit | 16,669 | 1.39x |
| preamble + 8 blocks at the limit (a crowd scene) | 20,744 | 1.73x |
| a realistic sheet: 600-char preamble, 3 blocks of 250 | 13,619 | 1.13x |

2000 is not a round number chosen for looking reasonable: it puts the always-on
half at 1.18x, which is where D5 measured and accepted neighbour context on prose
(1.16x) — and unlike that feature this one is absent by default. The 1.73x crowd
scene is stated rather than capped, because the injected set is bounded by the
names the batch itself contains: a request carrying eight of them is a request
about eight characters, which is exactly when the notes are wanted.
`_glossary_hints` has always worked this way. *Lost:* one total limit over the
whole file, which would have to be tight enough for the always-on half and would
therefore cap the cast at the size of a short story — throwing away the reason
the format has blocks. Comments are stripped before anything is measured; a sheet
annotated by the person reading the book must not be refused for prose nobody
will ever send.

**The style sheet does not enter the translation-memory key**, on the same
footing as the glossary, which has never been in it either. D4 put `tone` in
because tone is *per-document*, so two registers coexist inside one project at no
cost and overwrite each other silently. A style sheet is per-project and so is
the memory — `tm_path(lang)` is `.lx/tm.{lang}.jsonl`, relative to the working
directory — so the locality argument that forced `tone` in does not reach it.

The residue is real and is not paid for with a key axis: a sheet refined at
chapter 20 means chapters 1–19 were banked under an earlier version of it. That
is the standing answer to this whole class of problem — a memory hit is a
*proposal* that goes through `translate.accept` and is re-validated at
`lx check`, exactly as a glossary edit is. *Lost:* a sixth key field. It would
invalidate the entire memory on every edit to the sheet, which is a file the
workflow expects to be edited while reading; the failure it would prevent —
serving wording banked under different voice instructions — is the failure the
acceptance path already exists to catch.

**One matcher now serves three callers, and converging them found a real gap.**
`translate.mentions` is used by `_glossary_hints`, by the style sheet's
selection, and by `cmd_todo`, which had a fourth copy of the regex inline. Its
boundary class is `A-Za-zÀ-ÖØ-öø-ɏ`, widened from the bare `[A-Za-z]` the
glossary used, for the reason `cli._LETTER` already records: with an ASCII-only
lookahead, `ï` is not a letter, so `Ana` matches inside `Anaïs` and a minor
character inherits the leading lady's notes. The change is a strict narrowing —
fewer matches, never more.

The gap: **that `lx todo` attaches a glossary hint only to the segments naming
the term was never asserted anywhere.** Removing the filter left the suite green.
Found by the mutation sweep, not by review, and now covered by
`test_one_matcher_governs_the_glossary_and_the_style_sheet_alike`.

**`lx todo` gains `voice` and `voice_notes`, and both keys are always present.**
`AGENTS.md` treats an API model, an agent in its own context and a human as three
equal sources of a translation; until this landed the register brief reached only
`translate_segments`, so an agent produced documentation prose for a novel and
`lx commit` banked it under the literary key anyway. That was recorded as
not-taken on 2026-07-29 and pointed here. Both fields are the same strings the
model path assembles, from the same two functions, so the paths cannot drift.
Empty rather than absent when there is nothing to say: HANDOFF-203 and
HANDOFF-207 will freeze this shape, and a consumer that must branch on a missing
key breaks on the first project with no style sheet. Confirmed unfrozen before
adding — both packages are still in `90-later/`.

**`cfg["style"]` joins the config-borne paths invariant 11 names as untrusted**,
beside `glossary`, `dnt` and `output_pattern`. It is trusted today on the
invariant's own stated ground — configuration is written by hand — and the
exemption ends for all four at once the moment configuration becomes writable
over HTTP. HANDOFF-206 carries that red line and has been corrected to name this
key.

**`lx init` scaffolds a comments-only sheet.** Present but silent, the trade
`config/dnt.txt` already makes: a hand-authored format nobody can discover is a
format nobody writes, and a scaffolded file that reached the model would brief
every fresh project with an example about a character named Eleanor. The example
lives inside the comments for exactly that reason.

**Verified by mutation, not by review.** Twenty-four mutants, one per guard this
change added; the first sweep left two alive and both were real. The Latin
Extended boundary survived because the test written for it did not discriminate
— `José` inside `Josée` is blocked by the narrow class too, and the case that
separates them is an accented letter *after* the name, which is now what the test
uses. The `cmd_todo` matcher survived because of the untested filter above. After
both were closed, 24 of 24 turn the suite red. 514 tests pass.

## 2026-08-02 · A segment travels with its neighbours, and the reference form cost 2x what D5 estimated

HANDOFF-014, implementing D5 of the 2026-07-29 re-founding. Each request item
now carries the segments either side of it as read-only source: `before_id` /
`after_id` when that neighbour is another item of the same request, `before_text`
/ `after_text` when it is not. `retry_one` gets both sides inlined, which was the
point — it sends one segment alone, and that is where a hard sentence ends up.
The window is `batch.context`, one segment either side, and `0` turns the feature
off including the paragraph of system prompt that explains it.

**Adjacency is the document's, never the caller's list.** `_neighbour_context`
reads `doc["segments"]`, the same authority `tone` and `eol` already have for
facts about a document rather than about one request. *Lost:* taking the
`segments` argument as document order, which is what a reading of `_chunks`
suggests — it does slice consecutive segments. But `lx repair` passes only the
failing segments and `lx translate --ids` passes whatever the user named, and
under either the payload would have told the model that segment 5 and segment 40
are consecutive prose. A confident lie about flow is worse than no context.

**The field shape was measured, and the first one was wrong.** D5 said "about
two extra segments per batch of 25 — roughly 8%". The text cost is indeed about
that; what the estimate did not count is the *container*. `_user_message`
serializes with `indent=1`, so a neighbour written `[{"id": "s0004"}]` costs some
48 characters where the id inside it costs 8. Measured at `batch.size` 25,
`batch.context` 1, as a ratio of the same request with no context (characters of
the user message — no tokenizer may be vendored under invariant 1, and the three
variants serialize the same text in the same script, so the character ratio is a
faithful proxy):

| document | chars/segment | nested `[{"id":…}]` | shipped `before_id` | naive |
|---|---|---|---|---|
| `tests/corpus/long-manual.md` | 45 | 1.95x | **1.50x** | 2.30x |
| `docs/decisions.md` | 415 | 1.25x | **1.16x** | 2.85x |
| `README.md` | 104 | 1.63x | **1.35x** | 2.56x |

The cost tracks *segment length*, not batch size: the per-item field is fixed
overhead, so a 415-character prose paragraph absorbs it and a 45-character table
cell does not. Prose is the use case this exists for, and the number that governs
is 1.16x — against the naive form's 2.85x, which is what D5's "roughly 3x" meant
and is confirmed. Larger batches are cheaper (1.12x on prose at `batch.size` 50,
1.27x at 10) because the inlined text lives only at the two edges. The retry path
is 2.3–2.8x for the one segment it re-sends, which is the price of having no
batch to borrow from and was accepted in advance. The system prompt grows 625
characters, once per run.

*Lost:* the nested list-of-objects form, on the numbers above alone. *Lost:* a
positional convention — "the items are in document order, so your neighbours are
the items beside you", which measured 1.05–1.07x and would have hit D5's
estimate. It makes *silence* carry meaning: a missing `before` would have to mean
"the item above is my previous paragraph" in a contiguous batch and "I am the
start of the document" in the first one, with an override rule for repair mode
where the array is not document order at all. This project puts determinism in
code and asks the model for judgement; a three-clause inference about array
position is neither. Nine points of request size on the primary use case is not
worth buying with it.

**A guard that survived the mutation sweep was removed rather than kept.** An
early `if window <= 0: return None` looked load-bearing and was not: a window of
0 slices `ids[i:i]` on both sides, so every entry is empty, no item gains a field
and nothing is briefed. Seventeen mutations were applied one at a time to a copy
of the tree and all seventeen turn the suite red; that one did not, and an inert
branch a later reader would take for load-bearing is worse than the dictionary it
saved building. `ids[max(0, i - window):i]` **is** load-bearing and stayed — at
`i=1` with a window of 2 the unclamped slice is `ids[-1:1]`, which is empty, so
the second segment of a document would silently lose the first.

**Neighbours do not enter the translation-memory key**, and `store.py` was not
touched. Same reasoning the key already applies to the mask configuration: it is
deliberately blind to what produced a proposal, and `translate.accept` is what
makes the blindness safe. A paragraph translated beside different neighbours is
still the same wording under the same key. *Lost:* a context hash in the key,
which invalidates the entire memory on any edit anywhere in the document.

**No new provider request field** — invariant 7 holds, and
`tests/test_provider.py::test_neighbour_context_survives_an_actual_request` runs
a real `translate_segments` against the mock HTTP server and asserts the body
still has exactly `model`, `messages`, `temperature`, `max_tokens`, `stream`.
`checks.py` was not touched either: continuity is judgement, invariant 4.

## 2026-08-02 · Document state is one SQLite database, and a batch is durable when it lands

HANDOFF-202. `.lx/docs/*.json` is gone; document state lives in `.lx/state.db`.
The translation memory is untouched and stays JSONL — its git diffability is the
entire reason it is versioned, and a binary blob produces unresolvable conflicts
in exactly the two-machines-or-two-branches case this tool exists for.

**The defect that made this urgent was not rewrite amplification.** The package
was written claiming every segment write rewrote the whole state file; measured
against the tree at `f11fb53`, it did not — `save_doc` had three call sites, all
at the end of a whole command. The severe half was the other one: **there was no
intermediate persistence at all.** `cli._translate` ran `translate_segments` over
the entire list and called `do_apply` once, so a 100k-word novel — some 2,000
paragraph segments and eighty requests at the default batch size, tens of minutes
to hours of model time — lost every translated segment to one Ctrl-C or one
dropped connection at 90%, because nothing had been written yet.

So `translate_segments` gained `on_batch`, called under the same lock as
`results`, and both callers pass `store.save_targets`. The CLI's final `do_apply`
still runs and is still the authority on what was applied; it is simply no longer
the first thing to reach disk. `tests/test_state.py::test_resume_after_interrupt_
keeps_every_completed_batch` cuts a provider off mid-run with `KeyboardInterrupt`
— not an `Exception`, which `run_batch` deliberately catches and retries — and
asserts the completed batches survived and the resumed run asks for the rest and
only the rest.

**Two version numbers, and they answer different questions.** `PRAGMA
user_version` (`SCHEMA_VERSION`) is the *database shape*: a newer one holds
columns this build has no statement for, so it is refused at the connection,
before any document has been named. `documents.state_version` (`STATE_VERSION`,
still 3) is what a *document row means*: versions 2 and 3 were both changes to
the JSON inside a segment, which no schema could have caught. *Lost:* collapsing
them into one number, which the package suggested. It kills the escape hatch —
a database-wide refusal makes `lx extract --reset` unreachable, so a content bump
would force every document in the project to be re-extracted at once and the
sentence that refusal message prints would become false. `STATE_VERSION` is not
bumped by this work: no older build can see the database at all, so there is
nothing to misread. HANDOFF-208 bumps it, because raw-nodes-as-bytes is not
readable under any older interpretation.

**Raw skeleton nodes are a BLOB column, not JSON text.** A raw node's value is
lifted out of the node's JSON into its own column; a `str` goes in as TEXT and a
`bytes` as BLOB, and each comes back as what it was. Nothing writes bytes yet —
HANDOFF-208 changes the parsers — but this is what makes that package a column
type rather than a base64 scheme, and it is why a damaged source can become
readable at all: `json.dumps` accepts the surrogate a `surrogateescape` decode
produces and the *file write* then dies with `UnicodeEncodeError: surrogates not
allowed`, so no serializer option ever avoided it.

**Segment identity is three nullable columns, and no comparison is made on them
in SQL.** `content_hash`, `context` and `variant` are read out and the key is
built by `store.tm_key`, where absent and null have been one value since
HANDOFF-007 because the key is a tuple of read fields. This is the answer to the
question the package asked about `NULL` in a unique index: **there is no unique
index on the identity**, deliberately. A `WHERE variant = ?` never matches a
NULL, and a UNIQUE index treats two NULLs as distinct — both would have been
silent — and beyond that a document may legitimately hold the same sentence
twice, so uniqueness there would be wrong even if the nulls behaved. Uniqueness
of a *memory entry* belongs to the memory file, which this package does not
touch. `tone` needs no column: it is per-document, lives in `meta`, and its own
collapse stays inside `tm_key` where no caller can route around it.

**`prior_targets` changed shape, reversing part of the 2026-07-29 split.** It was
`prior_targets(doc)` over a document `prior_doc` had already read, so that extract
would not read a whole book twice. It is now `prior_targets(src, lang)`, a query
over four columns, and `prior_doc` returns document-level facts with no skeleton
and no segments — the same saving reached properly rather than by sharing one
full read. The casualty is the `kind`-for-a-missing-`context` migration rule: in
JSON, presence was observable and a pre-version-3 file could be read that way; a
column is present or NULL and cannot tell absence from a legitimate null. It
migrated a file format that no longer exists, so it went with it.

**Concurrency: WAL, and it is enough. Measured, not assumed** — the package asked
for exactly this. Two writer processes doing 100 narrow `save_targets` each
against one `.lx/state.db`, with a third process reading whole documents in a
loop: zero `SQLITE_BUSY`, all 200 writes landed, `PRAGMA integrity_check` ok, and
855 full document loads completed alongside them in three seconds. The
counterfactual, same shape on a rollback journal: writers took 0.55s and 1.30s
against WAL's 0.01s, and the reader completed 14,857 iterations against WAL's
124,348 — no errors either way, because the 5-second `busy_timeout` absorbs the
blocking rather than surfacing it. So the rollback journal is not *incorrect*
here; it is one to two orders of magnitude slower under precisely the `lx web`
plus `lx run` case, which is the case this project has. What WAL costs: the
`-wal` and `-shm` sidecars, which exist while a process holds the database open
and are removed on the last clean close — hence `.lx/state.db*` as a glob in
`.gitignore`, since a committed WAL is a half-written transaction somebody else
has to recover — and it rules out a `.lx/` on a network share, which is not a
place working state belongs.

**One database, not one per document.** `tracked` becomes a query and the
cross-document reads a status contract will need cost nothing. *Lost:* a database
per document, which would have removed even the brief write contention between
`lx run` on one document and the workbench on another — not worth three files per
document in `.lx/` for a lock held for the length of one batch.

**No migration is written, and `.lx/docs/*.json` is not read.** It is regenerable
and gitignored, which is what made the move free; re-extracting is both cheaper
than a migration and unable to half-succeed. The whole debt is a sentence: when
state is missing and a legacy file is sitting there, the "no state" message says
where state lives now and that the translation memory carries over.

## 2026-08-02 · Decoding successfully is not decoding correctly, and cp950 is not reversible

Three findings from the adversarial review of the plain-text branch, before it
merged. All three are the same shape: a green suite standing behind a claim it
does not actually test.

**Encoding detection gains a plausibility veto.** First-success-wins over an
ordered candidate list reads a short Traditional Chinese file as `shift_jis`,
which decodes it into half-width katakana — and does so *byte-reversibly*, so the
byte-level round-trip fixtures stay green while every segment's text and hash are
wrong and get banked in the translation memory. This is not a corner: a
per-chapter `.txt` and an epigraph are how a novel arrives. Measured over 300
slices per length, misdetection ran 175/300 at five characters and 5/300 at
thirty; a whole novel was never at risk, which is exactly why the fixtures could
not see it.

`docio._implausible` now vetoes a decode whose non-ASCII is mostly half-width
katakana (U+FF61–FF9F) or C1 controls, and the first *plausible* candidate wins.
Measured after: 0/300 at every length, Japanese unaffected at every length (real
kana are full-width), and of 5000 random byte strings none became undecodable
that was not undecodable before. When every candidate looks like mojibake the
veto abstains and candidate order decides, as before — a heuristic may reorder
candidates but may not become a gate.

*The alternative that lost:* score every candidate by how much of it is CJK,
kana, Hangul or Latin and take the best. That decides between two *plausible*
readings, which this project already answers with candidate order and a printed
winner — and it would silently overrule the ordering rationale recorded in
`config.TEXT_DEFAULTS`. A veto only removes readings that are not plausible at
all, so the two mechanisms stay separable. Also rejected: reordering
`shift_jis` after the Chinese candidates, which just moves the misdetection onto
Japanese files, since `gbk` swallows them.

**cp950's decode is not injective, and invariant 2a's byte claim does not hold
for it.** Ten two-byte sequences — `A2CC`, `A2CE`, `F9E9`–`F9EB`, `F9F9`–`F9FD`,
the Big5 duplicate-encoding block — decode to a character that re-encodes to
different bytes. `A2CC` is 十 as a numeric run writes it; `F9F9`–`F9FD` are the
box-drawing characters a BBS-era Traditional Chinese `.txt` rules its chapters
with. `gbk`, `shift_jis` and `cp1252` are injective; cp950 alone is not, and it
is in the candidate list precisely because Python's `big5` codec is too narrow
for real Windows-authored text.

Nothing is corrupted today: `write_document` encodes UTF-8 and the pipeline never
writes a document back in its source encoding, so the reader's characters are
right and only the bytes would not survive. What was false is the `decode_document`
docstring, which claimed re-encoding with the same concrete codec reproduces the
original bytes — and that claim was the whole of invariant 2a's standing for this
format.

*What was chosen:* correct the claim, pin the ten sequences and the end-to-end
character path in fixtures, and add
`test_source_encoding_write_would_break_invariant_2a`, which fails the moment a
caller writes a document back in its source encoding — the one change that turns
this from latent into durable corruption of a Big5 novel.

*The alternative that lost, and why it is not "more thorough":* fix it now by
preserving bytes. Byte-exactness here is not a special case for ten sequences —
the round-trip is text end to end, so it needs raw skeleton nodes held as bytes
rather than as JSON text, which is the BLOB work invariant 2a already names as a
known gap and `decode_document`'s refusal message already points at. Scoped
properly it is a separate package; scoped narrowly it is a hack that generalizes
to nothing. *Also rejected:* a reversibility guard in the candidate loop —
measured, it sends a Big5 novel containing a chapter rule down to `gbk` and reads
it as mojibake, which is worse than the defect and is the exact failure the
`big5` → `cp950` repair existed to prevent.

**A 1.59M-combination sweep proved less than it looked.** The comment at
`checks.py` recorded that the host-profile rewrite left Markdown unchanged,
measured over twelve line shapes. The shape set contained no blank or
whitespace-only *target*. Adding six moves the difference count from 3 of 864 to
129 of 1944, and 126 of those are one case: a single-line-source `heading` with
an empty target, which the old code answered `[]` and the new one answers with
the blank-line message.

Markdown *is* unchanged in fact — none of the 129 is reachable, because
`check_segment` returns at its `not tgt.strip()` guard before containment runs.
But that is a different reason from the one recorded, it is a reason a direct
caller of the public `containment_problems` does not inherit, and nothing in the
suite stated it. The comment is corrected and
`test_a_blank_target_stops_at_the_missing_rule_and_never_reaches_containment`
now pins the guard. The general lesson is worth more than the fix: a large sweep
across a shape set missing one dimension reads like proof and is not, and the
number of combinations is not evidence of coverage.

## 2026-08-02 · Plain text lands, and the format registry lands with it

Closing HANDOFF-017. A novel could not enter the pipeline at all before this: the
only parser was Markdown's and every path reached it regardless of what the file
was called. Plain text is the cheapest format that changes that, and being the
first non-Markdown one it brings the registry that was deferred out of
HANDOFF-007 to land with it. Suite 362 → 474.

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
