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
FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*?)(\s*#*\s*)$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-{2,})\s*$")
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
HR_RE = re.compile(r"^\s{0,3}(?:\*{3,}|-{3,}|_{3,})\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
DEF_RE = re.compile(r"^(\s*\[[^\]]+\]:\s*)(.*)$")

#: CommonMark's tab stop, and its indented-code threshold. They are the same
#: number in the spec and are written twice here because they answer different
#: questions: a tab advances to the next multiple of the first, and a chunk is
#: code at or past the second. A future spec could move one without the other.
TAB_STOP = 4
CODE_INDENT = 4


def _columns(s, col=0):
    """Display columns of ``s``, expanding tabs to CommonMark's four-column stops.

    ``len(s)`` is the obvious alternative and gets every tab wrong: one tab is
    four columns, so ``\\tdef x():`` opens a code block that
    ``len(line) - len(line.lstrip())`` scores as a single column of indent.

    ``col`` is the document column ``s`` starts at, and it matters for the same
    reason it does in :func:`_indent_columns`: a tab stop is absolute in the
    line. A list marker inside a blockquote starts at the quote's content column,
    so ``1.\\t`` measured from 0 is four columns and measured from 2 is six.
    """
    start = col
    for ch in s:
        col += TAB_STOP - col % TAB_STOP if ch == "\t" else 1
    return col - start


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

    #: The same two questions asked *inside* the innermost blockquote, where
    #: `para_open` and `list_col` cannot answer them. `mdparse` emits one segment
    #: per quoted line and never descends into the quote, so a quoted chunk's
    #: indent has to be measured after the `>` marker — the quote's content
    #: column — and against a list item opened inside the quote rather than one
    #: opened outside it. `quote_para` is the lazy-continuation half:
    #: `> intro\n>     def x():` is one paragraph, `> intro\n>\n>     def x():` is
    #: a code block, and the bare `>` between them is the whole difference.
    quote_para = False
    quote_list_col = None

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
        if line.strip() and ind == 0 and not lazy and not LIST_RE.match(line):
            list_col = None

        # The column at which an indented chunk becomes code, read here rather
        # than at the branch that consumes one because the fence branch needs the
        # same number: CommonMark bounds a fence's own indentation at three
        # columns *past its container's content column*, which is `code_floor`
        # minus one exactly.
        code_floor = CODE_INDENT if list_col is None else list_col + CODE_INDENT

        m = FENCE_RE.match(line)
        if m and ind < code_floor:
            fence = m.group(2)[0] * 3
            # The closing search keeps its unbounded `\s*`, and the two are not
            # the same question. Bounding the *opening* indent leaves more text
            # translatable, which is this parser's direction wherever it cannot
            # know; bounding the closing one would run every fence further and
            # turn more of a document into skeleton — and it cannot be bounded
            # correctly here anyway, since a closing fence's indent is measured
            # after its container's prefix, which this parser never strips.
            j = i + 1
            while j < n and not re.match(rf"^\s*{re.escape(fence)}", lines[j]):
                j += 1
            if j >= n:
                # Nothing closed it, so its extent is a guess rather than a fact,
                # and the guess is bounded by the container the fence opened in:
                # CommonMark ends an unclosed fence where its container ends, and
                # a list item ends at the first non-blank line below its content
                # column. Measured 2026-08-03: without this,
                # `- item\n\n      ```\n    ```\n\ntext` swallowed `text` to end
                # of file, in 84 generated shapes.
                #
                # Two things the bound is not, both measured on the way here. It
                # is not the *fence's own* indent, which reaches too far: a
                # ` ``` ` one column in at the margin legitimately holds content
                # at column 0, and bounding by the opener handed 1158 markers of
                # that content to the model. And it does not apply to a fence
                # that is not *in* the item — one at the margin under an open
                # item runs to end of file the way CommonMark says, so `ind >=
                # list_col` is load-bearing: the margin rule above declines to
                # close an item on a `lazy` line, and a fence is not a lazy
                # continuation because it may interrupt a paragraph. 1373 markers
                # of `- item\n```\n\ntext`.
                #
                # `min` rather than a test, because `list_col` is deliberately
                # the item's whole prefix and is therefore *larger* than
                # CommonMark's content column. For the code floor that is
                # conservative — too high only keeps text translatable — but read
                # as "is the fence inside the item" it inverts: `- [ ] item` puts
                # `list_col` at 6 while the item's content starts at 2, so a
                # fence indented 2 was judged outside the item, took floor 0, and
                # swallowed the rest of the document. That is the exact failure
                # this branch exists to remove, and adversarial review found it in
                # 400 shapes on 2026-08-03; the sweep's indents stopped at 8, so a
                # checkbox item's `code_floor` of 10 was never reached and this
                # branch was never entered. Taking the smaller of the two is right
                # in both directions: a lower floor only ever runs the fence
                # further, so where the two disagree the conservative answer is
                # the one that stops sooner.
                #
                # With no list open the whole test is vacuous and the run reaches
                # end of file exactly as it did before, which is what
                # `tests/corpus/fences-and-unclosed.md` pins.
                floor = 0 if list_col is None else min(list_col, ind)
                j = i
                while j + 1 < n and (not lines[j + 1].strip()
                                     or _indent_columns(lines[j + 1]) >= floor):
                    j += 1
            j = min(j, n - 1)
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
                quote_para, quote_list_col = False, None
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

        m = DEF_RE.match(line)
        if m:
            emit_raw(line + "\n")
            i += 1
            # A link definition with no destination is not one. `[x]:` is a
            # paragraph to CommonMark, and the indented line under it is that
            # paragraph's lazy continuation rather than code. Only the empty
            # destination is answered here: deciding whether a *non*-empty one
            # is a well-formed link destination is a parser this file does not
            # have, and every case it would catch fails in the safe direction —
            # the text stays translatable.
            para_open = not m.group(2).strip()
            continue

        # table
        if "|" in line and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
            while i < n and "|" in lines[i]:
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
            # A quoted block at the marker's own column closes a list item open
            # inside the quote — the document level's margin rule, one container
            # down, with `quote_para` standing in for `lazy` for the same reason
            # it does there. Without it, one `> - item` anywhere keeps every
            # later quoted chunk in the document translatable.
            if filled and cind == 0 and not quote_para and not LIST_RE.match(content):
                quote_list_col = None
            quote_floor = (CODE_INDENT if quote_list_col is None
                           else quote_list_col + CODE_INDENT)
            # `filled` here is redundant and kept for the reader, the way the
            # chunk loop's `lines[j].strip()` is: a blank quote line takes the
            # other branch to the same bytes, because `emit_seg` refuses a source
            # with nothing translatable in it and emits exactly what this one
            # would. Measured 2026-08-03 over 158543 documents — every spelling
            # of a blank quote line in eight contexts, plus the whole differential
            # sweep — comparing segment sources, kinds and skeleton bytes: zero
            # differences. That is what separates a redundant guard from an
            # untested one, and the mutation harness lists it as equivalent so
            # the next reader does not re-derive it.
            quoted_code = (filled and not quote_para and cind >= quote_floor
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
            if (m2 := LIST_RE.match(content)):
                # The whole prefix, checkbox included, exactly as the document
                # level takes it: never smaller than CommonMark's content column,
                # so the floor it produces is never too low.
                #
                # Measured from the quote's content column, not from zero. The
                # prefix starts where the quote's content starts, so `> 1.\t`
                # puts that tab at column 4 and it advances to 8 — six columns of
                # marker, where measuring the prefix alone scores four and drops
                # the floor by two. Found by adversarial review 2026-08-03 in 42
                # shapes, every one a list prefix containing a tab, after a
                # sweep that spelled a quoted list only as `- `, `- [ ] ` and
                # `1. `. Same origin bug as `_indent_columns` above, one line
                # further down, and the sweep that caught the first did not vary
                # the axis that hides the second.
                quote_list_col = _columns(m2.group(1), _columns(prefix))
            # `not quoted_code`, not `filled` alone: a code block opens no
            # paragraph, so the *next* quoted line is measured against the floor
            # too rather than read as a lazy continuation of it. Without this the
            # first line of a quoted chunk moves into the skeleton and every line
            # under it stays a segment — half a repair, which is worse than none
            # because the block renders as code with its body translated.
            quote_para = filled and not quoted_code
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
                # *here* and never reaches the quote branch. The quote's interior
                # state would then keep whatever it had, and its opening value,
                # False, is the one that turns the next quoted line into
                # skeleton: `-\titem\n   > intro\n>     chunk` lost `chunk` in 40
                # generated shapes. Recorded where the line is actually consumed,
                # rather than by teaching this loop to stop — stopping would
                # re-cut every list item that contains a blockquote, which is a
                # segmentation change and a different package's decision.
                if (mq := QUOTE_RE.match(lines[j])):
                    quote_para = mq.group(2).strip(" \t\r") != ""
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
