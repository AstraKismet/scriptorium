"""A reviewer answering one segment's report, and the four places it must not reach.

`lx waive` exists because a mechanically correct rule can be wrong about one
sentence and right about every other, and until 2026-09-03 the only way to say so
was `checks_disabled`, which turns the rule off for the whole project. The
measurement that opened it: against a source masking a repeated protected term,
four of six shapes of *correct* Traditional Chinese trip `tags` at error
severity, and no mechanical rule separates them from the two that are genuinely
broken — at the level the rule can see, both are a lost placeholder.

What is tested here is mostly what a waiver **cannot** do, because that is where
it would do harm:

* it cannot reach an issue about the substituted **bytes** — a dangling pair
  half, a crossed pair, an id the segment has no slot for, containment, escaping,
  the invented carriage return — since judgement is not a second opinion on
  whether a tag closes;
* it cannot outlive the wording it was granted on;
* it cannot delete a hold, which is the measured reason it is its own body key
  rather than a second `review` value;
* and it cannot travel through the translation memory, where it would answer a
  report on behalf of a reader who has never seen the document.
"""

import ast
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import checks, cli  # noqa: E402
from scriptorium.checks import check_segment, is_held, is_waived  # noqa: E402
from scriptorium.cli import (  # noqa: E402
    UnusableTarget,
    do_apply,
    do_check,
    do_commit,
    do_extract,
    do_hold,
    do_status,
    do_waive,
)
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.store import (  # noqa: E402
    load_doc,
    save_targets,
    target_token,
    tm_path,
)

CFG = dict(DEFAULT_CONFIG)

#: Two protected mentions of one name in the first paragraph, which is the shape
#: Chinese prose renders once and English repeats.
DOC = (b"Ana waited at the gate. Ana did not move.\n"
       b"\n"
       b"The hill was quiet.\n")

#: Correct Traditional Chinese for the first paragraph, and it drops the second
#: placeholder because the sentence has already named her. `tags` reports it.
FOLDED = "⟦1⟧在門邊等著，一動也不動。"


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A two-paragraph project whose first segment a person folded."""
    root = tmp_path / "book"
    (root / "config").mkdir(parents=True)
    (root / "config" / "dnt.txt").write_text("Ana\n", encoding="utf-8")
    (root / "d.md").write_bytes(DOC)
    monkeypatch.chdir(root)
    do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]]
    do_apply("d.md", "zh-TW", CFG,
             {ids[0]: FOLDED, ids[1]: "山丘很安靜。"}, origin="human")
    return ids


def _rules(src="d.md", lang="zh-TW"):
    report, _ = do_check(src, lang, CFG)
    return sorted((i["severity"], i["rule"]) for i in report["issues"])


def _seg(sid, masked, target, slots=None):
    out = {"id": sid, "masked": masked, "target": target,
           "kind": "para", "host": "markdown"}
    if slots:
        out["slots"] = slots
    return out


_OPEN = {"original": "<em>", "role": "open", "pair_id": "p", "can_reorder": True}
_CLOSE = {"original": "</em>", "role": "close", "pair_id": "p", "can_reorder": True}
_TERM = {"original": "Ana", "role": "term", "pair_id": None, "can_reorder": True}


def _untranslated():
    """The id of a segment `d.md` holds no wording for.

    Appended to the fixture document rather than left untranslated in it, so the
    other tests here still see exactly two segments and one error.
    """
    with open("d.md", "ab") as f:
        f.write(b"\nA line nobody has translated.\n")
    do_extract("d.md", "zh-TW", CFG)
    return [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]
            if not (s.get("target") or "").strip()][0]


# ── what a waiver does ──────────────────────────────────────────────────────

def test_a_waived_segment_does_not_fail_the_build(book):
    """The whole feature, in one assertion pair.

    Before: an error the reviewer disagrees with, and an exit code stuck at 1 for
    the life of the document. After: the same finding, still printed, still
    counted — under `warnings`, where it does not stop `lx run` rendering.
    """
    before, _ = do_check("d.md", "zh-TW", CFG)
    assert before["errors"] == 1 and before["by_rule"]["tags"] == 1

    assert do_waive("d.md", "zh-TW", CFG, [book[0]]) == (1, [], [])

    after, _ = do_check("d.md", "zh-TW", CFG)
    assert after["errors"] == 0
    # Downgraded, never removed: the `tags` finding is still in the report and
    # still under its own rule name, and a `waived` warning names the segment so
    # a reader can tell a waived warn from an ordinary one.
    assert after["by_rule"] == {"tags": 1, "waived": 1}
    assert after["warnings"] == 2


def test_a_waived_wording_is_banked_and_its_memory_line_says_so(book):
    """`lx commit` needs no rule of its own, and the line carries the mark.

    The bank gate *is* `checks.check_segment` at error severity, so a waiver
    moving those issues to warn takes the segment through the gate that already
    exists — one rule, one home. What the memory then has to do is tell the next
    document, because the waiver itself does not travel.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    committed, refused, held, stranded = do_commit("d.md", "zh-TW", CFG)
    assert (committed, refused, held, stranded) == (2, [], [], [])

    lines = [json.loads(ln) for ln in
             open(tm_path("zh-TW"), encoding="utf-8").read().splitlines() if ln]
    waived = [rec for rec in lines if rec.get("waived")]
    assert [rec["target"] for rec in waived] == [FOLDED]
    # And only the waived one is marked: the field is written when true and
    # omitted otherwise, so a memory with no waivers is the file it always was.
    assert sum("waived" in rec for rec in lines) == 1


