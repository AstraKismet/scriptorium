"""Orchestration between the deterministic pipeline and a language model.

The model is asked to do one thing: turn a list of sentences into a list of
sentences. Everything that can go wrong mechanically — malformed JSON, dropped
placeholders, missing ids — is caught here and retried per segment, so one bad
sentence never costs a whole batch.
"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from .checks import check_segment, workable
from .config import (
    DEFAULT_TONE,
    canonical_tone,
    load_dnt,
    load_glossary,
    load_style,
    resolve_route,
)
from .mask import placeholder_ids, repair_placeholders, reseat
from .normalize import normalize, reseat_outer_blanks
from .providers import build as build_provider

# ── prompts ────────────────────────────────────────────────────────────────

_BASE_RULES = """\
You translate segments of a document from {source_lang} into {target_lang}.

Placeholders written as ⟦1⟧ ⟦2⟧ stand for code, URLs, formulas, template
variables, and protected product names. Treat each one as an opaque noun:
reproduce it exactly, move it wherever the target grammar needs it, and never
invent, merge, drop, or renumber one. The number inside identifies which piece
of content it is.

Translate only the prose around them. Do not explain, do not add notes, do not
translate a segment into two segments.

Tone: {tone}.

Reply with a single JSON object mapping each segment id to its translation, and
nothing else — no code fences, no commentary:

{{"s0001": "...", "s0002": "..."}}"""

#: What a language needs whatever register it is written in: the terms, and the
#: punctuation width. Register-independent by definition and **shared, not
#: duplicated** — the zh-TW list is the output of the 2026-07-28 invariant-4
#: audit, and two copies of it are how that audit gets silently undone.
#:
#: The sense-split half carries the terms that audit removed from
#: `checks.py::ZH_TW_LEXICON`: each is correct Taiwanese usage in one sense, so
#: choosing between them needs the sentence, which invariant 4 keeps out of a
#: validator and puts here instead. Do not compress it back into "X not Y" —
#: that phrasing is what taught the model the wrong half.
_LANG_TERMS = {
    "zh-TW": """\
Converting characters is not enough — use the vocabulary Taiwan uses:
軟體 not 軟件, 網路 not 網絡, 執行緒 not 線程, 快取 not 緩存,
螢幕 not 屏幕, 資訊 not 信息, 列印 not 打印, 影片 not 視頻.
Some words are correct in Taiwan in one sense and wrong in another, so choose by
meaning rather than by reflex: 程式 for software but 程序 for a legal or
operational procedure; 資料 for data but 數據 for measured readings; 品質 for
quality but 質量 for physical mass; 支援 for technical support but 支持 for
endorsement; 物件 for an OOP object but 對象 for a person or a subject of study;
函式 for code but 函數 for mathematics; 預設 for a default but 默認 for
acquiescence; 選單 for a UI but 菜單 for food. The same split applies to 指標 vs
指針 (a clock's hand), 行程 vs 進程 (a historical process), 登入 vs 登錄 (to
place on a register), 互動 vs 交互 (交互作用), 佇列 vs 隊列 (a military
formation), 音訊 vs 音頻 (audio frequency), 智慧 vs 智能 (智能障礙), 檢視 vs
視圖 (正視圖), 文字 vs 文本 (a text under analysis), 使用者 vs 用戶端.
Use full-width ，。！？；： and 「」 inside Chinese text.""",
    "ja": "Use 全角 punctuation 、。 and 「」.",
}

#: ``(language, register)`` -> ``(what the target variety is, how it is written)``.
#: The shared block above goes between the two halves, so a register cannot
#: acquire a private copy of the terminology by being edited on its own.
#:
#: Selected by ``doc["tone"]``, and an unrecognized value falls back to the
#: default register — `tone` stays free text for the ``Tone:`` line of
#: `_BASE_RULES`, which is where an unrecognized one still has its effect.
#: Before 2026-07-29 there was one brief per language and it ended in the
#: documentation-register sentence unconditionally, so it overrode the knob two
#: paragraphs above it: `docs/decisions.md`, D4.
_LANG_BRIEFS = {
    ("zh-TW", DEFAULT_TONE): (
        "Target is Traditional Chinese as used in Taiwanese technical documentation.",
        """\
