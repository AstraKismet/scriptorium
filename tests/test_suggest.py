"""Near matches from the translation memory, and the four things they may not do.

The memory answers exactly or not at all — `store.tm_lookup` takes a key, and one
character's difference in the source is a miss. That is the whole story for
documentation, where a re-run meets the same sentences, and it is not the story
for a novel, where the second chapter says *She had not slept* and the first said
*She had not slept well*.

What this file pins is mostly what the feature is **forbidden** to do, because
every one of those was a way the surface could have been built wrong and looked
right:

* it may not **write** — a fuzzy hit differs in its placeholder set by
  definition, so wording lifted from one segment renders a bare placeholder in
  another. There is no apply path here and deliberately none anywhere;
* it may not offer a segment's **own exact hit**, which `POST /api/extract` has
  already applied, and it must still offer a record whose *text* is identical
  under a different key, which an exact lookup structurally cannot reach;
* it may not **move an exit code**. `lx audit`'s rule and `lx renderings`': a
  number produced by a threshold is not evidence, and invariant 10 reserves that
  word for `lx check`;
* it may not **add a compiled dependency**. `rapidfuzz` is the library everyone
  means and invariant 1 excludes it by name.

`docs/decisions.md`, 2026-09-07; the algorithm and the cutoff were decided
2026-08-17.
"""

import argparse
import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import suggest  # noqa: E402
from scriptorium.cli import (  # noqa: E402
    SUGGEST_SEGMENTS,
    UnusableTarget,
    checked_cutoff,
    cmd_suggest,
    do_apply,
    do_commit,
    do_extract,
    do_style,
    do_suggest,
)
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.store import load_doc  # noqa: E402

CFG = dict(DEFAULT_CONFIG, tone="literary")

ROOT = os.path.join(os.path.dirname(__file__), "..")

#: One banked paragraph and the three that will be compared against it. The
#: distances are the fixture: `NEAR` differs by one word near the end, `FAR`
#: shares the language and nothing else, and `SAME` is byte-identical.
#:
#: Chosen so the answer does not sit near the 0.70 cutoff in either direction —
#: a fixture whose expected score is 0.71 is a fixture that will flip the first
#: time anybody touches the scoring, and then somebody will "fix" it by moving
#: the cutoff.
BANKED = "Eleanor had not slept well that night, and the lamp burned low."
NEAR = "Eleanor had not slept well that evening, and the lamp burned low."
FAR = "Thomas went down to the harbour before anyone else was awake."
SAME = BANKED

BANKED_TARGET = "艾蓮諾那晚沒睡好，燈火低低地燃著。"


def _project(tmp_path, monkeypatch, banked=(BANKED,), targets=(BANKED_TARGET,)):
    """A project whose memory holds ``banked``, committed and readable.

    Banked through `do_apply` and `do_commit` rather than by writing the JSONL
    by hand, so the records under test are records this build actually produces —
    a hand-written fixture is a second implementation of `store.tm_record` and
    drifts the first time the key gains a field.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / "bank.md").write_bytes(("\n\n".join(banked) + "\n").encode("utf-8"))
    do_extract("bank.md", "zh-TW", CFG)
    doc = load_doc("bank.md", "zh-TW")
    do_apply("bank.md", "zh-TW", CFG,
             {seg["id"]: target for seg, target in zip(doc["segments"], targets)},
             origin="human")
    do_commit("bank.md", "zh-TW", CFG)
    return tmp_path


def _document(tmp_path, paragraphs, name="ch2.md"):
    (tmp_path / name).write_bytes(("\n\n".join(paragraphs) + "\n").encode("utf-8"))
    do_extract(name, "zh-TW", CFG)
    return name, load_doc(name, "zh-TW")


# ── the known pair, in both directions ─────────────────────────────────────

def test_a_near_pair_is_offered_and_a_far_one_is_not(tmp_path, monkeypatch):
    """The package's own criterion: a known near pair, and a known far one.

    Both in one test on purpose. A detector that offers everything passes the
    first assertion, and one that offers nothing passes the second; only the
    pair together says the threshold is doing work. That is the null-detector
    check, and it is cheap enough to be unconditional.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [NEAR, FAR])

    report = do_suggest(load_doc(src, "zh-TW"), CFG)
    rows = {row["id"]: row for row in report["segments"]}
    near_row, far_row = rows["s0001"], rows["s0002"]

    assert len(near_row["suggestions"]) == 1
    hit = near_row["suggestions"][0]
    assert hit["source"] == BANKED
    assert hit["target"] == BANKED_TARGET
    assert hit["score"] >= suggest.CUTOFF
    # Well clear of the cutoff rather than merely above it, so this fixture does
    # not become a reason to move the cutoff later.
    assert hit["score"] > 0.9

    assert far_row["suggestions"] == []