def test_the_waiver_does_not_travel_with_the_wording_it_banked(book, tmp_path):
    """A second document takes the wording, unwaived, and is told.

    One reviewer's judgement about one position is not a judgement about a
    document they have never seen. So the hit lands, `lx check` reports it where
    it landed, and `lx extract` names the segment rather than leaving the next
    reader to discover it from a check they did not expect to fail.

    The wording here trips `lexicon` and not `tags`, and that is the population
    this path actually has rather than a convenience. A waived wording that
    dropped a placeholder is refused by `translate.accept` at the receiving end —
    the multiset gate, which knows nothing about waivers and should not — so it
    never lands anywhere to be named. What reaches a second document is a waiver
    over a rule that judges the *text*: `lexicon`, `glossary`, `numbers`, and the
    advisory ones. Measured while writing this test.
    """
    (tmp_path / "book" / "g.md").write_bytes(b"Check the network.\n")
    do_extract("g.md", "zh-TW", CFG)
    gid = load_doc("g.md", "zh-TW")["segments"][0]["id"]
    # Correct for a novel quoting a speaker who says it that way, and an error
    # the zh-TW lexicon is right to report in every other paragraph.
    do_apply("g.md", "zh-TW", CFG, {gid: "看一下網絡。"}, origin="human")
    assert _rules("g.md") == [("error", "lexicon")]
    do_waive("g.md", "zh-TW", CFG, [gid])
    assert do_commit("g.md", "zh-TW", CFG)[0] == 1

    (tmp_path / "book" / "e.md").write_bytes(b"Check the network.\n")
    _doc, reused, _rejected, notes = do_extract("e.md", "zh-TW", CFG)
    assert reused == 1

    fresh = {s["id"]: s for s in load_doc("e.md", "zh-TW")["segments"]}
    took = notes["waived_source"]
    assert len(took) == 1
    assert fresh[took[0]]["target"] == "看一下網絡。"
    # It arrives unwaived, so this document's own check reports it and this
    # reader decides. That is the whole of what the memory mark buys.
    assert not is_waived(fresh[took[0]])
    assert _rules("e.md") == [("error", "lexicon")]