Write technical documentation register: neutral-formal, subject usually dropped,
請 for instructions, active voice rather than 被. Nominalize headings.""",
    ),
    ("zh-TW", "literary"): (
        "Target is Traditional Chinese as it is written in novels published in Taiwan.",
        """\
Write narrative prose. This is a novel, and the target has to read as a book
written in Chinese rather than an English one showing through it: build each
sentence in Chinese order instead of following the English clause by clause, and
let it break where Chinese would break it.
Speech goes inside 「」 and a quotation inside speech inside 『』; a book, a film
or a song takes 《》. An em dash is —— and an ellipsis is ……, two characters each.
Keep a subject where the sentence wants one and drop it where Chinese would —
neither is the rule here. 請 belongs to a character who is speaking, never to the
narrator and never as an instruction to the reader; and avoid 被 where 讓, 由, or
an active sentence is what Chinese uses.
Avoid the marks of translated English: stacked 的, 一個 standing in for an
article, 當……的時候, 對於……來說, and 進行/作出 in place of a plain verb.
Keep an image an image. Where an English metaphor has no Chinese collocation,
write the image a Chinese writer would use rather than the words the English
used, and never flatten it into an explanation.
Narration holds one voice throughout, and a character keeps their own diction
and level of formality wherever they speak.""",
    ),
    ("ja", DEFAULT_TONE): (
        """\
Target is Japanese technical documentation register (である/だ体 for reference
material, ですます体 for guides — follow whichever the surrounding segments use).""",
        None,
    ),
    ("ja", "literary"): (
        """\
Target is Japanese narrative prose: だ・である体 for narration, held in one
register throughout, while dialogue in 「」 takes the speaker's own.""",
        """\
Build each sentence in Japanese order rather than following the English clause
by clause. Japanese carries the subject in the context rather than in every
sentence: drop 彼, 彼女 and 私 wherever the reader can still follow, and name the
person when the referent has to be marked — a pronoun in every sentence is the
plainest mark of 翻訳調. A quotation inside speech takes 『』; an em dash is ——
and an ellipsis ……, two characters each. Do not add explanation the source does
not have, and do not flatten an image into a statement.""",
    ),
}

#: Appended only when at least one item actually carries a neighbour, so a
#: project that set `batch.context` to 0 does not pay for an instruction about
#: fields that will never appear.
#:
#: It goes *before* the language brief and not after it. D4's finding was that
#: the last thing the model reads overrides the knobs above it, which is how an
#: unconditional documentation-register sentence beat `Tone:` for months; the
#: brief keeps that position and this takes the one below `_BASE_RULES`.
_CONTEXT_RULES = """\
Some items carry `before_id`, `before_text`, `after_id` or `after_text`: the
segments that surround this one in the document, in document order, so that a
pronoun, a speaker, a tense, or a level of formality has something to resolve
against. They are context and nothing else.

`before_id` and `after_id` name another item of this same request — read its
`text` there. `before_text` and `after_text` carry a neighbour's source inline,
because that neighbour is not in this request.

Never translate a neighbour, never fold one into the segment you are
translating, and never reply under an id that is not the `id` of an item."""

_POLISH_RULES = """\
You are revising an existing translation into {target_lang} for fluency.

You receive each segment's source and its current draft. Rewrite the draft so it
reads as if originally written in {target_lang}: remove translationese, fix
awkward word order, cut redundant pronouns and particles.

Do not change meaning, do not add or remove information, and do not touch the
⟦n⟧ placeholders — the same set must appear in your version.

If a draft is already good, return it unchanged.

Reply with a single JSON object mapping segment id to the revised text, and
nothing else."""


#: What introduces the project's own preamble. Named, because the model has to
#: be able to tell "this project's narrator sounds like X" from the register
#: brief above it: one is a fact about Traditional Chinese prose, the other is a
#: fact about this book, and a paragraph that arrives unlabelled reads as more
#: of the former.
_STYLE_HEAD = ("This project's style sheet. It refines the register above for "
               "this particular book; where it is silent, the register stands.")

#: And what introduces the per-character notes in a batch's own message. It says
#: *why* these characters and not others — the batch mentions them — so their
#: absence from the next request is not read as a change of instruction.
_STYLE_BATCH_HEAD = ("Voice notes from the style sheet, for the characters this "
                     "batch mentions:")


