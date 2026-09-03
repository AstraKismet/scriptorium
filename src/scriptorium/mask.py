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
from collections import Counter

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

    Public since 2026-09-03 because :func:`unrenderable` — then
    `checks.unbalanced_markup`, and moved here the same day — needs the same
    reading: whether losing a slot leaves the document unbalanced is a question
    about the tag text, and asking it twice in two spellings is how the two
    answers come to differ. Ask it through :func:`_is_tag_original` rather than
    directly unless the text is known to have come from the ``htmltag`` pattern;
    read that function for the autolink it otherwise misreads.

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


_AUTOLINK_RE = dict(INLINE_PATTERNS)["autolink"]


def _is_tag_original(original):
    """Whether losing or repeating this slot unbalances the document's markup.

    :func:`tag_shape` reads the text as written, which is right for its own
    caller — :func:`_pair_tags` only ever hands it text the ``htmltag`` pattern
    matched. Asked of an arbitrary slot original it over-reaches:
    ``<https://example.com/a>`` and ``<me@example.com>`` both match
    :data:`_TAG_SHAPE_RE` and read as an open ``https`` and an open ``me``.
    :func:`mask` masks both under the ``autolink`` pattern, which is ordered
    *before* ``htmltag``, so neither is ever a tag slot — and calling one a tag
    made a translation that simply dropped a link an **unwaivable** `tags` error,
    and would have taken the whole paragraph out of the rendered file once
    :func:`unrenderable` reached the render. That is a misread rather than a
    conservative approximation, so it is answered from this module's own pattern
    table and there is no second place to be wrong about which of the two a slot
    holds. Measured 2026-09-03.
    """
    if _AUTOLINK_RE.fullmatch(original):
        return False
    return tag_shape(original)[0] in ("open", "close")


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


# ── what a stored target would do to the document ──────────────────────────
#
# Three functions, and they are here rather than in `checks.py` for
# :func:`target_map`'s own reason: `checks` and `skeleton` both ask them, both
# already import this module, and neither may import the other —
# ``skeleton`` → ``checks`` → ``mdparse`` → ``skeleton`` is a real cycle that
# raises ``ImportError`` from every entry point (measured 2026-09-03). Two
# callers asking one question in two modules is how the exit code and the
# rendered file come to disagree about which wording is writable, which is the
# defect `target_map` itself was extracted to remove.


def pair_problems(target, slots):
    """Messages for paired placeholders the target broke; empty when it did not.

    Two rules, both decidable: an open comes before its close, and two pairs
    either nest or stay apart. Standalone slots keep multiset semantics, because
    moving a URL or a code span is an ordinary thing for a translation to do — a
    pair is not. Until 2026-07-28 a target of ``⟦2⟧粗體⟦1⟧`` against a source of
    ``⟦1⟧粗體⟦2⟧`` reported zero issues and rendered ``</b>粗體<b>``: a layout
    defect in Markdown, and in XHTML a file that does not open.

    Deliberately *not* checked: a pair that stops containing another without
    crossing it. Reassociating emphasis is a meaning decision a translator is
    allowed to make, and a rule against it would fail correct work — the
    false-positive trap the zh-TW lexicon was audited for on the same date.

    Messages name slot ids and never slot contents. Validator messages are fed
    back to the model as ``problems`` by ``translate._user_message``, so putting
    the original ``<b>`` in one would show it markup, against invariant 3.

    It moved here from `checks.py` on 2026-09-03, unchanged, because
    :func:`unrenderable` has to ask it: the swap above is a placeholder
    substitution that produces malformed bytes, and the render may not write one.
    `checks.pair_problems` still resolves — the name is imported back — so the
    rule and its messages have moved module and not home.
    """
    pos = {}
    for m in PH_RE.finditer(target):
        pos.setdefault(m.group(1), m.start())

    halves = {}
    for sid, rec in slots.items():
        if rec.get("pair_id"):
            halves.setdefault(rec["pair_id"], {})[rec.get("role")] = sid

    out, spans = [], []
    for pid in sorted(halves):
        o, c = halves[pid].get("open"), halves[pid].get("close")
        if o is None or c is None or o not in pos or c not in pos:
            continue          # a half that never arrived is already a mismatch
        if pos[o] > pos[c]:
            out.append(f"placeholder pair inverted: ⟦{o}⟧ opens and must come "
                       f"before ⟦{c}⟧")
        else:
            spans.append((pos[o], pos[c], o, c))

    for i, (a_open, a_close, a1, a2) in enumerate(spans):
        for b_open, b_close, b1, b2 in spans[i + 1:]:
            if a_open < b_open < a_close < b_close or b_open < a_open < b_close < a_close:
                out.append(f"placeholder pairs cross: ⟦{a1}⟧…⟦{a2}⟧ and "
                           f"⟦{b1}⟧…⟦{b2}⟧ must nest or stay apart")
    return out


