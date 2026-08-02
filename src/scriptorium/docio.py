"""Reading and writing user documents without letting text mode touch a byte.

Python's text mode is wrong here in both directions. Reading enables universal
newlines, so ``\\r\\n`` and a lone ``\\r`` both arrive as ``\\n`` and the parser
never sees what the file actually contained. Writing translates every ``\\n`` to
``os.linesep``, so the platform decides the document's line endings rather than
the document. Together they made a CRLF file round-trip on Windows by
coincidence, lose every CR on Linux, and made mixed terminators impossible
anywhere — none of which is a change the pipeline decided to make, which is what
invariant 2a forbids.

This module is for *user documents* only. ``config.py`` and the ``.lx/`` state
stay in text mode on purpose: those are files this project writes for itself, no
invariant claims their bytes, and changing them is a separate decision with its
own reasoning. The one exception is the translation memory, which is an append
log whose terminator ``.gitattributes`` already pins to LF.
"""

import os
import re
import sys

__all__ = ["UndecodableDocument", "decode_document", "read_document",
           "write_document", "write_document_to_stdout",
           "split_terminator", "apply_terminator"]


class UndecodableDocument(ValueError):
    """A file whose bytes are not valid in any encoding tried. Refused, not mangled."""


# A CRLF pair, an LF that is not part of one, and a CR that is not part of one.
# Counted separately because the three answer different questions: the first is
# the terminator, the second says the file is not uniform, and the third is
# ordinary text under `docs/decisions.md`, 2026-07-28 — `parse` splits on "\n"
# alone, so a lone CR is a character in a sentence, not a line ending.
_CRLF_RE = re.compile(r"\r\n")
_LONE_LF_RE = re.compile(r"(?<!\r)\n")
_LONE_CR_RE = re.compile(r"\r(?!\n)")
_ANY_LF_RE = re.compile(r"\r?\n")


