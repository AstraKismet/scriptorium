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

#: What makes a block worth sending to a model: a letter somewhere in it. A rule
#: of thumb rather than a definition \u2014 a line of `* * *`, a row of digits, a
#: horizontal rule and a run of punctuation all have nothing to translate and
#: belong in the skeleton, where they round-trip for free.
#:
#: The range is the one `mdparse` has always used, character for character,
#: because this predicate was extracted from it and a "while we are here"
#: widening would move segment boundaries in the corpus. It is therefore *not*
#: `cli._LETTER`, which excludes \u00d7 and \u00f7 from the same block; that difference is
#: recorded rather than reconciled. Kana and Hangul are outside it too, which is
#: a real limitation for a Japanese or Korean source and one both formats share.
_TRANSLATABLE_RE = re.compile(r"[A-Za-z\u00c0-\u024f" + CJK + r"]")


def has_translatable_text(s):
    """Whether a block has anything a model should see. One copy, two formats."""
    return bool(s.strip()) and bool(_TRANSLATABLE_RE.search(s))

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


def tag_shape(tag):
    """``(role, name)`` for a masked HTML tag, read as written.

    Public since 2026-09-03 because `checks.unbalanced_markup` needs the same
    reading: whether losing a slot leaves the document unbalanced is a question
    about the tag text, and asking it twice in two spellings is how the two
    answers come to differ.

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
        role, name = tag_shape(tag)
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
        # `term_pattern` rather than the rule inline, because `reseat` has to
        # decide "does this term occur here" exactly the way this does.
        out = term_pattern(term).sub(lambda _m, t=term: take(t), out)
    return out, slots


def target_map(seg):
    """The map a segment's **stored target**'s ids mean.

    One answer, because there were two readers of a stored target and they
    disagreed for a fortnight. `save_doc` rewrites `slots` from the fresh parse
    on every extract and the divergence (24) keep path leaves an older wording
    sitting on a newer segment, so `cli.do_extract` pins the wording's own map as
    `target_slots` — written only when the two differ, which is why the ordinary
    segment costs nothing here.

    `skeleton.render_blocks` and `checks.containment_problems` both go through
    this. They must: the containment rule asks "what does this target do to the
    block it lands in", answered on the unmasked text *because that is what
    reaches the file* — so a render reading one map while the rule reads the
    other reports on bytes nobody writes. Measured 2026-09-01, when the render
    moved first and the rule did not: a stranded segment rendering
    `Note 說。` was failed at error severity for opening a list, which the
    rendered line does not do, and `lx run` then refused to render a document
    that renders correctly.

    It is here rather than in `store` so that `checks` and `skeleton` do not gain
    an import edge to it for one dictionary lookup, and because "which slot map"
    is this module's question.
    """
    return seg.get("target_slots") or seg.get("slots") or {}


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


def term_pattern(term):
    """How :func:`mask` decides a term occurs here, as a compiled pattern.

    One rule, two callers: masking a source, and re-seating a wording that was
    masked against a different map. A plain substring search reads `API` inside
    `APIs` and refuses an ordinary sentence for having two occurrences where the
    source had one — measured 2026-08-17.
    """
    if _ASCII_RE.fullmatch(term):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])")
    return re.compile(re.escape(term))


def reseat(text, was, now):
    """Move a wording from the numbering it was written in into another. ``(text, why)``.

    ``was`` is the slot map the wording's ``⟦n⟧`` refer to and ``now`` is the map
    it has to speak in. The wording is unmasked against ``was`` — exact, no
    inference, because that map is what its ids meant — and then every original
    ``now`` holds is seated back into the prose **by content**.

    **By content, and never by a second call to** :func:`mask`. Re-masking looks
    like the same operation and is not: `mask` numbers by position in the text it
    is given, so a translation that legitimately reordered two code spans comes
    back with them swapped, silently, with an id set that matches. Measured
    2026-08-17 on ``Run `alpha` then `beta` to finish.`` and a target that put
    them the other way round. Seating by content cannot do that: mask-then-unmask
    is the identity for the spans it seats, so the rendered bytes always equal the
    wording that went in, and the only thing a wrong answer can change is the id
    multiset — which the acceptance path already compares.

    Originals are seated longest first and never into a span already claimed,
    which is `mask`'s own precedence: it masks the longer term first, so ``York``
    finds nothing left inside ``New York``. When an original occurs a different
    number of times than the map has ids for it, the seating is refused rather
    than guessed — that is the ambiguous case, and a guess there is what puts one
    character's name where another's belongs.
    """
    literal = unmask(text, was)
    ids_by_original = {}
    for pid, rec in (now or {}).items():
        ids_by_original.setdefault(rec["original"], []).append(pid)

    claimed, seats = [], []
    for original in sorted(ids_by_original, key=len, reverse=True):
        ids = sorted(ids_by_original[original], key=int)
        spans = [m.span() for m in term_pattern(original).finditer(literal)
                 if not any(m.start() < end and start < m.end() for start, end in claimed)]
        if len(spans) != len(ids):
            return None, (f"cannot place {original!r}: this segment has "
                          f"{len(ids)} of it and the wording has {len(spans)}")
        claimed.extend(spans)
        seats.extend(zip(spans, ids))

    out = literal
    for (start, end), pid in sorted(seats, reverse=True):
        out = f"{out[:start]}{PH_OPEN}{pid}{PH_CLOSE}{out[end:]}"
    return out, None

