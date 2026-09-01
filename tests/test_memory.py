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

import statedb

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import store as store_mod  # noqa: E402
from scriptorium.cli import (  # noqa: E402
    UnnamedRegister,
    UnusableTarget,
    do_apply,
    do_check,
    do_commit,
    do_extract,
    do_hold,
    do_render,
)
from scriptorium.config import DEFAULT_CONFIG, DEFAULT_TONE  # noqa: E402
from scriptorium.mdparse import parse  # noqa: E402
from scriptorium.normalize import reseat_outer_blanks  # noqa: E402
from scriptorium.store import (  # noqa: E402
    SEGMENTATION_VERSION,
    append_tm,
    load_doc,
    load_tm,
    record_key,
    save_doc,
    seg_hash,
    segment_key,
    target_token,
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

    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_DOCUMENTATION})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    # The documentation wording is not offered to the novel at all: not as a hit
    # that `accept` then refuses — it never reaches `accept`, because the key
    # does not match.
    doc, reused, rejected, _notes = do_extract("novel.md", "zh-TW", CFG, tone="literary")
    assert (reused, rejected) == (0, 0)
    assert _only(doc)["status"] == "pending"

    do_apply("novel.md", "zh-TW", CFG, {_only(doc)["id"]: AS_PROSE})
    banked = tm_records(load_doc("novel.md", "zh-TW"), load_tm("zh-TW"))
    assert [r.get("tone") for r in banked] == ["literary"]
    append_tm("zh-TW", banked)

    tm = load_tm("zh-TW")
    assert len(tm) == 2
    _nodes, segs = parse(LEAVING.decode("utf-8"), [])
    assert tm_lookup(tm, segs[0]) == (AS_DOCUMENTATION, "tm", None)
    assert tm_lookup(tm, segs[0], "literary") == (AS_PROSE, "tm", None)


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
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_DOCUMENTATION})

    doc, reused, rejected, _notes = do_extract("d.md", "zh-TW", CFG, tone="literary")
    assert (reused, rejected) == (0, 0)
    assert (doc["tone"], _only(doc)["status"]) == ("literary", "pending")
    assert tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")) == []


def test_the_register_is_frozen_on_the_document_and_a_later_extract_keeps_it(
        tmp_path, monkeypatch):
    """A forgotten `--tone` must not return the document to the configured
    default. It was harmless while the register only reached the `Tone:` line;
    now it would take every carryover and every memory hit with it."""
    _project(tmp_path, monkeypatch, doc=LEAVING)
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG, tone="literary")
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_PROSE})

    doc, reused, rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    assert (doc["tone"], reused, rejected) == ("literary", 1, 0)
    assert _only(doc)["target"] == AS_PROSE

    # `--reset` is the exception, and deliberately so: it does not read the state
    # file at all, because it has to work on one this build cannot read. Until
    # 2026-08-19 that made it invent a register from config, which this assertion
    # pinned as intended behaviour; it is refused now.
    with pytest.raises(UnnamedRegister) as e:
        do_extract("d.md", "zh-TW", CFG, reset=True)
    # The flag, not the prose: the wording of a refusal sentence is explicitly
    # not frozen, and asserting a phrase out of it pins the one part that is free
    # to change. `--tone` is what the reader has to be handed.
    assert "--tone" in str(e.value), "the message has to name the flag that fixes it"

    # Re-pinned through the carryover rather than through `tone`, and that is the
    # whole point of these three lines: `tone or stored.get("tone") or cfg…` means
    # an explicit `--tone` wins whether or not the stored row was read, so
    # asserting `doc["tone"] == DEFAULT_TONE` after passing `tone=DEFAULT_TONE`
    # is a tautology wearing the old assertion's clothes. What still proves the
    # row went unread is that nothing came across it.
    doc, reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG,
                                                tone=DEFAULT_TONE, reset=True)
    assert (doc["tone"], reused) == (DEFAULT_TONE, 0)
    assert _only(doc)["status"] == "pending" and not _only(doc).get("target")


@pytest.mark.parametrize("tone", [None, "", "   ", "\t\n"])
def test_a_reset_that_names_no_register_is_refused_however_the_blank_is_spelled(
        tone, tmp_path, monkeypatch):
    """`is None` was the obvious guard and it leaves the defect reachable.

    `config.canonical_tone` folds `None`, `""` and any run of blanks onto the
    default register, and `do_extract` resolves `tone or stored… or cfg…`, so
    `--tone ""` on the CLI and `{"tone": ""}` on the wire would both pass an
    identity check and land on exactly the silent `technical` the refusal exists
    to stop. `web/server.py` passes `body.get("tone")` through unvalidated, so
    the wire is where a blank actually arrives.
    """
    _project(tmp_path, monkeypatch, doc=LEAVING)
    do_extract("d.md", "zh-TW", CFG, tone="literary")
    with pytest.raises(UnnamedRegister):
        do_extract("d.md", "zh-TW", CFG, tone=tone, reset=True)


def test_a_refused_reset_leaves_the_document_exactly_as_it_was(tmp_path, monkeypatch):
    """The oracle is the state on disk, not the exception.

    A guard placed anywhere in `do_extract` raises, so "it raised" cannot tell a
    guard at the top from one below the line that rebinds `tone` — and below that
    line it could never fire, which is the guard-fires-once shape this repository
    has already paid for once. Asserting the register and the target are still
    there is what pins that half.

    Only that half. State on disk cannot see a guard sitting *below* the parse
    and above the state read, which writes nothing either — measured by an
    adversarial pass, which moved it there and left all 1168 tests green. The
    other half is next door, where the oracle is which refusal wins.
    """
    _project(tmp_path, monkeypatch, doc=LEAVING)
    doc, *_ = do_extract("d.md", "zh-TW", CFG, tone="literary")
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_PROSE}, origin="human")

    with pytest.raises(UnnamedRegister):
        do_extract("d.md", "zh-TW", CFG, reset=True)

    after = load_doc("d.md", "zh-TW")
    assert after["tone"] == "literary", "the refusal moved the register it refused to guess"
    assert _only(after)["target"] == AS_PROSE
    assert _only(after)["origin"] == "human"


def test_a_refused_reset_never_reads_the_document(tmp_path, monkeypatch):
    """Which complaint wins, which is the only thing that can see this.

    The refusal is decidable from two arguments, so it has to come before every
    complaint the file itself could make. A guard below `read_document` and
    `fmt.parse` still writes no state row, so the state-on-disk oracle next door
    passes against it — and `lx extract missing.md --lang zh-TW --reset` then
    reports the missing file instead of the missing register, which is the wrong
    one: the absent `--tone` is a defect in the command as typed and will still
    be there once the file exists.
    """
    _project(tmp_path, monkeypatch, doc=LEAVING)
    with pytest.raises(UnnamedRegister):
        do_extract("gone.md", "zh-TW", CFG, reset=True)
    (tmp_path / "d.unknown").write_bytes(b"A sentence.\n")
    with pytest.raises(UnnamedRegister):
        do_extract("d.unknown", "zh-TW", CFG, reset=True)


def test_an_unnamed_register_is_not_an_unusable_target():
    """Its own class, and the docstring in `cli.py` argues why.

    Folding the refusal into `UnusableTarget` would widen every existing
    `pytest.raises(UnusableTarget)` in this suite onto an unrelated failure, so
    the split is load-bearing and not a naming preference. One line, because
    subclassing it back is a one-line change that nothing else would notice.
    """
    assert not issubclass(UnnamedRegister, UnusableTarget)
    assert not issubclass(UnusableTarget, UnnamedRegister)


