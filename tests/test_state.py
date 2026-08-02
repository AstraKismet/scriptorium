"""The state layer itself: what survives a write, and what a version refuses.

`test_memory.py` owns what identifies a translation; this file owns where one is
kept. The four properties here are the ones the move from `.lx/docs/*.json` to
SQLite was made for, and each of them was either impossible or untrue before it:

* a translation run is durable batch by batch, so an interrupted novel keeps what
  it had translated;
* raw skeleton bytes survive unchanged, including bytes no UTF-8 JSON file can
  hold at all;
* a segment's identity is three nullable columns and the null of each compares
  equal to its own absence;
* both version refusals still say what to do next.

Everything runs on the library functions. The commands are covered in
`test_cli.py`, and a process boundary would only make these slower to read.
"""

import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import statedb  # noqa: E402
from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import _translate, do_extract, pending_segments  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.store import (  # noqa: E402
    SEGMENTATION_VERSION,
    StateVersionError,
    load_doc,
    prior_doc,
    prior_targets,
    save_doc,
    save_targets,
    segment_key,
    tm_key,
)

CFG = dict(DEFAULT_CONFIG)


def _project(tmp_path, monkeypatch, doc=b"", name="d.md"):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / name).write_bytes(doc)
    return name


# --- resumability ------------------------------------------------------------


class _Interrupting:
    """A provider that answers a fixed number of batches and then is cut off.

    `KeyboardInterrupt` on purpose, and not an `Exception`: `run_batch` catches
    every `Exception` and falls back to a per-segment retry, which is what should
    happen to a provider error. Ctrl-C is the case this test is about, and it is
    the one that used to discard hours of model time.
    """

    def __init__(self, answer_batches):
        self.answer_batches = answer_batches
        self.seen = []

    def describe(self):
        return "stub"

    def complete(self, system, user):
        # Read off the request rather than closed over the segment list, so the
        # test sees what was actually asked for — which is the second half of
        # what it asserts.
        ids = [item["id"] for item in json.loads(user[user.find("["):])]
        if len(self.seen) >= self.answer_batches:
            raise KeyboardInterrupt("^C")
        self.seen.append(ids)
        return json.dumps({sid: "已翻譯。" for sid in ids}, ensure_ascii=False)


def _args(**over):
    return argparse.Namespace(**{"dry_run": False, "provider": None, "batch": 2,
                                 "concurrency": 1, **over})


def test_resume_after_interrupt_keeps_every_completed_batch(tmp_path, monkeypatch):
    """The severe defect this package exists for, on the smallest document that shows it.

    Before per-batch persistence there was no intermediate write at all: the
    whole segment list came back from `translate_segments` and only then reached
    `do_apply`. At two thousand segments and eighty requests, one Ctrl-C at 90%
    threw away every translated segment, because nothing had been written yet.
    """
    src = _project(tmp_path, monkeypatch,
                   b"\n\n".join(f"Sentence number {i}.".encode() for i in range(10)) + b"\n")
    do_extract(src, "zh-TW", CFG)
    doc = load_doc(src, "zh-TW")
    assert len(doc["segments"]) == 10

    stub = _Interrupting(answer_batches=2)
    monkeypatch.setattr(translate_mod, "build_provider", lambda name, cfg: stub)
    with pytest.raises(KeyboardInterrupt):
        _translate(src, "zh-TW", CFG, doc["segments"], "draft", _args())

    done = [s for s in load_doc(src, "zh-TW")["segments"] if s.get("target")]
    answered = [sid for batch in stub.seen for sid in batch]
    assert [s["id"] for s in done] == answered, "a committed batch was lost"
    assert len(done) == 4
    assert all(s["origin"] == "llm:draft" for s in done)

    # And the resumed run asks for the rest and only the rest.
    resumed = _Interrupting(answer_batches=99)
    monkeypatch.setattr(translate_mod, "build_provider", lambda name, cfg: resumed)
    pending = pending_segments(load_doc(src, "zh-TW"))
    assert len(pending) == 6
    _translate(src, "zh-TW", CFG, pending, "draft", _args())
    asked = {sid for batch in resumed.seen for sid in batch}
    assert asked.isdisjoint(answered), "a completed segment was translated twice"
    assert all(s.get("target") for s in load_doc(src, "zh-TW")["segments"])


