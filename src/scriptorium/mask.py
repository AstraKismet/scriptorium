"""Markup protection.

Non-translatable spans are replaced with opaque ``⟦n⟧`` placeholders before a
model ever sees the text, and restored afterwards. Doing this in code rather
than in a prompt is what makes structural fidelity a property of the pipeline
instead of a hope.
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


def mask(text, dnt=()):
    """Return ``(masked_text, {slot_id: original})``.

    Inline markup is masked first, then verbatim do-not-translate terms.
    ASCII terms match at word boundaries, so ``Go`` will not match inside
    ``Google``.
    """
    slots = {}
    counter = [0]

    def take(value):
        counter[0] += 1
        n = str(counter[0])
        slots[n] = value
        return f"{PH_OPEN}{n}{PH_CLOSE}"

    out = text
    for _name, pat in INLINE_PATTERNS:
        out = pat.sub(lambda m: take(m.group(0)), out)
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
    """Restore placeholders, following nesting a few levels deep."""
    def repl(m):
        return slots.get(m.group(1), m.group(0))

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