def test_the_status_surface_counts_a_waiver_without_a_check(book):
    """`waived` is state and not a finding, so it does not wait for `lx check`.

    Read off the live segments beside `held`. Read from the persisted report
    instead it would inherit `_check`'s staleness hole — placing a waiver moves
    neither of the two integers `stale` compares — and answer `waived: 0` on a
    document that had just waived one.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    project = do_status(CFG)["projects"][0]
    assert project["documents"][0]["waived"] == 1
    assert project["totals"]["waived"] == 1
    assert project["documents"][0]["check"] is None


# ── what a waiver may not reach ─────────────────────────────────────────────

@pytest.mark.parametrize("name,seg", [
    ("one half of a pair is gone",
     _seg("s", "This is ⟦1⟧very⟦2⟧ important.", "這是⟦1⟧非常重要的。",
          {"1": _OPEN, "2": _CLOSE})),
    ("the pair is inverted",
     _seg("s", "⟦1⟧bold⟦2⟧ text", "⟦2⟧粗體⟦1⟧文字", {"1": _OPEN, "2": _CLOSE})),
    ("an id the segment has no slot for",
     _seg("s", "⟦1⟧bold⟦2⟧ text", "⟦1⟧粗體⟦2⟧文字⟦99⟧", {"1": _OPEN, "2": _CLOSE})),
    ("a block-start sequence the target invents",
     _seg("s", "A line.", "- 一行。")),
    ("a carriage return the target invents",
     _seg("s", "A line.", "一行。\r")),
    # No format produces an XML host today — `escaping` activates with EPUB —
    # so this shape is unreachable through `lx extract` and reachable here.
    # Covered anyway, because the rule ships and a mutation round found it was
    # the one unwaivable answer nothing asserted.
    ("a raw '<' in an XML host",
     dict(_seg("s", "A line.", "一行 < 兩行。"), host="xml")),
    ("a raw '&' in an XML host",
     dict(_seg("s", "A line.", "一行 & 兩行。"), host="xhtml")),
])
def test_a_waiver_cannot_reach_an_issue_about_the_bytes(name, seg):
    """The line the whole design is drawn on, case by case.

    Each of these renders malformed bytes — an `<em>` that never closes,
    `</em>粗體<em>`, a literal `⟦99⟧` in the file, a paragraph that became a list
    item, a terminator the document did not decide. A reviewer is a second
    opinion on a sentence and never on any of that, so the waiver leaves them at
    error and the build still stops.
    """
    waived = check_segment(dict(seg, waived=True), "zh-TW", CFG, [], ["Ana"])
    assert any(i["severity"] == "error" for i in waived), name
    # And the marker is still emitted, so the report says a waiver is in force
    # even where it changed nothing.
    assert any(i["rule"] == "waived" for i in waived), name


def test_a_waiver_reaches_the_shapes_whose_bytes_are_well_formed():
    """The other side of the same line, so the parametrized test above is not
    passing by refusing everything.

    A term dropped, and a whole pair dropped together: in both the unmasking is
    ordinary prose that may be missing something, which is the judgement call a
    reviewer exists for.
    """
    for seg in (_seg("s", "⟦1⟧ waited. ⟦2⟧ left.", "⟦1⟧等著，然後離開。",
                     {"1": _TERM, "2": _TERM}),
                _seg("s", "She ⟦1⟧whispered⟦2⟧ it.", "她悄聲說了。",
                     {"1": _OPEN, "2": _CLOSE})):
        assert any(i["severity"] == "error"
                   for i in check_segment(seg, "zh-TW", CFG, [], ["Ana"]))
        assert not any(i["severity"] == "error" for i in
                       check_segment(dict(seg, waived=True), "zh-TW", CFG, [], ["Ana"]))


def test_only_an_error_is_downgraded_whatever_severity_a_row_carries():
    """The `sev == "error"` half of the downgrade, which a mutation round found
    nothing asserting.

    It looks like a no-op — downgrading a warning to a warning changes nothing —
    and it is not, because two severities are **data**: `glossary` passes column
    four of `config/glossary.csv` through unvalidated and `lexicon_extra` does the
    same, so a hand-edited configuration can put any string there, and the
    workbench contract says so out loud. Without this clause a waiver would
    rewrite such a row to `warn`, quietly narrowing a severity the project chose.
    """
    row = {"source": "gate", "target": "大門", "forbidden": [], "severity": "critical"}
    seg = _seg("s", "The gate stood open.", "門開著。")
    for waived in (False, True):
        found = check_segment(dict(seg, waived=waived), "zh-TW", CFG, [row], [])
        assert ("critical", "glossary") in [(i["severity"], i["rule"]) for i in found]


def test_lifting_a_waiver_removes_the_key_rather_than_storing_false(book):
    """One row for "never waived" and "waiver lifted", the rule `save_review`
    follows.

    `is_waived` reads `bool(...)`, so a stored `false` would behave the same and
    a mutation round says no test could tell them apart. It still matters: the
    body blob is compared and diffed, and two spellings of one state is how a
    later reader comes to believe there are two.
    """
    import sqlite3

    from scriptorium.store import db_path, doc_id, save_waived

    def body(sid):
        conn = sqlite3.connect(db_path())
        try:
            row = conn.execute("SELECT body FROM segments WHERE doc_id=? AND seg_id=?",
                               (doc_id("d.md"), sid)).fetchone()
        finally:
            conn.close()
        return json.loads(row[0])

    ids = [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]]
    assert "waived" not in body(ids[0])
    save_waived("d.md", "zh-TW", {ids[0]: True})
    # The stored value is the token of the wording it was granted over, never a
    # bare `true`: `store._segment` compares it on the way out, so a waiver
    # cannot outlive the sentence a reviewer read even under a build that does
    # not know the field. What a reader outside `store` sees is still a boolean.
    assert body(ids[0])["waived"] == target_token(FOLDED)
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0]) is True
    save_waived("d.md", "zh-TW", {ids[0]: False})
    assert "waived" not in body(ids[0])


def test_a_waiver_survives_a_carryover_the_acceptance_path_takes(book, tmp_path):
    """The branch the other carryover test does not reach.

    `test_a_waiver_survives_the_re_extract_every_run_performs` exercises the
    *kept* branch, because the wording it waives drops a placeholder and
    `translate.accept` refuses it. A waiver over a rule that judges the text —
    `lexicon` here — is accepted instead and lands through the winning-candidate
    branch, which is a different line and had no test until a mutation round
    removed it and nothing failed.
    """
    (tmp_path / "book" / "h.md").write_bytes(b"Check the network.\n")
    do_extract("h.md", "zh-TW", CFG)
    hid = load_doc("h.md", "zh-TW")["segments"][0]["id"]
    do_apply("h.md", "zh-TW", CFG, {hid: "看一下網絡。"}, origin="human")
    do_waive("h.md", "zh-TW", CFG, [hid])

    _doc, reused, _rejected, _notes = do_extract("h.md", "zh-TW", CFG)
    assert reused == 1
    assert is_waived(load_doc("h.md", "zh-TW")["segments"][0])
    report, _ = do_check("h.md", "zh-TW", CFG)
    assert report["errors"] == 0


def test_a_tag_half_whose_partner_is_in_another_segment_is_not_waivable(tmp_path,
                                                                       monkeypatch):
    """The hole the first version of this feature shipped with.

    `mask._pair_tags` pairs only *within* a segment — its own docstring names
    "a `<div>` whose partner is in another block" as ordinary input — so a
    `<span>` opened in one paragraph and closed in the next leaves both halves
    `role: "standalone", pair_id: None`. The first predicate keyed on `pair_id`
    and called that waivable. Measured 2026-09-03 on this exact file: waive the
    closing segment, `lx check` exits **0**, and `lx render` writes an unclosed
    `<span class="note">` into the book. A `<div>` around several paragraphs is
    the commonest shape in technical source and in EPUB, so it is not a corner.
    """
    root = tmp_path / "span"
    (root / "config").mkdir(parents=True)
    (root / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (root / "s.md").write_bytes(
        b'Intro.\n\n<span class="note">First warning.\n\nSecond warning.</span>\n')
    monkeypatch.chdir(root)
    do_extract("s.md", "zh-TW", CFG)
    segs = load_doc("s.md", "zh-TW")["segments"]
    # The premise, asserted rather than assumed: both halves are standalone.
    holders = [s for s in segs if s.get("slots")]
    assert len(holders) == 2
    assert all(rec["pair_id"] is None for s in holders for rec in s["slots"].values())

    ids = [s["id"] for s in segs]
    do_apply("s.md", "zh-TW", CFG,
             {ids[0]: "引言。", ids[1]: "⟦1⟧第一段警語。",
              ids[2]: "第二段警語。"}, origin="human")
    assert _rules("s.md") == [("error", "tags")]
    do_waive("s.md", "zh-TW", CFG, [ids[2]])
    report, _ = do_check("s.md", "zh-TW", CFG)
    assert report["errors"] == 1, "a lost tag half is not a reviewer's to overrule"


def test_a_repeated_placeholder_is_waivable_when_repeating_it_is_legal():
    """The mirror direction, which the first predicate got wrong the other way.

    English says a code span once and Chinese may want it twice. `mask.unmask`
    substitutes both, the bytes are ordinary Markdown, and the reviewer is
    exactly the right authority — but `waivable = not extra` refused it, so
    `lx run` permanently declined a correct document and the waiver could not
    reach it. Repeating a *tag* is a different thing and stays unwaivable:
    `⟦1⟧x⟦1⟧` over an `<b>` renders `<b>x<b>`.
    """
    code = {"original": "`make`", "role": "standalone", "pair_id": None,
            "can_reorder": True}
    legal = _seg("s", "Run ⟦1⟧ first.", "請先執行 ⟦1⟧；⟦1⟧ 就是它。", {"1": code})
    assert any(i["severity"] == "error"
               for i in check_segment(legal, "zh-TW", CFG, [], []))
    assert not any(i["severity"] == "error" for i in
                   check_segment(dict(legal, waived=True), "zh-TW", CFG, [], []))

    broken = _seg("s", "⟦1⟧bold⟦2⟧", "⟦1⟧粗⟦1⟧體⟦2⟧", {"1": _OPEN, "2": _CLOSE})
    assert any(i["severity"] == "error" for i in
               check_segment(dict(broken, waived=True), "zh-TW", CFG, [], []))


def test_a_waiver_stored_against_other_wording_is_not_in_force(book):
    """The guarantee that does not depend on a writer remembering.

    The two writers drop the flag when the target moves, which is enough while
    every write goes through this build. It is not enough across builds: one
    without the field writes a new target and leaves the flag standing, and a
    stale waiver is *fail-open* where a stale hold is fail-safe — it moves the
    exit code invariant 10 rests on. So the stored value is the token of the
    wording it was granted over and `store._segment` compares it on the way out.
    Simulated here by writing the row the way an older build would leave it.
    """
    import sqlite3

    from scriptorium.store import db_path, doc_id

    do_waive("d.md", "zh-TW", CFG, [book[0]])
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0])

    conn = sqlite3.connect(db_path())
    try:
        with conn:
            conn.execute("UPDATE segments SET target=? WHERE doc_id=? AND seg_id=?",
                         ("⟦1⟧在門邊等了很久。", doc_id("d.md"), book[0]))
    finally:
        conn.close()
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])
    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 1


def test_a_wording_that_moved_under_the_reviewer_is_refused_rather_than_waived(book):
    """The compare-and-swap, which `POST /api/save` has and this needed too.

    `do_waive` reads the document, a reviewer reads the wording, and the write
    lands later. A translation batch committing in between used to leave the
    waiver on a sentence nobody had seen, with `lx check` green over it —
    measured 2026-09-03 with two threads. The named ids come back in `stale`
    rather than being applied to whatever the row holds now.
    """
    from scriptorium.store import save_waived

    applied, stale = save_waived("d.md", "zh-TW", {book[0]: True},
                                 expect={book[0]: "wording nobody stored"})
    assert (applied, stale) == (0, [book[0]])
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])

    applied, stale = save_waived("d.md", "zh-TW", {book[0]: True},
                                 expect={book[0]: FOLDED})
    assert (applied, stale) == (1, [])


def test_waiving_a_segment_with_nothing_to_answer_is_refused(book):
    """A waiver answers a finding; it is not a mark of approval.

    Left open, it reached `store.tm_record` and put a line in the tracked memory
    claiming a reviewer had overruled a rule that never fired — and toggling one
    appended a full duplicate line per commit, six for one wording in the
    measured run. Both found 2026-09-03 by the adversarial pass.
    """
    with pytest.raises(UnusableTarget) as caught:
        do_waive("d.md", "zh-TW", CFG, [book[1]])
    assert "nothing to stand by" in str(caught.value)
    assert "lx hold" in str(caught.value), "say what the reviewer wanted instead"

    # Whole-request: the failing segment beside it is not waived either.
    with pytest.raises(UnusableTarget):
        do_waive("d.md", "zh-TW", CFG, [book[0], book[1]])
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])

    # And re-affirming a waiver already in force is not refused by the state it
    # put there: the check is run with the flag forced off.
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    assert do_waive("d.md", "zh-TW", CFG, [book[0]]) == (0, [], [])


def test_the_memory_mark_does_not_come_off_the_wording_it_belongs_to(book):
    """The mark is the whole payment for banking a waived wording, and it leaked.

    `tm_record` can only read the segment in front of it, and every document
    after the first is a *different* segment that this build deliberately leaves
    unwaived. So the first commit without a waiver erased the field from the
    tracked file. Measured 2026-09-03 in one project: waive → commit → unwaive →
    commit, and the mark was gone.

    Fixing it also stops a reviewer's flag churning a source of truth: the record
    no longer differs by that field alone, so the toggle appends nothing.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    assert do_commit("d.md", "zh-TW", CFG)[0] == 2

    def lines():
        return [json.loads(ln) for ln in
                open(tm_path("zh-TW"), encoding="utf-8").read().splitlines() if ln]

    assert sum(rec.get("waived") is True for rec in lines()) == 1
    before = len(lines())

    do_waive("d.md", "zh-TW", CFG, [book[0]], waived=False)
    do_commit("d.md", "zh-TW", CFG)
    assert sum(rec.get("waived") is True for rec in lines()) == 1
    assert len(lines()) == before, "a toggled flag is not a new wording"