def test_a_reset_that_names_a_register_still_starts_over(tmp_path, monkeypatch):
    """The refusal narrows `--reset`; it does not take the escape hatch away.

    Named separately from the register test above because that one is about
    stickiness and this one is about the flag still doing its job — a refusal
    that quietly made `--reset` unusable would be a worse defect than the one it
    closes, and `store._refuse_if_newer` points a person at this exact command.
    """
    _project(tmp_path, monkeypatch, doc=LEAVING)
    doc, *_ = do_extract("d.md", "zh-TW", CFG, tone="literary")
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: AS_PROSE}, origin="human")

    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG,
                                              tone="literary", reset=True)
    assert (doc["tone"], reused, rejected) == ("literary", 0, 0)
    assert not _only(doc).get("target"), "a reset that kept the target is not a reset"
    # `notes["register"]` is `None` here by construction rather than by decision:
    # its guard reads `stored`, which a reset never fills, so a register change
    # is undetectable on this path. Said here rather than asserted — an assertion
    # that cannot fail reads like a guarantee and is not one. The contract says
    # the same to a client.
    assert notes["register"] is None


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
    tm = {record_key(LEGACY): LEGACY}
    _nodes, segs = parse("A shared sentence.\n", [])
    target, origin, _slots = tm_lookup(tm, segs[0])
    assert target == LEGACY["target"]
    assert origin == "tm:legacy"


def test_an_unversioned_hit_is_marked_so_a_reviewer_can_see_it():
    """`tm:legacy` and not `tm`: a match on content alone is precisely the
    context-blind reuse the key was changed to stop, so which segments still rest
    on it has to be visible rather than inferred."""
    tm = {record_key(LEGACY): LEGACY}
    _nodes, segs = parse("A shared sentence.\n\n> A shared sentence.\n", [])
    assert [tm_lookup(tm, s)[1] for s in segs] == ["tm:legacy", "tm:legacy"]

    versioned = dict(LEGACY, context="para", segmentation_version=SEGMENTATION_VERSION)
    tm = {record_key(versioned): versioned}
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
    assert tm_lookup(tm, segs[0]) == (LEGACY["target"], "tm", None)
    assert tm_lookup(tm, segs[0], DEFAULT_TONE) == (LEGACY["target"], "tm", None)

    # And the unversioned tier below it, reached only through the fallback.
    bare = {record_key(LEGACY): LEGACY}
    assert tm_lookup(bare, segs[0], DEFAULT_TONE) == (LEGACY["target"], "tm:legacy", None)

    # End to end, because that is where a whole-memory invalidation would show.
    doc, reused, rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["target"] == LEGACY["target"]


def test_a_literary_document_is_not_offered_the_unversioned_tier():
    """The register's half of the rule `variant` already had, one step along.

    A pre-variant record cannot be *known* to be the right form. A pre-register
    record is stronger than that: it is documentation-register wording by
    construction, because the build that wrote it ended every zh-TW brief with
    "Write technical documentation register" whatever `tone` said.
    """
    tm = {record_key(LEGACY): LEGACY}
    _nodes, segs = parse("A shared sentence.\n", [])
    assert tm_lookup(tm, segs[0], "literary") == (None, None, None)
    assert tm_lookup(tm, segs[0], DEFAULT_TONE) == (LEGACY["target"], "tm:legacy", None)


def test_a_segment_with_a_variant_is_not_offered_a_pre_variant_record():
    """A record written before variants existed cannot be known to be the right
    form, and guessing is how a plural becomes a singular somewhere nobody looks.
    Constructed by hand: no format emits a variant yet, which is the point of
    adding the field before one does."""
    tm = {record_key(LEGACY): LEGACY}
    _nodes, segs = parse("A shared sentence.\n", [])
    assert tm_lookup(tm, dict(segs[0], variant="plural")) == (None, None, None)


def test_committing_upgrades_an_unversioned_record_instead_of_reusing_it_forever(
        tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, doc=b"A shared sentence.\n")
    append_tm("zh-TW", [LEGACY])

    doc, reused, rejected, _notes = do_extract("d.md", "zh-TW", CFG)
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
    assert [r["target"] for r in load_tm("zh-TW").values()] == [LEGACY["target"]]


# --- reuse takes the same acceptance path as model output --------------------


def test_protecting_the_other_name_does_not_rename_the_person_in_the_sentence(
        tmp_path, monkeypatch):
    """The measurement HANDOFF-033 was written from, end to end.

    An author renaming a character, or deciding to protect the surname instead of
    the given name, is a routine mid-book act. It leaves the segment's
    *placeholder count* alone, so the id-set comparison that gated every reuse
    had nothing to object to: the stored wording was accepted, `lx check` exited
    0, and the rendered sentence named the wrong person twice —

        config/dnt.txt  Brian   ->  render  Brian 在門口迎接了 Wendy。
        config/dnt.txt  Wendy   ->  render  Wendy 在門口迎接了 Wendy。

    The wording knows which map its ⟦n⟧ referred to, so it is moved into the new
    one instead: `Brian` stops being a placeholder because it stops being
    protected, `Wendy` becomes one because it starts, and the sentence says what
    the reviewer wrote. Asserted on the rendered bytes, because that is where the
    defect was visible and the counts were not.
    """
    _project(tmp_path, monkeypatch, dnt="Brian\n",
             doc=b"Brian greeted Wendy at the gate.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    assert _only(doc)["masked"] == "⟦1⟧ greeted Wendy at the gate."
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 在門口迎接了 Wendy。"},
             origin="human")
    text, _missing = do_render("d.md", "zh-TW", CFG)
    assert text == "Brian 在門口迎接了 Wendy。\n"

    (tmp_path / "config" / "dnt.txt").write_text("Wendy\n", encoding="utf-8")
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert _only(doc)["masked"] == "Brian greeted ⟦1⟧ at the gate."
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["target"] == "Brian 在門口迎接了 ⟦1⟧。"
    assert _only(doc)["origin"] == "human", "a person's wording changed hands"

    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 0
    text, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, text) == (0, "Brian 在門口迎接了 Wendy。\n"), \
        "the sentence renamed the person it is about"


