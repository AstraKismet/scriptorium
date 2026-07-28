"""Validators. Every rule here is mechanically decidable — that is the entry test.

Rules append to ``issues`` with a severity; ``error`` fails the build. Structural
fidelity is deliberately absent: it is guaranteed by :mod:`.mdparse`, not checked.
"""

import re
from collections import Counter

from .mask import CJK, CJK_RE, PH_RE, strip_placeholders

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

    # 1. placeholder integrity
    a, b = Counter(PH_RE.findall(src)), Counter(PH_RE.findall(tgt))
    if a != b:
        lost = sorted((a - b).elements())
        extra = sorted((b - a).elements())
        add("tags", "error", f"placeholder mismatch lost={lost} extra={extra}")

    # 2. untranslated passthrough
    if tgt.strip() == src.strip() and len(strip_placeholders(src).split()) >= 3:
        add("untranslated", "warn", "target identical to source")

    # 3. glossary
    ls = src.lower()
    for row in glossary:
        if re.search(rf"(?<![A-Za-z]){re.escape(row['source'].lower())}(?![A-Za-z])", ls):
            if row["target"] and row["target"] not in tgt:
                add("glossary", row["severity"],
                    f"{row['source']!r} should render as {row['target']!r}")
            for bad in row["forbidden"]:
                if bad in tgt:
                    add("glossary", "error", f"forbidden rendering {bad!r} for {row['source']!r}")

    # 4. locale lexicon
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

    # 5. punctuation
    if CJK_RE.search(tgt):
        m = re.search(rf"[{CJK}]\s*([,;:!?])", tgt)
        if m:
            add("punct", "warn", f"half-width {m.group(1)!r} after CJK")
        if re.search(rf'"[^"]*[{CJK}][^"]*"', tgt):
            add("punct", "warn", "straight quotes around CJK; prefer 「」")
        if re.search(rf"[{CJK}][A-Za-z0-9]|[A-Za-z0-9][{CJK}]", tgt):
            add("spacing", "warn", "missing space at CJK/Latin boundary")

    # 6. numeric fidelity
    ns = Counter(re.findall(r"\d+(?:\.\d+)?", strip_placeholders(src)))
    nt = Counter(re.findall(r"\d+(?:\.\d+)?", strip_placeholders(tgt)))
    missing = sorted((ns - nt).elements())
    if missing:
        add("numbers", "error", f"numbers absent from target: {missing}")

    # 7. do-not-translate leakage (terms not masked because they appeared post-hoc)
    for term in dnt:
        if term in strip_placeholders(src) and term not in strip_placeholders(tgt):
            add("dnt", "warn", f"protected term {term!r} missing in target")

    # 8. length plausibility
    lo, hi = cfg.get("length_ratio", {}).get(lang, [0.2, 2.5])
    slen, tlen = len(strip_placeholders(src).strip()), len(strip_placeholders(tgt).strip())
    if slen >= 40:
        ratio = tlen / slen
        if ratio < lo:
            add("length", "warn", f"target unusually short (ratio {ratio:.2f} < {lo})")
        elif ratio > hi:
            add("length", "warn", f"target unusually long (ratio {ratio:.2f} > {hi})")

    return issues
