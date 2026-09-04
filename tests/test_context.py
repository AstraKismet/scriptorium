"""Neighbour context: what a request item is told about the segments around it.

A segment used to reach the model alone — no antecedent for a pronoun, no
speaker for a line of dialogue, no tense or register to hold to — and the retry
path, which is where a hard sentence actually ends up, sent one segment by
itself. For a reference paragraph that costs almost nothing. For prose it
removes exactly what prose depends on.

**Half of that was undone on 2026-09-04, and this file says which half.** A
neighbour outside the request used to arrive as its own source text, inlined
under `before_text` / `after_text` with no id of its own; measured against a real
model, the id-less paragraph is translated like any other content and the answer
takes the *first real id*, moving every answer after it one place. So a batch
edge now carries nothing, and a retried segment — which is a batch of one, and
so had both sides inlined — carries nothing either. `translate._attach` holds the
numbers and `docs/decisions.md`, 2026-09-04, holds the argument.

What is left, and what each test here pins:

* a neighbour already in the payload is *referenced by id*, never repeated, so
  the default window costs an id per item rather than trebling every request;
* a neighbour that is **not** an item of this request gets no field at all, and
  no payload anywhere carries a `_text` field;
* adjacency is **the document's**, never the order of the caller's list, which
  `lx repair` and `lx translate --ids` both hand over non-contiguous;
* the prompt paragraph appears only when a reference actually will.

`docs/decisions.md`, 2026-07-29 D5, and 2026-09-04.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import do_extract, do_translate  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.store import load_doc  # noqa: E402
from scriptorium.translate import (  # noqa: E402
    _CONTEXT_RULES,
    _neighbour_context,
    _user_message,
)

CFG = dict(DEFAULT_CONFIG)

#: Six paragraphs of prose, each recognizable on sight, so an assertion about
#: *which* neighbour arrived reads as one. No straight quote and no backslash
#: anywhere in it: `json.dumps` escapes both, and half these tests count
#: occurrences of a segment's source in the serialized request.
BOOK = "\n\n".join([
    "The gate stood open when Mara came down the hill.",
    "She had not expected that, and she stopped in the road.",
    "“You are late,” said the man on the step.",
    "Mara counted the windows before she answered him.",
    "The lamps were lit in only two of them.",
    "She went in anyway, and the gate swung shut behind her.",
]) + "\n"

#: A stand-in translation, and its **length is load-bearing**. Since
#: 2026-09-04 `translate.misattributed` throws a whole reply away when an
#: answer cannot plausibly be a translation of the source it was asked
#: about, and the four characters this used to be were 8% of a fifty-character
#: sentence -- refused, correctly, and the refusal turned every test in this
#: file into an assertion about the retry path instead of about its own
#: subject. Sized at roughly the 0.45 zh-TW renders English at.
DONE = "這一段已經翻譯完成，內容僅供測試使用。"


def done(seg):
    """The stand-in translation for one segment, **distinct per id**.

    One string returned for two different sources is a shape
    `translate.misattributed` refuses, so a stub that answers every item with
    the same constant is not a plausible reply — it makes every test in this
    file an assertion about the retry path instead of about its own subject.
    Takes a payload item or a stored segment; both carry `id`.
    """
    return DONE + seg["id"]


class _Recorder:
    """A provider that answers every id it was asked for, and records the request.

    The ids come off the request rather than out of a closure over the segment
    list, for the same reason `test_state._Interrupting` does it: what the test
    asserts about is then what the model would actually have seen.
    """

    def __init__(self, answer=None):
        self.requests = []          # the user message, verbatim
        self.payloads = []          # its JSON array, parsed
        self.systems = []
        self._answer = answer

    def describe(self):
        return "stub"

    def complete(self, system, user):
        items = json.loads(user[user.index("["):])
        self.requests.append(user)
        self.payloads.append(items)
        self.systems.append(system)
        if self._answer is not None:
            return self._answer(items)
        return json.dumps({i["id"]: done(i) for i in items}, ensure_ascii=False)


#: Serial on purpose: two workers make the order of `stub.payloads` a race, and
#: every test here asserts on which request carried what.
_SERIAL = 1


def _book(tmp_path, monkeypatch, text=BOOK, name="novel.md"):
    """A tracked project holding one document, extracted and ready to translate."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (tmp_path / name).write_bytes(text.encode("utf-8"))
    do_extract(name, "zh-TW", CFG)
    return name, load_doc(name, "zh-TW")


def _run(src, cfg, segments, stub, monkeypatch, batch=6, mode="draft"):
    monkeypatch.setattr(translate_mod, "build_provider", lambda name, _cfg, model=None: stub)
    return do_translate(src, "zh-TW", cfg, segments, mode, batch=batch,
                        concurrency=_SERIAL)


