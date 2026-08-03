"""Validators. Every rule here is mechanically decidable — that is the entry test.

Rules append to ``issues`` with a severity; ``error`` fails the build.

Structural fidelity splits in two, and only one half is *checkable*. The skeleton
(invariant 2a) is guaranteed by :mod:`.mdparse` — every byte the pipeline did not
change is reproduced, so there is nothing to validate and no rule here reads it.
What a target value does once substituted into that skeleton (invariant 2b) is
not under our control, and is checked in full: placeholder pairs, block-start
containment, host escaping, and the carriage return a segment may not invent.
"""

import re
from collections import Counter

from .mask import CJK, CJK_RE, PH_RE, strip_placeholders, unmask
from .mdparse import (
    FENCE_RE,
    HEADING_RE,
    HR_RE,
    LIST_RE,
    QUOTE_RE,
    SETEXT_RE,
    opens_a_link_definition,
)

# Per-locale term preferences: each key is a form that zh-TW technical writing
# spells differently, mapped to the form it uses. This is a consistency rule, not
# a correctness one — the flagged form is correct in the conventions it comes
# from, and the only claim made here is that one document should not mix the two.
#
# Entry test, from invariant 4: a row lives here only if deciding it needs no
# judgement. Two questions, both answered before a row is added or restored:
#
#   1. Does the string carry a standard zh-TW sense of its own? If it does, the
#      row is judgement — 物體的質量 is mass, 法律程序 is a legal procedure — and
#      it belongs in `translate.py::_LANG_BRIEFS` and `skill/reference/zh-TW.md`
#      instead. The 2026-07-28 audit moved eighteen rows out on this test; they
#      are listed below so the next reader does not restore one.
#   2. Can it fall out of an ordinary zh-TW phrase across a word boundary?
#      Chinese is unspaced and the match is a plain substring, so 電視頻道
#      contains 視頻 and 參數組合 contains 數組. Such a row is `warn`, never
#      `error`: a reviewer spends three seconds, the build does not stop.
#
# A row saved by tightening the match carries one condition, in
# `_LEXICON_UNLESS_FOLLOWED_BY`, and only where the colliding continuation is a
# closed set. Two conditions means the row is judgement and leaves; a ladder of
# them is how a validator becomes untrustworthy in the other direction.
#
# A project that wants a removed row back — or any term at error severity in its
# own domain — adds it under `lexicon_extra` in its config, where the judgement
# is that project's to make. severity: error | warn
ZH_TW_LEXICON = {
    # No zh-TW sense, no ordinary phrase collides: safe to fail the build.
    "軟件": ("軟體", "error"), "硬件": ("硬體", "error"), "插件": ("外掛/擴充套件", "error"),
    "屏幕": ("螢幕", "error"), "網絡": ("網路", "error"), "服務器": ("伺服器", "error"),
    "硬盤": ("硬碟", "error"), "打印": ("列印", "error"), "缺省": ("預設", "error"),
    "賬號": ("帳號", "error"), "線程": ("執行緒", "error"), "緩存": ("快取", "error"),
    "標簽": ("標籤", "error"), "端口": ("連接埠", "error"), "短信": ("簡訊", "error"),
    "信息": ("資訊", "error"),
    # Same, but each is guarded below against one closed set of continuations.
    "視頻": ("影片", "error"), "鼠標": ("滑鼠", "error"), "兼容": ("相容", "error"),
    # Demoted 2026-07-28: the form itself is never zh-TW, but the substring falls
    # out of ordinary prose, so it may report and must not fail a build.
    "內存": ("記憶體", "warn"),      # 體內存在、國內存款
    "激活": ("啟用", "warn"),        # 刺激活化、感激活動主辦單位
    "集成": ("整合", "warn"),        # 收集成果、募集成功 — 集 + 成X is productive
    "調試": ("除錯", "warn"),        # 強調試用期、協調試驗
    "數組": ("陣列", "warn"),        # 參數組合、多數組織 — and in this very domain
    "變量": ("變數", "warn"),        # 改變量、不變量 (both standard zh-TW)
    "帶寬": ("頻寬", "warn"),        # 皮帶寬度、地帶寬廣
    "復用": ("重複使用", "warn"),     # 恢復用電、修復用具
}

