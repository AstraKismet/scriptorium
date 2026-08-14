"""On-disk state: per-document segment stores and the translation memory."""

import hashlib
import json
import os
import re
import sqlite3

from .config import DEFAULT_TONE, STATE, canonical_tone

#: Shape of a document state file. Bumped when a reader of an older file would be
#: wrong rather than merely incomplete. `__version__` cannot serve here: it moves
#: for unrelated reasons and a development build can move it backwards.
#:
#: 2 — slots became records (``original`` / ``role`` / ``pair_id`` /
#:     ``can_reorder``) instead of plain strings.
#: 3 — segments carry ``context`` and ``variant``, the two axes the translation
#:     memory key gained beside the content hash.
STATE_VERSION = 3

#: The shape of the *database* — its tables and columns — held in
#: ``PRAGMA user_version``. Distinct from :data:`STATE_VERSION`, and the two
#: answer different questions on purpose:
#:
#: * this one is what a build must be able to read at all. A newer schema holds
#:   columns this build has no statement for, so it is refused at the connection
#:   and no command runs. There is no per-document escape from that, because the
#:   refusal happens before any document has been named.
#: * ``STATE_VERSION`` is what a *document row* means. Versions 2 and 3 were both
#:   changes to the JSON inside a segment, which no schema could have caught, and
#:   the escape from one is still ``lx extract --reset`` on the one document.
#:
#: Collapsing them into one number was the alternative. It loses the escape
#: hatch: a whole-database refusal makes ``--reset`` unreachable, so a content
#: bump would force every document in the project to be re-extracted at once,
#: and the message that promises otherwise would become false.
SCHEMA_VERSION = 1

#: Seconds a writer waits for another process's write lock before giving up.
#: `lx web` and `lx run` in one directory is the case this exists for; WAL keeps
#: readers out of the way entirely, so what is being waited on is only the other
#: writer's transaction, and those are a few segments long.
BUSY_TIMEOUT = 5.0

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


def target_token(target):
    """What a client sends back to prove its edit was based on what it was shown.

    The hash of the stored target and nothing else. Derived rather than stored,
    so it costs no column and no :data:`SCHEMA_VERSION` — and derived *from the
    text* rather than from a revision counter on purpose: two writes that produce
    the same wording are not a lost update, and a counter would report one. A
    counter would also have to survive `lx apply`, which does not go through this
    surface at all.

    ``None`` and ``""`` are one value here, because they are one value to every
    reader of a target in this project — `checks.check_segment` reads
    ``seg.get("target") or ""`` and both counters test truthiness.
    """
    return seg_hash(target or "")


def doc_label(src):
    """The one spelling of a document's identity that every surface shows.

    :func:`doc_id` is what a state row is keyed on and is deliberately lossy;
    this is what a person and a client read, and its only difference from
    ``os.path.relpath`` is that the separator is ``/`` on every platform.

    Two spellings of one identity used to travel in one ``/api/state`` body —
    ``docs\\guide.md`` from `os.path.relpath` beside ``docs/guide.md`` from the
    candidate scan — and nothing compared them, which is what made the Windows
    defect in `docs/contracts/workbench-http.md` (13) possible. Fixing only the
    comparison would have left the condition and pushed a normalizer into every
    client, so the label is normalized where it is read and where it is written
    and there is one spelling from here down. See ``docs/decisions.md``,
    2026-08-14.

    Idempotent on a value it produced, which is what lets :func:`_meta` apply it
    to a row written before this existed instead of migrating one.
    """
    return os.path.relpath(src).replace(os.sep, "/")


def doc_id(src):
    return re.sub(r"[^A-Za-z0-9._-]", "_", doc_label(src))


def db_path():
    """The one working-state database for the project rooted at the cwd.

    One file, not one per document. `tracked` becomes a query instead of a
    directory walk, and the cross-document reads a status contract needs cost
    nothing. *Lost:* a database per document, which would have removed even the
    brief write contention between `lx run` on one document and the workbench on
    another. It was not worth three files per document in `.lx/` — a `.db` with
    its `-wal` and `-shm` sidecars — for a lock that is held for the length of
    one batch.
    """
    return os.path.join(STATE, "state.db")


