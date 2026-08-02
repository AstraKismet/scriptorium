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
from scriptorium.normalize import normalize  # noqa: E402
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
    monkeypatch.setattr(
        translate, "_system_prompt",
        lambda _src, _tgt, tone, _mode, _ctx=False, _style="": briefed.append(tone) or "")
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


# --- what the memory used to launder on the way back in ----------------------

#: An indented code block, a tab-indented one, and prose either side. The four
#: spaces are what make the first block code, and until 2026-08-02 they were at
#: position 0 of a translatable segment.
CODE_DOC = (
    b"Introducing an indented code block.\n"
    b"\n"
    b"    def indented():\n"
    b"        return 'four spaces, not a fence'\n"
    b"\n"
    b"Closing paragraph.\n"
)


def test_an_indented_code_block_survives_a_state_rebuilt_from_the_memory(
        tmp_path, monkeypatch):
    """The cycle that reached disk without anything saying so.

    Reproduced 2026-08-02 before the fix: `lx apply` with the indent intact,
    `lx commit`, delete the state database, `lx extract` — which reported
    `reused=2 rejected=0` and handed the code segment back four spaces shorter,
    because reuse goes through `translate.accept` and `accept` strips a
    proposal's leading whitespace. The key is the source hash and is untouched by
    that, and `tm_records` only rewrites when the stored target differs from the
    banked one, so the two never converged. `lx check` exited 0 the whole way,
    and a real CommonMark render turns the shortened block from `<pre><code>`
    into a reflowed `<p>`: a document changes its rendered structure merely by
    having its state rebuilt.

    A unit test on `accept` cannot see any of that — the laundering needs the
    round trip. The assertion is on the rendered bytes rather than on the segment
    list, because bytes are what the defect changed.
    """
    _project(tmp_path, monkeypatch, doc=CODE_DOC)
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    assert [s["source"] for s in doc["segments"]] == [
        "Introducing an indented code block.", "Closing paragraph."]
    do_apply("d.md", "zh-TW", CFG,
             {s["id"]: f"第{k}段譯文。" for k, s in enumerate(doc["segments"])})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))
    first, missing = do_render("d.md", "zh-TW", CFG)
    assert missing == 0

    # Delete the working state and rebuild it from the memory alone. Nothing
    # about this step looks like an edit to the document, which is the point.
    for leftover in (tmp_path / ".lx").glob("state.db*"):
        leftover.unlink()
    _doc2, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (2, 0)
    second, missing = do_render("d.md", "zh-TW", CFG)

    assert (missing, second) == (0, first)
    assert "    def indented():\n        return 'four spaces, not a fence'" in second
    assert "\n\nClosing paragraph." not in second      # the prose *was* translated


#: The shape HANDOFF-018's fix does *not* reach, because it is not code: a list
#: item's second paragraph. `mdparse` keeps its four spaces at position 0 of the
#: segment on purpose — a raw node can only sit before or after a whole segment,
#: so an indent that follows a newline inside the source cannot be held by one —
#: and CommonMark reads them as what keeps the paragraph inside the item.
INDENTED_ITEM_DOC = (
    b"- item one\n"
    b"\n"
    b"    A second paragraph of the item.\n"
    b"\n"
    b"1. an ordered item\n"
    b"\n"
    b"   A second paragraph, indented three.\n"
)


def test_an_indent_a_segment_owns_survives_a_state_rebuilt_from_the_memory(
        tmp_path, monkeypatch):
    """The same laundering cycle as above, on the shape that is still a segment.

    HANDOFF-018 closed this for an indented code block by moving it into the
    skeleton, and recorded the premise that nothing else could arrive with an
    indent at position 0. Measured while closing it: false. The cycle is
    unchanged — `lx apply` with the indent intact, `lx commit`, delete the state
    database, `lx extract` — and reuse still goes through `translate.accept`, so
    before the fix the second paragraph came back four columns shorter and left
    the list item. `lx check` exited 0 at every step, both times.

    Asserted on the rendered bytes rather than on the segment list, because bytes
    are what the defect changed and what a reader would have had to notice.
    """
    _project(tmp_path, monkeypatch, doc=INDENTED_ITEM_DOC)
    doc, _reused, _rejected = do_extract("d.md", "zh-TW", CFG)
    indented = [s for s in doc["segments"] if s["source"][:1].isspace()]
    assert [s["source"] for s in indented] == [
        "    A second paragraph of the item.",
        "   A second paragraph, indented three.",
    ], [s["source"] for s in doc["segments"]]

    # A person's words, applied with the indent intact — what a reviewer who
    # copied the source line would produce. `do_apply` is the path that never
    # stripped, so this is the target that used to be banked whole and handed
    # back short. No digits in the wording: `pangu` spaces CJK against them, and
    # this test is about blanks at the ends rather than in the middle.
    words = ["首段譯文。", "次段譯文。", "第三段譯文。", "末段譯文。"]
    do_apply("d.md", "zh-TW", CFG,
             {s["id"]: (s["source"][: len(s["source"]) - len(s["source"].lstrip())]
                        + words[k])
              for k, s in enumerate(doc["segments"])})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))
    first, missing = do_render("d.md", "zh-TW", CFG)
    assert missing == 0
    assert "\n\n    次段譯文。\n" in first
    assert "\n\n   末段譯文。\n" in first

    for leftover in (tmp_path / ".lx").glob("state.db*"):
        leftover.unlink()
    _doc2, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (4, 0)
    second, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, second) == (0, first)


