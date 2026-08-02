"""Plain text, and the format registry that landed with it. No network, no model.

`tests/corpus-text/` holds one input file per round-trip property and nothing
else — the same rule as `tests/corpus/`, and for the same reason: every file in
it is collected as a fixture, so the explanation lives here.

The properties covered, one file each: hard-wrapped prose separated by blank
lines; one paragraph per line; indent-marked paragraphs with no blank lines;
CRLF; terminators already mixed; no trailing newline; CR-only terminators; a
UTF-8 byte-order mark; UTF-16 with a mark in both byte orders; Windows Big5;
chapter headings beside the lines that must not be read as one; scene breaks and
bare numerals; a form feed used as a chapter separator; every character
`str.splitlines()` breaks on that `str.split("\\n")` does not; a trailing blank
line and nothing else; and the three degenerate inputs.

Three of them are load-bearing rather than decorative.

`big5-cp950.txt` contains 裏, which Python's `big5` codec rejects and `cp950`
accepts. Before the candidate list was measured on 2026-08-02 this file fell
through every double-byte candidate and was read by `cp1252` as Latin-1
gibberish — with a green round-trip, because cp1252 is byte-exact over it. That
is why `test_plaintext_big5_roundtrip_keeps_every_byte` asserts the *detected
encoding* as well as the bytes: the byte property alone cannot see this defect.

`bom-utf16-le.txt` and `bom-utf16-be.txt` are what separate a real byte-order
mark implementation from one that writes `utf-16`. Measured: encoding a string
that already begins U+FEFF with the bare `utf-16` codec emits two marks.

`trailing-blank-line-only.txt` is the file the weaker form of `auto` gets wrong.
"Does a blank line exist" is true of it, and would join its two one-line
paragraphs into a single segment.

Red line, inherited from `tests/corpus/`: a fixture is never edited to make a
test pass. If one fails, either the parser is wrong or the fixture is not valid
input — decide which, and say so in the commit.
"""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import formats  # noqa: E402
from scriptorium.checks import check_segment  # noqa: E402
from scriptorium.cli import do_extract, do_render  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG, TEXT_DEFAULTS  # noqa: E402
from scriptorium.docio import UndecodableDocument, decode_document, write_document  # noqa: E402
from scriptorium.textparse import describe, parse  # noqa: E402

CORPUS = pathlib.Path(__file__).parent / "corpus-text"
UNDECODABLE = pathlib.Path(__file__).parent / "corpus-text-undecodable"
CFG = dict(DEFAULT_CONFIG)
TEXT = formats.by_name("text")
OPTS = formats.options(TEXT, CFG)
ENCODINGS = formats.encodings(TEXT, CFG)


def _corpus_files():
    return sorted(p for p in CORPUS.iterdir() if p.is_file())


def _explain(name, expected, actual):
    i = next((k for k, (a, b) in enumerate(zip(expected, actual)) if a != b),
             min(len(expected), len(actual)))
    lo, hi = max(0, i - 60), i + 60
    return (f"{name} did not round-trip; first difference at byte {i}\n"
            f"  expected: {expected[lo:hi]!r}\n"
            f"  actual  : {actual[lo:hi]!r}\n"
            f"  lengths : expected {len(expected)}, actual {len(actual)}")


def _substituted(text, opts=None):
    """Put each segment's source back into the skeleton.

    Deliberately not through ``render()``, which also unmasks and normalizes: a
    failure there could be a masking defect rather than a skeleton defect, and
    this property is about the skeleton alone. Same discipline as
    ``tests/test_pipeline.py::identity_roundtrip``.
    """
    nodes, segs = parse(text, (), opts if opts is not None else OPTS)
    by_id = {s["id"]: s for s in segs}
    return "".join(n["v"] if n["t"] == "raw" else by_id[n["id"]]["source"] for n in nodes)


# ── the skeleton guarantee, on bytes (invariant 2a) ──────────────────────────

