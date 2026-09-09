"""Document I/O byte-exactness (invariant 2a, at the file boundary).

`test_pipeline.py` proves the *parser* reproduces every byte: it substitutes each
segment source straight back into the skeleton, deliberately bypassing render()
so a failure cannot be a masking defect wearing a skeleton defect's clothes. That
left the other half untested, and untested it was wrong — the CLI read documents
in text mode, so universal newlines deleted every CR before the parser was
reached, and wrote them in text mode, so the platform chose the terminator.

These tests close the loop through the real CLI entry points and the filesystem.
Two properties, deliberately separate: the read helper hands over exactly the
bytes on disk, and a document that goes all the way out to a file and back is
unchanged. The first can pass while the second fails, which is why it is asserted
on its own.
"""

import json
import os
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.cli import do_extract, do_render  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.docio import (  # noqa: E402
    ByteSpanMismatch,
    apply_terminator,
    byte_spans,
    read_document,
    split_terminator,
    write_document,
)
from scriptorium.mdparse import parse  # noqa: E402
from scriptorium.store import append_tm, tm_path  # noqa: E402

CORPUS = pathlib.Path(__file__).parent / "corpus"
CFG = dict(DEFAULT_CONFIG)


def _corpus_files():
    return sorted(p for p in CORPUS.iterdir() if p.is_file())


CASES = [pytest.param(p, id=p.name) for p in _corpus_files()]


@pytest.mark.parametrize("path", CASES)
def test_read_helper_returns_the_bytes_on_disk(path):
    # The whole point of the helper. Text mode would delete every CR here, and
    # utf-8-sig would eat the BOM fixture's first three bytes — both silently.
    # It returns the encoding beside the text since formats landed, because the
    # document's state file records it the way it records `eol`.
    assert read_document(path) == (path.read_bytes().decode("utf-8"), "utf-8",
                                   path.read_bytes())


@pytest.mark.parametrize("path", CASES)
def test_document_survives_extract_render_and_write(tmp_path, monkeypatch, path):
    """The property this whole package exists for, on the real CLI path.

    `fallback=True` with nothing translated means render() takes the fallback
    branch for every segment, and that branch does not call `polish` — see
    `mdparse.render`. So this measures the I/O layer alone, with no deliberate
    change mixed in. If polish ever moves onto that branch this test will start
    failing for a reason that is not a bug in this module, and the fix is to pass
    `polish=None` explicitly, never to relax the assertion.

    It also runs the state through JSON and back, which is where a raw node
    holding a CR would be lost if `dump_json` were ever made lossy.
    """
    raw = path.read_bytes()
    monkeypatch.chdir(tmp_path)
    src = tmp_path / path.name
    shutil.copyfile(path, src)          # bytes, not text: copyfile does not translate

    do_extract(str(src), "zh-TW", CFG)
    text, _missing = do_render(str(src), "zh-TW", CFG, fallback=True)

    out = tmp_path / "out" / path.name
    write_document(str(out), text)
    assert out.read_bytes() == raw, _explain(path.name, raw, out.read_bytes())


def _explain(name, expected, actual):
    # repr() windowed on the first difference: a CR is invisible otherwise, and
    # long-manual.md is 112k characters.
    i = next((k for k, (a, b) in enumerate(zip(expected, actual)) if a != b),
             min(len(expected), len(actual)))
    lo, hi = max(0, i - 60), i + 60
    return (f"{name} did not survive the round trip; first difference at byte {i}\n"
            f"  expected: {expected[lo:hi]!r}\n"
            f"  actual  : {actual[lo:hi]!r}\n"
            f"  lengths : expected {len(expected)}, actual {len(actual)}")


def test_write_helper_does_not_translate_line_endings(tmp_path):
    # Direct, so a regression is reported here rather than as 28 corpus failures.
    dest = tmp_path / "nested" / "doc.md"
    write_document(str(dest), "a\nb\r\nc\rd")
    assert dest.read_bytes() == b"a\nb\r\nc\rd"


# --- the terminator profile (M-A) ------------------------------------------


