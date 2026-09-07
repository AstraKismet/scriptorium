"""Near matches from the translation memory: what this sentence nearly is.

The memory answers exactly or not at all. :func:`store.tm_lookup` takes a key —
content hash, context, segmentation version, variant, register — and one
character's difference in the source makes it a miss. For documentation that is
the whole story, because a re-run of the same guide meets the same sentences.
For a novel it is not: the second chapter says *She had not slept* where the
first said *She had not slept well*, and the memory holds wording a reviewer
already approved for one of them and cannot offer it for the other.

**It shows and it never writes.** A near match is advisory by the standing rule
that a fuzzy hit differs in its placeholder set by definition — the ids in the
banked wording were numbered against a different sentence, so substituting it
renders a bare ``⟦2⟧``. Automatic application of fuzzy matches is deferred
indefinitely (`AGENTS.md`, *Not in scope*), and **there is deliberately no
one-click apply**: a reviewer who wants those words retypes them, or sends them
through `POST /api/save` as their own, where `translate.accept` and the origin
rules see them like anything else. Nothing in the pipeline consults this module,
and `cli.do_suggest` opens no writer.

That makes it `renderings.py`'s sibling rather than `checks.py`'s. It reports,
it infers, and it may never move an exit code: a number produced by a threshold
is not evidence, and invariant 10 reserves that word for `lx check`.

**Why `difflib` and not the library everyone means.** Invariant 1 excludes
compiled extensions, which excludes `rapidfuzz` by name (`docs/decisions.md`,
2026-07-28) — the pipeline has to run on a bare interpreter, in CI, in an agent
sandbox and on a locked-down machine, and the industry-standard fuzzy-matching
library is C++. `difflib` is stdlib and pure Python. The score is therefore
*this implementation's* number and not a standard anybody else implements,
which is why :data:`ALGORITHM` travels in every response: a second client
computing its own ratio would get different numbers, and a later change of
algorithm should be visible rather than silent. Decided 2026-08-17; the losing
options were a token or character n-gram overlap (trivially reimplementable, but
character n-grams behave differently on unspaced CJK) and a pure-Python
Levenshtein (O(n·m) per candidate with no C to hide behind).

Character-level on the raw source text, which is what "a difflib ratio" means to
anyone reading the name in the response, and what `store.tm_record` writes and
the content hash is taken over. **Word-level was measured and refused**: it is
an order of magnitude faster and makes `quick_ratio` a real filter, but it
scores a source language written without spaces as a single token — silently,
and as a plausible number rather than an error.

## Two `difflib` defaults are wrong here, and both are silent

``autojunk`` is on by default and discards any element occurring in more than 1%
of a sequence longer than 200. On a 500-character English paragraph that makes
the space character junk, and ``e``, and every common letter. It exists for
lines of code, not prose. It is off everywhere in this module and that is not a
tuning knob — `store.Carryover` reached the same conclusion about the same
engine.

``SequenceMatcher`` is quadratic in the product of the two lengths, which is why
:data:`WORK_BUDGET` exists at all. See below; the numbers are the argument.

## What the prefilters are actually worth, measured

2026-09-07, against 875 real English paragraphs from this repository's own
`docs/decisions.md` (41–600 characters, median 384), six probes built by
truncating a record and appending a clause:

* **The length guard rejects 34–83%** of the memory and costs arithmetic. It is
  `real_quick_ratio` spelled out — that method is `2·min(la, lb) / (la + lb)`
  and nothing else — so calling both would measure one thing twice.
* **`quick_ratio` rejects almost nothing: 90 of 669 survivors on one probe,
  92 of 669 on another.** It is a multiset intersection over *characters*, and
  any two English paragraphs of similar length share nearly every character, so
  it returns ≈1.0 for almost every pair. It is kept because it is a valid upper
  bound, it is linear, and it is the sort key below — not because it filters.
* **`ratio()` therefore runs on 146–577 candidates per segment**, at a measured
  **65M character-pairs per second**, which is 80–870 ms per segment against
  875 records and scales linearly with the memory.

**A branch-and-bound was built and removed.** Sorting candidates by their upper
bound and stopping once `limit` results beat the best remaining bound is exact
and costs nothing — and saved **0 of 2209** full comparisons across those six
probes, because the prune cannot fire until `limit` matches are already in hand
and the realistic case finds none. The sort survives it: it is what makes
:data:`WORK_BUDGET` drop the least-promising candidates rather than an arbitrary
tail.
"""

