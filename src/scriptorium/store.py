"""On-disk state: per-document segment stores and the translation memory."""

import hashlib
import json
import os
import re

from .config import DEFAULT_TONE, STATE, canonical_tone, dump_json, load_json

#: Shape of a document state file. Bumped when a reader of an older file would be
#: wrong rather than merely incomplete. `__version__` cannot serve here: it moves
#: for unrelated reasons and a development build can move it backwards.
#:
#: 2 — slots became records (``original`` / ``role`` / ``pair_id`` /
#:     ``can_reorder``) instead of plain strings.
#: 3 — segments carry ``context`` and ``variant``, the two axes the translation
#:     memory key gained beside the content hash.
STATE_VERSION = 3

#: How the parsers cut a document into segments. Bumped when a change to that
#: decision changes segment text — rewrapping a list continuation, merging two
#: paragraphs, splitting on a different boundary.
#:
#: It prevents nothing. Every such change invalidates every entry in the memory
#: by changing the text that was hashed, and no field can stop that; what this one
#: buys is that the invalidation is **detectable** rather than silent, because a
#: record written under an older segmentation stops answering lookups instead of
#: answering them with wording cut for a different sentence. A record with no such
#: field predates the field and is version 0 — see :func:`tm_lookup`.
SEGMENTATION_VERSION = 1


class StateVersionError(RuntimeError):
    """A state file this build cannot read. The message names the way out."""


def seg_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ── the translation-memory key ─────────────────────────────────────────────

def key_tone(tone):
    """The register as the key sees it, where the default register is the null.

    Absent, null, and the default register are one value. That is what keeps
    every entry banked before registers existed answering a document in the
    default register — and for everything a *model* produced it is true as well
    as convenient, because the build that wrote it ended the brief with "Write
    technical documentation register" whatever `tone` had been typed. The
    exception is wording that arrived through `lx apply`, which is a person's or
    an agent's own words and was never briefed by anything; such an entry sits in
    this tier because nothing recorded its register, not because it is known to
    be in this one.

    *Lost:* keying on the register always and adding a second, register-blind
    lookup for the old tier, the way :func:`tm_lookup` already does for
    ``segmentation_version``. It costs a lookup, and — decisively — it lets a
    documentation-era wording be claimed by a novel, which is the one failure
    this axis was added to prevent. See ``docs/decisions.md``, 2026-07-29.
    """
    register = canonical_tone(tone)
    return None if register == DEFAULT_TONE else register


def tm_key(content_hash, context=None, segmentation_version=SEGMENTATION_VERSION,
           variant=None, tone=None):
    """What identifies a translation: its content, its place, its cut, its form,
    and the register it was written in.

    A tuple, not a digest over a canonical serialization. The hard requirement is
    that ``variant=None`` be indistinguishable from the field's absence — getting
    that wrong invalidates the entire memory the moment it lands — and a tuple
    makes it true by construction, since ``dict.get`` yields ``None`` for both,
    rather than a canonicalization rule someone has to keep correct. *Lost:* an
    opaque digest, which would also hand a future SQLite schema one indexable
    column. Nothing on disk holds the key — the memory file holds the fields it is
    built from — so the representation is free to stay readable in a traceback.

    ``tone`` is the one field that cannot hold by construction, because its null
    is a *string* the caller is holding — ``"technical"`` has to compare equal to
    absent, and no amount of ``dict.get`` makes it. So the collapse runs here,
    inside the one function no caller can route around, rather than at the four
    call sites where it would be a rule someone has to keep correct. The other
    three fields are still passed through raw.

    ``context`` is gettext's ``msgctxt``: what lets one source string carry
    different translations in different places. Markdown sets it to the segment
    kind, so a sentence appearing as a paragraph and as a blockquote is two
    entries rather than one. Measured 2026-07-28: both hashed ``649729361f3c``,
    and a paragraph translation wrapped across two lines, carried onto the
    blockquote by a memory hit, put its second line outside the quote.

    Deliberately *not* in the key: anything derived from the mask configuration.
    Reuse is gated by ``translate.accept`` instead — see ``docs/decisions.md``,
    2026-07-29.
    """
    return (content_hash, context, segmentation_version, variant, key_tone(tone))