def legacy_store_path(src, lang):
    """Where a build before the database kept this document's state.

    Kept only so the "no state" message can say what happened to someone whose
    `.lx/` predates the move. Nothing reads the file: it is regenerable and
    gitignored, which is what made the move free in the first place.
    """
    return os.path.join(STATE, "docs", f"{doc_id(src)}.{lang}.json")


def report_path(src, lang):
    return os.path.join(STATE, "reports", f"{doc_id(src)}.{lang}.json")


def tm_path(lang):
    return os.path.join(STATE, f"tm.{lang}.jsonl")


# ── the state database ─────────────────────────────────────────────────────
#
# Three tables, and the shape of them is the whole storage decision.
#
# `documents` holds one row per (document, language) carrying every
# document-level fact as JSON — `source`, `lang`, `tone`, `format`, `encoding`,
# `eol`, and whatever a parser reported about what it guessed. They are read
# together and never queried across, so promoting them to columns would buy
# nothing and cost a schema migration every time a format learns a new fact.
#
# `nodes` is the skeleton, one row per node, in `pos` order. The raw value lives
# in its own **BLOB** column rather than inside the JSON, and that column is
# invariant 2a's storage half: SQLite hands back exactly the bytes it was given,
# while a UTF-8 JSON file cannot hold a byte sequence that is not valid text at
# all (measured: `UnicodeEncodeError: surrogates not allowed`, which is what
# refuses an older Big5 or Shift-JIS novel today). Nothing writes bytes there
# yet — HANDOFF-208 is what changes the parsers — and the column takes either,
# because SQLite stores a `str` as TEXT and a `bytes` as BLOB in the same
# declared column and returns each unchanged.
#
# `segments` promotes the fields something other than the segment's own body
# needs to read: its id, the three that identify it for carryover, and the two a
# narrow write updates. Everything else stays JSON in `body`. There is no unique
# index on the identity, deliberately — a document may hold the same sentence
# twice, and uniqueness of a *memory entry* belongs to the memory file. Nor is
# any comparison made on those three in SQL, which is what keeps `NULL` from
# meaning something here that it does not mean in `tm_key`.
_SCHEMA = """
CREATE TABLE documents (
    doc_id        TEXT NOT NULL,
    lang          TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    meta          TEXT NOT NULL,
    PRIMARY KEY (doc_id, lang)
);
CREATE TABLE nodes (
    doc_id TEXT NOT NULL,
    lang   TEXT NOT NULL,
    pos    INTEGER NOT NULL,
    raw    BLOB,
    body   TEXT NOT NULL,
    PRIMARY KEY (doc_id, lang, pos)
);
CREATE TABLE segments (
    doc_id       TEXT NOT NULL,
    lang         TEXT NOT NULL,
    seg_id       TEXT NOT NULL,
    pos          INTEGER NOT NULL,
    content_hash TEXT,
    context      TEXT,
    variant      TEXT,
    status       TEXT,
    target       TEXT,
    body         TEXT NOT NULL,
    PRIMARY KEY (doc_id, lang, seg_id)
);
CREATE INDEX segments_carry ON segments (doc_id, lang, content_hash);
"""

#: One entry per step from schema version *n* to *n+1*. The first step is the
#: creation of a fresh database, which is why a new file (``user_version`` 0)
#: and an upgrade run through the same loop rather than through two code paths
#: that would have to be kept agreeing.
_MIGRATIONS = [lambda conn: conn.executescript(_SCHEMA)]


