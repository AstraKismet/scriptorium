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

from .checks import check_segment
from .config import load_dnt, load_glossary
from .mask import placeholder_ids, repair_placeholders
from .normalize import normalize
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

_LANG_BRIEFS = {
    # The second paragraph carries the terms the 2026-07-28 audit removed from
    # `checks.py::ZH_TW_LEXICON`: each is correct Taiwanese usage in one sense, so
    # choosing between them needs the sentence, which invariant 4 keeps out of a
    # validator and puts here instead. Do not compress it back into "X not Y" —
    # that phrasing is what taught the model the wrong half.
    "zh-TW": """\
Target is Traditional Chinese as used in Taiwanese technical documentation.
Converting characters is not enough — use the vocabulary that documentation
uses: 軟體 not 軟件, 網路 not 網絡, 執行緒 not 線程, 快取 not 緩存,
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
Use full-width ，。！？；： and 「」 inside Chinese text. Write technical
documentation register: neutral-formal, subject usually dropped, 請 for
instructions, active voice rather than 被. Nominalize headings.""",
    "ja": """\
Target is Japanese technical documentation register (である/だ体 for reference
material, ですます体 for guides — follow whichever the surrounding segments use).
Use 全角 punctuation 、。 and 「」.""",
}

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


def _system_prompt(source_lang, target_lang, tone, mode):
    if mode == "polish":
        base = _POLISH_RULES.format(target_lang=target_lang)
    else:
        base = _BASE_RULES.format(source_lang=source_lang, target_lang=target_lang, tone=tone)
    brief = _LANG_BRIEFS.get(target_lang)
    return f"{base}\n\n{brief}" if brief else base


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

def _glossary_hints(text, glossary):
    low = text.lower()
    hints = []
    for row in glossary:
        pat = rf"(?<![A-Za-z]){re.escape(row['source'].lower())}(?![A-Za-z])"
        if re.search(pat, low):
            hints.append(f"{row['source']} -> {row['target']}")
    return hints


def _user_message(segments, glossary, mode):
    terms = []
    for seg in segments:
        terms.extend(_glossary_hints(seg["masked"], glossary))
    payload = []
    for seg in segments:
        item = {"id": seg["id"], "kind": seg["kind"], "text": seg["masked"]}
        if mode == "polish":
            item["draft"] = seg.get("target") or ""
        if seg.get("issues"):
            item["problems"] = seg["issues"]
        payload.append(item)
    head = ""
    if terms:
        uniq = sorted(set(terms))
        head = "Required terminology for this batch:\n" + "\n".join(f"- {t}" for t in uniq) + "\n\n"
    return head + json.dumps(payload, ensure_ascii=False, indent=1)


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ── driver ─────────────────────────────────────────────────────────────────

def accept(seg, text, lang, cfg):
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
    looking. See ``docs/decisions.md``, 2026-07-29.
    """
    text = repair_placeholders(text).strip()
    want, got = sorted(placeholder_ids(seg["masked"])), sorted(placeholder_ids(text))
    if want != got:
        return None, f"placeholder mismatch (expected {want}, got {got})"
    if not text:
        return None, "empty translation"
    return normalize(text, lang, cfg), None


class Progress:
    """Minimal thread-safe reporter; the web UI swaps in its own sink."""

    def __init__(self, sink=None):
        self.sink = sink or (lambda msg: None)
        self._lock = threading.Lock()

    def __call__(self, msg):
        with self._lock:
            self.sink(msg)


def translate_segments(segments, doc, cfg, provider_name=None, mode="draft",
                       batch_size=None, concurrency=None, progress=None):
    """Translate ``segments`` in place-safe fashion; returns (results, failures).

    ``results`` maps segment id to text. ``failures`` is a list of
    ``(segment_id, reason)`` for segments the model could not produce a usable
    answer for after an individual retry.
    """
    progress = progress or Progress()
    lang = doc["lang"]
    source_lang = cfg.get("source_lang", "en")
    tone = doc.get("tone") or cfg.get("tone", "technical")

    routing = cfg.get("routing", {})
    name = provider_name or routing.get(mode) or routing.get("draft") or "local"
    provider = build_provider(name, cfg)

    batch_cfg = cfg.get("batch", {})
    size = batch_size or batch_cfg.get("size", 25)
    workers = max(1, concurrency or batch_cfg.get("concurrency", 2))

    glossary = load_glossary(cfg)
    system = _system_prompt(source_lang, lang, tone, mode)

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
            reply = provider.complete(system, _user_message([seg], glossary, mode) + note)
            got = parse_reply(reply).get(seg["id"], "")
        except Exception as e:  # noqa: BLE001 - surfaced to the caller
            return None, str(e)
        return accept(seg, got, lang, cfg)

    def run_batch(idx, batch):
        try:
            reply = provider.complete(system, _user_message(batch, glossary, mode))
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
        progress(f"batch {idx + 1}/{len(batches)} · {len(local_ok)} ok"
                 + (f" · {len(local_bad)} unresolved" if local_bad else ""))

    if workers == 1:
        for i, batch in enumerate(batches):
            run_batch(i, batch)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda p: run_batch(*p), enumerate(batches)))

    return results, failures


def failing_segments(doc, cfg):
    """Segments a fresh check would reject — the repair pass works on these."""
    glossary, dnt = load_glossary(cfg), load_dnt(cfg)
    out = []
    for seg in doc["segments"]:
        issues = check_segment(seg, doc["lang"], cfg, glossary, dnt)
        errors = [i for i in issues if i["severity"] == "error"]
        if errors:
            seg = dict(seg)
            seg["issues"] = [f"{i['rule']}: {i['message']}" for i in errors]
            out.append(seg)
    return out
