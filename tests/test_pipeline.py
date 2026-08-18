"""Round-trip and validator tests. No network, no model."""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.checks import check_segment, containment_problems  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG, load_dnt  # noqa: E402
from scriptorium.mask import (  # noqa: E402
    mask,
    placeholder_ids,
    repair_placeholders,
    reseat,
    unmask,
)
from scriptorium.mdparse import parse, render  # noqa: E402
from scriptorium.normalize import normalize, reseat_outer_blanks  # noqa: E402
from scriptorium.translate import _LANG_TERMS, _system_prompt, accept, parse_reply  # noqa: E402

SAMPLE = """\
---
title: Guide
---

# Deployment Guide

The **Celurion** server requires Go 1.22 and `postgres`.

| Option | Default |
| --- | --- |
| `port` | 8080 |

1. Clone from [the repo](https://example.com/x).
2. Run `make build`.

> Never commit secrets.

```python
print("keep me")
```

Formula $E = mc^2$ stays.
"""

CFG = dict(DEFAULT_CONFIG)


def test_identity_roundtrip_without_translation():
    nodes, segs = parse(SAMPLE, ["Celurion", "Go"])
    for s in segs:
        s["target"] = s["masked"]          # translate to itself
    doc = {"nodes": nodes, "segments": segs, "lang": "zh-TW"}
    out, missing = render(doc, CFG)
    assert missing == 0
    assert out == SAMPLE


def test_code_and_frontmatter_never_become_segments():
    _nodes, segs = parse(SAMPLE, [])
    joined = " ".join(s["source"] for s in segs)
    assert "keep me" not in joined
    assert "title: Guide" not in joined


# --- an indented code block is code, not a paragraph -------------------------
#
# `mdparse` had no branch for one, so a chunk indented by four spaces fell
# through to the paragraph branch and the model was asked to translate Python.
# Worse: the four spaces that *make* it code sat at position 0 of the segment,
# where `translate.accept` stripped the leading whitespace off every proposal it
# took — and a translation-memory hit comes through `accept` too, so a target a
# person applied with its indent intact was banked with the indent and handed
# back without it on the next extract. `test_memory.py` owns that cycle.
#
# That second half was repaired on its own on 2026-08-03, because a list item's
# second paragraph reaches position 0 the same way and is not code. See
# "the blanks a segment opens and closes with belong to the source" below.
#
# The rule implemented is CommonMark's, not a four-space test. An indented chunk
# is code only where a paragraph could not be continued lazily, and only past the
# content column of any list item it is sitting inside. Either one wrong in the
# permissive direction turns prose into skeleton and stops translating it, which
# is worse than the defect being repaired — so the must-not-fire list below is
# the longer of the two on purpose. Every row in both was confirmed against a
# real CommonMark render (markdown-it-py, 2026-08-02), and the two rows that
# deliberately disagree with it say so.


def test_an_indented_code_block_is_skeleton_and_never_reaches_the_model():
    """Derived from the fixture, never quoted from it, so it keeps testing the file."""
    text = (CORPUS / "indented-code-block.md").read_bytes().decode("utf-8")
    nodes, segs = parse(text)
    lines = text.split("\n")
    indented = [ln for ln in lines if ln[:1] in (" ", "\t")]
    assert indented, ("indented-code-block.md no longer contains an indented line, "
                      "so this test measures nothing — fix the test, not the fixture")
    joined = "\n".join(s["source"] for s in segs)
    raw = "".join(n["v"] for n in nodes if n["t"] == "raw")
    for line in indented:
        assert line not in joined, line       # never offered to the model
        assert line in raw, line              # and reproduced byte for byte
    # The prose around it is still translated. A "fix" that swallowed the whole
    # file into the skeleton would pass both assertions above and nothing else.
    assert [ln for ln in lines if ln and ln[:1] not in (" ", "\t")] \
        == [s["source"] for s in segs]


@pytest.mark.parametrize("text, code", [
    ("Paragraph.\n\n    def x():\n        return 1\n", "def x():"),
    # A heading, a setext underline and a thematic break each close the
    # paragraph, so no blank line is needed to open code after one.
    ("# Heading\n    def x():\n", "def x():"),
    ("Title\n=====\n    def x():\n", "def x():"),
    ("Text.\n\n***\n\n    def x():\n", "def x():"),
    ("| h |\n| --- |\n| c |\n\n    def x():\n", "def x():"),
    # Nothing before it at all.
    ("    def x():\n", "def x():"),
    # One tab is four columns, not one character of indent. This is the row that
    # fails when the width is taken as `len(line) - len(line.lstrip())`.
    ("\tdef x():\n", "def x():"),
    ("  \tdef x():\n", "def x():"),
    # Eight columns clears a `- ` item's content column by four.
    ("- item\n\n        deep code\n", "deep code"),
    # A chunk ends at the first line below its floor, and the prose after it is
    # a separate block that is still translated.
    ("    def x():\nParagraph after it.\n", "def x():"),
    # Two chunks either side of a blank line. CommonMark calls that one code
    # block and this calls it two; both are raw, so the bytes agree.
    ("Text.\n\n    print('a')\n\n    print('b')\n", "print('b')"),
    # A block at the left margin closes the list item above, so the floor drops
    # back to four. Without that, one list anywhere in a document keeps every
    # later code block translatable — which is most technical documentation, and
    # is the defect quietly coming back.
    ("- item\n\nParagraph at the margin.\n\n    def x():\n", "def x():"),
    # A chunk inside a list item runs while its own floor holds, not while four
    # columns hold. The next row is the other half: the line below it is the
    # item's prose and must survive.
    ("- item\n\n        deep code\n    a shallower line of the item.\n", "deep code"),
    # A CRLF document reaches `parse` as LF text when its terminators are
    # uniform, but a *mixed* one keeps its CRs, and a CR at the end of a line is
    # a terminator rather than text. Those lines are still code.
    ("Para.\r\n\r\n    def x():\r\n        return 1\r\n\r\nClose.\n", "def x():"),
    # The must-still-be-code half of the four guards below. A thematic break
    # with nothing above it is still a break; a link definition that has a
    # destination is still a definition; and a line Python calls blank but
    # CommonMark does not opens a paragraph that a *real* blank then closes.
    ("---\n    def x():\n", "def x():"),
    ("[x]: https://example.invalid\n    def x():\n", "def x():"),
    ("First paragraph.\n　\n\n    def x():\n", "def x():"),
    # A heading may interrupt a paragraph, and it closes it. The branches that
    # say nothing about the paragraph state — heading, fence, table, and a chunk
    # itself — rely on the reset at the top of the loop to close it for them, and
    # this row is the only one where that reset is observable.
    ("Paragraph.\n# Heading\n    def x():\n", "def x():"),
], ids=["after-a-paragraph", "after-a-heading", "after-a-setext-underline",
        "after-a-thematic-break", "after-a-table", "at-the-start-of-the-file",
        "one-tab", "two-spaces-and-a-tab", "inside-a-list-item",
        "prose-directly-below", "across-a-blank-line",
        "a-margin-block-closes-the-item", "a-chunk-stops-at-its-own-floor",
        "a-crlf-terminator-is-not-text", "a-thematic-break-underlines-nothing",
        "a-link-definition-with-a-destination", "a-real-blank-still-closes",
        "after-a-heading-that-interrupted-a-paragraph"])
def test_an_indented_chunk_that_is_code_leaves_no_segment_behind(text, code):
    assert code not in "\n".join(s["source"] for s in parse(text)[1])
    assert identity_roundtrip(text) == text


@pytest.mark.parametrize("text, prose", [
    # CommonMark's own words: an indented code block cannot interrupt a
    # paragraph. These two are the headline case and its tab spelling.
    ("A paragraph line.\n    a lazy continuation.\n", "a lazy continuation."),
    ("A paragraph line.\n\ta tab lazy continuation.\n", "a tab lazy continuation."),
    # The paragraph branch stops at anything that looks like a block start at any
    # indent, so these three reach the block dispatch while CommonMark is still
    # inside one paragraph. They are why the rule is state and not a claim about
    # which lines the paragraph branch happens to reach.
    ("Paragraph.\n    - a list-looking lazy line.\n", "a list-looking lazy line."),
    ("Paragraph.\n    > a quote-looking lazy line.\n", "a quote-looking lazy line."),
    ("Paragraph.\n    1. an ordered lazy line.\n", "an ordered lazy line."),
    # A blockquote is emitted one line at a time, so its lazy continuations reach
    # the dispatch too.
    ("> quoted line\n    a lazy quote continuation.\n", "a lazy quote continuation."),
    # Four columns is only two past a `- ` item's content column, so this is the
    # item's second paragraph and not code.
    ("- item\n\n    a second paragraph of the item.\n",
     "a second paragraph of the item."),
    ("- item\n\n   three spaces only.\n", "three spaces only."),
    ("1. item\n\n    still inside the item.\n", "still inside the item."),
    ("  - item\n\n      inside the indented item.\n", "inside the indented item."),
    ("- item\n      - a deeply indented nested item.\n",
     "a deeply indented nested item."),
    # One tab is four columns and not eight, so it does not clear a `- ` item's
    # floor of six. `\tdef x():` at the margin is code either way and separates
    # nothing — this is the row that does.
    ("- item\n\n\ta tab is four columns.\n", "a tab is four columns."),
    # The other half of `a-chunk-stops-at-its-own-floor`: the line below the deep
    # chunk is four columns, which is the item's prose and not code.
    ("- item\n\n        deep code\n    a shallower line of the item.\n",
     "a shallower line of the item."),
    # A tab inside the list marker itself. `len(prefix)` scores `-\t` as two
    # columns and puts the floor at six; the marker actually ends at column four,
    # so the floor is eight and these six columns are the item's second paragraph.
    ("-\titem\n\n      six columns is below the floor.\n",
     "six columns is below the floor."),
    # A line that looks like a list at the margin does not close the item above
    # it — and this one is not handled by the list branch at all, because the
    # table branch is tested first and claims it. Obscure, and the only shape
    # that separates the two spellings of the closing rule.
    ("- item\n\n- | a |\n| --- |\n| b |\n\n    still inside the item.\n",
     "still inside the item."),
    # Below the threshold at the left margin.
    ("Text.\n\n   only three spaces.\n", "only three spaces."),
    # Only a space and a tab indent. U+3000 is the zh-TW paragraph indent and
    # U+00A0 is what a paste from a word processor leaves; `str.lstrip()` eats
    # both and would score these four columns. The form feed separates chapters
    # in an older .txt and is in `tests/corpus-text/` for that reason.
    ("　　　　An ideographic indent is not four columns.\n",
     "An ideographic indent is not four columns."),
    ("\xa0\xa0\xa0\xa0A no-break space indent is not four columns.\n",
     "A no-break space indent is not four columns."),
    ("\x0c   A form feed is not indentation.\n", "A form feed is not indentation."),
    # The four found by adversarial review on 2026-08-02, after a 37224-shape
    # sweep had reported zero. Each is a line the *dispatch* misreads, so each
    # was invisible to a sweep that varied the block above the chunk without
    # varying that block's own shape. They are ordinary hand-written Markdown.
    #
    # A list item whose text wraps to the left margin: the continuation is at
    # column 0 and does not close the item, so the second paragraph is still
    # measured against the item's floor of six and not against four.
    ("- item wraps and\ncontinues at the left margin.\n"
     "\n    a second paragraph of the item.\n", "a second paragraph of the item."),
    # A line that is blank to `str.strip()` and not to CommonMark. It is content,
    # so it opens a paragraph rather than merely keeping one open — the heading
    # row is the degenerate end that refuted the first spelling of this guard.
    ("First paragraph.\n　\n    an indented continuation.\n",
     "an indented continuation."),
    ("First paragraph.\n\xa0\n    an indented continuation.\n",
     "an indented continuation."),
    ("# Heading\n　\n    an indented continuation.\n", "an indented continuation."),
    # `=====` and `--` underline nothing when no paragraph is open, so CommonMark
    # reads them as paragraph text and the indented line as their continuation.
    ("=====\n    indented prose after it.\n", "indented prose after it."),
    ("--\n    indented prose after it.\n", "indented prose after it."),
    # A link definition with no destination is not a link definition.
    ("[x]:\n    indented prose after it.\n", "indented prose after it."),
    # A CR-only document — Classic Mac OS — is *one* line to `str.split("\n")`,
    # because this parser treats a lone CR as text rather than as a terminator
    # (`docs/decisions.md`, 2026-07-28). CommonMark calls it two lines, a code
    # block and a paragraph. The parser cannot know where the chunk ends, so it
    # declines to make any of it skeleton and the prose stays translatable.
    ("    def x():\rprose after a bare carriage return\r",
     "prose after a bare carriage return"),
    # The same guard on a chunk's *continuation* line rather than its first. The
    # chunk loop starts at `i + 1`, so the opening line's copy of this test can
    # no longer stand in for this one.
    ("Para.\n\n    def a():\n    b\rprose after a text CR\n",
     "prose after a text CR"),
    # The two that knowingly disagree with CommonMark, both conservative.
    #
    # A bare `>` closes the paragraph inside a blockquote, so CommonMark opens a
    # code block here. This parser has no container stack and cannot measure an
    # indent against a blockquote's content column: reading the bare `>` that way
    # fixed 285 generated shapes and turned prose into skeleton in 57, because
    # `> q\n>\n    > x` is still inside the quote where `> q\n>\n    y` is not.
    ("> quoted line\n>\n    still not code here.\n", "still not code here."),
    # And the floor is the whole list prefix, checkbox included, where
    # CommonMark's content column stops after the marker.
    ("- [ ] task\n\n      six spaces after a checkbox.\n",
     "six spaces after a checkbox."),
], ids=["lazy-continuation", "lazy-continuation-by-tab", "lazy-looks-like-a-list",
        "lazy-looks-like-a-quote", "lazy-looks-like-an-ordered-item",
        "lazy-continuation-of-a-quote", "second-paragraph-of-an-item",
        "three-spaces-in-an-item", "ordered-item-content-column",
        "indented-item-content-column", "deeply-indented-nested-item",
        "a-tab-is-four-columns", "a-shallower-line-below-a-chunk",
        "a-tab-inside-the-list-marker", "a-margin-list-the-table-branch-claims",
        "three-spaces-at-the-margin", "ideographic-space", "no-break-space",
        "form-feed",
        "a-lazy-continuation-does-not-close-the-item",
        "an-ideographic-space-line-is-not-blank",
        "a-no-break-space-line-is-not-blank",
        "a-content-line-opens-a-paragraph-after-a-heading",
        "an-equals-run-that-underlines-nothing",
        "a-dash-run-that-underlines-nothing",
        "a-link-definition-with-no-destination",
        "a-bare-carriage-return-inside-the-line",
        "a-text-cr-on-a-continuation-line",
        "bare-quote-marker", "task-list-checkbox"])
def test_an_indented_chunk_that_is_prose_is_still_translated(text, prose):
    assert prose in "\n".join(s["source"] for s in parse(text)[1])
    assert identity_roundtrip(text) == text


@pytest.mark.parametrize("name, count", [
    ("list-continuation-indent.md", 3),          # four spaces, two, and an ordered three
    ("list-continuation-two-space-indent.md", 2),
    ("crlf-list-items.md", 3),
    ("nested-lists.md", 14),                     # three levels, and `    - third`
    ("blockquote-nested.md", 4),
    ("html-block.md", 5),
    ("mixed-blocks.md", 8),
    # 112k characters of real documentation. A rule that is wrong anywhere in the
    # permissive direction moves this number, and the round-trip property cannot
    # see it: `tests/corpus/` substitutes each segment's *source* back into the
    # skeleton, so a block that stopped being translated round-trips perfectly.
    ("long-manual.md", 1572),
    # The two HANDOFF-020 added. Pinned here as well as in their own tests
    # below, because this is the list a later change reads when it wants to know
    # what it moved.
    # 10 rather than 8: a quoted list item's deep chunk is deliberately still
    # translatable, see `_QUOTED_PROSE`.
    ("blockquote-indented-code.md", 10),
    ("indented-fence-run.md", 8),
    # And the two HANDOFF-021 added. Both are almost entirely *recovered* prose:
    # under the parent build the first segmented 5 blocks of 9 and the second 18
    # of a different 16, because a marker that is not a marker cuts a paragraph
    # in two and then closes it.
    ("fence-info-string-backtick.md", 9),
    ("block-marker-whitespace.md", 16),
    # The one pre-existing fixture this package moves, and it moves because a
    # defect older than the package was repaired: a closer must be at least as
    # long as its opener, so `````\n```\n…\n`````  is one fence rather than two
    # and its body stops being translated. 3 before, and markdown-it-py agrees
    # with the 1.
    ("fences-and-unclosed.md", 1),
    # And the one HANDOFF-022 added, which HANDOFF-024 grew the label half of.
    # 6 of the 15 it had were the tail's near misses and their lazy
    # continuations, none of which the parent emitted at all: it read each
    # candidate line as a definition and took the indented line under it as
    # code. The 8 new ones are the label's near misses, recovered the same way —
    # one segment each, the candidate line plus the line below it.
    ("link-definition-tails.md", 23),
])
def test_the_indented_code_rule_moved_no_other_fixture_s_segment_count(name, count):
    text = (CORPUS / name).read_bytes().decode("utf-8")
    got = [s["source"] for s in parse(text)[1]]
    assert len(got) == count, got[:5]


# --- the two containers HANDOFF-018 left behind ------------------------------
#
# Both measured 2026-08-03 at `ade9fa9`, both fixed here, and neither is visible
# to `tests/corpus/`: that harness substitutes each segment's *source* back into
# the skeleton, so a block that stopped being translated round-trips perfectly
# and a block handed to the model round-trips perfectly too. The measurement has
# to be on the segment set and on the target side, which is what these do.
#
# *A blockquote's interior was not measured at all.* `> intro\n>\n>     def x():`
# put `    def x():` in a segment and asked the model to translate Python.
# `mdparse` emits one segment per quoted line and never descends into the quote,
# so the fix is a content column read after the `>` marker plus the quote's own
# paragraph state — the bare `>` between the two lines is the whole difference
# between a code block and a lazy continuation.
#
# *An indented fence run swallowed prose to end of file.* `FENCE_RE` matched at
# any indent, so `    ```` ` was claimed by the fence branch, and with nothing to
# close it the branch consumed the rest of the document into the skeleton. A
# fence's indentation is bounded at three columns past its container's content
# column, which is `code_floor` minus one.
#
# Every row below was confirmed against a real CommonMark render (markdown-it-py,
# 2026-08-03), and the three that knowingly disagree with it say so.