import difflib

#: What produced :func:`near`'s score, carried in every response.
#:
#: A name rather than a version, because what a client must not do is assume the
#: number is comparable to another tool's — and that is true of every `difflib`
#: release. If this is ever replaced this string changes, so a client that
#: recorded scores can see it must not compare them across the change.
ALGORITHM = "difflib.SequenceMatcher.ratio"

#: Nothing below this is offered. Decided 2026-08-17 with the algorithm.
#:
#: A reviewer drowned in near-misses stops reading the panel, and a fuzzy hit
#: differs in its placeholder set by definition, so a marginal one is not worth
#: the attention it costs. The number is a default a surface may expose later;
#: what is frozen is the *meaning* of the score — a ratio in ``[0, 1]``.
CUTOFF = 0.70

#: Suggestions per segment. A panel, not a search result.
#:
#: The comparison has already been made for every candidate by the time this
#: truncates, so its cost is a reviewer's attention rather than time. ``0`` is
#: every match above the cutoff, the reading `cli.checked_limit` gives every
#: other bound in this project.
MAX_SUGGESTIONS = 5

#: Character-pairs compared per segment before the search stops and says so.
#:
#: `store.ALIGN_BUDGET`'s shape, for the same engine hitting the same wall, and
#: the one difference is what happens at the ceiling: alignment degrades to a
#: worse *complete* answer, and this degrades to a *partial* one — so unlike
#: that budget, this one is reported on the wire. `truncated` is a first-class
#: part of the response and not an error, because a panel that quietly stopped
#: looking is indistinguishable from a memory that holds nothing.
#:
#: **It is a backstop against a pathological memory, not a routine cap, and the
#: difference was measured rather than assumed.** The first value tried was
#: 20M — a ~300 ms ceiling, which looked like a sensible panel budget and was
#: badly wrong. Against 1967 real English paragraphs (median 291 characters)
#: from this repository's own tracked Markdown, 37 probes each a record with its
#: second half rewritten, 16 of which have a true match above the cutoff:
#:
#: ===========  ==========  ========  ===========  ========
#: budget       median ms   max ms    truncated    recall
#: ===========  ==========  ========  ===========  ========
#: 20M                 394       474      24 / 37       56%
#: 50M                 823      1053      18 / 37       75%
#: 100M                761      2304      10 / 37       88%
#: **200M**            833      3328       0 / 37      100%
#: unbounded           793      3309       0 / 37      100%
#: ===========  ==========  ========  ===========  ========
#:
#: **A budget that fires routinely loses matches close to at random**, because
#: truncation drops candidates in `quick_ratio` order and `quick_ratio` is ≈1.0
#: for any two English paragraphs of similar length — so the order it drops in
#: barely predicts the true ratio. At 20M that cost 44% of the findings while
#: `truncated` said only "there may be more". Note also what the table shows
#: about the *median*: it is flat from 50M up, because the budget only ever
#: bites the expensive tail. Buying recall here costs almost nothing typical.
#:
#: 200M is where it stops costing anything and is still a ceiling — about 3.3 s
#: for one segment against that memory. It bounds *one segment against the whole
#: memory*; how many segments a request answers is bounded separately, by
#: `cli.SUGGEST_SEGMENTS`.
#:
#: **The milliseconds in that table are not the ones `cli.SUGGEST_SEGMENTS`
#: quotes, and the difference is the probe set rather than a contradiction.**
#: These probes were built from records of 80 characters or more; that constant
#: was derived from a later, frozen run over probes of 120 or more, where one
#: segment costs 1.3 s at the median. Cost is the product of the two lengths, so
#: longer probes cost more. What this table is *for* is the comparison down its
#: last two columns, which is a property of the budget and not of the probes.
WORK_BUDGET = 200_000_000


