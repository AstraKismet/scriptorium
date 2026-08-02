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

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*?)(\s*#*\s*)$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-{2,})\s*$")
LIST_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
QUOTE_RE = re.compile(r"^(\s*>\s?)(.*)$")
HR_RE = re.compile(r"^\s{0,3}(?:\*{3,}|-{3,}|_{3,})\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
DEF_RE = re.compile(r"^(\s*\[[^\]]+\]:\s*)(.*)$")

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
    """
    col = 0
    for ch in s:
        col += TAB_STOP - col % TAB_STOP if ch == "\t" else 1
    return col


def _indent_columns(line):
    """Columns of ``line``'s leading space/tab run.

    Only those two characters indent. ``str.lstrip()`` would also eat U+3000, a
    form feed and U+00A0, none of which CommonMark counts — and U+3000 in
    particular is the zh-TW paragraph indent, so counting it would turn ordinary
    translated prose into a code block.
    """
    return _columns(line[: len(line) - len(line.lstrip(" \t"))])


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

    while i < n:
        line = lines[i]
        ind = _indent_columns(line)
        # Read for this line, then cleared. The three branches that leave a
        # paragraph open — quote, list, paragraph — set it again on the way out,
        # so every other block start closes one by saying nothing.
        lazy, para_open = para_open, False

        # Only a block *start* reaches the top of this loop — every branch below
        # consumes its own continuations — so a block starting at the left margin
        # is one no open list item can contain, and closes it. A blank line
        # closes nothing: a list item is free to hold several paragraphs.
        if line.strip() and ind == 0 and not LIST_RE.match(line):
            list_col = None

        m = FENCE_RE.match(line)
        if m:
            fence = m.group(2)[0] * 3
            j = i + 1
            while j < n and not re.match(rf"^\s*{re.escape(fence)}", lines[j]):
                j += 1
            j = min(j, n - 1)
            emit_raw("\n".join(lines[i : j + 1]) + "\n")
            i = j + 1
            continue

        if not line.strip() or HR_RE.match(line) or SETEXT_RE.match(line):
            emit_raw(line + "\n")
            i += 1
            continue

        # An indented code block, held in the skeleton the way a fenced one
        # already is. Two spellings of one construct were being treated
        # oppositely: the model was asked to translate Python, and the four
        # spaces that make it code sat at position 0 of the segment where
        # `translate.accept` strips them off every proposal it takes.
        #
        # CommonMark's other half of the rule — an indented chunk cannot
        # interrupt a paragraph — is `lazy`. Reading it off the paragraph
        # branch's reach instead was the first attempt and it was wrong in the
        # one direction that costs a translation; see `para_open` above.
        code_floor = CODE_INDENT if list_col is None else list_col + CODE_INDENT
        if not lazy and ind >= code_floor and not _carries_a_text_cr(line):
            j = i
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
            emit_raw(m.group(1))
            emit_seg(m.group(2), "quote")
            emit_raw("\n")
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
