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

__all__ = ["read_document", "write_document", "write_document_to_stdout",
           "split_terminator", "apply_terminator"]

# A CRLF pair, an LF that is not part of one, and a CR that is not part of one.
# Counted separately because the three answer different questions: the first is
# the terminator, the second says the file is not uniform, and the third is
# ordinary text under `docs/decisions.md`, 2026-07-28 — `parse` splits on "\n"
# alone, so a lone CR is a character in a sentence, not a line ending.
_CRLF_RE = re.compile(r"\r\n")
_LONE_LF_RE = re.compile(r"(?<!\r)\n")
_LONE_CR_RE = re.compile(r"\r(?!\n)")
_ANY_LF_RE = re.compile(r"\r?\n")


def read_document(path):
    """Decode a source document, preserving every line terminator it contains.

    Deliberately **not** ``utf-8-sig``. A byte-order mark is a byte the pipeline
    did not decide to change, so it belongs in the skeleton like any other;
    ``tests/corpus/bom-utf8-heading.md`` is the fixture that proves it survives.
    Stripping it here would silently make that fixture pass for the wrong reason.
    """
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


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
