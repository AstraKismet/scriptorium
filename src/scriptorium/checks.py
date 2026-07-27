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
# severity: error | warn
ZH_TW_LEXICON = {
    "軟件": ("軟體", "error"), "硬件": ("硬體", "error"), "插件": ("外掛/擴充套件", "error"),
    "視頻": ("影片", "error"), "音頻": ("音訊", "error"), "屏幕": ("螢幕", "error"),
    "程序": ("程式", "warn"), "數據": ("資料", "error"), "網絡": ("網路", "error"),
    "服務器": ("伺服器", "error"), "內存": ("記憶體", "error"), "硬盤": ("硬碟", "error"),
    "打印": ("列印", "error"), "缺省": ("預設", "error"), "默認": ("預設", "error"),
    "鼠標": ("滑鼠", "error"), "菜單": ("選單", "error"), "信息": ("資訊", "error"),
    "激活": ("啟用", "error"), "集成": ("整合", "error"), "交互": ("互動", "error"),
    "兼容": ("相容", "error"), "調試": ("除錯", "error"), "隊列": ("佇列", "error"),
    "數組": ("陣列", "error"), "對象": ("物件", "error"), "函數": ("函式", "error"),
    "變量": ("變數", "error"), "指針": ("指標", "error"), "線程": ("執行緒", "error"),
    "進程": ("行程", "error"), "緩存": ("快取", "error"), "帶寬": ("頻寬", "error"),
    "端口": ("連接埠", "error"), "登錄": ("登入", "error"), "賬號": ("帳號", "error"),
    "短信": ("簡訊", "error"), "標簽": ("標籤", "error"), "復用": ("重複使用", "warn"),
    "質量": ("品質", "warn"), "智能": ("智慧/智慧型", "warn"), "視圖": ("檢視", "warn"),
    "用戶": ("使用者", "warn"), "支持": ("支援", "warn"), "文本": ("文字/文本", "warn"),
}


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
            if bad in tgt:
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