#: The two halves of `blockquote-indented-code.md`, named rather than derived.
#: Deriving them means re-implementing the rule under test inside the test, and
#: this fixture holds prose indented exactly as far as its code — which is the
#: point of it, and what no mechanical filter can separate without being the
#: parser. Naming them costs a check that the fixture still contains each line,
#: below, so a fixture edit fails loudly instead of quietly measuring nothing.
_QUOTED_CODE = [
    ">     def quoted():",
    ">         return 'four columns past the marker'",
    ">\t\tdef tabbed():",
    ">\t\t\treturn 'two tabs past the marker'",
]
_QUOTED_PROSE = [
    "    a lazy continuation of the sentence above it.",
    "    Four columns is below this item's floor, so it is a second paragraph.",
    # The repair this package deliberately does not make, kept in the fixture so
    # the trade is visible rather than merely written down. Six columns clears
    # CommonMark's floor for this item and *is* a code block there — but the
    # quote's interior tracks an open list as a boolean rather than a column, so
    # nothing inside a quoted list is code. Six of the eight regressions
    # adversarial review found on 2026-08-03 lived in that column's arithmetic,
    # and every one of them lost prose; this costs a visible translated code
    # block instead. `docs/decisions.md` of that date has the reasoning.
    "      def deep():",
    "          return 'six columns clears the floor'",
]


def test_a_quoted_chunk_is_skeleton_and_never_reaches_the_model():
    text = (CORPUS / "blockquote-indented-code.md").read_bytes().decode("utf-8")
    lines = text.split("\n")
    for line in _QUOTED_CODE:
        assert line in lines, (line, "blockquote-indented-code.md no longer holds "
                                     "this line — fix the test, not the fixture")
    nodes, segs = parse(text)
    joined = "\n".join(s["source"] for s in segs)
    raw = "".join(n["v"] for n in nodes if n["t"] == "raw")
    for line in _QUOTED_CODE:
        assert line.split(">", 1)[1].lstrip(" \t") not in joined, line
        assert line in raw, line                  # reproduced byte for byte
    # …and the prose beside it, indented by exactly as much, is still translated.
    # A "fix" that swallowed the whole quote into the skeleton passes every
    # assertion above and nothing else.
    for line in _QUOTED_PROSE:
        assert line in joined, line


#: The two halves of `link-definition-tails.md`, named rather than derived, for
#: the reason `_QUOTED_CODE` is: deriving them means re-implementing the rule
#: under test inside the test. The whole point of the fixture is that the two
#: halves are indistinguishable by shape — every line in both is `[label]: …` —
#: so a mechanical filter that could separate them would be the parser.
_REAL_DEFINITIONS = [
    "[plain]: https://example.invalid/a",
    '[titled]: https://example.invalid/b "With a double-quoted title"',
    "[single]: https://example.invalid/c 'and a single-quoted one'",
    "[parend]: https://example.invalid/d (and a parenthesized one)",
    '[angled]: </a destination with a space> "which only the angle form allows"',
    "[balanced]: https://example.invalid/e(1)",
    "[escaped]: https://example.invalid/f\\(1",
    "[ideographic]:　https://example.invalid/g",
    "[bare]: destination",
    # The label half, HANDOFF-024. A label may hold spaces and CJK, and a
    # backslash in it consumes what follows — so the first of these two closes
    # at its *second* `]`, and the second holds a `[` that would refuse the
    # label were it not escaped.
    "[a label with spaces]: https://example.invalid/h",
    "[標籤]: https://example.invalid/i",
    "[a\\]b]: https://example.invalid/j",
    "[a\\[b]: https://example.invalid/k",
    # A backslash before a space is not an escape — CommonMark escapes only
    # ASCII punctuation — so the label is a literal backslash and a space, and a
    # backslash is not whitespace. The label is therefore not blank.
    "[\\ ]: https://example.invalid/l",
]
_NEAR_MISSES = [
    "[x]: /url not a title",
    "[Ana]: Hello there, she said.",
    "[spaced]: /two words",
    "[unclosed]: <a destination that never closes",
    '[junk]: /url "a title" and then some junk',
    "[nested]: /url (a (nested) title)",
    "[unbalanced]: /url(1",
    "[empty]:",
    # The label half, HANDOFF-024: a line whose *tail* is a perfectly good
    # destination and whose label is not a label. Each one cost the line and the
    # indented line under it, because reading it as a definition closed the
    # paragraph above.
    "[ ]: /url",
    "[　]: /url",
    "[a[b]: /url",
    "[[a]: /url",
    "[a\\]: /url",
    # The three that were already right, kept so a narrowing cannot overshoot
    # them into definitions.
    "[]: /url",
    "[a]b: /url",
    "[[a]]: /url",
    # Not a tail question at all: a definition may not interrupt a paragraph, so
    # this one is well-formed and is still prose.
    "[lazy]: /url",
]


def test_a_line_that_only_looks_like_a_link_definition_is_still_translated():
    text = (CORPUS / "link-definition-tails.md").read_bytes().decode("utf-8")
    lines = text.split("\n")
    for line in _REAL_DEFINITIONS + _NEAR_MISSES:
        assert line in lines, (line, "link-definition-tails.md no longer holds "
                                     "this line — fix the test, not the fixture")
    nodes, segs = parse(text)
    joined = "\n".join(s["source"] for s in segs)
    raw = "".join(n["v"] for n in nodes if n["t"] == "raw")
    for line in _REAL_DEFINITIONS:
        assert line not in joined, line
        assert line in raw, line                  # reproduced byte for byte
    # …and the half that only looks like one, which is what a repair aimed at
    # the first half alone would swallow. Both the candidate line and the
    # indented line under it, since a closed paragraph is what turns the second
    # one into code.
    for line in _NEAR_MISSES:
        assert line in joined, line
    assert "A bare word cannot be a title." in joined
    assert "An empty destination is no destination at all." in joined
    # …and the same for the label half, whose near misses cost the line below
    # them the same way.
    assert "A label holding no non-whitespace character is not a label." in joined
    assert ("A backslash consumes the bracket, so this label never closes at "
            "all." in joined)
    # The one indented line that *is* code, because the definition above it is a
    # real one. Without this the test passes for a parser that gave up on the
    # rule entirely.
    assert "A definition closes the paragraph above it" not in joined


def test_an_indented_fence_run_is_a_chunk_and_the_prose_below_it_survives():
    """Derived from the fixture, never quoted from it."""
    text = (CORPUS / "indented-fence-run.md").read_bytes().decode("utf-8")
    nodes, segs = parse(text)
    lines = text.split("\n")
    runs = [ln for ln in lines if ln.strip()[:3] in ("```", "~~~")]
    assert len(runs) >= 5, ("indented-fence-run.md no longer contains the runs "
                            "this measures — fix the test, not the fixture")
    joined = "\n".join(s["source"] for s in segs)
    raw = "".join(n["v"] for n in nodes if n["t"] == "raw")
    for line in runs:
        assert line not in joined, line
        assert line in raw, line
    # The prose an unbounded `\s*` used to swallow, at both ends of the file.
    assert "Ordinary prose that must stay translatable." in joined
    assert "Prose after the list." in joined


# --- the two shapes HANDOFF-020 left behind, both measured 2026-08-03 ---------
#
# Neither is a regression from that package — both fail identically at `c86363b`
# and at `e3399d8` — and neither is visible to `tests/corpus/`, for the reason
# written above: that harness substitutes each segment's *source* back into the
# skeleton, so a block that stopped being translated round-trips perfectly. Each
# costs an entire document, silently: `lx check` exits 0 and nothing says the
# text was never translated.


#: The two halves of `fence-info-string-backtick.md`, named rather than derived.
#: Separating them mechanically means re-implementing the rule under test inside
#: the test — which of these runs opens a fence is the entire question. Naming
#: them costs a check that the fixture still holds each line, below, so a fixture
#: edit fails loudly instead of quietly measuring nothing.
_INFO_STRING_PROSE = ["```js`", "``` text `"]
_INFO_STRING_CODE = ["~~~js`", "````text"]


def test_a_backtick_in_an_info_string_is_not_a_fence():
    text = (CORPUS / "fence-info-string-backtick.md").read_bytes().decode("utf-8")
    lines = text.split("\n")
    for line in _INFO_STRING_PROSE + _INFO_STRING_CODE:
        assert line in lines, (line, "fence-info-string-backtick.md no longer holds "
                                     "this line — fix the test, not the fixture")
    nodes, segs = parse(text)
    seg_lines = {ln for s in segs for ln in s["source"].split("\n")}
    raw = "".join(n["v"] for n in nodes if n["t"] == "raw")
    # CommonMark forbids a backtick in a backtick fence's info string, so these
    # are paragraph text and the model has to see them.
    for line in _INFO_STRING_PROSE:
        assert line in seg_lines, line
    # …and the tilde spelling carries no such restriction, so this one is a real
    # fence. That asymmetry is the whole risk of the repair: a rule applied to
    # both spellings would hand every tilde-fenced code block to the model.
    for line in _INFO_STRING_CODE:
        assert line not in seg_lines, line
        assert line in raw, line
    # The prose the unclosed run used to swallow, at both ends of the file.
    assert "The paragraph that used to vanish, and the rest of the file with it." in seg_lines
    assert "Prose after every fence." in seg_lines
    # …and the three fenced bodies that must stay out of the model's hands.
    for body in ("held in the skeleton", "also held in the skeleton",
                 "held in the skeleton too"):
        assert body not in seg_lines, body
        assert body in raw, body


#: `block-marker-whitespace.md`, split the same way and for the same reason.
#: Whether a line is a marker at all is what the fixture measures.
_NOT_A_MARKER = [
    "\t# Not a heading",
    "　# Not a heading either",
    "#　Not a heading, an ordinary paragraph",
    "***　",
    "\t***",
    "===　",
    "　[not-a-ref]: /url",
]
#: Lines that are wholly skeleton. A real heading is deliberately absent: its
#: marker is raw and its text is a segment, so the *line* is neither, and the
#: three of them are pinned by kind below instead.
_STILL_A_MARKER = [
    "***",
    "=====================",
    '[real]: /url "with a title"',
    "     [kept]: /url",
]


def test_a_marker_s_indent_is_measured_in_columns_not_characters():
    text = (CORPUS / "block-marker-whitespace.md").read_bytes().decode("utf-8")
    lines = text.split("\n")
    for line in _NOT_A_MARKER + _STILL_A_MARKER:
        assert line in lines, (line, "block-marker-whitespace.md no longer holds "
                                     "this line — fix the test, not the fixture")
    nodes, segs = parse(text)
    seg_lines = {ln for s in segs for ln in s["source"].split("\n")}
    raw = "".join(n["v"] for n in nodes if n["t"] == "raw")
    for line in _NOT_A_MARKER:
        assert line in seg_lines, line
    for line in _STILL_A_MARKER:
        assert line not in seg_lines, line
        assert line in raw, line
    # Three real headings, and a heading is still cut the way it always was.
    assert [s["source"] for s in segs if s["kind"] == "heading"] == [
        "A real heading",
        "A real heading three columns in",
        "A real heading whose hashes are followed by a tab",
    ]
    # Every line the four markers used to take into the skeleton with them. The
    # four spaces are part of the assertion: they are what made each of these an
    # indented code block once the marker above had closed the paragraph.
    for prose in ("    Prose that used to vanish beneath it.",
                  "    Prose that used to vanish beneath this one.",
                  "    And its own lazy continuation.",
                  "    Prose below a break that is not a break.",
                  "    Prose below a tab-indented break.",
                  "    Prose below an underline that is not an underline.",
                  "    Prose below a definition that is not one."):
        assert prose in seg_lines, prose


def test_a_marker_that_is_not_one_cannot_become_one_through_the_target():
    """The risk the narrowing takes on, and the two mechanisms that cover it.

    Before HANDOFF-021 a line like `　# 標題` was cut as a *heading* segment, so
    its `　# ` sat in the skeleton and no translation could reach it — the
    structure was safe by construction. Now the whole line is a paragraph segment
    and the model sees the hash. What stops it inventing an `<h1>` is not one
    thing but two, and which one applies depends on where the U+3000 is.

    Measured 2026-08-03 rather than argued, because "the neighbour a repair
    silently blinds" is the failure HANDOFF-020's adversarial pass found twice.
    """
    # (1) At position 0 the run is the segment's place in the structure, so it is
    # re-imposed from the source on every proposal — `translate.accept` and
    # `cli.do_apply` share this. `str.strip()` with no argument is what covers
    # U+3000 without enumerating it, which is why the reseat reaches this case.
    src = parse("　# 中文標題\n    後面的散文\n")[1][0]["source"]
    assert src.startswith("　#"), src
    for damaged in (" # 中文標題\n    後面的散文",     # full-width to half-width
                    "# 中文標題\n    後面的散文"):     # dropped outright
        assert reseat_outer_blanks(src, damaged).startswith("　#")

    # (2) Between the hashes and the text there is nothing to re-impose — that
    # run is interior — so the *check* has to catch it, and does. This is the
    # assertion that fails if `checks.py` ever stops importing `mdparse`'s own
    # patterns, or if `HEADING_RE` widens back.
    seg = parse("#　中文標題\n    後面的散文\n")[1][0]
    assert seg["kind"] == "para", seg["kind"]
    seg["target"] = seg["masked"].replace("　", " ", 1)
    assert containment_problems(seg) == [
        "the target opens a heading; the source does not"]


