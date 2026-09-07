"""Where one source term was written more than one way in the target.

A character's name drifts across a long book. `checks.py` cannot see it: its
rules are per segment, and "this sentence disagrees with another one" is not
visible from inside either sentence. That is the same reason `audit.py` exists,
arriving from the other side — where that module needs a network service and a
threshold, this one needs the whole book at once.

**Two moments, and they are the same command.** A reviewer who already suspects
a name wants every place it occurs, beside its target, with nothing inferred:
:func:`occurrences` answers that, cannot produce a false positive, and is what
`lx renderings --term` is. A reviewer who does not know which of two hundred
names drifted wants a ranking of which of those questions is worth asking:
:func:`run` answers that, and it infers.

**It reports and it never repairs**, and nothing else in the pipeline consults
it. The remedy it points at is a person's: decide the rendering, write it into
the glossary with `lx glossary set`, and `checks.py`'s glossary rule then names
every disagreeing segment mechanically, at an exit code, on three surfaces. The
inference finds; the decidable half adjudicates. That is why nothing here may
move an exit code — see `cli.cmd_renderings`.

Five things it structurally cannot see, printed with every report rather than
left to be discovered:

* **A rendering used once.** Two supporting segments is the floor, and it is
  not a knob. At support one every gram in a target passes the discriminative
  filter, so the "rendering" that comes back is the sentence — measured
  2026-09-07 on the 26-segment case set, where a rule built to reach exactly
  this case returned ``帳是艾蓮諾`` and ``達西先生是從灰``. The cost is the case a
  proof-reader meets most: seven segments right and the eighth wrong. A term
  *named* fewer than twice is reported as unexamined; a term whose rival
  **variant** is used once is invisible to the sweep altogether, and `--term`
  is the only thing that finds one. That is the third layer of the design
  rather than a hole in it, and the report says so.
* **A rendering that spans a separator** — a space, a middle dot. It is seen
  only in its parts, so a target language written with spaces is not supported
  in practice and says so rather than answering zero.
* **Two renderings that share their text.** What this finds reliably is two
  renderings with nothing in common — 灰岸 against 阿什科姆. Where one contains
  the other, or where both are built on the same core, it usually finds one
  rendering and reports nothing: 馬奇蒙 against 馬奇蒙特 needs both forms written
  at the end of a phrase twice, and 瑞德格瑞 against 阿德格瑞 is absorbed by the
  德格瑞 they share. Measured 2026-09-07 on five 240-segment synthetic books of
  thirty names each: **no false finding on any of the five with nothing
  drifted**, and one of five real drifts found when every drift was of the
  shared-core kind. The trade is deliberate — a sweep with forty false findings
  is a sweep nobody reads, and `--term` finds what this misses.
* **A rendering that is also an ordinary word.** The filter keeping ordinary
  words out is *this gram appears in no segment that never names the term*; it
  strengthens as the book grows and is weakest on a short one. Measured on a
  synthetic book whose names were drawn from the same characters as its prose,
  this is where the design degrades rather than where it is silent.
* **A name written as a pronoun, or dropped.** Listed beside the finding as
  `unrendered` and never counted as evidence of drift — which is precisely what
  the glossary rule cannot do, since it reports that segment as an error.

Designed and measured 2026-09-07; `docs/decisions.md` of that date carries the
alternatives that lost and the numbers that killed them.
"""

import re

#: Runs of letters, which is where a rendering can live. Digits, underscores,
#: whitespace and every mark of punctuation are separators — including the
#: ``⟦``/``⟧`` of a placeholder and the ``·`` of ``艾蓮諾·凡斯``.
_RUN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

#: One letter, for asking whether a gram ends the run it sits in.
_WORD_CH = re.compile(r"[^\W\d_]", re.UNICODE)

#: Longest gram considered as a rendering. A bound on work, not a threshold on
#: evidence: nothing is decided by it, a longer rendering is simply invisible.
MAX_GRAM = 8

#: How many segments have to carry a rendering before it is one.
#:
#: A module constant with no configuration key over it, which is `audit.MARGIN`'s
#: decision and for its reason: a per-project threshold lets two runs of one
#: command over one book disagree with nothing in either report saying why. It
#: has no flag either, because unlike a margin there is no useful other value —
#: at one, the rule stops working rather than becoming stricter or looser.
MIN_SUPPORT = 2