def test_wording_survives_the_mask_configuration_moving_under_it(
        tmp_path, monkeypatch):
    """Measured 2026-07-27, repaired 2026-08-17, and the shape has changed twice.

    The key is deliberately blind to the do-not-translate list — so that the same
    wording banked on two machines with different lists is still one entry —
    which means editing that list changes a segment's placeholder count while its
    key stays put. Written straight to target, as reuse used to be, the extra ⟦2⟧
    had no slot to restore from and reached the rendered document verbatim. The
    answer was to *refuse* such a proposal, and this test asserted the refusal.

    A refusal is the wrong answer when the repair is deterministic, which
    invariant 5 says plainly. The document's own stored wording knows the map its
    placeholders were written against, so `translate.accept` unmasks it against
    that map and seats the current one back in by content: the wording is
    **repaired**, the render is correct, and nothing is refused or kept or
    reported. What still refuses is a proposal that cannot be seated — the test
    below this one — and a memory hit, which carries no map at all.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\nAcme\n", doc=DNT_DOC)
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    assert _only(doc)["masked"] == "⟦1⟧ and ⟦2⟧ ship together."
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 與 ⟦2⟧ 一同出貨。"})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    # Drop one term. The sentence is unchanged, so the key still matches and the
    # segment now has one slot where the wording names two.
    (tmp_path / "config" / "dnt.txt").write_text("Celurion\n", encoding="utf-8")
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0), "the wording was repaired, not refused"
    assert _only(doc)["target"] == "⟦1⟧ 與 Acme 一同出貨。"
    assert _only(doc)["origin"] == "agent", "the wording changed hands"
    assert notes["kept"] == [], "nothing had to be kept: nothing was refused"
    assert "⟦2⟧" in list(load_tm("zh-TW").values())[0]["target"], "the entry is still there"

    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 0
    text, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, text) == (0, "Celurion 與 Acme 一同出貨。\n"), \
        "the sentence the reviewer wrote, rendered under the new configuration"

    # And back again. The wording is now stored in the *new* numbering, so this
    # is the same repair in the other direction rather than an undo.
    (tmp_path / "config" / "dnt.txt").write_text("Celurion\nAcme\n", encoding="utf-8")
    doc, reused, rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["target"] == "⟦1⟧ 與 ⟦2⟧ 一同出貨。"
    text, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, text) == (0, "Celurion 與 Acme 一同出貨。\n")


def test_a_memory_hit_carries_its_own_map_and_is_repaired_with_it(
        tmp_path, monkeypatch):
    """The second half of the repair, and what the record grew a field for.

    A hit is a target and nothing else until the line says what its placeholders
    stood for. `store.tm_record` writes that as `slots` — the originals in id
    order, which is the whole of the information, since `mask` numbers from 1
    with one counter — and `store.tm_lookup` hands it back so the same
    `mask.reseat` that repairs a carryover repairs a hit.

    The document's own copy is cleared first, deliberately: with it in place the
    carryover answers and the memory is never consulted, which is a different
    test.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\nAcme\n", doc=DNT_DOC)
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 與 ⟦2⟧ 一同出貨。"})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))
    assert list(load_tm("zh-TW").values())[0]["slots"] == ["Celurion", "Acme"], \
        "the line does not say what its placeholders stood for"

    state = load_doc("d.md", "zh-TW")
    state["segments"][0]["target"] = ""
    save_doc("d.md", "zh-TW", state)

    (tmp_path / "config" / "dnt.txt").write_text("Celurion\n", encoding="utf-8")
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["target"] == "⟦1⟧ 與 Acme 一同出貨。"
    assert _only(doc)["origin"] == "tm"
    text, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, text) == (0, "Celurion 與 Acme 一同出貨。\n")


def test_a_memory_line_with_no_map_is_offered_only_where_a_renumbering_cannot_reach(
        tmp_path, monkeypatch):
    """The transition rule, for every line anyone already has.

    A line banked before the map existed cannot say what its ids meant, so the
    id-set comparison is the whole of its gate — and that is what a wholesale
    renumbering satisfies. It is offered anyway wherever a renumbering could not
    have moved it, and that is decidable rather than a guess: `mask.mask` numbers
    every inline match first and the do-not-translate terms after, so a markup
    slot's id is a pure function of the source text, which the content hash has
    already fixed. Only the term tail was ever exposed.

    Measured when this landed: 0.6% of this repository's segments carry a
    do-not-translate slot against 34.8% carrying any slot, so refusing every
    placeholder-bearing line instead would have discarded reuse that was never at
    risk.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n",
             doc=b"Celurion ships today.\n\nSee [the guide](https://example.com/x).\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    term_seg, link_seg = doc["segments"]
    assert term_seg["masked"] == "⟦1⟧ ships today."
    assert "⟦1⟧" in link_seg["masked"], "the link was not masked"

    # Two lines in the shape the memory held before 2026-08-17: a target, and no
    # account of what its placeholder stood for.
    append_tm("zh-TW", [
        {"hash": term_seg["hash"], "context": term_seg["context"],
         "segmentation_version": SEGMENTATION_VERSION,
         "source": term_seg["source"], "target": "⟦1⟧ 今天出貨。"},
        {"hash": link_seg["hash"], "context": link_seg["context"],
         "segmentation_version": SEGMENTATION_VERSION,
         "source": link_seg["source"], "target": "請見[指南]⟦1⟧。"},
    ])
    assert all("slots" not in r for r in load_tm("zh-TW").values())

    # `tone=` because a `--reset` has to name the register since 2026-08-19; the
    # document was extracted in the default one above, so this changes nothing
    # the test measures — `tm_lookup` gets the same value either way.
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG,
                                              tone=DEFAULT_TONE, reset=True)
    by_id = {s["id"]: s for s in doc["segments"]}
    assert by_id[link_seg["id"]]["target"] == "請見[指南]⟦1⟧。", \
        "a markup-only line was never at risk and should still answer"
    assert not by_id[term_seg["id"]].get("target"), \
        "a line with no map placed a placeholder on a protected term"
    assert (reused, rejected) == (1, 0), "the refused line was not even offered"


def test_adding_a_term_repairs_the_wording_that_was_written_without_it(
        tmp_path, monkeypatch):
    """The other edit to the same list, and the direction HANDOFF-007 wrote down.

    Drop a term and the stored wording keeps a placeholder the new slot map
    cannot restore; add one and the wording is short a placeholder instead, so
    the new term reaches the rendered document unprotected — wrong, and
    impossible to see by reading the output, which is why this direction has its
    own test. Both were refused until 2026-08-17.

    Both are repaired now, and this is the direction that shows the repair is not
    merely a renumbering: the wording never had a placeholder for `Acme` at all,
    and it comes back with one, because the term is seated by content into the
    prose the old map unmasks.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=DNT_DOC)
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    assert _only(doc)["masked"] == "⟦1⟧ and Acme ship together."
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 與 Acme 一同出貨。"})
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    (tmp_path / "config" / "dnt.txt").write_text("Celurion\nAcme\n", encoding="utf-8")
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0)
    assert _only(doc)["target"] == "⟦1⟧ 與 ⟦2⟧ 一同出貨。", "the new term was not seated"
    assert notes["kept"] == []

    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 0
    text, missing = do_render("d.md", "zh-TW", CFG)
    assert (missing, text) == (0, "Celurion 與 Acme 一同出貨。\n")


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
    doc, reused, rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1)
    assert _only(doc)["status"] == "pending"


