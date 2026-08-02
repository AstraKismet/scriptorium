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
#: invented for symmetry: ``mdparse`` has no indented-code-block branch, so
#: ``tests/corpus/indented-code-block.md`` arrives as a paragraph whose source
#: *starts* with the four spaces that make it code. ``translate.accept`` strips a
#: model's leading whitespace before this is reached, but ``cli.do_apply`` — a
#: person's or an agent's own words — does not. That asymmetry is not this
#: module's to fix and is scheduled as HANDOFF-018.
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