#: Printed with every report. A report never says clean.
FLOORS = (
    "a term named fewer than twice is reported as unexamined rather than as "
    "checked; a rival rendering used only once is invisible here altogether, and "
    "`--term` is the only thing that finds one",
    "a rendering that spans a space or a middle dot is seen only in its parts",
    "two renderings that share their text are told apart only if each is written "
    "at the end of a phrase in two segments, so a one-character variant of a long "
    "name usually reads as consistent",
    "a name written as a pronoun, or dropped, is listed beside the finding and is "
    "not evidence of drift",
    "the filter that keeps ordinary words out is `this gram appears in no segment "
    "that never names the term`, which is weakest on a short book",
)

#: How a finding is ordered, never whether it is reported.
#:
#: Measured 2026-09-07 on a 5000-segment synthetic book whose renderings were
#: drawn from the same characters as its prose: 39 false findings against 7 true
#: ones, and 29 of the 39 named three or more renderings while 6 of the 7 true
#: ones named exactly two sharing nothing. Suppressing the two noisy shapes
#: would have removed 35 of 39 false and 1 of 7 true — and it would also silence
#: 阿什科姆 against 阿希科姆, a one-character drift, which is the drift a reviewer
#: is least able to catch by eye. So the shape orders the report and does not
#: filter it.
SHAPES = ("distinct", "overlapping", "several")


def _runs(text):
    return _RUN_RE.findall(text or "")


def _grams(text, maxlen=MAX_GRAM):
    """Every gram of length 2..maxlen inside a run of letters."""
    out = set()
    for run in _runs(text):
        n = len(run)
        for i in range(n):
            for j in range(i + 2, min(i + maxlen, n) + 1):
                out.add(run[i:j])
    return out


def _stands_alone(text, gram, left, right):
    """Does some occurrence of ``gram`` end its run on the sides asked about?

    The evidence that a short form is a rendering in its own right rather than
    the front of a longer one: somewhere it is written with nothing attached on
    the side where the long form attaches something. ``馬奇蒙。`` and ``馬奇蒙，``
    are that evidence; ``灰岸莊園走`` and ``灰岸莊園的`` are not.

    For a target language written with spaces every occurrence of every word
    stands alone, so this test is vacuous there — the third of the floors above,
    arriving in the code that implements it.
    """
    i = text.find(gram)
    while i >= 0:
        ok = True
        if left and i > 0 and _WORD_CH.match(text[i - 1]):
            ok = False
        j = i + len(gram)
        if right and j < len(text) and _WORD_CH.match(text[j]):
            ok = False
        if ok:
            return True
        i = text.find(gram, i + 1)
    return False


def _translated(seg):
    # `store._segment` re-derives status from the target on every read, so the
    # target is the fact and `status` is its projection. Asking the text keeps
    # this module working on a segment dict a test built by hand.
    return bool((seg.get("target") or "").strip())


def occurrences(segments, term, mentions):
    """Every segment whose masked source mentions ``term``. No inference at all.

    The answer a reviewer gets when they already know the name, and the lower
    bound the inferring half has to beat. It reads ``masked`` rather than
    ``source`` for the reason `cli.candidate_terms` does: a do-not-translate
    term is already ``⟦n⟧`` by the time a segment is stored, so a protected name
    cannot be reported here at all.

    ``mentions`` is passed in rather than imported, because there is exactly one
    matcher for "does this text contain this name" and it lives in
    `translate.mentions`; a copy of its boundary class here would be the fourth.
    """
    return [seg for seg in segments
            if mentions((seg.get("masked") or "").lower(), term)]


def _covers(longer, shorter, mentions):
    """Is ``shorter`` a whole-word part of ``longer``, and strictly shorter?"""
    return longer != shorter and mentions(longer.lower(), shorter)


def _attribute(segments, terms, mentions):
    """``term -> ({ids that mention it}, {ids it is the longest term for})``.

    An occurrence belongs to the *longest* term that matches at it. ``Ashcombe
    Hall`` takes its segments away from ``Ashcombe``, which is what keeps
    灰岸莊園 out of Ashcombe's rendering set and those segments out of its list.

    The exclusion is per *segment* rather than per occurrence: a paragraph
    naming both ``Ashcombe Hall`` and a bare ``Ashcombe`` is credited to the
    Hall alone and the bare mention is lost. Telling them apart needs the
    offsets `sentences.py` refused to emit on 2026-08-17, so this is unrepaired
    rather than overlooked.
    """
    lows = {seg["id"]: (seg.get("masked") or "").lower() for seg in segments}
    mention = {t: {sid for sid, low in lows.items() if mentions(low, t)}
               for t in terms}
    longer = {t: [u for u in terms if _covers(u, t, mentions)] for t in terms}
    own = {t: {sid for sid in mention[t]
               if not any(sid in mention[u] for u in longer[t])}
           for t in terms}
    return mention, own