@pytest.mark.parametrize("origin,gives_way", [
    ("llm:draft", True), ("tm", True), ("tm:legacy", True),
    ("human", False), ("agent", False), ("carryover", False),
])
def test_only_a_machine_draft_gives_way_to_a_memory_hit(
        tmp_path, monkeypatch, origin, gives_way):
    """Divergence (27), reproduced on both sides of the split and closed.

    Prior state and the memory are both proposals and they can disagree. Until
    2026-09-01 they were tried in order with no regard for who wrote the first,
    so a stored target that no longer fits — with a banked wording behind it that
    does — was replaced, and a `human` segment came back as `tm`: a provenance
    nobody claimed, and not the one *Origin precedence* protects, so the next
    unattended run could overwrite it. No collision and no race required.

    Invariant 9 is the line. A machine draft is regenerable and the memory still
    holds the wording that replaced it, so nothing is lost by letting it give
    way; a person's or an agent's sentence is not regenerable, so it is kept and
    reported at `lx check` like any other kept wording. `carryover` is the origin
    `store.prior_targets` gives a body written before the field existed — nobody's
    *known* prose, so it is kept, because the rule enumerates what may be replaced
    and never what is protected.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=b"Celurion ships.\n")
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧ 出貨。"}, origin=origin)
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    # Damage only the document's copy, leaving the memory's intact.
    state = load_doc("d.md", "zh-TW")
    state["segments"][0]["target"] = "⟦1⟧ 與 ⟦2⟧ 出貨。"
    save_doc("d.md", "zh-TW", state)

    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    after = _only(doc)
    if gives_way:
        assert (reused, rejected) == (1, 0)
        assert (after["origin"], after["target"]) == ("tm", "⟦1⟧ 出貨。")
        assert notes["replaced"] == [sid], "the swap must not be silent"
        assert notes["kept"] == []
    else:
        assert (reused, rejected) == (0, 1), "the memory was never offered"
        assert after["origin"] == origin, "the provenance stays with the wording"
        assert after["target"] == "⟦1⟧ 與 ⟦2⟧ 出貨。"
        assert notes["kept"] == [sid] and notes["replaced"] == []
        report, _ = do_check("d.md", "zh-TW", CFG)
        assert report["errors"] == 1 and report["by_rule"]["tags"] == 1


def test_a_body_written_before_the_origin_field_existed_is_kept(tmp_path, monkeypatch):
    """The `carryover` case reached the way it is actually reached.

    `store.prior_targets` reads ``held.get("origin") or "carryover"``, so a state
    row from before that field existed surfaces under that name. The parametrized
    test above sets the string; this one removes the key, which is the state an
    older build actually left behind and which `store` still reads today.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=b"Celurion ships.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧ 出貨。"}, origin="human")
    append_tm("zh-TW", tm_records(load_doc("d.md", "zh-TW"), load_tm("zh-TW")))

    state = load_doc("d.md", "zh-TW")
    state["segments"][0].pop("origin")
    state["segments"][0]["target"] = "⟦1⟧ 與 ⟦2⟧ 出貨。"
    save_doc("d.md", "zh-TW", state)

    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1)
    assert notes["kept"] == [sid] and notes["replaced"] == []
    assert _only(doc)["origin"] == "carryover"


# ── what a re-parse may do to wording the document already holds ────────────
#
# `docs/contracts/workbench-http.md` divergences (24) and (25), both closed
# 2026-08-17, and the two things left open beside them — (26), a run of
# identical paragraphs that changed size, and (27), the memory answering over
# wording this document was holding.