def test_do_waive_gives_the_writer_the_wording_it_read(book, monkeypatch):
    """The compare-and-swap is only a guard if the caller hands it the snapshot.

    Its own test calls `store.save_waived` directly, so a mutation round could
    replace `do_waive`'s `expect=` with `None` and nothing failed — the guard was
    there and unwired. Reproduced here without threads by making the read stale
    the way a translation batch makes it stale: `do_waive` sees the wording the
    reviewer read while the row already holds the next one. The observable state
    is identical and the test is deterministic.
    """
    stale_doc = load_doc("d.md", "zh-TW")
    save_targets("d.md", "zh-TW", {book[0]: "⟦1⟧在門邊等了很久。"}, origin="agent")
    monkeypatch.setattr(cli, "load_doc", lambda *a, **k: stale_doc)

    applied, unknown, stale = do_waive("d.md", "zh-TW", CFG, [book[0]])
    assert (applied, unknown, stale) == (0, [], [book[0]])
    # Not `monkeypatch.undo()`: the fixture's `chdir` rode in on the same
    # instance, and undoing it here moves the working directory out of the
    # project. The module-level `load_doc` is `store`'s and was never patched.
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])


def test_the_memory_mark_survives_a_commit_from_a_document_without_a_waiver(book):
    """The laundering path that remains once a clean segment cannot be waived.

    A waiver answers a finding, so while the finding stands every commit of that
    wording is a waived one. The mark comes off when the finding stops standing —
    a project adds a `lexicon_extra` row downgrading the term, say — and the same
    wording is then banked by a document with no waiver of its own. That is the
    ordinary shape of the measured defect: the mark is the whole payment for
    banking a waived wording, and it survived exactly one such commit.
    """
    (open("m.md", "wb")).write(b"Check the network.\n")
    do_extract("m.md", "zh-TW", CFG)
    mid = load_doc("m.md", "zh-TW")["segments"][0]["id"]
    do_apply("m.md", "zh-TW", CFG, {mid: "看一下網絡。"}, origin="human")
    do_waive("m.md", "zh-TW", CFG, [mid])
    assert do_commit("m.md", "zh-TW", CFG)[0] == 1

    def lines():
        return [json.loads(ln) for ln in
                open(tm_path("zh-TW"), encoding="utf-8").read().splitlines() if ln]

    assert [rec.get("waived") for rec in lines()] == [True]

    # The project decides that term is theirs to keep, so the finding stops
    # standing and the waiver comes off.
    relaxed = {**CFG, "lexicon_extra": {"網絡": ["網路", "warn"]}}
    do_waive("m.md", "zh-TW", relaxed, [mid], waived=False)
    assert do_commit("m.md", "zh-TW", relaxed)[0] == 0, "the record did not change"
    assert [rec.get("waived") for rec in lines()] == [True]