def brief(target_lang, tone):
    """The language brief for a register, or ``None`` for an unbriefed language.

    An unrecognized register takes the default one's brief rather than none: the
    knob is free text by design, and a language losing its terminology because
    somebody typed a register nobody has written yet is a far worse trade than
    a novel briefed as documentation, which is what happened before D4 anyway.

    Public, and called by `cli.cmd_todo` as well as from here. `AGENTS.md`
    treats an API model, an agent in its own context and a human as three equal
    sources of a translation, so a register that reaches only the first of them
    reaches one third of the pipeline — which is what HANDOFF-013 left behind
    and `docs/decisions.md`, 2026-07-29, records as not-taken.
    """
    register = canonical_tone(tone)
    parts = (_LANG_BRIEFS.get((target_lang, register))
             or _LANG_BRIEFS.get((target_lang, DEFAULT_TONE)))
    if parts is None:
        return None
    head, rules = parts
    return "\n".join(p for p in (head, _LANG_TERMS.get(target_lang), rules) if p)


def style_preamble_text(preamble):
    """The always-on half of the style sheet as it reaches a prompt, or ``""``."""
    return f"{_STYLE_HEAD}\n\n{preamble}" if preamble else ""


def _system_prompt(source_lang, target_lang, tone, mode, context=False, style=""):
    """The instructions that hold for the whole document, assembled once.

    ``style`` is the style sheet's **preamble only**. Per-character blocks are
    not here: they are selected per batch, and per-batch content lives in the
    user message beside the required terminology, which is where this project
    has always put it. Two consequences were what decided it — this string stays
    identical across every request of a run, so a local runtime's prefix cache
    survives the whole book, and `retry_one` needs no second assembly. The
    losing alternative put the matched blocks here and rebuilt the prompt per
    batch; `docs/decisions.md`, 2026-08-02.
    """
    if mode == "polish":
        base = _POLISH_RULES.format(target_lang=target_lang)
    else:
        base = _BASE_RULES.format(source_lang=source_lang, target_lang=target_lang, tone=tone)
    # Order is the decision, not the assembly: `_CONTEXT_RULES` above the brief
    # for D4's reason, the brief above the style sheet so a project refines its
    # register rather than replacing it, and the style sheet last because the
    # last thing read is what wins a contradiction.
    parts = [base]
    if context:
        parts.append(_CONTEXT_RULES)
    parts.append(brief(target_lang, tone))
    parts.append(style_preamble_text(style))
    return "\n\n".join(p for p in parts if p)


# ── response parsing ───────────────────────────────────────────────────────

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)


def parse_reply(text):
    """Extract the mapping from a model reply that may be wrapped or chatty."""
    cleaned = _FENCE.sub("", text).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in reply: {cleaned[:200]!r}") from None
        data = json.loads(cleaned[start : end + 1])
    if isinstance(data, list):
        data = {d["id"]: d.get("text", d.get("target", "")) for d in data}
    if not isinstance(data, dict):
        raise ValueError("reply is not an object")
    return {str(k): v for k, v in data.items() if isinstance(v, str)}


# ── batch payloads ─────────────────────────────────────────────────────────

#: The letters a name can be made of, for deciding where one ends. ASCII plus
#: Latin-1 Supplement through Latin Extended-B, the same range `cli._LETTER`
#: documents and for the same reason: a novel in English is full of names that
#: are not ASCII, and with a bare `[A-Za-z]` boundary `José` matches inside
#: `Josée` — `é` is not in the class, so the lookahead lets it through.
_NAME_LETTER = "A-Za-zÀ-ÖØ-öø-ɏ"


def mentions(low, term):
    """Does ``low`` — already lower-cased — contain ``term`` as a whole word?

    One implementation, three callers: the glossary hints below, the style
    sheet's block selection, and `cli.cmd_todo`, which had a fourth copy of this
    regex inline. Three copies of a matching rule is how the glossary and the
    style sheet come to disagree about whether `Ashcombe's` mentions Ashcombe,
    which is the kind of divergence nobody finds by reading.
    """
    return re.search(
        rf"(?<![{_NAME_LETTER}]){re.escape(term.lower())}(?![{_NAME_LETTER}])",
        low) is not None