@pytest.mark.parametrize("path", [pytest.param(p, id=p.name) for p in _corpus_files()])
def test_plaintext_roundtrip_byte_for_byte(path):
    """Detect, decode, parse, substitute, re-encode — and get the file back.

    Bytes on both ends rather than text, because for plain text the encoding is
    part of the format: a parser that round-trips the characters and loses the
    codec has not preserved the file.
    """
    data = path.read_bytes()
    text, encoding = decode_document(data, ENCODINGS, name=path.name)
    got = _substituted(text).encode(encoding)
    assert got == data, _explain(path.name, data, got)


def test_corpus_text_is_present_and_holds_only_plain_text():
    # The quiet failure this guards: a renamed or deleted fixture takes its
    # coverage with it and nothing complains. The second half keeps a .md out of
    # here, where it would be parsed by the wrong parser and still pass.
    names = [p.name for p in _corpus_files()]
    assert names, "tests/corpus-text/ is empty"
    assert all(n.endswith(".txt") for n in names), f"non-.txt fixtures: {names}"


def test_markdown_corpus_holds_only_markdown():
    # The mirror of the rule above. A .txt dropped into tests/corpus/ is read as
    # UTF-8 and parsed by `mdparse`, so it passes that round-trip while
    # measuring the wrong parser entirely — and a Big5 one crashes the decode.
    md = pathlib.Path(__file__).parent / "corpus"
    stray = sorted(p.name for p in md.iterdir() if p.is_file() and p.suffix != ".md")
    assert not stray, f"tests/corpus/ is the Markdown corpus; found: {stray}"


# ── encoding detection ───────────────────────────────────────────────────────

def test_plaintext_big5_roundtrip_keeps_every_byte_and_names_the_codec():
    """The byte property alone cannot see the defect this fixture exists for.

    `cp1252` is byte-exact over Big5 — it maps 128 single bytes bijectively — so
    a run that mis-detects this file still round-trips. What changes is every
    segment's text, its hash, and therefore what `lx commit` banks. So the codec
    is asserted, not just the bytes.
    """
    data = (CORPUS / "big5-cp950.txt").read_bytes()
    text, encoding = decode_document(data, ENCODINGS, name="big5-cp950.txt")
    assert encoding == "cp950"
    assert "屋裏很冷" in text
    assert _substituted(text).encode(encoding) == data


#: The Big5 duplicate-encoding block: two source byte pairs for one character, so
#: cp950's decode is not injective and `text.encode("cp950")` cannot return the
#: bytes it came from. Measured 2026-08-02 by sweeping the whole double-byte
#: range; `gbk`, `shift_jis` and `cp1252` have no such pair. Written out rather
#: than re-swept at test time so that a change in Python's codec tables shows up
#: here as a failure with a name, instead of silently reshaping the sweep.
CP950_NOT_REVERSIBLE = {
    b"\xa2\xcc": ("十", b"\xa4Q"),        # U+5341, as a numeric run writes it
    b"\xa2\xce": ("卅", b"\xa4\xca"),     # U+5345
    b"\xf9\xe9": ("╞", b"\xa2\xa5"),      # U+255E, and the nine below are rules
    b"\xf9\xea": ("╪", b"\xa2\xa6"),      # U+256A
    b"\xf9\xeb": ("╡", b"\xa2\xa7"),      # U+2561
    b"\xf9\xf9": ("═", b"\xa2\xa4"),      # U+2550
    b"\xf9\xfa": ("╭", b"\xa2~"),         # U+256D
    b"\xf9\xfb": ("╮", b"\xa2\xa1"),      # U+256E
    b"\xf9\xfc": ("╰", b"\xa2\xa2"),      # U+2570
    b"\xf9\xfd": ("╯", b"\xa2\xa3"),      # U+256F
}


def test_cp950_decode_is_not_injective_and_the_characters_are_what_survives():
    """Invariant 2a's byte claim does not hold for cp950. Pin what does hold.

    `A2CC` is 十 as a numeric run writes it and `F9F9`-`F9FD` are the box-drawing
    characters a BBS-era Traditional Chinese `.txt` rules its chapters with, so
    this is the primary corpus, not a corner. What the reader gets is still
    right, because rendering encodes UTF-8 and never goes back through cp950 —
    the characters survive and the bytes would not. Byte-exactness here needs
    raw skeleton nodes held as bytes rather than as JSON text, which is
    scheduled, and this test is what will fail loudly when it lands.
    """
    for raw, (char, reencoded) in CP950_NOT_REVERSIBLE.items():
        assert raw.decode("cp950") == char
        assert char.encode("cp950") == reencoded != raw

    for enc in ("gbk", "shift_jis", "cp1252"):
        for raw in CP950_NOT_REVERSIBLE:
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            assert text.encode(enc) == raw, f"{enc} is not injective either"