def test_resume_after_interrupt_does_not_clobber_a_reviewers_edit(tmp_path, monkeypatch):
    """The other half of a narrow write: it touches its own rows and no others.

    A batch used to be recorded by reading the whole document, changing it in
    memory and writing all of it back — so a segment the workbench saved while
    the run was in flight was overwritten by the copy the run had loaded minutes
    earlier. `save_targets` never reads the document, which is what makes the
    interleaving safe rather than merely unlikely.
    """
    src = _project(tmp_path, monkeypatch, b"First one.\n\nSecond one.\n")
    do_extract(src, "zh-TW", CFG)
    ids = [s["id"] for s in load_doc(src, "zh-TW")["segments"]]

    save_targets(src, "zh-TW", {ids[1]: "審校者的字。"}, "human")
    save_targets(src, "zh-TW", {ids[0]: "第一句。"}, "llm:draft")

    by_id = {s["id"]: s for s in load_doc(src, "zh-TW")["segments"]}
    assert by_id[ids[1]]["target"] == "審校者的字。"
    assert by_id[ids[1]]["origin"] == "human"
    assert by_id[ids[0]]["origin"] == "llm:draft"


# --- bytes -------------------------------------------------------------------


#: A Big5 sequence — 中文 — followed by a byte that is invalid in UTF-8 and in
#: every other candidate encoding. Held as a literal rather than encoded from a
#: string, because the point is the bytes.
DAMAGED = b"\xa4\xa4\xa4\xe5\x80 tail\n"


def test_non_utf8_state_roundtrip(tmp_path, monkeypatch):
    """Invariant 2a's storage half, measured on the bytes it exists for.

    A raw node holding these bytes cannot be written to a UTF-8 JSON state file
    at all — `json.dumps` accepts the surrogate a `surrogateescape` decode
    produces and the file write then dies with `UnicodeEncodeError: surrogates
    not allowed`, so the failure is at the file boundary and no serializer option
    avoids it. SQLite returns a BLOB unchanged, which is what lets HANDOFF-208
    make plain text byte-exact without touching this layer again.

    Nothing writes bytes into a node yet. This asserts the storage can carry them
    the day something does — the whole reason that package can be a column type
    rather than a base64 scheme.
    """
    monkeypatch.chdir(tmp_path)
    doc = {"source": "novel.txt", "lang": "zh-TW", "format": "text",
           "encoding": "cp950", "eol": "\n",
           "nodes": [{"t": "raw", "v": DAMAGED}, {"t": "seg", "id": "s0001"},
                     {"t": "raw", "v": "plain text stays text\n"}],
           "segments": [{"id": "s0001", "kind": "para", "source": "x", "masked": "x",
                         "slots": {}, "status": "pending", "hash": "abc123",
                         "context": "para", "variant": None}]}
    save_doc("novel.txt", "zh-TW", doc)

    back = load_doc("novel.txt", "zh-TW")
    assert back["nodes"][0]["v"] == DAMAGED
    assert isinstance(back["nodes"][0]["v"], bytes)
    # A str node comes back a str: the column takes either, and the reader must
    # not turn one into the other on the way through.
    assert back["nodes"][2]["v"] == "plain text stays text\n"
    assert isinstance(back["nodes"][2]["v"], str)
    assert back["nodes"][1] == {"t": "seg", "id": "s0001"}

    # What the JSON state file would have done with the same document, asserted
    # rather than described — this is the measurement the package was built on.
    with pytest.raises(TypeError):
        json.dumps(doc["nodes"])


