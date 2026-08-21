"""Plain text to render skeleton + translatable segments.

The format novels actually arrive in, and the first non-Markdown one. It
implements the same pair as :mod:`.mdparse` — ``parse(text, dnt, opts) ->
(nodes, segments)``, with ``render`` shared from :mod:`.skeleton` — and is
reached through :mod:`.formats` rather than by anyone importing it directly.

Three things here are heuristics rather than rules, and all three are therefore
configuration under ``formats.text`` in ``lx.config.json`` (invariant 4 keeps
judgement out of ``checks.py``, and a guess about someone else's file is
judgement): which encoding a file is in — that one lives in :mod:`.docio`,
because it is a question about bytes — where a paragraph ends, and which line is
a chapter title.

**Plain text has no markup**, so nothing here masks anything of its own; ``mask``
still runs, because a URL or a ``{placeholder}`` in a novel is as
non-translatable as it is in a manual. That is also why every segment carries
``host="text"``: :func:`checks.containment_problems` reads it and applies the
plain-text profile, under which no line opens a block — a line of dialogue
beginning ``- `` is dialogue, not a list item.
"""

import re

from .config import TEXT_DEFAULTS
from .mask import has_translatable_text, mask, strip_placeholders
from .skeleton import render, render_blocks
from .store import seg_hash

__all__ = ["MARKER", "describe", "parse", "render", "render_blocks", "split_document"]

#: What stands in for an untranslated segment. Not Markdown's HTML comment: in a
#: .txt file that is four words of visible junk pretending to be invisible.
MARKER = "[untranslated {id}]"

def _blank(line):
    """A line the reader sees as empty.

    ``str.strip()`` rather than a character class, deliberately: it already takes
    the ideographic space U+3000 that indents CJK prose and the form feed U+000C
    that separates chapters in older text files. Both are blank lines to a
    person, and a character class would have to name them one at a time.
    """
    return not line.strip()


#: The three shapes plain text arrives in, and the value that asks for a guess.
MODES = ("blank-line", "line", "indent")


def paragraph_mode(lines, opts):
    """Which shape this document is in. ``auto`` reads the file to decide.

    All three shapes are ordinary, and the difference between them is the whole
    of this format's segmentation.

    ``blank-line`` — a hard-wrapped novel with a blank line between paragraphs.
    Treating each physical line as a paragraph here would cut every sentence into
    fragments the model has to translate blind, which is the alternative
    ``docs/decisions.md`` rejected on 2026-07-28 for list continuations and D2 of
    2026-07-29 rejected again for prose.

    ``line`` — one paragraph per line, no blank lines. Treating *that* as one
    blank-line-separated block makes the whole book a single segment.

    ``indent`` — hard-wrapped, no blank lines, a new paragraph marked by an
    indented first line. Neither of the other two segments it correctly.

    ``auto`` chooses between the first two only, and asks whether a blank line
    ever *separates* two runs of text rather than whether one exists: a file
    whose only blank line is at the end — the shape a trailing newline plus an
    editor's final blank produces — is one-per-line, and the weaker test would
    call it blank-line separated and hand back two enormous segments.

    **It deliberately never guesses ``indent``.** The available test — some lines
    indented, some not — is equally true of a per-line file containing one
    indented line, and guessing wrong there joins the entire book into a handful
    of segments. A project with an indent-marked novel names the mode; a wrong
    guess is more expensive than a config line.

    ``auto`` can still be wrong the other way: a per-line file with one stray
    blank line in the middle reads as blank-line separated. Same answer, same
    config key. The outcome is recorded in the skeleton rather than re-derived,
    so a document already extracted does not shift under a config edit — but a
    re-extract after one does re-cut it, which is what ``lx commit`` before
    changing this setting is for.
    """
    mode = opts.get("paragraph_mode") or TEXT_DEFAULTS["paragraph_mode"]
    if mode in MODES:
        return mode
    filled = [i for i, line in enumerate(lines) if not _blank(line)]
    if not filled:
        return "line"
    # Only the blank lines *between* the first and last line of text count, which
    # is what makes leading and trailing blanks — the shape an editor's final
    # newline produces — not decide the whole document.
    return ("blank-line" if any(_blank(line) for line in lines[filled[0]:filled[-1]])
            else "line")