def test_a_box_drawn_chapter_rule_reaches_the_reader_intact():
    """End to end for the characters above: cp950 in, correct UTF-8 out.

    Built from bytes, not from a Python string. Encoding `"╭═╮"` to cp950 emits
    the A2 spellings and round-trips perfectly, so a test written that way
    exercises everything except the case that matters — which is how a whole
    corpus of tests missed this.
    """
    data = ("第一章\n\n".encode("cp950")
            + b"\xf9\xfa" + b"\xf9\xf9" * 6 + b"\xf9\xfb"
            + "\n\n屋裏很冷。\n".encode("cp950"))
    text, encoding = decode_document(data, ENCODINGS, name="ruled.txt")
    assert encoding == "cp950"
    assert text == "第一章\n\n╭══════╮\n\n屋裏很冷。\n"

    # The reader's copy is right; the bytes are not the ones we read. Both halves
    # are asserted so that neither can drift without a failure.
    assert _substituted(text).encode("utf-8") == text.encode("utf-8")
    assert _substituted(text).encode("cp950") != data


def test_source_encoding_write_would_break_invariant_2a(tmp_path):
    """The guard on the one change that turns the above into real corruption.

    Nothing writes a document back in its source encoding today; `write_document`
    takes no encoding at all. If it ever grows one and honours a document's
    recorded `encoding`, a Big5 novel's chapter rules become different bytes on
    every save. Delete this test only together with the BLOB work that makes
    byte-exactness real — not to make a new feature's tests pass.
    """
    out = tmp_path / "ruled.txt"
    source = "╭══════╮\n"
    write_document(str(out), source)
    assert out.read_bytes() == source.encode("utf-8")

    # And the loss this is guarding against, stated in the direction it happens:
    # bytes -> text -> bytes, which is what reading a source file and writing it
    # back would do. Starting from a *string* instead round-trips fine, which is
    # why the defect hid: every test that begins with a Python literal misses it.
    original = b"\xf9\xfa" + b"\xf9\xf9" * 6 + b"\xf9\xfb\n"
    assert original.decode("cp950").encode("cp950") != original


# ── plausibility: decoding successfully is not decoding correctly ────────────

def test_a_short_traditional_chinese_file_is_not_eaten_by_shift_jis():
    """The defect the veto exists for, in the shape it actually arrives in.

    A per-chapter `.txt` or an epigraph is short enough that `shift_jis` accepts
    it, and it accepts it *byte-reversibly* — so the round-trip fixtures stay
    green while every segment's text and hash are wrong and get banked. Measured
    2026-08-02: 175 of 300 five-character slices before the veto, 0 after.
    """
    source = "「你來了。」她說。"
    data = source.encode("cp950")
    assert data.decode("shift_jis").encode("shift_jis") == data, (
        "the premise: shift_jis round-trips these bytes, so bytes cannot catch it")
    text, encoding = decode_document(data, ENCODINGS, name="chapter-01.txt")
    assert encoding == "cp950"
    assert text == source


def test_the_veto_costs_japanese_nothing():
    """Real Japanese writes kana full-width, which the veto does not look at."""
    for source in ("「来たのね。」", "第一章　風の音", "ラーメンを食べた"):
        data = source.encode("shift_jis")
        text, encoding = decode_document(data, ENCODINGS, name="ja.txt")
        assert (text, encoding) == (source, "shift_jis"), source


def test_a_quoted_halfwidth_katakana_string_does_not_veto_its_own_encoding():
    # Why the rule is a majority and not any occurrence.
    source = "彼は「ｱｲｳ」と書いた。"
    data = source.encode("shift_jis")
    assert decode_document(data, ENCODINGS, name="ja.txt") == (source, "shift_jis")