def test_non_utf8_state_roundtrip_through_the_commands(tmp_path, monkeypatch):
    """The same property on a real document: a cp950 novel with no UTF-8 in it.

    This one decodes cleanly, so it was never refused; what it pins is that the
    text survives the state layer unchanged and renders back to the same
    characters. The byte-level guarantee for a non-injective codec is
    HANDOFF-208's, and this test does not claim it.
    """
    from scriptorium.cli import do_render

    body = "第一章\n\n他走進了屋裡。\n\n十年後，一切都變了。\n"
    src = _project(tmp_path, monkeypatch, body.encode("cp950"), name="novel.txt")
    doc, _, _ = do_extract(src, "zh-TW", CFG)
    assert doc["encoding"] == "cp950"

    text, _ = do_render(src, "zh-TW", CFG, fallback=True)
    assert text == body


# --- identity ----------------------------------------------------------------


def test_segment_identity_is_three_columns_and_a_null_is_not_a_second_row(tmp_path, monkeypatch):
    """What `prior_targets` looks a segment up by, and where the nulls collapse.

    Two segments with the same content hash and different `context` are two
    entries, because one sentence appearing as a paragraph and as a blockquote is
    two translations — the collision the context axis was added to remove.

    The nulls are the hazard SQL introduces and Python does not. `variant` is
    nullable and must compare equal to its own *absence*; in a `WHERE variant = ?`
    it never would, and in a UNIQUE index two NULLs are distinct. So no
    comparison is made in SQL at all: the columns are read out and the key is
    built by `tm_key`, where absent and null have always been one value because
    the key is a tuple of read fields. There is deliberately no unique index on
    the identity either — a document may legitimately hold the same sentence
    twice, and uniqueness of a *memory entry* belongs to the memory file.
    """
    monkeypatch.chdir(tmp_path)
    same_hash = "deadbeef0001"
    doc = {
        "source": "d.md", "lang": "zh-TW", "tone": "technical",
        "nodes": [],
        "segments": [
            # Same text in two places, and a third whose `variant` key is absent
            # rather than null — the two spellings that must be one key.
            {"id": "s1", "kind": "para", "masked": "x", "slots": {}, "status": "translated",
             "hash": same_hash, "context": "para", "variant": None, "target": "段落"},
            {"id": "s2", "kind": "quote", "masked": "x", "slots": {}, "status": "translated",
             "hash": same_hash, "context": "quote", "variant": None, "target": "引言"},
            {"id": "s3", "kind": "para", "masked": "y", "slots": {}, "status": "translated",
             "hash": "deadbeef0002", "context": "para", "target": "無 variant 欄"},
        ],
    }
    save_doc("d.md", "zh-TW", doc)

    prior = prior_targets("d.md", "zh-TW")
    assert len(prior) == 3
    assert prior[tm_key(same_hash, "para", SEGMENTATION_VERSION)][0] == "段落"
    assert prior[tm_key(same_hash, "quote", SEGMENTATION_VERSION)][0] == "引言"

    # Absent and null are one key in both directions: the row was written from a
    # segment with no `variant` key, and it answers a lookup that spells it null.
    assert segment_key({"hash": "deadbeef0002", "context": "para"}) == \
        segment_key({"hash": "deadbeef0002", "context": "para", "variant": None})
    assert prior[segment_key({"hash": "deadbeef0002", "context": "para", "variant": None})][0] \
        == "無 variant 欄"

    # An untranslated segment is not a carryover candidate, so it is filtered in
    # SQL rather than in the loop that would otherwise have to load it.
    save_doc("d.md", "zh-TW", {**doc, "segments": [{**doc["segments"][0], "target": ""}]})
    assert prior_targets("d.md", "zh-TW") == {}


