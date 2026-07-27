"""Markdown to render skeleton + translatable segments, and back.

``parse`` returns ``(nodes, segments)`` where nodes reproduce the file exactly
once segment values are substituted. Structure therefore cannot regress: it is
never reconstructed from a model's output, only refilled.
"""

import re

from .mask import CJK, mask, strip_placeholders, unmask
from .store import seg_hash

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*?)(\s*#*\s*)$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-{2,})\s*$")
LIST_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
QUOTE_RE = re.compile(r"^(\s*>\s?)(.*)$")
HR_RE = re.compile(r"^\s{0,3}(?:\*{3,}|-{3,}|_{3,})\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
DEF_RE = re.compile(r"^(\s*\[[^\]]+\]:\s*)(.*)$")


def parse(text, dnt=()):
    """Split markdown into a render skeleton + translatable segments."""
    lines = text.split("\n")
    trailing_nl = text.endswith("\n")
    if trailing_nl and lines and lines[-1] == "":
        lines.pop()          # the split artifact, not a real blank line
    nodes, segs = [], []
    i, n = 0, len(lines)
    counter = [0]

    def emit_raw(s):
        if nodes and nodes[-1]["t"] == "raw":
            nodes[-1]["v"] += s
        else:
            nodes.append({"t": "raw", "v": s})

    def emit_seg(source, kind):
        source = source.rstrip("\r")
        if not source.strip() or not re.search(r"[A-Za-z\u00c0-\u024f" + CJK + r"]", source):
            emit_raw(source)
            return
        counter[0] += 1
        sid = f"s{counter[0]:04d}"
        masked, slots = mask(source, dnt)
        if not strip_placeholders(masked).strip():
            emit_raw(source)  # nothing left to translate
            counter[0] -= 1
            return
        segs.append({
            "id": sid, "kind": kind, "hash": seg_hash(source),
            "source": source, "masked": masked, "slots": slots,
            "target": None, "status": "pending", "origin": None,
        })
        nodes.append({"t": "seg", "id": sid})

    # front matter
    if n and lines[0].strip() == "---":
        j = 1
        while j < n and lines[j].strip() != "---":
            j += 1
        if j < n:
            emit_raw("\n".join(lines[: j + 1]) + "\n")
            i = j + 1

    while i < n:
        line = lines[i]

        m = FENCE_RE.match(line)
        if m:
            fence = m.group(2)[0] * 3
            j = i + 1
            while j < n and not re.match(rf"^\s*{re.escape(fence)}", lines[j]):
                j += 1
            j = min(j, n - 1)
            emit_raw("\n".join(lines[i : j + 1]) + "\n")
            i = j + 1
            continue

        if not line.strip() or HR_RE.match(line) or SETEXT_RE.match(line):
            emit_raw(line + "\n")
            i += 1
            continue

        m = HEADING_RE.match(line)
        if m:
            emit_raw(m.group(1))
            emit_seg(m.group(2), "heading")
            emit_raw(m.group(3) + "\n")
            i += 1
            continue

        m = DEF_RE.match(line)
        if m:
            emit_raw(line + "\n")
            i += 1
            continue

        # table
        if "|" in line and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1]):
            while i < n and "|" in lines[i]:
                if TABLE_SEP_RE.match(lines[i]):
                    emit_raw(lines[i] + "\n")
                else:
                    parts = re.split(r"(\|)", lines[i])
                    for p in parts:
                        if p == "|":
                            emit_raw(p)
                        elif p.strip():
                            lead = p[: len(p) - len(p.lstrip())]
                            trail = p[len(p.rstrip()) :]
                            emit_raw(lead)
                            emit_seg(p.strip(), "cell")
                            emit_raw(trail)
                        else:
                            emit_raw(p)
                    emit_raw("\n")
                i += 1
            continue

        m = QUOTE_RE.match(line)
        if m:
            emit_raw(m.group(1))
            emit_seg(m.group(2), "quote")
            emit_raw("\n")
            i += 1
            continue

        m = LIST_RE.match(line)
        if m:
            prefix, rest = m.group(1), m.group(2)
            body = [rest]
            j = i + 1
            indent = len(prefix)
            while j < n and lines[j].strip() and not LIST_RE.match(lines[j]) \
                    and not HEADING_RE.match(lines[j]) and not FENCE_RE.match(lines[j]) \
                    and len(lines[j]) - len(lines[j].lstrip()) >= indent:
                body.append(lines[j].strip())
                j += 1
            emit_raw(prefix)
            emit_seg("\n".join(body), "list")
            emit_raw("\n")
            i = j
            continue

        # paragraph
        body = [line]
        j = i + 1
        while j < n and lines[j].strip() and not LIST_RE.match(lines[j]) \
                and not HEADING_RE.match(lines[j]) and not FENCE_RE.match(lines[j]) \
                and not QUOTE_RE.match(lines[j]) and not HR_RE.match(lines[j]) \
                and not SETEXT_RE.match(lines[j]):
            body.append(lines[j])
            j += 1
        emit_seg("\n".join(body), "para")
        emit_raw("\n")
        i = j

    # every block emitter appends its own newline; drop the last one when the
    # source did not actually end with a line break
    if not trailing_nl and nodes and nodes[-1]["t"] == "raw" and nodes[-1]["v"].endswith("\n"):
        nodes[-1]["v"] = nodes[-1]["v"][:-1]
        if not nodes[-1]["v"]:
            nodes.pop()

    return nodes, segs


def render(doc, cfg, polish=None, fallback=False):
    """Rebuild the target document from the skeleton."""
    by_id = {s["id"]: s for s in doc["segments"]}
    parts, missing = [], 0
    for node in doc["nodes"]:
        if node["t"] == "raw":
            parts.append(node["v"])
            continue
        seg = by_id[node["id"]]
        if seg.get("target"):
            text = unmask(seg["target"], seg["slots"])
            parts.append(polish(text) if polish else text)
        else:
            missing += 1
            parts.append(unmask(seg["masked"], seg["slots"]) if fallback
                         else f"<!-- untranslated {seg['id']} -->")
    return "".join(parts), missing
