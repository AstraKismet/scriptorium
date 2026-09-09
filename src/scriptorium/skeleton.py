"""Rebuilding a document from its skeleton, for every format at once.

``render`` walks ``doc["nodes"]`` and substitutes segment values into it. Nothing
in it is Markdown — a raw node is bytes the pipeline did not change and a segment
node is a hole, whatever produced them — so it lives here rather than in a
parser, where the second format would have had to copy it. A copy of this
function is exactly the kind of drift `docs/conventions/delegated-work.md` §6
lists: two renderers, one fallback branch fixed in one of them.

It is re-exported from :mod:`.mdparse` because that is where every existing
caller and test has always found it.

A format whose output is not simply the concatenation of its nodes — EPUB, whose
render has to write a container back — brings its own ``render`` and registers
it. The registry has a slot for that; both formats that exist today point it
here.
"""

from .docio import byte_spans
from .mask import target_map, unmask, unrenderable

__all__ = ["MARKDOWN_MARKER", "render", "render_blocks"]

#: What stands in for a segment nobody has translated yet, when the caller did
#: not ask for the source as a fallback. An HTML comment, so it is invisible in
#: rendered Markdown while still being greppable in the file — which is why it
#: cannot be the default for every format: in a plain-text novel the same string
#: is four words of visible junk. A format that needs another one passes it.
MARKDOWN_MARKER = "<!-- untranslated {id} -->"


def walk(doc):
    """The one iteration of ``doc["nodes"]`` in this project. ``(node, seg)`` per node.

    ``seg`` is the segment a segment node stands for and ``None`` for a raw
    node — the question every consumer of the skeleton starts by asking,
    answered once so that asking it is not a second walk.

    It exists because there are two consumers now rather than one.
    :func:`render_blocks` answers *what does this document say at this
    position*; :func:`source_map` answers *what bytes did this position come
    from*. Those are different questions and both are legitimate. The reason
    the record says there is one walk was never that only one question may be
    asked — it is that two walks are two orders, and an order that drifts is a
    block map and a byte map that disagree about which node is the fourth. One
    generator makes that unreachable rather than checked.
    """
    by_id = {s["id"]: s for s in doc["segments"]}
    for node in doc["nodes"]:
        yield (node, None) if node["t"] == "raw" else (node, by_id[node["id"]])


def source_parts(doc):
    """The exact ordered partition of the text ``fmt.parse`` was handed.

    One string per node, concatenating to that text exactly — the property
    ``tests/test_pipeline.py::identity_roundtrip`` and
    ``tests/test_textformat.py::_substituted`` have always asserted at the
    parser, read here off the stored document instead.

    Deliberately ``seg["source"]`` rather than ``unmask(seg["masked"],
    seg["slots"])``: the partition is a fact about the parse, and routing it
    through the mask would let a masking change move a byte boundary.

    It is separate from :func:`source_map` rather than inlined into it because
    it is the half a test can check with **no codec involved at all** — the
    parser's promise, stated on its own, so a failure says which of the two
    halves broke. It is also, for the same reason, the one that must not become
    the join of the other: written that way it would be dead code and every
    test comparing the two would compare a value to itself, which is exactly
    what ``Format.render`` was found doing.
    """
    return [node["v"] if seg is None else seg["source"] for node, seg in walk(doc)]


def source_map(doc, data):
    """Where in ``data`` each skeleton position came from. One row per node.

    ``[{"pos", "id", "kind", "start", "stop"}]`` in document order, with
    ``data[start:stop]`` the exact source bytes of that position — the
    non-canonical cp950 spelling, the byte-order mark, the ``\r`` that
    ``docio.split_terminator`` removed before the parser ever saw the text.
    Their concatenation is the file.

    **This is the whole of invariant 2a's byte guarantee, and it is a query.**
    The bytes are not rebuilt from anything: they are the file, kept once by
    ``lx extract`` and handed back by slicing. What is computed is only *where*
    each node's characters sit in them, and that is computed by consuming those
    characters against a fresh decode of those bytes — so the two outcomes are
    the exact span and :class:`docio.ByteSpanMismatch`, never a plausible
    wrong slice. Segments are covered as well as raw nodes, which is not a
    bonus: 十 and 卅 are the two cp950 residues that are *always* translatable
    text and therefore never reach the skeleton, so a raw-node-only
    representation cannot reach them at all.

    Costs a byte-at-a-time decode of the whole document and is therefore not on
    any pipeline path. ``lx extract`` does not call it, ``lx render`` does not
    call it, and the workbench cannot reach it.
    """
    rows = [{"pos": i, "id": None if seg is None else seg["id"],
             "kind": None if seg is None else seg.get("kind")}
            for i, (_node, seg) in enumerate(walk(doc))]
    spans = byte_spans(data, doc.get("encoding") or "utf-8",
                       source_parts(doc), doc.get("eol", "\n"))
    for row, (start, stop) in zip(rows, spans):
        row["start"], row["stop"] = start, stop
    return rows