def test_a_carryover_that_cannot_be_seated_keeps_the_wording_it_could_not_carry(
        tmp_path, monkeypatch):
    """Divergence (24), reproduced and closed — on the case that still refuses.

    The repair takes the deterministic half: a term renumbered, dropped or added
    is seated by content. What is left is genuinely ambiguous, and this is its
    smallest shape — a translation that names a protected term twice where the
    source names it once. Two occurrences, one slot: no rule can say which of them
    the placeholder belongs to, so the seating refuses rather than guessing, and
    a guess there is what puts one character's name where another's belongs.

    A refusal is where divergence (24) begins. Until 2026-08-17 the segment then
    came back with **no target at all** — a sentence a person wrote, deleted by a
    re-parse, with `rejected` counting it and nothing naming it. It stays now,
    with its `origin` and its status, and `lx check` reports it.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=b"Celurion ships today.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    assert _only(doc)["masked"] == "⟦1⟧ ships today."
    # A person's wording that names the protected term a second time. `lx apply`
    # stores it deliberately — a person's words are reported at `lx check`, not
    # rejected at the door.
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧ 今天出貨，⟦1⟧ 準時。"}, origin="human")

    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1), "the acceptance path still refuses it"
    assert notes["kept"] == [sid]
    kept = _only(doc)
    assert kept["target"] == "⟦1⟧ 今天出貨，⟦1⟧ 準時。", "a sentence a person wrote was deleted"
    assert (kept["origin"], kept["status"]) == ("human", "translated")

    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 1 and report["by_rule"]["tags"] == 1


@pytest.mark.parametrize("first,third", [("human", "llm:draft"), ("llm:draft", "human")])
def test_two_positions_holding_one_sentence_keep_their_own_wording(
        tmp_path, monkeypatch, first, third):
    """Divergence (25), reproduced in both directions and closed.

    `store.prior_targets` grouped rows under `tm_key`, which is deliberately
    blind to position, so a document holding one sentence twice held one entry
    for two positions and the last row read filled both. Measured with
    `Yes. / Middle. / Yes.`: the person's wording at the first paragraph was
    replaced by the model's draft from the third **and its `origin` with it**, so
    the guard in `store.save_targets` — which compares the origin on disk — was
    evadable with no race and no second process. The other order launders a
    machine draft into `human` and locks the model out of that position for good.

    Read through `tests/statedb.py` rather than `load_doc`, deliberately:
    `store._segment` recomputes `status` on read, and a reader that recomputes
    has already hidden two defects from two tests in this repository.
    """
    _project(tmp_path, monkeypatch, doc=b"Yes.\n\nMiddle.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    assert len(ids) == 3
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "好。"}, origin=first)
    do_apply("d.md", "zh-TW", CFG, {ids[1]: "中間。"}, origin="agent")
    do_apply("d.md", "zh-TW", CFG, {ids[2]: "是的。"}, origin=third)

    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (3, 0)
    rows = {s["id"]: s for s in statedb.segments(tmp_path)}
    assert (rows[ids[0]]["target"], rows[ids[0]]["origin"]) == ("好。", first)
    assert (rows[ids[2]]["target"], rows[ids[2]]["origin"]) == ("是的。", third)
    assert (rows[ids[1]]["target"], rows[ids[1]]["origin"]) == ("中間。", "agent")
    assert notes["ambiguous"] == [], "nothing about this document is ambiguous"


def test_a_run_of_identical_paragraphs_survives_an_insertion_earlier_in_the_file(
        tmp_path, monkeypatch):
    """The half a by-id rule cannot reach, and the reason `align` has a second tier.

    Segment ids are sequential over translatable blocks, so inserting one
    paragraph in chapter one shifts every id after it: on a five-thousand-segment
    novel an id-only carryover answers for the three segments before the
    insertion and nothing else, and every duplicate in the book falls back to the
    rule (25) exists to remove. Matching each member of a key's class to the one
    at the same place in the stored class carries all of them, because an
    insertion outside the class leaves its membership and its order alone.
    """
    _project(tmp_path, monkeypatch, doc=b"Yes.\n\nMiddle.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "好。"}, origin="human")
    do_apply("d.md", "zh-TW", CFG, {ids[2]: "是的。"}, origin="llm:draft")

    (tmp_path / "d.md").write_bytes(b"A new opening.\n\nYes.\n\nMiddle.\n\nYes.\n")
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    rows = {s["id"]: s for s in statedb.segments(tmp_path)}
    assert rows["s0001"]["target"] is None, "the inserted paragraph is new work"
    assert (rows["s0002"]["target"], rows["s0002"]["origin"]) == ("好。", "human")
    assert (rows["s0004"]["target"], rows["s0004"]["origin"]) == ("是的。", "llm:draft")
    assert (reused, notes["ambiguous"]) == (2, []), "`Middle.` was never translated here"


def test_a_new_occurrence_of_an_old_sentence_is_named_and_carries_no_hold(
        tmp_path, monkeypatch):
    """Divergence (26), open and reported rather than hidden.

    The author writes a line of dialogue the book already has. Every stored
    paragraph is still in the document, so the diff carries all three to their
    new positions — that part is not a guess. The *new* paragraph is: it matches
    the key of a sentence that already exists, nothing establishes which of them
    it is, and the pre-2026-08-17 rule handed it the last stored wording.

    It still does, because refusing to answer would delete wording to avoid
    mislabelling it and the other half of this package exists to stop that. Two
    things are new. `lx extract` **names** it, since its run changed size and it
    was not placed by the alignment. And the **hold does not ride the fallback**:
    a hold is one reviewer's statement about a position, and this is the branch
    that could not establish one — carrying it would take a paragraph nobody has
    ever read out of every queue, leaving `lx check` green because a hold is a
    warning, and render it into the book.
    """
    _project(tmp_path, monkeypatch, doc=b"Yes.\n\nMiddle.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "好。"}, origin="human")
    do_apply("d.md", "zh-TW", CFG, {ids[1]: "中間。"}, origin="agent")
    do_apply("d.md", "zh-TW", CFG, {ids[2]: "是的。"}, origin="llm:draft")
    do_hold("d.md", "zh-TW", CFG, [ids[2]])

    (tmp_path / "d.md").write_bytes(b"Yes.\n\nYes.\n\nMiddle.\n\nYes.\n")
    doc, _reused, _rejected, notes = do_extract("d.md", "zh-TW", CFG)
    rows = {s["id"]: s for s in statedb.segments(tmp_path)}
    # The three that existed, each still holding its own wording, origin and hold.
    assert (rows["s0002"]["target"], rows["s0002"]["origin"]) == ("好。", "human")
    assert (rows["s0003"]["target"], rows["s0003"]["origin"]) == ("中間。", "agent")
    assert (rows["s0004"]["target"], rows["s0004"]["origin"]) == ("是的。", "llm:draft")
    assert rows["s0004"].get("review") == "held", "the hold left the wording it was on"
    # And the one the author just wrote.
    assert notes["ambiguous"] == ["s0001"]
    assert rows["s0001"]["target"] == "是的。", "the old rule's answer, still given"
    assert rows["s0001"].get("review") is None, "a hold rode a guess"


def test_a_deletion_earlier_in_the_file_leaves_a_run_holding_its_own_wording(
        tmp_path, monkeypatch):
    """The direction an id-keyed carryover gets *backwards*, and the reason this
    is a diff rather than a rule about ids or ordinals.

    Deleting a paragraph shifts every later id down by one, so the row that sat
    at `s0001` now names a different sentence — and if that row happens to hold
    the same text, an id-keyed rule hands its wording to the wrong position with
    complete confidence. Measured by the adversarial pass over the first version
    of this change: it moved a person's wording onto a machine's position and
    `origin: human` with it, which the guard in `store.save_targets` then honours
    for good. The version before this package got that case right by accident.
    """
    _project(tmp_path, monkeypatch, doc=b"Middle.\n\nYes.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "中間。"}, origin="agent")
    do_apply("d.md", "zh-TW", CFG, {ids[1]: "好。"}, origin="human")
    do_apply("d.md", "zh-TW", CFG, {ids[2]: "是的。"}, origin="llm:draft")

    (tmp_path / "d.md").write_bytes(b"Yes.\n\nYes.\n")
    doc, reused, _rejected, notes = do_extract("d.md", "zh-TW", CFG)
    rows = {s["id"]: s for s in statedb.segments(tmp_path)}
    assert (rows["s0001"]["target"], rows["s0001"]["origin"]) == ("好。", "human")
    assert (rows["s0002"]["target"], rows["s0002"]["origin"]) == ("是的。", "llm:draft")
    assert (reused, notes["ambiguous"]) == (2, [])


def test_a_run_that_lost_a_member_hands_no_ones_origin_to_another_position(
        tmp_path, monkeypatch):
    """Four byte-identical paragraphs and nothing else: the document that cannot
    be aligned, because there is no unique prose anywhere to anchor a match.

    Every rule is guessing here, so the one thing that must hold is that guessing
    is not *upgraded*. The first version of this change matched the run against
    itself at the first offset that fitted and handed the person's wording — and
    `origin: human` — to a position the model had drafted, where origin
    precedence then locks every later run out of it. A matching block whose
    elements all share one key is refused when that key's run changed size, so
    the answer degrades to the one this build gave before, and every segment is
    named.
    """
    _project(tmp_path, monkeypatch, doc=b"Yes.\n\nYes.\n\nYes.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "甲。"}, origin="human")
    do_apply("d.md", "zh-TW", CFG, {ids[1]: "乙。"}, origin="llm:draft")
    do_apply("d.md", "zh-TW", CFG, {ids[2]: "丙。"}, origin="llm:draft")
    # `ids[3]` is left untranslated on purpose: it is what made the first
    # version's ordinal test compare a count of translated rows against a count
    # of parsed segments, two different populations.

    (tmp_path / "d.md").write_bytes(b"Yes.\n\nYes.\n\nYes.\n")
    doc, _reused, _rejected, notes = do_extract("d.md", "zh-TW", CFG)
    rows = statedb.segments(tmp_path)
    assert [s["origin"] for s in rows] == ["llm:draft"] * 3, "a person's origin moved"
    assert [s["target"] for s in rows] == ["丙。"] * 3, "the answer this build gave before"
    assert notes["ambiguous"] == ["s0001", "s0002", "s0003"], "and it says so"


def test_a_run_carries_across_an_insertion_with_no_unique_text_to_anchor_it(
        tmp_path, monkeypatch):
    """The other side of the rule above, and where the whole gain is.

    Nothing in this document is unique either, but the run did not change size,
    so the offset is not a coin toss: three paragraphs went in and three came
    out, in order, and each keeps its own wording, origin and hold. On a novel
    this is forty identical lines of dialogue surviving a paragraph inserted in
    chapter one — measured 41/41 against 2/41 for the rule this replaces.
    """
    _project(tmp_path, monkeypatch, doc=b"Yes.\n\nYes.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "甲。"}, origin="human")
    do_apply("d.md", "zh-TW", CFG, {ids[1]: "乙。"}, origin="llm:draft")
    do_apply("d.md", "zh-TW", CFG, {ids[2]: "丙。"}, origin="agent")
    do_hold("d.md", "zh-TW", CFG, [ids[1]])

    (tmp_path / "d.md").write_bytes(b"A new opening.\n\nYes.\n\nYes.\n\nYes.\n")
    doc, _reused, _rejected, notes = do_extract("d.md", "zh-TW", CFG)
    rows = statedb.segments(tmp_path)
    assert [s["target"] for s in rows] == [None, "甲。", "乙。", "丙。"]
    assert [s["origin"] for s in rows] == [None, "human", "llm:draft", "agent"]
    assert rows[2].get("review") == "held", "the hold left the wording it was on"
    assert notes["ambiguous"] == []


def test_the_alignment_has_a_budget_and_degrades_to_the_old_rule_over_it(
        tmp_path, monkeypatch):
    """`SequenceMatcher` is near-linear on mostly-distinct sequences and
    quadratic on ones that are not, and a document is allowed to be
    pathological: measured 2026-08-17, five thousand byte-identical paragraphs
    take 2.0 s and twelve thousand take 14.2 s, against 8 ms for a realistic
    five-thousand-segment novel. Over the budget the diff is skipped and every
    segment resolves the way it did before — a worse answer, not a hung command.

    Driven by lowering the budget rather than by building a pathological
    document, so the test costs nothing and still asserts the thing that matters:
    which answer comes out on the other side.
    """
    _project(tmp_path, monkeypatch, doc=b"Yes.\n\nYes.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "甲。"}, origin="human")
    do_apply("d.md", "zh-TW", CFG, {ids[1]: "乙。"}, origin="llm:draft")

    monkeypatch.setattr(store_mod, "ALIGN_BUDGET", 0)
    do_extract("d.md", "zh-TW", CFG)
    rows = statedb.segments(tmp_path)
    assert [s["target"] for s in rows] == ["乙。", "乙。"], "the pre-2026-08-17 answer"
    assert [s["origin"] for s in rows] == ["llm:draft", "llm:draft"]


def test_a_kept_wording_remembers_which_numbering_it_was_written_in(
        tmp_path, monkeypatch):
    """The guard that fires once is not a guard, and this is the case that
    proves it.

    `store.save_doc` rewrites a segment's `slots` from the fresh parse on every
    `lx extract`, and the divergence (24) keep path writes the *old* target onto
    the *fresh* segment. So a rule that reads provenance off the segment sees the
    new map from the second extract onward and accepts the stale wording in
    silence — measured 2026-08-17, before `target_slots` existed:

        after apply        target='⟦1⟧ …'   slots={'1': 'Brian'}
        after extract #1   target='⟦1⟧ …'   slots={'1': 'Wendy'}
        after extract #2   target='⟦1⟧ …'   slots={'1': 'Wendy'}

    So the map a target was written against is pinned beside the target, written
    only when it differs from the segment's own — which is why the ordinary
    segment carries nothing and this one does. Asserted twice over, because once
    is what the defect looked like.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\nAcme\n",
             doc=b"Celurion and Acme ship together.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    # Names one protected term twice, so no seating can place it once `Acme`
    # stops being protected: two occurrences of `Celurion`, one slot.
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧ 與 ⟦2⟧ 一同出貨，⟦1⟧ 準時。"},
             origin="human")
    assert statedb.segments(tmp_path)[0].get("target_slots") is None, \
        "a target written against its own segment carries no provenance"

    (tmp_path / "config" / "dnt.txt").write_text("Celurion\n", encoding="utf-8")
    first = None
    for _extract in (1, 2):
        doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
        row = statedb.segments(tmp_path)[0]
        state = (reused, rejected, notes["kept"], row["target"], row["origin"])
        if first is None:
            first = state
            assert notes["kept"] == [sid]
            assert row["target"] == "⟦1⟧ 與 ⟦2⟧ 一同出貨，⟦1⟧ 準時。"
            assert {k: v["original"] for k, v in row["target_slots"].items()} == \
                {"1": "Celurion", "2": "Acme"}, "the map it was written against"
        else:
            assert state == first, "the second extract accepted what the first refused"