def test_the_score_is_a_ratio_and_the_algorithm_is_named(tmp_path, monkeypatch):
    """A `difflib` ratio is not a standard, so the response says whose it is.

    A second client computing its own would get different numbers. The name
    travelling in every response is what lets that client know not to compare
    them — and what makes a later change of algorithm visible rather than
    silent.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [NEAR])

    report = do_suggest(load_doc(src, "zh-TW"), CFG)

    assert report["algorithm"] == "difflib.SequenceMatcher.ratio"
    assert report["cutoff"] == suggest.CUTOFF
    assert report["records"] == 1
    for row in report["segments"]:
        for hit in row["suggestions"]:
            assert 0.0 <= hit["score"] <= 1.0


# ── the exclusion rule, which is the subtle half ───────────────────────────

def test_a_segments_own_exact_hit_is_not_offered_back_to_it(tmp_path, monkeypatch):
    """`POST /api/extract` already applied it; showing it again is one wording twice.

    The segment here is byte-identical to the banked one **and** in the same
    context, so it shares the whole memory key — `do_extract` carries the
    wording over as an exact `tm` hit, and this surface must then stay silent
    about it. `examined == 0` is the assertion that matters: the exclusion
    happens before any comparison, not by filtering the result afterwards.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [SAME])

    doc = load_doc(src, "zh-TW")
    assert doc["segments"][0]["origin"] == "tm"
    assert doc["segments"][0]["target"] == BANKED_TARGET

    row = do_suggest(doc, CFG)["segments"][0]
    assert row["suggestions"] == []
    assert row["examined"] == 0


def test_identical_text_under_a_different_key_is_still_offered(tmp_path, monkeypatch):
    """At 1.00, and this is the case an exact lookup structurally cannot reach.

    The same sentence as a **heading** rather than a paragraph is a different
    `context`, so it is a different memory key and `tm_lookup` misses it — while
    being, to a reviewer, exactly the wording they want. Excluding it as "an
    exact match" would have been the obvious implementation and would have
    thrown away the most useful answer this surface has.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, ["## " + BANKED])

    doc = load_doc(src, "zh-TW")
    assert doc["segments"][0]["context"] == "heading"
    assert not doc["segments"][0]["target"], "the exact lookup must have missed it"

    hits = do_suggest(doc, CFG)["segments"][0]["suggestions"]
    assert len(hits) == 1
    assert hits[0]["score"] == 1.0
    assert hits[0]["target"] == BANKED_TARGET
    # `para`, the kind `mdparse` emits (mdparse.py:1251) and the value the
    # memory key is built from — not the English word for it. The context axis
    # is what makes this record a miss for the exact lookup and a hit here.
    assert hits[0]["context"] == "para"


# ── it writes nothing ──────────────────────────────────────────────────────

def test_a_suggestion_is_not_written_anywhere(tmp_path, monkeypatch):
    """Call the surface, then assert the segment is exactly as it was.

    The one that stops a one-click apply arriving by accident. Compared against
    a snapshot of every field the acceptance path touches rather than against
    `target` alone: a write that set `origin` or `review` without setting
    `target` would be just as wrong and is the shape a half-built apply takes.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [NEAR])

    def snapshot():
        return [{k: seg.get(k) for k in
                 ("target", "origin", "status", "review", "waived", "target_slots")}
                for seg in load_doc(src, "zh-TW")["segments"]]

    before = snapshot()
    assert before[0]["target"] in (None, "")

    doc = load_doc(src, "zh-TW")
    passed_in = copy.deepcopy(doc)
    report = do_suggest(doc, CFG)
    assert report["segments"][0]["suggestions"], "the fixture must offer something"

    assert snapshot() == before

    # **And the document the caller handed in is unchanged.** Measured
    # 2026-09-07: a mutant that assigned the best suggestion to `seg["target"]`
    # and never persisted it survived the reload comparison above, because the
    # reload reads the database and the mutation was in memory. That is not a
    # harmless difference — `web/server.py` passes a throwaway `load_doc`, but a
    # caller that went on to `save_doc` would bank a fuzzy hit as a translation,
    # which is the exact thing this surface exists not to do.
    assert doc == passed_in

    # And the memory itself is untouched — a surface that "helpfully" banked
    # what it offered would be writing to a source of truth.
    with open(".lx/tm.zh-TW.jsonl", encoding="utf-8") as f:
        assert len(f.readlines()) == 1


