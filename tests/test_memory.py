"""The translation-memory key, and what a reuse is allowed to write.

`test_pipeline.py` owns the round trip and the validators; `test_cli.py` owns what
the commands print. This file owns the other half of invariant 2: what identifies
a translation, and what has to be true before wording written for another moment
is allowed into a segment.

Everything here runs on the library functions rather than through a subprocess.
The end-to-end commands are covered in `test_cli.py`, and the properties asserted
here are about identity, which a process boundary only makes slower to read.
"""

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.cli import do_apply, do_extract, do_render  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG, DEFAULT_TONE  # noqa: E402
from scriptorium.mdparse import parse  # noqa: E402
from scriptorium.store import (  # noqa: E402
    SEGMENTATION_VERSION,
    append_tm,
    load_doc,
    load_tm,
    record_key,
    save_doc,
    seg_hash,
    segment_key,
    tm_key,
    tm_lookup,
    tm_record,
    tm_records,
)
from scriptorium.translate import accept  # noqa: E402

CFG = dict(DEFAULT_CONFIG)

#: One sentence carrying two do-not-translate candidates, so changing the list
#: changes how many placeholders the segment has while leaving its key alone.
DNT_DOC = b"Celurion and Acme ship together.\n"


def _project(tmp_path, monkeypatch, dnt="", doc=None, name="d.md"):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text(dnt, encoding="utf-8")
    if doc is not None:
        (tmp_path / name).write_bytes(doc)
    return name


def _only(doc):
    assert len(doc["segments"]) == 1, [s["source"] for s in doc["segments"]]
    return doc["segments"][0]


# --- the key itself ----------------------------------------------------------


def test_variant_null_is_the_same_key_as_no_variant_at_all():
    """The one requirement that invalidates the whole memory if it is wrong.

    Asserted directly rather than argued, because the argument — "``dict.get``
    returns ``None`` either way" — is only true while the key stays a tuple of
    read fields. Anything that canonicalizes, hashes, or serializes on the way in
    can break it without breaking a single other test.
    """
    assert tm_key("abc123", "para") == tm_key("abc123", "para", variant=None)

    absent = {"hash": "abc123", "context": "para", "segmentation_version": 1}
    explicit = dict(absent, variant=None)
    assert record_key(absent) == record_key(explicit)

    seg = {"hash": "abc123", "context": "para"}
    assert segment_key(seg) == segment_key(dict(seg, variant=None))


def test_a_null_field_read_from_another_writer_is_its_absence_everywhere():
    """The same collapse for the other two nullable fields, so the rule is one
    rule. `tm_record` never writes a null, but the memory is append-only text and
    another tool may."""
    bare = {"hash": "abc123", "target": "x"}
    nulled = {"hash": "abc123", "context": None, "segmentation_version": None,
              "variant": None, "tone": None, "target": "x"}
    assert record_key(bare) == record_key(nulled)

    # `tone` extends the rule by one step, because its null is a string the
    # caller is holding rather than an absence: the default register has to
    # compare equal to the field being missing, or every entry banked before
    # registers existed stops answering at once.
    assert record_key(dict(bare, tone=DEFAULT_TONE)) == record_key(bare)


def test_context_separates_one_sentence_in_two_blocks():
    """The measured HANDOFF-006 collision: same text, same hash, two blocks.

    A paragraph translation may wrap across lines; carried onto a blockquote by a
    hit on content alone, the second line lands outside the quote. The content
    hash cannot see the difference, which is what the context axis is for.
    """
    _nodes, segs = parse("A shared sentence.\n\n> A shared sentence.\n", [])
    para, quote = segs
    assert para["hash"] == quote["hash"] == seg_hash("A shared sentence.")
    assert (para["context"], quote["context"]) == ("para", "quote")
    assert segment_key(para) != segment_key(quote)


def test_a_segmentation_bump_makes_older_entries_miss_rather_than_answer():
    """The field prevents nothing — it makes the invalidation detectable."""
    assert tm_key("abc123", "para", 1) != tm_key("abc123", "para", 2)
    assert record_key({"hash": "abc123", "context": "para"}) == tm_key("abc123", "para", 0)