def test_a_register_change_says_what_it_left_behind(tmp_path, monkeypatch):
    """A re-extract into another register carries nothing over — deliberately,
    because the alternative banks a documentation voice under the literary key
    and poisons the memory permanently. It said nothing at all while doing it:
    `reused 0`, no refusal counted, and a reviewed book emptied.

    The behaviour is unchanged and only the silence is fixed. `lx extract` is a
    person's command, and the contract tells a client to send `tone` on a
    re-extract button — both need to be told what a register move costs.
    """
    _project(tmp_path, monkeypatch, doc=b"He left without a word.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "他一言不發地走了。"}, origin="human")

    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG, tone="literary")
    assert (reused, rejected) == (0, 0), "nothing was even offered"
    assert notes["register"] == (DEFAULT_TONE, "literary", 1)
    assert _only(doc)["status"] == "pending"
    assert notes["kept"] == [], "there was no proposal to refuse and nothing to keep"


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
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
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
    _doc2, reused, rejected, _d = do_extract("d.md", "zh-TW", CFG)
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
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", CFG)
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
    _doc2, reused, rejected, _d = do_extract("d.md", "zh-TW", CFG)
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
    doc, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))

    # Written straight to the memory, bypassing `do_apply` on purpose: this is an
    # entry an older build wrote, and no current path can produce one.
    banked = dict(seg, target="這是第二段。", origin="human")
    append_tm("zh-TW", [tm_record(banked, DEFAULT_TONE)])

    for leftover in (tmp_path / ".lx").glob("state.db*"):
        leftover.unlink()
    doc2, reused, rejected, _d = do_extract("d.md", "zh-TW", CFG)
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
    doc, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    applied_n, unknown, written, conflicts, _refused = do_apply(
        "d.md", "zh-TW", CFG, {seg["id"]: applied})
    assert (applied_n, unknown, conflicts) == (1, [], {})
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert stored["target"] == "    這是第二段。"
    # The readback is the stored text, not the submitted one, which is the whole
    # reason it exists: a client that trusted what it sent would show the indent
    # missing until it refetched the book.
    assert written[seg["id"]]["text"] == stored["target"]
    assert written[seg["id"]]["token"] == target_token(stored["target"])