def segment_key(seg, tone=None):
    """The key for a segment this build just parsed, so the cut is this build's.

    ``tone`` is threaded in rather than read off the segment: the register is a
    document-level fact, and a document-level fact does not belong inside a
    segment — the same rule ``doc["eol"]`` follows, and for the same reason.
    Copying it onto every segment would also be a state-file schema change, so it
    would cost a ``STATE_VERSION`` bump and a migration, for a duplicate.
    """
    return tm_key(seg["hash"], seg.get("context"), SEGMENTATION_VERSION,
                  seg.get("variant"), tone)


def record_key(rec):
    """The key for a line of the memory, which may have been written long ago.

    A field that is null and a field that is absent mean the same thing in both
    directions: this reader collapses them, and :func:`tm_record` never writes a
    null. That is the one rule the whole memory rests on. ``tone`` extends it by
    one step — the default register collapses too, in :func:`key_tone` — so a
    line another tool wrote as ``"tone": "technical"`` is the same entry as a
    line with no ``tone`` at all.
    """
    version = rec.get("segmentation_version")
    return tm_key(rec["hash"], rec.get("context"),
                  0 if version is None else version, rec.get("variant"), rec.get("tone"))


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


def prior_doc(src, lang):
    """The stored state, read the way extract has to read it. ``{}`` if there is none.

    Deliberately not :func:`load_doc` for the *older* direction: this is the one
    reader that must work across a bump, because re-extracting is how a stale
    file is migrated and carrying the translations over is the whole point of
    doing it that way. Only fields that no bump has changed are read.

    A file from a *newer* build is refused, because the caller is about to
    replace it. Reading it here and letting the write proceed was the first
    shape of this function, and it silently downgraded such a file — with a green
    exit code, while `lx check` on the same file refused to touch it.

    Split out of :func:`prior_targets` on 2026-07-29 so that extract can read the
    document's register from the same parse it reads its translations from. The
    alternative was a second full read of a file that is a whole book.
    """
    p = store_path(src, lang)
    if not os.path.exists(p):
        return {}
    doc = load_json(p, {})
    _refuse_if_newer(doc, src, lang)
    return doc


def prior_targets(doc):
    """``{key: (target, origin)}`` from a state file :func:`prior_doc` has read.

    The keys are :func:`segment_key`, not the content hash alone, because the
    collision the context axis removes is a within-document one first: a sentence
    that appears as a paragraph and as a blockquote used to carry over from one to
    the other. The segmentation version is this build's on both sides rather than
    the file's, and that is not an oversight — this field guards the memory across
    time, while here the source has just been re-parsed by this build, so a
    changed segmentation has already changed the segment text and the content hash
    discriminates on its own. Keying on the file's version instead would make
    every bump silently discard the translations `lx extract` promises to carry.

    The register does **not** get that treatment, and the difference is the point:
    a changed segmentation changes the segment text, so the hash discriminates on
    its own, while a changed register leaves the source byte-identical. So these
    keys carry the *file's* register, extract looks them up under the new one, and
    a document re-extracted into another register carries nothing over. That is
    the intended result — the alternative keeps documentation wording in a
    document now labelled `literary`, and `lx commit` then banks all of it under
    the literary key, which poisons the memory permanently rather than costing
    one re-translation.
    """
    out = {}
    tone = doc.get("tone")
    for s in doc.get("segments", []):
        if not (s.get("hash") and s.get("target")):
            continue
        # A file older than version 3 has no `context`. Every build that could
        # have written one produced Markdown, where the context *is* the kind, so
        # reading `kind` is the migration rule rather than a guess. Tested for
        # presence, not truth: a format whose context is legitimately null must
        # not silently acquire the kind instead.
        ctx = s["context"] if "context" in s else s.get("kind")
        key = tm_key(s["hash"], ctx, SEGMENTATION_VERSION, s.get("variant"), tone)
        out[key] = (s["target"], s.get("origin") or "carryover")
    return out


