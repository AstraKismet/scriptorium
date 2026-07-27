"""On-disk state: per-document segment stores and the translation memory."""

import hashlib
import json
import os
import re

from .config import STATE, dump_json, load_json


def seg_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def doc_id(src):
    rel = os.path.relpath(src).replace(os.sep, "/")
    return re.sub(r"[^A-Za-z0-9._-]", "_", rel)


def store_path(src, lang):
    return os.path.join(STATE, "docs", f"{doc_id(src)}.{lang}.json")


def report_path(src, lang):
    return os.path.join(STATE, "reports", f"{doc_id(src)}.{lang}.json")


def tm_path(lang):
    return os.path.join(STATE, f"tm.{lang}.jsonl")


def load_doc(src, lang):
    p = store_path(src, lang)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"no state for {src} [{lang}] — run `lx extract {src} --lang {lang}` first")
    return load_json(p)


def save_doc(src, lang, doc):
    dump_json(store_path(src, lang), doc)


def tracked(lang=None):
    d = os.path.join(STATE, "docs")
    out = []
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        doc = load_json(os.path.join(d, name))
        if lang and doc.get("lang") != lang:
            continue
        out.append(doc)
    return out


def load_tm(lang):
    """Last write wins, so a corrected segment supersedes its earlier form."""
    tm = {}
    p = tm_path(lang)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tm[rec["hash"]] = rec["target"]
    return tm


def append_tm(lang, records):
    if not records:
        return 0
    os.makedirs(STATE, exist_ok=True)
    with open(tm_path(lang), "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)