@pytest.mark.parametrize("blank", ["", "   ", "\n", "　"])
def test_apply_refuses_a_blank_target_and_names_the_way_to_redo_the_segment(
        tmp_path, monkeypatch, blank):
    """An empty target is a rejected input at `contract_version = 2`, not a result.

    It used to be storable, and combined with status-derived-from-text and the
    origin precedence scheduled next it produces a segment every run selects, no
    writer may write, and `lx check` can never pass: the draft pass takes it on
    `status == "pending"`, the repair pass on the `missing` error, and both writes
    are refused for being `llm:*` over `human`. Refusing at the door makes that
    state unreachable rather than guarded against.

    The refusal is in `do_apply` rather than at the endpoint, so `lx apply` cannot
    walk around it: an empty string is not "a person's words" that `AGENTS.md`
    exempts from refusal, it is their absence. `docs/decisions.md`, 2026-08-14.

    All four blanks, because the predicate is `.strip()` — the same one
    `checks.check_segment`'s `missing` rule uses, so the two cannot come to
    disagree about what empty means. U+3000 is in the list because it is what a
    zh-TW reviewer's own paragraph indent is made of.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    doc, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    with pytest.raises(UnusableTarget) as e:
        do_apply("d.md", "zh-TW", CFG, {seg["id"]: blank})
    assert seg["id"] in str(e.value)
    assert "lx translate" in str(e.value), "a refusal says what to do next"
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert not stored["target"]


def test_one_blank_target_refuses_the_whole_save_and_writes_none_of_it(
        tmp_path, monkeypatch):
    """Whole-request, before anything is written.

    A workbench save carries every dirty segment at once, so refusing per id
    would leave a reviewer's other edits half-applied with no way for the page to
    say which. Rejected input, not partial failure.
    """
    _project(tmp_path, monkeypatch, doc=b"- item one\n\n    A second paragraph.\n")
    doc, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {s["id"]: "譯文。" for s in doc["segments"]})
    blanked = next(s for s in doc["segments"] if s["source"].startswith("    "))
    other = next(s for s in doc["segments"] if s["id"] != blanked["id"])
    with pytest.raises(UnusableTarget):
        do_apply("d.md", "zh-TW", CFG,
                 {blanked["id"]: "", other["id"]: "改過的譯文。"})
    stored = {s["id"]: s.get("target") for s in load_doc("d.md", "zh-TW")["segments"]}
    assert stored[blanked["id"]] == "    譯文。"
    assert stored[other["id"]] == "譯文。", "the good edit in the same payload was not written"
    out, missing = do_render("d.md", "zh-TW", CFG, fallback=False)
    assert missing == 0 and "譯文。" in out


@pytest.mark.parametrize("blank", ["", "   ", "\n", "　"])
def test_reseat_leaves_a_blank_target_blank_rather_than_dressing_it_in_an_indent(blank):
    """The guard the mutation pass found, pinned at its own level now.

    `do_apply` was the only caller that could reach it with a blank text and it
    refuses one at the door since `contract_version = 2`, so the property is
    asserted directly rather than through a path that can no longer produce it —
    the alternative was letting a guard nothing exercises rot until someone
    deletes it as dead. Reseated, a cleared target becomes `"    "`, which is
    *truthy*: `render` would emit four spaces where the untranslated marker
    belongs and report nothing missing.
    """
    assert reseat_outer_blanks("    A second paragraph.", blank,
                               keep_added_indent=True) == blank


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
    parsed, _r, _j, _d = do_extract(name, "zh-TW", CFG)
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
    parsed, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
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
    parsed, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
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
    parsed, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
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
    doc, _r, _j, _d = do_extract("d.md", "zh-TW", CFG)
    seg = next(s for s in doc["segments"] if s["source"].startswith("    "))
    assert seg["masked"] == "    Run ⟦1⟧ now."
    assert accept(seg, "現在執行。", "zh-TW", CFG)[0] is None
    assert do_apply("d.md", "zh-TW", CFG, {seg["id"]: "現在執行。"})[:2] == (1, [])
    stored = next(s for s in load_doc("d.md", "zh-TW")["segments"] if s["id"] == seg["id"])
    assert stored["target"] == "    現在執行。"


# ── what `lx commit` may put in a source of truth ───────────────────────────
#
# `.lx/tm.*.jsonl` is tracked in git, `store.load_tm` keeps the LAST record per
# key, and nothing is ever deleted from it. So a banked wording does not merely
# sit there being useless — it hides the good one already under that key.
# Decided 2026-09-01: what `lx check` calls an error is not banked, and neither
# is a hold.


def _commit(src="d.md", lang="zh-TW"):
    return do_commit(src, lang, CFG)


def test_a_broken_wording_does_not_hide_the_good_one_already_banked(
        tmp_path, monkeypatch):
    """The measured harm, and the reason the status quo could not stand.

    A correct wording is banked from one document. A second document holds
    wording a person typed that names the protected term twice — `lx apply`
    takes it, `lx check` reports it — and committing that document used to
    append it under the same key, where `load_tm`'s last-write-wins made it the
    answer. A third, brand-new document then found nothing usable, with the
    right sentence one line up in the file and unreachable.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=b"Celurion ships.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    do_apply("d.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 出貨。"}, origin="human")
    assert _commit()[0] == 1
    good = list(load_tm("zh-TW").values())[0]["target"]

    (tmp_path / "b.md").write_bytes(b"Celurion ships.\n")
    doc, *_ = do_extract("b.md", "zh-TW", CFG)
    do_apply("b.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧ 出貨，⟦1⟧ 準時。"},
             origin="human")
    committed, refused, held, stranded = _commit("b.md")
    assert (committed, refused, held, stranded) == (0, [_only(doc)["id"]], [], [])
    assert [r["target"] for r in load_tm("zh-TW").values()] == [good], \
        "the memory still answers with the wording that fits"

    (tmp_path / "c.md").write_bytes(b"Celurion ships.\n")
    doc, reused, rejected, _notes = do_extract("c.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0), "a third document still finds it"
    assert _only(doc)["target"] == good


def test_the_gate_is_lx_checks_rule_and_not_a_second_copy_of_half_of_it(
        tmp_path, monkeypatch):
    """A swapped pair is what decided which rule the gate reads.

    `translate.accept` compares placeholder ids as a *multiset*, so
    `⟦2⟧粗體⟦1⟧` against `<b>bold</b>` satisfies it — it is accepted, it renders
    `</b>粗體<b>`, and it reaches a second document intact. `checks.pair_problems`
    is what sees it, at the same `tags` rule and the same error severity. A gate
    written as its own placeholder comparison in `store.tm_records` would bank
    this; the gate that asks `checks.py` does not.
    """
    _project(tmp_path, monkeypatch, doc=b"This is <b>bold</b> text.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    seg = _only(doc)
    assert seg["masked"] == "This is ⟦1⟧bold⟦2⟧ text."
    crossed = "這是⟦2⟧粗體⟦1⟧文字。"
    assert accept(seg, crossed, "zh-TW", CFG)[0] is not None, \
        "the acceptance path cannot see this, which is the point"

    do_apply("d.md", "zh-TW", CFG, {seg["id"]: crossed}, origin="human")
    assert _commit() == (0, [seg["id"]], [], [])
    assert load_tm("zh-TW") == {}


def test_a_held_segment_is_not_banked_and_an_unhold_is_all_it_takes(
        tmp_path, monkeypatch):
    """A hold is the only thing in a commit request that can say "not this one".

    `lx commit` takes a whole document and has no per-segment selection, so the
    reviewer's own declaration that a segment is theirs to finish is what the
    batch act has to read. Nothing is lost: `tm_records` re-derives from the live
    segment every time, so the wording is eligible again the moment the hold
    lifts.
    """
    _project(tmp_path, monkeypatch, doc=b"Alpha.\n\nBeta.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    a, b = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", CFG, {a: "甲。", b: "乙。"}, origin="human")
    do_hold("d.md", "zh-TW", CFG, [b])

    assert _commit() == (1, [], [b], [])
    assert [r["target"] for r in load_tm("zh-TW").values()] == ["甲。"]

    do_hold("d.md", "zh-TW", CFG, [b], held=False)
    assert _commit() == (1, [], [], [])
    assert sorted(r["target"] for r in load_tm("zh-TW").values()) == ["乙。", "甲。"]


def test_a_segment_that_is_both_held_and_failing_is_reported_as_held(
        tmp_path, monkeypatch):
    """One id, one list, and it is the list whose remedy comes first.

    Both answers are true; "unhold it" is the useful one, because a reviewer who
    is told only that the wording fails will fix it and find it still not banked.
    """
    _project(tmp_path, monkeypatch, dnt="Celurion\n", doc=b"Celurion ships.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧ 出貨，⟦1⟧ 準時。"}, origin="human")
    do_hold("d.md", "zh-TW", CFG, [sid])
    assert _commit() == (0, [], [sid], [])


def test_the_gate_honours_a_rule_the_project_turned_off(tmp_path, monkeypatch):
    """One rule, one home, one disable list.

    `numbers` is an error and 「三天」 for "3 days" trips it — correct Chinese for
    a novel, and the reason the gate must not carry its own opinion. A project
    that decides the rule is wrong for it says so once, and the gate agrees by
    construction rather than by a second exception list.
    """
    _project(tmp_path, monkeypatch, doc=b"He waited 3 days.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    do_apply("d.md", "zh-TW", CFG, {sid: "他等了三天。"}, origin="human")
    assert _commit() == (0, [sid], [], [])

    cfg = dict(CFG, checks_disabled=["numbers"])
    assert do_commit("d.md", "zh-TW", cfg) == (1, [], [], [])


# ── which map a stored wording's ⟦n⟧ actually mean ──────────────────────────


def test_a_stranded_wording_renders_the_words_that_were_written(
        tmp_path, monkeypatch):
    """The silent half of divergence (24)'s cost, measured and closed.

    A `config/dnt.txt` edit that *swaps* one protected term for another leaves
    the placeholder ids equal on both sides, so every gate that compares ids is
    blind to it: `mask.reseat` refuses (it cannot find "met" in the Chinese), the
    wording is kept, and `lx check` passes. Rendering it against the segment's
    own map named the wrong entity — `Alpha 遇見 met。` where the reviewer wrote
    `Beta` — with `missing` 0 and nothing anywhere reporting it.

    `cli.do_extract` already pins the map the wording's ids mean. The render
    reads it now, so the bytes are the reviewer's own words, and the `numbering`
    rule says the source has moved under them.
    """
    _project(tmp_path, monkeypatch, dnt="Alpha\nBeta\n", doc=b"Alpha met Beta.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    assert _only(doc)["masked"] == "⟦1⟧ met ⟦2⟧."
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧遇見⟦2⟧。"}, origin="human")

    (tmp_path / "config" / "dnt.txt").write_text("Alpha\nmet\n", encoding="utf-8")
    doc, reused, rejected, notes = do_extract("d.md", "zh-TW", CFG)
    assert (reused, rejected) == (0, 1) and notes["kept"] == [sid]
    assert _only(doc)["masked"] == "⟦1⟧ ⟦2⟧ Beta."

    text, missing = do_render("d.md", "zh-TW", CFG)
    assert text == "Alpha 遇見 Beta。\n", "the wording is rendered as it was written"
    assert missing == 0
    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 0
    assert report["by_rule"] == {"numbering": 1}, "silent is what it must not be"


def test_a_corrective_apply_does_not_inherit_the_map_it_replaced(
        tmp_path, monkeypatch):
    """`store.save_segments` clears `target_slots`, as `store.save_targets` does.

    The wording being written is written against the segment as it stands, so an
    older target's map is not its provenance. Left behind it was wrong three
    ways at once, all measured 2026-09-01: the render unmasked the corrected
    wording against the map it was written to replace, `store.tm_record` banked
    a `slots` array naming originals the ids do not mean — into a source of truth
    — and `store.prior_targets` would hand `translate.accept` the wrong map at
    the next extract.
    """
    _project(tmp_path, monkeypatch, dnt="Alpha\nBeta\n", doc=b"Alpha met Beta.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧遇見⟦2⟧。"}, origin="human")
    (tmp_path / "config" / "dnt.txt").write_text("Alpha\nmet\n", encoding="utf-8")
    do_extract("d.md", "zh-TW", CFG)
    assert load_doc("d.md", "zh-TW")["segments"][0].get("target_slots")

    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧⟦2⟧了 Beta。"}, origin="human")
    stored = load_doc("d.md", "zh-TW")["segments"][0]
    assert "target_slots" not in stored
    assert do_render("d.md", "zh-TW", CFG)[0] == "Alphamet 了 Beta。\n"
    assert tm_record(stored)["slots"] == ["Alpha", "met"]

def test_re_applying_a_stranded_segments_own_words_changes_nothing(
        tmp_path, monkeypatch):
    """Found by the adversarial pass, 2026-09-01, and it is the whole reason the
    `target_slots` strip is conditional.

    `store.save_targets` pops the map unconditionally and is right to: its text
    has been through `translate.accept` against the current segment. `lx apply`
    takes whatever it is handed — and an agent's whole-document round trip hands
    back every segment, unchanged ones included. An unconditional pop there
    un-strands a segment nobody edited: the render flips to the wrong original
    and the `numbering` warning that was the only report of it disappears, with
    `lx check` green on both sides of the act.
    """
    _project(tmp_path, monkeypatch, dnt="Alpha\nBeta\n", doc=b"Alpha met Beta.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧遇見⟦2⟧。"}, origin="human")
    (tmp_path / "config" / "dnt.txt").write_text("Alpha\nmet\n", encoding="utf-8")
    do_extract("d.md", "zh-TW", CFG)

    stored = load_doc("d.md", "zh-TW")["segments"][0]
    assert stored.get("target_slots")
    do_apply("d.md", "zh-TW", CFG, {sid: stored["target"]}, origin="human")

    after = load_doc("d.md", "zh-TW")["segments"][0]
    assert after.get("target_slots"), "a no-op re-apply must not un-strand it"
    assert do_render("d.md", "zh-TW", CFG)[0] == "Alpha 遇見 Beta。\n"
    assert do_check("d.md", "zh-TW", CFG)[0]["by_rule"] == {"numbering": 1}


def test_a_stranded_wording_is_not_banked_over_one_that_fits(tmp_path, monkeypatch):
    """The `numbering` rule is warn, so the error gate cannot see this one.

    Found by the adversarial pass, 2026-09-01: a stranded wording renders
    correctly and passes `lx check`, so it went into the memory — where it
    shadowed a wording banked under the same key that *did* speak the current
    numbering, and the next document came back with nothing usable. The memory is
    read by every document in this project under the numbering the project has
    now; a wording that does not speak it does not belong there yet.
    """
    _project(tmp_path, monkeypatch, dnt="Alpha\nBeta\n",
             doc=b"Alpha met Beta.\n", name="a.md")
    doc, *_ = do_extract("a.md", "zh-TW", CFG)
    do_apply("a.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧遇見⟦2⟧。"}, origin="human")
    assert _commit("a.md")[0] == 1

    (tmp_path / "config" / "dnt.txt").write_text("Alpha\nmet\n", encoding="utf-8")
    (tmp_path / "b.md").write_bytes(b"Alpha met Beta.\n")
    doc, *_ = do_extract("b.md", "zh-TW", CFG)
    do_apply("b.md", "zh-TW", CFG, {_only(doc)["id"]: "⟦1⟧⟦2⟧了 Beta。"}, origin="human")
    assert _commit("b.md")[0] == 1
    good = "⟦1⟧⟦2⟧了 Beta。"

    do_extract("a.md", "zh-TW", CFG)
    sid = load_doc("a.md", "zh-TW")["segments"][0]["id"]
    assert do_check("a.md", "zh-TW", CFG)[0]["errors"] == 0, "warn, not error"
    assert _commit("a.md") == (0, [], [], [sid])
    assert [r["target"] for r in load_tm("zh-TW").values()] == [good]

    (tmp_path / "c.md").write_bytes(b"Alpha met Beta.\n")
    doc, reused, rejected, _notes = do_extract("c.md", "zh-TW", CFG)
    assert (reused, rejected) == (1, 0), "the good record still answers"