def unresolved(seg):
    """The ids in this segment's stored target that the render's map cannot answer.

    :func:`unmask` returns an id it holds no record for **verbatim**, so every id
    here is a literal ``⟦n⟧`` in the delivered file —
    `docs/contracts/workbench-http.md` divergence (31).

    Two halves, and each is a measurement rather than taste, because the obvious
    other spelling of each was in the code and was wrong.

    **Asked of** :func:`target_map` **alone.** That is the map the bytes are
    actually substituted from. The predicate this replaced read the segment's own
    ``slots`` as well — it only had to answer waivability, where the union errs
    towards refusing — and on a stranded wording that calls an id known which the
    render never consults.

    **And asked of every id the wording carries**, not of the ids it carries *in
    excess* of the source. Those are different questions and one document
    separates them. On ``Xenon and Yttrium here.`` with `config/dnt.txt` naming
    only ``Xenon``, type a ``⟦2⟧`` the segment has no slot for, then widen the
    list so the re-parse numbers ``Yttrium`` first: `mask.reseat` refuses, the
    divergence (24) keep path pins ``target_slots = {1: Xenon}`` against fresh
    ``slots = {1: Yttrium, 2: Xenon}``, and the two id multisets now agree by
    coincidence. Measured 2026-09-03: `lx check` exited **0** and `lx render`
    wrote ``Xenon 和 ⟦2⟧ 在此。`` into the file. Nothing in this project asked
    either question the right way round before that day.
    """
    now = target_map(seg)
    return sorted({sid for sid in PH_RE.findall(seg.get("target") or "")
                   if sid not in now})