@pytest.mark.parametrize("raw, fed, eol", [
    ("a\r\nb\r\n", "a\nb\n", "\r\n"),        # uniform CRLF: normalized, recorded
    ("a\nb\n", "a\nb\n", "\n"),              # uniform LF: untouched
    ("a\r\nb\nc\r\n", "a\r\nb\nc\r\n", "\n"),  # mixed: verbatim, the residual
    ("one\rtwo\r", "one\rtwo\r", "\n"),      # CR-only is text, not a terminator
    ("no terminator", "no terminator", "\n"),
])
def test_terminator_profile(raw, fed, eol):
    assert split_terminator(raw) == (fed, eol)


def test_apply_terminator_is_blanket_not_positional():
    # The property that makes this immune to a model rewrapping a block: the
    # count of line breaks in the target need not match the source's.
    assert apply_terminator("one\ntwo\nthree\n", "\r\n") == "one\r\ntwo\r\nthree\r\n"
    assert apply_terminator("joined into one line", "\r\n") == "joined into one line"
    assert apply_terminator("a\nb", "\n") == "a\nb"
    # A stray CRLF from a model must not become \r\r\n.
    assert apply_terminator("a\r\nb", "\r\n") == "a\r\nb"


def test_a_uniform_crlf_document_hands_the_model_no_carriage_return():
    """The point of the whole mechanism.

    Measured before this landed: the wrapped paragraph of `crlf-line-endings.md`
    reached the model as `...Windows. Every terminator here is CRLF,\\r\\ncontinues...`
    — an invisible control character it was expected to copy, with no check able
    to tell whether it had.
    """
    raw, _enc, _data = read_document(CORPUS / "crlf-line-endings.md")
    fed, eol = split_terminator(raw)
    assert eol == "\r\n"
    _nodes, segs = parse(fed, [])
    assert segs, "fixture should produce segments"
    offenders = [s["id"] for s in segs if "\r" in s["source"] or "\r" in s["masked"]]
    assert not offenders, f"segments still carrying a CR: {offenders}"


def test_crlf_and_lf_twins_share_translation_memory():
    """A sentence must not need translating twice because a file came from Windows.

    Measured before this landed: the wrapped paragraph hashed 8fcdf9940052 under
    CRLF and c788218aac8a under LF, so the two spellings of one document could
    not share a memory entry. The LF hash is also what text-mode reads produced
    all along, which is why no existing `.lx/` state or `tm.*.jsonl` moves.
    """
    crlf, _enc, _data = read_document(CORPUS / "crlf-line-endings.md")
    lf = crlf.replace("\r\n", "\n")
    assert "\r" in crlf, "fixture must actually be CRLF"
    hashes = [[s["hash"] for s in parse(split_terminator(t)[0], [])[1]] for t in (crlf, lf)]
    assert hashes[0] == hashes[1]


def test_mixed_terminators_keep_todays_behaviour_and_still_round_trip():
    """The recorded residual, asserted rather than assumed — and now half closed.

    A mixed document has no single terminator to re-impose, so it is passed
    through verbatim and its CRs stay in the segment source. Bytes still survive;
    what does not survive is the guarantee that the model is never shown a CR.

    The containment validators landed 2026-07-28 and did **not** close it, which
    is worth saying because the package that scheduled them expected otherwise.
    Their `eol` rule makes an *invented* carriage return an error, and "invented"
    means the segment source has none — true of every uniform document, false of
    this one, where the CR is in the source and the rule is therefore inert.
    Measured on this fixture: CRLF kept, LF only and a bare CR added all still
    report zero structural issues. Catching that would mean comparing CR
    *position*, which a translation is free to change by rewrapping, so invariant
    4 excludes it; closing it properly needs the per-segment terminator mechanism
    `docs/decisions.md` (2026-07-28, "Where a line terminator lives") prices
    against one fixture in 27. If this test ever starts failing because no
    segment carries a CR, the residual has been closed and this test should be
    replaced, not deleted.
    """
    raw, _enc, _data = read_document(CORPUS / "crlf-mixed-terminators.md")
    fed, eol = split_terminator(raw)
    assert (fed, eol) == (raw, "\n")
    _nodes, segs = parse(fed, [])
    assert any("\r" in s["source"] for s in segs)