def save_doc(src, lang, doc):
    # Stamped here rather than by each caller, so a writer cannot forget it and
    # leave a file that reads as pre-record state.
    doc["state_version"] = STATE_VERSION
    dump_json(store_path(src, lang), doc)


def tracked(lang=None):
    # Version-independent, like `prior_doc` and for the same reason: `stats`
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
    """``{key: target}``. Last write wins, so a correction supersedes its original.

    A line that is not an object with a hash and a target is skipped rather than
    raised on. The file is append-only and hand-editable by design, and one bad
    line taking down every command that reads the memory is a poor trade for a
    diagnostic nobody asked for.
    """
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
                    if not isinstance(rec, dict) or not rec.get("hash") or not rec.get("target"):
                        continue
                    tm[record_key(rec)] = rec["target"]
    return tm


def tm_lookup(tm, seg, tone=None):
    """``(target, origin)`` for a segment, or ``(None, None)``.

    The exact key first. A record carrying neither a context nor a segmentation
    version predates both, and is then tried on content alone — that is every
    entry in every memory written before this key existed, and refusing them would
    empty a user's memory on upgrade for nothing. Accepting them is safe in a way
    it would not have been a week ago: the hit goes through ``translate.accept``
    like any other, and `lx commit` rewrites the entry under the full key the
    first time that wording is banked again, so the legacy tier drains rather than
    lingers.

    It is marked ``tm:legacy`` and not ``tm``, because a match on content alone is
    exactly the context-blind reuse this key was changed to stop, and a reviewer
    should be able to see which reuses still rest on it.

    A segment carrying a variant is not offered the fallback. A record written
    before variants existed cannot be known to be the right form, and guessing
    there is how a plural becomes a singular in a place nobody looks.

    A segment in a non-default register is not offered it either, for the same
    reason one step along: nothing in that tier records a register, and what a
    model put there was briefed as documentation whatever `tone` said, so handing
    it to a novel is a guess with the odds against it rather than one that might
    be right. A document in the default register still gets the tier in full,
    which is what keeps the upgrade free.
    """
    exact = tm.get(segment_key(seg, tone))
    if exact is not None:
        return exact, "tm"
    if seg.get("variant") is None and key_tone(tone) is None:
        legacy = tm.get(tm_key(seg["hash"], None, 0, None, None))
        if legacy is not None:
            return legacy, "tm:legacy"
    return None, None


def tm_record(seg, tone=None):
    """The memory line for a translated segment. A null field is not written.

    Omitting a null is the same rule :func:`record_key` reads by — absent and null
    mean one thing — and it keeps the file legible, which is why the memory is
    JSONL and in version control at all. The default register is a null here by
    :func:`key_tone`, so a documentation project's memory file is byte-for-byte
    the file it was before registers existed.
    """
    rec = {"hash": seg["hash"]}
    if seg.get("context") is not None:
        rec["context"] = seg["context"]
    rec["segmentation_version"] = SEGMENTATION_VERSION
    if seg.get("variant") is not None:
        rec["variant"] = seg["variant"]
    if key_tone(tone) is not None:
        rec["tone"] = key_tone(tone)
    rec["source"] = seg["source"]
    rec["target"] = seg["target"]
    return rec


def tm_records(doc, tm):
    """Lines for the segments whose wording the memory does not already hold.

    One builder for `lx commit` and for the workbench's commit endpoint, because
    two of them is how two surfaces come to disagree about what a record is.

    A segment reused from the legacy tier is written again here, under the full
    key: the comparison is against the exact key, which such a segment misses.
    That is the upgrade path, not a duplicate — the second commit finds the
    versioned record and skips it.

    The register is read off the document here rather than passed in, because
    both callers already hold the document and neither should have to know that
    the key grew a field. It is the only place a key is built from stored state
    whose register is the one that produced the wording.
    """
    out = []
    tone = doc.get("tone")
    for seg in doc["segments"]:
        if not seg.get("target"):
            continue
        if tm.get(segment_key(seg, tone)) == seg["target"]:
            continue
        out.append(tm_record(seg, tone))
    return out


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