def test_the_command_exits_zero_whether_or_not_it_found_anything(
        tmp_path, monkeypatch, capsys):
    """`lx audit`'s rule. A finding may never move an exit code.

    The moment exit 1 meant "found something", exit 0 would mean "found
    nothing", which is one shell script away from "this document is covered by
    the memory" — the claim this command cannot make.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [NEAR, FAR])
    args = argparse.Namespace(src=src, lang="zh-TW", ids=None, cutoff=None,
                              limit=None, most=None, json=False)

    assert cmd_suggest(args, CFG) is None
    found = capsys.readouterr().out
    assert "1 of 2 segment(s) have a near match" in found

    # And with nothing to find, from a document that shares no wording at all.
    other, _ = _document(tmp_path, [FAR], name="ch3.md")
    assert cmd_suggest(argparse.Namespace(**{**vars(args), "src": other}), CFG) is None
    assert "0 of 1 segment(s) have a near match" in capsys.readouterr().out


# ── the bounds, and the two that are not interchangeable ───────────────────

def test_the_cutoff_is_refused_where_it_would_be_silently_useless(
        tmp_path, monkeypatch):
    """`checked_margin`'s rule pointing the other way: a floor, not a ceiling.

    A NaN offers nothing because every comparison against it is false, which
    reads exactly like a memory that holds nothing. A zero offers everything, at
    the cost the prefilters exist to avoid — the length guard is derived from the
    cutoff, so a zero admits the whole memory to a quadratic comparison.
    """
    assert checked_cutoff(None) == suggest.CUTOFF
    assert checked_cutoff(0.5) == 0.5
    assert checked_cutoff(1) == 1.0

    for bad in (0, 0.0, -0.5, 1.5, float("nan"), float("inf"), True, "0.8", None or [],):
        with pytest.raises(UnusableTarget):
            checked_cutoff(bad)


def test_a_lower_cutoff_offers_more_and_never_fewer(tmp_path, monkeypatch):
    """The knob does what its name says, asserted as a monotone property.

    Against the *set* of offered sources rather than a count, so this cannot
    pass by swapping one suggestion for another.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [FAR])

    strict = do_suggest(load_doc(src, "zh-TW"), CFG)["segments"][0]["suggestions"]
    loose = do_suggest(load_doc(src, "zh-TW"), CFG,
                       cutoff=0.05)["segments"][0]["suggestions"]

    assert {h["source"] for h in strict} <= {h["source"] for h in loose}
    assert strict == []
    assert loose, "a cutoff of 0.05 must reach the far pair this fixture is built on"


def test_the_two_bounds_are_different_bounds(tmp_path, monkeypatch):
    """`limit` counts segments and `most` counts suggestions per segment.

    Two names for two costs — a request's breadth and a panel's depth — and
    conflating them is how a client asking for five suggestions gets five
    segments. Pinned because the wire spells them as two sibling integers and
    nothing about the shape says which is which.
    """
    _project(tmp_path, monkeypatch,
             banked=(BANKED, NEAR), targets=(BANKED_TARGET, BANKED_TARGET + "。"))
    src, _ = _document(tmp_path, [NEAR + " Again.", FAR, BANKED + " Again."])

    doc = load_doc(src, "zh-TW")
    assert len(do_suggest(doc, CFG, limit=1)["segments"]) == 1
    assert len(do_suggest(doc, CFG, limit=0)["segments"]) == 3
    assert len(do_suggest(doc, CFG)["segments"]) == 3, "under the default of 25"

    deep = do_suggest(doc, CFG, limit=1)["segments"][0]
    assert len(deep["suggestions"]) == 2, "both banked wordings are near this one"
    shallow = do_suggest(doc, CFG, limit=1, most=1)["segments"][0]
    assert len(shallow["suggestions"]) == 1
    # Depth truncates the *worst*, so the best answer survives either way.
    assert shallow["suggestions"][0] == deep["suggestions"][0]