def test_neighbour_by_id_keeps_an_interior_segment_from_being_sent_three_times(
        tmp_path, monkeypatch):
    """The cost control. Everything a batch needs is already in the batch.

    A naive implementation attaches both neighbours' text to every item, so each
    segment travels three times and the request is roughly 3x — measured at
    2.76x to 2.95x. Pointing at the item that is already there costs an id.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    ids = [s["id"] for s in segs]
    stub = _Recorder()
    _run(src, CFG, segs, stub, monkeypatch, batch=len(segs))

    assert len(stub.payloads) == 1, "the whole document should fit one batch here"
    assert stub.payloads[0][2]["before_id"] == ids[1]
    assert stub.payloads[0][2]["after_id"] == ids[3]
    # Key order is reading order, and the payload reaches the model as text.
    assert list(stub.payloads[0][2]) == ["id", "kind", "before_id", "text", "after_id"]

    request = stub.requests[0]
    for seg in segs:
        assert request.count(seg["masked"]) == 1, f"{seg['id']} was repeated"


def test_a_batch_edge_carries_no_neighbour_at_all(tmp_path, monkeypatch):
    """The two edges point at nothing, and that is the 2026-09-04 reversal.

    They used to carry the neighbour's source inlined under `before_text` /
    `after_text`, which is what made a batch boundary as informed as its middle.
    The field was measured: an id-less paragraph placed first in an item is
    translated like any other content, and with nowhere of its own to go the
    answer takes the *first real id* — 24, 25 and 25 of 25 segments
    misattributed across three trials against the production shape, against 0, 0
    and 1 with the field removed. `translate._attach` carries the numbers.

    So a batch keeps its interior references and its edges keep nothing. What
    this test pins is the absence: no item, anywhere, under any window, carries
    a text field.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    ids = [s["id"] for s in segs]
    stub = _Recorder()
    _run(src, CFG, segs, stub, monkeypatch, batch=2)

    middle = next(p for p in stub.payloads if p[0]["id"] == ids[2])
    assert set(middle[0]) == {"id", "kind", "text", "after_id"}   # nothing across
    assert middle[0]["after_id"] == ids[3]                        # inside the batch
    assert set(middle[1]) == {"id", "kind", "before_id", "text"}
    assert middle[1]["before_id"] == ids[2]
    assert not any(k.endswith("_text") for p in stub.payloads for i in p for k in i)

    # Every id the model can see is one it may answer for: an inlined neighbour
    # has nowhere to carry an id, and a named one is an item of this request.
    named = False
    for payload in stub.payloads:
        here = {i["id"] for i in payload}
        ours = {n for i in payload for k in ("before_id", "after_id")
                for n in i.get(k, "").split()}
        assert ours <= here, "an id from outside this request was named"
        named = named or bool(ours)
    assert named, "nothing was referenced by id at all"