def test_a_memory_entry_banked_without_the_indent_gets_it_back_on_reuse(
        tmp_path, monkeypatch):
    """The other half of the laundering, and the half a build cannot avoid.

    Every entry banked before this fix holds the *stripped* wording, because
    `accept` is what wrote it. The key is the source hash — which includes the
    indent, since the indent is part of the source — so those entries still
    match, and matching is what makes them dangerous: a short target reused into
    an indented segment is how the defect would have kept arriving after the
    repair. `accept` decides the runs from the segment in front of it rather than
    from the entry, so the entry is reseated instead of trusted.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    doc, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))

    # Written straight to the memory, bypassing `do_apply` on purpose: this is an
    # entry an older build wrote, and no current path can produce one.
    banked = dict(seg, target="這是第二段。", origin="human")
    append_tm("zh-TW", [tm_record(banked, DEFAULT_TONE)])

    for leftover in (tmp_path / ".lx").glob("state.db*"):
        leftover.unlink()
    doc2, reused, rejected = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert next(s["target"] for s in doc2["segments"] if s["id"] == seg["id"]) \
        == "    這是第二段。"
    out, missing = do_render("d.md", "zh-TW", CFG)
    assert missing == 1                                  # `item one` is untranslated
    assert "\n\n    這是第二段。\n" in out


@pytest.mark.parametrize("applied", [
    "這是第二段。",              # the indent dropped, which a textarea invites
    "    這是第二段。",          # reproduced
    "\n  這是第二段。  \n",      # an agent's padding instead
])
def test_apply_seats_a_person_s_words_in_the_indent_the_same_way_accept_does(
        tmp_path, monkeypatch, applied):
    """The half of the `do_apply` / `accept` asymmetry that had to close.

    HANDOFF-018 kept that asymmetry deliberately and for a different reason — a
    person's or an agent's words are reported at `lx check`, never refused at the
    door (`docs/decisions.md`, 2026-07-29) — and this does not touch that half:
    nothing here can return `None`. What it closes is a document rendering
    differently depending on which of the three equal sources produced its
    target, over a run of blanks that belongs to the host syntax rather than to
    the translator.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    doc, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    applied_n, unknown = do_apply("d.md", "zh-TW", CFG, {seg["id"]: applied})
    assert (applied_n, unknown) == (1, [])
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert stored["target"] == "    這是第二段。"


@pytest.mark.parametrize("blank", ["", "   ", "\n", "　"])
def test_apply_clearing_a_segment_does_not_leave_an_indent_standing_alone(
        tmp_path, monkeypatch, blank):
    """The reachable half of `reseat_outer_blanks`'s blank-text guard.

    `accept` never reaches it — it refuses an empty proposal before reseating —
    but `lx apply` and the workbench's save endpoint do, and clearing a segment
    is what a reviewer does when a paragraph needs redoing. Reseated, a cleared
    target becomes `"    "`, which is *truthy*: `render` would emit four spaces
    where the untranslated marker belongs and report nothing missing. Found by
    the mutation pass; nothing else in the suite could see the guard.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    doc, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    do_apply("d.md", "zh-TW", CFG, {seg["id"]: blank})
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    # What `normalize` made of it and nothing more — not the four spaces in front.
    assert stored["target"] == normalize(blank, "zh-TW", CFG)


def test_apply_clearing_a_segment_still_renders_the_untranslated_marker(
        tmp_path, monkeypatch):
    """The consequence, on the one blank `render` can tell apart.

    `render` reads a target for truth, so only `""` reaches the marker branch —
    which is exactly the value the reseat would have destroyed. A whitespace-only
    target renders as whitespace and always has; that is `render`'s question, not
    this one's.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    doc, _r, _j = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {s["id"]: "譯文。" for s in doc["segments"]})
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    do_apply("d.md", "zh-TW", CFG, {seg["id"]: ""})
    out, missing = do_render("d.md", "zh-TW", CFG, fallback=False)
    assert missing == 1
    assert "    A second paragraph." not in out and "譯文。" in out