def _migrate(conn):
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found > SCHEMA_VERSION:
        raise StateVersionError(
            f"{db_path()} was written by a newer scriptorium: its schema is version "
            f"{found} and this build reads {SCHEMA_VERSION}. Upgrade scriptorium, or "
            f"delete {db_path()} and re-run `lx extract` — the state is rebuilt from "
            f"the sources and the translation memory, which that does not touch.")
    if found == SCHEMA_VERSION:
        return
    with conn:
        for step in range(found, SCHEMA_VERSION):
            _MIGRATIONS[step](conn)
        # Not a parameter: PRAGMA takes no placeholders. The value is a module
        # constant and never a caller's string.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _connect(create=True):
    """An open connection, or ``None`` when there is no database and none is wanted.

    Opened per call and closed by the caller rather than cached on the module.
    Every path here is relative to the process's working directory — `doc_id` is
    `os.path.relpath` by construction — and both the test suite and the workbench
    change it, so a cached handle would answer for whichever project happened to
    be current when it was first opened.
    """
    path = db_path()
    if not create and not os.path.exists(path):
        return None
    if create:
        os.makedirs(STATE, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT)
    # WAL, so a reader never blocks the writer and the workbench can render a
    # preview while `lx run` is committing a batch. It costs the two sidecar
    # files and rules out a `.lx/` on a network share, which is not a place
    # working state belongs. See `docs/decisions.md`, 2026-08-02.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        _migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _refuse_if_newer(found, src, lang):
    """Refuse a document row this build cannot represent. Returns its version.

    The two directions are not symmetrical and must not be handled in one place.
    An *older* row is readable in the sense that matters — extract rebuilds it —
    so only the readers that would misinterpret it refuse. A *newer* one holds
    fields this build does not know about, and a save replaces the whole
    document, so any path that could write over it has to stop first. That
    includes the extract path, which does not go through :func:`load_doc` at all.
    """
    if found > STATE_VERSION:
        raise StateVersionError(
            f"state for {src} [{lang}] is version {found}, newer than the {STATE_VERSION} "
            f"this build reads — upgrade scriptorium, or start over with "
            f"`lx extract {src} --lang {lang} --reset`, which discards the newer state "
            f"(anything in it and not in the translation memory is lost that way).")
    return found


def _no_state(src, lang):
    message = f"no state for {src} [{lang}] — run `lx extract {src} --lang {lang}` first"
    legacy = legacy_store_path(src, lang)
    if os.path.exists(legacy):
        # Not migrated, and deliberately so: `.lx/docs/` is regenerable and
        # gitignored, so re-extracting is both the cheaper answer and the one
        # that cannot half-succeed. Saying where it went is the whole debt.
        message += (f" (state now lives in {db_path()}; {legacy} was written by an "
                    f"older build and is no longer read — extract rebuilds it, and "
                    f"anything committed to the translation memory carries over)")
    raise FileNotFoundError(message)


# ── document rows ──────────────────────────────────────────────────────────

def _node_row(pos, node):
    # `v` is lifted out of the JSON and into the BLOB column; every other field
    # stays in `body`. A segment node has no `v` at all and stores NULL, which
    # is what tells the reader not to put the key back.
    body = {k: v for k, v in node.items() if k != "v"}
    return (pos, node.get("v"), json.dumps(body, ensure_ascii=False))


def _node(raw, body):
    node = json.loads(body)
    if raw is not None:
        node["v"] = raw
    return node


#: Segment fields that are columns rather than JSON. `hash`, `context` and
#: `variant` are what carryover looks a segment up by; `status` and `target` are
#: what a narrow write updates. Their names differ where SQL would rather they
#: did — `hash` is a function in SQLite and `id` invites confusion with a rowid.
_SEG_COLUMNS = (("seg_id", "id"), ("content_hash", "hash"), ("context", "context"),
                ("variant", "variant"), ("status", "status"), ("target", "target"))


def _seg_row(pos, seg):
    body = {k: v for k, v in seg.items() if k not in {f for _, f in _SEG_COLUMNS}}
    return (seg["id"], pos, seg.get("hash"), seg.get("context"), seg.get("variant"),
            seg.get("status"), seg.get("target"), json.dumps(body, ensure_ascii=False))


def _segment(row):
    seg = json.loads(row[-1])
    for value, (_, field) in zip(row, _SEG_COLUMNS):
        seg[field] = value
    # Derived on read as well as on write, and for the same reason `_meta`
    # re-normalizes `source`: the guard that keeps the two agreeing — an empty
    # target is refused at the door — binds every *future* write and does nothing
    # for a row already on disk. A document translated under a build that let an
    # empty target through carries `status="translated"` with `target=""`, which
    # `report.translated` and `docs[].done` both count as undone while
    # `pending_segments` never selects it again: the segment falls out of the
    # queue that would redo it, which is the whole of divergence (14). Recomputing
    # here closes it for the population the fix exists for, with no
    # `STATE_VERSION` bump, because nothing about the old row is unreadable —
    # only wrong. Found by the adversarial pass over the change that added the
    # write-side guard, which had made the neighbouring `source` fix self-healing
    # on read and this one not.
    seg["status"] = "translated" if (seg.get("target") or "").strip() else "pending"
    return seg