def _glossary_hints(text, glossary):
    low = text.lower()
    hints = []
    for row in glossary:
        # A row with no target is a candidate `lx terms` proposed and nobody has
        # decided on yet. `Ashcombe -> ` in the required-terminology block tells
        # the model to render the name as nothing, so an unfinished row must be
        # silent rather than harmful — which is what `checks.check_segment`
        # already does with one, so the two halves of the glossary path agree.
        if not row["target"]:
            continue
        if mentions(low, row["source"]):
            hints.append(f"{row['source']} -> {row['target']}")
    return hints


def style_notes(segments, blocks):
    """The style-sheet blocks this set of segments actually mentions, in file order.

    **Matched against the batch, not against each segment.** A batch is
    twenty-five consecutive paragraphs — a scene — and a character active in a
    scene is named somewhere in it even though most individual paragraphs of
    their dialogue do not name them. Per-segment matching was the alternative
    and it loses exactly the dialogue this feature exists for.

    What it deliberately does not attempt is *who is speaking*. That is
    judgement, HANDOFF-015 puts it out of scope, and it is not what the
    selection needs: "this text contains this name" is mechanically decidable,
    which is what lets the notes be selected in code at all rather than being
    sent in full to every request.

    The honest residue: a scene whose speaker is named only in the paragraph
    before the batch begins gets no note. The preamble is what covers a rule
    that must never be missed, and the limits are sized on the assumption that
    anything load-bearing lives there.
    """
    if not blocks:
        return []
    # The masked form, so a name inside a protected span is not matched — the
    # same text the model is about to be shown, which is the only text a
    # selection rule may honestly claim to be about.
    low = "\n".join(seg["masked"] for seg in segments).lower()
    return [b for b in blocks if any(mentions(low, n) for n in b["names"])]


def _style_block_text(blocks):
    """Matched blocks as they reach a request, or ``""`` when none matched."""
    if not blocks:
        return ""
    bodies = [f"{', '.join(b['names'])}:\n{b['notes']}" for b in blocks]
    return _STYLE_BATCH_HEAD + "\n\n" + "\n\n".join(bodies) + "\n\n"


def _neighbour_context(doc, segments, window):
    """``({id: (before_ids, after_ids)}, {id: source})`` in document order.

    Adjacency comes from **the document**, never from the ``segments`` argument.
    `lx repair` passes only the failing segments and `lx translate --ids` passes
    whatever the user named, so reading the caller's list as document order
    would tell the model that segment 5 and segment 40 are consecutive prose. A
    confident lie about flow is worse than no context at all, which is the whole
    reason this consults ``doc["segments"]`` — the authority `tone` and `eol`
    already have for facts about the document rather than about one request.

    The source text is the *masked* form, so invariant 3 holds for a neighbour
    exactly as it does for the segment being translated.

    A window of ``0`` needs no early return and does not get one: it slices
    ``ids[i:i]`` on both sides, so every entry comes out empty, no item gains a
    field and `_system_prompt` is not told to explain one. The guard that used
    to sit here survived the mutation sweep — nothing could observe it — and an
    inert branch a later reader would take for load-bearing is worse than the
    dictionary it saved building.
    """
    order = [s for s in (doc.get("segments") or segments) if s.get("id")]
    ids = [s["id"] for s in order]
    at = {sid: i for i, sid in enumerate(ids)}
    source = {s["id"]: s.get("masked") or "" for s in order}
    adjacency = {}
    for seg in segments:
        i = at.get(seg.get("id"))
        if i is None:
            # Not a segment of this document. Unreachable from the CLI and the
            # workbench, which both derive their list from the document they
            # pass — but a stale list should cost the context, not the run.
            continue
        # `max(0, ...)` and not a bare `i - window`: at i=1 with a window of 2
        # that slices `ids[-1:1]`, which is empty, so the second segment of a
        # document would silently lose the first.
        adjacency[seg["id"]] = (ids[max(0, i - window):i], ids[i + 1 : i + 1 + window])
    return adjacency, source