def test_the_veto_never_turns_a_readable_file_into_a_refusal():
    """A heuristic may reorder candidates; it may not become a gate.

    When every candidate looks like mojibake the veto has no opinion left and
    candidate order decides, exactly as it did before. Measured over 5000 random
    byte strings: none became undecodable that was not undecodable before.
    """
    mojibake_only = b"\xa1\xb1\xa1\xb2"  # decodes somewhere, plausibly nowhere
    text, encoding = decode_document(mojibake_only, ENCODINGS, name="junk.txt")
    assert encoding in ENCODINGS and text


@pytest.mark.parametrize("name,expected", [
    ("bom-utf8.txt", "utf-8"),
    ("bom-utf16-le.txt", "utf-16-le"),
    ("bom-utf16-be.txt", "utf-16-be"),
])
def test_a_byte_order_mark_decides_the_codec_and_survives_into_the_skeleton(name, expected):
    data = (CORPUS / name).read_bytes()
    text, encoding = decode_document(data, ENCODINGS, name=name)
    assert encoding == expected
    assert text.startswith("﻿")
    nodes, segs = parse(text, (), OPTS)
    # The mark is skeleton. If it reached a segment the model would be asked to
    # reproduce an invisible character, and the same paragraph would hash
    # differently depending on whether its file carried a mark.
    assert nodes[0] == {"t": "raw", "v": "﻿"}
    assert not any(s["source"].startswith("﻿") for s in segs)


def test_a_doubled_byte_order_mark_is_skeleton_too():
    """Not hypothetical: it is what Python's bare `utf-16` codec writes.

    Encoding a string that already begins U+FEFF with `utf-16` emits a second
    mark, so a file that has been through such a tool carries two. Taking only
    the first hands the second to the model inside the segment source and splits
    that paragraph's memory hash away from every other copy of it.
    """
    nodes, segs = parse("﻿﻿Chapter One\n\nBody.\n", (), OPTS)
    assert nodes[0] == {"t": "raw", "v": "﻿﻿"}
    assert not any("﻿" in s["source"] for s in segs)
    assert _substituted("﻿﻿Chapter One\n\nBody.\n") == "﻿﻿Chapter One\n\nBody.\n"


def test_the_recorded_paragraph_mode_is_the_one_the_skeleton_was_cut_with():
    """`describe` and `parse` have to answer the same question the same way.

    A byte-order mark is not whitespace, so a file beginning `\\ufeff\\n\\n` has a
    non-blank first line before the mark is taken out and a blank one after.
    Measured 2026-08-02: `describe` said `blank-line` for a document `parse` had
    cut one paragraph per line, so `doc["paragraph_mode"]` and the `lx extract`
    line both stated something the skeleton contradicted.
    """
    text = "﻿\n\nHer name was Ada.\nShe did not answer.\n"
    mode = describe(text, OPTS)["paragraph_mode"]
    hosts = {s["host"] for s in parse(text, (), OPTS)[1]}
    assert mode == "line"
    assert hosts == {"text-line"}, "the host is what the recorded mode has to match"


def test_a_mark_overrides_the_configured_candidates():
    # Otherwise a project that pins `encodings` to one codec makes every marked
    # file undecodable, and the mark is the one declaration the file itself makes.
    data = "﻿hello".encode("utf-16-le")
    assert decode_document(data, ("big5",))[1] == "utf-16-le"


def test_utf16_without_a_mark_is_refused_rather_than_read_as_nul_studded_utf8():
    # It decodes cleanly as UTF-8 — every second byte is NUL, which is legal
    # UTF-8 — so without the guard this is mangled silently rather than refused.
    data = "Hello world".encode("utf-16-le")
    with pytest.raises(UndecodableDocument) as e:
        decode_document(data, ENCODINGS, name="x.txt")
    assert "NUL" in str(e.value)