def test_the_terminal_status_names_a_waiver_beside_the_counts(book, capsys):
    """The half the first version shipped only to the machine-readable surface.

    `errors: 0` on a document carrying a waiver is a person's judgement rather
    than a clean sweep, and this is the surface a maintainer looks at before
    saying the book is done.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    do_check("d.md", "zh-TW", CFG)
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "1 waived" in out, out


def test_every_finding_declares_whether_it_may_be_waived():
    """The guard that keeps the design honest, read with `ast`.

    There is no list of waivable rule names anywhere, deliberately — this
    repository has recorded five times what happens when an enumeration becomes
    what a reader trusts. The answer lives beside the severity at each call site
    and the argument is **required**, so a rule added later cannot inherit one by
    omission. This asserts that property rather than the answers, because the
    answers are judgement and the property is what makes them reviewable.

    Read with `ast` and not with a `grep`, for the reason `tests/test_contract.py`
    gives: the text of this module discusses `add(` in its comments too.
    """
    src = os.path.join(os.path.dirname(__file__), "..", "src", "scriptorium", "checks.py")
    with open(src, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_segment")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "add"]
    assert len(calls) >= 20, "the extractor found no `add(...)` calls; it is wrong"
    missing = [n.lineno for n in calls
               if len(n.args) < 4 and "waivable" not in {k.arg for k in n.keywords}]
    assert not missing, (
        f"`add(...)` at lines {missing} of checks.py does not say whether a reviewer "
        f"may waive it. The argument is required so that this cannot be answered by "
        f"omission; decide it where the finding is made.")


def test_missing_is_not_waivable_and_is_refused_at_the_door_besides(book):
    """Both halves, because they are two statements and the second is free.

    A waiver on an untranslated segment would answer a report nobody has read
    about wording nobody has written — and it would aim at `missing`, which is
    unwaivable, so the request could only ever be a mistake. Refused whole-request
    the way `do_hold` refuses one, and answered at the rule as well.
    """
    blank = _seg("s", "A line.", "")
    assert [(i["severity"], i["rule"]) for i in
            check_segment(dict(blank, waived=True), "zh-TW", CFG, [], [])] == \
        [("error", "missing")]

    with pytest.raises(UnusableTarget) as caught:
        do_waive("d.md", "zh-TW", CFG, [_untranslated()])
    assert "nothing here to waive" in str(caught.value)
    # Refused for the **whole request**, so a client cannot waive three segments
    # and discover afterwards that one of them was never written.
    with pytest.raises(UnusableTarget):
        do_waive("d.md", "zh-TW", CFG, [book[0], _untranslated()])
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])
    # An id that names no segment is ignored rather than refused, which is what
    # `unknown` means everywhere else on this surface.
    assert do_waive("d.md", "zh-TW", CFG, ["nope"]) == (0, ["nope"], [])
    # And lifting carries no such condition: undoing must never be harder.
    assert do_waive("d.md", "zh-TW", CFG, [_untranslated()], waived=False) == (0, [], [])


# ── what a waiver may not outlive ───────────────────────────────────────────

def test_a_waiver_does_not_survive_the_wording_it_was_granted_on(book):
    """Structural, not checked: the writers drop it, so no read has to ask.

    A reviewer stood by *these words*. New words are a new judgement, and the
    same rule `target_slots` follows in the same two functions — one
    unconditional where every caller feeds it accepted output, one comparing the
    stored target where an agent may resend a whole document byte for byte.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0])

    # A save that changed nothing keeps it: an agent round-tripping the document
    # must not lift every waiver in the book.
    do_apply("d.md", "zh-TW", CFG, {book[0]: FOLDED}, origin="human")
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0])

    do_apply("d.md", "zh-TW", CFG, {book[0]: "⟦1⟧在門邊等著。"}, origin="human")
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])