def _attach(item, side, nids, present, source):
    """Give one side of ``item`` its neighbours: a reference, an inline source, or both.

    Referencing is what keeps the cost bounded: inside a batch the neighbours of
    an interior segment are other items of the same request, so sending their
    text again would treble the payload for nothing. Only the two edges of a
    batch — and, on the retry path, both sides — have nothing to point at.

    **Two scalar fields, not one list of objects, and the reason is measured.**
    `_user_message` serializes with ``indent=1``, so ``[{"id": "s0004"}]`` costs
    about 48 characters where the id inside it costs 8 — the container, not the
    reference. On a document of short blocks the nested form made the request
    1.95x its no-context size against these fields' 1.50x, and on prose, which
    is what this feature is for, 1.25x against 1.16x. `docs/decisions.md`,
    2026-08-02.

    A widened window joins rather than lists, for that same reason: its
    neighbours on one side are consecutive segments of one document, so a blank
    line between them is what the document itself says there.

    An inlined neighbour has nowhere to carry an id, which is deliberate rather
    than incidental. An id the model can see is an id it can answer under, and
    an answer for a segment nobody asked about is exactly what must not come
    back. `run_batch` and `retry_one` both read only the ids they requested, so
    such an answer is already discarded; this removes the temptation one layer
    earlier rather than relying on that alone.
    """
    refs = [n for n in nids if n in present]
    texts = [source[n] for n in nids if n not in present and source.get(n)]
    if refs:
        item[f"{side}_id"] = " ".join(refs)
    if texts:
        item[f"{side}_text"] = "\n\n".join(texts)


def _user_message(segments, glossary, mode, context=None, style_blocks=None):
    terms = []
    for seg in segments:
        terms.extend(_glossary_hints(seg["masked"], glossary))
    adjacency, source = context or ({}, {})
    present = {seg["id"] for seg in segments}
    payload = []
    for seg in segments:
        before, after = adjacency.get(seg["id"], ((), ()))
        # Key order is reading order — what precedes the segment, the segment,
        # what follows it. The payload reaches the model as text, and flow is
        # the one thing these fields exist to carry.
        item = {"id": seg["id"], "kind": seg["kind"]}
        _attach(item, "before", before, present, source)
        item["text"] = seg["masked"]
        _attach(item, "after", after, present, source)
        if mode == "polish":
            item["draft"] = seg.get("target") or ""
        if seg.get("issues"):
            item["problems"] = seg["issues"]
        payload.append(item)
    head = ""
    if terms:
        uniq = sorted(set(terms))
        head = "Required terminology for this batch:\n" + "\n".join(f"- {t}" for t in uniq) + "\n\n"
    # Voice above terminology, and terminology closest to the payload. The
    # terminology block is the half `checks.py` enforces afterwards, so it is
    # the half that must not be pushed away from the text it governs; voice is
    # the broader instruction and takes the outer position.
    return _style_block_text(style_notes(segments, style_blocks or [])) + head + json.dumps(
        payload, ensure_ascii=False, indent=1)


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ── driver ─────────────────────────────────────────────────────────────────