def _gram_index(segments, wanted):
    """``gram -> {ids whose target contains it}``, for the grams in ``wanted``.

    One pass over every translated target. ``wanted`` is the union of every
    term's own supported grams, so the index holds only what some term could
    possibly claim — which is what keeps this affordable on a real book, and the
    difference between 44 MB and 1.5 GB on a 300-page novel.
    """
    index = {g: set() for g in wanted}
    for seg in segments:
        if not _translated(seg):
            continue
        for gram in _grams(seg["target"]):
            hit = index.get(gram)
            if hit is not None:
                hit.add(seg["id"])
    return index


def _supported(targets):
    """Grams appearing in at least ``MIN_SUPPORT`` of ``targets`` -> support."""
    seen = {}
    for sid, text in targets.items():
        for gram in _grams(text):
            seen.setdefault(gram, set()).add(sid)
    return {g: s for g, s in seen.items() if len(s) >= MIN_SUPPORT}


def _separable(longer, shorter, here, remainder, targets):
    """Are these two forms two renderings, or one rendering and some grammar?

    **Both** have to be seen standing alone on the side that separates them, in
    ``MIN_SUPPORT`` segments each. That is the whole test, and it is the
    difference between 馬奇蒙特 against 馬奇蒙 and 灰岸的 against 灰岸: a
    grammatical particle is mostly *not* terminated on the side it attaches to,
    because attaching is what it is for, while a name is followed by a comma or a
    full stop sooner or later.

    Measured 2026-09-07, in three rounds, and each round is a rule that lost.
    Asking it of the short form alone took a 5000-segment synthetic book from 184
    of 200 terms reported, with 8 real drifts, to 9 of 200 — a Chinese name is
    followed by 的 constantly, so ``X的`` reaches support on any long book and
    splits ``X`` off itself. Asking the long form for a *single* standing-alone
    occurrence was the next attempt and is not enough: 的 does end a Chinese
    clause, and on five 240-segment books with nothing drifted that reported all
    thirty names in all five. Requiring ``MIN_SUPPORT`` on both sides is what
    survived — and it is still scale-dependent, which is why `_grammar` exists
    above it.

    What it costs is in the module's floors: a rendering never written at the end
    of a phrase cannot be told from the same rendering with a particle stuck to
    it, and a term whose long form is always followed by 的 reads as consistent.
    """
    at = longer.find(shorter)
    left, right = at > 0, at + len(shorter) < len(longer)
    return (sum(1 for sid in remainder
                if _stands_alone(targets[sid], shorter, left, right)) >= MIN_SUPPORT
            and sum(1 for sid in here
                    if _stands_alone(targets[sid], longer, left, right))
            >= MIN_SUPPORT)


def _delta(longer, shorter):
    """The characters the long form adds to the short one. 灰岸的 over 灰岸 is 的."""
    return longer.replace(shorter, "", 1)


def _candidates(own_ids, targets, mention_ids, index):
    """The grams that could be a rendering of one term, and the segments each is in.

    A gram qualifies on two counts: it stands in at least ``MIN_SUPPORT`` of the
    term's own segments, and it appears in **no** segment whose source never
    names the term at all. The second is the whole discriminative filter, and it
    is what keeps an ordinary word out — it strengthens as the book grows, which
    is why the module's floors call it weakest on a short one.

    Nothing prunes a gram a longer one covers the same segments as. There was
    such a pass and it was removed on 2026-09-07, by the mutation round that
    could not kill it: over the frozen case set, the hand-written traps, six
    1200-segment synthetic books and one of 5000 segments and 200 names, its
    presence changed **no finding at all** and cost 0.4 s. What it was there for
    — stopping the report naming 灰 where it means 灰岸 — the coverage stage
    already does, because it breaks a tie on coverage by taking the longer gram.
    """
    return {g: sup & own_ids for g, sup in
            _supported({sid: targets[sid] for sid in own_ids}).items()
            if not (index[g] - mention_ids) and len(sup & own_ids) >= MIN_SUPPORT}