def near(source, records, cutoff=CUTOFF, limit=MAX_SUGGESTIONS, skip=(),
         budget=WORK_BUDGET):
    """``(hits, examined, skipped)`` for the memory lines ``source`` nearly is.

    ``hits`` is ``[(score, key, record)]``, best first, ties broken by key so the
    order is a function of the data rather than of dictionary iteration.
    ``examined`` is how many candidates reached the full comparison and
    ``skipped`` how many the budget stopped it from reaching — ``skipped > 0``
    is what a caller reports as `truncated`.

    ``records`` is ``(key, record)`` pairs, which is what
    ``store.load_tm(lang).items()`` gives.

    ``skip`` is the keys already answered elsewhere: a caller passes the
    segment's own exact key, because a record the memory already offers through
    :func:`store.tm_lookup` is not a *near* match and listing it beside the
    others would put one wording on the screen twice.

    **A record whose source text is identical but whose key differs is kept**,
    at a score of ``1.0``. That is not something to filter out — it is the same
    sentence banked under another context, variant or register, which is the
    case a reviewer most wants to see and the one thing an exact lookup
    structurally cannot offer. The score says ``1.0`` and is telling the truth;
    what differs is the key, and the caller holds both.
    """
    if not source:
        return [], 0, 0
    # The segment is `b` and the candidates are `a`, which is the whole reason
    # this is one matcher rather than one per record: `quick_ratio` builds a
    # multiset of `b` and caches it, and `set_seq1` leaves that cache alone.
    # The other way round, every candidate would rebuild it.
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(source)
    la = len(source)

    candidates = []
    for key, rec in records:
        if key in skip:
            continue
        other = rec.get("source") or ""
        lb = len(other)
        # `real_quick_ratio` as arithmetic, before the matcher is touched: two
        # sequences too different in length cannot match enough elements to
        # reach the cutoff, whatever they contain.
        if not lb or 2 * min(la, lb) < cutoff * (la + lb):
            continue
        matcher.set_seq1(other)
        bound = matcher.quick_ratio()
        if bound < cutoff:
            continue
        candidates.append((bound, key, rec))
    # Descending by upper bound, so that if the budget stops the walk it drops
    # the candidates whose best possible score was lowest. That is the only
    # thing this sort buys — the exact prune it was written for never fired.
    candidates.sort(key=lambda row: (-row[0], row[1]))

    hits, spent, examined = [], 0, 0
    for index, (_bound, key, rec) in enumerate(candidates):
        cost = la * len(rec["source"])
        # Checked before the comparison and never after: a budget that stops
        # once it is already over has not bounded the worst case, which is one
        # pair of very long paragraphs.
        if spent and spent + cost > budget:
            return _best(hits, limit), examined, len(candidates) - index
        matcher.set_seq1(rec["source"])
        spent += cost
        examined += 1
        score = matcher.ratio()
        if score >= cutoff:
            hits.append((score, key, rec))
    return _best(hits, limit), examined, 0


def _best(hits, limit):
    """The best ``limit`` hits, or all of them when ``limit`` is falsy.

    ``0`` means unbounded here, which is `cli.checked_limit`'s reading of it for
    every other bound in this project — and getting it wrong is not a
    theoretical worry: `hits[:0]` is the empty list, so a caller asking for
    "no cap" would have been told the memory holds nothing.
    """
    ranked = sorted(hits, key=lambda row: (-row[0], row[1]))
    return ranked[:limit] if limit else ranked