def test_a_record_carries_the_key_fields_and_never_a_null():
    seg = {"hash": "abc123", "context": "para", "variant": None,
           "source": "one", "target": "一"}
    rec = tm_record(seg)
    assert rec == {"hash": "abc123", "context": "para",
                   "segmentation_version": SEGMENTATION_VERSION,
                   "source": "one", "target": "一"}
    assert record_key(rec) == segment_key(seg)


# --- the register axis -------------------------------------------------------
#
# `tone` is per-*document*, and the memory file is per-project, so two registers
# inside one project used to cost nothing and fail silently: a paragraph
# translated as documentation was served verbatim to a novel. See
# `docs/decisions.md`, 2026-07-29, D4.

#: One English sentence with an obvious answer in each register. The
#: documentation one is not wrong Chinese — it is wrong *for a novel*, which is
#: the failure no validator can see (invariant 4).
LEAVING = b"He left without a word.\n"
AS_DOCUMENTATION = "他未發一語即行離開。"
AS_PROSE = "他一句話也沒說就走了。"


def test_a_record_carries_the_register_only_when_it_is_not_the_default():
    seg = {"hash": "abc123", "context": "para", "source": "one", "target": "一"}
    assert tm_record(seg, "literary")["tone"] == "literary"
    assert record_key(tm_record(seg, "literary")) == segment_key(seg, "literary")

    # A documentation project's memory file is the file it was before registers
    # existed, byte for byte — which is what "no whole-memory invalidation"
    # means at the writing end.
    assert "tone" not in tm_record(seg, DEFAULT_TONE)
    assert tm_record(seg, DEFAULT_TONE) == tm_record(seg) == tm_record(seg, None)


@pytest.mark.parametrize("typed", ["Literary", " literary ", "LITERARY"])
def test_case_and_padding_do_not_split_a_register(typed):
    """The same normalization the brief selection uses, on the other side of the
    key: two spellings of one register would be two sets of banked wording, and
    nobody would ever find the split."""
    seg = {"hash": "abc123", "context": "para"}
    assert segment_key(seg, typed) == segment_key(seg, "literary")
    assert record_key({"hash": "abc123", "tone": typed}) == \
        record_key({"hash": "abc123", "tone": "literary"})


def test_tone_in_memory_key_keeps_two_registers_apart(tmp_path, monkeypatch):
    """One sentence, two documents, one memory file: two entries, and neither is
    served to the other.

    `tm_path` already holds a novels project apart from a documentation project,
    because it is relative to the working directory. The register is per-document,
    so that separation does nothing for two documents in one project — which is
    why the key had to grow the field rather than the path.
    """
    _project(tmp_path, monkeypatch, doc=LEAVING)
    (tmp_path / "novel.md").write_bytes(LEAVING)

    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_DOCUMENTATION})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    # The documentation wording is not offered to the novel at all: not as a hit
    # that `accept` then refuses — it never reaches `accept`, because the key
    # does not match.
    doc, reused, rejected = do_extract("novel.md", "zh-TW", CFG, tone="literary")
    assert (reused, rejected) == (0, 0)
    assert _only(doc)["status"] == "pending"

    do_apply("novel.md", "zh-TW", CFG, {_only(doc)["id"]: AS_PROSE})
    banked = tm_records(load_doc("novel.md", "zh-TW"), load_tm("zh-TW"))
    assert [r.get("tone") for r in banked] == ["literary"]
    append_tm("zh-TW", banked)

    tm = load_tm("zh-TW")
    assert len(tm) == 2
    _nodes, segs = parse(LEAVING.decode("utf-8"), [])
    assert tm_lookup(tm, segs[0]) == (AS_DOCUMENTATION, "tm")
    assert tm_lookup(tm, segs[0], "literary") == (AS_PROSE, "tm")


def test_the_register_is_resolved_from_the_document_and_nowhere_else(monkeypatch):
    """The prompt and the key must never disagree about which register this is.

    Measured before the fix: a state file with no `tone` and a config saying
    `literary` briefed the model as literary while `tm_records` keyed the result
    in the default register's tier — the silent cross-register overwrite this
    axis exists to prevent, arriving through a divergent fallback rather than
    through the key. `translate_segments` therefore reads the document only; the
    config decides the register once, at extract, and `do_extract` freezes it.
    """
    from scriptorium import translate

    doc = {"lang": "zh-TW",
           "segments": [{"hash": "h", "context": "para", "source": "a", "target": "b"}]}
    cfg = dict(DEFAULT_CONFIG, tone="literary")

    briefed = []
    monkeypatch.setattr(translate, "_system_prompt",
                        lambda _src, _tgt, tone, _mode: briefed.append(tone) or "")
    # No segments, so no request is built and no provider is contacted; the
    # system prompt is assembled before the batch loop, which is the one thing
    # under test.
    translate.translate_segments([], doc, cfg, provider_name="local")

    assert briefed == [DEFAULT_TONE]
    assert [r.get("tone") for r in tm_records(doc, {})] == [None]


