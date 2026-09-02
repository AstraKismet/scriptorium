"""Which segments a run works on, and the one function that decides it.

The rule used to live in three places. `cmd_translate` read `--mode repair` as
*pending* segments because it had no repair branch at all; `cmd_repair` and
`cmd_run` read it as *failing* ones; and `web/server.py` carried a fourth copy
that agreed with neither command it sat in front of. That is
`docs/contracts/workbench-http.md` divergence (2), and this file is what stops it
coming back: every predicate that selects work is asserted here, against
`cli.do_select`, and the wire's own answer is asserted against the same function
in `test_web.py`.

The document is built so the three modes cannot agree by accident — one segment
is failing *without* being pending, which is the case a `repair`-means-pending
implementation gets wrong and a `repair`-means-failing one gets right.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import cli  # noqa: E402
from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import (  # noqa: E402
    UnusableTarget,
    do_apply,
    do_extract,
    do_hold,
    do_select,
)
from scriptorium.config import DEFAULT_CONFIG, DEFAULT_TONE  # noqa: E402
from scriptorium.store import load_doc  # noqa: E402

CFG = dict(DEFAULT_CONFIG)

#: A heading and three paragraphs. The second paragraph carries a link, so its
#: masked source holds a `⟦n⟧` — which is how a segment is made to *fail* a check
#: while still having a target, the one shape that tells `repair` and `draft`
#: apart. No digit outside the masked span, so the `numbers` rule stays quiet.
DOC = (b"# Title\n"
       b"\n"
       b"See [the guide](https://example.com/here) for details.\n"
       b"\n"
       b"The gate stood open when she came down the hill.\n"
       b"\n"
       b"She went in anyway, and it swung shut behind her.\n")


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A document whose four segments sit in three different selection states.

    * `heading` — translated and clean.
    * `broken`  — translated and **failing**, because its target dropped the
      placeholder the source carries. Reached through `do_apply`, which is the
      one write path that deliberately does not refuse a person's words.
    * the last two — no target at all, so pending *and* failing on `missing`.

    ``origin="agent"`` and not ``"human"``, deliberately: an agent is a peer and
    is not guarded, so these tests measure what a *mode* selects and nothing
    else. What origin precedence removes from a selection is asserted separately,
    under "a person's wording is not offered to a model run", against the same
    document written the other way.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / "d.md").write_bytes(DOC)
    do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]]
    assert len(ids) == 4
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "標題", ids[1]: "請見指南。"}, origin="agent")
    doc = load_doc("d.md", "zh-TW")
    return doc, {"heading": ids[0], "broken": ids[1],
                 "pending": [ids[2], ids[3]]}


def _picked(doc, cfg, mode, **over):
    return [s["id"] for s in do_select(doc, cfg, mode, **over)]


def test_the_fixture_really_holds_the_three_states_every_test_below_needs(book):
    """Asserted rather than assumed: a fixture that drifts makes the rest of this
    file pass for the wrong reason.

    The load-bearing row is `broken` — it has a target, so `draft` must not take
    it, and it fails a check, so `repair` must. If a future change to masking or
    to `do_apply` ever made that segment clean, every mode below would agree by
    accident and the divergence could come back unnoticed.
    """
    doc, ids = book
    by_id = {s["id"]: s for s in doc["segments"]}
    assert by_id[ids["heading"]]["kind"] == "heading"
    assert by_id[ids["broken"]]["kind"] == "para"
    assert "⟦" in by_id[ids["broken"]]["masked"], "the link was not masked"
    assert "⟦" not in by_id[ids["broken"]]["target"], "the target kept it"
    assert all(by_id[i]["status"] == "pending" for i in ids["pending"])
    assert all(by_id[i]["status"] == "translated"
               for i in (ids["heading"], ids["broken"]))

    from scriptorium.checks import check_segment
    def rules(sid):
        return {i["rule"] for i in check_segment(by_id[sid], "zh-TW", CFG, [], [])
                if i["severity"] == "error"}
    assert rules(ids["heading"]) == set()
    assert rules(ids["broken"]) == {"tags"}
    assert all(rules(i) == {"missing"} for i in ids["pending"])


def test_draft_takes_the_pending_segments_and_nothing_else(book):
    doc, ids = book
    assert _picked(doc, CFG, "draft") == ids["pending"]


def test_repair_takes_the_failing_segments_including_one_that_is_not_pending(book):
    """The assertion divergence (2) is about.

    `broken` has a target, so a `repair` that meant "pending" would skip the one
    segment a repair pass exists for, and would hand the model the untranslated
    remainder of the book instead.
    """
    doc, ids = book
    assert _picked(doc, CFG, "repair") == [ids["broken"], *ids["pending"]]


def test_polish_takes_translated_prose_and_leaves_a_heading_alone(book):
    doc, ids = book
    assert _picked(doc, CFG, "polish") == [ids["broken"]]


def test_an_unknown_mode_selects_what_draft_selects(book):
    """The contract's own row: anything else is `draft`, and the value is still
    forwarded as the routing stage."""
    doc, ids = book
    assert _picked(doc, CFG, "audit") == ids["pending"]


def test_ids_outrank_the_mode_including_the_one_that_used_to_come_first(book):
    """`lx translate --mode polish --ids <heading>` silently ignored `--ids`.

    The CLI tested `mode == "polish"` before it tested `args.ids`, so the flag
    was dropped for exactly one mode — while the endpoint honoured it for all of
    them. An explicit id is a person naming the work, so it outranks the mode,
    and it is filtered by nothing else: that is what makes the sentence
    `do_apply` prints when it refuses a blank target — "run `lx translate --ids
    <id>`" — true whatever state the segment is in.
    """
    doc, ids = book
    for mode in ("draft", "polish", "repair", "audit"):
        assert _picked(doc, CFG, mode, ids=[ids["heading"]]) == [ids["heading"]]


def test_an_empty_id_list_is_falsy_and_falls_through_to_the_mode(book):
    """The contract says so in as many words, and `[]` is what a client sends
    when its selection is empty rather than absent."""
    doc, ids = book
    assert _picked(doc, CFG, "draft", ids=[]) == ids["pending"]


def test_an_id_that_names_no_segment_is_dropped_rather_than_refused(book):
    doc, ids = book
    assert _picked(doc, CFG, "draft", ids=["nope", ids["heading"]]) == [ids["heading"]]


def test_all_reaches_the_pending_branch_only(book):
    """`--all` always did, and the shared function must not quietly widen it.

    "Everything, not only the pending ones" is a question the other three
    branches each answer for themselves. Unlike `limit`, which was widened on
    2026-09-02 — see the test below.
    """
    doc, ids = book
    assert len(_picked(doc, CFG, "draft", include_all=True)) == 4
    assert _picked(doc, CFG, "polish", include_all=True) == [ids["broken"]]


def test_limit_bounds_every_branch_except_a_named_ids(book):
    """Widened on 2026-09-02; until then it reached the pending branch alone.

    This test replaces `test_limit_and_all_reach_the_pending_branch_only`, whose
    third assertion pinned `repair, limit=1` returning all three failing
    segments. Its stated reason was that a bounded repair "would leave a
    document failing `lx check` with no sign of why" — which is an argument
    about *reporting*, applies equally to the draft branch that has always had a
    limit, and is answered by `cli._report_limit` saying so. What it cost
    meanwhile is in `_model_writable`'s own docstring: `lx translate --mode
    polish` on a 2000-paragraph novel selects all two thousand, and no bound
    could reach it from either surface. `docs/decisions.md`, 2026-09-02.
    """
    doc, ids = book
    assert _picked(doc, CFG, "draft", limit=1) == ids["pending"][:1]
    assert _picked(doc, CFG, "repair", limit=1) == [ids["broken"]]
    assert _picked(doc, CFG, "repair", limit=2) == [ids["broken"], ids["pending"][0]]
    assert _picked(doc, CFG, "polish", limit=1) == [ids["broken"]]
    # An unknown mode selects what draft selects, and is bounded with it.
    assert _picked(doc, CFG, "audit", limit=1) == ids["pending"][:1]
    # A limit at or above the selection changes nothing, and neither does 0.
    assert _picked(doc, CFG, "repair", limit=99) == [ids["broken"], *ids["pending"]]
    assert _picked(doc, CFG, "repair", limit=0) == [ids["broken"], *ids["pending"]]


def test_a_named_id_is_never_truncated_by_a_limit(book):
    """`ids` is tested before the limit is read, and that is the rule.

    Naming ids is a person pointing at segments, so a bound would silently drop
    work they asked for — the same argument that makes `ids` outrank `mode`, a
    hold and origin precedence. It was true by reading the code and pinned by
    nothing until 2026-09-02: no test passed `limit` alongside `ids`, so a build
    that threaded the bound into that branch stayed green.
    """
    doc, ids = book
    named = [ids["heading"], ids["broken"], *ids["pending"]]
    for mode in ("draft", "repair", "polish", "audit"):
        assert _picked(doc, CFG, mode, ids=named, limit=1) == named, mode


@pytest.mark.parametrize("bad", [True, False, "5", 2.0, [5], {"n": 5}, -1, -5])
def test_a_bound_that_is_not_a_count_is_refused_rather_than_guessed(book, bad):
    """Both halves are silent in Python, which is why neither is coerced.

    `isinstance(True, int)` is true, so a `bool` would slice to exactly one
    segment; and `out[:-5]` is *everything except the last five*, so a negative
    bound is not a smaller run but a nearly complete one — measured on the
    parent build, `lx translate --limit -5` on a 100-segment document translated
    95 and exited 0.

    `False` is in the list and is *accepted*, alone among the booleans: it is
    what `body.get("limit")` yields for a caller that sends `false`, and 0,
    `null` and absent are already one value meaning unbounded. `True` is not,
    because there is no reading of "limit: true" that means a number.
    """
    doc, ids = book
    if bad is False:
        assert _picked(doc, CFG, "draft", limit=bad) == ids["pending"]
        return
    with pytest.raises(UnusableTarget):
        do_select(doc, CFG, "draft", limit=bad)


def test_a_bound_is_applied_after_the_exclusions_and_not_before(tmp_path, monkeypatch):
    """A run of segments no model may write must not eat the bound.

    The argument `pending_segments` already makes for a hold, applied to the
    other rule that removes work. Measured on the parent build `67629fd`: the
    slice ran inside `pending_segments`, *before* `_model_writable`, so
    `--all --limit 2` on a document whose first segments are a person's wording
    selected **nothing** while the unbounded call selected the rest.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / "d.md").write_bytes(DOC)
    do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]]
    # The first two segments become a person's, so `--all` offers four and a
    # model run may write only the last two.
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "標題", ids[1]: "請見指南。"}, origin="human")
    doc = load_doc("d.md", "zh-TW")
    assert _picked(doc, CFG, "draft", include_all=True) == ids[2:]
    assert _picked(doc, CFG, "draft", include_all=True, limit=2) == ids[2:]
    assert _picked(doc, CFG, "draft", include_all=True, limit=1) == ids[2:3]


class _Echo:
    """A provider that answers every id it was asked for, and records the ask.

    The answer carries the segment's own placeholders back, so `translate.accept`
    takes it and `retry_one` never fires. That matters here rather than being
    tidiness: a rejected segment is asked for a *second* time, alone, and this
    file counts what was asked for.
    """

    def __init__(self):
        self.seen = []

    def describe(self):
        return "stub"

    def complete(self, system, user):
        items = json.loads(user[user.index("["):])
        self.seen.extend(i["id"] for i in items)
        return json.dumps(
            {i["id"]: "已翻譯。" + "".join(re.findall(r"⟦\d+⟧", i["text"]))
             for i in items}, ensure_ascii=False)


def _through_the_parser(monkeypatch, argv, exits=None):
    """Run a command the way a terminal does, and return what the model was asked.

    Through `build_parser` rather than a hand-built `Namespace`, because half of
    what this asserts is that the subcommand carries the flags the run reads —
    an omission that is invisible until somebody types that command.

    ``exits`` is the status code the command is expected to leave with, for the
    commands that end in `sys.exit`. Asserted rather than swallowed: `lx run`
    answering 1 when it did not render is part of what the caller reads, so a
    harness that let any exit through would hide a change to it.
    """
    stub = _Echo()
    monkeypatch.setattr(translate_mod, "build_provider",
                        lambda name, cfg, model=None: stub)
    args = cli.build_parser().parse_args(argv)
    if exits is None:
        args.fn(args, CFG)
    else:
        with pytest.raises(SystemExit) as left:
            args.fn(args, CFG)
        assert left.value.code == exits
    return stub.seen


def test_lx_translate_mode_repair_asks_for_the_failing_segments(book, monkeypatch):
    """The CLI half of divergence (2), through the parser and the real command.

    Before 2026-08-15 this sent the *pending* segments, so `lx translate --mode
    repair` and the Repair button on the same document translated different
    things and neither said so.
    """
    _doc, ids = book
    asked = _through_the_parser(
        monkeypatch, ["translate", "d.md", "--lang", "zh-TW", "--mode", "repair"])
    assert sorted(asked) == sorted([ids["broken"], *ids["pending"]])


def test_lx_translate_limit_reaches_the_command_through_the_parser(book, monkeypatch):
    """The flag existed and no test drove it through argparse.

    Every prior limit assertion called `do_select` with a Python keyword, so a
    subcommand that stopped forwarding `--limit` was invisible. Repair is the
    mode asserted here because it is the one the bound could not reach at all
    before 2026-09-02.
    """
    _doc, ids = book
    asked = _through_the_parser(
        monkeypatch, ["translate", "d.md", "--lang", "zh-TW",
                      "--mode", "repair", "--limit", "1"])
    assert asked == [ids["broken"]]


def test_lx_repair_takes_a_limit_because_the_wire_does(book, monkeypatch):
    """Invariant 8, in the direction it is usually not tested.

    `POST /api/translate` can bound a repair run. A `lx repair` that could not
    would be the CLI-lacks-what-the-wire-has shape that invariant exists to
    stop — divergence (30) is the standing open example of it.
    """
    _doc, ids = book
    asked = _through_the_parser(
        monkeypatch, ["repair", "d.md", "--lang", "zh-TW", "--limit", "2"])
    assert asked == [ids["broken"], ids["pending"][0]]


def test_lx_run_bounded_does_not_let_the_repair_rounds_undo_the_bound(book, monkeypatch, capsys):
    """The trap the flag exists to work around, driven end to end.

    An untranslated segment fails `checks.check_segment`'s `missing` rule at
    *error* severity, so `do_select(mode="repair")` returns everything the bound
    left alone. Without the narrowing in `cmd_run`, repair round 1 translates
    the whole remainder — the same money, stamped `llm:repair` instead of
    `llm:draft`. Verified on the parent build `67629fd`.

    The fixture has two pending segments and one translated-but-failing one, so
    `--limit 1` drafts exactly one and the round that follows may revisit only
    that one. `s0002` is failing throughout and is *not* this run's work, so it
    must never be asked for.
    """
    _doc, ids = book
    # Exit 1, as `lx run` has always answered when errors remain and it did not
    # render. The code keeps its meaning — "the document is not finished" —
    # rather than gaining a second one for a bounded pass that went fine, which
    # would leave a caller unable to tell the two apart. Invariant 10's
    # territory: the exit code is the evidence, so it must not start meaning two
    # things. Only the sentence beside it changes.
    asked = _through_the_parser(
        monkeypatch, ["run", "d.md", "--lang", "zh-TW", "--limit", "1"], exits=1)
    assert ids["pending"][0] in asked
    assert ids["pending"][1] not in asked, "the bound was undone by a repair round"
    assert ids["broken"] not in asked, "a bounded run repaired what it did not write"
    out = capsys.readouterr().out
    assert "bounded to 1 segment(s) per pass" in out, "the refusal must name the bound"
    assert "inspect with `lx check`" not in out, (
        "sent to `lx check` a reader finds errors they created on purpose")


def test_a_bounded_polish_asks_for_the_same_segments_every_time(book, monkeypatch, capsys):
    """The bound is on spend, not on progress, and nothing may promise otherwise.

    A polished segment is still translated prose, so `polish` selects it again —
    where a drafted segment leaves the pending queue and the next run gets the
    next ones. Measured 2026-09-02: three consecutive bounded polish runs asked
    for the same head of the document and billed for each.

    So this pins the behaviour **and** the sentence beside it. The first version
    printed "run the same command again for the rest", which is an instruction
    that silently does nothing here and costs money to follow.
    """
    doc, ids = book
    do_apply("d.md", "zh-TW", CFG,
             {i: "已翻譯。" for i in ids["pending"]}, origin="agent")
    asked = [_through_the_parser(
        monkeypatch, ["translate", "d.md", "--lang", "zh-TW",
                      "--mode", "polish", "--limit", "1"]) for _ in range(3)]
    assert asked[0] == asked[1] == asked[2], f"the selection advanced: {asked}"
    assert len(asked[0]) == 1

    out = capsys.readouterr().out
    assert "stopped at the --limit of 1" in out
    assert "for the rest" not in out, "a bounded polish has no 'rest' to come back for"


def test_a_bound_is_not_announced_when_ids_named_the_work(book, monkeypatch, capsys):
    """`ids` outranks the bound, so a message about the bound describes a rule
    that did not apply. Reachable whenever the id count equals the limit."""
    doc, ids = book
    _through_the_parser(monkeypatch, ["translate", "d.md", "--lang", "zh-TW",
                                      "--ids", ids["pending"][0], "--limit", "1"])
    assert "stopped at the --limit" not in capsys.readouterr().out


def test_a_bounded_run_does_not_claim_untranslated_work_that_is_not_there(
        book, monkeypatch, capsys):
    """The message is gated on the draft queue, never on the flag being set.

    Measured 2026-09-02 on the first version of this work: a document translated
    12 of 12, still failing because a person had written one of the segments and
    origin precedence means no run may replace it, was told "the rest of the
    document is still untranslated. Run the same command again to continue" —
    naming the wrong cause and prescribing a remedy that does nothing. The
    general sentence, which names the blockers, is the right one there.
    """
    doc, ids = book
    # Everything translated, and the failing one is a person's — so nothing is
    # pending, `repair` may not touch it, and no further run changes anything.
    do_apply("d.md", "zh-TW", CFG,
             {i: "已翻譯。" for i in ids["pending"]}, origin="agent")
    do_apply("d.md", "zh-TW", CFG, {ids["broken"]: "沒有標記。"}, origin="human")

    _through_the_parser(monkeypatch, ["run", "d.md", "--lang", "zh-TW",
                                      "--limit", "3"], exits=1)
    out = capsys.readouterr().out
    assert "still untranslated" not in out, "it claimed work that does not exist"
    assert "not rendering while errors remain" in out, "the general sentence is the true one"
    assert "written by a person" in out, "and the blocker must still be named"


def test_lx_run_unbounded_still_repairs_what_it_did_not_write(book, monkeypatch):
    """The narrowing is conditional, and this is what it must not cost.

    `s0002` was written by an agent and fails; an unbounded `lx run` has always
    repaired it, and a build that narrowed every run to its own draft pass would
    silently stop. Same command, one flag apart.
    """
    _doc, ids = book
    asked = _through_the_parser(monkeypatch, ["run", "d.md", "--lang", "zh-TW"])
    assert ids["broken"] in asked, "an unbounded run must still repair a stale wording"


def test_lx_translate_with_ids_asks_for_exactly_those(book, monkeypatch):
    _doc, ids = book
    asked = _through_the_parser(
        monkeypatch, ["translate", "d.md", "--lang", "zh-TW",
                      "--mode", "polish", "--ids", ids["heading"]])
    assert asked == [ids["heading"]]


def test_lx_repair_asks_for_the_failing_segments_and_asks_once(book, monkeypatch):
    """Two commands, one predicate. They were two predicates until this landed.

    Named against the fixture's own ids rather than against `do_select`'s answer,
    which is not a detail: the first draft of this test compared the command's
    ask to `do_select(doc, cfg, "repair")` and a mutant that made `repair` mean
    *pending* — the exact defect this file exists for — moved both sides together
    and passed. A test whose oracle is the code under test asserts only that the
    code is self-consistent.
    """
    _doc, ids = book
    asked = _through_the_parser(monkeypatch, ["repair", "d.md", "--lang", "zh-TW"])
    assert set(asked) == {ids["broken"], *ids["pending"]}
    assert len(asked) == 3, "a segment was asked for twice"


# ── a held segment is out of every queue, and in reach of an explicit id ────

def _hold(ids):
    return do_hold("d.md", "zh-TW", CFG, ids)


def test_a_held_segment_leaves_every_queue_at_once(book):
    """The exclusion is one helper applied at every predicate that selects work.

    Asserted against every mode in one test on purpose: the defect this guards
    is not "the helper is wrong", it is "somebody added a fourth predicate and
    did not call it", and a per-mode test would pass for three of them while the
    fourth quietly fed a held segment back to the model.
    """
    doc, ids = book
    before = {m: _picked(doc, CFG, m) for m in ("draft", "repair", "polish")}
    assert ids["broken"] in before["repair"] and ids["broken"] in before["polish"]
    assert _hold([ids["broken"]]) == (1, [])

    doc = load_doc("d.md", "zh-TW")
    after = {m: _picked(doc, CFG, m) for m in ("draft", "repair", "polish")}
    for mode in after:
        assert ids["broken"] not in after[mode], f"{mode} still selects a held segment"
    # And nothing else moved: holding one segment must not change what the
    # queues think about the rest.
    for mode in before:
        assert set(after[mode]) == set(before[mode]) - {ids["broken"]}
    # `--all` is the only way a held segment could reach the draft branch, since
    # the branch's own predicate is `status == "pending"` — see the test below.
    assert ids["broken"] not in _picked(doc, CFG, "draft", include_all=True)


def test_a_held_segment_can_never_be_pending_and_that_is_by_construction(book):
    """Two rules compose into a third, and it is worth writing down.

    Holding requires a non-empty target, and `status` is derived from the target
    text — so a held segment is always `translated` and the *draft* queue could
    not have selected it even with no exclusion at all. The exclusion still
    earns its place on that branch: `--all` ignores `status` entirely, and a
    predicate that is only correct because of a rule two modules away is one
    refactor from being wrong.
    """
    doc, ids = book
    _hold([ids["broken"]])
    held = {s["id"]: s for s in load_doc("d.md", "zh-TW")["segments"]}[ids["broken"]]
    assert held["review"] == "held"
    assert held["status"] == "translated"


def test_failing_segments_itself_excludes_a_held_one(book):
    """The predicate the shared helper exists for.

    `translate.failing_segments` asks the validators rather than the queue, so it
    is status-blind by construction: without the exclusion *inside it*, a held
    segment carrying an unrelated error would come back to the model on every
    repair round of every run. Called directly rather than through `do_select`,
    because a future caller reaching for it directly is exactly the case.
    """
    from scriptorium.translate import failing_segments
    doc, ids = book
    assert ids["broken"] in [s["id"] for s in failing_segments(doc, CFG)]
    _hold([ids["broken"]])
    doc = load_doc("d.md", "zh-TW")
    assert ids["broken"] not in [s["id"] for s in failing_segments(doc, CFG)]


def test_pending_segments_excludes_a_held_one_before_the_limit(book):
    """Before the limit, not after: filtering afterwards lets a run of held
    segments eat a `--limit 20` and hand back four."""
    from scriptorium.cli import pending_segments
    doc, ids = book
    _hold([ids["heading"]])
    doc = load_doc("d.md", "zh-TW")
    # `--all` is where a held segment can reach this predicate at all, since a
    # held one is always `translated`. Two of the four remain.
    assert [s["id"] for s in pending_segments(doc, include_all=True, limit=2)] == [
        ids["broken"], ids["pending"][0]]
    assert [s["id"] for s in pending_segments(doc, include_all=True)] == [
        ids["broken"], *ids["pending"]]


def test_an_explicit_id_still_reaches_a_held_segment(book):
    """The one exemption, and it is the design.

    Holding says "no *queue* may take this"; naming an id is a person pointing
    at one segment. It is also what keeps `do_apply`'s own refusal message
    honest — that message tells a reviewer to run `lx translate --ids <id>`, and
    a hold silently swallowing it would make the sentence false. The model still
    cannot overwrite their wording, because origin precedence is a separate rule
    enforced at the write.
    """
    doc, ids = book
    _hold([ids["broken"]])
    doc = load_doc("d.md", "zh-TW")
    for mode in ("draft", "repair", "polish"):
        assert _picked(doc, CFG, mode, ids=[ids["broken"]]) == [ids["broken"]]


def test_holding_reports_a_held_segment_at_warn_and_check_still_passes(book):
    """Warn and never error, which is the whole design of the severity choice.

    A severity that failed the build would make lifting every hold the only way
    to finish a book — so `lx check` still exits 0 with a held segment in the
    document, and the reviewer is told rather than blocked.
    """
    from scriptorium.checks import check_segment
    doc, ids = book
    _hold([ids["broken"]])
    seg = {s["id"]: s for s in load_doc("d.md", "zh-TW")["segments"]}[ids["broken"]]

    found = check_segment(seg, "zh-TW", CFG, [], [])
    held = [i for i in found if i["rule"] == "held"]
    assert len(held) == 1 and held[0]["severity"] == "warn"
    # Disable-able like every other rule.
    off = {**CFG, "checks_disabled": ["held"]}
    assert not [i for i in check_segment(seg, "zh-TW", off, [], []) if i["rule"] == "held"]


def test_an_untranslated_segment_is_answered_by_missing_rather_than_held(book):
    """`check_segment` returns on an empty target before any other rule runs, and
    that ordering is right rather than incidental: `missing` is the more useful
    sentence, and holding an untranslated segment is refused at the door anyway.
    """
    from scriptorium.checks import check_segment
    doc, ids = book
    seg = dict({s["id"]: s for s in doc["segments"]}[ids["pending"][0]], review="held")
    rules = {i["rule"] for i in check_segment(seg, "zh-TW", CFG, [], [])}
    assert rules == {"missing"}


def test_holding_an_untranslated_segment_is_refused_for_the_whole_request(book):
    """Whole-request, like `do_apply`'s empty-target refusal and for the same
    reason: a control that carries several ids must not half-happen."""
    doc, ids = book
    with pytest.raises(UnusableTarget) as caught:
        do_hold("d.md", "zh-TW", CFG, [ids["broken"], ids["pending"][0]])
    assert ids["pending"][0] in str(caught.value)
    assert "lx translate" in str(caught.value), "the message must name the way forward"
    # Nothing was written, including the id that was fine.
    doc = load_doc("d.md", "zh-TW")
    assert all(s.get("review") is None for s in doc["segments"])


def test_lifting_a_hold_carries_no_such_condition(book):
    """Undoing something must never be harder than doing it."""
    doc, ids = book
    _hold([ids["broken"]])
    # One, not two: `applied` counts rows whose review value actually moved. It
    # counted every row it touched until 2026-08-16, so `lx unhold` on a segment
    # that was never held printed "released 1 segment(s)" — and that count is the
    # only feedback the command gives.
    assert do_hold("d.md", "zh-TW", CFG, [ids["broken"], ids["pending"][0]],
                   held=False) == (1, [])
    assert all(s.get("review") is None
               for s in load_doc("d.md", "zh-TW")["segments"])


def test_an_id_naming_no_segment_is_ignored_rather_than_refused(book):
    doc, ids = book
    assert do_hold("d.md", "zh-TW", CFG, ["nope", ids["broken"]]) == (1, ["nope"])


# ── a hold survives the commands a translator runs most ────────────────────

def test_a_hold_survives_a_re_extract(book):
    """`lx run`'s first statement is `do_extract`, so this is the difference
    between a hold and a hold that lasts until the next run.

    Before 2026-08-15 `review` was dropped by every re-extract while `origin`
    survived, because `prior_targets` carried `(target, origin)` and the
    carryover loop wrote only those two. The reviewer was told nothing; the
    source had not even changed. Found by an adversarial pass on the axis the
    change had listed as held constant.
    """
    doc, ids = book
    # The heading, whose wording carries over cleanly. `broken` is the segment
    # whose carryover `accept` refuses — see the test below, which is the other
    # half of this rule.
    _hold([ids["heading"]])
    do_extract("d.md", "zh-TW", CFG)
    after = {s["id"]: s for s in load_doc("d.md", "zh-TW")["segments"]}
    assert after[ids["heading"]]["review"] == "held", "the re-extract lifted the hold"
    assert after[ids["heading"]]["origin"] == "agent"
    assert after[ids["heading"]]["target"] == "標題"


def test_reset_drops_a_hold_with_everything_else(book):
    """`--reset` reads no prior state at all, which is what "start over" means.

    Asserted rather than left implicit: the carryover is where the hold now
    rides, so a reader could reasonably expect the hold to be independent of it.
    """
    doc, ids = book
    _hold([ids["heading"]])
    # The register the `book` fixture froze, not another one: a different tone
    # drops the carryover for a register reason, and the test would still pass
    # while measuring the wrong rule.
    do_extract("d.md", "zh-TW", CFG, tone=DEFAULT_TONE, reset=True)
    after = {s["id"]: s for s in load_doc("d.md", "zh-TW")["segments"]}
    assert after[ids["heading"]].get("review") is None
    assert not after[ids["heading"]].get("target"), "reset kept a target"


def test_a_hold_rides_with_wording_the_acceptance_path_refused(book):
    """A hold is about *this wording*, and the wording is still there.

    No monkeypatching: `broken`'s target dropped the placeholder its masked
    source carries, so `translate.accept` refuses that carryover for real. Until
    2026-08-17 the refusal deleted the target and the hold went with it, on the
    argument that there was nothing left to hold — and that argument was true
    only because the deletion made it true. `lx extract` keeps the wording now
    (`docs/contracts/workbench-http.md` divergence (24), closed), so the hold has
    a subject and stays with it. This test asserted the deletion before that
    date; its docstring said it would change when the decision was taken.

    The deadlock the hold exclusion exists to make unreachable still is, in a
    new shape: the segment comes back held *and translated*, `lx check` reports
    its placeholder error, no queue may select it, and `cmd_repair` names it with
    the way out — asserted in
    `test_repair_names_the_failing_segments_it_declined_to_select`.
    """
    doc, ids = book
    _hold([ids["broken"]])
    do_extract("d.md", "zh-TW", CFG)
    after = {s["id"]: s for s in load_doc("d.md", "zh-TW")["segments"]}
    assert after[ids["broken"]].get("target") == "請見指南。", "divergence (24) has changed"
    assert after[ids["broken"]].get("review") == "held", "the hold left its wording behind"
    assert after[ids["broken"]].get("origin") == "agent", "the wording changed hands"
    assert after[ids["broken"]]["status"] == "translated"


def test_repair_names_the_failing_segments_it_declined_to_select(book, capsys):
    """`lx check` exits 1 and `lx repair` said "nothing failing" — two commands
    of one product disagreeing, which is the class `do_select` was unified to
    remove and which the hold exclusion re-created by another route.

    `do_check` walks every segment, so a held segment's errors still count in the
    exit code; `failing_segments` cannot select one. The repair pass says so now
    instead of reporting silence.
    """
    doc, ids = book
    # Everything else clean, so the held segment is the *only* thing failing and
    # the repair pass genuinely has nothing it may select.
    do_apply("d.md", "zh-TW", CFG, {i: "已翻譯。" for i in ids["pending"]}, origin="agent")
    _hold([ids["broken"]])
    cli.cmd_repair(cli.build_parser().parse_args(
        ["repair", "d.md", "--lang", "zh-TW"]), CFG)
    said = capsys.readouterr().out
    assert "nothing failing" not in said
    assert ids["broken"] in said
    assert "lx unhold" in said, "the message must name the way forward"

    # And with nothing failing at all, the old sentence is unchanged.
    do_hold("d.md", "zh-TW", CFG, [ids["broken"]], held=False)
    do_apply("d.md", "zh-TW", CFG, {ids["broken"]: "請見指南。⟦1⟧"}, origin="agent")
    cli.cmd_repair(cli.build_parser().parse_args(
        ["repair", "d.md", "--lang", "zh-TW"]), CFG)
    assert "nothing failing" in capsys.readouterr().out


def test_hold_refuses_a_payload_shape_it_cannot_mean(book):
    """The silent-coercion defect `do_apply` already refuses a mis-shaped `base`
    for: `held: null` would read as false and *release* a hold, and a bare
    string `ids` would be walked one character at a time and answer
    `applied: 0` while looking like it had worked."""
    doc, ids = book
    for bad in (None, "false", 0, 1):
        with pytest.raises(UnusableTarget):
            do_hold("d.md", "zh-TW", CFG, [ids["broken"]], held=bad)
    for bad in ("s0002", 7, {"a": 1}):
        with pytest.raises(UnusableTarget):
            do_hold("d.md", "zh-TW", CFG, bad)
    # An empty or whitespace-only id is dropped rather than reported unknown.
    assert do_hold("d.md", "zh-TW", CFG, ["", "  ", ids["broken"]]) == (1, [])
    assert do_hold("d.md", "zh-TW", CFG, None, held=False) == (0, [])
