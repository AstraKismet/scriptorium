"""Neighbour context: what a request item is told about the segments around it.

A segment used to reach the model alone — no antecedent for a pronoun, no
speaker for a line of dialogue, no tense or register to hold to — and the retry
path, which is where a hard sentence actually ends up, sent one segment by
itself. For a reference paragraph that costs almost nothing. For prose it
removes exactly what prose depends on.

Four properties keep the fix honest, and each has a test here:

* a neighbour already in the payload is *referenced by id*, never repeated, so
  the default window costs the two edges of a batch rather than trebling every
  request;
* the two edges, and a retried segment on both sides, carry the source inline;
* adjacency is **the document's**, never the order of the caller's list, which
  `lx repair` and `lx translate --ids` both hand over non-contiguous;
* a neighbour is context the model may read and must not answer for.

`docs/decisions.md`, 2026-07-29, D5.
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

DONE = "已翻譯。"


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
        return json.dumps({i["id"]: DONE for i in items}, ensure_ascii=False)


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


def test_neighbour_batch_edges_carry_the_source_across_the_boundary(tmp_path, monkeypatch):
    """The two edges are the only places a batch has nothing to point at.

    `_chunks` slices consecutive segments, so adjacency inside a batch is real —
    but it stops at the boundary, and before this the first and last segment of
    every batch were as contextless as a segment sent alone.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    ids = [s["id"] for s in segs]
    stub = _Recorder()
    _run(src, CFG, segs, stub, monkeypatch, batch=2)

    middle = next(p for p in stub.payloads if p[0]["id"] == ids[2])
    assert middle[0]["before_text"] == segs[1]["masked"]   # across the boundary
    assert middle[0]["after_id"] == ids[3]                 # inside the batch
    assert middle[1]["before_id"] == ids[2]
    assert middle[1]["after_text"] == segs[4]["masked"]

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


def test_neighbour_on_retry_arrives_in_full_on_both_sides(tmp_path, monkeypatch):
    """The path that needs context most, and the one that had none at all.

    `retry_one` sends a single segment, so there is no batch to borrow from and
    both sides are inlined. That token cost is one segment's worth of request,
    accepted deliberately: this is where a hard sentence ends up.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    ids = [s["id"] for s in segs]

    def answer(items):
        # Skip the third id in the batch reply and answer it when it comes back
        # alone — which is exactly how `run_batch` falls through to `retry_one`.
        skip = items[2]["id"] if len(items) > 2 else None
        return json.dumps({i["id"]: DONE for i in items if i["id"] != skip},
                          ensure_ascii=False)

    stub = _Recorder(answer)
    _run(src, CFG, segs, stub, monkeypatch, batch=len(segs))

    retry = stub.payloads[-1]
    assert [i["id"] for i in retry] == [ids[2]], "the retry should carry one segment"
    assert retry[0]["before_text"] == segs[1]["masked"]
    assert retry[0]["after_text"] == segs[3]["masked"]
    assert "before_id" not in retry[0] and "after_id" not in retry[0]
    assert all(s.get("target") for s in load_doc(src, "zh-TW")["segments"])


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
        out = {i["id"]: DONE for i in items}
        out[ids[1]] = echo          # inlined before the requested pair
        out[ids[4]] = echo          # inlined after it
        return json.dumps(out, ensure_ascii=False)

    stub = _Recorder(answer)
    applied, failures, refused = _run(src, CFG, [segs[2], segs[3]], stub, monkeypatch,
                                      batch=2)

    assert (applied, failures, refused) == (2, [], [])
    after = {s["id"]: s.get("target") for s in load_doc(src, "zh-TW")["segments"]}
    assert after[ids[2]] == after[ids[3]] == DONE
    assert after[ids[1]] is None and after[ids[4]] is None, "a neighbour was written"


def test_neighbour_context_follows_the_document_and_not_the_callers_list(
        tmp_path, monkeypatch):
    """`lx repair` passes the failing segments; `lx translate --ids` passes a set.

    Taking either as document order would tell the model that segment 2 and
    segment 5 are consecutive prose. A confident lie about flow is worse than no
    context, so adjacency is read off `doc["segments"]` — the same authority
    `tone` and `eol` already have for facts about the document.
    """
    src, doc = _book(tmp_path, monkeypatch)
    segs = doc["segments"]
    stub = _Recorder()
    _run(src, CFG, [segs[1], segs[4]], stub, monkeypatch, batch=6)

    payload = stub.payloads[0]
    assert payload[0]["after_text"] == segs[2]["masked"], "the caller's next, not the document's"
    assert payload[1]["before_text"] == segs[3]["masked"], "the caller's previous, not the document's"
    assert "after_id" not in payload[0] and "before_id" not in payload[1]


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

    # Widened, and every neighbour inlined, which is what pins the ordering:
    # a side reads in document order and a joined pair is what the document says
    # there — so `before_text` ends with the immediately preceding segment.
    wide = dict(CFG, batch=dict(CFG["batch"], context=2))
    stub = _Recorder()
    _run(src, wide, segs, stub, monkeypatch, batch=1)
    item = next(p[0] for p in stub.payloads if p[0]["id"] == segs[3]["id"])
    assert item["before_text"] == f"{segs[1]['masked']}\n\n{segs[2]['masked']}"
    assert item["after_text"] == f"{segs[4]['masked']}\n\n{segs[5]['masked']}"
    assert _CONTEXT_RULES in stub.systems[0]

    # A window wider than the room left on one side truncates, and does not
    # wrap: the second segment of the document has one segment before it, not
    # two and not none. `ids[i - window:i]` without the clamp slices `ids[-1:1]`
    # here, which is empty.
    second = next(p[0] for p in stub.payloads if p[0]["id"] == segs[1]["id"])
    assert second["before_text"] == segs[0]["masked"]

    # The same window with room to reference: two ids a side, space separated,
    # in document order. This is the only place more than one id lands in a
    # field, and `lx repair` reaches it whenever two failing segments are close.
    stub = _Recorder()
    _run(src, wide, segs, stub, monkeypatch, batch=len(segs))
    item = stub.payloads[0][3]
    assert item["before_id"] == f"{segs[1]['id']} {segs[2]['id']}"
    assert item["after_id"] == f"{segs[4]['id']} {segs[5]['id']}"

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
    assert set(context[0]) == {doc["segments"][0]["id"]}
    item = json.loads(_user_message([stranger], [], "draft", context))[0]
    assert set(item) == {"id", "kind", "text"}


def test_neighbour_context_drops_a_neighbour_with_no_source_rather_than_sending_an_empty_one():
    """An empty `before_text` is noise with a shape that invites the model to fill it in."""
    doc = {"segments": [
        {"id": "s0001", "kind": "para", "masked": ""},
        {"id": "s0002", "kind": "para", "masked": "The gate stood open."},
        {"id": "s0003", "kind": "para", "masked": "She stopped in the road."},
    ]}
    target = doc["segments"][1]
    context = _neighbour_context(doc, [target], 1)
    item = json.loads(_user_message([target], [], "draft", context))[0]
    assert set(item) == {"id", "kind", "text", "after_text"}
    assert item["after_text"] == "She stopped in the road."


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
    assert middle[0]["draft"] == DONE
    # Source, not target: the drafts are all `DONE` and these are English.
    assert middle[0]["before_text"] == segs[1]["masked"]
    assert middle[0]["after_id"] == segs[3]["id"]
    assert all(DONE not in i.get(k, "")
               for i in middle for k in ("before_text", "after_text"))