def test_a_register_change_does_not_carry_the_old_wording_forward(tmp_path, monkeypatch):
    """The within-document half, which the memory key alone does not cover.

    Carryover is keyed on the *stored* register, so re-extracting into another one
    carries nothing. The segmentation version deliberately works the other way —
    this build's on both sides — and the difference is the whole reason: a changed
    segmentation changes the segment text, so the content hash discriminates on
    its own, while a changed register leaves the source byte-identical.

    The alternative loses nothing visible at extract and is far worse: the
    document then holds documentation wording while saying `tone: literary`, and
    `lx commit` banks all of it under the literary key.
    """
    _project(tmp_path, monkeypatch, doc=LEAVING)
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_DOCUMENTATION})

    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG, tone="literary")
    assert (reused, rejected) == (0, 0)
    assert (doc["tone"], _only(doc)["status"]) == ("literary", "pending")
    assert tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")) == []


def test_the_register_is_frozen_on_the_document_and_a_later_extract_keeps_it(
        tmp_path, monkeypatch):
    """A forgotten `--tone` must not return the document to the configured
    default. It was harmless while the register only reached the `Tone:` line;
    now it would take every carryover and every memory hit with it."""
    _project(tmp_path, monkeypatch, doc=LEAVING)
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG, tone="literary")
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_PROSE})

    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (doc["tone"], reused, rejected) == ("literary", 1, 0)
    assert _only(doc)["target"] == AS_PROSE

    # `--reset` is the exception, and deliberately so: it does not read the state
    # file at all, because it has to work on one this build cannot read.
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG, reset=True)
    assert doc["tone"] == DEFAULT_TONE


# --- what happens to a memory written before this key existed ----------------

#: The shape every entry banked before 2026-07-29 has: content hash, source,
#: target, and nothing that says where it came from or how it was cut.
LEGACY = {"hash": seg_hash("A shared sentence."), "source": "A shared sentence.",
          "target": "一句共用的句子。"}


def test_a_record_from_before_the_key_existed_is_still_reachable():
    """The stated policy for the unversioned tier: absent means version 0, and
    version 0 is accepted. It is safe because such a hit goes through `accept`
    like every other, and refusing them would empty a user's memory on upgrade in
    exchange for nothing."""
    tm = {record_key(LEGACY): LEGACY["target"]}
    _nodes, segs = parse("A shared sentence.\n", [])
    target, origin = tm_lookup(tm, segs[0])
    assert target == LEGACY["target"]
    assert origin == "tm:legacy"


def test_an_unversioned_hit_is_marked_so_a_reviewer_can_see_it():
    """`tm:legacy` and not `tm`: a match on content alone is precisely the
    context-blind reuse the key was changed to stop, so which segments still rest
    on it has to be visible rather than inferred."""
    tm = {record_key(LEGACY): LEGACY["target"]}
    _nodes, segs = parse("A shared sentence.\n\n> A shared sentence.\n", [])
    assert [tm_lookup(tm, s)[1] for s in segs] == ["tm:legacy", "tm:legacy"]

    versioned = dict(LEGACY, context="para", segmentation_version=SEGMENTATION_VERSION)
    tm = {record_key(versioned): versioned["target"]}
    assert [tm_lookup(tm, s)[1] for s in segs] == ["tm", None]