@pytest.mark.parametrize("text, body", [
    # --- Defect B: an indented run of fence characters is a chunk, not a fence.
    ("Para.\n\n    ```\n    body of the chunk\nProse after.\n", "body of the chunk"),
    ("Para.\n\n    ~~~\n    body of the chunk\nProse after.\n", "body of the chunk"),
    ("Para.\n\n    ````\n    body of the chunk\nProse after.\n", "body of the chunk"),
    # …and the must-still-be-a-fence half, which is the longer risk. Three
    # columns is CommonMark's bound at the margin, and inside a list item the
    # bound moves with the item — a fence indented into one is still a fence, and
    # a rule spelled `\s{0,3}` absolutely would put this code in front of the
    # model.
    ("Text.\n\n   ```\n   body of the chunk\n   ```\n\nProse.\n", "body of the chunk"),
    ("- item\n\n  ```\n  body of the chunk\n  ```\n", "body of the chunk"),
    ("1. item\n\n   ```\n   body of the chunk\n   ```\n", "body of the chunk"),
    ("  - item\n\n    ```\n    body of the chunk\n    ```\n", "body of the chunk"),
    # An unclosed fence at the margin still reaches end of file, which is both
    # CommonMark's answer and what `tests/corpus/fences-and-unclosed.md` pins.
    ("```\nbody of the chunk\n", "body of the chunk"),
    (" ```\nbody of the chunk\n", "body of the chunk"),
    # A margin fence under an open list item is not inside the item, so the
    # container bound on an unclosed run must not apply to it.
    ("- item\n```\n\nbody of the chunk\n", "body of the chunk"),
    # --- Defect A: a chunk inside a blockquote.
    ("> intro\n>\n>     def x():\n>         return 1\n", "def x():"),
    ("> intro\n>\n>     def x():\n>         return 1\n", "return 1"),
    (">     def x():\n", "def x():"),
    ("> a\n\n>     def x():\n", "def x():"),
    # A quote line that is blank closes the quote's paragraph in three spellings.
    ("> intro\n>  \n>     def x():\n", "def x():"),
    ("> intro\n>\t\n>     def x():\n", "def x():"),
    # One tab after the marker is two columns, not four: the marker takes `>` and
    # one space of it. Two tabs clear the floor.
    (">\t\tdef x():\n", "def x():"),
    # A block at the quote's own column closes a list open inside the quote, so
    # the floor drops back to four. Without it, one `> - item` anywhere keeps
    # every later quoted chunk in the document translatable — the document
    # level's margin rule, one container down, and the same defect quietly
    # coming back.
    ("> - item\n>\n> margin text\n>\n>     def x():\n", "def x():"),
    # U+3000 and U+00A0 are not indentation, so neither line opens a list item
    # and the chunk below is measured against four columns rather than against a
    # phantom item's floor of seven. `LIST_RE`'s leading `\s*` said otherwise:
    # 706 markers of code across the sweep, and the reason its class is narrowed
    # even though the quote defect it caused is now fixed a second way.
    ("　- a paragraph, not a list item\n\n    def x():\n", "def x():"),
    ("\xa0- a paragraph, not a list item\n\n    def x():\n", "def x():"),
    # An unclosed run inside an item ends where the item does, so a line between
    # the item's content column and the fence's own indent is still inside the
    # run. Bounding by the opener instead stops there and hands its body to the
    # model.
    ("- item\n\n    ```\n   three columns\nprose.\n", "three columns"),
    # A blank line inside an unclosed run does not end it: the run reaches the
    # container's end, so the containment loop steps over blanks for the same
    # reason the chunk loop does. Without that step the run stops at the blank
    # and everything below it is handed to the model.
    ("- an item\n\n  ```\n  code body\n\n  more code body\nprose after.\n",
     "more code body"),
    # --- HANDOFF-021: the must-still-work half of both rules it repaired, which
    # is the longer risk in each case. Every row below was confirmed against a
    # markdown-it-py render before it was written down.
    #
    # CommonMark restricts an info string to the *backtick* spelling of a fence,
    # so a tilde fence carrying a backtick is still a fence and its body must
    # never reach the model.
    ("~~~js`\ncode body\n~~~\n", "code body"),
    ("````text\ncode body\n````\n", "code body"),
    # `FENCE_RE` grew a `$` when it grew the info string, and every line of a
    # CRLF document keeps its CR — `parse` splits on "\n" alone. A fence whose
    # info string is clean has to survive that.
    ("```js\r\ncode body\r\n```\r\n", "code body"),
    # A real heading, break, underline and definition all still close the
    # paragraph above them, and a closed paragraph is what makes the four-column
    # line below an indented code block. Narrowing a class that decides a block
    # start is only safe if the block starts that remain still decide it.
    ("para\n# heading\n    code body\n", "code body"),
    ("para\n   ### heading\n    code body\n", "code body"),
    ("para\n#\theading\n    code body\n", "code body"),
    ("para\n***\n    code body\n", "code body"),
    ("para\n===\n    code body\n", "code body"),
    ("[x]: /url\n    code body\n", "code body"),
    # A full-width space after the colon lands in the *destination*, which
    # CommonMark allows, so this is still a definition. Narrowing that run to
    # `[ \t]` moved where the character is read and not what the line is.
    ("[x]:　/url\n    lazy prose\n", "lazy prose"),
    # `DEF_RE`'s leading run is the one deliberately left unbounded. The column
    # is enforced one branch earlier by the chunk branch, and ` {0,3}` here would
    # make a legitimate definition a segment and let a translated label break the
    # reference. The only row in this table that is skeleton because CommonMark
    # calls it a *definition* rather than because it calls it code.
    ("-    item\n\n     [x]: /url\n", "[x]: /url"),
    # --- HANDOFF-022 narrowed which lines are definitions, so these rows are the
    # must-not-overshoot half: every legitimate spelling of a destination and a
    # title still has to close the paragraph. A rule that only kept `[x]: /url`
    # working would hand a *titled* definition to the model, and a translated
    # label breaks every reference to it. Each row confirmed against a
    # markdown-it-py render before it was written down.
    ('[x]: /url "a title"\n    code body\n', "code body"),
    ("[x]: /url 'a title'\n    code body\n", "code body"),
    ("[x]: /url (a title)\n    code body\n", "code body"),
    ('[x]: </u rl> "a space, which only the angle form allows"\n    code body\n',
     "code body"),
    ("[x]: /u(r)l\n    code body\n", "code body"),
    ("[x]: /u\\(rl\n    code body\n", "code body"),
    # A backslash escapes the delimiter inside a title too. Without that the
    # title ends at the escaped quote and the junk after it refuses the whole
    # line, so a legitimate definition becomes a segment.
    ('[x]: /url "a \\" b"\n    code body\n', "code body"),
    # A parenthesis inside a *quoted* title is an ordinary character. Only a
    # parenthesized title refuses one, and applying that rule to all three
    # delimiters — which this parser did until it was read against the rule —
    # turns an ordinary sentence in a title into a paragraph.
    ('[x]: /url "a (b) c"\n    code body\n', "code body"),
    ("[x]: /url 'a (b c'\n    code body\n", "code body"),
    # A definition may end in spaces or tabs. Dropping the run after the title
    # leaves the suite green on every other row, because no other row has one.
    ('[x]: /url "a title"  \n    code body\n', "code body"),
    # NUL is not in the refused class: markdown-it replaces U+0000 with U+FFFD
    # before parsing, so this is a definition and refusing it would move a line
    # *into* the skeleton that CommonMark keeps out of it.
    ("[x]: /url\x00\n    code body\n", "code body"),
    # The metric this package was told to watch and was not being scored on. In a
    # CRLF document every line carries the CR of its own terminator, and a CR is
    # in the destination's refused class — so without the `\r*$` run at the tail,
    # every definition in a Windows-authored file stops being one at once, in
    # silence, with a green suite. `SETEXT_RE` and `HR_RE` learned the same
    # lesson one package earlier.
    ("[x]: /url\r\n    code body\r\n", "code body"),
    ('[x]: /url "a title"\r\n    code body\r\n', "code body"),
    # --- and the axis narrowing those two trailing runs broke, found by the
    # differential sweep and invisible to every test that existed: a CARRIAGE
    # RETURN. `parse` splits on "\n" alone, so in a CRLF document every line
    # still carries the CR of its own terminator. `\s*$` swallowed it by
    # accident; `[ \t]*$` cannot, so `Title\r\n=====\r\n` stopped being a setext
    # heading and became a two-line paragraph handed to the model with its
    # underline inside it, and the same for every thematic break.
    #
    # `docio.split_terminator` normalizes a *uniform* CRLF document to LF before
    # `parse` sees it, which is why this is not every Windows document — but
    # mixed and CR-only terminators are passed through verbatim by design
    # (`docs/decisions.md`, 2026-07-28), and they are what these rows are.
    ("Title\r\n===\r\n    code body\r\n", "code body"),
    ("Para.\r\n\r\n***\r\n    code body\r\n", "code body"),
    # …and the underline itself, which is the assertion that fails first: with
    # the CR unmatched it is inside the paragraph segment above it. Skeleton here
    # because it is an underline, not because CommonMark calls it code.
    ("Title\r\n=====\r\n\r\nBody.\r\n", "====="),
    ("Title\r\n=====\nBody.\r\n\r\n***\r\n    code body\n", "code body"),
    # A run rather than one CR, for the reason `emit_seg` takes a run: `\r\r\n`
    # is what a twice-applied LF-to-CRLF conversion leaves behind.
    ("Title\r\r\n===\r\r\n    code body\r\r\n", "code body"),
    # A closer shorter than its opener does not close, so the inner ` ``` ` is
    # this fence's content and not its end. `m.group(2)[0] * 3` said otherwise,
    # which is what broke the four-backtick-wrapping-three idiom.
    ("````\n```\ncode body\n````\n", "code body"),
    # --- HANDOFF-023, the must-not-overshoot half. The line below a table is
    # not a fresh block start, and a fence there is still a fence: the fence
    # branch has no `lazy` clause and must keep not having one.
    ("| a | b |\n| --- | --- |\n| c | d |\n```\ncode body\n```\n", "code body"),
    # The one line this repair stops translating, pinned so that a future change
    # to it has to be re-derived rather than noticed. The indented line below
    # the table is now a paragraph, so `===` underlines it and the four columns
    # under that are an indented code block — which is what CommonMark reads.
    # Only GFM, having read the first indented line as a code block, reads the
    # last one as prose. 90 lines of 20160 generated documents, three shapes;
    # see `docs/decisions.md`, 2026-08-11.
    ("| a | b |\n| --- | --- |\n| c | d |\n    indented\n===\n    code body\n",
     "code body"),
    # --- HANDOFF-024 narrowed which *labels* are labels, so these rows are its
    # must-not-overshoot half: a label may hold spaces, CJK and 1000 characters,
    # and every one of these still has to close the paragraph above it. A label
    # that stopped being one would be handed to the model, and a translated
    # label breaks every reference to it. Each row confirmed against a
    # markdown-it-py render before it was written down.
    ("[a b]: /url\n    code body\n", "code body"),
    ("[註]: /url\n    code body\n", "code body"),
    ("[" + "L" * 1000 + "]: /url\n    code body\n", "code body"),
    # The escaped-`]` row, and the direction this package took it. A backslash
    # consumes what follows, so the label closes at the *second* `]` and the
    # line is a definition — which is what CommonMark reads. The escape-blind
    # class this replaced read a paragraph.
    ("[a\\]b]: /url\n    code body\n", "code body"),
    ("[\\]]: /url\n    code body\n", "code body"),
    # …and the same backslash before the bracket that would otherwise refuse the
    # label outright. Refusing an *escaped* `[` too is the mutant this kills.
    ("[a\\[b]: /url\n    code body\n", "code body"),
    # Blank is `str.strip()`'s notion of it, borrowed from markdown-it-py's own
    # `normalizeReference` rather than written out here. These two rows are what
    # a hand-written whitespace class gets wrong in the losing direction:
    # neither U+FEFF nor U+200B is whitespace to Python, and markdown-it-py
    # reads a definition for both.
    ("[\ufeff]: /url\n    code body\n", "code body"),
    ("[\u200b]: /url\n    code body\n", "code body"),
    # A backslash before a space is not an escape, so the label is a literal
    # backslash and a space — not blank, because a backslash is not whitespace.
    ("[\\ ]: /url\n    code body\n", "code body"),
    # The blank test runs over the *whole* label, not its first character.
    # Found by the mutation pass and by nothing else: every other row here has a
    # non-blank first character, so a rule that looked only there passed all of
    # them, and `[ x]: /url` would have stopped being a definition in silence.
    ("[ x]: /url\n    code body\n", "code body"),
    ("[x ]: /url\n    code body\n", "code body"),
    # --- HANDOFF-025's must-not-overshoot half. A setext underline still
    # closes every paragraph it really does underline, and each row below is
    # one both references call a block. Confirmed against a markdown-it-py
    # render with the table rule enabled and without it, 2026-08-12.
    #
    # A table at the margin leaves the margin's own paragraph open: CommonMark
    # underlines the run it read as one paragraph, GFM reads the underline as a
    # cell and ends the body at the four-column line. Neither makes this line
    # prose, and a `doc_para` that said `False` for every table would.
    ("| a | b |\n| --- | --- |\n| c | d |\n===\n    code body\n", "code body"),
    # An underline indented into an open list item is in the item's own
    # container, so it underlines the item's paragraph however that paragraph
    # was opened. Without `in_item` this line stops being code.
    ("- item\nlazy line\n   ===\n        code body\n", "code body"),
    # A line that is blank to Python and content to CommonMark opens the
    # margin's paragraph where there was nothing to continue — so the `===`
    # below it is a real underline.
    ("　\ntext\n===\n    code body\n", "code body"),
    # An underline with nothing above it is the margin's new paragraph, which
    # the *next* one underlines. `doc_para` has to be True there, not merely
    # `para_open`.
    ("===\n===\n    code body\n", "code body"),
    # The must-not-overshoot side of the table clause: a table run that
    # continues a *margin* paragraph is still the margin's, so the underline
    # below it is a real one. Both references make this line a block.
    ("Intro.\n| a | b |\n| --- | --- |\n| c | d |\n===\n    code body\n",
     "code body"),
], ids=["indented-backtick-run", "indented-tilde-run", "indented-longer-run",
        "three-columns-is-still-a-fence", "a-fence-indented-into-an-item",
        "a-fence-in-an-ordered-item", "a-fence-in-a-nested-item",
        "an-unclosed-margin-fence", "an-unclosed-one-column-fence",
        "a-margin-fence-under-an-item", "a-quoted-chunk",
        "a-quoted-chunk-runs-on", "a-quoted-chunk-opens-the-file",
        "a-blank-line-reopens-the-quote",
        "a-quote-blank-with-a-space", "a-quote-blank-with-a-tab",
        "two-tabs-after-the-marker", "a-quoted-margin-block-closes-the-item",
        "an-ideographic-space-opens-no-item", "a-no-break-space-opens-no-item",
        "an-unclosed-run-holds-a-shallower-line",
        "an-unclosed-run-holds-a-blank-line",
        "a-tilde-fence-keeps-its-backtick-info-string",
        "a-longer-run-with-a-clean-info-string",
        "a-clean-info-string-under-crlf",
        "a-real-heading-still-closes-a-paragraph",
        "three-columns-is-still-a-heading",
        "a-tab-after-the-hashes-is-still-a-heading",
        "a-real-break-still-interrupts-a-paragraph",
        "a-real-underline-still-closes-a-paragraph",
        "a-real-definition-still-closes-a-paragraph",
        "a-full-width-space-after-the-definition-colon",
        "a-definition-indented-past-three-columns",
        "a-double-quoted-title-is-still-a-definition",
        "a-single-quoted-title-is-still-a-definition",
        "a-parenthesized-title-is-still-a-definition",
        "an-angle-destination-holding-a-space",
        "balanced-parentheses-in-a-destination",
        "an-escaped-parenthesis-in-a-destination",
        "an-escaped-delimiter-inside-a-title",
        "a-parenthesis-inside-a-double-quoted-title",
        "a-parenthesis-inside-a-single-quoted-title",
        "a-definition-may-end-in-spaces",
        "a-nul-in-a-destination-is-not-a-control-character",
        "a-crlf-definition-still-closes-a-paragraph",
        "a-crlf-definition-with-a-title",
        "a-crlf-setext-underline-still-underlines",
        "a-crlf-thematic-break-still-breaks",
        "a-crlf-underline-is-not-translated",
        "a-mixed-terminator-underline",
        "a-doubled-cr-underline",
        "a-closer-shorter-than-its-opener-does-not-close",
        "a-fence-below-the-last-table-row-is-still-a-fence",
        "an-underline-below-a-four-column-line-below-a-table",
        "a-label-may-hold-a-space", "a-cjk-label-is-still-a-label",
        "a-thousand-character-label", "an-escaped-bracket-closes-no-label",
        "a-label-that-is-one-escaped-bracket",
        "an-escaped-opening-bracket-is-label-text",
        "a-byte-order-mark-is-not-whitespace-to-python",
        "a-zero-width-space-is-not-whitespace-to-python",
        "a-backslash-before-a-space-is-not-blank",
        "a-label-may-begin-with-a-blank", "a-label-may-end-with-a-blank",
        "an-underline-below-a-margin-table-still-underlines",
        "an-underline-indented-into-an-item-underlines-it",
        "an-underline-below-an-ideographic-space-paragraph",
        "an-underline-below-an-underline-that-opened-a-paragraph",
        "an-underline-below-a-table-that-continues-a-margin-paragraph"])
def test_a_chunk_in_a_fence_run_or_a_quote_leaves_no_segment_behind(text, body):
    assert body not in "\n".join(s["source"] for s in parse(text)[1])
    assert identity_roundtrip(text) == text


