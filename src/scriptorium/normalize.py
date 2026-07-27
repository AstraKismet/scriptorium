"""Automatic repair of mechanical defects.

The cheapest defect is the one that cannot be introduced. Punctuation width and
CJK/Latin spacing are fully decidable, so they are fixed on ingest rather than
reported as problems for a model to think about.
"""

import re

from .mask import CJK, CJK_RE, mask, unmask

_HALF_TO_FULL = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
FULLWIDTH = "，。！？；：「」『』（）【】《》、…—"


def normalize_zh(text, ops):
    out = text
    if "punct" in ops:
        for half, full in _HALF_TO_FULL.items():
            out = re.sub(rf"(?<=[{CJK}]){re.escape(half)}(?=\s|$|[{CJK}])", full, out)
        out = re.sub(rf"(?<=[{CJK}])\.(?=\s*$|[{CJK}])", "。", out)
        out = re.sub(
            r"\(([^()]*)\)",
            lambda m: f"（{m.group(1)}）" if CJK_RE.search(m.group(1)) else m.group(0),
            out,
        )
        out = re.sub(rf"[ \t]+(?=[{re.escape(FULLWIDTH)}])", "", out)
        out = re.sub(rf"(?<=[{re.escape(FULLWIDTH)}])[ \t]+", "", out)
    if "pangu" in ops:
        out = re.sub(rf"(?<=[{CJK}])(?=[A-Za-z0-9$])", " ", out)
        out = re.sub(rf"(?<=[A-Za-z0-9%$)])(?=[{CJK}])", " ", out)
    if "collapse_space" in ops:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"[ \t]+$", "", out, flags=re.M)
    return out


def ops_for(lang, cfg):
    return cfg.get("normalize", {}).get(lang, [])


def normalize(text, lang, cfg):
    """Applied on ingest, while placeholders are still in place."""
    ops = ops_for(lang, cfg)
    if ops and lang.lower().startswith("zh"):
        return normalize_zh(text, ops)
    return text


def polish_rendered(text, lang, cfg):
    """Applied after restoration, so boundaries around restored spans get spaced.

    Markup is re-masked first so nothing inside a code span or URL is touched.
    """
    ops = [o for o in ops_for(lang, cfg) if o == "pangu"]
    if not ops or not lang.lower().startswith("zh"):
        return text
    masked, slots = mask(text)
    return unmask(normalize_zh(masked, ops), slots)
