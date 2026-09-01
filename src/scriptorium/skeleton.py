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

from .mask import unmask

__all__ = ["MARKDOWN_MARKER", "render", "render_blocks"]

#: What stands in for a segment nobody has translated yet, when the caller did
#: not ask for the source as a fallback. An HTML comment, so it is invisible in
#: rendered Markdown while still being greppable in the file — which is why it
#: cannot be the default for every format: in a plain-text novel the same string
#: is four words of visible junk. A format that needs another one passes it.
MARKDOWN_MARKER = "<!-- untranslated {id} -->"


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
    by_id = {s["id"]: s for s in doc["segments"]}
    blocks, missing = [], 0
    for node in doc["nodes"]:
        if node["t"] == "raw":
            blocks.append({"id": None, "kind": None, "from": None, "text": node["v"]})
            continue
        seg = by_id[node["id"]]
        if seg.get("target"):
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
            text = unmask(seg["target"], seg.get("target_slots") or seg["slots"])
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
