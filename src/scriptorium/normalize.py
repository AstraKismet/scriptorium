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
        out = re.sub(rf"[ \t]+(?=[{re.escape(FULLWIDTH)}])", "", out)
        # Not a run that ends a line. A zh-TW line ends in 。 far more often than
        # not, so this op — which runs before `collapse_space` gets a chance to
        # protect anything — is the one that actually deletes most hard breaks.
        # The guard has to span the whole run: with a bare `(?!\r?\n)` the match
        # backtracks to one space, and half a hard break is not one.
        out = re.sub(rf"(?<=[{re.escape(FULLWIDTH)}])[ \t]+(?![ \t]*\r?\n)", "", out)
    if "pangu" in ops:
        out = re.sub(rf"(?<=[{CJK}])(?=[A-Za-z0-9$])", " ", out)
        out = re.sub(rf"(?<=[A-Za-z0-9%$)])(?=[{CJK}])", " ", out)
    if "collapse_space" in ops:
        # Line ends first, so the interior collapse below only has to refuse to
        # cross one rather than decide what it means.
        out = _LINE_END_BLANKS_RE.sub(_line_end_blanks, out)
        # The lookahead is the whole fix on this line: without it the sub reaches
        # straight past a surviving hard break and deletes it again. `[ \t]*` is
        # redundant *given the pass above*, which leaves no run longer than two
        # before a line end — it is here so this sub is correct read on its own,
        # rather than only for a reader holding the previous line in their head
        # to see that a bare `(?!\r?\n)` cannot backtrack below two.
        out = re.sub(r"[ \t]{2,}(?![ \t]*\r?\n)", " ", out)
        # `\Z`, not `$`: `$` also matches before a trailing newline, which is
        # where the hard break just rescued lives.
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
