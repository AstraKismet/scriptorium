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
from scriptorium.cli import do_apply, do_extract, do_select  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
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
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / "d.md").write_bytes(DOC)
    do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]]
    assert len(ids) == 4
    do_apply("d.md", "zh-TW", CFG, {ids[0]: "標題", ids[1]: "請見指南。"}, origin="human")
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


def test_limit_and_all_reach_the_pending_branch_only(book):
    """They always did, and the shared function must not quietly widen them.

    `--all` means "everything, not only the pending ones"; the other three
    branches each already answer that question for themselves, and applying a
    limit to a repair round would leave a document failing `lx check` with no
    sign of why.
    """
    doc, ids = book
    assert _picked(doc, CFG, "draft", limit=1) == ids["pending"][:1]
    assert len(_picked(doc, CFG, "draft", include_all=True)) == 4
    assert _picked(doc, CFG, "repair", limit=1) == [ids["broken"], *ids["pending"]]
    assert _picked(doc, CFG, "polish", include_all=True) == [ids["broken"]]


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


def _through_the_parser(monkeypatch, argv):
    """Run a command the way a terminal does, and return what the model was asked.

    Through `build_parser` rather than a hand-built `Namespace`, because half of
    what this asserts is that the subcommand carries the flags the run reads —
    an omission that is invisible until somebody types that command.
    """
    stub = _Echo()
    monkeypatch.setattr(translate_mod, "build_provider",
                        lambda name, cfg, model=None: stub)
    args = cli.build_parser().parse_args(argv)
    args.fn(args, CFG)
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