_SEG_READ = ("SELECT seg_id, content_hash, context, variant, status, target, body "
             "FROM segments WHERE doc_id=? AND lang=? ORDER BY pos")


def _meta(row_meta, state_version):
    doc = json.loads(row_meta)
    doc["state_version"] = state_version
    # Normalized on the way out as well as on the way in. `cli.do_extract` writes
    # the label through `doc_label`, so a row written by this build is already in
    # one spelling — but a row written before the normalization landed holds the
    # platform separator, and a project holding both would show two spellings of
    # one kind of value in one listing. `doc_label` is idempotent on its own
    # output, so this costs a string operation and no state migration: nothing
    # about an older row is *wrong*, which is `STATE_VERSION`'s own bar, only
    # spelled the old way. This is the single funnel from a stored row to a
    # dict — `_read_meta` and `tracked` are its only callers — so normalizing
    # here covers every reader, including ones added later.
    if doc.get("source"):
        doc["source"] = doc_label(doc["source"])
    return doc


def _read_meta(conn, src, lang):
    row = conn.execute("SELECT state_version, meta FROM documents WHERE doc_id=? AND lang=?",
                       (doc_id(src), lang)).fetchone()
    return None if row is None else _meta(row[1], row[0])


def load_doc(src, lang):
    conn = _connect(create=False)
    if conn is None:
        _no_state(src, lang)
    try:
        doc = _read_meta(conn, src, lang)
        if doc is None:
            _no_state(src, lang)
        if _refuse_if_newer(doc["state_version"], src, lang) < STATE_VERSION:
            raise StateVersionError(
                f"state for {src} [{lang}] is version {doc['state_version']}, this build "
                f"reads {STATE_VERSION} — run `lx extract {src} --lang {lang}` to rebuild "
                f"it. Translations already in the state are carried over by content hash, "
                f"so do not pass --reset.")
        did = doc_id(src)
        doc["nodes"] = [_node(raw, body) for raw, body in conn.execute(
            "SELECT raw, body FROM nodes WHERE doc_id=? AND lang=? ORDER BY pos", (did, lang))]
        doc["segments"] = [_segment(row) for row in conn.execute(_SEG_READ, (did, lang))]
        return doc
    finally:
        conn.close()


def prior_doc(src, lang):
    """The stored document-level facts, without its skeleton. ``{}`` if there is none.

    Deliberately not :func:`load_doc` for the *older* direction: this is the one
    reader that must work across a bump, because re-extracting is how stale state
    is migrated and carrying the translations over is the whole point of doing it
    that way. Only fields that no bump has changed are read.

    A row from a *newer* build is refused, because the caller is about to replace
    it. Reading it here and letting the write proceed was the first shape of this
    function, and it silently downgraded such a document — with a green exit code,
    while `lx check` on the same one refused to touch it.

    Split out of :func:`prior_targets` on 2026-07-29 so that extract could read
    the register from the same parse it read the translations from; since the
    move to SQLite it does not read the segments at all, which is the same saving
    reached properly — `prior_targets` is now a query over three columns rather
    than a walk over a whole book held in memory.
    """
    conn = _connect(create=False)
    if conn is None:
        return {}
    try:
        doc = _read_meta(conn, src, lang)
        if doc is None:
            return {}
        _refuse_if_newer(doc["state_version"], src, lang)
        return doc
    finally:
        conn.close()