# The one tightening mechanism, deliberately tiny. Value is a character class:
# the row does not fire on an occurrence followed by one of these, because that
# occurrence is inside a longer, correct word. Keep each set closed and keep this
# dict short — if a row needs an open-ended list, it is judgement and it leaves.
_LEXICON_UNLESS_FOLLOWED_BY = {
    "視頻": "道率寬譜段繁仍",   # 電視頻道、監視頻率 — 頻X is a closed set of words
    "鼠標": "本",              # 老鼠標本
    "兼容": "並",              # 兼容並蓄
}

# Removed 2026-07-28 as judgement rather than rule, with the zh-TW sense that
# makes each undecidable by substring. Guidance for them lives in the language
# brief and in `skill/reference/zh-TW.md`; do not restore them here.
#
#   程序   法律程序、議事程序        質量   物體的質量 (mass)
#   數據   統計數據、實驗數據        支持   支持這項提案 (endorsement)
#   文本   文本分析 (literary text)  對象   研究對象、交往對象
#   函數   三角函數 (mathematics)    指針   時鐘的指針、羅盤指針
#   進程   歷史進程、和平進程        登錄   戶籍登錄、登錄有案
#   交互   交互作用、交互驗證        隊列   隊列訓練 (formation)
#   菜單   餐廳的菜單                默認   默認、默許 (to acquiesce)
#   音頻   音頻放大器 (frequency)    智能   智能障礙、智能不足
#   視圖   正視圖、俯視圖、透視圖    用戶   用戶端、電信用戶


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


# ── containment and host escaping (invariant 2b) ────────────────────────────
#
# Invariant 2a guarantees the bytes *around* a segment and claims nothing about
# what happens once a target is substituted between them. Five cases measured
# 2026-07-27, every one reporting zero errors and zero warnings: a cell
# translation containing `|` grew a third column; a paragraph beginning `1. `
# became an ordered list; a paragraph carrying a line-initial `#` invented a
# heading; a heading carrying a blank line split into two blocks; a blockquote
# carrying a newline dropped its second half out of the quote.

#: Read in :mod:`.mdparse`'s own order, with its own rules. Whether a line opens
#: a block is the parser's question; a second copy of these regexes here would be
#: a second answer to it, and the copy is the one nobody re-reads when a flavour
#: detail changes. A format that is not Markdown brings its own kinds and its own
#: table — the shape of the rule does not change with it.
#:
#: Each entry is a *callable*, not a pattern, because one of the seven rules
#: outgrew a pattern (see below) and a table where six entries are regexes and
#: one is a function is a table whose reader has to check which.
_MARKDOWN_BLOCK_STARTS = (
    ("code fence", FENCE_RE.match),
    ("thematic break", HR_RE.match),
    ("setext heading", SETEXT_RE.match),
    ("heading", HEADING_RE.match),
    # The worst of the family, and the one an adversarial pass found after the
    # other six were in: a target of `[foo]: http://example.com` does not merely
    # land in the wrong block, it renders to *nothing at all*, and the segment
    # disappears from the stream on the next parse.
    #
    # `opens_a_link_definition` and not `DEF_RE`, since HANDOFF-022: the pattern
    # answers on the strength of `[label]:` and the rule is the whole line, so
    # the pattern reported a target of `[安娜]: 你好 世界` — an ordinary
    # paragraph, a space in it where a destination may not have one — as a block
    # the source did not open, at error severity. Narrowing *sharpens* this check
    # rather than weakening it, because the question it asks is precisely "does
    # this line disappear from the render", and only a well-formed definition
    # does. Measured over the corpus's 2251 segment lines: the rule names **2**
    # where the pattern names 10, and the eight it drops are every one of
    # `link-definition-tails.md`'s near misses. What the narrowing gives up is a
    # definition the model spreads over two lines, which this rule cannot see
    # because `_block_start` reads one line at a time — the same blind spot the
    # parser has, deliberately, and the same place it would have to be fixed.
    #
    # It used to say here that no segment line can ever be one, "by construction —
    # `mdparse` folds a source link definition into a raw node", measured at 0 of
    # 2154 corpus segment lines. Re-derived 2026-08-03 and both halves are wrong.
    # The construction never held: this rule is not one of the paragraph loop's
    # stop conditions, so `para\n[x]: /url` already put a definition line inside a
    # paragraph segment. And the number has moved — 2 of 2251, `　[not-a-ref]:
    # /url` in `block-marker-whitespace.md`, which `_block_start` lstrips into a
    # match though the parser reads it as an ordinary paragraph, and `[lazy]:
    # /url` in `link-definition-tails.md`, which is well formed and is prose only
    # because a definition may not interrupt the paragraph above it — a rule
    # `_block_start` has no line above to apply.
    #
    # What keeps it free of false positives is not construction but symmetry:
    # both sides of every comparison in `containment_problems` come through
    # `_block_start`, so a source line that answers "link reference definition"
    # licenses a target line that answers the same, and only a target that invents
    # one where the source had none is reported.
    ("link reference definition", opens_a_link_definition),
    ("blockquote", QUOTE_RE.match),
    ("list", LIST_RE.match),
)