def _renderings(own_ids, targets, mention_ids, index, grammar=frozenset()):
    """The renderings of one term, and the segments carrying each.

    Two stages, because there are two different failures.

    **Coverage.** Greedily take the gram explaining the most of what is left,
    longest first on a tie. This separates two renderings that share nothing —
    灰岸 against 阿什科姆 — and the "appears in no segment that never names the
    term" filter is what keeps an ordinary word out.

    **Extension.** A rendering that is a strict part of another survives
    coverage whole: every target carrying 馬奇蒙特 also carries 馬奇蒙, so
    coverage sees one rendering in five segments and reports nothing. So each
    chosen rendering is asked whether some longer supported gram containing it
    covers only part of its own segments; if it does, the two are split.

    Both stages require ``MIN_SUPPORT``, on both sides of a split. Without the
    second half, 灰岸莊園的燈 and 灰岸莊園的樓梯 split ``Ashcombe Hall`` off its
    own third segment on the strength of a grammatical particle — one segment is
    not a rendering, it is a sentence.
    """
    if len(own_ids) < MIN_SUPPORT:
        return [], set(own_ids)

    cands = _candidates(own_ids, targets, mention_ids, index)

    chosen, left = [], set(own_ids)
    while True:
        best = None
        for gram, sup in cands.items():
            here = sup & left
            if len(here) < MIN_SUPPORT:
                continue
            key = (len(here), len(gram), gram)
            if best is None or key > best[0]:
                best = (key, gram, here)
        if best is None:
            break
        _, gram, here = best
        chosen.append((gram, here))
        left -= here

    out = []
    for gram, here in chosen:
        # A split is marked, because only a split can be explained away by a
        # longer source run — two renderings that share nothing never can be.
        longer = [(h, s & here) for h, s in cands.items()
                  if len(h) > len(gram) and gram in h]
        longer = [(h, s) for h, s in longer
                  if len(s) >= MIN_SUPPORT and len(here - s) >= MIN_SUPPORT
                  and _delta(h, gram) not in grammar
                  and _separable(h, gram, s, here - s, targets)]
        if not longer:
            out.append((gram, here, False))
            continue
        longer.sort(key=lambda pair: (-len(pair[0]), pair[0]))
        head, ids = longer[0]
        out.append((head, ids, True))
        out.append((gram, here - ids, False))
    return out, left


def _grammar(terms, per_term, targets, mention, live, index):
    """The deltas that are the target language's grammar rather than a rendering.

    One pass over every term, collecting what each candidate rendering *adds* to
    a shorter one it contains, and keeping the additions more than one term
    makes. 的 extends every Chinese name in a book; 特 extends `Marchmont` and
    nothing else. The corpus answers the question, so no table of particles is
    written down for any language and nothing here is maintained per locale.

    **Why a corpus-wide pass exists at all.** The per-term test — both forms
    written at the end of a phrase, twice each — is scale-dependent, and that is
    measured rather than feared: on a 240-segment synthetic book it reported
    nothing with nothing drifted, and on the same generator at 4800 segments it
    reported thirteen names, every one of them ``X的`` against ``X``. 的 does end
    a Chinese clause; it just takes a long book to do it twice. A rule whose
    precision falls as the book grows is the wrong way round for a command whose
    subject is a novel.

    Collected from the **candidates** rather than from the splits that survive,
    because which extension a term happens to choose varies with the length of
    its rendering — collected from the survivors, thirty names all extended by
    的 produced thirty different deltas and the filter caught none of them.

    It can only remove findings, never add one, so a book with a single name in
    it behaves exactly as it did before.
    """
    seen = {}
    for term in terms:
        ids = per_term[term]
        if len(ids) < MIN_SUPPORT:
            continue
        cands = _candidates(ids, targets, mention[term] & live, index)
        for longer in cands:
            for shorter in cands:
                if longer != shorter and shorter in longer:
                    seen.setdefault(_delta(longer, shorter), set()).add(term)
    return {delta for delta, owners in seen.items() if len(owners) >= 2}