def test_a_bare_string_of_ids_is_refused_rather_than_iterated(
        tmp_path, monkeypatch):
    """`do_hold`'s guard, on both new surfaces, and for the identical reason.

    `{str(i) for i in "s0001"}` is a set of six characters, so `ids: "s0001"`
    would match no segment and answer an empty panel **while looking like it
    worked** — the shape of divergence (28), where an endpoint type-checks
    nothing and one of its fields destroys work. A new endpoint should not join
    that list, so the refusal is in the shared selector and both surfaces get it.
    """
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [NEAR])
    doc = load_doc(src, "zh-TW")

    for bad in ("s0001", 1, {"s0001": True}):
        with pytest.raises(UnusableTarget):
            do_suggest(doc, CFG, ids=bad)
        with pytest.raises(UnusableTarget):
            do_style(doc, CFG, ids=bad)

    # A list still works, and so does a list with blanks in it — those are
    # dropped rather than refused, which is what `do_hold` does with them.
    assert do_suggest(doc, CFG, ids=["s0001", "  "])["segments"][0]["id"] == "s0001"
    assert do_style(doc, CFG, ids=["s0001", ""])["ids"] == ["s0001"]


def test_zero_means_unbounded_for_both_bounds_and_not_the_default(
        tmp_path, monkeypatch):
    """`checked_limit`'s reading of `0`, and `most` did not have it at first.

    `limit=0` meaning "every segment" is the rule this project already states
    everywhere. `most=0` was collapsed into the default of five by an `or`,
    which is the silent kind of wrong: a client asking for no cap on the panel
    got five and nothing said so. The mirror mistake is worse — `hits[:0]` is
    the empty list, so the other plausible spelling would have answered "the
    memory holds nothing".
    """
    _project(tmp_path, monkeypatch,
             banked=(BANKED, NEAR), targets=(BANKED_TARGET, BANKED_TARGET + "。"))
    src, _ = _document(tmp_path, [NEAR + " Again."])
    doc = load_doc(src, "zh-TW")

    capped = do_suggest(doc, CFG, most=1)["segments"][0]["suggestions"]
    uncapped = do_suggest(doc, CFG, most=0)["segments"][0]["suggestions"]
    default = do_suggest(doc, CFG)["segments"][0]["suggestions"]

    assert len(capped) == 1
    assert len(uncapped) == 2, "0 is every match above the cutoff, not the default"
    assert uncapped == default, "the fixture has fewer matches than the default cap"


def test_the_default_bounds_the_segments_because_the_cost_is_local(
        tmp_path, monkeypatch):
    """The one default on this surface that is not "everything", stated as a value.

    Every other bound in this CLI defaults to unbounded. This one does not,
    because what a suggestion costs is CPU on the machine running the workbench
    rather than money at a provider — and unbounded, one request compares a
    novel's every segment against a novel's whole memory.

    The value is pinned rather than merely exercised. It was 25 by analogy with
    the translation batch, and the analogy is wrong — a batch is bounded by what
    a model costs and this by a quadratic comparison — so it is five, measured
    at about 1.3 s per segment against a 1966-record memory. A change to it is a
    change to a documented wire default.
    """
    assert SUGGEST_SEGMENTS == 5
    _project(tmp_path, monkeypatch)
    src, _ = _document(tmp_path, [FAR] * (SUGGEST_SEGMENTS + 3))

    doc = load_doc(src, "zh-TW")
    assert len(doc["segments"]) == SUGGEST_SEGMENTS + 3
    assert len(do_suggest(doc, CFG)["segments"]) == SUGGEST_SEGMENTS
    assert len(do_suggest(doc, CFG, limit=0)["segments"]) == SUGGEST_SEGMENTS + 3


# ── the work budget, and saying so ─────────────────────────────────────────

def test_a_truncated_search_says_it_was_truncated(tmp_path, monkeypatch):
    """A panel that quietly stopped looking looks like a memory holding nothing.

    Driven through `suggest.near`'s own budget argument rather than by building
    a memory large enough to hit the default, which would make this a slow test
    measuring the same one line.
    """
    records = [(f"k{i}", {"source": NEAR, "target": "x"}) for i in range(5)]

    hits, examined, skipped = suggest.near(BANKED, records)
    assert examined == 5 and skipped == 0
    assert len(hits) == 5

    hits, examined, skipped = suggest.near(BANKED, records, budget=1)
    assert examined == 1, "the first candidate always runs, or a long pair answers nothing"
    assert skipped == 4
    assert len(hits) == 1