@pytest.mark.parametrize("text, prose", [
    # --- Defect B: the prose an unclosed indented run used to swallow.
    ("Para.\n\n    ```\n    code\nOrdinary prose.\n", "Ordinary prose."),
    ("Para.\n\n    ~~~\n    code\nOrdinary prose.\n", "Ordinary prose."),
    ("- item\n\n      ```\n    ```\n\nProse after the list.\n", "Prose after the list."),
    # A run that cannot interrupt the paragraph above it is that paragraph's
    # lazy continuation, and the prose below it comes back with it.
    ("Para.\n    ```\n    code\nlazy prose\n", "lazy prose"),
    # Only a space and a tab indent a fence. `\s` reaches both of these, and
    # U+3000 is the zh-TW paragraph indent — a paragraph that begins with one
    # was being read as a fence and swallowed with everything under it.
    ("　```\nprose after an ideographic space\n```\n",
     "prose after an ideographic space"),
    ("\xa0```\nprose after a no-break space\n```\n", "prose after a no-break space"),
    # --- Defect A: what a blockquote's interior must keep.
    ("> intro\n>     a lazy continuation.\n", "a lazy continuation."),
    ("> - item\n>\n>     a second paragraph of the quoted item.\n",
     "a second paragraph of the quoted item."),
    # …and its deeper sibling, which CommonMark *does* call code. The quote's
    # interior tracks an open list as a boolean rather than a column, so nothing
    # inside a quoted list is code: the column was six of the eight regressions
    # adversarial review found, every one losing prose. This is what that costs.
    ("> - item\n>\n>       def deep():\n", "def deep():"),
    ("> -\titem\n>\n>         def x():\n", "def x():"),
    # A bare `> -` opens an empty list item CommonMark still counts, and `LIST_RE`
    # does not match it because it wants whitespace after the marker.
    ("> -\n>     a\n>\n>     text\n", "text"),
    ("> 1)\n>     a\n>\n>     text\n", "text"),
    # A quote marker that is itself indented under an open paragraph is not a
    # blockquote at all — it is that paragraph's lazy continuation.
    ("prose paragraph\n    >     def x():\n", "def x():"),
    # A quoted line the *table* loop consumes, the third branch that reads one
    # without being the quote branch.
    ("| a |\n|---|\n> q | p\n>     still prose\n", "still prose"),
    ("> Note | caveat\n|---|---|\n>     still prose\n", "still prose"),
    # A quoted line the *fence* loop consumes, the fourth.
    ("- [ ] item\n      ```\n> intro\n      ```\n>     still prose\n",
     "still prose"),
    # A fence marker below the item's content column is not inside the item, so
    # its own indentation is bounded at the document's floor and not the item's.
    # Reading it as the item's calls a four-column marker a fence where
    # CommonMark calls it an indented chunk, and the fence then runs away.
    ("   - an item\n    ```\n\nprose after the run.\n", "prose after the run."),
    # Where the fence *is* inside the item, the run stops at the item's end —
    # bounded by the upper estimate of the content column, because a floor below
    # the real one runs past that end.
    ("- [ ] item\n      ```\n  > intro to the quote\n>     a lazy continuation.\n",
     "intro to the quote"),
    # …and the line that ends the container may be the fence's own closing
    # marker, which is otherwise re-read as a fresh opener and reaches end of
    # file from the margin.
    ("-\titem\n\n  ```\n```\n\nprose after the fence.\n", "prose after the fence."),
    # Two blank quote lines in a row: the second reaches the rule that closes a
    # list open inside the quote, and a blank line closes no list.
    ("> - a\n>\n>\n>     a second paragraph.\n", "a second paragraph."),
    ("> intro\n>\n>    three columns only.\n", "three columns only."),
    # A quote line that is blank to `str.strip()` and not to CommonMark is
    # content, so it keeps the quote's paragraph open — the same distinction the
    # document level draws, one container down, and the shape a sweep that varied
    # only ASCII blanks could not see.
    ("> intro\n>　\n>     still a lazy continuation.\n", "still a lazy continuation."),
    ("> intro\n>\xa0\n>     still a lazy continuation.\n", "still a lazy continuation."),
    # A U+3000 *before* the marker moves the quote's content column if `_columns`
    # is allowed to score it, which put an ordinary paragraph four columns in.
    ("　>     an ideographic space before the marker.\n",
     "an ideographic space before the marker."),
    (">\tdef x():\n", "def x():"),
    # A tab stop is absolute in the line, so a tab after the marker's space
    # starts at column 2 and advances to 4 — two columns of indent, not four.
    # Measuring the content string on its own scores it as four and calls the
    # line code, in 2276 generated shapes.
    ("> \tdef x():\n", "def x():"),
    # A U+3000 line *outside* the quote is a lazy continuation of it, so it
    # neither closes the quote nor its paragraph. A real blank line does both,
    # and reading these two the same way is what the character class is for.
    ("> intro\n　\n>     a lazy continuation.\n", "a lazy continuation."),
    # A code block opens no paragraph, so a shallower quoted line after one is
    # prose again and everything below it is measured afresh.
    ("> intro\n>\n>     def x():\n> shallow again\n>     a lazy continuation.\n",
     "a lazy continuation."),
    # The chunk loop's carriage-return guard, inside a quote. A lone CR is text
    # here and a line ending to CommonMark, so the parser cannot know where the
    # chunk ends and declines to make any of it skeleton.
    ("> intro\n>\n>     def x():\rprose after a text CR\r", "prose after a text CR"),
    ("> - item\n> continues at the quote's margin\n>\n>     second paragraph.\n",
     "second paragraph."),
    # The four adversarial review found on 2026-08-03 after a sweep of 83451
    # documents had reported zero. Each lives on an axis that sweep held
    # constant, and every one of them stopped prose being translated.
    #
    # A list marker inside a quote starts at the quote's content column, so a tab
    # in the marker advances from there. Measuring the prefix from column 0
    # scores `1.\t` as four columns instead of six and drops the floor by two.
    ("> 1.\titem\n>\n>         a second paragraph of the quoted item.\n",
     "a second paragraph of the quoted item."),
    ("> 10.\titem\n>\n>         a second paragraph of the quoted item.\n",
     "a second paragraph of the quoted item."),
    # U+3000 is not indentation, so this opens no list item — but `LIST_RE`'s
    # `\s*` said it did, the list branch swallowed the quote line below it, and
    # the quote's paragraph state was therefore never set at all.
    ("　- a paragraph, not a list item\n   > intro\n>     a lazy continuation.\n",
     "a lazy continuation."),
    ("\xa0- a paragraph, not a list item\n   > intro\n>     a lazy continuation.\n",
     "a lazy continuation."),
    # …and where there *is* a list item, the same loop still consumes the quoted
    # line, so the state is recorded where the line is read instead.
    ("-\titem\n   > intro\n>     a lazy continuation.\n", "a lazy continuation."),
    ("- item\n  > intro\n>     a lazy continuation.\n", "a lazy continuation."),
    # `list_col` is the item's whole prefix and overshoots CommonMark's content
    # column, so reading it as "is the fence inside the item" judged a fence at
    # two columns to be outside a `- [ ] ` item, took the margin's bound of zero,
    # and swallowed the rest of the file.
    ("- [ ] item\n\n          ```\n  ```\nprose after the item.\n",
     "prose after the item."),
    ("-    item\n\n         ```\n   ```\nprose after the item.\n",
     "prose after the item."),
    # The three that knowingly disagree with CommonMark, all conservative and all
    # inherited rather than invented. The first two are the document level's own
    # divergences arriving one container down: a list item's floor is its whole
    # prefix, checkbox included, and a bare `>` does not close a paragraph. The
    # third is this parser having no container stack — it strips one `>` and
    # measures the rest, so a chunk inside a *nested* quote stays translatable.
    ("> - [ ] task\n>\n>       six columns after a quoted checkbox.\n",
     "six columns after a quoted checkbox."),
    ("> > nested\n>\n> >     four columns after a nested marker\n",
     "four columns after a nested marker"),
    ("> quoted line\n>\n    still not code here.\n", "still not code here."),
    # --- HANDOFF-021, defect A: a *backtick* fence's info string may not contain
    # a backtick, so none of the runs below opens a fence. The old pattern said
    # they did, no closing marker was found, and with no list open the
    # containment bound is vacuous — the run reached end of file and `parse`
    # returned zero segments for a three-paragraph document.
    ("```js`\nprose that used to vanish\n\nand this too\n```\n",
     "prose that used to vanish"),
    ("```js`\nprose that used to vanish\n\nand this too\n```\n", "and this too"),
    ("````js`\nprose that used to vanish\n", "prose that used to vanish"),
    ("``` `\nprose that used to vanish\n", "prose that used to vanish"),
    ("```js`\r\nprose that used to vanish\r\n", "prose that used to vanish"),
    # `FENCE_RE` is also the paragraph loop's stop condition, so a run that stops
    # being a fence stops cutting the paragraph it sits in.
    ("Para.\n```js`\nlazy prose\n", "lazy prose"),
    # --- HANDOFF-021, defect B: four markers whose indent was counted in
    # characters where the column it stands for is what CommonMark bounds. Each
    # of them closes the paragraph above it, and a closed paragraph is what turns
    # the four-column line below into an indented code block — so every row here
    # is a whole line of prose that stopped being translated, silently.
    #
    # One tab is four columns, which is one past the three a heading may carry.
    ("para above\n\t# not a heading\n    lazy prose\n", "lazy prose"),
    # …and U+3000, the zh-TW paragraph indent, is not indentation at all.
    ("　# not a heading\n    lazy prose\n", "lazy prose"),
    ("\xa0# not a heading\n    lazy prose\n", "lazy prose"),
    # The run *after* the hashes is spaces or tabs too. It measures no column,
    # and it is narrowed all the same because it decides whether the line is a
    # heading, which is a block start, which closes a paragraph.
    ("#　not a heading\n    lazy prose\n", "lazy prose"),
    ("#\x0bnot a heading\n    lazy prose\n", "lazy prose"),
    # A thematic break, in all three spellings and at both ends of the line.
    ("para above\n\t***\n    lazy prose\n", "lazy prose"),
    ("para above\n***　\n    lazy prose\n", "lazy prose"),
    ("para above\n\x0c---\n    lazy prose\n", "lazy prose"),
    # A setext underline, which is the same two classes again.
    ("para above\n\t===\n    lazy prose\n", "lazy prose"),
    ("para above\n===　\n    lazy prose\n", "lazy prose"),
    # And a link reference definition, whose leading run keeps `*` rather than
    # `{0,3}` — only its character class narrowed.
    ("　[x]: /url\n    lazy prose\n", "lazy prose"),
    ("\x0c[x]: /url\n    lazy prose\n", "lazy prose"),
    # --- HANDOFF-022: the tail decides too, and until it did, `[label]:` alone
    # put *two* blocks into the skeleton — the line itself, and the indented line
    # under it that a closed paragraph turns into code. markdown-it-py renders a
    # `<p>` for every candidate line below; the three that knowingly disagree
    # with it say so at the row.
    ("[x]: /url not a title\n    lazy prose\n", "lazy prose"),
    ("[x]: /url not a title\n    lazy prose\n", "[x]: /url not a title"),
    # The shape this matters for, in the use case this project is for: a line of
    # dialogue in square brackets is a paragraph, and it was going untranslated.
    ("[Ana]: Hello there, she said.\n", "[Ana]: Hello there, she said."),
    ("[x]: /u rl\n    lazy prose\n", "lazy prose"),
    # A control character may not sit between the colon and the destination, nor
    # inside one. The first row is what makes `DEF_RE`'s post-colon `[ \t]*`
    # load-bearing rather than the equivalent mutant it used to be.
    ("[x]:\x0c/url\n    lazy prose\n", "lazy prose"),
    ("[x]: /u\x0brl\n    lazy prose\n", "lazy prose"),
    ("[x]: /u\x7frl\n    lazy prose\n", "lazy prose"),
    # The angle form has no fallback to the bare one: CommonMark's bare
    # destination "does not start with `<`".
    ("[x]: <a destination that never closes\n    lazy prose\n", "lazy prose"),
    ("[x]: <url>\"t\"\n    lazy prose\n", "lazy prose"),
    ('[x]: /url "a title" and then junk\n    lazy prose\n', "lazy prose"),
    ("[x]: /url (a (nested) title)\n    lazy prose\n", "lazy prose"),
    ("[x]: /u(rl\n    lazy prose\n", "lazy prose"),
    ("[x]: /u)rl\n    lazy prose\n", "lazy prose"),
    # …and the one that balances *overall* while closing a parenthesis it never
    # opened. Counting to zero at the end is not the rule; a negative depth
    # refuses the destination where it happens. Added because the mutation pass
    # found the guard untested — every earlier row leaves the final depth
    # non-zero too, so removing it changed nothing they could see.
    ("[x]: /u)r(l\n    lazy prose\n", "lazy prose"),
    # The angle form's own three refusals, all three untested until the mutation
    # pass said so. A line ending may not sit inside the brackets and a backslash
    # cannot escape one; an unescaped `<` may not either.
    ("[x]: <u\rrl>\n    lazy prose\n", "lazy prose"),
    ("[x]: <u\\\rrl>\n    lazy prose\n", "lazy prose"),
    ("[x]: <u<rl>\n    lazy prose\n", "lazy prose"),
    # A `(` inside a parenthesized title refuses the title outright rather than
    # nesting. `(a (b) c)` alone does not prove it — the title ends at the first
    # `)` and the junk after it refuses the line anyway — so the row has to be
    # one whose closer is the last character on the line.
    ("[x]: /url (a (b)\n    lazy prose\n", "lazy prose"),
    # Only a space and a tab separate a destination from its title. After the
    # *angle* form that is visible, because U+3000 cannot be absorbed into the
    # destination the way it is after a bare one.
    ('[x]: <url>　"t"\n    lazy prose\n', "lazy prose"),
    # A backslash escapes what follows it — except a space, which ends the
    # destination at the backslash instead. markdown-it has the same exception.
    ('[x]: a\\ "t"\n    lazy prose\n', "lazy prose"),
    # An empty destination, which was the one half the old branch got right, and
    # which now keeps the line itself translatable rather than only the line
    # below it.
    ("[x]:\n    lazy prose\n", "lazy prose"),
    # A definition may not interrupt a paragraph, so this is the quote's lazy
    # continuation and renders as its own literal text. Once the tail was
    # decided this was 7228 lines of the sweep's remaining loss column.
    ("> quoted above\n[x]: /url\n", "[x]: /url"),
    ("- item above\n[x]: /url\n", "[x]: /url"),
    # --- the three rows where the two references disagree with *each other*,
    # because markdown-it trims the whole reference before parsing it and the
    # spec does not. The first two are definitions to markdown-it and paragraphs
    # to the spec; the third is the other way round. This parser refuses all
    # three, which is the direction that keeps the text translatable rather than
    # a compromise. See `_completes_a_definition`.
    ('[x]: /url "t"　\n    lazy prose\n', "lazy prose"),
    ("[x]: /url\x0c\n    lazy prose\n", "lazy prose"),
    ("[x]:　\n    lazy prose\n", "lazy prose"),
    # --- what the info-string rule cost downstream, found by adversarial review
    # on the axis the 40284-document sweep never varied: the *sequence* of fence
    # markers in one document. Once a line stops being an opener, every later
    # marker re-pairs one step over, and the last one runs to end of file. The
    # closing search had all three of CommonMark's closer rules missing.
    #
    # A closer carries no info string.
    ("```\n```js`\n```js`\n```\nprose after the fence\n", "prose after the fence"),
    # …and the shape a person actually writes, which is what makes it serious.
    ("# Configuring the widget\n\nSet the mode first:\n\n```js`\nwidget.mode = 1;\n"
     "```\n\nThe mode cannot be changed.\n\n```js\nwidget.start();\n```\n\n"
     "prose after the fence\n", "prose after the fence"),
    # A closer is at least as long as its opener — the four-backtick fence
    # wrapping a three-backtick example, which is *the* idiom for documenting
    # Markdown and which `m.group(2)[0] * 3` broke. Pre-existing, and the root
    # cause of the row above.
    ("````markdown\n```\nan inner example\n````\n\nprose after the fence\n",
     "prose after the fence"),
    # A marker is a run of ONE character. ``[`~]+`` reads ```` ```~~~ ```` as a
    # six-character marker nothing can close — caught by re-running the harness
    # that found the defect, never by the suite.
    ("```~~~\ncode body\n```\nprose after the fence\n", "prose after the fence"),
    ("~~~`\ncode body\n~~~\nprose after the fence\n", "prose after the fence"),
    # A marker at the margin cannot close a fence *inside* a list item, so the
    # container's-end rule only applies where the fence sits below the item's
    # content column — which is the case it was written for.
    ("- an item\n    ```js`\nlazy line.\n    ```\n\n```\nx\n```\n\n"
     "prose after the fence\n", "prose after the fence"),
    # A blockquote interrupts a paragraph, so it closes a list item even when the
    # line above it was that item's lazy continuation. Without that the item's
    # content column outlives it and a four-column fence marker below is read as
    # inside the item, which takes the prose under it into the skeleton.
    ("- an item\n***　\n> quoted\n    ```js\n    still prose\n"
     "    still prose too\nunindented.\n", "still prose"),
    # --- HANDOFF-023: a GFM table body does not end at the last line holding a
    # `|`. The line below the last row renders as a *cell*, and CommonMark with
    # no table rule reads the whole run as one paragraph and that line as its
    # lazy continuation — prose under both readings, and skeleton here until the
    # branch started leaving a paragraph open. 3771 lines of the sweep under the
    # GFM reading and 7313 under CommonMark's. Every row below was confirmed
    # against a markdown-it-py render with the table rule enabled *and* against
    # `MarkdownIt("commonmark")` without it, on 2026-08-11.
    ("| a | b |\n| --- | --- |\n| c | d |\n[x]: /url\n", "[x]: /url"),
    ('| a | b |\n| --- | --- |\n| c | d |\n[x]: /url "a title"\n',
     '[x]: /url "a title"'),
    # A uniform CRLF document is normalized to LF by `docio` before `parse` sees
    # it; a mixed one is passed through verbatim by design, and is this row.
    ("| a | b |\r\n| --- | --- |\r\n| c | d |\r\n[x]: /url\r\n", "[x]: /url"),
    # The second behaviour change, and the direction it takes. The chunk branch
    # reads `lazy` too, so a four-column line below a table stops being
    # skeleton. GFM ends the body at four columns and calls the line code;
    # CommonMark calls it a lazy continuation and calls it prose; where the two
    # disagree the text stays translatable.
    ("| a | b |\n| --- | --- |\n| c | d |\n    indented prose\n",
     "indented prose"),
    # `- | a | b |` is claimed by the *table* branch, which is tested above the
    # list branch, so the item's content column is recorded there or nowhere.
    # Without it every floor below the table is measured from the margin, and
    # this line — two columns past the item's content column, and the item's
    # prose under both readings — becomes an indented code block. 260 lines of
    # the sweep, and they are lines the open paragraph newly created rather than
    # lines the parent had already lost.
    ("- | a | b |\n  | --- | --- |\n  | c | d |\n[x]: /url\n===\n"
     "    still inside the item.\n", "still inside the item."),
    ("- | a | b |\n  | --- | --- |\n  | c | d |\n"
     "    still inside the item.\n", "still inside the item."),
    # --- HANDOFF-024: a link label is not any run of brackets. Every row below
    # has a tail that parses perfectly well, so until the label decided too,
    # each cost the line *and* the indented line under it — a closed paragraph
    # is what turns four columns into a code block. markdown-it-py renders a
    # `<p>` for every one of them.
    #
    # A label must hold at least one character that survives `str.strip()`.
    ("[ ]: /url\n    lazy prose\n", "lazy prose"),
    ("[ ]: /url\n    lazy prose\n", "[ ]: /url"),
    ("[　]: /url\n    lazy prose\n", "lazy prose"),
    ("[\xa0]: /url\n    lazy prose\n", "lazy prose"),
    ("[\t]: /url\n    lazy prose\n", "lazy prose"),
    ("[\x0c]: /url\n    lazy prose\n", "lazy prose"),
    ("[\u2028]: /url\n    lazy prose\n", "lazy prose"),
    # …and it is the *whole* label that must, not its first character.
    ("[  　 ]: /url\n    lazy prose\n", "lazy prose"),
    # An unescaped `[` refuses the label outright rather than ending it, so it
    # costs the line wherever in the label it sits.
    ("[a[b]: /url\n    lazy prose\n", "lazy prose"),
    ("[[]: /url\n    lazy prose\n", "lazy prose"),
    ("[[a]: /url\n    lazy prose\n", "lazy prose"),
    ("[a[]: /url\n    lazy prose\n", "lazy prose"),
    # The fifth loss shape, which the package did not name and which is the
    # reason the label half became a scanner rather than a narrower class: a
    # backslash consumes the `]`, so this label never closes at all. No class
    # can see that, which is why refusing to parse escapes was never the
    # conservative direction it looks like.
    ("[a\\]: /url\n    lazy prose\n", "lazy prose"),
    ("[a\\]: /url\n    lazy prose\n", "[a\\]: /url"),
    # --- and the three the package measured as already right, which a
    # narrowing must not overshoot into definitions.
    ("[]: /url\n    lazy prose\n", "lazy prose"),
    ("[a]b: /url\n    lazy prose\n", "lazy prose"),
    ("[[a]]: /url\n    lazy prose\n", "lazy prose"),
    # --- HANDOFF-025: a setext underline under a block that is not the
    # margin's paragraph underlines nothing, and the line below it is prose.
    # Both references agree on every row here — the underline is paragraph
    # continuation *text* of whatever container was open, so nothing closes and
    # the four-column line under it is a lazy continuation. Each confirmed
    # against a markdown-it-py render with the table rule and without it,
    # 2026-08-12. Until this, every one cost the line below the underline.
    ("> quoted\n===\n    tail prose\n", "tail prose"),
    # The chunk branch is not the only reader of `para_open`: the definition
    # branch reads it too, so a repair that taught only the chunk branch would
    # still take this whole line into the skeleton. 3836 lines of the sweep.
    ("> quoted\n===\n[y]: /url2\n", "[y]: /url2"),
    ("- item\n===\n    tail prose\n", "tail prose"),
    # The three families the package named, all of them present at the parent.
    ("| a | b |\n| --- | --- |\n| c | d |\n> quoted\n===\n    tail prose\n",
     "tail prose"),
    ("> | a | b |\n> | --- | --- |\n> | c | d |\n===\n    tail prose\n",
     "tail prose"),
    ("> | a | b |\n> | --- | --- |\n> | c | d |\n[x]: /url\n===\n    tail prose\n",
     "tail prose"),
    # A table inside a list item: the margin underline ends the item, so the
    # paragraph it might have underlined is one container down.
    ("- | a | b |\n  | --- | --- |\n  | c | d |\n===\n        deep tail prose\n",
     "deep tail prose"),
    # A line blank to Python and content to CommonMark is a lazy continuation
    # of the quote, so the underline below it still underlines nothing.
    ("> quoted\n　\n===\n    tail prose\n", "tail prose"),
    # An underline that underlines nothing does not start a paragraph of its
    # own either — it is more text in the block that was already open, so the
    # *second* underline has nothing to close.
    ("> quoted\n===\n===\n    tail prose\n", "tail prose"),
    # A paragraph inside an open list item is not the margin's, even though
    # this parser started it fresh after a blank line.
    ("- outer item\nPara.\n\n    in the item\n===\n        deep tail prose\n",
     "deep tail prose"),
    # Which bound on the item's content column decides "inside". The item's
    # content begins at four columns and the underline sits at three, so it is
    # outside the item — the lower bound calls it inside and loses this line.
    ("-   padded item\n   ===\n        deep tail prose\n", "deep tail prose"),
    # A table run is a paragraph to CommonMark, so a table that begins as some
    # container's lazy continuation leaves *that* container's paragraph open
    # and not the margin's. Found by the adversarial pass on the axis the
    # sweep held constant — the sweep only ever started a table at the top of
    # its own block.
    ("> intro\n| a | b |\n| --- | --- |\n| c | d |\n===\n    tail prose\n",
     "tail prose"),
    ("- item\n| a | b |\n| --- | --- |\n| c | d |\n===\n    tail prose\n",
     "tail prose"),
    # …and the same thing reached from inside the run. The table loop takes
    # every consecutive line holding a `|`, a blockquote marker included, so
    # the quote branch never sees this one — the fourth swallowing loop, and
    # the state it has to leave behind is `doc_para` as well as `quote_para`.
    ("| a | b |\n| --- | --- |\n> q | p\n===\n    tail prose\n", "tail prose"),
    # …and the marker may be indented up to three columns and still be one.
    # The quote branch refuses an indented `>` because it cannot tell a
    # blockquote from a lazy continuation; inside a table run that question has
    # no cost, and copying the refusal here lost this line. Found by the
    # mutation pass, not by the sweep.
    ("| a | b |\n| --- | --- |\n   > q | p\n===\n    tail prose\n", "tail prose"),
], ids=["prose-below-an-indented-run", "prose-below-an-indented-tilde-run",
        "prose-below-a-contained-run", "a-run-that-cannot-interrupt-a-paragraph",
        "an-ideographic-space-before-a-run", "a-no-break-space-before-a-run",
        "a-lazy-continuation-inside-a-quote", "a-quoted-item-second-paragraph",
        "a-quoted-item-deep-chunk", "a-tab-in-a-quoted-bullet-marker",
        "a-bare-quoted-list-marker", "a-bare-quoted-ordered-marker",
        "an-indented-quote-marker-under-a-paragraph",
        "a-quoted-line-the-table-branch-consumes",
        "a-quoted-table-row-that-opens-the-quote",
        "a-quoted-line-the-fence-branch-consumes",
        "a-fence-marker-below-the-item-s-content-column",
        "a-contained-run-stops-at-the-item-s-end",
        "the-container-s-end-is-the-fence-s-closer",
        "two-blank-quote-lines-close-no-list",
        "three-columns-inside-a-quote", "an-ideographic-space-quote-line",
        "a-no-break-space-quote-line", "an-ideographic-space-before-the-marker",
        "one-tab-after-the-marker", "a-tab-after-the-marker-s-space",
        "an-ideographic-space-line-outside-the-quote",
        "a-shallower-line-below-a-quoted-chunk",
        "a-text-cr-inside-a-quote", "a-quoted-item-that-wraps",
        "a-tab-in-a-quoted-list-marker", "a-tab-in-a-longer-quoted-marker",
        "an-ideographic-space-is-not-a-list-item",
        "a-no-break-space-is-not-a-list-item",
        "a-quoted-line-the-list-branch-consumes",
        "a-quoted-line-inside-a-plain-item",
        "an-unclosed-fence-below-a-checkbox-item-s-prefix",
        "an-unclosed-fence-below-a-padded-item-s-prefix",
        "a-quoted-task-list-checkbox", "a-chunk-inside-a-nested-quote",
        "a-bare-quote-marker-still-does-not-close",
        "a-backtick-in-a-backtick-info-string",
        "a-backtick-info-string-runs-on",
        "a-backtick-in-a-longer-info-string",
        "an-info-string-that-is-one-backtick",
        "a-backtick-info-string-under-crlf",
        "a-backtick-run-that-cannot-interrupt-a-paragraph",
        "a-tab-before-the-hashes-is-four-columns",
        "an-ideographic-space-before-the-hashes",
        "a-no-break-space-before-the-hashes",
        "an-ideographic-space-after-the-hashes",
        "a-vertical-tab-after-the-hashes",
        "a-tab-before-a-thematic-break",
        "an-ideographic-space-after-a-thematic-break",
        "a-form-feed-before-a-thematic-break",
        "a-tab-before-a-setext-underline",
        "an-ideographic-space-after-a-setext-underline",
        "an-ideographic-space-before-a-link-definition",
        "a-form-feed-before-a-link-definition",
        "an-unquoted-title-is-not-a-title",
        "an-unquoted-title-keeps-its-own-line-translatable",
        "a-line-of-dialogue-is-not-a-link-definition",
        "a-space-in-a-bare-destination",
        "a-form-feed-after-the-definition-colon",
        "a-vertical-tab-inside-a-destination",
        "a-del-inside-a-destination",
        "an-unclosed-angle-destination",
        "an-angle-destination-with-no-separator-before-the-title",
        "junk-after-a-link-title",
        "a-nested-parenthesis-in-a-title",
        "an-unclosed-parenthesis-in-a-destination",
        "an-unopened-parenthesis-in-a-destination",
        "parentheses-that-balance-only-at-the-end",
        "a-text-cr-inside-an-angle-destination",
        "an-escaped-text-cr-inside-an-angle-destination",
        "an-unescaped-bracket-inside-an-angle-destination",
        "a-nested-parenthesis-that-closes-at-the-line-s-end",
        "an-ideographic-space-after-an-angle-destination",
        "a-backslash-does-not-escape-a-space",
        "an-empty-destination-is-not-a-definition",
        "a-definition-may-not-interrupt-a-quoted-paragraph",
        "a-definition-may-not-interrupt-an-item-s-paragraph",
        "an-ideographic-space-after-a-link-title",
        "a-form-feed-after-a-destination",
        "an-ideographic-space-is-no-destination",
        "a-closer-may-not-carry-an-info-string",
        "a-realistic-manual-page",
        "a-closer-is-at-least-as-long-as-its-opener",
        "a-mixed-marker-run-is-not-one-marker",
        "a-tilde-opener-with-a-backtick-info-string-closes",
        "a-margin-marker-does-not-close-a-fence-inside-an-item",
        "a-quote-at-the-margin-closes-the-item-above-it",
        "a-definition-below-the-last-table-row",
        "a-titled-definition-below-the-last-table-row",
        "a-crlf-definition-below-the-last-table-row",
        "a-four-column-line-below-the-last-table-row",
        "a-table-that-swallowed-a-list-marker-keeps-the-item-open",
        "a-four-column-line-below-a-table-inside-an-item",
        "a-space-is-not-a-label", "a-space-label-keeps-its-own-line",
        "an-ideographic-space-is-not-a-label",
        "a-no-break-space-is-not-a-label", "a-tab-is-not-a-label",
        "a-form-feed-is-not-a-label", "a-line-separator-is-not-a-label",
        "a-run-of-blanks-is-not-a-label",
        "an-unescaped-bracket-inside-a-label",
        "an-unescaped-bracket-opening-a-label",
        "an-unescaped-bracket-after-the-opener",
        "an-unescaped-bracket-ending-a-label",
        "an-escaped-bracket-never-closes-the-label",
        "a-label-that-never-closes-keeps-its-own-line",
        "an-empty-label-is-not-a-label",
        "a-colon-that-does-not-follow-the-bracket",
        "a-doubled-bracket-label",
        "an-underline-below-a-quote", "a-definition-below-a-quoted-underline",
        "an-underline-below-a-list-item",
        "an-underline-below-a-quote-below-a-table",
        "an-underline-below-a-quoted-table",
        "an-underline-below-a-definition-below-a-quoted-table",
        "an-underline-below-a-table-inside-an-item",
        "an-underline-below-an-ideographic-space-inside-a-quote",
        "an-underline-below-an-underline-that-underlined-nothing",
        "an-underline-below-an-item-s-second-paragraph",
        "an-underline-outside-a-padded-item",
        "an-underline-below-a-table-that-continues-a-quote",
        "an-underline-below-a-table-that-continues-an-item",
        "an-underline-below-a-table-run-holding-a-quote-marker",
        "an-underline-below-a-table-run-holding-an-indented-quote-marker"])