def _explained_by_a_longer_name(segments, term, ids, extract, mentions):
    """The longer source run that explains a split, or ``None``.

    ``Eleanor Vance`` renders 艾蓮諾·凡斯 and ``Eleanor`` renders 艾蓮諾; the
    second is a part of the first and their supports are disjoint, which is
    exactly ``Marchmont``'s shape. What separates them is on the source side —
    the segments carrying the longer rendering all name a longer source run.

    Attribution removes this case whenever the longer run is already a term.
    This catches the case where it is not: a name every occurrence of which
    opens a sentence, which `cli.candidate_terms` suppresses by design.

    ``require_mid_sentence=False`` is why that parameter exists, and lowering
    ``min_count`` alone is not enough — measured 2026-09-07, ``Isolde Vance``
    named twice with both occurrences sentence-initial came back from
    ``candidate_terms(subset, min_count=1)`` as nothing and the split fired.
    Proposing a glossary row and explaining a split are different questions: a
    proposal may not be a sentence's first word, an explanation only has to be a
    longer run that exists.
    """
    subset = [seg for seg in segments if seg["id"] in ids]
    for row in extract(subset, 1, require_mid_sentence=False):
        if _covers(row["source"], term, mentions):
            return row["source"]
    return None


def _shared_core(renderings):
    """The longest substring every rendering carries; ``""`` when they share none."""
    core = renderings[0]
    for other in renderings[1:]:
        best = ""
        for i in range(len(core)):
            for j in range(i + len(best) + 1, len(core) + 1):
                if core[i:j] in other:
                    best = core[i:j]
        core = best
        if not core:
            break
    return core


def _shape(renderings):
    if len(renderings) > 2:
        return "several"
    return "overlapping" if _shared_core(renderings) else "distinct"


def run(segments, glossary, terms, extract, mentions):
    """The whole report. ``{findings, unexamined, no_finding, terms, segments, floors}``.

    ``terms`` is the source runs to ask about and ``extract`` is
    `cli.candidate_terms`; both are handed in rather than imported, because
    `cli.py` is the caller and importing it from here would be a cycle. Passing
    the extractor is also what keeps the term vocabulary one function: the
    capitalization heuristic, the sentence-initial suppression and the reading
    of ``masked`` are all decided in `cli.candidate_terms` and are not re-decided
    here.
    """
    mention, own = _attribute(segments, terms, mentions)
    targets = {s["id"]: s["target"] for s in segments if _translated(s)}
    live = set(targets)

    wanted, per_term = set(), {}
    for term in terms:
        ids = own[term] & live
        per_term[term] = ids
        if len(ids) >= MIN_SUPPORT:
            wanted |= set(_supported({i: targets[i] for i in ids}))
    index = _gram_index(segments, wanted)
    grammar = _grammar(terms, per_term, targets, mention, live, index)
    configured = {r["source"]: r.get("target") or "" for r in glossary}

    findings, unexamined, quiet = [], [], []
    for term in terms:
        ids = per_term[term]
        row = {"term": term, "renderings": [], "ids": [], "by_rendering": {},
               "unrendered": [], "occurrences": len(ids),
               "glossary": configured.get(term)}
        if len(ids) < MIN_SUPPORT:
            row["why"] = "named fewer than twice in translated text"
            unexamined.append(row)
            continue
        rendered, unrendered = _renderings(ids, targets, mention[term] & live,
                                           index, grammar)
        row["renderings"] = [g for g, _, _ in rendered]
        row["by_rendering"] = {g: sorted(s) for g, s, _ in rendered}
        row["ids"] = sorted(set().union(*[s for _, s, _ in rendered])
                            if rendered else set())
        row["unrendered"] = sorted(unrendered)
        if len(rendered) >= 2:
            longer = None
            for _gram, sids, was_split in rendered:
                if was_split and longer is None:
                    longer = _explained_by_a_longer_name(
                        segments, term, sids, extract, mentions)
            if longer is not None:
                row["explained_by"] = longer
                quiet.append(row)
                continue
            row["shape"] = _shape(row["renderings"])
            findings.append(row)
        elif len(ids) < MIN_SUPPORT + 1:
            row["why"] = ("named twice; two occurrences cannot separate a "
                          "rendering from a longer one that contains it")
            unexamined.append(row)
        else:
            quiet.append(row)

    findings.sort(key=lambda r: (SHAPES.index(r["shape"]), -r["occurrences"],
                                 r["term"]))
    unexamined.sort(key=lambda r: (-r["occurrences"], r["term"]))
    quiet.sort(key=lambda r: (-r["occurrences"], r["term"]))
    return {"findings": findings, "unexamined": unexamined, "no_finding": quiet,
            "terms": len(terms), "segments": len(live), "floors": list(FLOORS)}