def test_containment_reads_the_target_against_the_map_the_render_uses(
        tmp_path, monkeypatch):
    """Found by the adversarial pass, 2026-09-01, and it is invariant 2b's own rule.

    `checks.containment_problems` asks what a target does to the block it lands
    in, answered on the unmasked text *because that is what reaches the file*.
    When the render moved to `target_slots` and this rule did not, a stranded
    segment rendering `Note 說。` was failed at error severity for opening a list
    — which the rendered line does not do — and `lx run` then refused to render a
    document that renders correctly, while `lx commit` refused to bank it.
    """
    _project(tmp_path, monkeypatch, dnt="Note\n", doc=b"Say - Note here.\n")
    doc, *_ = do_extract("d.md", "zh-TW", CFG)
    sid = _only(doc)["id"]
    assert _only(doc)["masked"] == "Say - ⟦1⟧ here."
    do_apply("d.md", "zh-TW", CFG, {sid: "⟦1⟧ 說。"}, origin="human")

    (tmp_path / "config" / "dnt.txt").write_text("- Note\n", encoding="utf-8")
    do_extract("d.md", "zh-TW", CFG)
    text, _missing = do_render("d.md", "zh-TW", CFG)
    assert text == "Note 說。\n", "the rendered line opens no list"
    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 0 and report["by_rule"] == {"numbering": 1}