#: Byte-order marks, longest first. The order is load-bearing rather than tidy:
#: UTF-32-LE begins with the whole UTF-16-LE mark, so reading them the other way
#: round decodes a UTF-32 file as UTF-16 and produces text made of NUL characters.
#:
#: Each maps to a **concrete** codec, never to bare ``utf-16`` or ``utf-8-sig``.
#: Measured: ``"﻿Hello".encode("utf-16")`` is ``b'\xff\xfe\xff\xfeH\x00…'`` —
#: those codecs write a mark of their own on top of the one already in the text,
#: so a document that round-trips through them gains three bytes every time.
_BOMS = (
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def decode_document(data, encodings=("utf-8",), name="the document"):
    """``(text, encoding)`` for a document's bytes, or refuse to guess.

    Deliberately **not** ``utf-8-sig``, and no mark is stripped. A byte-order
    mark is a byte the pipeline did not decide to change, so it decodes to
    U+FEFF and belongs in the skeleton like any other;
    ``tests/corpus/bom-utf8-heading.md`` is the fixture that proves it survives.
    Because the mark stays in the text, encoding back with the same concrete
    codec reproduces the original bytes with no special case at the write end.

    A mark, when there is one, **decides** — it is a declaration rather than a
    guess, and it overrides the candidate list. Without one the candidates are
    tried in order with ``errors="strict"`` and the first that decodes wins.

    Two rules keep that from mangling a file instead of refusing it:

    - A decode that yields a NUL character is rejected. UTF-16 without a mark is
      the case: ``"Hello".encode("utf-16-le")`` is valid UTF-8, and it decodes to
      ``'H\\x00e\\x00…'`` rather than raising. No plain-text novel contains a NUL,
      so nothing legitimate is lost, and the file is refused with a message
      instead of silently becoming interleaved rubbish.
    - Nothing falls back to ``latin-1``, which decodes every possible byte and
      would make refusal impossible. ``cp1252`` is the closest the default list
      comes and it is deliberately last: measured 2026-08-02, none of its five
      undefined bytes can occur in a standard Big5 or GB2312 stream, so for those
      it is a *total* catch-all rather than a near one. What keeps refusal
      reachable is that a damaged file is usually damaged in a way cp1252 also
      rejects — a truncated multi-byte sequence ends on one of those five bytes
      often enough — and what keeps a Chinese novel from reaching it is the order
      of the candidates in front of it, not any property of cp1252 itself.

    First success wins, and *ambiguity is not an error*. Refusing when more than
    one candidate decodes was the alternative and it refuses the primary use
    case: measured, cp1252 accepts every ordinary Big5, GBK and Shift-JIS
    document, so "more than one succeeded" is true of nearly every non-UTF-8
    novel there is. The residual — simplified Chinese read as Big5, a Latin-1
    European source read as Shift-JIS — is announced by ``lx extract``, which
    prints the winning encoding, and fixed by reordering the candidate list.
    """
    by_bom = False
    for bom, enc in _BOMS:
        if data.startswith(bom):
            candidates, by_bom = (enc,), True
            break
    else:
        candidates = tuple(encodings) or ("utf-8",)

    tried = []
    for enc in candidates:
        try:
            text = data.decode(enc)
        except UnicodeDecodeError as e:
            tried.append(f"{enc} (invalid byte at offset {e.start})")
            continue
        except LookupError:
            tried.append(f"{enc} (no such codec in this Python)")
            continue
        if "\x00" in text:
            tried.append(f"{enc} (decodes, but to text containing NUL — "
                         f"almost always UTF-16 or UTF-32 without a byte-order mark)")
            continue
        return text, enc

    # A byte-order mark overrides the candidate list, so telling the reader to
    # edit that list would be advice that changes nothing. The mark is a
    # declaration the file makes about itself, and a file that declares one and
    # then contradicts it is damaged in the second sense below, not misconfigured.
    hint = (f"Its byte-order mark declares {candidates[0]}, which overrides the configured "
            f"candidates, and the bytes after it do not decode as that.\n"
            if by_bom else
            "If you know the encoding, put it first in \"formats\": "
            "{\"text\": {\"encodings\": [...]}} in lx.config.json — a name Python knows, "
            "such as \"cp950\", \"gbk\", \"shift_jis\" or \"utf-16-le\".\n")
    raise UndecodableDocument(
        f"{name} could not be decoded. Tried: {'; '.join(tried)}.\n"
        f"{hint}"
        f"If the file really does contain bytes that are invalid in its own encoding — a "
        f"truncated download, a damaged archive — it is refused rather than repaired with "
        f"replacement characters, because that silently changes bytes this project promises "
        f"to preserve. Reading one needs the state layer to hold raw skeleton nodes as bytes "
        f"rather than as JSON text, which is scheduled and not built; until then, repair the "
        f"file with a tool that shows you what it changed.")


def read_document(path, encodings=("utf-8",)):
    """``(text, encoding)`` for a source document, every terminator preserved.

    Returns the encoding as well as the text because the document's own state
    file records it, the same way it records ``eol``: both are facts about the
    file that the segments must not carry and that a later command must not have
    to re-derive.

    The default is UTF-8 alone, which is what this function did before formats
    existed and what Markdown still asks for. A format with more candidates
    passes them; :func:`formats.encodings` is where they come from.
    """
    with open(path, "rb") as f:
        return decode_document(f.read(), encodings, name=str(path))


def split_terminator(text):
    """Return ``(text_for_the_parser, eol)`` for a document just read.

    A document whose every line ends CRLF is handed to the parser as LF and its
    terminator is recorded, to be re-applied once at render. Anything else — LF,
    mixed, CR-only, or no terminator at all — is returned untouched with ``"\\n"``,
    which means "no re-application" rather than "this file uses LF".

    **Why the terminator is not left in the segment.** Left in place it ends up
    inside a wrapped block's source, and from there it reaches the model, which
    is asked to reproduce an invisible control character. Measured on
    ``crlf-line-endings.md``: five different answers — CRLF kept, LF only, lines
    joined, a break added, a bare CR — all produced zero errors from
    ``check_segment`` and none was collected by ``failing_segments``, so the
    repair loop cannot see a wrong one. Invariant 4 says a rule belongs in
    ``checks.py`` only if a program can decide it, and no program can: the only
    candidate rule, comparing break counts, rejects the legitimate case where a
    translation rewraps. The human path is no better — the workbench edits
    segments in an HTML ``textarea``, and the parser collapses CRLF to LF in the
    element's value before a reviewer touches it.

    Normalizing here is safe in a way it was not before ``parse`` learned to hold
    a terminator (``docs/decisions.md``, 2026-07-28): that entry's objection was
    to doing this *instead of* fixing the parser, because it would have hidden
    the defect. The corpus test calls ``parse`` on the raw bytes and never comes
    through this module, so it still measures the parser on its own.

    The mixed case is a deliberate, recorded residual: it keeps today's verbatim
    behaviour, CR and all. Handling it needs a per-segment mechanism, which is
    the wrong price for one file in 76 — the containment validators own it.
    """
    if _CRLF_RE.search(text) and not _LONE_LF_RE.search(text) \
            and not _LONE_CR_RE.search(text):
        return text.replace("\r\n", "\n"), "\r\n"
    return text, "\n"


def apply_terminator(text, eol):
    """Re-impose a document's recorded terminator on freshly rendered text.

    A blanket substitution, not a positional one, which is what makes it immune
    to the model changing how many lines a block wraps to: joining two lines,
    adding four, or returning none all yield a correct document. A per-position
    record of the original terminators cannot survive that, and rewrapping is
    ordinary in translation — CJK targets are routinely shorter than their
    English sources.

    ``\\r?\\n`` rather than ``\\n`` so a stray CRLF from a model cannot become
    ``\\r\\r\\n``. A lone CR it invents is left alone, because it is
    indistinguishable from the lone CR that ``decisions.md`` classifies as text.
    """
    return text if eol == "\n" else _ANY_LF_RE.sub(eol, text)


def write_document(path, text):
    """Write a rendered document with the terminators the text already carries.

    Encoding to bytes rather than opening with ``newline=""`` because the two are
    equivalent for correctness and only one of them is obvious to the next reader:
    a missing ``newline=""`` looks like an oversight, while ``.encode("utf-8")``
    states that the caller decided the bytes.

    Creating the parent directory is folded in because every call site did it
    immediately before opening the file, and a site that forgets is a crash the
    tests would not see.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def write_document_to_stdout(text):
    """Emit a rendered document on stdout without newline or codec translation.

    ``lx render --out -`` writes a document, not a message, so it goes out as the
    bytes the render produced. On Windows the text layer would otherwise expand
    every ``\\n`` to ``\\r\\n`` and encode through the console code page, which
    raises ``UnicodeEncodeError`` the moment the output is redirected.

    Diagnostics printed by ``lx todo`` and ``lx check`` have the same encoding
    problem and *not* this newline one; they are fixed separately by
    reconfiguring the stream, so the two repairs stay independent.

    ``sys.stdout`` is replaced by a stand-in under test and in embedded callers,
    and not every stand-in exposes a binary buffer — falling back to a text write
    keeps those working, at the cost of the translation this function exists to
    avoid, which is acceptable only because no document is written that way.
    """
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:
        sys.stdout.write(text)
        return
    sys.stdout.flush()
    buf.write(text.encode("utf-8"))
    buf.flush()