#: What a host lets a segment do, keyed by ``seg["host"]``. Three questions, and
#: every format answers all three:
#:
#: ``block_starts``   which line shapes open a block, in the parser's own order.
#: ``inline_kinds``   kinds whose content is an *inline* context, where a
#:                    line-initial marker is literal text. `## # x` is a heading
#:                    whose text begins with a hash and `| - x |` is a cell
#:                    containing a dash; applying the block rule to either would
#:                    fail correct work, which is the direction the zh-TW lexicon
#:                    had to be audited for on 2026-07-28.
#: ``no_added_lines`` kinds whose target may not have more lines than its source,
#:                    with the message to say so.
#:
#: Plain text answers differently on all three, and the difference is the reason
#: this is a table rather than three module constants. No line of plain text opens
#: anything — a line of dialogue beginning ``- `` is dialogue, not a list item —
#: so its block table is empty and that whole rule goes quiet.
#:
#: It gets **two** rows, because the containment question genuinely has two
#: answers for one format. Where paragraphs are separated by blank lines, a
#: paragraph is a run of wrapped lines and a translation is free to re-wrap it:
#: comparing line counts there is precisely the rule ``docs/decisions.md``
#: rejected on 2026-07-28 for failing correct work, and invariant 4 excludes it.
#: Where every line *is* a paragraph, an added line is not a re-wrap — it is a
#: second paragraph — and the same comparison becomes decidable. So the parser
#: records which shape the document is in, as ``host``, and the rule follows the
#: shape rather than guessing at it.
_MARKDOWN_PROFILE = {
    "block_starts": _MARKDOWN_BLOCK_STARTS,
    "inline_kinds": frozenset({"heading", "cell"}),
    # A heading, a blockquote line and a cell get exactly one line in Markdown.
    # Any added line leaves the block: a second line of a heading is a new
    # paragraph, and a second line of a blockquote is outside the quote.
    "no_added_lines": frozenset({"heading", "quote", "cell"}),
    "line_message": ("a {kind} segment is one line in the host; a line break "
                     "in the target leaves the block"),
}

#: Paragraphs separated by blank lines. A chapter title is still one line — a
#: break in it leaves a second line of a title standing as a paragraph — but a
#: body paragraph may be re-wrapped freely.
_TEXT_PROFILE = {
    "block_starts": (),
    "inline_kinds": frozenset(),
    "no_added_lines": frozenset({"heading"}),
    "line_message": ("a {kind} is one line in a plain-text document; a line break "
                     "in the target leaves it"),
}

#: One paragraph per line, which is how a great many .txt novels are written.
#: Here the rule reaches the body too, because there is no wrapping for it to be
#: confused with.
_TEXT_LINE_PROFILE = dict(_TEXT_PROFILE, no_added_lines=frozenset({"heading", "para"}),
                          line_message=("the target has more lines than the source, and in "
                                        "this document a line break starts a new paragraph"))

#: Markdown is the fallback for an absent or unrecognized host, because every
#: state file written before formats existed holds Markdown and says nothing.
#: An unrecognized one is not an error: a validator that raised on a `host` it
#: had not met would take down `lx check` for a document it could still mostly
#: judge, and `xhtml` already appears as a test value with no parser behind it.
_HOST_PROFILES = {
    "markdown": _MARKDOWN_PROFILE,
    "text": _TEXT_PROFILE,
    "text-line": _TEXT_LINE_PROFILE,
}


