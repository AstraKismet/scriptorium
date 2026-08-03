"""Markdown to render skeleton + translatable segments, and back.

``parse`` returns ``(nodes, segments)`` where nodes reproduce the file exactly
once segment values are substituted. Structure therefore cannot regress: it is
never reconstructed from a model's output, only refilled.

``render`` is re-exported from :mod:`.skeleton` and is not Markdown's: it walks
nodes and knows nothing about the syntax that produced them. It stays importable
from here because that is where every caller has always found it.
"""

import re

from .mask import has_translatable_text, mask, strip_placeholders
from .skeleton import render
from .store import seg_hash

__all__ = ["parse", "render"]

#: Only a space and a tab indent a fence, the same character class
#: `_indent_columns` counts and for the same reason. `\s` reaches U+3000, U+00A0,
#: a form feed and U+2028; U+3000 is the zh-TW paragraph indent, so `　```` `
#: was read as a fence and the whole block it opened — to end of file when it
#: closed nowhere — became skeleton. CommonMark calls that line a paragraph.
#: How far it may be indented is *not* in this pattern: the bound is three
#: columns past the container's content column, which only the loop knows.
#:
#: The info string is part of the pattern because CommonMark forbids a backtick
#: inside a *backtick* fence's — the run would otherwise be ambiguous with an
#: inline code span — and forbids nothing inside a tilde fence's. So ```` ```js` ````
#: is an ordinary paragraph, and reading it as a fence opened a run that closed
#: nowhere and took the rest of the file into the skeleton: zero segments out of a
#: three-paragraph document. Spelled in the regex rather than beside it because
#: `checks.py` imports this pattern to answer the same question about a *target*,
#: and a second copy is a second answer.
FENCE_RE = re.compile(r"^([ \t]*)(`{3,}[^`]*|~{3,}.*)$")
#: Three patterns — this one, `SETEXT_RE` and `HR_RE` — share one character
#: class, and the old `\s` spelling of it was wrong in two separate ways.
#:
#: The **leading** run is measured in columns, so it is ` {0,3}` and not
#: `[ \t]{0,3}`: CommonMark bounds a heading, a thematic break and a setext
#: underline at three columns, and at column 0 a single tab is already four.
#: `\s{0,3}` counted that tab as one character, so `\t# 標題` was read as a
#: heading; the heading branch then closed the paragraph above it and the
#: four-column line below became an indented code block — which is how
#: `　- 中文\n\t# 標題\n    文字` lost `文字` to the skeleton with nothing said.
#:
#: The runs that are **not** the indent — after a heading's hashes, and at the
#: end of an underline or a break — measure no column, and are narrowed all the
#: same, because they decide whether the line is a block start at all and every
#: block start closes the paragraph above it. CommonMark spells all three
#: "spaces or tabs"; `\s` reaches U+3000, U+00A0, a form feed, a vertical tab and
#: U+2028, so `#　標題`, `===　` and `***　` were read as a heading, an underline
#: and a break where CommonMark reads three paragraphs — and each one took the
#: indented line below it out of translation. Measured 2026-08-03 against
#: markdown-it-py: 74, 34 and 147 loss shapes across the three patterns.
#:
#: `HEADING_RE`'s third group is the one class left alone. A closing `#` run
#: decides where the *segment* is cut and never whether a block starts, so no
#: spelling of it can move a line into the skeleton — the whole heading is a
#: segment either way. Narrowing it would only move `　#` from the raw node into
#: the segment, which is a change to what the model is asked to translate and not
#: a defect.
#:
#: `\r*` before the anchor is not decoration and not part of the class. `parse`
#: splits on `"\n"` alone, so in a CRLF document *every* line still carries the
#: CR of its own terminator — that is the whole reason `emit_seg` moves a
#: trailing CR run into the skeleton. The old `\s*$` swallowed it by accident;
#: `[ \t]*$` cannot, and without this every setext underline and every thematic
#: break in a Windows-authored document silently stopped being one. Measured
#: 2026-08-03: `Title\r\n=====\r\n` became a two-line paragraph handed to the
#: model with its underline inside it. The run rather than one CR, for the reason
#: `emit_seg` takes a run: `text\r\r\n` is what a twice-applied LF-to-CRLF
#: conversion produces. A CR anywhere *else* on the line is text in this project
#: and still refuses the match, which is the conservative direction — see
#: `_carries_a_text_cr`. Only `SETEXT_RE` and `HR_RE` carry it, `HEADING_RE`
#: below deliberately not: every other pattern here ends in `.*` or `\s*`, which
#: absorb the CR already.
HEADING_RE = re.compile(r"^( {0,3}#{1,6}[ \t]+)(.*?)(\s*#*\s*)$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-{2,})[ \t]*\r*$")
#: The third pattern whose whitespace class is load-bearing, and the one whose
#: `\s` survived HANDOFF-020's first pass. Only the *leading* run is narrowed:
#: it is the one measured as columns, and `\s` reaching U+3000 made `　- item` a
#: list item that CommonMark reads as an ordinary paragraph. That mattered
#: quietly until the quote branch started depending on it — the list branch's
#: continuation loop then swallowed the quote line below it, so `quote_para` was
#: never set, and the next quoted line went into the skeleton. Found by
#: adversarial review 2026-08-03, in 56 shapes across U+3000, U+00A0 and a form
#: feed. The runs *after* the marker keep `\s`: they are not measured against a
#: column, and narrowing them would change what counts as a list marker rather
#: than where its content begins.
LIST_RE = re.compile(r"^([ \t]*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
#: Same character class as `FENCE_RE`, and it became load-bearing on the same
#: day. `\s` reaches U+3000, so `>　` was read as the marker plus its optional
#: space, leaving empty content — a blank quote line, which closes the quote's
#: paragraph and makes the indented line below it code. CommonMark reads `　` as
#: content, so the line below is a lazy continuation and is prose. `　>` was the
#: same mistake at the other end: `_columns` scores U+3000 as one column, which
#: moved the quote's content column and put an ordinary paragraph four columns
#: in. Both directions turn prose into skeleton, which is the failure this parser
#: refuses everywhere else.
QUOTE_RE = re.compile(r"^([ \t]*>[ \t]?)(.*)$")
#: Does a *quoted* line's content open a list item? Deliberately not `LIST_RE`,
#: which needs a run of whitespace after the marker and so misses `> -` — an
#: empty list item, which CommonMark opens a list for all the same. This one
#: answers a yes/no question rather than yielding a column, which is the whole
#: difference between it and `LIST_RE`: see the quote branch for why the interior
#: refuses to compute a column at all.
QUOTE_LIST_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])(?:[ \t]|$)")
#: The marker alone, without the whitespace and checkbox `LIST_RE` swallows into
#: its prefix. One column past it is a *lower* bound on CommonMark's content
#: column, where `_columns(prefix)` is an upper one — and the unclosed-fence
#: containment needs both, pointing in opposite directions. See the fence branch.
LIST_MARKER_RE = re.compile(r"^([ \t]*(?:[-*+]|\d+[.)]))")
#: The third of the trio above, and the loudest of the three: 147 loss shapes,
#: because a thematic break is spelled three ways and both of its runs were `\s`.
HR_RE = re.compile(r"^ {0,3}(?:\*{3,}|-{3,}|_{3,})[ \t]*\r*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
#: The one of the four whose leading run stays *unbounded*, and deliberately.
#: CommonMark bounds a link reference definition at three columns like the rest,
#: but here the column is already enforced one branch earlier: a line four
#: columns past its container's floor has been taken by the chunk branch before
#: this pattern is reached, and every indent that does reach it is inside a
#: container where the definition is legitimate. Writing ` {0,3}` here would
#: instead hand `-    item\n\n     [x]: /url` to the model, whose reference
#: breaks if the label comes back translated. So only the character class
#: narrows: `\s` reaching U+3000 made `　[x]: /url` a definition — CommonMark
#: reads a paragraph — and the whole line went into the skeleton untranslated,
#: taking the indented line below it as well. 36 loss shapes, same measurement.
#:
#: The run *after* the colon is the same class, and since HANDOFF-022 it is
#: **load-bearing rather than equivalent**. It used to be a measured equivalent
#: mutant: its only consumer was `not m.group(2).strip()`, which eats whatever
#: `\s*` would have eaten, so wherever the group boundary fell the answer was the
#: same. That stopped being true the moment group 2 became the input to
#: :func:`_completes_a_definition`. `\s` reaches a form feed, so `\s*` here hands
#: the destination parser `/url` for `[x]:\x0c/url` and reads a definition where
#: CommonMark reads a paragraph — a control character may not sit between the
#: colon and the destination, and consuming it as whitespace is how the line goes
#: into the skeleton with the indented line below it. The *leading* run has its
#: guard the other way round: bounding it to ` {0,3}` turns
#: `tests/corpus/block-marker-whitespace.md` red, because that fixture holds a
#: definition five columns into a list item — which is the case the paragraph
#: above is about.
#:
#: The pattern decides the *label* half and nothing else. Whether the rest of the
#: line completes a definition is :func:`opens_a_link_definition`, because it
#: needs a scanner and not a class: a destination may be angle-bracketed, may
#: carry backslash escapes, and must balance its parentheses.
DEF_RE = re.compile(r"^([ \t]*\[[^\]]+\]:[ \t]*)(.*)$")

#: What a *bare* link destination may not contain: the ASCII space and the ASCII
#: control characters. NUL is deliberately absent — markdown-it replaces U+0000
#: with U+FFFD before parsing, so `[x]:\x00/url` is a definition and refusing it
#: would take a line into the skeleton that CommonMark keeps out of it (measured
#: 2026-08-03). U+3000 and U+00A0 are absent for the opposite reason and it is
#: the same reason as everywhere else in this file: they are ordinary destination
#: characters, so `[x]:　/url` is a definition whose destination begins with an
#: ideographic space.
#:
#: A carriage return is *in* the class, and that is what makes a
#: `_carries_a_text_cr` guard unnecessary at the definition branch: `[x]: /u\rrl`
#: fails its destination and the line stays translatable, which is the direction
#: this project takes wherever a lone CR makes CommonMark and this parser
#: disagree about where a line ends.
_DEST_REFUSES = frozenset(chr(c) for c in range(0x01, 0x20)) | {"\x7f", " "}

#: CommonMark's tab stop, and its indented-code threshold. They are the same
#: number in the spec and are written twice here because they answer different
#: questions: a tab advances to the next multiple of the first, and a chunk is
#: code at or past the second. A future spec could move one without the other.
TAB_STOP = 4
CODE_INDENT = 4


def _columns(s):
    """Display columns of ``s``, expanding tabs to CommonMark's four-column stops.

    ``len(s)`` is the obvious alternative and gets every tab wrong: one tab is
    four columns, so ``\\tdef x():`` opens a code block that
    ``len(line) - len(line.lstrip())`` scores as a single column of indent.

    No origin parameter, unlike :func:`_indent_columns`. It briefly had one, for
    a list marker inside a blockquote — which starts at the quote's content
    column, so ``1.\\t`` is four columns measured from 0 and six from 2. That
    caller is gone: the quote's interior tracks an open list as a boolean rather
    than a column, because the column was six of eight measured regressions. Only
    prefixes that genuinely begin at column 0 reach this now, and a parameter no
    caller passes is a claim nothing checks.
    """
    col = 0
    for ch in s:
        col += TAB_STOP - col % TAB_STOP if ch == "\t" else 1
    return col


def _indent_columns(line, col=0):
    """Columns of ``line``'s leading space/tab run, starting at document column ``col``.

    Only those two characters indent. ``str.lstrip()`` would also eat U+3000, a
    form feed and U+00A0, none of which CommonMark counts — and U+3000 in
    particular is the zh-TW paragraph indent, so counting it would turn ordinary
    translated prose into a code block.

    ``col`` exists because a tab stop is absolute in the line and a blockquote's
    content is not at column 0. `> \\tdef x():` has a tab starting at column 2,
    which advances to 4 and is therefore *two* columns of indent; measuring the
    content string on its own scores the same tab as four and calls the line
    code. Measured against markdown-it-py, 2026-08-03, in 2276 generated shapes:
    it is a paragraph.
    """
    start = col
    for ch in line:
        if ch == "\t":
            col += TAB_STOP - col % TAB_STOP
        elif ch == " ":
            col += 1
        else:
            break
    return col - start


def _carries_a_text_cr(line):
    """Does this line hold a carriage return that is not its own terminator?

    ``parse`` splits on ``"\\n"`` alone, so a CR at the end of a line is the CRLF
    the document arrived with, and a CR anywhere else is a character in a
    sentence — `docs/decisions.md`, 2026-07-28, "a lone CR is text, not a line
    terminator". CommonMark disagrees and calls it a line ending, and that
    disagreement is exactly why such a line may not be offered to the code
    branch: a CR-only document is *one* line to ``split("\\n")``, so
    ``'    def x():\\rprose\\r'`` would put the entire file into the skeleton and
    stop translating ``prose``. Measured against markdown-it-py, 2026-08-02,
    which renders it as a code block followed by a paragraph.

    Conservative in the same direction as everywhere else here: where this parser
    cannot know a block's real boundaries, the text stays translatable.
    """
    return "\r" in line.rstrip("\r")


def _link_destination_end(rest):
    """Index just past the link destination at the start of ``rest``, or ``None``.

    Two forms and no fallback between them. CommonMark's bare destination "does
    not start with ``<``", so ``[x]: <url`` is not a definition whose destination
    is ``<url`` — it is a paragraph, and markdown-it-py renders one.
    """
    if rest[:1] == "<":
        j = 1
        while j < len(rest):
            ch = rest[j]
            # A line ending refuses the whole form. Only a CR can appear here —
            # ``parse`` splits on ``"\n"`` alone — and CommonMark calls it a line
            # ending where this project calls it text, so refusing keeps the line
            # translatable rather than guessing which. An unescaped ``<`` refuses
            # it too; every other character, control characters included, is
            # legal between the brackets.
            if ch == "\r" or ch == "<":
                return None
            if ch == ">":
                return j + 1
            # A backslash may not escape a line ending, so the escape is declined
            # there and the CR is refused on the next pass.
            j += 2 if ch == "\\" and j + 1 < len(rest) and rest[j + 1] != "\r" else 1
        return None
    j, depth = 0, 0
    while j < len(rest):
        ch = rest[j]
        if ch == "\\" and j + 1 < len(rest):
            # A backslash escapes what follows it — except a space, which ends
            # the destination *at the backslash* rather than being escaped into
            # it. Consuming the pair instead reads `[x]: a\ "t"` as one
            # destination followed by a title, so the line becomes skeleton where
            # CommonMark reads a paragraph. Measured against markdown-it-py,
            # whose `parseLinkDestination` has the same exception.
            if rest[j + 1] == " ":
                break
            j += 2
            continue
        if ch in _DEST_REFUSES:
            break
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
        j += 1
    # Unbalanced parentheses refuse the destination outright rather than ending
    # it early — `[x]: /u(rl` and `[x]: /u)rl` are both paragraphs — and an empty
    # one is no destination at all, which is what makes `[x]:` a paragraph whose
    # indented line below is a lazy continuation rather than code.
    #
    # `j == 0` is an **equivalent guard**, kept because it is the rule and not
    # because anything can see it, and labelled here so the next reader does not
    # go looking for the test that pins it. `DEF_RE`'s group 1 is greedy over
    # `[ \t]*`, so a `rest` that stops the scan at 0 always has a character at
    # position 0 that is neither a space nor a tab — and the caller's separator
    # test, `j == end`, then refuses the line anyway. The mutation pass on
    # 2026-08-03 confirmed it survives every row; the reasoning above is why that
    # is a property rather than a hole.
    return None if j == 0 or depth else j


def _link_title_end(rest, i):
    """Index just past the link title at ``rest[i]``, or ``None``."""
    opener = rest[i]
    if opener not in "\"'(":
        return None
    closer = ")" if opener == "(" else opener
    j = i + 1
    while j < len(rest):
        ch = rest[j]
        if ch == "\\" and j + 1 < len(rest):
            j += 2
            continue
        if ch == closer:
            return j + 1
        # A `(` inside a *parenthesized* title is not nesting. CommonMark admits
        # one "only if it is backslash-escaped", and refuses the title rather
        # than ending it, so `[x]: /url (a (b) c)` is a paragraph.
        #
        # `closer == ")"` is the whole of the condition and it is load-bearing:
        # without it a parenthesis inside a *quoted* title refuses that title
        # too, and `[x]: /url "a (b) c"` — an ordinary sentence in an ordinary
        # title — stops being a definition. Found by reading this function
        # against the rule rather than by the sweep, whose 22 title spellings
        # never put a parenthesis inside a quoted one.
        if ch == "(" and closer == ")":
            return None
        j += 1
    return None


def _completes_a_definition(rest):
    """Does everything after ``[label]:`` finish a link reference definition?

    A destination, an optional title separated from it by spaces or tabs, and
    nothing else on the line.
    """
    # The terminator's own CR run, the way `SETEXT_RE` and `HR_RE` end `\r*$`: in
    # a CRLF document every line carries it, and reading it as part of the
    # destination would stop every definition in a Windows-authored file from
    # being one.
    rest = rest.rstrip("\r")
    # The two references disagree about a trailing run of whitespace, in both
    # directions, because markdown-it trims the whole reference before parsing it
    # and the spec does not:
    #
    # * `[x]: /url "t"　` and `[x]: /url\x0c` are definitions to markdown-it and
    #   paragraphs to the spec, where the run is a character after the title, or
    #   a character a destination may not hold;
    # * `[x]:　` is a paragraph to markdown-it and a definition to the spec,
    #   whose destination is one ideographic space.
    #
    # This parser refuses both, which lands on the spec's answer for the first
    # and markdown-it's for the second — not a compromise but the one rule this
    # file has, that the text stays translatable where the references do not
    # agree what the line is. The strict ` \t` runs below cover the first; a tail
    # with nothing but whitespace in it covers the second, and covers it *whole*
    # rather than per-destination, because `[x]:　 "t"` is a definition with an
    # ideographic-space destination to both of them.
    #
    # `[x]: /url　` is *not* one of these: U+3000 is a legal destination
    # character, so the trailing run is simply more destination and every
    # reference agrees it is a definition, this one included.
    if not rest.strip():
        return False
    end = _link_destination_end(rest)
    if end is None:
        return False
    j = end
    while j < len(rest) and rest[j] in " \t":
        j += 1
    if j == len(rest):
        return True
    # A title must be *separated* from the destination. Without whitespace there
    # is nothing to separate, and `[x]: <url>"t"` is a paragraph — where
    # `[x]: /url"t"` is a definition, because there the quotes are simply more
    # destination.
    if j == end:
        return False
    end = _link_title_end(rest, j)
    if end is None:
        return False
    while end < len(rest) and rest[end] in " \t":
        end += 1
    return end == len(rest)


def opens_a_link_definition(line):
    """Is this whole line a CommonMark link reference definition?

    `DEF_RE` alone decides on the strength of `[label]:`, which is the label half
    of the rule and not the rule. CommonMark decides on the whole line, and when
    the rest of it does not parse the line is an ordinary **paragraph** — so
    `[x]: /url not a title` and `[Ana]: Hello there` are prose, where reading
    them as definitions put the line into the skeleton untranslated *and*, by
    closing the paragraph, turned the indented line below it into code. Two
    blocks lost for one wrong answer.

    Imported by :mod:`.checks`, which asks the same question about a model's
    *target*: a target of `[foo]: http://example.com` does not land in the wrong
    block, it renders to nothing at all. One rule for both sides, the way the
    patterns above are shared, and the narrowing sharpens that check rather than
    weakening it — only a well-formed definition disappears from the render, and
    a target that merely looks like one now stops being reported.

    Its one blind spot is the two-line definition, which `mdparse` reads one line
    at a time and deliberately keeps reading that way: see the branch in
    :func:`parse`.
    """
    m = DEF_RE.match(line)
    return bool(m) and _completes_a_definition(m.group(2))


def _quote_state(line, quote_para, quote_list):
    """The quote's interior state after ``line``, wherever it was consumed.

    **Four** branches read a line that may carry a ``>`` marker, and only one of
    them is the quote branch: the list branch's continuation loop stops at a
    list, heading or fence but not at a blockquote; the table loop takes every
    consecutive line containing a ``|``; and the fence branch takes everything up
    to its closing marker. A quoted line any of them swallows never reaches the
    quote branch, so the interior state keeps whatever it had — and the opening
    values, ``False`` and ``False``, are the ones that turn the *next* quoted
    line into skeleton.

    Every one of those three was a measured regression, all found by adversarial
    review on 2026-08-03 after a 153023-document sweep reported none: the list
    loop lost `chunk` from `-\\titem\\n   > intro\\n>     chunk` in 40 shapes,
    the table loop lost `prose` from `| a |\\n|---|\\n> q | p\\n>     prose`, and
    the fence loop lost it from
    `- [ ] item\\n      ```\\n> intro\\n      ```\\n>     prose`. A function
    rather than four copies, because the next branch to grow a swallowing loop
    will forget — three of four already did.

    Returns the state for a line read as ordinary quoted content. The quote
    branch overrides the paragraph half when it decides the line is code, since a
    code block opens no paragraph.
    """
    m = QUOTE_RE.match(line)
    if not m:
        return quote_para, quote_list
    content = m.group(2)
    return (content.strip(" \t\r") != "",
            quote_list or bool(QUOTE_LIST_RE.match(content)))


def _interrupts_a_paragraph(line):
    """Does this line start a block CommonMark lets interrupt a paragraph?"""
    return bool(QUOTE_RE.match(line) or FENCE_RE.match(line)
                or HEADING_RE.match(line) or HR_RE.match(line))


def parse(text, dnt=(), opts=None):
    """Split markdown into a render skeleton + translatable segments.

    ``opts`` is the format's config block, part of the registry's signature so
    that every parser is called the same way. Markdown has no knobs — its
    segmentation is decided by the syntax, not by a project — so it is ignored
    here rather than absent, because a parser whose signature differs is a
    parser the registry has to special-case.
    """
    lines = text.split("\n")
    trailing_nl = text.endswith("\n")
    if trailing_nl and lines and lines[-1] == "":
        lines.pop()          # the split artifact, not a real blank line
    nodes, segs = [], []
    i, n = 0, len(lines)
    counter = [0]

    def emit_raw(s):
        if nodes and nodes[-1]["t"] == "raw":
            nodes[-1]["v"] += s
        else:
            nodes.append({"t": "raw", "v": s})

    def emit_seg(source, kind):
        # A file written on Windows leaves the CR of its terminator at the end of
        # the block, because parse() splits on "\n" alone. That CR is part of the
        # line ending, not part of the sentence: the model must never see it, and
        # it still has to reach the rendered document. So it moves into the
        # skeleton here. The old code called `source.rstrip("\r")` and kept
        # nothing, so every block of every CRLF file lost a byte.
        #
        # The whole trailing run moves, not just one CR. Taking exactly one is
        # defensible — a second CR is arguably literal text — but `text\r\r\n`
        # is what a twice-applied LF-to-CRLF conversion produces, and leaving the
        # extra CR in the source would hand the model a control character to
        # reproduce for no gain. Moving the run also keeps every segment source
        # byte-identical to what the old code produced, so the repair invalidates
        # no translation memory at all.
        stripped = source.rstrip("\r")
        cr = source[len(stripped):]
        source = stripped
        # The predicate moved to `mask.has_translatable_text` when plain text
        # arrived, character range unchanged, so the two formats cannot drift.
        if not has_translatable_text(source):
            emit_raw(source + cr)
            return
        counter[0] += 1
        sid = f"s{counter[0]:04d}"
        masked, slots = mask(source, dnt)
        if not strip_placeholders(masked).strip():
            emit_raw(source + cr)  # nothing left to translate
            counter[0] -= 1
            return
        segs.append({
            # `context` is this format's answer to gettext's msgctxt, and for
            # Markdown the answer is the block kind: it is what stops a paragraph
            # translation from being reused inside a blockquote, where a line
            # break the paragraph was free to have lands outside the block. It
            # duplicates `kind` today and is stored separately anyway — a format
            # whose context is a key path or a spine position has no `kind` to
            # borrow, and the memory key must not have to know which format it is
            # looking at.
            #
            # `variant` is the i18n hedge: null until something emits plural or
            # gender forms, present now because adding it later means a second
            # migration and a second migration invalidates the whole memory.
            "id": sid, "kind": kind, "hash": seg_hash(source),
            "context": kind, "variant": None,
            "source": source, "masked": masked, "slots": slots,
            "target": None, "status": "pending", "origin": None,
        })
        nodes.append({"t": "seg", "id": sid})
        if cr:
            emit_raw(cr)  # lands ahead of the caller's "\n", restoring the CRLF

    # front matter
    if n and lines[0].strip() == "---":
        j = 1
        while j < n and lines[j].strip() != "---":
            j += 1
        if j < n:
            emit_raw("\n".join(lines[: j + 1]) + "\n")
            i = j + 1

    #: The content column of the innermost open list item, or ``None`` at the
    #: left margin. It exists so the indented-code threshold can move with the
    #: item: `- item\n\n    still prose` is a *second paragraph of that item*,
    #: because four columns is only two past a content column of two. A flat
    #: four-column test would make it skeleton and stop translating it, which is
    #: the failure this whole branch has to avoid being worse than the defect.
    list_col = None

    #: One column past that item's marker, ignoring the whitespace and checkbox
    #: `list_col` includes. It is a *lower* bound on CommonMark's content column
    #: where `list_col` is an upper one, and the unclosed-fence containment needs
    #: both — see the fence branch, which had three wrong single-number spellings
    #: before this.
    list_min_col = 0

    #: True while the line just consumed left a paragraph open. This is the
    #: state CommonMark's "an indented code block cannot interrupt a paragraph"
    #: is written against, and it has to be state rather than a claim about
    #: position: the paragraph branch stops at anything that looks like a block
    #: start *at any indent*, so `text\n    - like a list` and `> quoted\n
    #: lazy line` both hand their second line to the top of this loop while
    #: CommonMark is still inside one paragraph. Measured against markdown-it-py
    #: over 37224 generated shapes, 2026-08-02: without this, 2778 markers of
    #: prose became skeleton and stopped being translated at all.
    para_open = False

    #: The interior of the innermost blockquote, where `para_open` and `list_col`
    #: cannot answer for it. `mdparse` emits one segment per quoted line and never
    #: descends into the quote, so a quoted chunk's indent is measured after the
    #: `>` marker — the quote's content column. `quote_para` is the
    #: lazy-continuation half: `> intro\n>     def x():` is one paragraph,
    #: `> intro\n>\n>     def x():` is a code block, and the bare `>` between them
    #: is the whole difference. `quote_list` is a *boolean* and not a column, for
    #: the reason written at the branch: the column was six of eight measured
    #: regressions, and a parser with no container stack does not get to compute
    #: one. Both are maintained by `_quote_state`, because three other branches
    #: also consume quoted lines.
    quote_para = False
    quote_list = False

    while i < n:
        line = lines[i]
        ind = _indent_columns(line)
        # Read for this line, then cleared. The three branches that leave a
        # paragraph open — quote, list, paragraph — set it again on the way out,
        # so every other block start closes one by saying nothing.
        lazy, para_open = para_open, False

        # A block starting at the left margin is one no open list item can
        # contain, and closes it. Three things it is not:
        #
        # * a blank line — a list item is free to hold several paragraphs;
        # * another list — the branch below sets the new column on its way out;
        # * a **lazy continuation**, which is at the margin and is still inside
        #   the item. `- item wraps and\ncontinues here\n\n    second para` is
        #   one item with two paragraphs, and without `not lazy` the second one
        #   is measured against four columns instead of six and becomes
        #   skeleton. Found by adversarial review 2026-08-02, after a 37224-shape
        #   sweep that varied the block *above* the chunk and never wrapped one.
        if (line.strip() and ind == 0 and not LIST_RE.match(line)
                and (not lazy or _interrupts_a_paragraph(line))):
            # Clearing `list_min_col` too is tidiness rather than a guard, and
            # measured so over 158855 documents: every reader of it is inside
            # `list_col is not None`, so its value is unread the moment that is
            # None. Left in because a number that outlives its partner is how the
            # next reader gets a wrong answer from a right-looking expression.
            list_col, list_min_col = None, 0

        # The column at which an indented chunk becomes code, read here rather
        # than at the branch that consumes one because the fence branch needs the
        # same number: CommonMark bounds a fence's own indentation at three
        # columns *past its container's content column*, which is `code_floor`
        # minus one exactly.
        code_floor = CODE_INDENT if list_col is None else list_col + CODE_INDENT

        # A marker below the item's content column is not inside the item, so the
        # bound on its own indentation is the document's and not the item's.
        # Reading it as the item's let `   1. item\n\n          ```\n    ``` ` call
        # a four-column marker a fence when CommonMark calls it an indented code
        # block, and the fence then ran to end of file: 33 shapes.
        fence_floor = code_floor if ind >= list_min_col else CODE_INDENT

        m = FENCE_RE.match(line)
        if m and ind < fence_floor:
            # What closes this fence, and it is *this* opener's run rather than a
            # canonical three. CommonMark: a closer is the same character, at
            # least as long as the opener, and carries no info string. All three
            # halves were missing and each cost prose.
            #
            # `m.group(2)[0] * 3` with a prefix match let ```` ```` ```` be closed
            # by the ` ``` ` that is its own content — the four-backtick fence
            # wrapping a three-backtick example, which is *the* idiom for
            # documenting Markdown. The real closer was then read as a fresh
            # opener with nothing to close it and swallowed the rest of the file:
            # 2770 of 75810 generated documents, and it predates this package.
            #
            # Accepting a closer that carries an info string is what turned that
            # from a latent defect into a regression on 2026-08-03. Once
            # ```` ```js` ```` stopped being an *opener*, the fence above it still
            # closed on that line, every later marker re-paired one step over, and
            # the last one ran to end of file. Found by adversarial review after a
            # 40284-document sweep reported zero, on the axis it never varied: the
            # *sequence* of markers in one document. 796 of 75810.
            #
            # The indent keeps its unbounded `\s*`, which is a different question
            # and points the other way — bounding a closer's indent runs every
            # fence further, and it cannot be done correctly here anyway, since
            # that indent is measured after the container's prefix and this parser
            # never strips one. The trailing `[ \t]*\r*$` is the same rule
            # `SETEXT_RE` and `HR_RE` needed hours earlier: in a CRLF document
            # every line carries the CR of its own terminator, and without it
            # *every* fence in such a document would run to end of file.
            # `` `+|~+ ``, never ``[`~]+``: a marker is a run of ONE character,
            # and the class spelling reads ```` ```~~~ ```` as a six-character
            # marker whose closer nothing can match — the fence then runs to end
            # of file, which is the failure this whole repair is about. Caught by
            # re-running the adversarial harness that found the defect rather
            # than by the suite, in 1024 of 342528 generated documents.
            run = re.match(r"`+|~+", m.group(2)).group(0)
            closer = re.compile(
                rf"^\s*{re.escape(run[0])}{{{len(run)},}}[ \t]*\r*$")
            # How far this fence may reach, asked before where it closes. It ends
            # with the container it opened in: a list item ends at the first
            # non-blank line below its content column, and a closing marker under
            # that line belongs to a different block. Three measurements built
            # this bound, all on 2026-08-03.
            #
            # It is not the fence's *own* indent, which reaches too far: a ` ``` `
            # one column in at the margin legitimately holds content at column 0,
            # and bounding by the opener handed 1158 markers of it to the model.
            #
            # It takes *two* bounds on the item's content column, pointing
            # opposite ways, because `list_col` is deliberately the item's whole
            # prefix and CommonMark's content column can be anywhere between one
            # past the marker and there.
            #
            # Whether the fence is inside the item is asked against the **lower**
            # bound, `list_min_col`: judging "outside" wrongly selects the
            # margin's floor of 0 and swallows the document, which is how
            # `- [ ] item\n\n          ```\n  ```\nprose` lost its paragraph in
            # 400 shapes.
            #
            # Where to stop is then the **upper** bound, `list_col`: a floor
            # below the real content column runs the fence past the item's end,
            # and a floor above it only stops sooner, which costs nothing but a
            # visible translated line. `min(list_col, ind)` was the third
            # spelling and used one number for both questions — it cut the search
            # off before a fence's own closing marker whenever the fence sat
            # *below* the item, losing `- Item\n\n ```\n```\n\ntext` in 52 shapes.
            #
            # And it bounds the *search*, not only the unclosed fallback, which
            # was the first spelling. Once the new indent gate moved which line is
            # the opener, the leftover markers re-paired across the container's
            # end and everything between them became skeleton — adversarial
            # review broke it three ways, and an item holding a six-column run
            # above a four-column one lost the paragraph below both.
            #
            # With no list open the bound is vacuous, the search is the unbounded
            # one it has always been, and an unclosed fence reaches end of file —
            # which is CommonMark's answer and what
            # `tests/corpus/fences-and-unclosed.md` pins.
            floor = (list_col if list_col is not None and ind >= list_min_col
                     else 0)
            limit = i
            while limit + 1 < n and (not lines[limit + 1].strip()
                                     or _indent_columns(lines[limit + 1]) >= floor):
                limit += 1
            j = i + 1
            while j <= limit and not closer.match(lines[j]):
                j += 1
            if j > limit:
                # The line that ends the container may be this fence's own
                # closing marker: a closer is allowed to be less indented than
                # the content it closes, and `limit` has already absorbed every
                # blank line, so the first line below the floor is either that
                # closer or a different block. Without this the marker is read as
                # a fresh *opener* on the next pass, and at the margin an opener
                # with nothing to close it reaches end of file — which is how
                # bounding the search turned `-\titem\n\n  ```\n```\n\ntext` into
                # a lost paragraph in 77 shapes: the same failure one step later.
                j = (limit + 1 if ind < floor and limit + 1 < n
                     and closer.match(lines[limit + 1])
                     else limit)
            emit_raw("\n".join(lines[i : j + 1]) + "\n")
            i = j + 1
            continue

        if not line.strip():
            emit_raw(line + "\n")
            i += 1
            # CommonMark's blank line holds nothing but spaces and tabs.
            # `str.strip()` answers True for U+3000, U+00A0, U+2028, U+2029, a
            # form feed, a vertical tab and four more, so a line that looks
            # empty to Python may not close the paragraph above it — and U+3000
            # is the zh-TW paragraph indent, U+00A0 what a paste from EPUB or a
            # word processor leaves, so such a line is ordinary material here.
            # The block stays raw either way; only the paragraph state differs.
            #
            # It *opens* one rather than merely keeping one open, because such a
            # line is content: `# h\n　\n    chunk` puts the chunk inside the
            # U+3000 line's own paragraph even though the heading closed
            # everything before it. `lazy and ...` was the first spelling and the
            # widened sweep refuted it in 1482 documents.
            para_open = line.strip(" \t\r") != ""
            # A real blank line closes the blockquote itself, so both interior
            # answers go back to their opening values. A line that is blank only
            # to `str.strip()` does not: it is content, so it is a lazy
            # continuation of whatever the quote had open, and `> a\n　\n>     x`
            # is still one paragraph. Nothing else resets these — a heading or a
            # table does close a blockquote, and leaving the state alone across
            # one only ever keeps text translatable.
            if not para_open:
                quote_para, quote_list = False, False
            continue

        if HR_RE.match(line) or SETEXT_RE.match(line):
            emit_raw(line + "\n")
            i += 1
            # A `=====` or `--` with no paragraph above it underlines nothing,
            # so CommonMark reads it as ordinary paragraph text and the indented
            # line below it as that paragraph's lazy continuation. A thematic
            # break is a break either way, and a real underline has just turned
            # the paragraph above into a heading — both close.
            para_open = not lazy and not HR_RE.match(line)
            continue

        # An indented code block, held in the skeleton the way a fenced one
        # already is. Two spellings of one construct were being treated
        # oppositely: the model was asked to translate Python, and the four
        # spaces that make it code sat at position 0 of the segment, where
        # `translate.accept` stripped them off every proposal it took. Only the
        # first half of that is this branch's to fix — a list item's second
        # paragraph reaches position 0 the same way and is not code, so the
        # stripping was repaired separately in `normalize.reseat_outer_blanks`.
        #
        # CommonMark's other half of the rule — an indented chunk cannot
        # interrupt a paragraph — is `lazy`. Reading it off the paragraph
        # branch's reach instead was the first attempt and it was wrong in the
        # one direction that costs a translation; see `para_open` above.
        if not lazy and ind >= code_floor and not _carries_a_text_cr(line):
            # `i + 1`, not `i`. Line `i` has already passed exactly the tests the
            # loop below applies, so re-testing it is a no-op — but only while
            # the two conditions agree. Start at `i` and the day they stop
            # agreeing the loop exits with `j == i`, `i = j` advances nothing,
            # and `parse` spins forever on a real document. Found 2026-08-02 by
            # the mutation harness itself hanging for 56 minutes on a mutant
            # that removed the carriage-return guard from one side only.
            j = i + 1
            # `lines[j].strip()` is redundant and kept for the reader: a blank
            # line either indents past the floor, in which case it joins this raw
            # node instead of the next one and `emit_raw` concatenates the two
            # anyway, or it does not and the column test stops the chunk on its
            # own. The 2026-08-02 mutation sweep is where that was established —
            # removing it left the suite green *and* the CommonMark differential
            # unchanged, which is what separates a redundant guard from an
            # untested one.
            while j < n and lines[j].strip() \
                    and _indent_columns(lines[j]) >= code_floor \
                    and not _carries_a_text_cr(lines[j]):
                j += 1
            # Trailing blank lines are left to the blank branch, and a chunk
            # after them is recognized here again. CommonMark calls that one code
            # block and this calls it two; both are raw, so the skeleton bytes do
            # not know the difference and nothing downstream asks.
            emit_raw("\n".join(lines[i:j]) + "\n")
            i = j
            continue

        m = HEADING_RE.match(line)
        if m:
            emit_raw(m.group(1))
            emit_seg(m.group(2), "heading")
            emit_raw(m.group(3) + "\n")
            i += 1
            continue

        # `not lazy` is CommonMark's other half of the rule and it is the half
        # the tail parser exposed: **a link reference definition cannot interrupt
        # a paragraph**, so `> quoted\n[x]: /url` is that paragraph's lazy
        # continuation and renders as the literal text `[x]: /url`. Reading it as
        # a definition put a whole line of prose into the skeleton, and the sweep
        # for this package found it once the tail was decided: **7228 lines** of
        # its remaining loss column, present at the parent too. It is
        # `_interrupts_a_paragraph` saying nothing about definitions, spelled at
        # the branch that needed to hear it.
        if not lazy and opens_a_link_definition(line):
            emit_raw(line + "\n")
            i += 1
            # A definition closes the paragraph above it, so the four-column line
            # below it is an indented code block — which is what markdown-it
            # renders and what the chunk branch produces once `lazy` is False.
            # The old spelling was `para_open = not m.group(2).strip()`, the
            # empty-destination half of the rule; an empty destination is no
            # longer a definition at all, so it never reaches here and the
            # question has one answer.
            #
            # **A definition that spans two lines is not read as one.** Both the
            # destination and the title may sit on the line below, and this
            # branch refuses to look — which hands the second line to the model
            # as prose. The alternative reads it, and that is what makes it lose:
            # `Hello` is a valid bare destination, so `[Ana]:\nHello` would take
            # *both* lines into the skeleton and stop translating a line of
            # dialogue, silently. Refusing errs the other way, visibly, and
            # visible is the direction this parser takes everywhere. Measured
            # 2026-08-03: markdown-it reads `[x]: /url\n"a title"` as one
            # definition, and this reads a definition plus a paragraph.
            para_open = False
            continue

        # table
        if "|" in line and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
            while i < n and "|" in lines[i]:
                # Any consecutive line holding a `|` lands here, a blockquote
                # line included, and it never reaches the quote branch — the same
                # hole the list branch's continuation loop has. See
                # `_quote_state`; `| a |\n|---|\n> q | p\n>     prose` lost
                # `prose` without this.
                quote_para, quote_list = _quote_state(
                    lines[i], quote_para, quote_list)
                if TABLE_SEP_RE.match(lines[i]):
                    emit_raw(lines[i] + "\n")
                else:
                    parts = re.split(r"(\|)", lines[i])
                    for p in parts:
                        if p == "|":
                            emit_raw(p)
                        elif p.strip():
                            lead = p[: len(p) - len(p.lstrip())]
                            trail = p[len(p.rstrip()) :]
                            emit_raw(lead)
                            emit_seg(p.strip(), "cell")
                            emit_raw(trail)
                        else:
                            emit_raw(p)
                    emit_raw("\n")
                i += 1
            continue

        m = QUOTE_RE.match(line)
        if m:
            prefix, content = m.group(1), m.group(2)
            # CommonMark's blank line one container down: only a space and a tab
            # make one, so `> 　` is content and keeps the quote's paragraph open.
            filled = content.strip(" \t\r") != ""
            cind = _indent_columns(content, _columns(prefix))
            # A quoted block at the marker's own column closes a list open inside
            # the quote — the document level's margin rule, one container down,
            # with `quote_para` standing in for `lazy` for the same reason it does
            # there. Without it, one `> - item` anywhere keeps every later quoted
            # chunk in the document translatable.
            # `QUOTE_LIST_RE` and `LIST_RE` are interchangeable *here* and are
            # not in `_quote_state`: a line this rule declines to close on is one
            # the helper immediately re-opens on, so the two patterns converge on
            # the same state. Measured equivalent over 158855 documents; the
            # helper's copy is the one that matters and its mutant dies.
            if filled and cind == 0 and not quote_para \
                    and not QUOTE_LIST_RE.match(content):
                quote_list = False
            # Four conditions, and three of them are the parser admitting what it
            # cannot know. This branch is re-implementing block parsing one
            # container down without a container stack, and the first spelling
            # tried to do it properly — a real content column for a list inside
            # the quote, `quote_list_col`, mirroring the document level. Six of
            # the eight regressions adversarial review found on 2026-08-03 lived
            # in that column's arithmetic: a tab in the marker measured from the
            # wrong origin, a tab after the marker measuring the prefix and the
            # content from different origins, a bare `> -` that `LIST_RE` does not
            # match at all, and a quoted list marker on a line some other branch
            # had eaten. Every one turned prose into skeleton. So the column is
            # gone and `quote_list` is a *boolean*: while any list is open inside
            # the quote, nothing in it is code. That gives up
            # `> - item\n>\n>       def deep():` — a missed repair, which costs a
            # visible translated code block — to remove a family of ways to lose
            # text silently, and the governing rule of this whole area is that the
            # permissive direction is worse than the defect.
            #
            # `ind == 0` is the same admission about the quote's own marker: an
            # indented `>` under an open paragraph is not a blockquote at all but
            # a lazy continuation, and `prose\n    >     def x():` lost its
            # sentence to that. A `>` at the margin is unambiguous; an indented
            # one is not, and this parser does not get to guess.
            #
            # `filled` is redundant and kept for the reader, the way the chunk
            # loop's `lines[j].strip()` is: a blank quote line takes the other
            # branch to the same bytes, because `emit_seg` refuses a source with
            # nothing translatable in it and emits exactly what this one would.
            # Measured 2026-08-03 over 158543 documents — every spelling of a
            # blank quote line in eight contexts, plus the whole differential
            # sweep — comparing segment sources, kinds and skeleton bytes: zero
            # differences. That is what separates a redundant guard from an
            # untested one, and the mutation harness lists it as equivalent so the
            # next reader does not re-derive it.
            quoted_code = (filled and not quote_para and not quote_list
                           and ind == 0 and cind >= CODE_INDENT
                           and not _carries_a_text_cr(line))
            if quoted_code:
                # Code inside the quote: the marker goes into the skeleton with
                # it, because the quote branch's usual split — marker raw,
                # content segment — is what handed the model Python to translate.
                # One line at a time, since that is how this branch reads a quote
                # at all; consecutive lines land in one raw node regardless.
                emit_raw(line + "\n")
            else:
                emit_raw(prefix)
                emit_seg(content, "quote")
                emit_raw("\n")
            # Read through the same helper the three swallowing branches use, so
            # the state cannot mean one thing here and another there.
            quote_para, quote_list = _quote_state(line, quote_para, quote_list)
            # …then one override this branch alone can make: a code block opens
            # no paragraph, so the *next* quoted line is measured against the
            # floor too rather than read as a lazy continuation of it. Without it
            # the first line of a quoted chunk moves into the skeleton and every
            # line under it stays a segment — half a repair, which is worse than
            # none because the block renders as code with its body translated.
            if quoted_code:
                quote_para = False
            i += 1
            # Every quote line, including a bare `>`. Reading the bare one as
            # closing the paragraph — which is what CommonMark does — was tried
            # and reverted: this parser has no container stack, so it cannot
            # measure an indent against a blockquote's content column, and
            # `> q\n>\n    > x` is still inside the quote where `> q\n>\n    y`
            # is not. Measured 2026-08-02: the refinement fixed 285 shapes and
            # turned prose into skeleton in 57. Conservative here means the text
            # stays translatable, which is the direction that costs nothing.
            para_open = True
            continue

        m = LIST_RE.match(line)
        if m:
            prefix, rest = m.group(1), m.group(2)
            body = [rest]
            j = i + 1
            indent = len(prefix)
            while j < n and lines[j].strip() and not LIST_RE.match(lines[j]) \
                    and not HEADING_RE.match(lines[j]) and not FENCE_RE.match(lines[j]) \
                    and len(lines[j]) - len(lines[j].lstrip()) >= indent:
                # Kept verbatim, indent included. This looked like a case for
                # .strip() — the marker prefix is already in a raw node, so the
                # indent reads as skeleton — but the skeleton cannot hold it: a
                # continuation's indent sits *after* a newline that is inside the
                # segment source, and a raw node can only go before or after a
                # whole segment. Splitting the item into one segment per line
                # would make it representable, and would also cut a wrapped
                # sentence into fragments the model must translate blind. So the
                # indent stays in the source, where it round-trips.
                #
                # This loop does not stop at a blockquote marker — the paragraph
                # branch's copy does — so a quoted line inside an item is read
                # *here* and never reaches the quote branch. Recorded where the
                # line is actually consumed rather than by teaching this loop to
                # stop: stopping would re-cut every list item that contains a
                # blockquote, which is a segmentation change and a different
                # package's decision. See `_quote_state`.
                quote_para, quote_list = _quote_state(
                    lines[j], quote_para, quote_list)
                body.append(lines[j])
                j += 1
            emit_raw(prefix)
            emit_seg("\n".join(body), "list")
            emit_raw("\n")
            i = j
            # Deliberately the whole prefix, checkbox included, rather than
            # CommonMark's content column. It is never smaller, so the threshold
            # it produces is never too low, and too low is the only direction
            # that costs a translation.
            list_col = _columns(prefix)
            # …and the other side of the same number, for the one question where
            # too high is the costly direction. `_columns(prefix)` is an upper
            # bound on the item's content column; one past the marker is a lower
            # one, and the fence containment reads them in opposite directions.
            list_min_col = _columns(LIST_MARKER_RE.match(prefix).group(1)) + 1
            para_open = True
            continue

        # paragraph
        body = [line]
        j = i + 1
        while j < n and lines[j].strip() and not LIST_RE.match(lines[j]) \
                and not HEADING_RE.match(lines[j]) and not FENCE_RE.match(lines[j]) \
                and not QUOTE_RE.match(lines[j]) and not HR_RE.match(lines[j]) \
                and not SETEXT_RE.match(lines[j]):
            body.append(lines[j])
            j += 1
        emit_seg("\n".join(body), "para")
        emit_raw("\n")
        i = j
        para_open = True

    # every block emitter appends its own newline; drop the last one when the
    # source did not actually end with a line break
    if not trailing_nl and nodes and nodes[-1]["t"] == "raw" and nodes[-1]["v"].endswith("\n"):
        nodes[-1]["v"] = nodes[-1]["v"][:-1]
        if not nodes[-1]["v"]:
            nodes.pop()

    return nodes, segs