def accept(seg, text, lang, cfg, slots=None):
    """Take a proposed target only if its placeholders survived. ``(text, why)``.

    Model output and a translation-memory hit both arrive here, because they fail
    the same way. A memory entry is keyed on source text, not on the mask
    configuration that produced its placeholders — deliberately, so that the same
    wording banked on two machines with different do-not-translate lists is still
    one entry — and the consequence is that editing `config/dnt.txt` changes how
    many ``⟦n⟧`` a segment has while leaving its key untouched. Written straight
    to target, as reuse used to be, that renders a bare ``⟦2⟧``: measured
    2026-07-27, and caught only by a downstream check, which is safety by
    inspection where invariant 2 asks for safety by construction.

    `lx apply` deliberately does not come through here. It carries a person's or
    an agent's own words, and refusing those at the door with no way to override
    is worse than reporting them at `lx check`, where a reviewer is already
    looking. See ``docs/decisions.md``, 2026-07-29. It does share
    :func:`normalize.reseat_outer_blanks`, because that one is not a refusal:
    whichever of the three sources produced a target, the blanks a segment opens
    and closes with are the host syntax's and not the translator's.

    The strip is still here and still unconditional — models pad their answers,
    and every reuse path comes through this function — but what it takes off the
    ends is put back from the source rather than dropped.
    """
    text = repair_placeholders(text).strip()
    # **What a placeholder means, not what it is numbered.** `slots` is the map
    # this wording's ids referred to when it was written; when it differs from
    # the segment's own, the wording is moved into the segment's numbering before
    # anything is compared. Without it the test below is satisfied by a wholesale
    # renumbering: measured 2026-08-17, protecting `Wendy` instead of `Brian`
    # left `⟦1⟧ 在門口迎接了 Wendy。` accepted, `lx check` green, and the rendered
    # sentence naming the wrong person twice.
    #
    # Repaired rather than refused, because `mask.reseat` is deterministic and
    # invariant 5 says a correctable defect is corrected rather than reported. It
    # refuses only what it cannot place, and a refusal here reaches `lx check` as
    # a placeholder mismatch, which is the error that was missing.
    if slots is not None and slots != seg.get("slots"):
        text, why = reseat(text, slots, seg.get("slots") or {})
        if text is None:
            return None, why
    want, got = sorted(placeholder_ids(seg["masked"])), sorted(placeholder_ids(text))
    if want != got:
        return None, f"placeholder mismatch (expected {want}, got {got})"
    if not text:
        return None, "empty translation"
    # Reseated **after** normalization, not before, and the order is measured
    # rather than stylistic: `collapse_space` ends in `[ \t]+\Z`, which is in
    # zh-TW's default op list, so a trailing run handed to `normalize` is deleted
    # again and the fix is inert for the project's primary language. Running
    # `normalize` on the stripped sentence is also exactly what it received
    # before this change, so nothing about the ops moved underneath it.
    return reseat_outer_blanks(seg["masked"], normalize(text, lang, cfg)), None


class Progress:
    """Minimal thread-safe reporter; the web UI swaps in its own sink."""

    def __init__(self, sink=None):
        self.sink = sink or (lambda msg: None)
        self._lock = threading.Lock()

    def __call__(self, msg):
        with self._lock:
            self.sink(msg)