@pytest.mark.parametrize("doc, name", [
    (b"He walked into the room.\n\nShe did not look up.\n", "novel.md"),
    (b"He walked into the room.\n\nShe did not look up.\n", "novel.txt"),
])
def test_apply_keeps_the_paragraph_indent_a_zh_TW_translator_types(
        tmp_path, monkeypatch, doc, name):
    """Where the source has no run, a person's own is theirs to keep.

    Two U+3000 at the head of a paragraph is standard Traditional Chinese
    typography, and an English source has no leading run for it to be reseated
    from — so the strict form deletes it, silently, from every paragraph of a
    novel. That is neither reporting a person's words at `lx check` nor refusing
    them at the door, and after it no surface in the pipeline could produce an
    indented Chinese paragraph at all. Both formats, because `textparse` lifts a
    first line's indent into the skeleton and therefore *never* gives a segment a
    lead to be reseated from: for plain text this is the only branch there is.

    Found by adversarial review 2026-08-03, against the first version of this
    package, which closed the `do_apply` asymmetry too far.
    """
    _project(tmp_path, monkeypatch, doc=doc, name=name)
    parsed, _r, _j = do_extract(name, "zh-TW", CFG)
    do_apply(name, "zh-TW", CFG,
             {s["id"]: "　　" + w for s, w in zip(parsed["segments"],
                                                 ["他走進房間。", "她沒有抬頭。"])},
             origin="human")
    out, missing = do_render(name, "zh-TW", CFG)
    assert missing == 0
    assert out == "　　他走進房間。\n\n　　她沒有抬頭。\n"


@pytest.mark.parametrize("typed, stored", [
    ("  譯文。", "  譯文。"),          # two ASCII spaces: `collapse_space` eats them
    ("    譯文。", "    譯文。"),      # four, the width a reviewer copies
    ("  \t譯文。", "  \t譯文。"),      # a mixed run
    ("　  他走進房間。", "　  他走進房間。"),   # U+3000 then spaces, what a PDF paste leaves
])
def test_an_indent_apply_keeps_survives_normalization_on_the_way_through(
        tmp_path, monkeypatch, typed, stored):
    """`_INTERIOR` is load-bearing on this path, and only on this path.

    `accept` strips before `normalize` sees anything, so position 0 there is
    always a non-blank character. `do_apply` does neither — it passes the person's
    text through unstripped, and `keep_added_indent` then *keeps* the run instead
    of replacing it — so the run meets `collapse_space` on the way. Measured by
    neutering the lookbehind: four of these five shapes come back one space
    shorter without it, and nothing on `accept`'s path moves at all.

    The direct tests over `normalize` pin the regex; this pins the claim that a
    caller depends on it, which is the part that was written down wrongly twice.
    """
    _project(tmp_path, monkeypatch, doc=b"He walked into the room.\n")
    parsed, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = parsed["segments"][0]
    do_apply("d.md", "zh-TW", CFG, {seg["id"]: typed}, origin="human")
    saved = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert saved["target"] == stored


def test_a_model_s_own_indent_is_still_stripped_where_apply_s_is_kept(
        tmp_path, monkeypatch):
    """The half that did not move, asserted beside the half that did.

    `accept` is a gate and `lx apply` is not, which is the whole of the
    2026-07-29 decision. A model padding a Chinese line to its source's column is
    the case the strip exists for, and it stays stripped on that path.
    """
    _project(tmp_path, monkeypatch, doc=b"He walked into the room.\n")
    parsed, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = parsed["segments"][0]
    assert accept(seg, "　　他走進房間。", "zh-TW", CFG) == ("他走進房間。", None)
    do_apply("d.md", "zh-TW", CFG, {seg["id"]: "　　他走進房間。"}, origin="human")
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert stored["target"] == "　　他走進房間。"


def test_apply_does_not_let_a_deeper_indent_replace_the_one_the_source_has(
        tmp_path, monkeypatch):
    """And where the source *does* have a run, the source still wins.

    The recorded cost of the rule above: a person writing eight spaces into a
    segment whose source has four means an indented code block inside the list
    item, and they get four. That run is the host's layout — it is what keeps the
    paragraph inside the item at all — and a translation of a paragraph turning
    into a code block is the larger of the two failures. `docs/decisions.md`,
    2026-08-03.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    parsed, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in parsed["segments"] if s["source"].startswith("    "))
    do_apply("d.md", "zh-TW", CFG, {seg["id"]: "        這是程式碼"}, origin="human")
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert stored["target"] == "    這是程式碼"


def test_apply_still_takes_a_target_accept_would_refuse(tmp_path, monkeypatch):
    """The half that stays open, pinned so closing it needs a decision.

    A placeholder set that does not match is what `accept` refuses. `lx apply`
    takes it and lets `lx check` say so, which is the whole of the 2026-07-29
    decision — and reseating the blanks must not have quietly turned this path
    into a second acceptance gate.
    """
    _project(tmp_path, monkeypatch, doc=b"- item\n\n    Run `make build` now.\n")
    doc, _r, _j = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    assert seg["masked"] == "    Run ⟦1⟧ now."
    assert accept(seg, "現在執行。", "zh-TW", CFG)[0] is None
    assert do_apply("d.md", "zh-TW", CFG, {seg["id"]: "現在執行。"}) == (1, [])
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert stored["target"] == "    現在執行。"
