"""Automatic repair of mechanical defects.

The cheapest defect is the one that cannot be introduced. Punctuation width and
CJK/Latin spacing are fully decidable, so they are fixed on ingest rather than
reported as problems for a model to think about.
"""

import re

from .mask import CJK, CJK_RE, mask, unmask

_HALF_TO_FULL = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
FULLWIDTH = "，。！？；：「」『』（）【】《》、…—"

# A run of blanks that ends a line — and the CRLF case with it, because a
# document whose terminators arrived mixed keeps them inside the segment
# (``docio.split_terminator``), so a hard break there is ``  \r\n``. A lone CR
# is deliberately not matched: `docs/decisions.md`, 2026-07-28, classifies it as
# a character in a sentence rather than a line ending.
_LINE_END_BLANKS_RE = re.compile(r"[ \t]+(?=\r?\n)")

#: Asserts that the run about to be matched is preceded by something that is not
#: indentation. Prefixed to every op that could otherwise reach a run of blanks
#: at a line start, because such a run *is* indentation and indentation is inside
#: the segment by construction: it sits after a newline that is itself inside the
#: source, and a raw node can only go before or after a whole segment
#: (``mdparse``, ``textparse``, and ``docs/decisions.md`` 2026-07-28). AGENTS.md
#: invariant 3 records it as a deliberate exception; rewriting it is stripping it
#: by degrees. Two ops in this module are deliberately *not* prefixed, each with
#: its reason at the point of use.
#:
#: Verbatim, not canonicalized to some width. The hard break at the other end of
#: the line went the other way — two spaces and five mean one ``<br>``, so the
#: surplus is editor noise — and that reasoning does not transfer, because indent
#: widths are not interchangeable: four spaces are an indented code block where
#: one space is a paragraph.
#:
#: **The class is the whole design, and it took two measurements to get right.**
#: It has to fail on every character an indent can be made of, not only on ``\n``,
#: because a lookbehind that fails on ``\n`` alone is satisfied one character
#: *into* the indent: the engine starts the match on the second space and a
#: four-space indent comes out as two. So it excludes ASCII blanks — and then, for
#: the same reason, every other blank, because the run this op matches is
#: ``[ \t]+`` while the *indent* a translator writes need not be. A zh-TW
#: paragraph indent is U+3000, and a mixed ``　`` + spaces indent — what a
#: paste from a PDF, or a model padding a Chinese line to its source's column,
#: produces — kept its U+3000 and lost its spaces under ``[^ \t\n]``. Both
#: measured 2026-08-02, the first on the first version of this fix and the second
#: by adversarial review of it.
#:
#: ``\S`` covers U+3000, U+00A0, the form feed ``textparse`` separates chapters
#: on, and U+2028/U+2029. The price is that ``'a　  b'`` — an ideographic
#: space used *between words*, followed by ASCII blanks — no longer collapses.
#: One character of context cannot tell that from an indent, and the two failures
#: are not the same size: a surplus space is invisible, a deleted indent reflows a
#: code block into prose.
#:
#: ``\r`` is added back, so a run after a lone CR is interior text. That is
#: ``docio.split_terminator``'s classification — a lone CR is a character in a
#: sentence, not a line ending — and the same one ``_LINE_END_BLANKS_RE`` makes
#: with its ``\r?\n`` lookahead. Its *handler* does not: ``_line_end_blanks``
#: counts a lone CR as a line start, so ``'甲\r  \r\n乙'`` loses a hard break this
#: guard would call interior. Pre-existing, HANDOFF-010's to own, and left rather
#: than widened here, because a mixed-terminator document is the recorded
#: exception either way (``docs/decisions.md``, 2026-07-28).
#:
#: Position 0 is protected too, and it is a measured case rather than a rule
#: invented for symmetry: a list item's second paragraph is ``- item\n\n    text``,
#: so the four spaces that keep the paragraph inside the item sit at position 0 of
#: the segment. An indented code block used to arrive the same way and stopped on
#: 2026-08-02, when it became skeleton; the list item did not.
#:
#: The two callers reach position 0 differently since 2026-08-03, and only one of
#: them makes this guard redundant. `translate.accept` strips before calling here,
#: so what arrives at position 0 is already a non-blank character and the run it
#: will wear is put back afterwards by :func:`reseat_outer_blanks`. `cli.do_apply`
#: does neither: it passes the person's text through unstripped, and with
#: ``keep_added_indent=True`` a run the source has no counterpart for is *kept*
#: rather than replaced — so it reaches here, and this lookbehind is the only thing
#: standing between it and `collapse_space`. Measured by neutering the guard:
#: ``'  譯文。'`` applied to an unindented source stores ``' 譯文。'`` without it and
#: ``'  譯文。'`` with it, and four of five indent shapes differ the same way, while
#: nothing on `accept`'s path moves at all.
#:
#: So: load-bearing for `lx apply`, inert for `accept`, and the line-start half —
#: every run that follows a newline *inside* the segment, which is what
#: HANDOFF-011 was about — load-bearing for both. One regex, three jobs, and it
#: cannot be split.
_INTERIOR = r"(?<=[\S\r])"