def translate_segments(segments, doc, cfg, provider_name=None, mode="draft",
                       batch_size=None, concurrency=None, progress=None, on_batch=None,
                       model=None):
    """Translate ``segments`` in place-safe fashion; returns (results, failures).

    ``results`` maps segment id to text. ``failures`` is a list of
    ``(segment_id, reason)`` for segments the model could not produce a usable
    answer for after an individual retry.

    ``on_batch`` is called with each batch's accepted results as they land, and
    it is what makes a long run survivable. A 100k-word novel is on the order of
    2,000 segments and, at the default batch size, some eighty requests — tens of
    minutes to hours of model time — and until this existed a Ctrl-C or one
    dropped connection at 90% discarded every translated segment, because nothing
    was written until the whole list came back. It runs under the same lock as
    ``results``, so the writes are serialized and a batch is durable before the
    next one is reported.
    """
    progress = progress or Progress()
    lang = doc["lang"]
    source_lang = cfg.get("source_lang", "en")
    # The document, and not the config, is the authority for its own register.
    # `do_extract` froze it there, and reading `cfg["tone"]` again here would
    # resolve one fact a second time, at a later moment, from a source the
    # memory key does not consult: `store.tm_records` reads `doc["tone"]` only.
    # Measured on a state file with no `tone` and a config saying `literary` —
    # the model was briefed literary and the wording was banked in the default
    # register's tier, which is the silent cross-register overwrite this axis
    # exists to prevent, arriving through a divergent fallback.
    tone = doc.get("tone") or DEFAULT_TONE

    # Which backend and which model, decided in one place. `provider_name` and
    # `model` are this run's overrides; the routing entry and then the provider
    # spec answer for whatever they leave open. Resolving it here rather than
    # reading `cfg["routing"]` directly is what keeps `lx routing show`, the
    # dry-run line and `/api/state` describing the run that will actually happen.
    name, model_id = resolve_route(cfg, mode, provider_name, model)
    provider = build_provider(name, cfg, model_id)

    batch_cfg = cfg.get("batch", {})
    size = batch_size or batch_cfg.get("size", 25)
    workers = max(1, concurrency or batch_cfg.get("concurrency", 2))

    glossary = load_glossary(cfg)
    # Loaded once per run and refused loudly if it is malformed, because a style
    # sheet that half-applies is a whole book translated under voice
    # instructions the person believes are in force. `StyleSheetError` reaches
    # `cli.main` for exit 2.
    style_preamble, style_blocks = load_style(cfg)
    # Built once, from the document, and closed over by both request paths — so
    # `retry_one` gets its neighbours for free rather than needing the segment
    # list threaded into it. A payload of one has nothing to point at, so both
    # sides arrive inlined, which is precisely what the retry path needs: it is
    # where a hard sentence ends up and where the context was worst.
    context = _neighbour_context(doc, segments, batch_cfg.get("context", 1))
    briefed = any(b or a for b, a in context[0].values())
    system = _system_prompt(source_lang, lang, tone, mode, briefed, style_preamble)

    results, failures = {}, []
    lock = threading.Lock()
    batches = list(_chunks(segments, size))
    progress(f"{provider.describe()} · {len(segments)} segment(s) in {len(batches)} batch(es)")

    def retry_one(seg):
        note = ""
        if seg["masked"].count("\u27e6"):
            note = ("\nThis segment previously came back with the wrong placeholders. "
                    "Copy every ⟦n⟧ exactly as it appears.")
        try:
            # A payload of one selects its own notes: the retried segment's
            # names, not the batch's. Narrower than the batch that failed, and
            # free — the selection is a function of the segments passed in.
            reply = provider.complete(
                system,
                _user_message([seg], glossary, mode, context, style_blocks) + note)
            got = parse_reply(reply).get(seg["id"], "")
        except Exception as e:  # noqa: BLE001 - surfaced to the caller
            return None, str(e)
        return accept(seg, got, lang, cfg)

    def run_batch(idx, batch):
        try:
            reply = provider.complete(
                system, _user_message(batch, glossary, mode, context, style_blocks))
            mapping = parse_reply(reply)
        except Exception as e:  # noqa: BLE001
            progress(f"batch {idx + 1}/{len(batches)} failed ({e}); retrying segment by segment")
            mapping = {}

        local_ok, local_bad = {}, []
        for seg in batch:
            text = mapping.get(seg["id"])
            good, why = ((None, "no answer for this id") if text is None
                         else accept(seg, text, lang, cfg))
            if good is None:
                good, why = retry_one(seg)
            if good is None:
                local_bad.append((seg["id"], why))
            else:
                local_ok[seg["id"]] = good
        with lock:
            results.update(local_ok)
            failures.extend(local_bad)
            if on_batch and local_ok:
                on_batch(dict(local_ok))
        progress(f"batch {idx + 1}/{len(batches)} · {len(local_ok)} ok"
                 + (f" · {len(local_bad)} unresolved" if local_bad else ""))

    if workers == 1:
        for i, batch in enumerate(batches):
            run_batch(i, batch)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda p: run_batch(*p), enumerate(batches)))

    return results, failures


def failing_segments(doc, cfg, include_held=False):
    """Segments a fresh check would reject — the repair pass works on these.

    ``include_held`` is for **reporting only** and nothing that selects work may
    pass it. `cmd_repair` uses it to name the failing segments it declined to
    select: `lx check` walks every segment and so counts a held segment's errors
    in its exit code, while this function excludes them, so the two commands
    answered "is anything failing?" differently and one of them said "nothing
    failing" while the other exited 1. That is the class of divergence
    `cli.do_select` was unified to remove on the same day, arriving by another
    route.

    Held segments are excluded through the one shared helper, and this is the
    predicate that makes the helper worth having: this function is *status-blind*
    by design — it asks the validators, not the queue — so without the exclusion
    a held segment would come back to the model on every repair round of every
    run, which is the opposite of what holding it asked for. Its own `held` rule
    is at warn severity and so never selects it here, but the errors it may carry
    alongside would.
    """
    glossary, dnt = load_glossary(cfg), load_dnt(cfg)
    out = []
    for seg in (doc["segments"] if include_held else workable(doc["segments"])):
        issues = check_segment(seg, doc["lang"], cfg, glossary, dnt)
        errors = [i for i in issues if i["severity"] == "error"]
        if errors:
            seg = dict(seg)
            seg["issues"] = [f"{i['rule']}: {i['message']}" for i in errors]
            out.append(seg)
    return out