def test_plaintext_damaged_refused_without_a_replacement_character():
    """A file with bytes invalid in its own encoding is refused, never repaired.

    Substituting U+FFFD would change bytes the pipeline promises to preserve
    (invariant 2a) and the damage would be durable: the replacement is hashed
    and banked. Reading such a file needs raw skeleton nodes held as bytes rather
    than as JSON text, which is scheduled and not built — the message says so.
    """
    data = (UNDECODABLE / "truncated-big5.txt").read_bytes()
    with pytest.raises(UndecodableDocument) as e:
        decode_document(data, ENCODINGS, name="truncated-big5.txt")
    message = str(e.value)
    assert "�" not in message
    for enc in ENCODINGS:
        assert enc in message, f"the message should say {enc} was tried"
    assert "raw skeleton nodes as bytes" in message


def test_the_refusal_message_does_not_advise_a_setting_the_mark_overrides():
    # A mark, then a lone high surrogate: U+D800 is bytes 00 D8 little-endian.
    data = "﻿".encode("utf-16-le") + b"\x00\xd8"
    with pytest.raises(UndecodableDocument) as e:
        decode_document(data, ENCODINGS, name="x.txt")
    assert "byte-order mark declares" in str(e.value)
    assert "encodings" not in str(e.value)


def test_python_big5_would_have_sent_this_fixture_to_cp1252():
    """Why the candidate list names `cp950`. Measured, not asserted from memory.

    This is the defect the 2026-08-02 audit found: with `big5` in the list, an
    ordinary Windows Traditional Chinese novel containing 裏 fails every
    double-byte candidate and is read by `cp1252` as Latin-1 gibberish. If this
    test ever fails because `big5` now accepts the fixture, Python's codec has
    changed and the entry in `docs/decisions.md` should be corrected — not this.
    """
    data = (CORPUS / "big5-cp950.txt").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        data.decode("big5")
    assert decode_document(data, ("utf-8", "big5", "gbk", "shift_jis", "cp1252"))[1] == "cp1252"


# ── segmentation ─────────────────────────────────────────────────────────────

def test_plaintext_paragraph_segment_is_the_paragraph_not_the_line():
    """Decision D2, 2026-07-29: prose stays segmented at the paragraph."""
    text = (CORPUS / "wrapped-paragraphs.txt").read_bytes().decode("utf-8")
    _nodes, segs = parse(text, (), OPTS)
    bodies = [s for s in segs if s["kind"] == "para"]
    assert "\n" in bodies[0]["source"], "a hard-wrapped paragraph must be one segment"
    assert bodies[0]["source"].count("\n") == 2
    assert len(segs) == 4


def test_plaintext_paragraph_mode_auto_reads_the_document():
    for name, mode in [("wrapped-paragraphs.txt", "blank-line"),
                       ("one-paragraph-per-line.txt", "line"),
                       ("trailing-blank-line-only.txt", "line"),
                       ("blank-lines-only.txt", "line")]:
        text = (CORPUS / name).read_bytes().decode("utf-8")
        assert describe(text, OPTS) == {"paragraph_mode": mode}, name


def test_indent_mode_segments_the_shape_auto_will_not_guess():
    text = (CORPUS / "indented-paragraphs.txt").read_bytes().decode("utf-8")
    # auto reads it as one paragraph per line, which is wrong and is why the
    # mode is named rather than detected — the test that would detect it is also
    # true of a per-line file with one indented line in it.
    assert describe(text, OPTS)["paragraph_mode"] == "line"
    opts = dict(OPTS, paragraph_mode="indent")
    _nodes, segs = parse(text, (), opts)
    assert len(segs) == 2
    assert all("\n" in s["source"] for s in segs)
    assert _substituted(text, opts) == text


def test_a_blocks_first_line_indent_is_skeleton_and_its_continuations_are_not():
    text = "    Indented head\n    continued here.\n"
    # Explicitly blank-line mode: this two-line file has no blank line in it, so
    # `auto` would read it as one paragraph per line and there would be no
    # continuation to test.
    nodes, segs = parse(text, (), dict(OPTS, paragraph_mode="blank-line"))
    assert nodes[0] == {"t": "raw", "v": "    "}
    # The continuation keeps its indent, because a raw node cannot sit in the
    # middle of a segment — the same shape as a wrapped list item in `mdparse`,
    # and the reasoning is in `docs/decisions.md`, 2026-07-28.
    assert segs[0]["source"] == "Indented head\n    continued here."