def test_a_model_run_lifts_the_waiver_on_the_wording_it_replaces(book):
    """The other writer, which `do_apply` never reaches.

    `store.save_targets` is what a translation run commits each batch through,
    and it is the only writer `do_apply` does not go past — so the test above
    covers `save_segments` and this one covers the drop that would otherwise have
    no test at all. It is unconditional there on purpose: every caller feeds this
    function `translate.accept`'s output, so the text is never the wording that
    was waived, and a run that silenced its own fresh draft would be the waiver
    doing the one thing it must not.
    """
    do_apply("d.md", "zh-TW", CFG, {book[0]: FOLDED}, origin="agent")
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0])

    written, refused = save_targets("d.md", "zh-TW",
                                    {book[0]: "⟦1⟧在門邊等著，⟦2⟧沒有動。"},
                                    origin="llm:draft")
    assert (written, refused) == (1, [])
    assert not is_waived(load_doc("d.md", "zh-TW")["segments"][0])


def test_a_waiver_survives_the_re_extract_every_run_performs(book):
    """Otherwise the feature would not exist: `lx run` re-extracts every time.

    A carryover is the same wording at the same position, which is exactly what
    the waiver was granted over. What must not survive is a *new* wording, and
    the test above is the one that says so.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    do_extract("d.md", "zh-TW", CFG)
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0])
    report, _ = do_check("d.md", "zh-TW", CFG)
    assert report["errors"] == 0


def test_a_waiver_does_not_ride_a_carryover_that_could_not_place_itself(book):
    """The fallback branch drops it, the rule a hold already follows.

    That branch is reached when the diff cannot establish which stored wording
    belongs to which position. Carrying a hold in took a paragraph nobody had
    looked at out of every queue; carrying a waiver in would answer the report on
    a paragraph nobody had read, which is the one thing it must never do by
    itself.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    # A second copy of the first paragraph: the run its key belongs to changed
    # size, so nothing can place either occurrence and both fall to the fallback.
    open("d.md", "wb").write(DOC.replace(b"The hill was quiet.\n",
                                         b"Ana waited at the gate. Ana did not move.\n"))
    do_extract("d.md", "zh-TW", CFG)
    assert not any(is_waived(s) for s in load_doc("d.md", "zh-TW")["segments"])