def test_prose_in_a_fence_run_or_a_quote_is_still_translated(text, prose):
    assert prose in "\n".join(s["source"] for s in parse(text)[1])
    assert identity_roundtrip(text) == text


@pytest.mark.parametrize("line, payload, kind", [
    ("[x]: /url", "[x]: /url", "para"),
    ("[x]: /url not a title", "[x]: /url not a title", "para"),
    ("plain prose", "plain prose", "para"),
    ("# heading", "heading", "heading"),
    ("> quoted", "quoted", "quote"),
    ("- item", "item", "list"),
    ("| e | f |", "e", "cell"),
    ("    indented", "    indented", "para"),
    # Neither of these holds anything translatable, so both are raw whatever the
    # branch above decides: `emit_seg` refuses a source with no text in it. They
    # are rows so that a spelling which starts emitting one has to say so — the
    # underline in particular is a *cell* to GFM and an underline to CommonMark.
    ("===", "===", None),
    ("```", "```", None),
], ids=["a-definition", "a-definition-whose-tail-is-prose", "prose",
        "a-heading", "a-blockquote", "a-list-item", "another-row",
        "a-four-column-line", "a-setext-underline", "a-fence"])
def test_every_shape_below_the_last_table_row(line, payload, kind):
    """HANDOFF-023's measured table, as a test.

    The rule the table branch now carries is not "the line below a table is a
    paragraph" — it is "the line below a table is not a fresh block start", and
    every branch under the table branch still answers for its own syntax. That
    is why the expected kinds here are all different, and why a repair spelled
    as "read the trailing line as a row" would have collapsed six of them into
    `cell`. Each row was confirmed against a markdown-it-py render, with the
    table rule enabled and without it, on 2026-08-11.
    """
    text = "| a | b |\n| --- | --- |\n| c | d |\n" + line + "\n"
    segs = [s for s in parse(text)[1] if payload in s["source"]]
    assert [s["kind"] for s in segs] == ([] if kind is None else [kind])
    assert identity_roundtrip(text) == text


def test_masking_is_reversible():
    text = "Run `go build` then see https://x.dev/a?b=1 for {{var}}."
    masked, slots = mask(text, [])
    assert "go build" not in masked
    assert unmask(masked, slots) == text


def test_dnt_respects_word_boundaries():
    masked, slots = mask("Go to Google with Go.", ["Go"])
    assert "Google" in unmask(masked, slots)
    assert masked.count("\u27e6") == 2


def test_the_do_not_translate_order_is_total_so_a_slot_number_means_one_thing(tmp_path):
    """A placeholder number is only meaningful if it means the same term twice.

    `load_dnt` sorted by length alone and `sorted` is stable, so terms of equal
    length kept `set` iteration order \u2014 and `str`'s hash is randomised per
    process. `mask` numbers slots in that order and `translate.accept` compares
    the *set* of ids, which a wholesale renumbering satisfies, so a second `lx`
    process re-extracting an unedited document accepted the carried target and
    resolved its ids against a different map. Measured 2026-08-17 on a character
    list of eight five-letter names: five runs, five different permutations of
    the names in the rendered book, `lx check` exit 0 every time.

    Asserted on the order itself rather than on a rendered document, because the
    defect is one function's return value and a test that reproduces it end to
    end would depend on hash randomisation being on. The second half of the
    hazard \u2014 an edit that keeps the placeholder *count* and changes what a
    placeholder means \u2014 is not closed by this and is HANDOFF-033.
    """
    (tmp_path / "dnt.txt").write_text("Helen\nAlice\nBrian\nAlexander\nZoe\n",
                                      encoding="utf-8")
    order = load_dnt({"dnt": str(tmp_path / "dnt.txt")})
    assert order == ["Alexander", "Alice", "Brian", "Helen", "Zoe"]
    assert order == sorted(order, key=lambda t: (-len(t), t)), \
        "longest first is what stops `Go` masking inside `Google`"

# ── moving a wording from one numbering into another ────────────────────────
#
# `mask.reseat` is what lets a stored translation survive an edit to
# `config/dnt.txt`. Its whole correctness argument is that it seats by *content*
# and never by re-running `mask`, so the four rules below are the ones that make
# it sound rather than merely useful.


def test_a_protected_term_standing_bare_in_the_target_is_reported_at_warn():
    """The only rule that can see wording whose placeholders stopped meaning what
    they meant.

    Every gate compares ids — `translate.accept` and the `tags` rule alike — so a
    wholesale renumbering satisfies all of them, and the damage already stored in
    documents extracted before placeholder numbering became deterministic
    (2026-08-17) is reachable by nothing else. What is decidable without
    judgement, and therefore allowed here by invariant 4, is narrower than the
    defect: a term this configuration protects, appearing in the target as plain
    text.

    **Warn, and it cannot be anything else.** At the level of the text the defect
    and a legitimate repeat are the same string — a translation may name a
    protected term once through its placeholder and once in prose — so a severity
    that failed the build would make clearing those the only way to finish a
    book. That is the argument `held` is a warning by.
    """
    clean = _rules("⟦1⟧ ships today.", "⟦1⟧ 今天出貨。", dnt=["Celurion"],
                   slots={"1": {"original": "Celurion", "role": "standalone",
                                "pair_id": None, "can_reorder": True}})
    assert "bare_term" not in clean

    bare = _rules("⟦1⟧ ships today.", "⟦1⟧ 今天出貨，Celurion 準時。", dnt=["Celurion"],
                  slots={"1": {"original": "Celurion", "role": "standalone",
                               "pair_id": None, "can_reorder": True}})
    assert "bare_term" in bare
    issues = check_segment(
        _seg("⟦1⟧ ships today.", "⟦1⟧ 今天出貨，Celurion 準時。",
             {"1": {"original": "Celurion", "role": "standalone",
                    "pair_id": None, "can_reorder": True}}),
        "zh-TW", CFG, [], ["Celurion"])
    warned = [i["severity"] for i in issues if i["rule"] == "bare_term"]
    assert warned == ["warn"], "an error here would make clearing these the only way to finish"

    # And it reads `mask`'s own boundary rule rather than a substring search, or
    # every mention of `Celurions` would be one of these.
    plural = _rules("⟦1⟧ ships today.", "⟦1⟧ 今天出貨，Celurions 準時。", dnt=["Celurion"],
                    slots={"1": {"original": "Celurion", "role": "standalone",
                                 "pair_id": None, "can_reorder": True}})
    assert "bare_term" not in plural


def test_reseat_moves_a_wording_into_the_current_numbering():
    """The ordinary case: the same terms, numbered differently."""
    _src = "Alice met Brian."
    _was_masked, was = mask(_src, ["Alice", "Brian"])
    _now_masked, now = mask(_src, ["Brian", "Alice"])
    moved, why = reseat("⟦1⟧ 遇見了 ⟦2⟧。", was, now)
    assert (moved, why) == ("⟦2⟧ 遇見了 ⟦1⟧。", None)
    assert unmask(moved, now) == "Alice 遇見了 Brian。", "the wording changed meaning"


def test_reseat_keeps_an_order_the_translator_chose():
    """**Why it is not a second call to `mask`.**

    `mask` numbers by position in the text it is given, so re-masking a
    translation numbers the slots in the *translation's* order. A translation
    that legitimately put the second code span first therefore comes back with
    the two swapped — silently, with an id set that matches, so nothing
    downstream can see it. Measured 2026-08-17. Seating by content cannot do
    that: the ids follow the originals, wherever the translator put them.
    """
    _src = "Run `alpha` then `beta` for Acme."
    _was_masked, was = mask(_src, [])
    _now_masked, now = mask(_src, ["Acme"])
    moved, why = reseat("先執行 ⟦2⟧ 再執行 ⟦1⟧，給 Acme。", was, now)
    assert why is None
    assert unmask(moved, now) == "先執行 `beta` 再執行 `alpha`，給 Acme。"
    assert sorted(placeholder_ids(moved)) == sorted(placeholder_ids(_now_masked))


def test_reseat_refuses_rather_than_guessing_which_occurrence_is_the_slot():
    """Two of a term in the wording and one in the source: no rule can say which
    of them the placeholder belongs to, and a guess there is what puts one
    character's name where another's belongs."""
    _src = "Brian waited."
    _was_masked, was = mask(_src, ["Brian"])
    _now_masked, now = mask("Brian waited.", ["Brian"])
    moved, why = reseat("⟦1⟧ 等待著，Brian 很累。", was, now)
    assert moved is None
    assert "Brian" in why and "1" in why


def test_reseat_uses_masks_own_word_boundary():
    """`API` does not seat inside `APIs`, for the same reason `Go` does not mask
    inside `Google` — one rule, `mask.term_pattern`, shared by both. A plain
    substring search reads two occurrences here, refuses on the count, and turns
    an ordinary sentence into a refusal."""
    _src = "The API is ready."
    _was_masked, was = mask(_src, ["API"])
    _now_masked, now = mask(_src, ["API"])
    moved, why = reseat("⟦1⟧ 已就緒（參見 APIs 文件）。", was, {"2": now["1"]})
    assert (moved, why) == ("⟦2⟧ 已就緒（參見 APIs 文件）。", None)


def test_reseat_seats_the_longer_term_first():
    """`mask` masks the longer term first, so `York` finds nothing left inside
    `New York`; seating has to reproduce that precedence or the two collide."""
    _src = "New York and York differ."
    _was_masked, was = mask(_src, ["New York", "York"])
    assert _was_masked == "⟦1⟧ and ⟦2⟧ differ."
    moved, why = reseat("⟦1⟧ 與 ⟦2⟧ 不同。", was, was)
    assert (moved, why) == ("⟦1⟧ 與 ⟦2⟧ 不同。", None)
    assert unmask(moved, was) == "New York 與 York 不同。"


def test_slots_are_records_and_html_tags_pair():
    text = "A <b>bold</b> word and `code`."
    masked, slots = mask(text)
    by_text = {s["original"]: s for s in slots.values()}
    assert by_text["<b>"]["role"] == "open"
    assert by_text["</b>"]["role"] == "close"
    assert by_text["<b>"]["pair_id"] == by_text["</b>"]["pair_id"] is not None
    assert by_text["<b>"]["can_reorder"] is False
    assert by_text["`code`"] == {"original": "`code`", "role": "standalone",
                                 "pair_id": None, "can_reorder": True}
    assert unmask(masked, slots) == text


def test_nested_tags_pair_with_the_right_partner():
    text = "<b><i>x</i></b>"
    masked, slots = mask(text)
    assert unmask(masked, slots) == text
    pairs = {s["original"]: s["pair_id"] for s in slots.values()}
    assert pairs["<b>"] == pairs["</b>"]
    assert pairs["<i>"] == pairs["</i>"]
    assert pairs["<b>"] != pairs["<i>"]


def test_an_open_shadowed_by_a_void_element_still_pairs():
    # `<b><br>x</b>` is why the stack is searched downwards rather than read at
    # its top: a top-of-stack rule lets the <br> shadow the <b> and records no
    # pair at all. No void-element table is involved, deliberately.
    _masked, slots = mask("<b><br>x</b>")
    roles = {s["original"]: s["role"] for s in slots.values()}
    assert roles == {"<b>": "open", "<br>": "standalone", "</b>": "close"}


@pytest.mark.parametrize("text", [
    "Compare <b>a</i> and b.",                  # a close whose name does not match
    "A stray < in prose and an <b>unclosed tag.",
    "A void <br> element and <img src='x.png'/> beside it.",
    "An orphan </i> whose partner is in another block.",
])
def test_unbalanced_markup_stays_standalone_and_never_raises(text):
    # Unbalanced input is ordinary, not exceptional. Nothing may crash, and
    # nothing may pair with the wrong partner \u2014 a wrong pair is worse than none,
    # because the validator would then enforce it.
    masked, slots = mask(text)
    assert unmask(masked, slots) == text
    assert all(s["role"] == "standalone" and s["pair_id"] is None
               for s in slots.values())


@pytest.mark.parametrize("mangled", ["\u30103\u3011", "[[3]]", "\u27e6 \uff13 \u27e7", "\u30143\u3015"])
def test_placeholder_repair(mangled):
    assert repair_placeholders(f"x{mangled}y") == "x\u27e63\u27e7y"


def _seg(masked, target, slots=None, kind="para", host=None):
    seg = {"id": "s1", "kind": kind, "masked": masked, "target": target,
           "slots": slots or {}}
    if host:
        seg["host"] = host          # absent by default: the Markdown path is the default
    return seg


def _rules(masked, target, lang="zh-TW", glossary=(), dnt=(), slots=None):
    issues = check_segment(_seg(masked, target, slots), lang, CFG, list(glossary), list(dnt))
    return {i["rule"] for i in issues}


def test_dropped_placeholder_is_an_error():
    assert "tags" in _rules("Set \u27e61\u27e7 now.", "\u8acb\u8a2d\u5b9a\u3002")


# --- typed slot records: what may move and what may not ----------------------
#
# Reordering used to be legal for every placeholder, asserted as a property. It
# is legal for a *standalone* one \u2014 moving a URL or a code span is ordinary \u2014 and
# it is not for a pair: `\u27e62\u27e7\u7c97\u9ad4\u27e61\u27e7` against `\u27e61\u27e7\u7c97\u9ad4\u27e62\u27e7` reported zero issues
# until 2026-07-28 and rendered `</b>\u7c97\u9ad4<b>`. Sources are written as markup here
# rather than as hand-built records, because the record shape is `mask`'s to
# define and a fixture that hard-codes it stops testing the thing that broke.


def test_standalone_placeholders_may_still_reorder():
    masked, slots = mask("Run `go build` after https://x.dev/a")
    assert "tags" not in _rules(masked, "\u5148\u770b \u27e62\u27e7 \u518d\u57f7\u884c \u27e61\u27e7", slots=slots)


def test_an_inverted_pair_is_an_error():
    masked, slots = mask("<b>bold</b>")
    assert masked == "\u27e61\u27e7bold\u27e62\u27e7"
    assert "tags" in _rules(masked, "\u27e62\u27e7\u7c97\u9ad4\u27e61\u27e7", slots=slots)


def test_a_pair_kept_in_order_passes_wherever_it_moves():
    masked, slots = mask("The <b>fast</b> server.")
    assert "tags" not in _rules(masked, "\u9019\u53f0\u4f3a\u670d\u5668\u27e61\u27e7\u5f88\u5feb\u27e62\u27e7", slots=slots)


def test_crossed_pairs_are_an_error():
    masked, slots = mask("<b><i>x</i></b>")
    assert masked == "\u27e61\u27e7\u27e62\u27e7x\u27e63\u27e7\u27e64\u27e7"
    crossed = "\u27e61\u27e7\u27e62\u27e7\u5b57\u27e64\u27e7\u27e63\u27e7"
    assert "tags" in _rules(masked, crossed, slots=slots)