def prior_targets(src, lang):
    """``{key: (target, origin)}`` for what this document already holds.

    The keys are :func:`tm_key`, not the content hash alone, because the
    collision the context axis removes is a within-document one first: a sentence
    that appears as a paragraph and as a blockquote used to carry over from one to
    the other. The segmentation version is this build's on both sides rather than
    the stored one, and that is not an oversight — that field guards the memory
    across time, while here the source has just been re-parsed by this build, so a
    changed segmentation has already changed the segment text and the content hash
    discriminates on its own. Keying on the stored version instead would make
    every bump silently discard the translations `lx extract` promises to carry.

    The register does **not** get that treatment, and the difference is the point:
    a changed segmentation changes the segment text, so the hash discriminates on
    its own, while a changed register leaves the source byte-identical. So these
    keys carry the *stored* register, extract looks them up under the new one, and
    a document re-extracted into another register carries nothing over. That is
    the intended result — the alternative keeps documentation wording in a
    document now labelled `literary`, and `lx commit` then banks all of it under
    the literary key, which poisons the memory permanently rather than costing
    one re-translation.

    Why the register is read here rather than passed in: it is the one argument a
    caller could get wrong in a way nothing would report, and both callers would
    be reading it out of the row this function is already opening.
    """
    conn = _connect(create=False)
    if conn is None:
        return {}
    try:
        meta = _read_meta(conn, src, lang)
        if meta is None:
            return {}
        tone = meta.get("tone")
        out = {}
        # `origin` stays inside `body`: it is written and read with the target it
        # describes and nothing looks a segment up by it, so promoting it would
        # be a column for one JSON parse per translated segment.
        for content_hash, context, variant, target, body in conn.execute(
                "SELECT content_hash, context, variant, target, body FROM segments "
                "WHERE doc_id=? AND lang=? AND target IS NOT NULL AND target != ''",
                (doc_id(src), lang)):
            if not content_hash:
                continue
            key = tm_key(content_hash, context, SEGMENTATION_VERSION, variant, tone)
            out[key] = (target, json.loads(body).get("origin") or "carryover")
        return out
    finally:
        conn.close()