# ── what a waiver may not overwrite ─────────────────────────────────────────

def test_waiving_a_held_segment_leaves_the_hold_standing(book):
    """The measured reason this is its own field and not a second `review` value.

    `review` holds one string. Written there, a waiver would take `review` from
    `held` to `waived`, `is_held` would go false, and `checks.workable` would
    hand the segment back to the queues the hold had taken it out of — the exact
    thing the hold was placed to stop. Reproduced 2026-09-03, which is the fourth
    time a second `review` value has been refused and the first time by
    measurement rather than by precedent.
    """
    do_hold("d.md", "zh-TW", CFG, [book[0]])
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    seg = load_doc("d.md", "zh-TW")["segments"][0]
    assert is_held(seg) and is_waived(seg)

    # And lifting one leaves the other, in both directions.
    do_waive("d.md", "zh-TW", CFG, [book[0]], waived=False)
    assert is_held(load_doc("d.md", "zh-TW")["segments"][0])
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    do_hold("d.md", "zh-TW", CFG, [book[0]], held=False)
    assert is_waived(load_doc("d.md", "zh-TW")["segments"][0])


def test_disabling_the_rule_disables_the_feature_and_not_only_its_marker(book):
    """Armed together or not at all.

    `checks_disabled` reaches every rule name, `waived` included, and a project
    that silences the one line saying a reviewer overrode something must not keep
    the override. Measured 2026-09-03 on the first version of this: the cap still
    applied while `errors: 0` and `waived: 0` were both true, and nothing on any
    surface said so.
    """
    do_waive("d.md", "zh-TW", CFG, [book[0]])
    off = {**CFG, "checks_disabled": ["waived"]}
    report, _ = do_check("d.md", "zh-TW", off)
    assert report["errors"] == 1
    assert "waived" not in report["by_rule"]


def test_the_rule_is_named_by_its_own_constant_and_by_the_literal():
    """A rename must move both, and this is what makes that visible.

    The `add(...)` call spells the rule name as a literal because
    `tests/test_contract.py` reads the emitted names off `check_segment` with
    `ast` and can only see a constant — an identifier there would drop the rule
    out of the frozen contract's enumeration with nothing failing. The module
    constant is the *body key* and they coincide, so this asserts they still do.
    """
    assert checks.WAIVED == "waived"
    assert is_waived({"waived": True}) and not is_waived({})