def test_blocks_with_nothing_to_translate_stay_in_the_skeleton():
    text = (CORPUS / "scene-breaks.txt").read_bytes().decode("utf-8")
    _nodes, segs = parse(text, (), OPTS)
    sources = [s["source"] for s in segs]
    assert sources == ["She left.", "He arrived."]


def test_chapter_headings_are_detected_and_ordinary_prose_is_not():
    text = (CORPUS / "chapter-headings.txt").read_bytes().decode("utf-8")
    _nodes, segs = parse(text, (), OPTS)
    got = {s["source"]: s["kind"] for s in segs}
    assert got == {
        "Prologue": "heading",
        "Chapter 1": "heading",
        "Chapter Two: The Mill": "heading",
        "Book Three": "heading",
        # Both of these open with a keyword and are ordinary narration. The first
        # is why `part` requires a number after it; the second is why the
        # keywords are anchored at the start of the line.
        "Part of her wanted to run.": "para",
        "The chapter closed with a death.": "para",
    }


def test_a_form_feed_separates_blocks_like_any_other_blank_line():
    text = (CORPUS / "form-feed-chapters.txt").read_bytes().decode("utf-8")
    _nodes, segs = parse(text, (), OPTS)
    assert [s["kind"] for s in segs] == ["heading", "para", "heading", "para"]


def test_context_is_the_kind_and_host_records_the_paragraph_shape():
    wrapped = (CORPUS / "wrapped-paragraphs.txt").read_bytes().decode("utf-8")
    perline = (CORPUS / "one-paragraph-per-line.txt").read_bytes().decode("utf-8")
    for text, host in ((wrapped, "text"), (perline, "text-line")):
        _nodes, segs = parse(text, (), OPTS)
        assert all(s["context"] == s["kind"] for s in segs)
        assert all(s["host"] == host for s in segs)


def test_one_wording_is_one_memory_entry_across_the_two_paragraph_shapes():
    """The reason the shape is recorded on `host` and not on `kind`.

    `kind` becomes `context`, which is in the translation-memory key, so a second
    kind for line mode would stop a one-line paragraph banked from a wrapped
    document answering for a one-per-line one — for no gain, since the wording is
    the same wording.
    """
    line = "He said nothing at all.\n"
    a = parse(line, (), dict(OPTS, paragraph_mode="line"))[1][0]
    b = parse(line, (), dict(OPTS, paragraph_mode="blank-line"))[1][0]
    assert (a["hash"], a["context"]) == (b["hash"], b["context"])
    assert a["host"] != b["host"]


# ── containment, for the plain-text hosts (invariant 2b) ─────────────────────

def _issues(seg, target):
    seg = dict(seg, target=target)
    return {i["rule"] for i in check_segment(seg, "zh-TW", CFG, [], [])}


def _first_para(name, opts=None):
    text = (CORPUS / name).read_bytes().decode("utf-8")
    segs = parse(text, (), opts if opts is not None else OPTS)[1]
    return next(s for s in segs if s["kind"] == "para")


def test_a_rewrapped_target_is_not_a_containment_error_in_a_wrapped_document():
    """The false-positive direction, which is the one that fails correct work.

    A blank-line-mode paragraph is a maximal run of non-blank lines, so a target
    that wraps to a different number of lines re-parses to the same one block.
    Comparing line counts here is the rule `docs/decisions.md` recorded as lost
    on 2026-07-28, and invariant 4 excludes it.
    """
    seg = _first_para("wrapped-paragraphs.txt")
    assert "containment" not in _issues(seg, "一行。\n兩行。\n三行。\n四行。")
    assert "containment" not in _issues(seg, "全部併成一行。")


def test_a_blank_line_in_the_target_still_ends_the_plain_text_paragraph():
    seg = _first_para("wrapped-paragraphs.txt")
    assert "containment" in _issues(seg, "前半。\n\n後半。")


def test_an_added_line_is_an_error_where_a_line_is_a_paragraph():
    seg = _first_para("one-paragraph-per-line.txt", OPTS)
    assert "containment" not in _issues(seg, "這是一整行的譯文。")
    assert "containment" in _issues(seg, "第一段。\n第二段。")