def save_doc(src, lang, doc):
    """Replace a document's stored state entirely: meta, skeleton and segments.

    What `lx extract` does, and the only writer that touches the skeleton. Every
    other write is :func:`save_segments`, which is the reason a long translation
    no longer rewrites a whole book to record one batch.
    """
    # Stamped here rather than by each caller, so a writer cannot forget it and
    # leave state that reads as pre-record.
    doc["state_version"] = STATE_VERSION
    did = doc_id(src)
    meta = {k: v for k, v in doc.items() if k not in ("nodes", "segments", "state_version")}
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM nodes WHERE doc_id=? AND lang=?", (did, lang))
            conn.execute("DELETE FROM segments WHERE doc_id=? AND lang=?", (did, lang))
            conn.execute(
                "INSERT OR REPLACE INTO documents (doc_id, lang, state_version, meta) "
                "VALUES (?,?,?,?)",
                (did, lang, STATE_VERSION, json.dumps(meta, ensure_ascii=False)))
            conn.executemany(
                "INSERT INTO nodes (doc_id, lang, pos, raw, body) VALUES (?,?,?,?,?)",
                [(did, lang, *_node_row(i, n)) for i, n in enumerate(doc.get("nodes", []))])
            conn.executemany(
                "INSERT INTO segments (doc_id, lang, seg_id, pos, content_hash, context, "
                "variant, status, target, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(did, lang, *_seg_row(i, s)) for i, s in enumerate(doc.get("segments", []))])
    finally:
        conn.close()


def save_segments(src, lang, segments, expect=None):
    """Write these segments and nothing else. ``(written, stale)``.

    The narrow write, and the reason this package exists. `lx apply` and
    `lx check` touch the segments they changed instead of rewriting a novel's
    whole skeleton, and — the severe half — a translation run commits each batch
    as it lands, so a Ctrl-C or a dropped connection at 90% keeps the 90%.

    A segment whose id is not in the stored document is skipped rather than
    inserted: an id that was never extracted is a caller's mistake, and inserting
    it would put a segment in the document with no node referring to it.

    ``expect`` is ``{seg_id: previous_target}`` and makes the write a
    **compare-and-swap**: a named id is written only if the row still holds that
    exact text, and lands in ``stale`` otherwise. It is the only place the
    comparison is not racing. Checking a token against a snapshot read in an
    earlier transaction and then writing unconditionally — which is what
    :func:`cli.do_apply` did when the token was introduced — is not a check at
    all: two writers whose reads both land before either write both pass it, and
    the loser is told it succeeded while its text is discarded. Reproduced
    2026-08-14 with two threads under `ThreadingHTTPServer`, which is what `lx web`
    runs; ``UPDATE … WHERE … AND target IS ?`` closes it, and exactly one of the
    two sees a rowcount of 1.

    ``IS`` rather than ``=``, because a never-translated segment holds SQL NULL
    and an explicitly written one holds ``''``. ``=`` is never true against NULL,
    so the first write to every fresh segment would have been refused as stale.
    """
    rows = [(*_seg_row(0, seg)[2:], doc_id(src), lang, seg["id"]) for seg in segments]
    if not rows:
        return 0, []
    expect = expect or {}
    conn = _connect()
    try:
        with conn:
            written, stale = 0, []
            for row, seg in zip(rows, segments):
                seg_id = seg["id"]
                if seg_id in expect:
                    n = conn.execute(
                        "UPDATE segments SET content_hash=?, context=?, variant=?, status=?, "
                        "target=?, body=? WHERE doc_id=? AND lang=? AND seg_id=? "
                        "AND target IS ?", (*row, expect[seg_id])).rowcount
                    if not n:
                        stale.append(seg_id)
                else:
                    n = conn.execute(
                        "UPDATE segments SET content_hash=?, context=?, variant=?, status=?, "
                        "target=?, body=? WHERE doc_id=? AND lang=? AND seg_id=?", row).rowcount
                written += n
        return written, stale
    finally:
        conn.close()


def save_targets(src, lang, targets, origin):
    """Record translated text for these ids, reading nothing first.

    What a translation run calls per batch. It goes through the row rather than
    through a loaded document on purpose: the point is that a batch is durable
    the moment it lands, and a read-modify-write of the whole document would put
    the interrupt window back where it was — and, with the workbench editing the
    same document, would silently overwrite whatever it had saved meanwhile.

    Issues are cleared with the target for the same reason :func:`cli.do_apply`
    clears them: they describe wording that has just been replaced.

    ``status`` is derived from the text rather than hardcoded to ``translated``.
    Unreachable with an empty text today — every caller feeds this
    `translate.accept`'s output, which refuses one — and written this way anyway,
    because `status` is the *draft queue's selection predicate* and a writer that
    can mark an empty segment done is the shape of the defect, not the instance.
    The instance is closed at the door by :func:`cli.do_apply`.
    """
    conn = _connect()
    try:
        with conn:
            written = 0
            for seg_id, text in targets.items():
                row = conn.execute(
                    "SELECT body FROM segments WHERE doc_id=? AND lang=? AND seg_id=?",
                    (doc_id(src), lang, seg_id)).fetchone()
                if row is None:
                    continue
                body = json.loads(row[0])
                body["origin"] = origin
                body.pop("issues", None)
                written += conn.execute(
                    "UPDATE segments SET status=?, target=?, body=? "
                    "WHERE doc_id=? AND lang=? AND seg_id=?",
                    ("translated" if (text or "").strip() else "pending",
                     text, json.dumps(body, ensure_ascii=False),
                     doc_id(src), lang, seg_id)).rowcount
        return written
    finally:
        conn.close()


def tracked(lang=None):
    # Version-independent, like `prior_doc` and for the same reason: `stats` and
    # the workbench's document list read counts and a source path, so a document
    # waiting to be re-extracted should still appear rather than take the whole
    # listing down.
    #
    # No `nodes`, deliberately. Both callers count segments and read a source
    # path, and loading every skeleton in the project to answer "how far along is
    # each document" is the shape of read this move exists to stop. A caller that
    # needs a skeleton is asking about one document and calls `load_doc`.
    conn = _connect(create=False)
    if conn is None:
        return []
    try:
        out = []
        for did, dlang, version, meta in conn.execute(
                "SELECT doc_id, lang, state_version, meta FROM documents ORDER BY doc_id, lang"):
            if lang and dlang != lang:
                continue
            doc = _meta(meta, version)
            doc["segments"] = [_segment(row) for row in conn.execute(_SEG_READ, (did, dlang))]
            out.append(doc)
        return out
    finally:
        conn.close()


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