def test_a_retry_carries_no_neighbour_at_all(tmp_path, monkeypatch):
    """The rescue path sends the segment and nothing else, and that is a reversal.

    Until 2026-09-04 this file asserted the opposite, and the reasoning was
    written down: "`retry_one` sends a single segment, so there is no batch to
    borrow from and both sides are inlined ... this is where a hard sentence ends
    up." The cost that sentence weighed was tokens. The cost it could not have
    known about was measured on 2026-09-04 against a real model: of 25
    single-segment retries built exactly that way, **nine came back carrying a
    neighbour's translation**, eight of them the paragraph before. A batch of one
    has a single id, so a model that translates the inlined neighbour has only
    that id to put it under, and `accept` cannot tell — the placeholder gate is
    the only structural check there is and prose carries no placeholders.

    So the branch every other failure falls into was the likeliest place in the
    system to corrupt a segment. It now carries no `before_text`, no
    `after_text`, and — since a payload of one has nothing to point at — no
    `before_id` or `after_id` either. See `docs/decisions.md`, 2026-09-04.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    ids = [s["id"] for s in segs]

    def answer(items):
        # Skip the third id in the batch reply and answer it when it comes back
        # alone — which is exactly how `run_batch` falls through to `retry_one`.
        skip = items[2]["id"] if len(items) > 2 else None
        return json.dumps({i["id"]: done(i) for i in items if i["id"] != skip},
                          ensure_ascii=False)

    stub = _Recorder(answer)
    _run(src, CFG, segs, stub, monkeypatch, batch=len(segs))

    retry = stub.payloads[-1]
    assert [i["id"] for i in retry] == [ids[2]], "the retry should carry one segment"
    assert set(retry[0]) == {"id", "kind", "text"}, "a retry carries no neighbour"
    assert retry[0]["text"] == segs[2]["masked"]
    assert all(s.get("target") for s in load_doc(src, "zh-TW")["segments"])
    # And the batch that preceded it still referenced by id, so this changed the
    # retry path and nothing else.
    assert any("before_id" in i for i in stub.payloads[0])


def test_neighbour_document_edges_carry_the_side_that_exists_and_no_field_for_the_other(
        tmp_path, monkeypatch):
    """No empty field, no null: the first segment of a document simply has no `before`."""
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    stub = _Recorder()
    _run(src, CFG, segs, stub, monkeypatch, batch=len(segs))

    first, last = stub.payloads[0][0], stub.payloads[0][-1]
    assert set(first) == {"id", "kind", "text", "after_id"}
    assert set(last) == {"id", "kind", "before_id", "text"}


def test_neighbour_document_edges_of_a_one_segment_document_brief_nothing(
        tmp_path, monkeypatch):
    """The degenerate case: nothing to point at, nothing to inline, nothing to explain."""
    src, doc = _book(tmp_path, monkeypatch, text="The gate stood open.\n")
    stub = _Recorder()
    _run(src, CFG, doc["segments"], stub, monkeypatch)

    assert set(stub.payloads[0][0]) == {"id", "kind", "text"}
    assert _CONTEXT_RULES not in stub.systems[0]


def test_neighbour_echo_ignored_when_the_model_answers_for_one(tmp_path, monkeypatch):
    """A neighbour is context. An answer for one is discarded, not written.

    Nothing new guards this — `run_batch` reads `mapping.get(seg["id"])` for the
    segments of its own batch and `retry_one` reads its own id — but "already
    handled" was a reading of the code, and the package asked for the test rather
    than the reading.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    ids = [s["id"] for s in segs]
    echo = "鄰居的字。"

    def answer(items):
        out = {i["id"]: done(i) for i in items}
        out[ids[1]] = echo          # inlined before the requested pair
        out[ids[4]] = echo          # inlined after it
        return json.dumps(out, ensure_ascii=False)

    stub = _Recorder(answer)
    applied, failures, refused = _run(src, CFG, [segs[2], segs[3]], stub, monkeypatch,
                                      batch=2)

    assert (applied, failures, refused) == (2, [], [])
    after = {s["id"]: s.get("target") for s in load_doc(src, "zh-TW")["segments"]}
    assert after[ids[2]] == done(segs[2]) and after[ids[3]] == done(segs[3])
    assert after[ids[1]] is None and after[ids[4]] is None, "a neighbour was written"


