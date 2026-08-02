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


#: Character classes that a decode produces when it is wrong and almost never
#: when it is right. Half-width katakana is the signature of ``shift_jis`` eating
#: a Big5 or GBK file: real Japanese prose writes kana full-width, at U+3040–30FF,
#: so this range costs the language it belongs to nothing. C1 is here because no
#: prose contains control characters at all, in any language.
#:
#: Deliberately narrow. The wider rule considered — score every candidate by how
#: much of it is CJK, kana, Hangul or Latin, and take the best — decides between
#: two *plausible* readings, and this project already answered that question with
#: candidate order and a printed winner. This one only removes readings that are
#: not plausible at all, which is why it can be a veto rather than a ranking and
#: why it leaves the recorded ordering rationale in `config.TEXT_DEFAULTS` intact.
_HALFWIDTH_KATAKANA = range(0xFF61, 0xFFA0)
_C1_CONTROLS = range(0x80, 0xA0)


def _implausible(text):
    """True when most of the non-ASCII in ``text`` is mojibake's fingerprint.

    A majority rather than any occurrence: a Japanese document may quote a
    half-width katakana string, and a single one must not veto its own encoding.
    Text with no non-ASCII at all is never implausible — it is ASCII, which every
    candidate agrees on, so there is nothing for a veto to decide.
    """
    non_ascii = [c for c in text if c > "\x7f"]
    if not non_ascii:
        return False
    bad = sum(1 for c in non_ascii
              if ord(c) in _HALFWIDTH_KATAKANA or ord(c) in _C1_CONTROLS)
    return bad * 2 > len(non_ascii)


def decode_document(data, encodings=("utf-8",), name="the document"):
    """``(text, encoding)`` for a document's bytes, or refuse to guess.

    Deliberately **not** ``utf-8-sig``, and no mark is stripped. A byte-order
    mark is a byte the pipeline did not decide to change, so it decodes to
    U+FEFF and belongs in the skeleton like any other;
    ``tests/corpus/bom-utf8-heading.md`` is the fixture that proves it survives.
    The mark stays in the text rather than being stripped, so the write end
    needs no special case for it.

    **Decoding is not reversible for every codec, and cp950 is the case.**
    Measured 2026-08-02: ten two-byte sequences — ``A2CC`` ``A2CE`` ``F9E9``
    ``F9EA`` ``F9EB`` ``F9F9``–``F9FD``, the Big5 duplicate-encoding block —
    decode to a character that re-encodes to *different* bytes. ``A2CC`` is 十 as
    numeric runs write it, and ``F9F9``–``F9FD`` are the box-drawing characters
    a BBS-era Traditional Chinese ``.txt`` draws its chapter rules with, so this
    is the primary corpus rather than an exotic corner. ``gbk``, ``shift_jis``
    and ``cp1252`` are injective; cp950 alone is not.

    Nothing is mangled by it today, because :func:`write_document` encodes UTF-8
    and the pipeline never writes a document back in its source encoding — the
    characters are correct, and it is the *bytes* that would not survive a
    round trip through cp950. What the earlier version of this docstring claimed
    — that re-encoding with the same concrete codec reproduces the original bytes
    — is therefore false, and it was the only thing standing behind invariant 2a
    for this format. Byte-exactness needs raw skeleton nodes held as bytes rather
    than as JSON text, which is the same scheduled work the refusal message at
    the bottom of this function names. Until then
    ``test_source_encoding_write_would_break_invariant_2a`` fails the moment a
    caller writes a document back in its source encoding, which is the one way
    this becomes durable corruption.

    A mark, when there is one, **decides** — it is a declaration rather than a
    guess, and it overrides the candidate list. Without one every candidate is
    tried with ``errors="strict"`` and the first that decodes *plausibly* wins.

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

    First plausible success wins, and *ambiguity is not an error*. Refusing when
    more than one candidate decodes was the alternative and it refuses the
    primary use case: measured, cp1252 accepts every ordinary Big5, GBK and
    Shift-JIS document, so "more than one succeeded" is true of nearly every
    non-UTF-8 novel there is. The residual — simplified Chinese read as Big5, a
    Latin-1 European source read as Shift-JIS — is announced by ``lx extract``,
    which prints the winning encoding, and fixed by reordering the candidates.

    Plausibility is :func:`_implausible`, and it exists because decoding
    successfully is not the same as decoding correctly. Measured 2026-08-02: a
    short Traditional Chinese file — a per-chapter ``.txt``, an epigraph — is
    decoded by ``shift_jis`` into half-width katakana, *byte-reversibly*, so the
    round-trip fixtures cannot see it while every segment's text and hash are
    wrong and get banked in the translation memory. Over 300 slices per length,
    misdetection ran 175/300 at five characters and 5/300 at thirty; with the
    veto it is 0/300 at every length. Japanese cost nothing at any length (its
    kana are full-width), and of 5000 random byte strings none became
    undecodable that was not undecodable before.
    """
    by_bom = False
    for bom, enc in _BOMS:
        if data.startswith(bom):
            candidates, by_bom = (enc,), True
            break
    else:
        candidates = tuple(encodings) or ("utf-8",)

    tried, decoded = [], []
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
        decoded.append((text, enc))

    for text, enc in decoded:
        if not _implausible(text):
            return text, enc
    if decoded:
        # Every candidate that decoded looks like mojibake, so the veto has no
        # opinion left and the candidate order decides, exactly as it did before
        # the veto existed. Deliberately not a refusal: a file this project
        # cannot classify is still a file the reader may want, and refusing here
        # would turn a heuristic into a gate. `lx extract` prints the winner.
        return decoded[0]

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