def test_a_chapter_title_may_not_grow_a_line():
    text = (CORPUS / "wrapped-paragraphs.txt").read_bytes().decode("utf-8")
    seg = next(s for s in parse(text, (), OPTS)[1] if s["kind"] == "heading")
    assert "containment" not in _issues(seg, "第一章")
    assert "containment" in _issues(seg, "第一章\n續行")


def test_no_line_of_plain_text_opens_a_block():
    """A dash at the start of a line is dialogue, not a list item.

    The Markdown block-start table applied to plain text would fail correct work
    on the commonest punctuation in a novel, which is why the text profile's
    table is empty rather than inherited.
    """
    seg = _first_para("wrapped-paragraphs.txt")
    for target in ("- 他說。\n- 她沒有回答。", "# 一號。\n> 引述。", "1. 第一。\n2. 第二。"):
        assert "containment" not in _issues(seg, target), target


@pytest.mark.parametrize("path", [pytest.param(p, id=p.name) for p in _corpus_files()])
def test_every_plaintext_segment_translated_to_itself_is_structurally_clean(path):
    """The must-not-fire half of invariant 2b, on real files rather than toys.

    A segment translated to itself changes no structure by definition, so any
    containment, escaping or `eol` issue here is the validator failing correct
    work. Mirrors `tests/test_pipeline.py`'s sweep over the Markdown corpus.
    """
    data = path.read_bytes()
    text, _enc = decode_document(data, ENCODINGS, name=path.name)
    for seg in parse(text, (), OPTS)[1]:
        seg["target"] = seg["masked"]
        found = [f"{i['rule']}: {i['message']}"
                 for i in check_segment(seg, "zh-TW", CFG, [], [])
                 if i["rule"] in {"containment", "escaping", "eol", "tags"}]
        assert not found, f"{seg['id']} ({seg['kind']}) {seg['masked']!r}: {found}"


# ── the whole CLI path, which the skeleton property deliberately skips ───────

@pytest.mark.parametrize("path", [pytest.param(p, id=p.name) for p in _corpus_files()])
def test_a_plaintext_document_survives_extract_render_and_write(tmp_path, monkeypatch, path):
    """The property on the real CLI path: registry lookup, `.lx/` state, render.

    Asserted against the source's *characters* encoded as UTF-8, not against its
    bytes, because that is exactly the decision this package took: a rendered
    document is always UTF-8 whatever the source was. A Big5 novel translated
    into zh-TW cannot be written back as Big5 in general — a target character
    outside the codepage would raise at write time, on a file the user has
    already paid a model to produce — and `docio.write_document` has encoded
    UTF-8 since it existed. `doc["encoding"]` records what the source was so the
    decision is reversible later; nothing reads it back today.

    `fallback=True` with nothing translated makes `render` take the fallback
    branch for every segment, which does not call `polish`, so this measures the
    I/O and registry layers with no deliberate change mixed in.
    """
    raw = path.read_bytes()
    text, _enc = decode_document(raw, ENCODINGS, name=path.name)
    monkeypatch.chdir(tmp_path)
    src = tmp_path / path.name
    src.write_bytes(raw)

    doc, _reused, _rejected = do_extract(str(src), "zh-TW", CFG)
    assert doc["format"] == "text"
    rendered, _missing = do_render(str(src), "zh-TW", CFG, fallback=True)

    out = tmp_path / "out" / path.name
    write_document(str(out), rendered)
    want = text.encode("utf-8")
    assert out.read_bytes() == want, _explain(path.name, want, out.read_bytes())