def _profile(seg):
    return _HOST_PROFILES.get(seg.get("host") or "markdown", _MARKDOWN_PROFILE)


def _block_start(line, table):
    """Name of the block this line would open in this host, or ``None``.

    The line's own leading blanks come off first, and that is a correctness fix
    rather than tidiness. Three of the seven patterns cap the indent they will
    match — CommonMark's ``\\s{0,3}`` for a heading, a thematic break and a setext
    underline — so a segment that *sits* four columns in hides all three. A list
    item's second paragraph is exactly that segment, and since 2026-08-03 its
    target carries the source's four spaces by construction
    (``normalize.reseat_outer_blanks``), so the blinding was reachable from every
    caller at once: `'    # 標題'` matched nothing, `lx check` exited 0, and
    markdown-it-py rendered an ``<h1>`` where the source had a ``<p>``.

    Safe in the must-not-fire direction because it is monotone: every rule here
    anchors its indent at ``^`` with a run that a leading blank could only have
    satisfied — ``^[ \\t]*`` or ``^ {0,3}`` — so removing leading blanks can only
    make a match appear, never disappear. Both sides of every comparison in
    :func:`containment_problems` come through this function, so a source that
    legitimately opens a block keeps answering the same name it did.

    The character classes narrowed on 2026-08-03 (HANDOFF-021) and the
    monotonicity survived it, but the reason is worth stating because it changed:
    ``str.lstrip()`` eats U+3000, U+00A0, a form feed and a carriage return, and
    the patterns no longer do. So this function now answers *heading* for a line
    the parser would read as a paragraph — `` '\\u3000# x' `` lstrips to
    `` '# x' ``. That is the must-fire direction and it is deliberate: a segment
    sits at some indent inside its own block, and the rule is about what the
    line's *content* would open once it lands there.
    """
    line = line.lstrip()
    for name, opens in table:
        if opens(line):
            return name
    return None


def containment_problems(seg):
    """Messages for structure the target adds to the block it lands in.

    Read on the *unmasked* text, both sides. What reaches the file is what
    ``render`` writes, and ``render`` unmasks first — a rule that reads ⟦n⟧ is
    answering a slightly different question, and a near miss of that kind is
    exactly what this file exists to remove.

    Everything is compared against the source rather than stated absolutely,
    because a nested list item and a nested blockquote are ordinary input whose
    segments legitimately begin with a marker. The first line is compared
    positionally and later lines by set: ``- 譯文\\n- 內層`` turns one item
    carrying a nested list into two siblings, so position matters there, while a
    translation is free to rewrap, so it must not matter afterwards.

    Messages name the block a target opens and never the characters that open
    it. ``translate._user_message`` feeds these back to the model as ``problems``
    and the same restraint applies as in :func:`pair_problems`.
    """
    kind = seg.get("kind") or "para"
    profile = _profile(seg)
    slots = seg.get("slots") or {}
    src = unmask(seg["masked"], slots)
    tgt = unmask(seg.get("target") or "", slots)
    src_lines, tgt_lines = src.split("\n"), tgt.split("\n")
    table = profile["block_starts"]
    out = []

    if kind in profile["no_added_lines"] and len(tgt_lines) > len(src_lines):
        out.append(profile["line_message"].format(kind=kind))
    elif (any(not ln.strip() for ln in tgt_lines)
            and not any(not ln.strip() for ln in src_lines)):
        # A leading or trailing newline counts, because `"x\n".split("\n")`
        # ends in an empty line and so does the document. Cosmetic in a
        # paragraph and structural in a list item, where the blank line ends the
        # list — one rule rather than a carve-out per kind, since deciding which
        # blank lines are harmless is judgement, and invariant 4 excludes that.
        #
        # Reached now by a no-added-lines kind whose target did *not* grow, which
        # it was not before: the two branches used to key on the kind alone.
        #
        # Measured against the old branch, and the first measurement was wrong in
        # a way worth keeping written down. It swept twelve line shapes and found
        # every difference to be a `heading`, `quote` or `cell` whose *source* has
        # more than one line — which `mdparse` cannot produce, since all three
        # capture exactly one — and concluded Markdown was unchanged. The shape
        # set had no blank or whitespace-only *target* in it. Adding six (``""``,
        # ``" "``, ``"\n"``, and so on) moves the count from 3 of 864 to 129 of
        # 1944, and 126 of those 129 are exactly that case: a single-line-source
        # heading with an empty target, which the old code answered `[]` and this
        # one answers with the blank-line message. A large sweep across a shape
        # set missing one dimension reads like proof and is not.
        #
        # Re-measured 2026-08-02: none of the 129 is reachable through
        # `check_segment`, which returns at the `not tgt.strip()` guard below
        # before containment runs. So Markdown is unchanged in fact — but by that
        # guard, not by the reason first recorded here, and a direct caller of
        # this public function does not inherit it.
        #
        # What the change buys is plain text: a paragraph whose target grew a
        # blank line without growing past the source's line count.
        out.append("the target contains a blank line, which ends the block it sits in")

    if kind not in profile["inline_kinds"]:
        opens = _block_start(tgt_lines[0], table)
        if opens and opens != _block_start(src_lines[0], table):
            out.append(f"the target opens a {opens}; the source does not")
        later = {_block_start(ln, table) for ln in src_lines[1:]}
        for i, line in enumerate(tgt_lines[1:], start=2):
            opens = _block_start(line, table)
            if opens and opens not in later:
                out.append(f"line {i} of the target opens a {opens}; "
                           f"a translation may not add a block")

    if kind == "cell" and tgt.count("|") > src.count("|"):
        out.append("the target adds a '|', which starts a new table column")

    return out