def test_nesting_that_survives_is_not_flagged():
    # The must-not-fire half of the rule above: same four placeholders, still
    # nested, moved as a unit. A validator that fails this one is worse than none.
    masked, slots = mask("<b><i>x</i></b>")
    assert "tags" not in _rules(masked, "\u8b6f\u6587\u27e61\u27e7\u27e62\u27e7\u5b57\u27e63\u27e7\u27e64\u27e7", slots=slots)


# --- containment: what a target does to the block it lands in ----------------
#
# Invariant 2a guarantees the bytes around a segment and says nothing about what
# a target does once substituted between them. The five cases below were measured
# 2026-07-27 and every one reported zero errors and zero warnings; they are the
# specification of the rule, so they are written as the source and the target
# that produced the damage rather than as hand-built segments.
#
# Every rule here gets both halves. A validator that cries wolf is ignored, and
# the zh-TW lexicon above is in this file precisely because it had been failing
# correct Traditional Chinese \u2014 trading one failure direction for the other is
# not a repair.

_STRUCTURAL = {"containment", "escaping", "eol"}


def _structural(kind, source, target, host=None, cfg=CFG):
    """Invariant 2b issues as (rule, severity).

    Severity is half of what B2 decided \u2014 these stop a build \u2014 so this does not
    collapse to rule names the way `_rules` does.
    """
    issues = check_segment(_seg(source, target, kind=kind, host=host), "zh-TW", cfg, [], [])
    return [(i["rule"], i["severity"]) for i in issues if i["rule"] in _STRUCTURAL]


#: The five measured cases, and beside each an ordinary translation of the same
#: source. One list, so a fixture cannot be added to one half and forgotten in
#: the other.
MEASURED = [
    ("cell", "one", "\u542b|\u7ba1\u7dda", "\u4e00"),
    ("para", "Some sentence here.", "1. \u9019\u662f\u8b6f\u6587", "\u9019\u88e1\u6709\u4e00\u53e5\u8a71\u3002"),
    ("para", "Some sentence here.", "\u8b6f\u6587\n# \u6191\u7a7a\u9577\u51fa\u7684\u6a19\u984c",
     "\u9019\u662f\u4e00\u53e5\u666e\u901a\u7684\u8b6f\u6587\u3002"),
    ("heading", "Title", "\u4e0a\u534a\n\n\u4e0b\u534a", "\u6a19\u984c"),
    ("quote", "quoted line", "\u7b2c\u4e00\u884c\n\u9038\u51fa\u5f15\u8a00\u7684\u7b2c\u4e8c\u884c", "\u5f15\u7528\u7684\u4e00\u884c"),
]


@pytest.mark.parametrize("kind, source, damaging, _clean",
                         MEASURED, ids=["cell-pipe", "para-becomes-list",
                                        "para-invents-heading", "heading-splits",
                                        "quote-leaks"])
def test_the_five_measured_structural_cases_fail_the_build(kind, source, damaging, _clean):
    assert _structural(kind, source, damaging) == [("containment", "error")]


def test_a_blank_target_stops_at_the_missing_rule_and_never_reaches_containment():
    """What actually keeps the host-profile rewrite from changing Markdown.

    The rewrite guarded the single-line branch with `len(tgt_lines) >
    len(src_lines)`, so a single-line-source heading with an *empty* target now
    falls through to the blank-line rule where the old code answered nothing.
    Measured 2026-08-02: 129 such differences over an extended shape sweep, and
    none of them reachable — `check_segment` returns at its `not tgt.strip()`
    guard first, so every one of them is reported as `missing` and stops there.

    This test is that guard's only statement in the suite. `containment_problems`
    is public and a direct caller does not inherit the early return, so if the
    guard is ever moved or removed, the divergence becomes real and this fails.
    """
    for kind in ("heading", "quote", "cell", "para"):
        for target in ("", " ", "\n", "\t"):
            issues = check_segment(_seg("Title", target, kind=kind),
                                   "zh-TW", CFG, [], [])
            assert [i["rule"] for i in issues] == ["missing"], (kind, repr(target))


@pytest.mark.parametrize("kind, source, _damaging, clean",
                         MEASURED, ids=["cell", "para-1", "para-2", "heading", "quote"])
def test_an_ordinary_translation_of_each_source_is_clean(kind, source, _damaging, clean):
    # Every rule, not only the structural ones: a translation this plain must
    # produce no issue at all, or the fixture is measuring something else.
    assert check_segment(_seg(source, clean, kind=kind), "zh-TW", CFG, [], []) == []


@pytest.mark.parametrize("kind, source, target", [
    # A nested list item and a nested blockquote are ordinary input, and their
    # segments legitimately begin with a marker. This is why the rule compares
    # against the source instead of stating an absolute.
    ("list", "- inner item", "- \u5167\u5c64\u9805\u76ee"),
    ("list", "- inner item", "1. \u5167\u5c64\u9805\u76ee"),
    ("quote", "> inner", "> \u5167\u5c64"),
    # A wrapped paragraph and a wrapped list item keep their line breaks, and a
    # translation is free to rewrap: line count is never compared.
    ("para", "A long sentence\nwrapped over two lines.", "\u4e00\u53e5\u5f88\u9577\u7684\u53e5\u5b50\n\u8de8\u5169\u884c\u5beb\u6210\u3002"),
    ("para", "A long sentence\nwrapped over two lines.", "\u5beb\u6210\u4e00\u884c\u7684\u8b6f\u6587\u3002"),
    # `mdparse` stops a list continuation at a list, a heading and a fence, but
    # not at a quote \u2014 so a source continuation *can* carry a block start, and
    # the target may keep it.
    ("list", "item\n  > quoted", "\u9805\u76ee\n  > \u5f15\u8a00"),
    # A marker that is not line-initial is text.
    ("para", "Use the # sign.", "\u4f7f\u7528 # \u7b26\u865f\u3002"),
    ("para", "A well-known name.", "\u4e00\u500b - \u77e5\u540d\u7684\u540d\u5b57\u3002"),
    # The pipe rule belongs to cells. A pipe in prose is prose.
    ("para", "Choose A or B.", "\u9078\u7532 | \u4e59\u3002"),
    # A heading's and a cell's content is an inline context: a marker there is
    # literal text, and flagging it would fail correct work.
    ("heading", "The # sign", "# \u865f\u7b26\u865f"),
    ("cell", "dash", "- \u7834\u6298\u865f"),
])
def test_structure_the_source_already_had_is_not_flagged(kind, source, target):
    assert _structural(kind, source, target) == []


def test_a_cell_may_carry_a_pipe_that_arrived_inside_a_placeholder():
    # The rule counts pipes in the *restored* text, so a code span containing one
    # must not be charged to the translation that kept it where it was.
    masked, slots = mask("`a|b`")
    seg = _seg(masked, masked, slots=slots, kind="cell")
    assert [i for i in check_segment(seg, "zh-TW", CFG, [], [])
            if i["rule"] in _STRUCTURAL] == []


@pytest.mark.parametrize("kind, source, target", [
    ("list", "item", "\u9805\u76ee\n- \u7b2c\u4e8c\u9805"),          # a sibling item
    ("list", "- inner", "\u8b6f\u6587\n- \u5167\u5c64"),             # one nested item becomes two siblings
    ("para", "One sentence.", "\u4e0a\u534a\n\n\u4e0b\u534a"),        # a blank line ends the block
    ("para", "One sentence.", "\u4e00\u53e5\u8a71\u3002\n"),          # so does a trailing one
    ("para", "One sentence.", "\u8b6f\u6587\n```"),                   # a fence swallows the rest of the file
    ("para", "One sentence.", "\u8b6f\u6587\n---"),                   # a thematic break
    ("para", "One sentence.", "> \u5f15\u8a00"),                      # a blockquote
    ("cell", "one", "\u4e00\n\u4e8c"),                                # a cell is one line
    # The worst of the family: this one renders to nothing at all.
    ("para", "One sentence.", '[foo]: http://example.com "title"'),
])
def test_a_block_the_translation_invents_is_an_error(kind, source, target):
    assert _structural(kind, source, target) == [("containment", "error")]


def test_a_target_that_becomes_a_link_definition_does_not_merely_move_the_text():
    """Why the link-definition row is in the table, stated as the damage it does.

    The other block starts put the translated text in the wrong block, where a
    reviewer can see it. This one deletes it: the rendered document contains a
    link reference definition and no prose, and re-parsing finds no segment at
    all, so the text is gone and unrecoverable by re-extracting. Found by an
    adversarial pass after the first six rows were already in, which is why it
    gets its own test rather than a line in the table above.
    """
    nodes, segs = parse("Some sentence here about a topic.\n")
    segs[0]["target"] = '[foo]: http://example.com "link title"'
    assert _structural("para", segs[0]["masked"], segs[0]["target"]) \
        == [("containment", "error")]

    out, _missing = render({"nodes": nodes, "segments": segs, "lang": "zh-TW"}, CFG)
    _nodes2, segs2 = parse(out)
    assert segs2 == [], "the fixture must actually demonstrate the segment vanishing"


def test_a_source_link_definition_is_folded_into_a_raw_node():
    # The must-not-fire half for the ordinary case. It is *not* the reason the
    # row costs nothing — the comment here used to say "no source segment line
    # can ever be one, by construction", and 2026-08-03 measured that false:
    # this rule is not one of the paragraph loop's stop conditions, and a
    # definition that may not interrupt a paragraph is prose. What keeps the
    # rule free of false positives is symmetry, which the two tests below pin.
    _nodes, segs = parse('[foo]: http://example.com "title"\n\nOrdinary prose.\n')
    assert [s["source"] for s in segs] == ["Ordinary prose."]


def test_a_target_that_only_looks_like_a_link_definition_is_not_reported():
    """The false positive `DEF_RE` used to raise at error severity.

    A line is a definition when the whole line parses, so a target carrying a
    space where a destination may not have one is an ordinary paragraph and
    renders as its own text. Reporting it failed correct work — the direction
    `docs/decisions.md` calls the more expensive one, because the model is fed
    these messages back and asked to change a translation that was right.

    Each row is confirmed by re-parsing the rendered document: a target that is
    genuinely a definition leaves no segment behind, and these leave one.
    """
    for target in ("[安娜]: 你好 世界",
                   "[x]: /url not a title",
                   "[x]: <a destination that never closes"):
        assert _structural("para", "One sentence.", target) == [], target
        nodes, segs = parse("One sentence.\n")
        segs[0]["target"] = target
        out, _missing = render(
            {"nodes": nodes, "segments": segs, "lang": "zh-TW"}, CFG)
        assert parse(out)[1], (target, "the row must actually demonstrate the "
                                       "segment surviving")


def test_a_link_definition_the_source_also_opens_is_not_reported():
    # The symmetry half, and the reason the rule is safe on a source line that
    # *is* a definition: both sides come through `_block_start`, so a source
    # answering "link reference definition" licenses a target answering the
    # same. `[lazy]: /url` is such a source — well formed, and prose only
    # because a definition may not interrupt the paragraph above it.
    assert _structural("para", "[lazy]: /url", "[延遲]: /url") == []


#: The three patterns that cap the indent they will match — CommonMark spells it
#: `\s{0,3}` — paired with a source that sits four columns in, which is what a
#: list item's second paragraph is. The other four are anchored `^\s*` and were
#: never blinded.
_CAPPED_AT_THREE = [
    ("heading", "# 標題"),
    ("thematic break", "***"),
    ("setext heading", "====="),
]


@pytest.mark.parametrize("opens, target", _CAPPED_AT_THREE,
                         ids=[n for n, _ in _CAPPED_AT_THREE])
def test_a_block_start_is_seen_through_the_indent_the_segment_sits_in(opens, target):
    """The coverage the reseat would have removed, if `_block_start` still read raw lines.

    Since 2026-08-03 an indented segment's target carries the source's leading
    run by construction, so `'    # 標題'` reaches this rule instead of `'# 標題'`
    — and three of the seven patterns stop matching at four columns. Measured the
    day the reseat landed: `lx check` printed `0 error(s)` and exited 0 while
    markdown-it-py rendered an `<h1>` where the source had a `<p>`, which is
    invariant 10's exit code claiming something untrue. Found by adversarial
    review, not by the suite.
    """
    source = "    A second paragraph."
    got, why = accept({"masked": source}, target, "zh-TW", CFG)
    assert why is None and got == "    " + target
    assert _structural("para", source, got) == [("containment", "error")]
    assert containment_problems({"masked": source, "target": got, "kind": "para"}) \
        == [f"the target opens a {opens}; the source does not"]


@pytest.mark.parametrize("opens, target", _CAPPED_AT_THREE,
                         ids=[n for n, _ in _CAPPED_AT_THREE])
def test_an_indented_source_that_already_opens_that_block_is_not_flagged(opens, target):
    """The must-not-fire half: lstripping is monotone, so both sides move together.

    Every pattern in the table is anchored `^\\s*` or `^\\s{0,3}`, so removing a
    line's leading blanks can only make a match appear. That is safe *because*
    both sides of the comparison come through `_block_start` — a source that
    genuinely opens the block keeps answering the same name, and the target is
    then free to open it too.
    """
    source = "    " + target + " source text"
    assert _structural("para", source, "    " + target + " 譯文") == []


def test_containment_is_disableable_like_every_other_rule():
    # Default on at error severity; a project that genuinely needs it off records
    # that choice in its own config.
    cfg = {**CFG, "checks_disabled": ["containment"]}
    assert _structural("para", "Some sentence here.", "1. \u9019\u662f\u8b6f\u6587", cfg=cfg) == []


# --- host escaping, and the carriage return a segment may not invent ---------
#
# Markdown declares no escaping requirement, so the table has no live row until
# EPUB lands: `render()` performs no escaping of slot values at all, and on XHTML
# that produces a file which does not parse. The rule is written against a host
# rather than against a kind so the format that emits XHTML segments only has to
# set `host` on them. These fixtures are what a segment from such a format looks
# like \u2014 which is also the only way to test a rule with no live caller yet.


@pytest.mark.parametrize("target, expected", [
    ("a < b", [("escaping", "error")]),
    ("AT&T", [("escaping", "error")]),
    ("x ]]> y", [("escaping", "error")]),
    ("&amp; &#8212; &#x2014; &lt;", []),      # already written as references
    ("a > b", []),                            # legal character data on its own
])
def test_an_xml_host_rejects_what_it_cannot_hold(target, expected):
    assert _structural("para", "source text", target, host="xhtml") == expected


@pytest.mark.parametrize("target", ["a < b", "AT&T", "x ]]> y"])
def test_markdown_declares_no_escaping_requirement(target):
    # The same three targets, and the same default a document carries today.
    assert _structural("para", "source text", target) == []


def test_a_target_that_invents_a_carriage_return_is_an_error():
    # A document whose terminators arrived mixed keeps its CRs in the segment
    # source, so the model is asked to reproduce one. Measured 2026-07-28: five
    # different replies to one such segment \u2014 CRLF kept, LF only, lines joined, a
    # break added, a bare CR \u2014 all produced zero errors, so the repair loop could
    # not see a wrong one.
    assert _structural("para", "line one", "\u7b2c\u4e00\u884c\r\n\u7b2c\u4e8c\u884c") == [("eol", "error")]


def test_a_carriage_return_the_source_already_had_is_not_flagged():
    # One-directional, and not to be widened: a target that *drops* a CR cannot
    # be flagged, because a translation may rewrap and comparing break counts
    # fails the legitimate case.
    assert _structural("para", "a\r\nb", "\u7532\r\n\u4e59") == []
    assert _structural("para", "a\r\nb", "\u7532\u4e59") == []


def test_missing_number_is_an_error():
    assert "numbers" in _rules("Requires Go 1.22 exactly.", "\u9700\u8981 Go 1.21\u3002")


def test_lexicon_flags_a_nonpreferred_term():
    assert "lexicon" in _rules("The server caches data.", "\u670d\u52d9\u5668\u6703\u7de9\u5b58\u6578\u64da\u3002")


# --- the zh-TW lexicon, in both directions -----------------------------------
#
# Until 2026-07-28 the table failed correct Traditional Chinese, one case at
# error severity, because a plain substring match cannot tell \u7269\u9ad4\u7684\u8cea\u91cf (mass)
# from \u54c1\u8cea. Fixtures below are written raw rather than escaped: for a lexicon
# rule the fixture *is* the specification, and \u6578\u64da is not reviewable.

def _lexicon(target, cfg=CFG):
    """Lexicon issues for a target as (severity, message).

    Severity is half the assertion here \u2014 error stops a build, warn costs a
    reviewer three seconds \u2014 so this deliberately does not collapse to rule names
    the way `_rules` does.
    """
    issues = check_segment(_seg("A source sentence.", target), "zh-TW", cfg, [], [])
    return [(i["severity"], i["message"]) for i in issues if i["rule"] == "lexicon"]


@pytest.mark.parametrize("target", [
    "\u4f9d\u7167\u6cd5\u5f8b\u7a0b\u5e8f\u8fa6\u7406",       # \u7a0b\u5e8f \u2014 a legal procedure, not \u7a0b\u5f0f
    "\u7269\u9ad4\u7684\u8cea\u91cf\u662f\u5169\u516c\u65a4",     # \u8cea\u91cf \u2014 mass, not \u54c1\u8cea
    "\u4ed6\u652f\u6301\u9019\u9805\u63d0\u6848",         # \u652f\u6301 \u2014 endorsement, not \u652f\u63f4
    "\u95b1\u8b80\u539f\u59cb\u6587\u672c",           # \u6587\u672c \u2014 the text itself, not \u6587\u5b57
    "\u5206\u6790\u9019\u6279\u6578\u64da",           # \u6578\u64da \u2014 measured readings; this one failed the build
])
def test_lexicon_passes_correct_traditional_chinese(target):
    assert _lexicon(target) == []


@pytest.mark.parametrize("target", [
    "\u6709\u7dda\u96fb\u8996\u983b\u9053\u5f88\u591a",       # \u96fb\u8996 + \u983b\u9053, not \u8996\u983b
    "\u8001\u9f20\u6a19\u672c\u5df2\u7d93\u7de8\u865f",       # \u8001\u9f20 + \u6a19\u672c, not \u9f20\u6a19
    "\u517c\u5bb9\u4e26\u84c4\u7684\u614b\u5ea6",         # the idiom, not \u517c\u5bb9
])
def test_lexicon_guard_exempts_a_longer_word(target):
    assert _lexicon(target) == []


