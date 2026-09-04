"""A reply whose answers belong to the wrong segments is thrown away whole.

The defect these tests exist for was reported from the review workbench and
reproduced against the model the maintainer runs: the model assigns its answers
by *position* rather than by id, so a run of consecutive segments comes back
shifted by one, each carrying the translation of the segment before it.
`run_batch` reads `mapping.get(seg["id"])` and stored it faithfully; nothing
downstream could see it, because `translate.accept` refuses only a placeholder
mismatch and prose carries no placeholders — 273 of 273 segments of the book it
was found in.

`translate.misattributed` is the gate. It judges the **whole reply**, because a
cascade is contiguous but where it starts is not decidable from what a reply
carries: the id-shaped arms carry no position at all and the length arm is
systematically late. A reply it refuses is discarded exactly the way an
unparsable one already was — `mapping = {}` — so every segment falls to the
per-segment `retry_one` path that has always existed.

Scored on 180 real batches, trained on one chapter of a novel and held out on
another: 86% of batches carrying a misattribution refused, 4.6% of clean ones
refused. It is not a proof and the tests here do not pretend otherwise; what they
pin is that each arm fires on the shape it was written for, that a clean reply is
not refused, and that a refusal costs the batch a retry rather than the document
a wrong paragraph.

`docs/decisions.md`, 2026-09-04.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import do_extract, do_translate  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.store import load_doc  # noqa: E402
from scriptorium.translate import misattributed  # noqa: E402

CFG = dict(DEFAULT_CONFIG)

#: Five paragraphs long enough to clear `checks.length_ratio`'s forty-character
#: floor — under it the rule says nothing, which is correct for a heading and
#: would make these tests assert on a gate that never ran.
BOOK = "\n\n".join([
    "The keeper climbed the stairs before dawn, counting them out of habit.",
    "Salt had eaten the railing until it flaked away under his hand.",
    "He had not spoken to another living soul in eleven days, and did not mind.",
    "The lamp needed trimming, and the glass needed the cloth he had forgotten.",
    "Below him the sea went on being the colour of slate, as it always had.",
]) + "\n"

def _rendered(item):
    """A plausible target for one payload item — about the 0.45 of its source
    that zh-TW runs at, so a clean reply is not refused by the very gate under
    test, and **distinct per id**.

    Distinct because an identical answer over two different sources is precisely
    what the duplicate arm refuses. The first draft of this file rendered from
    the source's *length* alone, so two paragraphs that happened to be the same
    length got the same string and every test here failed against a gate that
    was working. A fixture that is not a plausible reply measures nothing.
    """
    return item["id"] + "譯" * max(4, round(len(item["text"]) * 0.45) - len(item["id"]))


class _Stub:
    """Answers a batch however the test says, and records every request."""

    def __init__(self, answer):
        self._answer = answer
        self.payloads = []

    def describe(self):
        return "stub"

    def complete(self, system, user):
        items = json.loads(user[user.index("["):])
        self.payloads.append(items)
        return json.dumps(self._answer(items), ensure_ascii=False)


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / "book.md").write_bytes(BOOK.encode("utf-8"))
    do_extract("book.md", "zh-TW", CFG)
    return "book.md", load_doc("book.md", "zh-TW")


def _run(src, segments, stub, monkeypatch, batch=None):
    monkeypatch.setattr(translate_mod, "build_provider",
                        lambda name, _cfg, model=None: stub)
    return do_translate(src, "zh-TW", CFG, segments, "draft",
                        batch=batch or len(segments), concurrency=1)


# ── the two shapes the maintainer reported ─────────────────────────────────


def test_a_reply_shifted_by_one_across_the_batch_is_not_stored(tmp_path, monkeypatch):
    """Every id present, none extra, none duplicated — and every answer wrong.

    The shape reproduced live: `s0002` carries `s0001`'s translation, `s0003`
    carries `s0002`'s, and so on to the end. Nothing about the *set* of ids is
    wrong, which is why the id-shaped arms cannot see it and why a length
    signal has to exist at all — the paragraphs here differ in length, as
    paragraphs of a novel do.

    What is asserted is the outcome and not the mechanism: no segment ends the
    run holding the answer that was offered for its neighbour.
    """
    src, doc = _project(tmp_path, monkeypatch)
    segs = doc["segments"]
    shifted = {}

    def answer(items):
        if len(items) == 1:                     # the retry: answer it correctly
            return {items[0]["id"]: _rendered(items[0])}
        out = {}
        for n, item in enumerate(items):        # every answer one place late
            out[item["id"]] = _rendered(items[max(0, n - 1)])
        shifted.update(out)
        return out

    stub = _Stub(answer)
    applied, failures, _refused = _run(src, segs, stub, monkeypatch)

    assert len(stub.payloads) == 1 + len(segs), "the batch was refused and re-asked"
    assert applied == len(segs) and failures == []
    after = {s["id"]: s["target"] for s in load_doc(src, "zh-TW")["segments"]}
    for sid, wrong in shifted.items():
        if after[sid] == wrong:
            # Only legitimate where the neighbour's source is the same length,
            # which this fixture has none of.
            raise AssertionError(f"{sid} kept the answer offered for its neighbour")


def test_one_translation_returned_for_two_different_sources_is_not_banked(
        tmp_path, monkeypatch):
    """The other reported shape: a segment's target duplicating its predecessor's.

    Byte-identical over two different sources, which is decidable and needs no
    threshold. The sources are compared rather than assumed different, because a
    document really can hold one sentence twice — the translation-memory key
    exists because it does, and two identical paragraphs *should* get identical
    wording.
    """
    src, doc = _project(tmp_path, monkeypatch)
    segs = doc["segments"]

    def answer(items):
        if len(items) == 1:
            return {items[0]["id"]: _rendered(items[0])}
        out = {i["id"]: _rendered(i) for i in items}
        out[items[2]["id"]] = out[items[1]["id"]]      # one answer, two sources
        return out

    stub = _Stub(answer)
    applied, failures, _refused = _run(src, segs, stub, monkeypatch)

    assert len(stub.payloads) == 1 + len(segs), "the batch was refused and re-asked"
    after = [s["target"] for s in load_doc(src, "zh-TW")["segments"]]
    assert len(set(after)) == len(after), "two segments were left holding one wording"
    assert applied == len(segs) and failures == []


# ── and the control, without which neither of the above means anything ──────


def test_a_clean_reply_is_stored_without_a_second_request(tmp_path, monkeypatch):
    """One batch, one request. A gate that refused everything would pass the two
    tests above and be useless, so the cost of a false alarm is pinned here."""
    src, doc = _project(tmp_path, monkeypatch)
    segs = doc["segments"]
    stub = _Stub(lambda items: {i["id"]: _rendered(i) for i in items})
    applied, failures, _refused = _run(src, segs, stub, monkeypatch)

    assert len(stub.payloads) == 1, "a clean reply was re-asked"
    assert applied == len(segs) and failures == []


def test_a_reply_that_stops_early_costs_only_the_ids_it_dropped(tmp_path, monkeypatch):
    """A missing id is not grounds to throw the reply away, and that is measured.

    A `missing_ids` arm was built and scored: every batch it caught was already
    caught by another arm, so it added nothing — and it would have turned a
    reply that merely stops a few ids short into a request per segment where the
    path that has always existed asks only for what is missing.
    """
    src, doc = _project(tmp_path, monkeypatch)
    segs = doc["segments"]
    dropped = segs[-1]["id"]

    def answer(items):
        if len(items) == 1:                     # the retry answers it
            return {items[0]["id"]: _rendered(items[0])}
        return {i["id"]: _rendered(i) for i in items if i["id"] != dropped}

    stub = _Stub(answer)
    applied, failures, _refused = _run(src, segs, stub, monkeypatch)

    assert [i["id"] for p in stub.payloads[1:] for i in p] == [dropped], \
        "only the dropped id should have been re-asked"
    assert applied == len(segs) and failures == []


# ── the arms, one at a time ────────────────────────────────────────────────


def _sources(n=5):
    return {f"s000{i}": f"A source sentence long enough to be measured, number {i}."
            for i in range(1, n + 1)}


def _answers(src, **override):
    """A plausible answer per id, and a **distinct** one — identical answers over
    different sources are what the duplicate arm exists to refuse, so a fixture
    that repeats one is not a clean reply."""
    out = {sid: f"第{i}段" + "譯" * 26 for i, sid in enumerate(src, start=1)}
    out.update(override)
    return out


def test_an_id_nobody_asked_for_refuses_the_reply():
    """The loudest signal, and the one that named the mechanism.

    Observed keys from a real model include `s0005_after_text` and `s0010_2` —
    the payload field it had just read, and the last id it held with a number
    stuck on. Either is proof the model was counting rather than reading.
    """
    src = _sources()
    asked = list(src)
    good = _answers(src)
    assert misattributed(asked, good, src, "zh-TW", CFG) is None
    assert misattributed(asked, {**good, "s0006": "譯" * 30}, src, "zh-TW", CFG)


def test_a_target_outside_the_projects_own_length_band_refuses_the_reply():
    """The band is `checks.length_ratio`'s, read from the project's own config.

    One rule, one home: `checks.py` reports it at warn after the fact and this
    asks the same arithmetic before anything is stored. A project that tightened
    its band gets a tighter gate here for nothing, which is the whole reason the
    number is not written down twice.
    """
    src = _sources()
    asked = list(src)
    good = _answers(src)
    assert misattributed(asked, good, src, "zh-TW", CFG) is None

    # The same reply, unchanged, against a project that declares a narrower
    # band. Nothing about the answers moved; what moved is what this project
    # says it tolerates, which is the point of reading the band rather than
    # writing a number down here.
    narrow = dict(CFG, length_ratio={"zh-TW": [0.6, 0.9]})
    ratio = len(good["s0001"]) / len(src["s0001"])
    assert not 0.6 <= ratio <= 0.9 and 0.25 <= ratio <= 1.2
    assert misattributed(asked, good, src, "zh-TW", narrow)
