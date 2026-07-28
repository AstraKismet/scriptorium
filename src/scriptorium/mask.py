"""Markup protection.

Non-translatable spans are replaced with opaque ``⟦n⟧`` placeholders before a
model ever sees the text, and restored afterwards. Doing this in code rather
than in a prompt is what makes structural fidelity a property of the pipeline
instead of a hope.

A slot is a **record**, not a string::

    {"original": "<b>", "role": "open", "pair_id": "p1", "can_reorder": False}

``role`` is ``open`` / ``close`` / ``standalone``; ``pair_id`` is ``None`` unless
the slot is half of a pair. The token the model sees is still a bare integer —
the type lives beside the slot map, never inside the token, which is what the
2026-07 decision on ⟦n⟧ settled and this does not reverse.
"""

import re
import unicodedata

PH_OPEN, PH_CLOSE = "\u27e6", "\u27e7"  # ⟦ ⟧
PH_RE = re.compile(r"\u27e6(\d+)\u27e7")

CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_RE = re.compile(f"[{CJK}]")

#: Ordered — earlier patterns win, so a URL inside a code span stays one unit.
INLINE_PATTERNS = [
    ("code", re.compile(r"``[^`]+``|`[^`\n]+`")),
    ("math", re.compile(r"\$\$[^$]+\$\$|\$[^$\n]+\$")),
    ("linkdest", re.compile(r"(?<=\])\([^)\s]+(?:\s+\"[^\"]*\")?\)")),
    ("refdest", re.compile(r"(?<=\])\[[^\]]*\]")),
    ("autolink", re.compile(r"<https?://[^>\s]+>|<[a-zA-Z0-9._%+-]+@[^>\s]+>")),
    ("url", re.compile(r"https?://[^\s)\]<>]+")),
    ("footnote", re.compile(r"\[\^[^\]]+\]")),
    ("htmltag", re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")),
    ("var", re.compile(r"\{\{[^}]*\}\}|\$\{[^}]*\}|\{[A-Za-z0-9_.]*\}|%\([A-Za-z0-9_]+\)[sd]|%[sd]")),
    ("entity", re.compile(r"&[a-zA-Z]+;|&#\d+;")),
]

_ASCII_RE = re.compile(r"[\x00-\x7f]+")

#: The one slot kind with a syntactic partner today. A code span or a URL is one
#: unit by construction, and `**bold**` is not masked at all — the measured gap
#: under invariant 3, whose repair is a separate package.
PAIRABLE = "htmltag"

_TAG_SHAPE_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)")


def _tag_shape(tag):
    """``(role, name)`` for a masked HTML tag, read as written.

    A self-closing tag is standalone whatever its name says. A void element
    written bare — ``<br>``, ``<img …>`` — needs no table here: it is an open
    whose close never arrives, and :func:`_pair_tags` leaves those standalone.
    Keeping the void list out is deliberate; it would be a second place to be
    wrong about HTML, and the general rule already covers it.
    """
    m = _TAG_SHAPE_RE.match(tag)
    if not m:
        return "standalone", None
    if tag.endswith("/>"):
        return "standalone", m.group(2).lower()
    return ("close" if m.group(1) else "open"), m.group(2).lower()