def render_blocks(doc, cfg, polish=None, fallback=False, marker=MARKDOWN_MARKER):
    """The rendered document as an ordered list of records. ``(blocks, missing)``.

    One record per node, in document order, so the concatenation of their ``text``
    is the document :func:`render` returns — that is the whole point of the shape
    and it is why :func:`render` is written in terms of this rather than beside
    it. **A second walk of ``doc["nodes"]`` is what this exists to prevent**: two
    walks are two answers to "what does this document say at this position", and
    the one a reading view uses would be the one nobody renders from.

    A record is::

        {"id": "s0003" | None, "kind": "para" | None,
         "from": "target" | "source" | "marker" | None, "text": "…"}

    ``id`` is ``None`` for a skeleton run, and that is the discriminator — a
    null-when-absent field rather than a ``type`` tag, which is this project's own
    idiom for the same question elsewhere.

    ``from`` names **which branch below produced the text**, and it is not
    derivable anywhere else. `status` is not it: the branch tests a *truthy*
    target while ``store`` derives ``status`` from a *stripped* one, so a target
    of three spaces renders its own text, reports ``pending``, and is not counted
    in ``missing``. Nor is ``missing`` it, which is a count and stays one — this
    is the per-block form that lets it stay an integer.

    **A truthy target is not enough**, since ``contract_version`` 4: a wording
    :func:`mask.unrenderable` refuses takes the same branch as one that was never
    written, so ``missing`` counts it and ``from`` says ``marker`` or ``source``.
    A target naming a ``⟦n⟧`` this document has no slot for used to be written
    into the file token and all — divergence (31) — and a gate is what invariant 2
    asks for where a downstream check was all there was. The predicate lives in
    :mod:`.mask` rather than here because `checks.check_segment` decides
    waivability with the *same call on the same segment*, so the exit code and
    the delivered bytes cannot come to disagree about which wording is writable.
    Its domain is what the placeholder substitution does and nothing wider: a
    target that opens a list where the source had a paragraph is `containment`'s,
    is an error at `lx check`, and is still written here.

    ``text`` is neither ``seg["target"]``, which is stored masked, nor
    ``seg["masked"]``: it is what this position contributes to the rendered file,
    after unmasking and after ``polish``. **Unmasked against the map the wording's
    ids mean** — ``target_slots`` where a re-parse moved the numbering out from
    under a kept wording, the segment's own ``slots`` otherwise, which is every
    ordinary segment. The document's line terminator is *not*
    applied here, because a terminator is a document-level fact and this function
    is handed no document-level facts — ``cli.do_blocks`` re-imposes it, once, the
    way ``cli.do_render`` always has.
    """
    blocks, missing = [], 0
    for node, seg in walk(doc):
        if seg is None:
            blocks.append({"id": None, "kind": None, "from": None, "text": node["v"]})
            continue
        if seg.get("target") and not unrenderable(seg):
            # **The map this wording's ids actually mean, which is not always the
            # segment's own.** `save_doc` rewrites `slots` from the fresh parse on
            # every extract, and the divergence (24) keep path leaves an older
            # wording sitting on a newer segment — so `cli.do_extract` pins the
            # map that wording was written in as `target_slots`, written only
            # when the two differ. `store.prior_targets` and `store.tm_record`
            # both already read it first, each saying why; this was the one
            # reader of a stored target that did not, and the cost was measured
            # on 2026-09-01: a `config/dnt.txt` edit that swapped one protected
            # term for another rendered `Alpha 遇見 met。` where the reviewer had
            # written `Beta`, with `lx check` green, `missing` 0 and `from`
            # `"target"` — nothing anywhere reporting it. Deterministic, so
            # invariant 5 says corrected rather than reported; the `numbering`
            # rule reports the segment as well, because the wording still does
            # not speak the numbering the source has now.
            text = unmask(seg["target"], target_map(seg))
            source = "target"
            text = polish(text) if polish else text
        else:
            missing += 1
            source = "source" if fallback else "marker"
            text = (unmask(seg["masked"], seg["slots"]) if fallback
                    else marker.format(id=seg["id"]))
        blocks.append({"id": seg["id"], "kind": seg.get("kind"),
                       "from": source, "text": text})
    return blocks, missing


def render(doc, cfg, polish=None, fallback=False, marker=MARKDOWN_MARKER):
    """Rebuild the target document from the skeleton. ``(text, missing)``."""
    blocks, missing = render_blocks(doc, cfg, polish=polish, fallback=fallback,
                                    marker=marker)
    return "".join(b["text"] for b in blocks), missing
