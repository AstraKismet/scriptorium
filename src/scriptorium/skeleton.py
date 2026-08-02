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

__all__ = ["MARKDOWN_MARKER", "render"]

#: What stands in for a segment nobody has translated yet, when the caller did
#: not ask for the source as a fallback. An HTML comment, so it is invisible in
#: rendered Markdown while still being greppable in the file — which is why it
#: cannot be the default for every format: in a plain-text novel the same string
#: is four words of visible junk. A format that needs another one passes it.
MARKDOWN_MARKER = "<!-- untranslated {id} -->"


def render(doc, cfg, polish=None, fallback=False, marker=MARKDOWN_MARKER):
    """Rebuild the target document from the skeleton. ``(text, missing)``."""
    by_id = {s["id"]: s for s in doc["segments"]}
    parts, missing = [], 0
    for node in doc["nodes"]:
        if node["t"] == "raw":
            parts.append(node["v"])
            continue
        seg = by_id[node["id"]]
        if seg.get("target"):
            text = unmask(seg["target"], seg["slots"])
            parts.append(polish(text) if polish else text)
        else:
            missing += 1
            parts.append(unmask(seg["masked"], seg["slots"]) if fallback
                         else marker.format(id=seg["id"]))
    return "".join(parts), missing