def test_neighbour_context_follows_the_document_and_not_the_callers_list(
        tmp_path, monkeypatch):
    """`lx repair` passes the failing segments; `lx translate --ids` passes a set.

    Taking either as document order would tell the model that segment 2 and
    segment 5 are consecutive prose. A confident lie about flow is worse than no
    context, so adjacency is read off `doc["segments"]` — the same authority
    `tone` and `eol` already have for facts about the document.

    Since 2026-09-04 the payload can only *reference*, so the lie this rules out
    has a sharper shape than it did: two segments the caller happened to put
    side by side are not neighbours, so neither may name the other. Read off the
    list, `s0002` would carry `after_id: s0005`; read off the document, it
    carries nothing, because its real neighbour is not in this request.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    stub = _Recorder()
    _run(src, CFG, [segs[1], segs[4]], stub, monkeypatch, batch=6)

    payload = stub.payloads[0]
    assert [i["id"] for i in payload] == [segs[1]["id"], segs[4]["id"]]
    assert set(payload[0]) == {"id", "kind", "text"}, "the caller's next is not a neighbour"
    assert set(payload[1]) == {"id", "kind", "text"}, "nor is the caller's previous"

    # And the same two segments, asked for together with what really sits
    # between them, do reference each other — so the absence above is the
    # document speaking and not the feature being off.
    both = _Recorder()
    _run(src, CFG, segs[1:5], both, monkeypatch, batch=6)
    assert both.payloads[0][0]["after_id"] == segs[2]["id"]


def test_neighbour_window_config_widens_the_window_and_zero_turns_it_off(
        tmp_path, monkeypatch):
    """One knob, and `0` removes the prompt paragraph too rather than only the fields."""
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]

    off = dict(CFG, batch=dict(CFG["batch"], context=0))
    silent = _Recorder()
    _run(src, off, segs, silent, monkeypatch, batch=2)
    assert all(set(i) == {"id", "kind", "text"} for p in silent.payloads for i in p)
    assert all(_CONTEXT_RULES not in s for s in silent.systems)

    # A batch of one can reference nothing, so a widened window is invisible in
    # the payload — and the prompt paragraph goes with it. Before 2026-09-04 an
    # id-less neighbour would have been inlined here instead, and `briefed` was
    # computed from the document's adjacency rather than from what a request can
    # actually carry; asking the weaker question would brief the model about
    # fields no item of this run has.
    wide = dict(CFG, batch=dict(CFG["batch"], context=2))
    alone = _Recorder()
    _run(src, wide, segs, alone, monkeypatch, batch=1)
    assert all(set(i) == {"id", "kind", "text"} for p in alone.payloads for i in p)
    assert all(_CONTEXT_RULES not in s for s in alone.systems)

    # The same window with room to reference: two ids a side, space separated,
    # in document order. This is the only place more than one id lands in a
    # field, and `lx repair` reaches it whenever two failing segments are close.
    stub = _Recorder()
    _run(src, wide, segs, stub, monkeypatch, batch=len(segs))
    item = stub.payloads[0][3]
    assert item["before_id"] == f"{segs[1]['id']} {segs[2]['id']}"
    assert item["after_id"] == f"{segs[4]['id']} {segs[5]['id']}"
    assert _CONTEXT_RULES in stub.systems[0]

    # A window wider than the room left on one side truncates, and does not
    # wrap: the second segment of the document has one segment before it, not
    # two and not none. `ids[i - window:i]` without the clamp slices `ids[-1:1]`
    # here, which is empty — and the clamp is why this is one id and not zero.
    assert stub.payloads[0][1]["before_id"] == segs[0]["id"]

    # And a config written before the knob existed gets the feature, not silence.
    # `lx init` scaffolds `DEFAULT_CONFIG`, but a project scaffolded last month
    # has a `batch` block with three keys in it and is not rewritten.
    old = {k: v for k, v in CFG.items() if k != "batch"}
    old["batch"] = {"size": 25, "concurrency": 2, "max_repair_rounds": 3}
    stub = _Recorder()
    _run(src, old, segs, stub, monkeypatch, batch=2)
    assert stub.payloads[0][0]["after_id"] == segs[1]["id"]


def test_neighbour_context_skips_a_segment_the_document_does_not_hold(tmp_path, monkeypatch):
    """A stale caller list costs the context, not the run.

    Unreachable from the CLI and the workbench, which both derive their segment
    list from the document they hand over. Asserted anyway because the
    alternative is a `KeyError` in the middle of an hour of model time, and
    because a guard nothing exercises is a guard nobody can trust.
    """
    src, doc = _book(tmp_path, monkeypatch)
    stranger = {"id": "s9999", "kind": "para", "masked": "From another document."}
    context = _neighbour_context(doc, [doc["segments"][0], stranger], 1)
    assert set(context) == {doc["segments"][0]["id"]}
    item = json.loads(_user_message([stranger], [], "draft", context))[0]
    assert set(item) == {"id", "kind", "text"}


def test_no_payload_ever_carries_a_neighbours_source(tmp_path, monkeypatch):
    """The invariant the 2026-09-04 measurement bought, stated as an absence.

    This replaces a test that pinned the opposite — that an inlined neighbour
    with no source was dropped rather than sent empty — because the branch it
    guarded is gone. The property worth a test now is that nothing brings it
    back: whatever the window, whatever the batch size, whatever the caller's
    list looks like, an item is `id`, `kind`, `text` and at most an id on each
    side. A field carrying a paragraph the model was not asked to translate is
    what put a neighbour's words under a real segment's id.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    allowed = {"id", "kind", "text", "before_id", "after_id", "draft", "problems"}
    for window in (0, 1, 2, 5):
        cfg = dict(CFG, batch=dict(CFG["batch"], context=window))
        for size in (1, 2, len(segs)):
            stub = _Recorder()
            _run(src, cfg, segs, stub, monkeypatch, batch=size)
            for payload in stub.payloads:
                for item in payload:
                    assert set(item) <= allowed, (
                        f"window {window}, batch {size}: unexpected {set(item) - allowed}")


def test_neighbour_context_reaches_the_polish_mode_payload_as_well(tmp_path, monkeypatch):
    """Flow matters at least as much when revising, and the two modes share the builder.

    The neighbour stays *source* in both. Feeding the model its own preceding
    target is what D5 rejected: `batch.concurrency` defaults to 2, so it is not
    guaranteed to exist yet, and making it exist means translating prose serially.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    _run(src, CFG, segs, _Recorder(), monkeypatch, batch=len(segs))

    drafted = load_doc(src, "zh-TW")["segments"]
    stub = _Recorder()
    _run(src, CFG, drafted, stub, monkeypatch, batch=2, mode="polish")

    middle = next(p for p in stub.payloads if p[0]["id"] == segs[2]["id"])
    assert middle[0]["draft"] == done(middle[0])
    assert middle[0]["after_id"] == segs[3]["id"]
    # The batch edge references nothing, and that is the same rule as the draft
    # path's: `_attach` has one branch and both modes go through it.
    assert set(middle[0]) == {"id", "kind", "text", "after_id", "draft"}