def _indented(line):
    """Whether a line begins with whitespace. The paragraph mark in ``indent`` mode.

    ``str.isspace()`` on the first character, so an empty line is not indented
    and an ideographic space U+3000 is — the indent CJK prose actually uses.
    """
    return line[:1].isspace()


def split_document(text):
    """``(bom, lines, trailing_nl)`` — the document cut the way :func:`parse` cuts it.

    One helper for the two functions that have to agree about it, because they
    did not. A byte-order mark is **not** whitespace, so a file beginning
    ``\\ufeff\\n\\n`` has a non-blank first line before the mark is taken out and a
    blank one after: measured 2026-08-02, ``describe`` reported ``blank-line``
    for a document ``parse`` had already cut one paragraph per line, so the state
    file and the ``lx extract`` line both stated something the skeleton
    contradicted.

    The whole leading *run* of marks comes out, not one. A doubled mark is not
    hypothetical — it is exactly what Python's bare ``utf-16`` codec writes over
    text that already carries one, which is why :mod:`.docio` never names that
    codec — and left in place the second rides into the first segment's source,
    where the model is asked to reproduce it and the memory key splits.
    """
    body = text.lstrip("﻿")
    bom = text[: len(text) - len(body)]
    lines = body.split("\n")
    trailing_nl = body.endswith("\n")
    if trailing_nl and lines and lines[-1] == "":
        lines.pop()          # the split artifact, not a real blank line
    return bom, lines, trailing_nl


def describe(text, opts=None):
    """Facts about how this document was cut, for the document's own state file.

    Only the resolved paragraph mode, and it is here rather than inferred later
    because ``auto`` is a heuristic that can be wrong: printing what it decided
    at ``lx extract`` is what turns a wrong guess from something the reviewer
    discovers on page four into something they read on the line that made it.
    """
    return {"paragraph_mode": paragraph_mode(split_document(text)[1], opts or {})}


def _chapter_patterns(opts):
    raw = opts.get("chapter_patterns")
    if raw is None:
        raw = TEXT_DEFAULTS["chapter_patterns"]
    return [re.compile(p, re.IGNORECASE) for p in raw]


