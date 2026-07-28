"""On-disk state: per-document segment stores and the translation memory."""

import hashlib
import json
import os
import re

from .config import STATE, dump_json, load_json

#: Shape of a document state file. Bumped when a reader of an older file would be
#: wrong rather than merely incomplete. `__version__` cannot serve here: it moves
#: for unrelated reasons and a development build can move it backwards.
#:
#: 2 — slots became records (``original`` / ``role`` / ``pair_id`` /
#:     ``can_reorder``) instead of plain strings.
STATE_VERSION = 2


class StateVersionError(RuntimeError):
    """A state file this build cannot read. The message names the way out."""


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


def _refuse_if_newer(doc, src, lang):
    """Return the file's state version, refusing one this build cannot represent.

    The two directions are not symmetrical and must not be handled in one place.
    An *older* file is readable in the sense that matters — extract rebuilds it —
    so only the readers that would misinterpret it refuse. A *newer* one holds
    fields this build does not know about, and every write here is a whole-file
    rewrite, so any path that could save over it has to stop first. That includes
    the extract path, which does not go through :func:`load_doc` at all.
    """
    found = doc.get("state_version", 1)
    if found > STATE_VERSION:
        raise StateVersionError(
            f"state for {src} [{lang}] is version {found}, newer than the {STATE_VERSION} "
            f"this build reads — upgrade scriptorium, or start over with "
            f"`lx extract {src} --lang {lang} --reset`, which discards the newer file "
            f"(anything in it and not in the translation memory is lost that way).")
    return found


def load_doc(src, lang):
    p = store_path(src, lang)
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"no state for {src} [{lang}] — run `lx extract {src} --lang {lang}` first")
    doc = load_json(p)
    if _refuse_if_newer(doc, src, lang) < STATE_VERSION:
        raise StateVersionError(
            f"state for {src} [{lang}] is version {doc.get('state_version', 1)}, this build "
            f"reads {STATE_VERSION} — run `lx extract {src} --lang {lang}` to rebuild it. "
            f"Translations already in the file are carried over by content hash, "
            f"so do not pass --reset.")
    return doc


def prior_targets(src, lang):
    """``{content hash: (target, origin)}`` from an existing state file.

    Deliberately not :func:`load_doc` for the *older* direction: this is the one
    reader that must work across a bump, because re-extracting is how a stale
    file is migrated and carrying the translations over is the whole point of
    doing it that way. Only fields that no bump has changed are read.

    A file from a *newer* build is refused, because the caller is about to
    replace it. Reading it here and letting the write proceed was the first
    shape of this function, and it silently downgraded such a file — with a green
    exit code, while `lx check` on the same file refused to touch it.
    """
    p = store_path(src, lang)
    if not os.path.exists(p):
        return {}
    doc = load_json(p, {})
    _refuse_if_newer(doc, src, lang)
    return {s["hash"]: (s["target"], s.get("origin") or "carryover")
            for s in doc.get("segments", []) if s.get("hash") and s.get("target")}


def save_doc(src, lang, doc):
    # Stamped here rather than by each caller, so a writer cannot forget it and
    # leave a file that reads as pre-record state.
    doc["state_version"] = STATE_VERSION
    dump_json(store_path(src, lang), doc)


def tracked(lang=None):
    # Version-independent, like `prior_targets` and for the same reason: `stats`
    # and the workbench's document list read counts and a source path, so a state
    # file waiting to be re-extracted should still appear rather than take the
    # whole listing down.
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
    # newline="\n" so the append log keeps LF on every platform. Without it text
    # mode writes CRLF on Windows, which contradicts the `*.jsonl text eol=lf`
    # rule in .gitattributes: git normalizes on commit, so the working file and
    # the committed file disagree, and the diff churn only becomes visible if
    # that rule is ever relaxed to -text.
    with open(tm_path(lang), "a", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)
