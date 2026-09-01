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

from .mask import (
    CJK,
    CJK_RE,
    PH_RE,
    strip_placeholders,
    target_map,
    term_pattern,
    unmask,
)
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


# ── the second numeral system ───────────────────────────────────────────────
#
# Chinese and Japanese write cardinal numbers twice over. 第一章 and 第 1 章 are
# both "Chapter 1", and which of them prose uses is habit and register rather
# than rule, so "the target carries the same ASCII digits" is not a decidable
# proposition for those languages. Measured 2026-09-02 over 129 labelled pairs:
# rule 7 reported 62 correct translations — 51 of the 55 CJK-target cases in the
# oracle alone — every one at error severity, which stops `lx run` rendering and
# makes the exit code invariant 10 rests on wrong. It is the second half of the
# 2026-07-27 measurement reappearing on a different rule.
#
# What *is* decidable is the figure a run of numeral characters reads as, so the
# relaxation parses the target rather than spelling the source. Spelling loses
# twice. One integer has several correct spellings — 二 and 兩, 一九八四 and
# 一千九百八十四 — so a spelling table has to enumerate them, and this project has
# recorded five times what happens when an enumeration is read as the definition
# of the rule it stood in for. And a substring search for a spelling accepts 一
# inside 十一, which is 11.
#
# What a character has to be to belong below: a glyph either language uses to
# write a cardinal number. The tables are that sentence's consequence, never its
# definition.
_CJK_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3,
               "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
               "壹": 1, "貳": 2, "贰": 2, "參": 3, "叁": 3, "参": 3, "肆": 4,
               "伍": 5, "陸": 6, "陆": 6, "柒": 7, "捌": 8, "玖": 9}
_CJK_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CJK_MYRIAD = {"萬": 10 ** 4, "万": 10 ** 4, "億": 10 ** 8, "亿": 10 ** 8,
               "兆": 10 ** 12}

#: 廿 and 卅 are the twenty and thirty a Taiwanese newspaper still sets. Alone
#: among the characters here they cannot collide with an ordinary word, so
#: admitting them costs nothing measurable and refusing them would be a gap.
_CJK_SCORE = {"廿": 20, "卅": 30}

#: The financial alphabet: mandatory on a cheque, an ordinary word everywhere
#: else. 參 occurs 13 times in this repository's own 9354 characters of Chinese
#: prose and is 參考/參照/參數/參閱/參見 every one of them, never 3; 陸 is 登陸,
#: 伍 is 隊伍, 拾 is 收拾. So a **lone** glyph from this set reads as no number.
#: One condition over a closed set — the shape `_LEXICON_UNLESS_FOLLOWED_BY`
#: already uses, and the discipline the 2026-07-28 audit set for it. A cheque
#: writes 伍仟元整 and never a bare 伍, so the guard costs the alphabet nothing.
_FINANCIAL = frozenset("壹貳贰參叁参肆伍陸陆柒捌玖拾佰仟")

_CJK_NUM_RE = re.compile("[" + "".join(sorted(
    set(_CJK_DIGITS) | set(_CJK_UNITS) | set(_CJK_MYRIAD) | set(_CJK_SCORE))) + "]+")

#: `25 萬` is a numeral too, half of it in Arabic, and it is how zh-TW writes a
#: large round number.
_ARABIC_MYRIAD_RE = re.compile(r"(\d[\d,]*)\s*([萬万億亿兆])")

_FULLWIDTH = {ord(wide): ord(narrow) for wide, narrow in
              zip("０１２３４５６７８９", "0123456789")}

#: A figure as the rule has read one since 2026-07-27. **Only a target language
#: this rule relaxes for gets anything else**, because a target that does not
#: write CJK numerals also does not group its thousands with a comma: folding the
#: separator for every language turned a French `1,234 words` rendered
#: `1 234 mots` from silent into an error, which is a regression on a language
#: this package was not about. Measured 2026-09-02.
STRICT_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

#: A figure, in either width and with or without its thousands separators.
#: Grouping is matched first, so `250,000` is one figure rather than `250` and
#: `000` — which is what it was until 2026-09-02, so a target that regrouped or
#: dropped the separators was told two numbers were missing.
NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

#: Which languages get the relaxation, by primary subtag, so `zh-TW`, `zh-Hant`
#: and a bare `zh` are one answer and no region or script suffix can take a
#: language out of the set. `_` is folded to `-` first because `cli.language_tag`
#: admits it, so `zh_TW` is a tag this project accepts and a gate that split on
#: `-` alone turned the relaxation silently off for it.
#:
#: **The test is a property of the language and not the list of languages this
#: project supports**: its ordinary prose writes cardinal numbers with the
#: characters above. That is why the Sinitic varieties are here although nothing
#: translates into them yet — the two mistakes are not the same size. Admitting a
#: language wrongly only relaxes a target that literally contains these
#: characters, which a non-CJK translation does not; refusing one wrongly
#: reinstates the whole defect for that language, silently. Korean is
#: deliberately out: modern Korean writes its numbers in Arabic or Hangul and
#: reaches for hanja numerals about as often as English reaches for Roman ones.
#: `Kapitel 一` is broken German and the rule has to keep saying so.
_CJK_NUMERAL_LANGS = ("zh", "cmn", "yue", "nan", "hak", "wuu", "ja")