def parse(text, dnt=(), opts=None):
    """Split plain text into a render skeleton + translatable segments.

    Blank lines, and any block with no letters in it — a ``* * *`` scene break, a
    row of digits, a rule of underscores — stay in the skeleton, where they
    round-trip for free and the model never sees them.
    """
    opts = opts or {}

    # A byte-order mark decodes to a character, and it is a byte the pipeline did
    # not decide to change, so it goes in the skeleton whole rather than riding
    # into the first segment's source. `mdparse` still carries it inside a
    # segment; that is the older behaviour its fixture pins, not a standard to
    # copy. `split_document` says why the run is taken rather than one mark, and
    # why `describe` has to call the same helper.
    bom, lines, trailing_nl = split_document(text)

    mode = paragraph_mode(lines, opts)
    chapters = _chapter_patterns(opts)
    # The host is how `checks.containment_problems` learns which shape this
    # document is in. It has to be a per-segment field because that is all a
    # validator is given, and it is `host` rather than `kind` because `kind`
    # becomes `context`: a one-line paragraph banked from a wrapped document must
    # still answer for a one-per-line document, and a second kind would split it.
    host = "text-line" if mode == "line" else "text"
    nodes, segs = [], []
    counter = [0]

    def emit_raw(s):
        if not s:
            return
        if nodes and nodes[-1]["t"] == "raw":
            nodes[-1]["v"] += s
        else:
            nodes.append({"t": "raw", "v": s})

    def emit_seg(source, kind):
        # The trailing CR of a CRLF document, moved into the skeleton exactly as
        # `mdparse.emit_seg` moves it: `parse` splits on "\n" alone, so the CR is
        # at the end of the block, and it is part of the line ending rather than
        # part of the sentence. See `docs/decisions.md`, 2026-07-28.
        stripped = source.rstrip("\r")
        cr = source[len(stripped):]
        source = stripped
        if not has_translatable_text(source):
            emit_raw(source + cr)
            return
        counter[0] += 1
        sid = f"s{counter[0]:04d}"
        masked, slots = mask(source, dnt)
        if not strip_placeholders(masked).strip():
            emit_raw(source + cr)   # nothing left to translate
            counter[0] -= 1
            return
        segs.append({
            # `context` is the block kind, which is what Markdown uses and for
            # the same reason: it is the smallest thing that keeps a chapter
            # title from sharing a memory entry with a body paragraph of the
            # same words. Position — a paragraph index or a chapter id — was the
            # alternative and loses: it drives exact reuse to zero and makes
            # inserting one paragraph invalidate every entry after it.
            "id": sid, "kind": kind, "hash": seg_hash(source),
            "context": kind, "variant": None, "host": host,
            "source": source, "masked": masked, "slots": slots,
            "target": None, "status": "pending", "origin": None,
        })
        nodes.append({"t": "seg", "id": sid})
        if cr:
            emit_raw(cr)    # lands ahead of the caller's "\n", restoring the CRLF

    emit_raw(bom)

    i, n = 0, len(lines)
    while i < n:
        if _blank(lines[i]):
            emit_raw(lines[i] + "\n")
            i += 1
            continue

        j = i + 1
        if mode == "blank-line":
            while j < n and not _blank(lines[j]):
                j += 1
        elif mode == "indent":
            # A paragraph runs until the next indented line, which is the mark
            # this shape uses instead of a blank line. Blank lines still end it,
            # so a file that mixes the two conventions is not made worse.
            while j < n and not _blank(lines[j]) and not _indented(lines[j]):
                j += 1
        block = lines[i:j]

        # The first line's indent is skeleton: it is layout and the model has no
        # use for it. This is the *stronger* answer to the question `mdparse`
        # cannot give — where a first-line indent can be lifted out, lifting it
        # out is better than protecting it downstream, because then no proposal
        # can carry one in either. A model that helpfully adds a U+3000 paragraph
        # indent to a zh-TW target still has it removed, since the source it is
        # reseated against has none (`normalize.reseat_outer_blanks`).
        #
        # The *continuation* lines keep theirs, because an indent that sits after
        # a newline inside the segment cannot be held by a raw node — the same
        # shape, and the same reasoning, as a wrapped list item in `mdparse`.
        head = block[0]
        indent = head[: len(head) - len(head.lstrip())]
        block[0] = head[len(indent):]

        source = "\n".join(block)
        kind = ("heading" if len(block) == 1 and _is_chapter(source, chapters)
                else "para")
        emit_raw(indent)
        emit_seg(source, kind)
        emit_raw("\n")
        i = j

    # every block emitter appends its own newline; drop the last one when the
    # source did not actually end with a line break
    if not trailing_nl and nodes and nodes[-1]["t"] == "raw" and nodes[-1]["v"].endswith("\n"):
        nodes[-1]["v"] = nodes[-1]["v"][:-1]
        if not nodes[-1]["v"]:
            nodes.pop()

    return nodes, segs


def _is_chapter(source, patterns):
    """Whether a one-line block reads as a chapter title.

    Matched against the line stripped of surrounding whitespace and of the CR a
    CRLF document leaves behind, so a pattern anchored with ``$`` behaves the way
    its author expects.

    Getting it wrong is cheap in both directions, which is what makes a heuristic
    acceptable here at all: a missed title is translated as a paragraph, and a
    false positive — a one-line paragraph that happens to open with "Chapter" —
    gets `context="heading"` and the no-added-lines rule, neither of which
    changes what the reader sees.
    """
    line = source.strip()
    return any(p.match(line) for p in patterns)