def test_translation_memory_is_appended_with_lf(tmp_path, monkeypatch):
    # Asserted on bytes. readlines() normalizes, so it reports success either way
    # — which is exactly how CRLF got into the log while .gitattributes declared
    # `*.jsonl text eol=lf` and nobody noticed.
    monkeypatch.chdir(tmp_path)
    append_tm("zh-TW", [{"hash": "a1", "source": "one", "target": "一"},
                        {"hash": "b2", "source": "two", "target": "二"}])
    blob = pathlib.Path(tm_path("zh-TW")).read_bytes()
    assert b"\r" not in blob
    assert blob.count(b"\n") == 2
    assert json.loads(blob.decode("utf-8").splitlines()[0])["hash"] == "a1"


# ── byte_spans: the query behind invariant 2a's byte guarantee ───────────────

_CHAPTER = ("第一章\n\n".encode("cp950")
            + b"\xf9\xfa" + b"\xf9\xf9" * 4 + b"\xf9\xfb" + b"\n\n"
            + "他走了".encode("cp950") + b"\xa2\xcc" + "年。\n".encode("cp950"))


def test_byte_spans_returns_the_source_spelling_and_not_the_canonical_one():
    """The property the whole package exists for, at its smallest.

    `A2CC` and the `F9`-family box rules decode to characters that re-encode to
    *different* bytes, so `text.encode("cp950")` is not the file. Slicing it is.
    """
    text = _CHAPTER.decode("cp950")
    assert text == "第一章\n\n╭════╮\n\n他走了十年。\n"
    parts = [text[:5], text[5:11], text[11:]]           # heading, rule, prose
    spans = byte_spans(_CHAPTER, "cp950", parts)
    assert b"".join(_CHAPTER[a:b] for a, b in spans) == _CHAPTER
    # The rule keeps its F9 spelling and the prose keeps A2CC, and neither is
    # what re-encoding the character would have produced.
    assert _CHAPTER[spans[1][0]:spans[1][1]] == b"\xf9\xfa" + b"\xf9\xf9" * 4 + b"\xf9\xfb"
    assert b"\xa2\xcc" in _CHAPTER[spans[2][0]:spans[2][1]]
    assert text.encode("cp950") != _CHAPTER, "the premise: re-encoding is lossy here"


def test_byte_spans_is_contiguous_and_covers_the_whole_file():
    text = _CHAPTER.decode("cp950")
    spans = byte_spans(_CHAPTER, "cp950", [text[:5], text[5:11], text[11:]])
    assert spans[0][0] == 0 and spans[-1][1] == len(_CHAPTER)
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


def test_a_wrong_recorded_encoding_is_refused_rather_than_answered():
    """The field this query makes load-bearing, and why that is safe.

    `doc["encoding"]` was decorative before this work — read in one place and
    printed. A byte query consumes it, so a wrong value has to be loud. It is,
    because the walk compares what the codec produces against characters the
    state already holds: a codec that decodes to something else cannot get past
    the first part, and one that raises cannot get past the first byte.

    A codec that yields *identical* characters is not a failure and is not
    listed here: the bytes recovered are still the file's own.
    """
    text = _CHAPTER.decode("cp950")
    parts = [text[:5], text[5:11], text[11:]]
    for wrong in ("utf-8", "gbk", "shift_jis", "cp1252", "utf-16-le", "big5"):
        with pytest.raises(ByteSpanMismatch):
            byte_spans(_CHAPTER, wrong, parts)


@pytest.mark.parametrize("mangle,expected", [
    (lambda d: d + b"tail", "left over"),
    (lambda d: d[:-4], "ran out"),
], ids=["extra-bytes", "truncated"])
def test_byte_spans_refuses_a_blob_that_is_not_this_documents(mangle, expected):
    text = _CHAPTER.decode("cp950")
    parts = [text[:5], text[5:11], text[11:]]
    with pytest.raises(ByteSpanMismatch) as e:
        byte_spans(mangle(_CHAPTER), "cp950", parts)
    assert expected in str(e.value)


def test_byte_spans_refuses_text_the_bytes_do_not_say():
    # The parser stopped partitioning its input, or a node value was edited.
    text = _CHAPTER.decode("cp950")
    with pytest.raises(ByteSpanMismatch) as e:
        byte_spans(_CHAPTER, "cp950", ["X" + text[1:]])
    assert "is not what the bytes at offset 0 decode to" in str(e.value)