def test_the_budget_is_checked_before_the_comparison_not_after(tmp_path, monkeypatch):
    """Or the worst case is unbounded by exactly one very long pair.

    A budget that stops once it is *already* over has not bounded anything: the
    single most expensive comparison still runs in full, which on two long
    paragraphs is the whole cost the budget exists to cap.

    **The budget here is chosen to separate the two guards, and that took a
    measurement.** The obvious test — a tiny budget and two long records —
    cannot tell them apart: with `spent` far past the ceiling after the first
    comparison, `spent > budget` and `spent + cost > budget` are both true and
    both stop at one. A mutant swapping them survived it on 2026-09-07. The
    budget has to land in the window where the first comparison *fits* and the
    second would overflow: then the correct guard stops at one and the mutant
    runs two.
    """
    a = "The lamp burned low over the table. " * 6
    b = a + "x"
    records = [("k1", {"source": a, "target": "x"}),
               ("k2", {"source": b, "target": "y"})]

    # The probe is `b`, and candidates are walked in descending order of their
    # upper bound — so the record identical to it is compared **first**. Getting
    # this backwards is what made the first version of this test unable to tell
    # the two guards apart, so it is asserted rather than assumed.
    first = len(b) * len(b)
    second = len(b) * len(a)
    assert suggest.near(b, records)[0][0][1] == "k2", "the identical record sorts first"

    # The window: `first` fits, `first + second` does not. Below it the correct
    # guard stops at one and a `spent > budget` mutant runs both.
    for budget in (first, first + second - 1):
        _hits, examined, skipped = suggest.near(b, records, budget=budget)
        assert (examined, skipped) == (1, 1), f"budget {budget} examined {examined}"

    # And the window is real rather than an artefact of the arithmetic: with
    # room for both, both run.
    _hits, examined, skipped = suggest.near(b, records, budget=first + second)
    assert (examined, skipped) == (2, 0)


# ── invariant 1, asserted rather than assumed ──────────────────────────────

def test_the_similarity_module_needs_no_compiled_extension():
    """Importable with nothing installed, and it imports only the standard library.

    Invariant 1's four situations — a bare interpreter, CI, an agent sandbox and
    a locked-down machine. `rapidfuzz` is the library everyone means by fuzzy
    matching and `docs/decisions.md` (2026-07-28) excludes its C++ by name, so
    this is the one place a well-meaning speed-up would break the invariant.

    Read with `ast` rather than by importing and inspecting: a module that
    imported a compiled package conditionally, or inside a function, would pass
    an import check on a machine where the package is absent.
    """
    import ast

    path = os.path.join(ROOT, "src", "scriptorium", "suggest.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert imported, "the extractor found no imports at all, which cannot be right"
    assert imported <= {"difflib"}, f"{sorted(imported)} is not the standard library"


def test_this_package_added_no_runtime_dependency():
    """Invariant 1, and the assertion lives one file over.

    `tests/test_import_boundary.py::test_the_split_trigger_has_not_fired` owns
    the check, because an empty `dependencies` list means two things at once —
    invariant 1 holds, *and* the core/studio split has nothing to partition yet,
    which is HANDOFF-201's trigger. One fact, one home; this is the pointer, so
    a reader of this file is not left thinking the criterion went unmet.
    """
    from test_import_boundary import test_the_split_trigger_has_not_fired
    test_the_split_trigger_has_not_fired()


# ── the shape a client is promised ─────────────────────────────────────────

def test_every_documented_key_is_present_even_when_there_is_nothing_to_say(
        tmp_path, monkeypatch):
    """Empty rather than absent, `lx todo`'s rule for the same reason.

    A consumer that branches on a missing key breaks the first time it meets a
    project whose memory is empty — which is every project on its first day.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    src, _ = _document(tmp_path, [NEAR])

    report = do_suggest(load_doc(src, "zh-TW"), CFG)

    assert set(report) == {"source", "lang", "tone", "algorithm", "cutoff",
                           "records", "segments"}
    assert report["records"] == 0
    row = report["segments"][0]
    assert set(row) == {"id", "source", "examined", "truncated", "suggestions"}
    assert row["suggestions"] == []
    assert row["truncated"] is False
    # Serializable as it stands, because both surfaces hand it straight to a
    # JSON encoder and a stray tuple or set would only fail on the wire.
    json.dumps(report, ensure_ascii=False)
