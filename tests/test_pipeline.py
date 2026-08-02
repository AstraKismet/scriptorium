"""Round-trip and validator tests. No network, no model."""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.checks import check_segment  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.mask import mask, repair_placeholders, unmask  # noqa: E402
from scriptorium.mdparse import parse, render  # noqa: E402
from scriptorium.normalize import normalize  # noqa: E402
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
# where `translate.accept` strips the leading whitespace off every proposal it
# takes — and a translation-memory hit comes through `accept` too, so a target a
# person applied with its indent intact was banked with the indent and handed
# back without it on the next extract. `test_memory.py` owns that cycle.
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
])
def test_the_indented_code_rule_moved_no_other_fixture_s_segment_count(name, count):
    text = (CORPUS / name).read_bytes().decode("utf-8")
    got = [s["source"] for s in parse(text)[1]]
    assert len(got) == count, got[:5]


def test_masking_is_reversible():
    text = "Run `go build` then see https://x.dev/a?b=1 for {{var}}."
    masked, slots = mask(text, [])
    assert "go build" not in masked
    assert unmask(masked, slots) == text


def test_dnt_respects_word_boundaries():
    masked, slots = mask("Go to Google with Go.", ["Go"])
    assert "Google" in unmask(masked, slots)
    assert masked.count("\u27e6") == 2


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


def test_a_source_link_definition_never_reaches_a_segment_in_the_first_place():
    # The must-not-fire half, and the reason the row costs nothing: `mdparse`
    # folds a link definition into a raw node, so no source segment line can be
    # one. Only a model-invented definition can match the rule.
    _nodes, segs = parse('[foo]: http://example.com "title"\n\nOrdinary prose.\n')
    assert [s["source"] for s in segs] == ["Ordinary prose."]


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
    # segment ending this way — the terminator is a raw node — but `cli.do_apply`
    # does not strip what it is given, so a reviewer's or an agent's target does
    # reach here whole. These are the rows that fail when the trailing strip
    # anchors on `$`, which in Python also matches before a final newline; the
    # CRLF pair above cannot catch it, because `$` does not match before a CR.
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
    # Position 0. `translate.accept` strips a model's leading whitespace before
    # normalize sees it; `cli.do_apply` \u2014 a person's or an agent's own words \u2014
    # does not. An indented code block stopped arriving here on 2026-08-02, when
    # it became skeleton, but position 0 did not: a list item's second paragraph
    # is `- item\n\n    text` and `mdparse` puts those four spaces at the front
    # of the segment, where losing them takes the paragraph out of the item.
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