def _pair_tags(slots, tags):
    """Join ⟦open⟧ … ⟦close⟧ into pairs with a stack; the rest stay standalone.

    ``tags`` is ``(slot_id, tag text)`` in document order.

    Unbalanced markup is ordinary input rather than an error — an unclosed
    ``<br>``, a stray ``<`` in prose, a ``<div>`` whose partner is in another
    block — so this never raises and never guesses. An open whose close never
    arrives, and a close with no open, keep the standalone they were created
    with, which is why nothing here has a cleanup path.

    The search walks *down* the stack for a matching name rather than reading
    only its top. ``<b><br>x</b>`` is the case that decides it: under a
    top-of-stack rule the ``<br>`` shadows the ``<b>`` and a real pair goes
    unrecorded. Opens passed over are dropped as standalone, so ``<b><i>x</b>``
    pairs the b and leaves the i — what the markup actually says.
    """
    stack = []
    for sid, tag in tags:
        role, name = _tag_shape(tag)
        if role == "open":
            stack.append((name, sid))
        elif role == "close":
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] != name:
                    continue
                open_id = stack[k][1]
                # Named for the opening slot, so pairs read in document order and
                # the id is stable. The "p" keeps the two id spaces apart:
                # `slots[pair_id]` must not be a thing that happens to work.
                pid = f"p{open_id}"
                slots[open_id].update(role="open", pair_id=pid, can_reorder=False)
                slots[sid].update(role="close", pair_id=pid, can_reorder=False)
                del stack[k:]
                break


def mask(text, dnt=()):
    """Return ``(masked_text, {slot_id: record})``; see the module docstring.

    Inline markup is masked first, then verbatim do-not-translate terms.
    ASCII terms match at word boundaries, so ``Go`` will not match inside
    ``Google``.
    """
    slots = {}
    counter = [0]
    tags = []

    def take(value):
        counter[0] += 1
        n = str(counter[0])
        # Standalone until something pairs it. Unbalanced markup is the common
        # case, so it is the default rather than an error path — and
        # `can_reorder` says the slot may be repositioned freely against every
        # other placeholder, which a pair member may not be. It is derivable from
        # `role` today and stored anyway: a format with a standalone code that
        # must not move (XLIFF spells it canReorder="no") is the case it exists
        # for, and readers should not have to re-derive intent.
        slots[n] = {"original": value, "role": "standalone",
                    "pair_id": None, "can_reorder": True}
        return f"{PH_OPEN}{n}{PH_CLOSE}"

    def take_tag(m):
        ph = take(m.group(0))
        tags.append((str(counter[0]), m.group(0)))
        return ph

    out = text
    for name, pat in INLINE_PATTERNS:
        # re.sub scans left to right, so `tags` comes out in document order,
        # which is what the pairing stack needs.
        out = pat.sub(take_tag if name == PAIRABLE else lambda m: take(m.group(0)), out)
    _pair_tags(slots, tags)
    for term in dnt:
        if not term or term not in out:
            continue
        if _ASCII_RE.fullmatch(term):
            pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])")
            out = pat.sub(lambda _m, t=term: take(t), out)
        else:
            out = out.replace(term, take(term))
    return out, slots


def unmask(text, slots):
    """Restore placeholders, following nesting a few levels deep.

    ``slots`` is the record map :func:`mask` returns, and only that. Accepting
    the older ``{id: str}`` shape here as well was the alternative, and it loses:
    a state file that predates the records would then restore correctly while
    every pair in it silently read as standalone, which is the defect the records
    exist to remove, in a file that looks current. It is refused at the door
    instead — see ``store.load_doc``.
    """
    def repl(m):
        rec = slots.get(m.group(1))
        return m.group(0) if rec is None else rec["original"]

    out = text
    for _ in range(5):
        prev, out = out, PH_RE.sub(repl, out)
        if out == prev:
            break
    return out


_VARIANTS = re.compile(
    r"[\u27e6\u3010\u3014\u301a\[]{1,2}\s*([0-9\uff10-\uff19]+)\s*[\u27e7\u3011\u3015\u301b\]]{1,2}"
)


def repair_placeholders(text):
    """Undo the mangling models apply to brackets: 【3】, [[3]], ⟦ ３ ⟧ → ⟦3⟧."""
    def repl(m):
        return f"{PH_OPEN}{unicodedata.normalize('NFKC', m.group(1))}{PH_CLOSE}"

    return _VARIANTS.sub(repl, text)


def strip_placeholders(text):
    return PH_RE.sub(" ", text)


def placeholder_ids(text):
    return PH_RE.findall(text)