def _line_end_blanks(m):
    """Decide what a run of blanks immediately before a line break becomes.

    Two or more spaces there are a Markdown hard break — a ``<br>`` the
    translation got right — so the run survives, canonicalized to exactly two.
    Three or more mean the same break and the surplus is precisely the editor
    noise this op exists to remove; preserving the run verbatim was the
    alternative and lost, because it keeps invisible byte-level variation
    between two renderings of one wording for no reader-visible gain
    (``docs/decisions.md``, 2026-07-29).

    Tabs are excluded by testing for spaces: CommonMark's hard break is *spaces*
    before the line ending, so a tab run is noise and goes. Turning it into two
    spaces would invent a break nobody wrote.

    A line that is nothing but blanks has no break to protect, and is emptied as
    before.
    """
    at_line_start = m.start() == 0 or m.string[m.start() - 1] in "\r\n"
    return "  " if not at_line_start and m.group().endswith("  ") else ""


def normalize_zh(text, ops):
    out = text
    if "punct" in ops:
        for half, full in _HALF_TO_FULL.items():
            out = re.sub(rf"(?<=[{CJK}]){re.escape(half)}(?=\s|$|[{CJK}])", full, out)
        out = re.sub(rf"(?<=[{CJK}])\.(?=\s*$|[{CJK}])", "。", out)
        out = re.sub(
            r"\(([^()]*)\)",
            lambda m: f"（{m.group(1)}）" if CJK_RE.search(m.group(1)) else m.group(0),
            out,
        )
        # Guarded, and this one is not theoretical: it is the rule that reaches a
        # continuation indent first and deletes it outright, where `collapse_space`
        # only shortens it. A zh-TW block set off by an indent — verse, an
        # epigraph, a quoted letter — opens its lines on 「, （, —— or …… more
        # often than not, and every one of those is in FULLWIDTH. Measured
        # 2026-08-02; HANDOFF-011 had this op out of scope on the claim that both
        # of `punct`'s whitespace rules need fullwidth punctuation *adjacent* to
        # the run, which is true and does not imply what it was read to imply.
        out = re.sub(rf"{_INTERIOR}[ \t]+(?=[{re.escape(FULLWIDTH)}])", "", out)
        # Not a run that ends a line. A zh-TW line ends in 。 far more often than
        # not, so this op — which runs before `collapse_space` gets a chance to
        # protect anything — is the one that actually deletes most hard breaks.
        # The guard has to span the whole run: with a bare `(?!\r?\n)` the match
        # backtracks to one space, and half a hard break is not one.
        # Unguarded on purpose. Its lookbehind already demands fullwidth
        # punctuation immediately before the run, and the character before a run
        # that begins a line is a newline, so it cannot reach one. Adding
        # `_INTERIOR` here would be a guard no test could ever turn red — which
        # is the definition of the redundant guard the mutation pass hunts for.
        out = re.sub(rf"(?<=[{re.escape(FULLWIDTH)}])[ \t]+(?![ \t]*\r?\n)", "", out)
    if "pangu" in ops:
        out = re.sub(rf"(?<=[{CJK}])(?=[A-Za-z0-9$])", " ", out)
        out = re.sub(rf"(?<=[A-Za-z0-9%$)])(?=[{CJK}])", " ", out)
    if "collapse_space" in ops:
        # Line ends first, so the interior collapse below only has to refuse to
        # cross one rather than decide what it means.
        out = _LINE_END_BLANKS_RE.sub(_line_end_blanks, out)
        # Three guards, one per end of the run. The lookahead: without it the sub
        # reaches straight past a surviving hard break and deletes it again.
        # `[ \t]*` inside it is redundant *given the pass above*, which leaves no
        # run longer than two before a line end — it is here so this sub is
        # correct read on its own, rather than only for a reader holding the
        # previous line in their head to see that a bare `(?!\r?\n)` cannot
        # backtrack below two. The lookbehind is the indent, and it composes with
        # the pass above rather than duplicating it: a run that begins a line and
        # ends one is a blanks-only line, which `_line_end_blanks` has already
        # emptied. The exception is a blanks-only *final* line with no terminator
        # after it — `_LINE_END_BLANKS_RE` needs a `\r?\n` to look at, so `'a\n  '`
        # reaches here indenting nothing. The guard blocks it anyway and the `\Z`
        # strip below removes it, which is the right answer by a different route.
        out = re.sub(_INTERIOR + r"[ \t]{2,}(?![ \t]*\r?\n)", " ", out)
        # `\Z`, not `$`: `$` also matches before a trailing newline, which is
        # where the hard break just rescued lives.
        #
        # Unguarded, and deliberately: a run that begins the segment's last line
        # with nothing after it indents nothing, which is the same judgement
        # `_line_end_blanks` makes about a line that is only blanks. Guarding it
        # would make `"item\n  "` keep two invisible trailing spaces.
        out = re.sub(r"[ \t]+\Z", "", out)
    return out