@pytest.mark.parametrize("name,expect_crlf", [
    ("crlf.txt", True), ("mixed-terminators.txt", False), ("wrapped-paragraphs.txt", False),
])
def test_plaintext_eol_is_a_document_level_fact(tmp_path, monkeypatch, name, expect_crlf):
    """A CRLF document renders CRLF; a mixed one passes through verbatim.

    The terminator is held in `doc["eol"]` and re-imposed once at render, never
    carried inside a segment — the rule `mdparse` follows, and plain text gets it
    for free because `split_terminator` runs before the parser is chosen. The
    mixed case is the recorded exception (`docs/decisions.md`, 2026-07-28): there
    is no single terminator to re-impose, so the CRs stay where they were.
    """
    raw = (CORPUS / name).read_bytes()
    monkeypatch.chdir(tmp_path)
    src = tmp_path / name
    src.write_bytes(raw)

    doc, _reused, _rejected = do_extract(str(src), "zh-TW", CFG)
    assert doc["eol"] == ("\r\n" if expect_crlf else "\n")
    if expect_crlf:
        assert not any("\r" in s["source"] for s in doc["segments"]), \
            "a uniform CRLF document must hand the model no carriage return"

    rendered, _missing = do_render(str(src), "zh-TW", CFG, fallback=True)
    out = tmp_path / "out" / name
    write_document(str(out), rendered)
    assert out.read_bytes() == raw


def test_an_unreadable_source_stops_the_command_rather_than_mangling_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "damaged.txt"
    src.write_bytes((UNDECODABLE / "truncated-big5.txt").read_bytes())
    with pytest.raises(UndecodableDocument):
        do_extract(str(src), "zh-TW", CFG)
    assert not (tmp_path / ".lx").exists(), "nothing may be written for a refused source"


def test_extract_refuses_a_format_it_has_no_parser_for(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "novel.epub"
    src.write_bytes(b"PK\x03\x04")
    with pytest.raises(formats.UnknownFormat):
        do_extract(str(src), "zh-TW", CFG)


# ── the registry ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,name", [
    ("docs/guide.md", "markdown"), ("README.MARKDOWN", "markdown"),
    ("book.txt", "text"), ("BOOK.TXT", "text"), ("a/b/c.text", "text"),
])
def test_the_registry_reads_the_extension(path, name):
    assert formats.name_for_path(path, CFG) == name


def test_an_unknown_extension_is_refused_with_the_line_that_fixes_it():
    # Before the registry every path went through `mdparse` whatever it was
    # called. That is harmless for a .rst and silent ruin for anything binary.
    with pytest.raises(formats.UnknownFormat) as e:
        formats.name_for_path("novel.epub", CFG)
    assert "formats" in str(e.value) and ".epub" in str(e.value)


def test_a_file_with_no_extension_says_so_rather_than_listing_none():
    with pytest.raises(formats.UnknownFormat) as e:
        formats.name_for_path("LICENSE", CFG)
    assert "no extension" in str(e.value)


@pytest.mark.parametrize("key", [".nfo", "nfo", ".NFO"])
def test_config_maps_an_extension_and_the_dot_is_optional(key):
    cfg = {**CFG, "formats": {**CFG["formats"], "map": {key: "text"}}}
    assert formats.name_for_path("readme.nfo", cfg) == "text"


def test_a_config_map_pointing_at_no_parser_names_what_exists():
    cfg = {**CFG, "formats": {**CFG["formats"], "map": {".nfo": "docx"}}}
    with pytest.raises(formats.UnknownFormat) as e:
        formats.name_for_path("readme.nfo", cfg)
    assert "markdown" in str(e.value) and "text" in str(e.value)


def test_a_state_file_from_before_the_registry_reads_as_markdown():
    # There was only one parser then, so an absent key is not ambiguous — which
    # is the whole argument against a STATE_VERSION bump for this field.
    assert formats.for_doc({"segments": [], "nodes": []}).name == "markdown"
    assert formats.for_doc({"format": None}).name == "markdown"


def test_markdown_still_asks_for_utf8_alone():
    # Widening Markdown's candidate list would be a behaviour change nobody asked
    # for, and it is a format people write in an editor that already decided.
    assert formats.encodings(formats.by_name("markdown"), CFG) == ("utf-8",)


def test_the_text_defaults_are_one_literal_shared_with_the_scaffolded_config():
    # `lx init` writes DEFAULT_CONFIG verbatim, and `textparse` reads
    # TEXT_DEFAULTS as its own fallbacks. Two copies would drift silently.
    assert CFG["formats"]["text"] == TEXT_DEFAULTS
    assert formats.options(TEXT, {}) == TEXT_DEFAULTS


def test_the_untranslated_marker_is_the_format_s_own():
    assert "<!--" in formats.by_name("markdown").marker
    assert "<!--" not in TEXT.marker