def test_legacy_tm_survives_tone_for_a_document_in_the_default_register(
        tmp_path, monkeypatch):
    """No whole-memory invalidation, in both tiers.

    The default register *is* the key's null, so a documentation document keys
    exactly as it did before the field existed. The alternative — key on the
    register always, and add a second register-blind lookup for the old entries —
    costs a lookup and lets a documentation-era wording be claimed by a novel,
    which is the one failure this axis was added to prevent.
    """
    _project(tmp_path, monkeypatch, doc=b"A shared sentence.\n")
    versioned = dict(LEGACY, context="para", segmentation_version=SEGMENTATION_VERSION)
    append_tm("zh-TW", [LEGACY, versioned])
    _nodes, segs = parse("A shared sentence.\n", [])

    # The fully-keyed tier, which carries no `tone` field because it predates one.
    tm = load_tm("zh-TW")
    assert tm_lookup(tm, segs[0]) == (LEGACY["target"], "tm")
    assert tm_lookup(tm, segs[0], DEFAULT_TONE) == (LEGACY["target"], "tm")

    # And the unversioned tier below it, reached only through the fallback.
    bare = {record_key(LEGACY): LEGACY["target"]}
    assert tm_lookup(bare, segs[0], DEFAULT_TONE) == (LEGACY["target"], "tm:legacy")

    # End to end, because that is where a whole-memory invalidation would show.
    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["target"] == LEGACY["target"]


def test_a_literary_document_is_not_offered_the_unversioned_tier():
    """The register's half of the rule `variant` already had, one step along.

    A pre-variant record cannot be *known* to be the right form. A pre-register
    record is stronger than that: it is documentation-register wording by
    construction, because the build that wrote it ended every zh-TW brief with
    "Write technical documentation register" whatever `tone` said.
    """
    tm = {record_key(LEGACY): LEGACY["target"]}
    _nodes, segs = parse("A shared sentence.\n", [])
    assert tm_lookup(tm, segs[0], "literary") == (None, None)
    assert tm_lookup(tm, segs[0], DEFAULT_TONE) == (LEGACY["target"], "tm:legacy")


def test_a_segment_with_a_variant_is_not_offered_a_pre_variant_record():
    """A record written before variants existed cannot be known to be the right
    form, and guessing is how a plural becomes a singular somewhere nobody looks.
    Constructed by hand: no format emits a variant yet, which is the point of
    adding the field before one does."""
    tm = {record_key(LEGACY): LEGACY["target"]}
    _nodes, segs = parse("A shared sentence.\n", [])
    assert tm_lookup(tm, dict(segs[0], variant="plural")) == (None, None)


def test_committing_upgrades_an_unversioned_record_instead_of_reusing_it_forever(
        tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, doc=b"A shared sentence.\n")
    append_tm("zh-TW", [LEGACY])

    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["origin"] == "tm:legacy"

    # First commit writes the full key beside the old line; the memory is
    # append-only, so the unversioned record stays where it is.
    banked = tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW"))
    assert [r["context"] for r in banked] == ["para"]
    append_tm("zh-TW", banked)

    # Second commit finds it and writes nothing — an upgrade, not a duplicate.
    assert tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")) == []

    # The memory now answers under the full key, so the next document to contain
    # this sentence gets a `tm` hit rather than another unversioned one. Asserted
    # on the lookup and not by re-extracting: this document's own state answers
    # first and carries the origin that produced the wording, which is the record
    # `origin` is for.
    _nodes, segs = parse("A shared sentence.\n", [])
    assert tm_lookup(load_tm("zh-TW"), segs[0])[1] == "tm"


def test_a_hand_damaged_line_is_skipped_rather_than_taking_the_memory_down(
        tmp_path, monkeypatch):
    """The file is append-only text and people edit it. One bad line costing every
    command that reads the memory is a poor trade for a diagnostic nobody asked
    for."""
    _project(tmp_path, monkeypatch)
    path = pathlib.Path(".lx") / "tm.zh-TW.jsonl"
    append_tm("zh-TW", [LEGACY])
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("{not json at all\n")
        f.write(json.dumps({"source": "no hash here"}) + "\n")
    assert list(load_tm("zh-TW").values()) == [LEGACY["target"]]


# --- reuse takes the same acceptance path as model output --------------------