def canonical(figure):
    """One spelling per figure — of its *encoding* only, never of its value.

    Full width folds to ASCII and the thousands separators come out, because
    `１９８４` and `250,000` are the same figures written for a different column
    or a different keyboard, and nothing about the number is lost either way.

    A leading zero stays, and so does a trailing one after the point. `08` is a
    month a target may legitimately render as 八 and this rule reports it — a
    cost recorded rather than paid off — but folding the zero means `007`
    rendered 七號 passes with nothing said, and folding `1.10` onto `1.1` lets a
    target claim a version the source never named. Those are one move, and the
    rule takes neither half of it.
    """
    return figure.translate(_FULLWIDTH).replace(",", "")


def _cjk_positional(run):
    """The place-value reading, as a figure — 十一 is 11, 一百零五 is 105.

    ``None`` when the run is not a number in this reading, which is what most
    ordinary words made of these characters come back as: 萬一 (in case), 千萬
    (by all means) and 三三兩兩 (in twos and threes) read as nothing at all
    rather than as some figure nobody wrote.

    A place and a myriad each need something in front of them. That is one rule
    doing two jobs. 百 with no digit is 百年 (a hundred years), 百般 (in every
    way) and the 百 of 百分之 (per cent, counting nothing) — one string in three
    senses, which is the judgement invariant 4 excludes — while the number is
    written 一百. And it bounds the arithmetic, because a myriad can never
    multiply an accumulator a myriad has already filled.

    A run of more than one character that *starts* with a zero has no
    place-value reading: 零 marks a skipped place, as in 一百零五, and a number
    is not written with one in front of it. Without this, 〇〇七 read as 7 here
    and the digit-by-digit reading below — which answers `007`, the figure the
    source wrote — was never reached.
    """
    if len(run) > 1 and run[0] in ("〇", "零"):
        return None
    total = section = 0
    digit = None
    scale = 0                             # the last place consumed; see below
    for ch in run:
        if ch in ("〇", "零"):
            if digit is not None:
                return None
            scale = 0                     # the skip names the place; see below
            continue                      # a placeholder, carrying no value
        if ch in _CJK_SCORE:
            if digit is not None or section:
                return None
            section = _CJK_SCORE[ch]      # 廿八 is 28, so the run keeps going
            continue
        if ch in _CJK_DIGITS:
            if digit is not None:
                return None               # two bare digits: not this reading
            digit = _CJK_DIGITS[ch]
        elif ch in _CJK_UNITS:
            unit = _CJK_UNITS[ch]
            if digit is None and unit != 10:
                return None
            section += (1 if digit is None else digit) * unit
            digit = None
            scale = unit
        else:
            head = section + (digit or 0)
            if not head:
                return None
            total += head * _CJK_MYRIAD[ch]
            section = 0
            digit = None
            scale = _CJK_MYRIAD[ch]
    # A trailing bare digit takes the place one below the last one used, which is
    # how Chinese elides it: 一千五 is 1500 and 兩百五 is 250, while 二十五 is 25
    # because one below 十 is the units. It is not a special case for large
    # numbers — it is the same rule at every scale, and 一千零五 still reads 1005
    # because the 零 says which place was skipped.
    return str(total + section + (digit or 0) * (scale // 10 if scale > 10 else 1))


def _cjk_digit_string(run):
    """The digit-by-digit reading — 一九八四 is 1984, 二〇二六 is 2026.

    How a novel writes a year, which is why it is here rather than left to the
    place-value reading: 一千九百八十四 is the same integer and almost nobody
    writes it. Two characters at least, because a one-character run already has
    a reading and the two agree on it. 兩 is excluded: it is the form 2 takes
    before a classifier and is never a digit in a string of them.

    A string of digits rather than an integer, so 〇〇七 answers `007`. Which is
    also why nothing in this module calls ``int`` on text it did not write: a
    source carrying a four-thousand-digit figure would otherwise reach a
    conversion that raises from Python 3.11 on, and CI runs 3.12.

    The length floor is an **equivalent guard**, labelled here rather than
    deleted: `cjk_numeral_values` offers the place-value reading first, and over
    the whole character class there is no single character that reading refuses
    while this one would accept — 百, 千 and 萬 have no place-value reading alone
    and are not digits either. It states this function's own contract for the
    caller that does not exist yet, so it stays, and no mutant can kill it.
    """
    if len(run) < 2:            # an equivalent guard; see the docstring
        return None
    digits = []
    for ch in run:
        if ch not in _CJK_DIGITS or ch in ("兩", "两"):
            return None
        digits.append(str(_CJK_DIGITS[ch]))
    return "".join(digits)


def cjk_numeral_values(text):
    """Every figure this text writes in CJK numerals, as a multiset.

    **One run answers for one figure**, which is the multiset discipline rule 7
    already had: the place-value reading is offered first and the digit-by-digit
    one only where it failed. The two cannot both succeed — a run of two or more
    bare digits is not place-value, and on a single character they agree — so the
    order costs nothing today, and it keeps "one run, one number" true by
    construction rather than by that coincidence.
    """
    figures = Counter()
    for match in _CJK_NUM_RE.finditer(text):
        run = match.group(0)
        if len(run) == 1 and run in _FINANCIAL:
            continue
        reading = _cjk_positional(run)
        if reading is None:
            reading = _cjk_digit_string(run)
        if reading is not None:
            figures[canonical(reading)] += 1
    for match in _ARABIC_MYRIAD_RE.finditer(text):
        # A multiplier that is a power of ten is a run of zeros, so the scaling
        # is a concatenation and `int()` is never reached — see the note in
        # `_cjk_digit_string`.
        figures[canonical(match.group(1))
                + "0" * (len(str(_CJK_MYRIAD[match.group(2)])) - 1)] += 1
    return figures


def spells_numbers_in_cjk(lang):
    """Whether this target language writes cardinal numbers with 一二三…

    A non-string answers no rather than raising. `cli.language_tag` refuses one
    at every surface that receives a `lang` from outside, but `check_segment`
    itself has no such guarantee and the rule it stands in has to *report* on a
    document rather than traceback on one — which is what the incumbent's
    `lang == "zh-TW"` did for free.
    """
    if not isinstance(lang, str):
        return False
    return lang.lower().replace("_", "-").split("-")[0] in _CJK_NUMERAL_LANGS


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

    Read on the *unmasked* text, both sides, and each side against **its own**
    slot map — the source against the segment's, the target against
    :func:`mask.target_map`. What reaches the file is what ``render`` writes, and
    ``render`` unmasks first, so a rule that reads ⟦n⟧ is answering a slightly
    different question and a rule that unmasks with the *other* map is answering
    about bytes nobody writes. Both near misses are exactly what this file exists
    to remove, and the second one was live for the length of one commit on
    2026-09-01.

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
    # Two maps, and they are not the same one. The source is the fresh parse's;
    # the target speaks whatever numbering it was written in, which is what the
    # render substitutes. See `mask.target_map`.
    src = unmask(seg["masked"], seg.get("slots") or {})
    tgt = unmask(seg.get("target") or "", target_map(seg))
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


#: The `review` field's closed vocabulary. One value today and a *closed* set
#: rather than a boolean, which costs exactly the same and leaves room for
#: `approved` — a workflow stage the 2026-07-29 re-founding has promised since it
#: made review distinct from translation. The same reasoning `variant` was added
#: under.
#:
#: It lives in this module rather than in `store.py` because this is the one both
#: `translate.py` and `cli.py` already import, so the name that decides what
#: `held` means and the rule that reports it have one home and add no edge to the
#: import graph. `store.py` writes whatever it is handed, the way it already
#: trusts an `origin`.
HELD = "held"
REVIEW_VALUES = (HELD,)


def is_held(seg):
    """Whether this segment is held out of every queue that selects work."""
    return seg.get("review") == HELD


def workable(segments):
    """The segments a run may touch: everything not held.

    **One helper, applied at every predicate that selects work** — the draft
    queue, the repair set and the polish set — rather than a condition copied
    into each. `translate.failing_segments` is the one that makes this worth
    stating: it is status-blind by design, so without the exclusion a held
    segment would be handed back to the model on every repair round of every run,
    which is the opposite of what holding it asked for.

    Naming the work an explicit id does is deliberately *not* filtered by this;
    see `cli.do_select`.
    """
    return [s for s in segments if not is_held(s)]


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

    # A hold is reported and never fails a build. `warn` is the whole design:
    # holding a segment says "leave this to me", and a severity that stopped
    # `lx check` would turn every held segment into a blocked render, so the one
    # way to finish a book would be to lift every hold first. Disable-able like
    # any other rule, and below the `missing` return because holding requires a
    # target — a segment with none is answered by `missing`, which is the more
    # useful sentence.
    if is_held(seg):
        add("held", "warn", "held for review; no queue will select this segment — `lx translate --ids` still will")

    # 1. placeholder integrity — presence as a multiset, then pair order
    a, b = Counter(PH_RE.findall(src)), Counter(PH_RE.findall(tgt))
    if a != b:
        lost = sorted((a - b).elements())
        extra = sorted((b - a).elements())
        add("tags", "error", f"placeholder mismatch lost={lost} extra={extra}")
    for msg in pair_problems(tgt, seg.get("slots") or {}):
        add("tags", "error", msg)

    # 1a. the wording speaks a numbering the source has moved on from
    #
    # A segment carrying `target_slots` is one the divergence (24) keep path
    # stranded: the wording was written against one map and sits on a segment
    # whose parse produced another. `skeleton.render_blocks` unmasks it against
    # the map it was written in, so the rendered bytes are the reviewer's own
    # words — but the two maps disagreeing means the *source* has changed under
    # the wording, and until 2026-09-01 nothing said so after the extract that
    # did it: rule 1 above compares ids, and the measured case has the same ids
    # on both sides, so `lx check` was green on a segment `lx extract` had just
    # named. That contradicted what this project's own documents claimed about
    # what a kept segment does.
    #
    # **Warn, and for `bare_term`'s reason one step along.** The render is
    # correct, so a severity that failed the build would block a book over a
    # segment that comes out right; and the remedy is judgement — re-word it, or
    # accept that the source now protects a term this wording translates.
    # Decidable without any of that: two unmaskings of one string, compared.
    if seg.get("target_slots") and seg.get("slots"):
        if unmask(tgt, seg["target_slots"]) != unmask(tgt, seg["slots"]):
            add("numbering", "warn",
                "this wording was written against an older numbering and is "
                "rendered with it; the source has changed under it, so re-check "
                "what each ⟦n⟧ names now")

    # 1b. a protected term standing bare in the target
    #
    # The one thing that can see wording whose placeholders stopped meaning what
    # they meant. Every gate compares ids — `translate.accept` and rule 1 above
    # both — so a wholesale renumbering satisfies them, and the damage already
    # sitting in documents extracted before the numbering became deterministic
    # (2026-08-17) is reachable by nothing else.
    #
    # **Warn, and it cannot be anything else.** At the level of the text a defect
    # and a legitimate repeat are the same string: a translation may perfectly
    # well name a protected term once through its placeholder and once in prose.
    # A severity that failed the build would make clearing those the only way to
    # finish a book, which is the argument `held` is warn by.
    protected = {rec["original"] for rec in (seg.get("slots") or {}).values()
                 if rec.get("original") in set(dnt)}
    for term in sorted(protected):
        if term_pattern(term).search(strip_placeholders(tgt)):
            add("bare_term", "warn",
                f"{term!r} is protected here and also stands in the target as "
                f"plain text — check that each ⟦n⟧ still means what it meant")

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
    #
    # The strict comparison first, then — for a language that has a second
    # numeral system — one more chance for each figure it found missing. The
    # order is the design: a target that kept the digits is answered without
    # parsing anything, and the relaxation can only ever remove a report,
    # never add one.
    #
    # Everything the change touches is under the language gate, including how
    # a figure is read off the two texts. A target that writes CJK numerals is
    # also one that regroups a thousands separator or sets its digits full
    # width; a target that does not is left byte for byte as it was, because
    # folding the separator for every language turned a French `1,234 words`
    # rendered `1 234 mots` from silent into an error.
    #
    # The comparison stays between *figures*, so a decimal is kept strict by
    # construction rather than by a rule of its own: `1.22` is not a string
    # any reading above can produce, and a version number is protected
    # exactly as it was.
    #
    # The measured cost is a false negative on a small integer whose target
    # dropped it and happens to hold an ordinary word made of numeral
    # characters. 一 is 一個/一直/一切 in 183 of its 232 occurrences in this
    # repository's own Chinese prose, and no rule tells those from the 一 of
    # 第一章 without the judgement invariant 4 excludes. It is bought
    # deliberately: `docs/decisions.md`, 2026-09-02.
    src_text, tgt_text = strip_placeholders(src), strip_placeholders(tgt)
    if not spells_numbers_in_cjk(lang):
        ns = Counter(STRICT_NUMBER_RE.findall(src_text))
        nt = Counter(STRICT_NUMBER_RE.findall(tgt_text))
        missing = sorted((ns - nt).elements())
    else:
        ns = Counter(canonical(f) for f in NUMBER_RE.findall(src_text))
        nt = Counter(canonical(f) for f in NUMBER_RE.findall(tgt_text))
        spelled = cjk_numeral_values(tgt_text)
        missing = []
        for figure in sorted((ns - nt).elements()):
            if spelled[figure]:
                spelled[figure] -= 1
            else:
                missing.append(figure)
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