def test_byte_spans_refuses_a_codec_with_no_boundary_where_it_was_asked():
    """A codec that emits several characters from one atom cannot answer.

    `utf-7` encodes a *run* of non-ASCII into one base64 group, so a boundary
    inside that run sits between two characters that share their bytes — there
    is no byte offset to return, and returning a plausible one is exactly what
    this function must never do. It is refused with a sentence naming the codec.

    Measured 2026-09-10, and the measurement is why the fixture is three
    characters rather than one: `"héllo wörld"` puts each accented letter in a
    group of its own, so every cut in it *is* a byte boundary and answers
    normally. It takes `+AOkA6QDp-` — one group, three characters — to reach the
    refusal, and cuts 1 and 2 reach it while 0 and 3 do not.

    Not in any candidate list; reachable only by writing it into
    `formats.<name>.encodings` by hand, which is why this is a refusal rather
    than a reason to reject the design.
    """
    data = "ééé".encode("utf-7")
    assert data == b"+AOkA6QDp-", "the premise: one base64 group, three characters"
    for cut in (1, 2):
        with pytest.raises(ByteSpanMismatch) as e:
            byte_spans(data, "utf-7", ["é" * cut, "é" * (3 - cut)])
        assert "no byte boundary" in str(e.value)
    # The edges of the group are boundaries and are answered, not refused.
    assert byte_spans(data, "utf-7", ["ééé"]) == [(0, len(data))]


#: One codec per row with text that codec can actually hold — `cp1252` has no 中
#: and `cp950`/`shift_jis` have no é, so a single shared string would have been a
#: test of the encoder rather than of `byte_spans`. The wide rows carry an astral
#: character on purpose: it is a surrogate *pair* in UTF-16 and one code point in
#: Python, which is precisely where a length-driven offset goes wrong.
_CUT_CASES = [
    ("utf-8", "aé中\U0001F600z"),
    ("utf-16-le", "aé中\U0001F600z"),
    ("utf-16-be", "aé中\U0001F600z"),
    ("utf-32-le", "aé中\U0001F600z"),
    ("cp950", "第一章 ╭═╮ 十"),
    ("gbk", "第一章 十年"),
    ("shift_jis", "第一章　風の音"),
    ("cp1252", "café — naïve"),
]


@pytest.mark.parametrize("encoding,text", _CUT_CASES, ids=[c[0] for c in _CUT_CASES])
def test_byte_spans_answers_at_every_cut_for_every_candidate_shaped_codec(
        encoding, text):
    """Every boundary, not one: the loop is where an off-by-one would hide."""
    data = text.encode(encoding)
    for cut in range(len(text) + 1):
        spans = byte_spans(data, encoding, [text[:cut], text[cut:]])
        assert b"".join(data[a:b] for a, b in spans) == data, cut
        assert data[spans[0][0]:spans[0][1]].decode(encoding) == text[:cut], cut


def test_byte_spans_gives_a_crlf_documents_terminator_to_the_part_that_holds_its_lf():
    """The reconciliation `split_terminator` forces, stated as a property.

    `split_terminator` deletes one CR per line *before* the parser sees the
    text, so the parser's characters are fewer than the file's bytes and every
    byte answer has to put those CRs back. A part's expected source spelling is
    the exact inverse of what was done to it, so the part that opens with the LF
    claims the CR and no part can end on a lone one.

    Asserted here rather than only through the corpus because it is the one
    place a plausible wrong answer is still a *contiguous cover of the file* —
    a join-level check cannot see it, so it is checked directly.
    """
    data = b"Chapter One\r\n\r\nIt was cold.\r\n"
    text, eol = split_terminator(data.decode("utf-8"))
    assert eol == "\r\n" and "\r" not in text
    parts = ["Chapter One", "\n\n", "It was cold.", "\n"]
    assert "".join(parts) == text
    spans = byte_spans(data, "utf-8", parts, eol)
    assert b"".join(data[a:b] for a, b in spans) == data
    assert not any(data[a:b].endswith(b"\r") for a, b in spans)
    assert not any(data[a:b].startswith(b"\n") for a, b in spans)