#: Reference *syntax*, so that `&amp;` and `&#8212;` are not reported as the raw
#: ampersand they contain. The name is deliberately not checked against a table:
#: an undeclared entity is the host's own error, and a second place to be wrong
#: about HTML is what the void-element decision already refused on 2026-07-28.
#: What this catches is the case that actually occurs — `AT&T`, `a & b`.
_XML_REF_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#\d+|#[xX][0-9A-Fa-f]+);")

#: Host syntaxes and their escaping requirement. Markdown declares none — a `<`
#: or an `&` in a Markdown document is ordinary text — so the table has no live
#: row until EPUB lands. It is written now because `render()` performs no
#: escaping of slot values at all, which on XHTML produces a file that does not
#: parse; a format that emits XHTML segments sets `host` on them and this rule
#: starts working with no change here.
_XML_HOSTS = frozenset({"xml", "xhtml"})


def escaping_problems(target, host):
    """Messages for characters a target carries raw that its host cannot hold.

    Absolute rather than compared against the source, unlike every other rule
    here: in an XML host these are never legal character data, so there is
    nothing a correct source could have had. If one ever fires on a character the
    source itself carried, the parser that produced the segment is the bug, and
    this is how it surfaces rather than how it is excused.

    Read on the *masked* target, deliberately — the opposite side from
    :func:`containment_problems`, and for the same reason. Every character the
    host's own markup contributed is a ⟦n⟧ here and restores verbatim, so what is
    left is exactly what the model wrote itself, which is the only thing that can
    be unescaped. Reading the restored text would flag every legitimate tag.

    ``>`` on its own is legal character data and is not reported; ``]]>`` is not,
    because it ends a CDATA section wherever one is open.
    """
    if host not in _XML_HOSTS:
        return []
    out = []
    if "<" in target:
        out.append("the target contains a raw '<'; an XML host needs '&lt;'")
    if "&" in _XML_REF_RE.sub("", target):
        out.append("the target contains a raw '&'; an XML host needs '&amp;'")
    if "]]>" in target:
        out.append("the target contains ']]>', which ends a CDATA section; "
                   "write ']]&gt;'")
    return out