def unrenderable(seg):
    """Whether substituting this segment's stored target would be malformed.

    This is the whole of what a reviewer may not overrule **and** the whole of
    what a render may not write, stated once as a property of the substituted
    *bytes*. `skeleton.render_blocks` decides with it whether a stored wording is
    a translation at all; `checks.check_segment` decides with it whether a
    reviewer's judgement can overrule the `tags` finding it made. It takes a
    **segment** rather than a pre-computed ``(lost, extra, target, maps)`` — the
    shape it had while it lived in `checks.py` and answered only the second
    question — because handed the pieces the two callers were free to hand it
    different ones, and the map is exactly what they would have differed on.

    Four ways a stored wording stops being a claim about the translation and
    becomes one about the document:

    1. **An id the render's map cannot resolve** — :func:`unresolved`, which
       carries that half's own measurement.

    2. **A repeated id whose original is a tag.** ``⟦1⟧x⟦1⟧`` over an ``<b>``
       renders ``<b>x<b>``. A repeated *term* or code span renders twice and is
       perfectly legal, which is why this asks what the original is rather than
       refusing every repetition — refusing every repetition was the first
       spelling and it made `lx run` permanently refuse a correct document.

    3. **A lost tag half whose partner is still standing.** ``<em>`` that never
       closes, or ``</em>`` that never opened.

    4. **A pair the wording inverted or crossed** — :func:`pair_problems`, asked
       of the same map the render substitutes from rather than of the segment's
       own, for the reason `checks.containment_problems` already asks it that
       way. Without this clause ``⟦2⟧粗體⟦1⟧`` over ``⟦1⟧bold⟦2⟧`` satisfies
       every other test — the multisets agree and both ids resolve — and writes
       ``</b>粗體<b>``, which `lx check` has always reported at error severity
       and never let a reviewer waive. Measured 2026-09-03.

       *The two maps cannot disagree here, and the choice is still not
       arbitrary.* A carryover matches on the content hash, so a stranded
       segment's source text is the one its wording was written against, and
       :func:`mask` numbers every inline match before any do-not-translate term
       — so a tag's id, and therefore every ``pair_id``, is a pure function of
       that text. Only term slots move, and they are always standalone. A
       mutation pass on 2026-09-03 found the other spelling survives for exactly
       that reason; the equivalence is pinned by
       `tests/test_memory.py::test_which_map_the_pair_rule_reads_cannot_matter_on_a_stranded_segment`
       so that a change breaking it is reported rather than silent.

    Case 3 is why this reads the tag text through :func:`tag_shape` instead of
    trusting ``pair_id``. :func:`_pair_tags` pairs only *within* a segment, so a
    ``<span>`` opened in one paragraph and closed in the next leaves both halves
    ``role: "standalone", pair_id: None`` — and the first version of this
    predicate, which keyed on ``pair_id``, called that waivable. Measured
    2026-09-03 on an ordinary Markdown file: waive the closing segment,
    `lx check` exits **0**, and `lx render` writes an unclosed
    ``<span class="note">``. A whole pair lost together is still fine, and stays
    waivable, because nothing is left standing.

    *Cost, deliberate:* a void element written bare — ``<br>``, ``<img …>`` —
    reads as an open whose close never arrives, so losing one is called malformed
    when the bytes would in fact be fine. Erring that way is the point:
    :func:`tag_shape` says a void list here "would be a second place to be wrong
    about HTML", and the cost of the false negative is a reviewer re-wording one
    segment where the cost of the false positive is a broken file under a green
    check. An **autolink** is not that cost and is excluded outright: `mask` masks
    ``<https://example.com/a>`` under its own pattern, before ``htmltag`` ever
    sees it, so it is never a tag slot — but :func:`tag_shape` reads it as an open
    ``https`` element, which made losing one unwaivable and, once the render
    consults this, would have taken the whole paragraph out of the file. That is
    a misread rather than a conservative approximation, and it is asked of this
    module's own pattern table so there is no second place to be wrong about it.

    Whether an id names a **tag** is asked of both maps — the wording's and the
    segment's — and that asymmetry with case 1 is deliberate: a lost id is a
    source-side id and an extra one is a target-side id, so on a stranded segment
    those are different maps, and an id is a tag if either says so. Erring wide
    there refuses; erring wide in case 1 would permit.

    What this deliberately does **not** name is a wording whose placeholders
    merely do not balance — a translation that dropped a protected term, or that
    legally names a code span twice. Those render as ordinary prose that may be
    missing something, which is the judgement call a reviewer is for, and
    `lx check` reports every one of them on the `tags` rule whatever this answers.
    Nor does it reach the rest of invariant 2b: a target that opens a list where
    the source had a paragraph is `containment`'s, and it is written into the file
    as before. **The domain of this function is what the placeholder substitution
    does, and nothing wider** — see `docs/decisions.md`, 2026-09-03.
    """
    target = seg.get("target") or ""
    now = target_map(seg)
    if unresolved(seg) or pair_problems(target, now):
        return True

    present = Counter(PH_RE.findall(target))
    source = Counter(PH_RE.findall(seg.get("masked") or ""))
    if present == source:
        return False

    merged = {}
    for slot_map in (now, seg.get("slots")):
        for sid, rec in (slot_map or {}).items():
            if isinstance(rec, dict):
                merged.setdefault(sid, []).append(rec)

    def is_tag(sid):
        return any(_is_tag_original(rec.get("original") or "")
                   for rec in merged.get(sid, ()))

    for sid in present - source:
        if is_tag(sid):
            return True

    partners = {}
    for sid, recs in merged.items():
        for rec in recs:
            if rec.get("pair_id"):
                partners.setdefault(rec["pair_id"], set()).add(sid)

    for sid in source - present:
        if not is_tag(sid):
            continue
        pair = set()
        for rec in merged.get(sid, ()):
            pair |= partners.get(rec.get("pair_id"), set())
        # The whole pair went together, so nothing is left standing.
        if pair and not any(present.get(other) for other in pair):
            continue
        return True
    return False


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