def test_a_memory_hit_is_refused_when_the_mask_configuration_moved_under_it(
        tmp_path, monkeypatch):
    """Measured 2026-07-27, and the reason reuse is now gated.

    The key is deliberately blind to the do-not-translate list — so that the same
    wording banked on two machines with different lists is still one entry — which
    means editing that list changes a segment's placeholder count while its key
    stays put. Written straight to target, as reuse used to be, the extra ⟦2⟧ has
    no slot to restore from and reaches the rendered document verbatim.

    Asserted on the rendered bytes, not on the memory: the entry is supposed to
    survive. Refusing a hit is not the same as discarding one, and the last third
    of this test is that difference.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\nAcme\n", doc=DNT_DOC)
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    assert _only(doc)["masked"] == "⟦1⟧ and ⟦2⟧ ship together."
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 與 ⟦2⟧ 一同出貨。"})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    # Drop one term. The sentence is unchanged, so the key still matches — the
    # banked wording no longer does.
    (tmp_path / "config" / "dnt.txt").write_text("Celurion\n", encoding="utf-8")
    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1)
    assert _only(doc)["status"] == "pending"
    assert "⟦2⟧" in list(load_tm("zh-TW").values())[0]     # the entry is still there

    text, missing = do_render("d.md", "zh-TW", CFG, fallback=True)
    assert missing == 1
    assert "⟦" not in text
    assert text == DNT_DOC.decode("utf-8")

    # Put the term back and the same entry answers again, restored through both
    # slots. Nothing was lost by refusing it.
    (tmp_path / "config" / "dnt.txt").write_text("Celurion\nAcme\n", encoding="utf-8")
    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    text, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, text) == (0, "Celurion 與 Acme 一同出貨。\n")


def test_adding_a_term_refuses_the_hit_that_was_banked_without_it(tmp_path, monkeypatch):
    """The other edit to the same list, and the direction HANDOFF-007 wrote down.

    It is worth separating from the removal above, because the two fail
    differently and only one of them was ever visible in a rendered document. Drop
    a term and the banked target keeps a placeholder the new slot map cannot
    restore, so a bare ⟦2⟧ reaches the file. Add one and the target is short a
    placeholder instead: the reused wording renders as ordinary text with the new
    term left unprotected — wrong, reported by `check`, and impossible to see by
    reading the output. The gate refuses both, and this asserts the quiet one.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=DNT_DOC)
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    assert _only(doc)["masked"] == "⟦1⟧ and Acme ship together."
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 與 Acme 一同出貨。"})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    (tmp_path / "config" / "dnt.txt").write_text("Celurion\nAcme\n", encoding="utf-8")
    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1)

    text, missing = do_render("d.md", "zh-TW", CFG, fallback=True)
    assert (missing, text) == (1, DNT_DOC.decode("utf-8"))


def test_reuse_and_model_output_are_refused_by_the_same_gate(tmp_path, monkeypatch):
    """The acceptance path is one function, so a rejection reason that has nothing
    to do with placeholders must reject a memory hit too. An empty target is the
    cheapest one that is not the placeholder rule wearing a different hat."""
    seg = {"masked": "Plain text.", "source": "Plain text."}
    assert accept(seg, "   ", "zh-TW", CFG) == (None, "empty translation")

    _project(tmp_path, monkeypatch, doc=b"Plain text.\n")
    append_tm("zh-TW", [{"hash": seg_hash("Plain text."), "context": "para",
                         "segmentation_version": SEGMENTATION_VERSION,
                         "source": "Plain text.", "target": "   "}])
    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1)
    assert _only(doc)["status"] == "pending"


def test_the_memory_is_still_tried_when_this_document_holds_a_stale_target(
        tmp_path, monkeypatch):
    """Prior state and the memory are both proposals, and they can disagree.

    The old code took the document's own target whenever it had one and never
    looked further. Now a refused carryover falls through, so a good banked
    wording is not lost behind a stale one sitting in front of it.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=b"Celurion ships.\n")
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 出貨。"})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    # Damage only the document's copy, leaving the memory's intact.
    state = load_doc("d.md", "zh-TW")
    state["segments"][0]["target"] = "⟦1⟧ 與 ⟦2⟧ 出貨。"
    save_doc("d.md", "zh-TW", state)

    doc, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert (_only(doc)["origin"], _only(doc)["target"]) == ("tm", "⟦1⟧ 出貨。")


@pytest.mark.parametrize("field", ["context", "variant"])
def test_a_fresh_segment_carries_both_new_axes(field):
    """A state file is version 3 because of these two, so a parser that forgets one
    produces a file that reads as current and keys wrongly."""
    _nodes, segs = parse("A sentence.\n", [])
    assert field in segs[0]