def check_segment(seg, lang, cfg, glossary, dnt):
    issues = []
    src, tgt = seg["masked"], seg.get("target") or ""
    disabled = set(cfg.get("checks_disabled", []))

    def add(rule, sev, msg):
        if rule not in disabled:
            issues.append({"seg": seg["id"], "rule": rule, "severity": sev, "message": msg})

    if not tgt.strip():
        add("missing", "error", "no translation")
        return issues

    # 1. placeholder integrity — presence as a multiset, then pair order
    a, b = Counter(PH_RE.findall(src)), Counter(PH_RE.findall(tgt))
    if a != b:
        lost = sorted((a - b).elements())
        extra = sorted((b - a).elements())
        add("tags", "error", f"placeholder mismatch lost={lost} extra={extra}")
    for msg in pair_problems(tgt, seg.get("slots") or {}):
        add("tags", "error", msg)

    # 2. containment and escaping — what the target does to the block it lands in
    for msg in containment_problems(seg):
        add("containment", "error", msg)
    for msg in escaping_problems(tgt, seg.get("host") or "markdown"):
        add("escaping", "error", msg)
    # One-directional, and deliberately not widened. A target that *drops* a CR
    # the source had cannot be flagged — a translation is allowed to rewrap and
    # comparing break counts fails the legitimate case. A target that invents one
    # is decidable, and that is the half worth taking: on a document whose
    # terminators arrived mixed, the CR stays in the source and the model is
    # asked to reproduce it, so five different replies all passed before this.
    if "\r" in tgt and "\r" not in src:
        add("eol", "error", "the target adds a carriage return; a document's "
                            "line terminator is applied once at render")

    # 3. untranslated passthrough
    if tgt.strip() == src.strip() and len(strip_placeholders(src).split()) >= 3:
        add("untranslated", "warn", "target identical to source")

    # 4. glossary
    ls = src.lower()
    for row in glossary:
        if re.search(rf"(?<![A-Za-z]){re.escape(row['source'].lower())}(?![A-Za-z])", ls):
            if row["target"] and row["target"] not in tgt:
                add("glossary", row["severity"],
                    f"{row['source']!r} should render as {row['target']!r}")
            for bad in row["forbidden"]:
                if bad in tgt:
                    add("glossary", "error", f"forbidden rendering {bad!r} for {row['source']!r}")

    # 5. locale lexicon
    if lang == "zh-TW":
        lex = dict(ZH_TW_LEXICON)
        lex.update({k: (v, "error") if isinstance(v, str) else tuple(v)
                    for k, v in cfg.get("lexicon_extra", {}).items()})
        for bad, (good, sev) in lex.items():
            if bad not in tgt:
                continue
            # A guarded row fires only if some occurrence is not inside the
            # longer word the guard names — 電視頻道 is exempt, a bare 視頻 is not.
            tail = _LEXICON_UNLESS_FOLLOWED_BY.get(bad)
            if tail and not re.search(f"{re.escape(bad)}(?![{tail}])", tgt):
                continue
            add("lexicon", sev, f"{lang} writes this as {good!r}, not {bad!r}")

    # 6. punctuation
    if CJK_RE.search(tgt):
        m = re.search(rf"[{CJK}]\s*([,;:!?])", tgt)
        if m:
            add("punct", "warn", f"half-width {m.group(1)!r} after CJK")
        if re.search(rf'"[^"]*[{CJK}][^"]*"', tgt):
            add("punct", "warn", "straight quotes around CJK; prefer 「」")
        if re.search(rf"[{CJK}][A-Za-z0-9]|[A-Za-z0-9][{CJK}]", tgt):
            add("spacing", "warn", "missing space at CJK/Latin boundary")

    # 7. numeric fidelity
    ns = Counter(re.findall(r"\d+(?:\.\d+)?", strip_placeholders(src)))
    nt = Counter(re.findall(r"\d+(?:\.\d+)?", strip_placeholders(tgt)))
    missing = sorted((ns - nt).elements())
    if missing:
        add("numbers", "error", f"numbers absent from target: {missing}")

    # 8. do-not-translate leakage (terms not masked because they appeared post-hoc)
    for term in dnt:
        if term in strip_placeholders(src) and term not in strip_placeholders(tgt):
            add("dnt", "warn", f"protected term {term!r} missing in target")

    # 9. length plausibility
    lo, hi = cfg.get("length_ratio", {}).get(lang, [0.2, 2.5])
    slen, tlen = len(strip_placeholders(src).strip()), len(strip_placeholders(tgt).strip())
    if slen >= 40:
        ratio = tlen / slen
        if ratio < lo:
            add("length", "warn", f"target unusually short (ratio {ratio:.2f} < {lo})")
        elif ratio > hi:
            add("length", "warn", f"target unusually long (ratio {ratio:.2f} > {hi})")

    return issues
