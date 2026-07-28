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
    apply_terminator,
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
    assert read_document(path) == path.read_bytes().decode("utf-8")


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
    raw = read_document(CORPUS / "crlf-line-endings.md")
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
    crlf = read_document(CORPUS / "crlf-line-endings.md")
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
    raw = read_document(CORPUS / "crlf-mixed-terminators.md")
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