def test_segment_identity_carries_over_only_within_its_own_register(tmp_path, monkeypatch):
    """The register is the stored one, and extract looks the key up under the new one.

    A document re-extracted into another register carries nothing over. That is
    the intended result: the alternative keeps documentation wording in a
    document now labelled `literary`, and `lx commit` then banks all of it under
    the literary key.
    """
    src = _project(tmp_path, monkeypatch, b"He left without a word.\n")
    do_extract(src, "zh-TW", CFG)
    from scriptorium.cli import do_apply
    do_apply(src, "zh-TW", CFG, {load_doc(src, "zh-TW")["segments"][0]["id"]: "他一言不發地走了。"},
             origin="human")

    assert len(prior_targets(src, "zh-TW")) == 1
    _, reused, _ = do_extract(src, "zh-TW", CFG, tone="literary")
    assert reused == 0
    assert load_doc(src, "zh-TW")["segments"][0]["status"] == "pending"


# --- versions ----------------------------------------------------------------


def test_state_version_refuses_both_directions_and_names_the_way_out(tmp_path, monkeypatch):
    """The contract that had to survive the move, in both of its asymmetric halves.

    Newer: the state holds fields this build cannot represent and a save would
    replace them, so every reader stops — including `prior_doc`, which extract
    uses and which would otherwise downgrade the document with a green exit code.
    Older: only the readers that would misinterpret it refuse, because
    re-extracting is how it is migrated and `prior_doc` is what migrates it.
    """
    src = _project(tmp_path, monkeypatch, b"A sentence to extract.\n")
    do_extract(src, "zh-TW", CFG)

    statedb.set_state_version(tmp_path, 99)
    for reader in (load_doc, prior_doc):
        with pytest.raises(StateVersionError) as e:
            reader(src, "zh-TW")
        assert "--reset" in str(e.value)

    statedb.set_state_version(tmp_path, 1)
    with pytest.raises(StateVersionError) as e:
        load_doc(src, "zh-TW")
    assert f"lx extract {src} --lang zh-TW" in str(e.value)
    assert "do not pass --reset" in str(e.value), "the older direction keeps the translations"
    assert prior_doc(src, "zh-TW")["source"] == src
    assert prior_targets(src, "zh-TW") == {}


def test_state_version_of_the_schema_is_refused_before_a_document_is_named(tmp_path, monkeypatch):
    """The other version, which answers a different question and cannot be reset.

    A newer *schema* has columns this build has no statement for, so the refusal
    happens at the connection — before any document has been named, which is why
    it cannot say `--reset` on one. It names the file and says the state is
    rebuildable, which is the true way out.
    """
    src = _project(tmp_path, monkeypatch, b"A sentence to extract.\n")
    do_extract(src, "zh-TW", CFG)

    statedb.set_schema_version(tmp_path, 99)
    for call in (lambda: load_doc(src, "zh-TW"), lambda: prior_doc(src, "zh-TW"),
                 lambda: do_extract(src, "zh-TW", CFG)):
        with pytest.raises(StateVersionError) as e:
            call()
        assert "state.db" in str(e.value)
        assert "lx extract" in str(e.value)


def test_state_version_survives_the_file_being_reopened(tmp_path, monkeypatch):
    """A fresh database is stamped, and reopening it does not re-run the schema."""
    src = _project(tmp_path, monkeypatch, b"A sentence to extract.\n")
    do_extract(src, "zh-TW", CFG)
    assert statedb.documents(tmp_path)[0]["state_version"] == 3
    load_doc(src, "zh-TW")
    do_extract(src, "zh-TW", CFG)
    assert len(statedb.documents(tmp_path)) == 1


def test_state_for_a_document_that_was_never_extracted_names_the_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError) as e:
        load_doc("missing.md", "zh-TW")
    assert "lx extract missing.md --lang zh-TW" in str(e.value)

    # And with state from before the database, which is not migrated: it is
    # regenerable and gitignored, so the message says where state lives now.
    os.makedirs(os.path.join(".lx", "docs"))
    with open(os.path.join(".lx", "docs", "missing.md.zh-TW.json"), "w") as f:
        f.write("{}")
    with pytest.raises(FileNotFoundError) as e:
        load_doc("missing.md", "zh-TW")
    assert "state.db" in str(e.value)