def reseat_outer_blanks(source, text, keep_added_indent=False):
    """``text``, wearing the whitespace runs ``source`` opens and closes with.

    Not an op, and deliberately not in :func:`normalize_zh`: it is unconditional,
    language-independent and driven by the segment rather than by config. It
    lives here because this module is where a deterministic repair goes
    (invariant 5) and because both callers already import from it — putting it in
    `translate.py` beside `accept` would make `cli.do_apply`, which the workbench
    calls on every save, import the provider stack to answer a question about
    whitespace.

    **What it exists for.** A run of blanks at position 0 of a segment is not
    padding, it is the segment's position in the document's structure: a list
    item's second paragraph is `- item\\n\\n    text`, and the four spaces are
    what keeps the paragraph inside the item. `accept` used to `.strip()` them
    off every proposal, which moved the paragraph out of the list — measured
    2026-08-02 against markdown-it-py across 441 generated shapes, 76 of which
    changed what the document *is* rather than how it looks. The trailing end is
    the same rule for a different reason: `mdparse` emits one segment per
    blockquote line, so the two spaces of a hard break between `> first` and
    `> second` sit at the end of a segment with the newline that gives them
    meaning outside it.

    **The runs come from the source, so a model that dropped one gets it back.**
    Preserving only a run the target already has was the alternative and it loses
    the case this is most likely to meet: a model asked to translate an indented
    paragraph answers with a sentence, not with a sentence wearing four spaces.
    Re-imposing is the same move `doc["eol"]` already makes — a fact about the
    document, applied once, never carried inside a segment where the model and
    the reviewer would both have to reproduce something invisible.

    ``str.strip()`` and its one-sided pair with no argument, deliberately: the
    set of characters preserved here has to be exactly the set `accept` deletes,
    and naming a class would be a second answer to one question. So U+3000 and
    U+00A0 — the zh-TW paragraph indent, and what a paste from EPUB leaves — are
    covered without being enumerated.

    **This is the only place either caller strips.** `accept` strips first as
    well, because its placeholder comparison and its emptiness test are about the
    sentence rather than the padding — but `cli.do_apply` does not, and a second
    strip there was measured redundant over 327600 combinations after the
    mutation pass found each of the two could be deleted alone. One place, so the
    contract holds however it is called: the blanks the result opens and closes
    with are the source's, whatever the input's were.

    ``keep_added_indent`` is `lx apply`'s, and it is the only place the two
    callers differ: where the source has **no** leading run, the target keeps its
    own. A model's answer has no business opening with blanks and the strict form
    is right for it — but a pair of U+3000 at the head of a paragraph is standard
    Traditional Chinese typography, an English source never has a run for it to
    be reseated from, and deleting it from a person's or an agent's words is
    neither reporting them at `lx check` nor refusing them at the door, which is
    what the 2026-07-29 decision allows. Found by adversarial review 2026-08-03,
    after the first version of this closed the asymmetry too far and left no
    surface anywhere in the pipeline able to produce an indented zh-TW paragraph.

    It is the *leading* run only. A trailing run is invisible in both hosts
    unless a line follows it in the skeleton, where it is a hard break — that is
    structure the source did not have, and structure is what `lx check` is for
    reporting. And where the source *does* have a lead, the source still wins:
    that run is the host's layout and a translator writing a deeper one turns a
    paragraph into something else. Both costs are recorded in
    ``docs/decisions.md``, 2026-08-03.

    A blank ``text`` is returned untouched rather than reseated. ``""`` is how
    `lx apply` clears a segment and how `render` decides to emit the untranslated
    marker; ``"    "`` is truthy and would render four spaces instead. The blank
    ``source`` guard has no caller that can reach it — both parsers refuse a
    block with no translatable text — and is kept rather than dropped because
    this is a public function shared by two modules, where "lead and trail are
    the same run" is a wrong answer rather than a missing one.
    """
    if not text.strip() or not source.strip():
        return text
    lead = source[: len(source) - len(source.lstrip())]
    trail = source[len(source.rstrip()):]
    if keep_added_indent and not lead:
        lead = text[: len(text) - len(text.lstrip())]
    return lead + text.strip() + trail


def ops_for(lang, cfg):
    return cfg.get("normalize", {}).get(lang, [])


def normalize(text, lang, cfg):
    """Applied on ingest, while placeholders are still in place."""
    ops = ops_for(lang, cfg)
    if ops and lang.lower().startswith("zh"):
        return normalize_zh(text, ops)
    return text


def polish_rendered(text, lang, cfg):
    """Applied after restoration, so boundaries around restored spans get spaced.

    Markup is re-masked first so nothing inside a code span or URL is touched.
    """
    ops = [o for o in ops_for(lang, cfg) if o == "pangu"]
    if not ops or not lang.lower().startswith("zh"):
        return text
    masked, slots = mask(text)
    return unmask(normalize_zh(masked, ops), slots)