@pytest.mark.parametrize("target", [
    "\u9ad4\u5167\u5b58\u5728\u6297\u9ad4",           # \u9ad4\u5167 + \u5b58\u5728, not \u5167\u5b58
    "\u53c3\u6578\u7d44\u5408\u592a\u591a",           # \u53c3\u6578 + \u7d44\u5408, not \u6578\u7d44
    "\u76ae\u5e36\u5bec\u5ea6\u4e0d\u8db3",           # \u76ae\u5e36 + \u5bec\u5ea6, not \u5e36\u5bec
    "\u523a\u6fc0\u6d3b\u5316\u7d30\u80de",           # \u523a\u6fc0 + \u6d3b\u5316, not \u6fc0\u6d3b
    "\u6536\u96c6\u6210\u679c\u4e26\u6b78\u6a94",         # \u6536\u96c6 + \u6210\u679c, not \u96c6\u6210
    "\u5f37\u8abf\u8a66\u7528\u671f\u7684\u898f\u5247",       # \u5f37\u8abf + \u8a66\u7528, not \u8abf\u8a66
    "\u6eab\u5ea6\u7684\u6539\u8b8a\u91cf\u5f88\u5c0f",       # \u6539\u8b8a + \u91cf, not \u8b8a\u91cf
    "\u6062\u5fa9\u7528\u96fb\u4e4b\u5f8c\u518d\u8a66",       # \u6062\u5fa9 + \u7528\u96fb, not \u5fa9\u7528
])
def test_lexicon_collision_never_fails_the_build(target):
    issues = _lexicon(target)
    assert issues, "row removed rather than demoted \u2014 retire this fixture with it"
    assert all(sev == "warn" for sev, _ in issues)


@pytest.mark.parametrize("target", [
    "\u8acb\u5148\u5b89\u88dd\u9019\u500b\u8edf\u4ef6",       # \u8edf\u9ad4
    "\u8abf\u6574\u87a2\u5e55\u7684\u8996\u983b\u8a2d\u5b9a",     # \u5f71\u7247 \u2014 guarded, but \u8a2d is not a \u983bX continuation
    "\u9ede\u64ca\u9f20\u6a19\u53f3\u9375",           # \u6ed1\u9f20 \u2014 guarded, but \u53f3 is not \u672c
    "\u9019\u500b\u7248\u672c\u4e0d\u517c\u5bb9",         # \u76f8\u5bb9 \u2014 guarded, but \u4e26 does not follow
])
def test_lexicon_still_fails_the_build_on_an_unambiguous_form(target):
    assert [sev for sev, _ in _lexicon(target)] == ["error"]


def test_lexicon_leaves_a_clean_translation_alone():
    assert _lexicon("\u9019\u53f0\u4f3a\u670d\u5668\u7684\u8edf\u9ad4\u5f88\u5feb") == []


def test_lexicon_extra_still_adds_a_project_term_at_error():
    # A list gives the severity; a bare string means error. The second form is how
    # a project restores a row the audit removed, in a domain where it is decidable.
    cfg = {**CFG, "lexicon_extra": {"\u5143\u6578\u64da": ["\u4e2d\u7e7c\u8cc7\u6599", "error"], "\u767b\u9304": "\u767b\u5165"}}
    assert [sev for sev, _ in _lexicon("\u5beb\u5165\u5143\u6578\u64da", cfg)] == ["error"]
    assert [sev for sev, _ in _lexicon("\u5b8c\u6210\u767b\u9304\u7a0b\u5e8f", cfg)] == ["error"]


def test_glossary_forbidden_variant():
    g = [{"source": "repository", "target": "\u5132\u5b58\u5eab", "forbidden": ["\u5009\u5eab"], "severity": "error"}]
    assert "glossary" in _rules("Clone the repository.", "\u8907\u88fd\u5009\u5eab\u3002", glossary=g)


def test_clean_segment_passes():
    assert _rules("Run the server on port 8080.", "\u5728\u9023\u63a5\u57e0 8080 \u4e0a\u57f7\u884c\u4f3a\u670d\u5668\u3002") == set()


def test_normalize_fixes_width_and_spacing():
    got = normalize("\u8b66\u544a:\u8acb\u5148\u57f7\u884cmake build\u6307\u4ee4", "zh-TW", CFG)
    assert got == "\u8b66\u544a\uff1a\u8acb\u5148\u57f7\u884c make build \u6307\u4ee4"


# Two spaces before a line break are a Markdown hard break. Deleting one is a
# content change landing on a translation that did the right thing, so these
# assert on the target side of what `tests/corpus/hard-line-breaks.md` gates for
# the source side.
_L1, _L2 = "\u7b2c\u4e00\u884c", "\u7b2c\u4e8c\u884c"


@pytest.mark.parametrize("text", [
    f"{_L1}  \n{_L2}",                    # the plain case
    f"{_L1}\u3002  \n{_L2}\u3002",        # after fullwidth punctuation, where a
                                          # zh-TW line ends far more often than
                                          # not — `punct` ate this one before
                                          # `collapse_space` could protect it
    f"{_L1}  \r\n{_L2}",                  # a mixed-terminator document keeps its
                                          # CRLF inside the segment
    f"{_L1}\u3002  \r\n{_L2}\u3002",      # both at once
    "Hello  \nWorld",                     # no CJK anywhere in the segment
    # Nothing after the break but the newline itself. A parser never emits a
    # segment ending this way — the terminator is a raw node — and since
    # 2026-08-03 neither `accept` nor `cli.do_apply` hands `normalize` one
    # either, because both strip before they call it. These rows are therefore a
    # property of this function alone, asserted directly rather than through a
    # caller: they are what fails when the trailing strip anchors on `$`, which
    # in Python also matches before a final newline, and the CRLF pair above
    # cannot catch it because `$` does not match before a CR.
    # Found by the 2026-08-02 mutation pass, on a guard HANDOFF-010 added.
    f"{_L1}  \n",
    f"{_L1}。  \n",
])
def test_normalize_keeps_a_markdown_hard_break(text):
    assert normalize(text, "zh-TW", CFG) == text


@pytest.mark.parametrize("run", ["   ", "    ", " \t  "])
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_normalize_canonicalizes_a_long_hard_break_run_to_two_spaces(run, eol):
    # Three or more mean the same <br>; the surplus is the editor noise this op
    # exists to remove. `docs/decisions.md`, 2026-07-29.
    assert normalize(f"{_L1}{run}{eol}{_L2}", "zh-TW", CFG) == f"{_L1}  {eol}{_L2}"


@pytest.mark.parametrize("text,want", [
    ("a  b", "a b"),                              # the run the op exists for
    ("a    b", "a b"),
    # One space is not a hard break; a tab run is not one either — making it
    # two spaces would invent a break nobody wrote — and a blank-only line has
    # none to protect. Each is repeated before a CRLF, because the removal side
    # of a line end is as easy to get wrong as the keeping side: only the CRLF
    # rows fail when the line-end pass stops recognizing a carriage return.
    (f"{_L1} \n{_L2}", f"{_L1}\n{_L2}"),
    (f"{_L1} \r\n{_L2}", f"{_L1}\r\n{_L2}"),
    (f"{_L1}\t\t\n{_L2}", f"{_L1}\n{_L2}"),
    (f"{_L1}\t\t\r\n{_L2}", f"{_L1}\r\n{_L2}"),
    (f"{_L1}\n   \n{_L2}", f"{_L1}\n\n{_L2}"),
    (f"{_L1}\r\n   \r\n{_L2}", f"{_L1}\r\n\r\n{_L2}"),
    # Nothing follows, so the run marks nothing. The first two do not end in
    # fullwidth punctuation, so they are the rows that actually reach
    # `collapse_space` — `punct` strips the third on its own.
    (f"{_L1}  ", _L1),
    ("abc  ", "abc"),
    ("\u7d50\u5c3e\u3002  ", "\u7d50\u5c3e\u3002"),
    # A run that begins the segment's last line with nothing after it indents
    # nothing, and goes for the same reason a blanks-only line is emptied. This
    # is the row that fails if the line-start guard is put on the `\Z` strip too.
    (f"{_L1}\n  ", f"{_L1}\n"),
    # A lone CR is a character in a sentence, not a line ending
    # (`docio.split_terminator`), so what follows it is interior text. The row
    # that fails if the guard's class is widened to `[^ \t\r\n]`.
    ("a\r  b", "a\r b"),
    # `punct`'s two whitespace rules, in their interior form. Both keep a
    # line-start indent now, and both must still do their own job in the middle
    # of a line \u2014 the rows that fail if the guard is bolted on with a `+` where
    # the rule wanted nothing at all.
    (f"{_L1}  \u300c\u5f15\u8ff0\u300d", f"{_L1}\u300c\u5f15\u8ff0\u300d"),
    ("\u7d50\u5c3e\u3002  \u63a5\u8457", "\u7d50\u5c3e\u3002\u63a5\u8457"),
])
def test_normalize_still_removes_whitespace_that_means_nothing(text, want):
    assert normalize(text, "zh-TW", CFG) == want


# A run of blanks at the *start* of a line is a wrapped block's continuation
# indent \u2014 inside the segment by construction, because a raw node can only sit
# before or after a whole one (AGENTS.md invariant 3). The source side of this is
# already gated by `tests/corpus/`, which substitutes sources back into the
# skeleton and never calls `normalize`; that is exactly why nothing here saw the
# ops rewriting it. These are the target side.
#
# Confirmed against a real CommonMark render (markdown-it-py, 2026-08-02) rather
# than assumed: for a prose continuation the damage is cosmetic \u2014 lazy
# continuation makes `- item\n continued` and `- item\n    continued` one
# paragraph either way \u2014 and for a continuation that could open a block it is
# structural. `- outer\n    - nested` renders as a nested list and
# `- outer\n - nested` as two siblings; an indented code block stops being
# `<pre><code>` and becomes `<p>`.

#: Named rather than swept, so deleting a fixture is a failure with a name.
_INDENT_FIXTURES = [
    "list-continuation-indent.md",              # four spaces, two, and an ordered three
    "list-continuation-two-space-indent.md",    # dash and star markers
    "crlf-list-items.md",                       # the same over CRLF
    "html-block.md",                            # interior indent between masked tags
]
# `indented-code-block.md` was here until 2026-08-02 and is not any more: its
# indented lines are in the skeleton now, so it has no segment with one in it and
# the guard below fires. The guard is doing its job, not failing — this entry
# came out rather than the fixture being edited, which is the red line in
# AGENTS.md. Position 0 is still reachable and still covered: a list item's
# second paragraph keeps its indent inside the segment, and the parametrization
# further down carries that shape.


def _opens_a_line_with_a_blank(text):
    return any(line[:1] in (" ", "\t") for line in text.split("\n"))


@pytest.mark.parametrize("name", _INDENT_FIXTURES)
def test_normalize_keeps_a_continuation_indent_at_its_own_width(name):
    """Parsed from the fixture, never quoted from it, so it keeps testing the file."""
    text = (CORPUS / name).read_bytes().decode("utf-8")
    segs = parse(text)[1]
    assert any(_opens_a_line_with_a_blank(s["masked"]) for s in segs), (
        f"{name} no longer has a segment with an indented line in it, so this "
        f"test measures nothing \u2014 fix the fixture list, not the fixture")
    for seg in segs:
        assert normalize(seg["masked"], "zh-TW", CFG) == seg["masked"], seg["id"]


@pytest.mark.parametrize("text", [
    # The shapes a zh-TW indented block actually opens on. Every one of these
    # lost its indent to `punct` outright \u2014 it deletes the run rather than
    # shortening it \u2014 which is why that op could not stay out of scope.
    "\u7b2c\u4e00\u884c\n  \u300c\u5f15\u8ff0\u300d",
    "\u7b2c\u4e00\u884c\n    \u2014\u2014\u4ed6\u8aaa",
    "\u7b2c\u4e00\u884c\n  \uff08\u8a3b\uff09",
    "\u7b2c\u4e00\u884c\n  \u2026\u2026\u7136\u5f8c",
    # And the ones that only `collapse_space` reached.
    "\u7b2c\u4e00\u884c\n    \u4ed6\u8aaa\u8a71",
    "\u7b2c\u4e00\u884c\n    hello",
    "\u7b2c\u4e00\u884c\n\t\t\u300c\u5f15\u8ff0\u300d",
    # Fullwidth punctuation ending the line before, where `punct`'s other
    # whitespace rule would reach if its lookbehind could see past a newline.
    "\u7b2c\u4e00\u884c\u3002\n    \u7e8c\u884c",
    # Three lines of verse: an indent must survive at four, not arrive at two,
    # which is what a lookbehind that only excludes `\n` produces.
    "\u98a8\u5439\u904e\n    \u96e8\u843d\u4e0b\n    \u5929\u4eae\u4e86",
    # Position 0. Both callers strip before they reach normalize and reseat the
    # source's own runs afterwards (2026-08-03), so what arrives here at position
    # 0 is a line start *inside* a reseated lead \u2014 `"- \n      text"` is the real
    # shape \u2014 rather than a segment's own indent. The guard is what keeps the two
    # from being one question: an indented code block stopped arriving here on
    # 2026-08-02 when it became skeleton, and a list item's second paragraph,
    # `- item\n\n    text`, never did stop.
    "  \u300c\u958b\u5834\u300d\n\u7b2c\u4e8c\u884c",
    "    \u9019\u662f\u6e05\u55ae\u9805\u76ee\u7684\u7b2c\u4e8c\u6bb5\u3002",
    # An indent need not be made of the characters the op matches. U+3000 is the
    # zh-TW paragraph indent \u2014 `textparse._blank` and `_indented` both know it \u2014
    # and a mixed \u3000+spaces indent is what a paste from a PDF, or a model padding
    # a Chinese line to its source's column, produces. These rows fail when the
    # guard's class names ASCII blanks instead of `\S`: the U+3000 survives and
    # the spaces behind it do not. Found by adversarial review, 2026-08-02.
    "\u6cb3\u6c34\u6536\u4e0b\u4e00\u5207\uff0c\n\u3000  \u300c\u53ea\u9084\u7528\u4e0d\u4e0a\u7684\u3002\u300d",
    "\u6cb3\u6c34\u6536\u4e0b\u4e00\u5207\uff0c\n\u3000    \u4ed6\u8f49\u904e\u8eab",
    "\u7b2c\u4e00\u884c\n\xa0   \u300c\u5f15\u8ff0\u300d",
    "\u6cb3\u6c34\u6536\u4e0b\u4e00\u5207\uff0c\n\u3000\u3000\u4ed6\u8f49\u904e\u8eab",
    # A form feed separates chapters in an older .txt, and U+2028 is in
    # `tests/corpus-text/line-separator-control-chars.txt` precisely because
    # `str.split("\n")` does not break on it.
    "\u7b2c\u4e00\u884c\n\x0c  \u300c\u5f15\u8ff0\u300d",
    "\u7b2c\u4e00\u884c\n\u2028  \u300c\u5f15\u8ff0\u300d",
])
def test_normalize_keeps_a_continuation_indent_verbatim_in_a_target(text):
    assert normalize(text, "zh-TW", CFG) == text


def test_a_blank_between_words_is_kept_when_it_could_be_an_indent():
    """The price of the guard's class, stated rather than discovered later.

    One character of context cannot tell an ideographic space used *between
    words* from one opening an indent, so a run behind either is left alone. The
    two failures are not the same size: a surplus space is invisible where a
    deleted indent reflows a code block into prose. `docs/decisions.md`,
    2026-08-02.
    """
    assert normalize("a\u3000  b", "zh-TW", CFG) == "a\u3000  b"
    assert normalize("a  b", "zh-TW", CFG) == "a b"


def test_accept_keeps_a_hard_break_the_model_got_right():
    # The property that matters: all three reuse paths — model output, `lx apply`
    # and a translation-memory hit — reach normalize, and two of them through
    # here. A unit test on normalize alone would miss `accept`'s own strip().
    text = (CORPUS / "hard-line-breaks.md").read_bytes().decode("utf-8")
    seg = next(s for s in parse(text)[1] if "\n" in s["masked"])
    target = ("\u4e00\u884c\u4ee5\u5169\u500b\u7a7a\u683c\u7d50\u5c3e\u3002  \n"
              "\u5728\u786c\u63db\u884c\u5f8c\u7e7c\u7e8c\u3002  \n"
              "\u9084\u6709\u7b2c\u4e09\u884c\u3002")
    assert accept(seg, target, "zh-TW", CFG) == (target, None)


# --- the blanks a segment opens and closes with belong to the source ---------
#
# `accept` did `repair_placeholders(text).strip()`, which deletes a run of blanks
# at position 0 of the proposed target. HANDOFF-018 left that alone on the stated
# premise that once an indented code block is skeleton, no segment starts with an
# indent — and the premise is false. A list item's second paragraph is
# `- item\n\n    text`, and `mdparse` puts those four spaces at the front of the
# segment, where deleting them takes the paragraph out of the item.
#
# Measured 2026-08-02 against markdown-it-py over 441 generated documents —
# format x container x blank character x width x which end — of which 76 changed
# what the document *is* rather than how it looks. Both ends were wrong, and the
# trailing end only shows up on an axis the first sweep held constant: whether a
# line the skeleton owns follows the segment. `mdparse` emits one segment per
# blockquote line, so the two spaces of a hard break between `> first` and
# `> second` sit at the *end* of a segment with the newline that gives them
# meaning outside it, and the unconditional rstrip deleted the `<br>`.
#
# The rule is re-imposition, not preservation: what the model returns is a
# sentence, and it is reseated in the source's own runs. So a model that dropped
# the indent gets it back, and a model that padded a segment with no run of its
# own still loses the padding — the reason the strip exists in the first place.
#
# `normalize.reseat_outer_blanks` runs *after* `normalize`, and that order is
# load-bearing rather than stylistic: `collapse_space` ends in `[ \t]+\Z` and is
# in zh-TW's default op list, so a trailing run handed to `normalize` is deleted
# again. The two tests below that translate to a bare sentence are what fail when
# the order is swapped.

_SECOND_PARA = "- item one\n\n    A second paragraph.\n"
_QUOTE_BREAK = "> first line  \n> second line\n"


def _seg_named(text, source):
    seg = next((s for s in parse(text)[1] if s["source"] == source), None)
    assert seg is not None, f"no segment with source {source!r} in {text!r}"
    return seg


@pytest.mark.parametrize("source, proposal", [
    # the model reproduced the indent …
    ("    A second paragraph.", "    譯文。"),
    # … dropped it …
    ("    A second paragraph.", "譯文。"),
    # … or answered with padding of its own instead
    ("    A second paragraph.", "\n\n  譯文。\n"),
])
def test_accept_reseats_a_target_in_the_indent_its_source_has(source, proposal):
    seg = _seg_named(_SECOND_PARA, source)
    assert accept(seg, proposal, "zh-TW", CFG) == ("    譯文。", None)


@pytest.mark.parametrize("padded", [
    "  譯文。",
    "譯文。  ",
    "\n譯文。\n",
    "　譯文。　",
    "\xa0譯文。\xa0",
    "\t譯文。\t",
])
def test_accept_still_removes_padding_a_source_did_not_ask_for(padded):
    """The half the strip exists for, and the half a naive fix loses.

    Models pad their answers, and every reuse path — model output, a memory hit,
    a carryover from the document's own prior state — comes through `accept`.
    """
    seg = _seg_named(_SECOND_PARA, "item one")
    assert accept(seg, padded, "zh-TW", CFG) == ("譯文。", None)


def test_accept_reseats_the_hard_break_that_ends_a_blockquote_segment():
    """The trailing end, on the axis the first sweep held constant.

    Not in `tests/corpus/`: the corpus substitutes each segment's *source* back
    into the skeleton and never calls `accept`, so this shape round-trips there
    whatever `accept` does with a target. `hard-line-breaks.md` covers the source
    side; the break that lives at a segment boundary has to be asserted here.
    """
    seg = _seg_named(_QUOTE_BREAK, "first line  ")
    assert accept(seg, "第一行", "zh-TW", CFG) == ("第一行  ", None)


@pytest.mark.parametrize("indent", [
    " ", "   ", "    ", "\t", "　", "\xa0", "　  ", "  　", "\x0c", " ",
])
def test_accept_reseats_an_indent_whatever_it_is_made_of(indent):
    """`str.strip()` takes U+3000 and U+00A0, so the rule that keeps a run has to.

    The zh-TW paragraph indent is U+3000 and a paste from EPUB leaves U+00A0, so
    a rule written on `" \\t"` would miss the case a novel actually produces.
    `mdparse._indent_columns` counts only space and tab because CommonMark does —
    a different question from what `accept` may delete, and conflating the two is
    the trap.
    """
    seg = dict(_seg_named(_SECOND_PARA, "item one"))
    seg["masked"] = indent + seg["masked"]
    assert accept(seg, "譯文。", "zh-TW", CFG) == (indent + "譯文。", None)


@pytest.mark.parametrize("blank", ["", "   ", "\n", "　"])
def test_accept_refuses_a_blank_target_rather_than_reseating_one(blank):
    """Emptiness is decided on the sentence, never on the reseated string.

    `lead + "" + trail` is truthy, and `render` reads a truthy target — so a
    model that answered with nothing would render four spaces where the
    untranslated marker belongs.
    """
    seg = _seg_named(_SECOND_PARA, "    A second paragraph.")
    assert accept(seg, blank, "zh-TW", CFG) == (None, "empty translation")


@pytest.mark.parametrize("source, trail", [
    ("A paragraph.　", "　"),        # U+3000, what a CJK source document ends on
    ("A paragraph.\xa0", "\xa0"),    # U+00A0, what a paste from EPUB leaves
    ("A paragraph.\t", "\t"),
    ("A paragraph.  ", "  "),
])
def test_accept_reseats_a_trailing_run_of_any_blank_too(source, trail):
    """The set restored has to be the set `str.strip()` deletes, at both ends.

    The leading side is covered by the shapes above; this is the row that fails
    when only the leading side is widened past ASCII, which is how the two ends
    come to disagree about what a blank is. Found by the mutation pass — the
    narrowed `rstrip(" \\t")` survived until this existed.
    """
    seg = dict(_seg_named(_SECOND_PARA, "item one"), masked=source)
    assert accept(seg, "譯文。", "zh-TW", CFG) == ("譯文。" + trail, None)


def test_reseating_a_source_that_is_only_blanks_is_a_no_op():
    """A public function's contract at the degenerate end of its own axis.

    No caller reaches it — both parsers refuse a block with nothing translatable
    in it — but `lead` and `trail` would be the *same run* for such a source, so
    the answer without the guard is wrong rather than merely absent, and this is
    shared by two modules.
    """
    assert reseat_outer_blanks("    ", "譯文。") == "譯文。"
    assert reseat_outer_blanks("", "譯文。") == "譯文。"
    assert reseat_outer_blanks("  x  ", "") == ""
    assert reseat_outer_blanks("  x  ", "   ") == "   "


def test_accept_reseating_is_idempotent():
    seg = _seg_named(_SECOND_PARA, "    A second paragraph.")
    once, _why = accept(seg, "譯文。", "zh-TW", CFG)
    assert accept(seg, once, "zh-TW", CFG) == (once, None)


def test_accept_still_refuses_a_target_whose_placeholders_moved():
    """The reseat must not run before the placeholder set is compared."""
    seg = _seg_named("- item\n\n    Run `make build` now.\n", "    Run `make build` now.")
    assert seg["masked"] == "    Run ⟦1⟧ now."
    got, why = accept(seg, "現在執行。", "zh-TW", CFG)
    assert got is None and "placeholder mismatch" in why


@pytest.mark.parametrize("text, source", [
    # the four shapes HANDOFF-019 measured, plus the two the sweep added
    ("- item one\n\n    A second paragraph.\n", "    A second paragraph."),
    ("1. item one\n\n   A second paragraph.\n", "   A second paragraph."),
    ("- outer\n  - inner\n\n    A second paragraph.\n", "    A second paragraph."),
    # HANDOFF-019 wrote this row as a bare `>     Indented …`, which HANDOFF-020
    # then made a code block inside the quote — CommonMark's reading, and the
    # whole of that package's Defect A. The lazy-continuation spelling is the
    # same measurement on a shape that is still prose: an indented chunk cannot
    # interrupt the quote's open paragraph, so the four spaces stay inside the
    # segment and `accept` still has to reseat them.
    ("> intro\n>     Indented inside a blockquote.\n",
     "    Indented inside a blockquote."),
    ("- [ ] a task\n\n  A second paragraph.\n", "  A second paragraph."),
    ("- item wraps and\ncontinues at the margin.\n\n    A second paragraph.\n",
     "    A second paragraph."),
])
def test_an_indent_a_segment_owns_survives_a_translation_to_itself(text, source):
    """End to end through `render`, on the bytes, for every measured shape.

    Translating each segment to its own source is the strongest form: any
    difference in the rendered file is something the pipeline did, since the
    words did not change.
    """
    nodes, segs = parse(text)
    assert any(s["source"] == source for s in segs), \
        f"{source!r} is no longer a segment of {text!r}"
    for seg in segs:
        target, why = accept(seg, seg["masked"], "zh-TW", CFG)
        assert why is None, (seg["id"], why)
        seg["target"], seg["status"] = target, "translated"
    out, missing = render({"nodes": nodes, "segments": segs, "lang": "zh-TW"}, CFG)
    assert (missing, out) == (0, text)


@pytest.mark.parametrize("name, skeleton, replaced", [
    ("indented-fence-run.md",
     ["    ```", "    ~~~", "    still inside the indented chunk",
      "   held in the skeleton", "  also held in the skeleton", "      ```"],
     ["Ordinary prose that must stay translatable.", "Prose after the list."]),
    ("blockquote-indented-code.md",
     _QUOTED_CODE,
     ["> Introducing a code block inside a blockquote."]),
])
def test_a_translated_document_keeps_the_fence_and_quote_skeletons_verbatim(
        name, skeleton, replaced):
    """The measurement neither `tests/corpus/` nor a segment count can make.

    The round-trip harness substitutes each segment's *source* back, so a block
    that stopped being translated round-trips perfectly and a block handed to the
    model round-trips perfectly too — the two are indistinguishable until a
    target differs from its source. So every segment here is translated to
    something else and the file is rendered: what the parser called skeleton has
    to come out byte for byte, and what it called a segment has to be gone.
    """
    text = (CORPUS / name).read_bytes().decode("utf-8")
    nodes, segs = parse(text)
    for seg in segs:
        target, why = accept(seg, seg["masked"] + "（譯）", "zh-TW", CFG)
        assert why is None, (seg["id"], why)
        seg["target"], seg["status"] = target, "translated"
    out, missing = render({"nodes": nodes, "segments": segs, "lang": "zh-TW"}, CFG)
    assert missing == 0
    assert out != text, "nothing was translated, so this test measured nothing"
    lines = out.split("\n")
    for line in skeleton:
        assert line in lines, line
    for line in replaced:
        assert line not in lines, line


def test_every_corpus_segment_reseated_by_accept_still_renders_the_file():
    """The sweep the parametrized rows above cannot be.

    `test_corpus_roundtrips_byte_for_byte` substitutes each segment's *source*
    and never calls `accept`, so nothing in the corpus could see this defect —
    which is why HANDOFF-019 needed a fixture at all. This is the same corpus put
    through the acceptance path instead.
    """
    for path in _corpus_files():
        text = path.read_bytes().decode("utf-8")
        nodes, segs = parse(text)
        for seg in segs:
            target, why = accept(seg, seg["masked"], "zh-TW", CFG)
            assert why is None, (path.name, seg["id"], why)
            seg["target"], seg["status"] = target, "translated"
        out, missing = render({"nodes": nodes, "segments": segs, "lang": "zh-TW"}, CFG)
        assert missing == 0, path.name
        assert out == text, _explain(path.name, text, out)


@pytest.mark.parametrize("reply", [
    '{"s1": "a"}',
    '```json\n{"s1": "a"}\n```',
    'Sure, here you go:\n{"s1": "a"}\nHope that helps.',
    '[{"id": "s1", "text": "a"}]',
])
def test_reply_parsing_tolerates_chatty_models(reply):
    assert parse_reply(reply)["s1"] == "a"


def test_reply_parsing_rejects_garbage():
    with pytest.raises(ValueError):
        parse_reply("I cannot help with that.")


def test_config_layering_keeps_new_defaults():
    from scriptorium.config import _merge
    merged = _merge(DEFAULT_CONFIG, {"tone": "literary", "batch": {"size": 5}})
    assert merged["tone"] == "literary"
    assert merged["batch"]["size"] == 5
    assert merged["batch"]["concurrency"] == DEFAULT_CONFIG["batch"]["concurrency"]


# --- the language brief, and the register that selects it --------------------
#
# The defect these pin, measured 2026-07-29 at commit f11fb53: `--tone literary`
# could be typed, was stored on the document, and reached `Tone: literary.` in the
# system prompt — and then `_LANG_BRIEFS["zh-TW"]` ended, unconditionally, with
# "Write technical documentation register". The last thing the model read
# overrode the knob two paragraphs above it. See `docs/decisions.md`, D4.


def test_register_brief_replaces_the_documentation_rules_for_a_novel():
    tech = _system_prompt("en", "zh-TW", "technical", "draft")
    lit = _system_prompt("en", "zh-TW", "literary", "draft")

    assert "Write technical documentation register" in tech
    assert "Nominalize headings" in tech
    for documentation_rule in ("Write technical documentation register",
                               "Nominalize headings", "請 for instructions",
                               "subject usually dropped", "technical documentation"):
        assert documentation_rule not in lit
    assert "Write narrative prose" in lit


def test_register_brief_covers_japanese_as_well():
    tech = _system_prompt("en", "ja", "technical", "draft")
    lit = _system_prompt("en", "ja", "literary", "draft")
    assert "technical documentation register" in tech
    assert "technical documentation register" not in lit
    assert "narrative prose" in lit
    assert "全角" in tech and "全角" in lit      # the shared block reaches both


@pytest.mark.parametrize("typed", ["Literary", " literary ", "LITERARY"])
def test_register_brief_ignores_case_and_padding(typed):
    """`--tone Literary` and `--tone literary` naming two registers — and so two
    sets of banked wording — is a split nobody would ever find."""
    assert (_system_prompt("en", "zh-TW", typed, "draft").replace(f"Tone: {typed}.", "")
            == _system_prompt("en", "zh-TW", "literary", "draft").replace("Tone: literary.", ""))


def test_brief_terminology_shared_between_the_registers():
    """One source, not two copies. The zh-TW vocabulary list is the output of the
    2026-07-28 invariant-4 audit, and two copies of it drifting apart is how that
    audit gets silently undone."""
    tech = _system_prompt("en", "zh-TW", "technical", "draft")
    lit = _system_prompt("en", "zh-TW", "literary", "draft")
    assert _LANG_TERMS["zh-TW"] in tech
    assert _LANG_TERMS["zh-TW"] in lit
    for row in ("軟體 not 軟件", "資料 for data but 數據 for measured readings",
                "智慧 vs 智能 (智能障礙)", "使用者 vs 用戶端",
                "Use full-width ，。！？；： and 「」 inside Chinese text."):
        assert tech.count(row) == 1
        assert lit.count(row) == 1


def test_unknown_tone_falls_back_to_the_default_register():
    """The knob is free text by design: an unrecognized value still reaches the
    model on the `Tone:` line, and the brief is the default register's."""
    odd = _system_prompt("en", "zh-TW", "brisk", "draft")
    assert "Tone: brisk." in odd
    assert odd.replace("Tone: brisk.", "Tone: technical.") == \
        _system_prompt("en", "zh-TW", "technical", "draft")


def test_unknown_tone_falls_back_for_a_language_with_no_brief_at_all():
    p = _system_prompt("en", "fr", "literary", "draft")
    assert "Tone: literary." in p
    assert p.endswith('{"s0001": "...", "s0002": "..."}')     # nothing appended


# --- the skeleton guarantee (invariant 2a) ---------------------------------
#
# `tests/corpus/` holds one input file per property, and nothing else — no
# README, no manifest. The same reasoning as `handoff/`: anything else in the
# directory would be collected as a fixture, so the explanation lives here.
#
# The properties covered, one file each: a UTF-8 BOM, CRLF terminators, CRLF
# mixed with bare LF, CR-only terminators, CRLF list items, nested lists, wrapped
# list-item continuations at four spaces and at two, indented code, HTML blocks,
# tables with alignment padding and tables without a leading pipe, hard line
# breaks, front matter, reference link definitions, setext headings and thematic
# breaks, fenced and unclosed code, nested blockquotes, inline markup, unbalanced
# inline HTML, CJK with full-width punctuation, a file with no trailing newline,
# whitespace-only, blank-lines-only, an empty file, and one 112k-character manual
# long enough for a per-block defect to hide in.
#
# Three of them are load-bearing rather than decorative.
#
# `cr-only-terminators.md` is what separates a real terminator fix from one that
# special-cases "\r\n": the latter passes every other fixture here and still
# loses this file's bytes. Note what it does *not* assert — the parser treats a
# lone CR as ordinary text, not as a line ending, so this file is one segment.
# CommonMark would call it two lines. Reinterpreting it would move a segment
# boundary, which is a decision separate from preserving the bytes, and
# `docs/decisions.md` records why it was not taken.
#
# `line-separator-control-chars.md` contains every character `str.splitlines()`
# breaks on that `str.split("\n")` does not. `parse` splits on "\n" alone and
# `splitlines` looks like a tidier way to handle terminators; this fixture is
# the standing proof that the swap is not behaviour-neutral.
#
# `html-unbalanced-inline.md` is the input the pairing stack must not crash on
# or guess about — a stray "<", an unclosed tag, an orphan close, a mismatched
# name, a void element between a real pair. `test_unbalanced_markup_renders`
# below is the half that also exercises restoration; this parametrization only
# proves the skeleton survives parsing it.
#
# Red line: a fixture is never edited to make a test pass. If one fails, either
# the parser is wrong or the fixture is not valid input — decide which, and say
# so in the commit.

CORPUS = pathlib.Path(__file__).parent / "corpus"

# Empty, and that is the state to keep it in. It held the two round-trip defects
# measured 2026-07-27, both fixed 2026-07-28 — see `docs/decisions.md`. The
# entries came out in the same commit as the fix, because strict=True turns a
# fixed-but-still-listed defect into a build failure. The machinery stays for the
# next measured defect:
# an xfail here is a scheduled repair, never a permanent exemption, and a file
# that is simply not in the corpus is the alternative that hides it instead.
KNOWN_BROKEN = {}


def identity_roundtrip(text, dnt=()):
    """Substitute each segment's source back into the skeleton.

    Deliberately not routed through ``render()``: render also unmasks and
    normalizes, so a failure there could be a masking defect rather than a
    skeleton defect, and this property is only about the skeleton.
    """
    nodes, segs = parse(text, dnt)
    by_id = {s["id"]: s for s in segs}
    return "".join(
        n["v"] if n["t"] == "raw" else by_id[n["id"]]["source"] for n in nodes
    )


def _corpus_files():
    return sorted(p for p in CORPUS.iterdir() if p.is_file())


def _case(path):
    marks = ([pytest.mark.xfail(strict=True, reason=KNOWN_BROKEN[path.name])]
             if path.name in KNOWN_BROKEN else [])
    return pytest.param(path, id=path.name, marks=marks)


def _explain(name, expected, actual):
    # repr() of both sides, windowed on the first difference: a CR or a trailing
    # space is invisible otherwise, and the long fixture is 112k characters.
    i = next((k for k, (a, b) in enumerate(zip(expected, actual)) if a != b),
             min(len(expected), len(actual)))
    lo, hi = max(0, i - 60), i + 60
    return (
        f"{name} did not round-trip; first difference at index {i}\n"
        f"  expected: {expected[lo:hi]!r}\n"
        f"  actual  : {actual[lo:hi]!r}\n"
        f"  lengths : expected {len(expected)}, actual {len(actual)}"
    )


@pytest.mark.parametrize("path", [_case(p) for p in _corpus_files()])
def test_corpus_roundtrips_byte_for_byte(path):
    # Bytes, then an explicit decode. utf-8-sig would eat the BOM fixture and
    # text mode would rewrite the CRLF fixture — in both cases silently hiding
    # the defect the fixture exists to expose.
    text = path.read_bytes().decode("utf-8")
    got = identity_roundtrip(text)
    assert got == text, _explain(path.name, text, got)


def test_unbalanced_markup_renders():
    """Restore the unbalanced fixture through the branch a translation takes.

    `identity_roundtrip` above substitutes each segment's *source* back and never
    calls `unmask`. `test_docio.py` does call it, on `render`'s fallback branch —
    untranslated segments. Neither covers the branch that matters here: a segment
    with a target, whose slot records are read after the model has moved the
    placeholders around.
    """
    text = (CORPUS / "html-unbalanced-inline.md").read_bytes().decode("utf-8")
    nodes, segs = parse(text)
    for s in segs:
        s["target"] = s["masked"]
    out, missing = render({"nodes": nodes, "segments": segs, "lang": "zh-TW"}, CFG)
    assert missing == 0
    assert out == text


@pytest.mark.parametrize("path", [pytest.param(p, id=p.name) for p in _corpus_files()])
def test_every_corpus_segment_translated_to_itself_is_structurally_clean(path):
    """The must-not-fire half of invariant 2b, on real documents rather than toys.

    A segment translated to itself changes no structure by definition, so any
    containment, escaping or `eol` issue here is the validator failing correct
    work — the direction the zh-TW lexicon had to be audited for, and the reason
    the per-rule fixtures above come in pairs. This is the sweep those pairs
    cannot be: nested lists, lazy continuations, alignment-padded tables, CRLF
    and CR-only terminators and a 112k-character manual, without anyone having
    to think of each case.

    Only the structural rules are read: translating a source to itself trips
    `untranslated` by design, and CJK rules have nothing to say about English.
    """
    text = path.read_bytes().decode("utf-8")
    _nodes, segs = parse(text)
    for seg in segs:
        seg["target"] = seg["masked"]
        issues = [f"{i['rule']}: {i['message']}"
                  for i in check_segment(seg, "zh-TW", CFG, [], [])
                  if i["rule"] in _STRUCTURAL]
        assert not issues, f"{seg['id']} ({seg['kind']}) {seg['masked']!r}: {issues}"


def test_corpus_is_present_and_known_breakage_names_real_fixtures():
    # A typo in KNOWN_BROKEN makes the real fixture run unmarked, which fails
    # loudly. A renamed or deleted fixture is the quiet case: its coverage
    # vanishes and nothing complains. This is the guard for that.
    names = {p.name for p in _corpus_files()}
    assert names, "tests/corpus/ is empty"
    missing = sorted(KNOWN_BROKEN.keys() - names)
    assert not missing, f"KNOWN_BROKEN names fixtures that do not exist: {missing}"
