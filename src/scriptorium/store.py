"""On-disk state: per-document segment stores and the translation memory."""

import difflib
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter

from .config import DEFAULT_TONE, STATE, canonical_tone
from .mask import placeholder_ids, target_map, unmask

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
SCHEMA_VERSION = 2

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
#:
#: The consequence, once there is more than one step: ``_SCHEMA`` above is the
#: shape of a version **1** database and is never edited again. A new column is
#: a new entry here, and a fresh file gets it by running the same ALTER an
#: existing one does. Editing ``_SCHEMA`` instead makes the fresh path and the
#: upgrade path disagree — measured 2026-09-10, a new database then fails with
#: ``duplicate column name`` on its own creation.
_MIGRATIONS = [
    lambda conn: conn.executescript(_SCHEMA),
    # 1 -> 2: the document's own bytes, beside the encoding that reads them.
    # Additive and nullable on purpose. An existing row keeps every field it
    # had and simply has no byte answer until the next `lx extract` fills one
    # in, so this costs no re-extract and no `STATE_VERSION` bump: nothing
    # about an older row is *wrong*, which is that constant's own bar.
    lambda conn: conn.execute("ALTER TABLE documents ADD COLUMN source BLOB"),
]


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
        # `--tone` is named because since 2026-08-19 the command without it is
        # refused, and this message is the *only* route out of a row this build
        # will not read — a sentence naming a command that exits 2 is a green
        # suite over a false user-facing string. It says "name" rather than
        # offering a value because the register that was frozen is inside the row
        # just refused: nothing here can read it, so the person has to decide.
        raise StateVersionError(
            f"state for {src} [{lang}] is version {found}, newer than the {STATE_VERSION} "
            f"this build reads — upgrade scriptorium, or start over with "
            f"`lx extract {src} --lang {lang} --reset --tone <technical|literary>`, which "
            f"discards the newer state (anything in it and not in the translation memory "
            f"is lost that way). The register has to be named because the reset does not "
            f"read the row it would have come from.")
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
    # The waiver is a boolean everywhere outside this module and a token inside
    # it, and this is the one place the two meet on the way in — :func:`_segment`
    # is the one place they meet on the way out. A caller hands back the segment
    # it was given, so it hands back `waived: True`; stored as-is that would be a
    # flag with nothing tying it to the wording, which is the whole guarantee.
    # Encoded here rather than asked of every writer: `cli.do_extract` carries a
    # waiver across a re-parse and `cli.do_apply` carries it across a save, and
    # neither should have to know how it is spelled on disk.
    if "waived" in body:
        body["waived"] = target_token(seg.get("target")) if body["waived"] else None
        if body["waived"] is None:
            del body["waived"]
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
    # **A waiver is only in force over the wording it was granted on**, and that
    # is decided here rather than trusted from the row. The two writers drop the
    # key when the target moves, which is enough while every write goes through
    # this build — and is not enough across builds: a build without the field
    # writes a new target and leaves the flag, and a stale waiver is *fail-open*
    # where a stale hold is fail-safe. It downgrades error-severity findings and
    # moves the exit code invariant 10 rests on, so it cannot be left to a writer
    # that may not exist. Stored as the token of the target it was granted over,
    # compared here, and simply not surfaced when the two disagree.
    #
    # Recomputed on read, exactly as `status` is one line up and for the same
    # reason: a guard that binds only future writes does nothing for a row
    # already on disk. `True` rather than the token, because no reader outside
    # this module has any use for the token and one of them ships it on the wire.
    if seg.pop("waived", None) == target_token(seg.get("target")):
        seg["waived"] = True
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


#: How much work `Carryover.align` may spend aligning two key sequences, as
#: ``len(prior) × the commonest key's count``. `SequenceMatcher` is near-linear
#: on sequences whose elements are mostly distinct and quadratic on ones that are
#: not, and a document is allowed to be pathological: measured 2026-08-17 on this
#: machine, five thousand *byte-identical* paragraphs take 2.0 s and twelve
#: thousand take 14.2 s, while a realistic five-thousand-segment novel with six
#: lines of dialogue repeated six hundred times takes 8 ms and the same novel with
#: a third of it repeated takes 80 ms. The budget sits between them. Over it the
#: alignment is skipped and every segment resolves the way it did before
#: 2026-08-17, which is a worse answer rather than no answer.
ALIGN_BUDGET = 8_000_000


def _slot_map(value):
    """A stored ``slots`` value, if it is the record map, else ``None``.

    All or nothing. A state file written before slots became records holds
    ``{id: "original"}`` — `lx extract` is what migrates such a file, so reading
    one must not raise, and half a map is worse than none: a re-seat that trusted
    the entries it understood would place some placeholders and silently drop the
    rest. ``None`` means "no provenance", which is exactly what an untyped map
    is.
    """
    if not isinstance(value, dict) or not value:
        return None
    if all(isinstance(v, dict) and "original" in v for v in value.values()):
        return value
    return None


def slot_originals(slots):
    """A slot map as the array a memory line carries, or ``None``.

    ``mask.mask`` numbers from 1 with a single counter, so the ids of a segment
    are contiguous and their order is the whole of the information — which makes
    the array both the smallest spelling and the one that reads in a diff, and
    the memory file is version-controlled precisely so that it can be read.
    ``role`` / ``pair_id`` / ``can_reorder`` are not carried: they are
    re-derivable by masking the same source, and what a reuse needs from a line
    is only what each placeholder stood for.

    ``None`` when the map is not contiguous from 1 — nothing this build writes
    can be, and a line another tool wrote is not something to guess about.
    """
    if not slots:
        return None
    try:
        ids = sorted(slots, key=int)
    except (TypeError, ValueError):
        return None
    if [int(i) for i in ids] != list(range(1, len(ids) + 1)):
        return None
    return [slots[i]["original"] for i in ids]


def slot_map(originals):
    """The inverse of :func:`slot_originals`: an array back into a slot map.

    Only ``original`` is restored, which is all a re-seat reads. A line whose
    ``slots`` is not a list of strings is ignored rather than raised on, on the
    same footing as :func:`load_tm`'s skip rule — the file is hand-editable by
    design.
    """
    if not isinstance(originals, list) or not originals:
        return None
    if not all(isinstance(o, str) for o in originals):
        return None
    return {str(i): {"original": o, "role": "standalone",
                     "pair_id": None, "can_reorder": True}
            for i, o in enumerate(originals, 1)}


def _run_positions(keys):
    """``(offset within the run, run length)`` for every position, in one pass.

    The two halves of what :meth:`Carryover.align` calls established, as one
    comparable value. A key that occurs once anywhere answers ``(0, 1)`` on both
    sides, so the guard is inert by construction on every segment that was never
    contested rather than by a branch someone has to keep correct.
    """
    out, n = [None] * len(keys), 0
    while n < len(keys):
        m = n
        while m < len(keys) and keys[m] == keys[n]:
            m += 1
        for x in range(n, m):
            out[x] = (x - n, m - n)
        n = m
    return out


class Carryover:
    """What a document already holds, and which entry a re-parsed segment inherits.

    The prior document as two parallel lists — every segment's :func:`tm_key` in
    document order, and the entry it holds, ``None`` where it holds nothing —
    plus the translated entries grouped by key, which is what answers when the
    lists cannot.

    **Untranslated segments are in the sequence on purpose.** They occupy
    positions, and the first version of this read only rows with a target: a
    document with four identical paragraphs of which three were translated then
    had its ordinals counted over three rows on one side and four segments on the
    other, and a paragraph nobody had ever translated came back holding somebody
    else's wording, `status: translated`, out of the draft queue for good.

    The map this replaced held **one entry per key**, so a document containing one
    sentence twice held one entry for two positions and the last row read won: a
    person's wording at one position was replaced by the model's draft from the
    other **carrying its `origin`**, which is what made origin precedence evadable
    with no race and no second process.
    `docs/contracts/workbench-http.md` divergence (25).

    :meth:`align` is where the answer is decided, for the document as a whole
    rather than a segment at a time, because two positions holding the same
    sentence can only be told apart by looking at what is around them.
    """

    def __init__(self, keys, entries, by_key):
        #: Every prior segment's key, in document order.
        self.keys = keys
        #: Parallel to :attr:`keys`: an entry, or ``None`` where that segment
        #: held no translation.
        self.entries = entries
        #: ``{key: [entry, ...]}`` — the *translated* entries under a key, in
        #: document order. The old rule's whole world, and since 2026-09-08 the
        #: answer only for a fresh segment the diff paired with nothing at all.
        #: An entry is ``(target, origin, review, waived, slots)``, where
        #: ``slots`` is the map the target's placeholders were written against
        #: and ``waived`` is whether a reviewer had answered that wording's
        #: report.
        self.by_key = by_key

    def __len__(self):
        """How many translations this document holds — not how many segments."""
        return sum(len(rows) for rows in self.by_key.values())

    def align(self, segments, tone):
        """``{seg_id: (entry, ambiguous)}`` — what each freshly parsed segment inherits.

        ``entry`` is ``(target, origin, review, waived, slots)`` or ``None``.

        **The two key sequences are diffed, and the matching blocks are the
        answer.** Nothing else establishes which of two identical paragraphs is
        which: an id is worthless the moment an insertion shifts it, an ordinal
        within the key's own class survives an insertion outside the class and
        slides by one the moment a member is added or removed inside it, and both
        were measured wrong — the ordinal rule on a delete, where it laundered a
        machine draft into `human`, and the id rule on the insertion it was
        written for. A diff gets both right, because the unique prose on either
        side of a repeated line anchors it. `difflib` is the standard library and
        pure Python, so invariant 1 permits it; ``autojunk=False`` is not
        optional, since the default discards any element occurring in more than
        1% of a sequence longer than 200 — every repeated line of dialogue in a
        novel.

        **What is not established is still answered, and the answer is the best
        one available rather than a worse one.** A pair the guard refuses keeps
        the wording the diff paired it with; only a fresh segment the diff matched
        to nothing at all — text that moved, a *new* occurrence of a sentence the
        document already had — falls back to the last translated entry under its
        key, which is the rule that carried everything before 2026-08-17. Neither
        carries a hold or a waiver. Both are one reviewer's statement about a
        position, and these are the branches that could not establish one;
        carrying a hold in would take a paragraph nobody has looked at out of
        every queue, which is how a run of new dialogue came back `held` and
        rendered into the book.

        **That split is the whole of what refusing costs, and it was measured
        before it was chosen.** Refusing *into* the key fallback hands every
        member of a run the same entry, so a run of five distinct wordings comes
        back as one wording five times and the other four are held by no segment
        at all. Over 622 single-edit shapes at a chapter's density of repeated
        lines: 52 stored wordings delivered to nobody before, 346 after, with
        total wrong deliveries up from 320 to 512 and one entry duplicated onto
        another position 204 times against 498. Keeping the diff's own pair
        leaves all three at the number this build already had — 52, 320 and 204 —
        and takes the silent misplacements to 0. The guard decides whether an
        answer is presented as established; it does not decide what the answer
        is. `docs/decisions.md`, 2026-09-08.

        **A pair with no anchor is not evidence, and the pair is the unit.**
        Where a matching block reaches into a run of one key, the diff took the
        first offset that fitted; if that run also changed size, one of its
        members was added or removed and the offset is a coin toss. So a pair is
        *established* only when the run of equal keys it sits in is the same
        length on both sides and the pair sits at the same offset inside it. A run
        whose size did not change is placed, which is what carries forty identical
        paragraphs across an insertion.

        **The block was the wrong unit and the document was the wrong scope**, and
        the sentence above was true of neither until 2026-09-08. The scope: the
        test compared a ``Counter`` over the whole document, so a key whose two
        runs changed size in opposite directions left the tally equal and neither
        was refused. The unit: it only ran where *every* element of the block
        carried one key, which a block spanning an anchor never does — on
        ``A C C B C C C E`` against ``A C C C B C C E`` the diff returns one block
        of ``C C B C C``, so nothing was asked and four positions were presented
        as established while each sat a member out of step. Being next to a
        matched anchor is not evidence when the paragraphs between you and the
        anchor are identical to each other. Together the two made the guard inert
        on any document with unique prose in it: over 622 single-edit shapes at a
        chapter's density of repeated lines it refused nothing, and scored equal
        to having no guard at all in every column. One tuple comparison replaces
        both, and its offset half is not decoration — the length alone leaves
        89634 silent misplacements over the exhaustive corpus where the pair
        leaves 88380, though on the novel-shaped corpora the two are equal to the
        case.

        ``ambiguous`` is then simply "the diff could not place this and something
        was carried anyway": a new occurrence of a sentence the document already
        had, a lone paragraph that moved, or a member of a run nothing could tell
        apart. `lx extract` names them.
        """
        return {sid: (entry, ambiguous)
                for sid, (entry, ambiguous, _guessed) in self.answers(segments, tone).items()}

    def answers(self, segments, tone):
        """:meth:`align`, and for each answer whether it is a *guess*.

        ``{seg_id: (entry, ambiguous, guessed)}``. ``guessed`` is true where the
        diff paired the fresh segment with nothing — or with a prior row holding
        no translation, at a position it could not establish — so the entry is
        the last translated one under that key: another position's wording,
        handed back because it is the best a document alone has. A pair the diff
        made and could not establish is ``ambiguous`` without being ``guessed``,
        because the wording there was held by *a* member of this run.

        Split out for `lx extract --from`, which holds two documents' answers for
        one position and has to know which of them is only a guess: measured on
        2026-09-10, a chapter whose run of identical lines grew answered the new
        member with its own last copy, a duplicate, over the named document's
        placed answer for that exact position. :meth:`align` is the projection
        every other caller reads, so none of them changed.
        """
        fresh = [(seg["id"], segment_key(seg, tone)) for seg in segments]
        keys = [key for _, key in fresh]
        prior_runs, fresh_runs = _run_positions(self.keys), _run_positions(keys)

        placed, paired = {}, {}
        for i, j, size in self._blocks(keys):
            # Asked of every pair the diff made rather than of the block it
            # arrived in: a block that spans an anchor is not homogeneous, so a
            # test on the block never reaches the run at either end of it. The
            # sentinel block needs no branch of its own — `range(0)` is empty.
            for d in range(size):
                sid, entry = fresh[j + d][0], self.entries[i + d]
                # Kept even where the guard refuses the pair, because it is still
                # the best answer anyone has for this position. A prior row that
                # holds no translation is not an answer, and needs no branch to
                # say so: it is stored as `None`, which the reader below already
                # has to treat as "the diff paired this with nothing" for the
                # segments no block reached.
                paired[sid] = entry
                if prior_runs[i + d] == fresh_runs[j + d]:
                    placed[sid] = entry

        out = {}
        for sid, key in fresh:
            if sid in placed:
                out[sid] = (placed[sid], False, False)
                continue
            row, guessed = paired.get(sid), False
            if row is None:
                rows = self.by_key.get(key)
                row, guessed = (rows[-1], True) if rows else (None, False)
            # Neither `review` nor the waiver survives either branch, and for
            # one reason: both are a reviewer's statement about a *position*, and
            # neither branch could establish one. Carrying a hold in took a
            # paragraph nobody had looked at out of every queue; carrying a
            # waiver in would go one worse and answer the report on a paragraph
            # nobody had read, which is the one thing a waiver must never do by
            # itself. The wording, the `origin` and the provenance map do travel,
            # because they describe the wording rather than the position — and
            # deleting them to avoid mislabelling them is the trade 2026-08-17
            # refused everywhere else.
            entry = (row[0], row[1], None, False, row[4]) if row else None
            out[sid] = (entry, entry is not None, guessed)
        return out

    def _blocks(self, keys):
        """The matching blocks, or none at all when the diff would cost too much.

        The budget is the only thing standing between `lx extract` and a
        quadratic afternoon on a document that is one sentence repeated ten
        thousand times. Over it, every segment falls to the key fallback — which
        is exactly what this build did before the diff existed, so the answer
        degrades rather than disappearing.
        """
        if not self.keys or not keys:
            return []
        commonest = max(Counter(keys).values())
        if len(self.keys) * commonest > ALIGN_BUDGET:
            return []
        return difflib.SequenceMatcher(None, self.keys, keys,
                                       autojunk=False).get_matching_blocks()


def no_carryover():
    """An empty :class:`Carryover`, for the paths that read no prior state.

    A function rather than a module-level constant: a shared empty singleton is
    the kind of thing that acquires an entry once and is very hard to find again.
    """
    return Carryover([], [], {})


def prior_targets(src, lang):
    """A :class:`Carryover` over what this document already holds.

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

    ``review`` travels with the target because a hold is about *this wording*,
    not about a position in the file. Carrying it here rather than in
    `cli.do_extract` is what makes a hold survive a re-extract — before
    2026-08-15 it did not, so `lx run`, whose first statement is `do_extract`,
    lifted every hold in the document before it did anything else and said
    nothing. A hold whose target the acceptance path refuses rides with it all
    the same since 2026-08-17: the wording is kept rather than deleted, so there
    is something left to hold. It is dropped when another proposal took the
    segment, because the wording it was placed on is then gone, and by the
    fallback in :meth:`Carryover.align`, which could not establish a position.

    **Every segment is read, translated or not.** The `WHERE target != ''` this
    used to carry looked like a free filter and was not: the alignment counts
    positions, and a filtered read counts them in one document and not the other.
    The untranslated ones arrive as ``None`` entries and are filtered where it is
    free — out of ``by_key``, which is the only structure that answers by content.
    """
    conn = _connect(create=False)
    if conn is None:
        return no_carryover()
    try:
        meta = _read_meta(conn, src, lang)
        if meta is None:
            return no_carryover()
        tone = meta.get("tone")
        keys, entries, by_key = [], [], {}
        # `ORDER BY pos` because the order *is* the answer now: this list is one
        # side of a diff. It was rowid order in practice and never stated, and
        # even "the last row wins" had rested on that.
        #
        # `origin` stays inside `body`: it is written and read with the target it
        # describes and nothing looks a segment up by it, so promoting it would
        # be a column for one JSON parse per translated segment.
        for content_hash, context, variant, target, body in conn.execute(
                "SELECT content_hash, context, variant, target, body FROM segments "
                "WHERE doc_id=? AND lang=? ORDER BY pos",
                (doc_id(src), lang)):
            # A row with no content hash cannot be keyed and cannot match, but it
            # still occupied a position: `None` keeps the sequence honest, and no
            # freshly parsed key is ever `None`, so it can only ever read as a
            # deletion.
            key = (tm_key(content_hash, context, SEGMENTATION_VERSION, variant, tone)
                   if content_hash else None)
            entry = None
            if key is not None and target:
                held = json.loads(body)
                # The last field is the map this *target* was written against,
                # which is not the segment's own `slots` whenever a re-parse has
                # moved under it: `save_doc` rewrites `slots` from the fresh
                # parse on every extract, and the divergence (24) keep path puts
                # an old target on a fresh segment. `target_slots` is written
                # only when the two differ, so its absence means "the segment's
                # own map", which is true of every row an earlier build wrote.
                #
                # The waiver rides here beside `review` for the same reason that
                # one does: it is a statement about *this wording*, and `lx run`
                # re-extracts on every invocation, so a waiver that did not
                # survive an ordinary carryover would be gone before the check
                # that was supposed to see it. What must not survive is a *new*
                # wording, and nothing here can produce one — every path that
                # writes a target drops the flag first.
                entry = (target, held.get("origin") or "carryover", held.get("review"),
                         held.get("waived") == target_token(target),
                         _slot_map(held.get("target_slots")) or _slot_map(held.get("slots")))
                by_key.setdefault(key, []).append(entry)
            keys.append(key)
            entries.append(entry)
        return Carryover(keys, entries, by_key)
    finally:
        conn.close()


class SourceBytesMissing(LookupError):
    """This document's state predates the column that keeps its bytes."""


def source_bytes(src, lang):
    """The document's own bytes as ``lx extract`` read them.

    Raises :class:`SourceBytesMissing` for a row written before this column
    existed — never returns ``None`` for it, because ``b""`` is a real answer
    (an empty file) and a caller that has to tell those apart will one day
    forget. The distinction is also why this is a column rather than a key in
    ``meta``: ``meta`` is JSON text, and a JSON file cannot hold a byte that is
    invalid in UTF-8, which is the whole argument the BLOB column beside it was
    bought with.

    Read on its own rather than inside :func:`load_doc`, because all
    twenty-four ``load_doc`` call sites ask questions about characters and none
    of them wants a megabyte it will not look at.
    """
    conn = _connect(create=False)
    if conn is None:
        _no_state(src, lang)
    try:
        row = conn.execute("SELECT source FROM documents WHERE doc_id=? AND lang=?",
                           (doc_id(src), lang)).fetchone()
        if row is None:
            _no_state(src, lang)
        if row[0] is None:
            raise SourceBytesMissing(
                f"state for {src} [{lang}] was written before it kept the document's "
                f"own bytes, so there is nothing to answer from — run `lx extract "
                f"{src} --lang {lang}` to fill them in. Translations already in the "
                f"state are carried over by content hash, so do not pass --reset.")
        return row[0]
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
    meta = {k: v for k, v in doc.items()
            if k not in ("nodes", "segments", "state_version", "source_bytes")}
    conn = _connect()
    try:
        with conn:
            conn.execute("DELETE FROM nodes WHERE doc_id=? AND lang=?", (did, lang))
            conn.execute("DELETE FROM segments WHERE doc_id=? AND lang=?", (did, lang))
            conn.execute(
                "INSERT OR REPLACE INTO documents (doc_id, lang, state_version, meta, "
                "source) VALUES (?,?,?,?,?)",
                (did, lang, STATE_VERSION, json.dumps(meta, ensure_ascii=False),
                 doc.get("source_bytes")))
            conn.executemany(
                "INSERT INTO nodes (doc_id, lang, pos, raw, body) VALUES (?,?,?,?,?)",
                [(did, lang, *_node_row(i, n)) for i, n in enumerate(doc.get("nodes", []))])
            conn.executemany(
                "INSERT INTO segments (doc_id, lang, seg_id, pos, content_hash, context, "
                "variant, status, target, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(did, lang, *_seg_row(i, s)) for i, s in enumerate(doc.get("segments", []))])
    finally:
        conn.close()


#: The `origin` a model's own pass may not silently replace. Three sources of a
#: translation are treated as equals here — an API model, an agent in its own
#: context, and a person — and this rule singles out exactly one of the three,
#: which is deliberate: `agent` stays unguarded, because an agent is a peer
#: writing its own words, while `llm:*` is the *unattended* pass that runs over
#: whatever it selects.
HUMAN = "human"
_MODEL_PREFIX = "llm:"


def is_model_origin(origin):
    """Whether a write claiming this origin is the model pass's rather than a peer's."""
    return isinstance(origin, str) and origin.startswith(_MODEL_PREFIX)


#: Wording a machine produced and a machine can produce again. `tm` and
#: `tm:legacy` are reuse, so the line they came from is still in
#: `.lx/tm.*.jsonl`; `llm:*` costs one call to make again.
_REGENERABLE = ("tm", "tm:legacy")


def is_regenerable_origin(origin):
    """Whether a memory hit may answer over wording carrying this origin.

    Invariant 9's line — nothing regenerable is a source of truth — applied to an
    ordering question rather than to a storage one. `cli.do_extract` offers this
    document's own stored target first and a banked wording second, and until
    2026-09-01 took whichever the acceptance path accepted first: a stored target
    that no longer fits *with a banked wording behind it that does* was replaced,
    and a `human` segment came back as `tm`, which is not the provenance *Origin
    precedence* protects. `docs/contracts/workbench-http.md` divergence (27).

    **It enumerates what may be replaced, never what is protected**, and the
    difference is the whole safety of it. `carryover` — what
    :func:`prior_targets` calls a body written before the `origin` field existed
    — is nobody's *known* prose, and an origin a later build invents is nobody's
    either; both are kept, because the cost of being wrong in that direction is
    one repair call, which is the cost this rule already accepted, and the cost
    of being wrong in the other is a sentence somebody wrote, replaced with
    nothing printed.
    """
    return is_model_origin(origin) or origin in _REGENERABLE


def _origin_rank(origin):
    """How strongly an `origin` records who wrote a wording: 2, 1 or 0.

    A person's word over anybody else's, and anybody's over a machine's —
    `human`, then every origin :func:`is_regenerable_origin` does not enumerate
    (`agent`, `carryover`, and whatever a later build invents), then `llm:*`,
    `tm` and `tm:legacy`. The middle rank is the default for the reason that
    function gives: an origin nobody here knows is kept, never replaced.
    """
    if origin == HUMAN:
        return 2
    return 0 if is_regenerable_origin(origin) else 1


def carry_candidates(mine, theirs, same, guessed=False):
    """What `lx extract NEW --from OLD` offers one segment of NEW. ``(candidates, how, keep)``.

    ``mine`` is NEW's own entry at this position and ``theirs`` OLD's, each a
    :class:`Carryover` entry or ``None``; ``same`` is whether the two write the
    same words into the document (:func:`as_written`), and ``guessed`` whether
    ``mine`` is only a guess where ``theirs`` is not (:meth:`Carryover.answers`).
    ``candidates`` is ``[(entry, kind)]`` in the order they are tried, ``kind``
    being ``"from"`` for OLD's entry and ``"own"`` for NEW's; the first one the
    acceptance path takes wins. ``keep`` is the ``(entry, kind)`` kept when none
    is taken, or ``None``. ``how`` is what a person has to be told: ``None``,
    ``"origin"``, ``"took"`` or ``"differs"``.

    **Until HANDOFF-066 NEW's own state was never read**, so a carry into a
    chapter that already held work replaced all of it with OLD's and printed
    nothing: a sentence a person re-typed after the first carry came back as
    OLD's, one OLD never translated was emptied, a hold a reviewer had lifted was
    put back. The rule is a precedence over the two, and it is one rule because
    `store._forget_analysis` has to predict what the carry does before it
    recommends one — a second copy of it there is how the advice and the carry
    would come to disagree.

    * **Where only one of them holds wording, that one answers.** Into an empty
      segment this is the carry it always was; a paragraph OLD never translated
      keeps NEW's.
    * **The same words: NEW's, with NEW's hold and waiver**, and the stronger
      `origin` of the two (:func:`_origin_rank`). A hold or a waiver is one
      reviewer's statement about *this* position, and a hold lifted in NEW is
      that reviewer's act too — OLD's copy must not put it back. The `origin` is
      about the words, and a person having written them in OLD is not undone by
      a model having arrived at them in NEW; carrying it is what lets `lx forget`
      stop refusing on the provenance it was protecting.
    * **Different words: NEW's, unless NEW's is a machine draft nobody has held
      or waived and OLD's is not a machine's.** Invariant 9's line, the one a
      memory hit already obeys: a draft is regenerable, and the person named OLD.
      Two drafts are a tie, and a tie stays with NEW — the newer machine work,
      a polish pass paid for in the chapter, is not reverted to OLD's older one.
    * **A guess is not what NEW holds.** Where NEW's own alignment paired this
      position with nothing and handed back the last wording under its key —
      another position's — OLD's answer is tried first whatever either `origin`
      says, and the guess stands only where OLD's does not fit. Measured
      2026-09-10 by the critique of the first version, which protected a
      person's or an agent's guess: a chapter whose run of identical lines grew
      by one had the new member answered with the chapter's own last copy, a
      duplicate, over the named document's placed wording for that exact
      position — which the carry this replaced had got right. A pair the
      alignment made and could not establish is not a guess and is judged by
      the rules above: the wording there was held by a member of this run, and
      reverting it is the loss the whole rule exists to stop.
    * **When nothing fits, what NEW held stays** — divergence (24)'s rule: a
      refusal does not delete what the segment already held. So ``keep`` is
      NEW's own entry wherever NEW held one, even where OLD's was tried first,
      and OLD's only where NEW held nothing or only a guess. The first version
      kept whichever candidate was tried first, which wrote OLD's stale wording
      over NEW's own draft and called it kept.

    *Lost:* OLD over everything, which is what this replaced. *Lost:* NEW over
    everything, which cannot take a reviewed chapter out of a model's first
    draft of it — the case `--from` exists to finish. *Lost:* refusing, which
    answers the one question with an exit code and leaves the person to work out
    which of twelve segments to re-type. What no rule here can tell apart is the
    chapter that was re-worded from the novel that was revised: both are NEW
    holding a person's words and OLD holding different ones, and the words are
    NEW's in both — reported by id, so the revision is one render away.
    """
    if mine is None:
        found = [(theirs, "from")] if theirs is not None else []
        return found, None, (found[0] if found else None)
    if theirs is None:
        return [(mine, "own")], None, (mine, "own")
    if guessed:
        return [(theirs, "from"), (mine, "own")], None, (theirs, "from")
    if same:
        if _origin_rank(theirs[1]) > _origin_rank(mine[1]):
            lifted = (mine[0], theirs[1], mine[2], mine[3], mine[4])
            return [(lifted, "own")], "origin", (lifted, "own")
        return [(mine, "own")], None, (mine, "own")
    if (not mine[2] and not mine[3]
            and _origin_rank(mine[1]) == 0 and _origin_rank(theirs[1]) > 0):
        return [(theirs, "from"), (mine, "own")], "took", (mine, "own")
    return [(mine, "own")], "differs", (mine, "own")


def _begin_write(conn):
    """Take the write lock **before** the first read of a read-then-write.

    Python's ``sqlite3`` defers ``BEGIN`` to the first statement that writes, so
    a ``SELECT`` inside ``with conn:`` runs in autocommit and sees a snapshot
    nothing is holding. Every guard in this module is a read-then-write — the
    origin-precedence check, :func:`save_targets`' body read,
    :func:`save_review`'s — so without this the check and the write it guards
    are two transactions with a window between them, which is exactly the defect
    the compare-and-swap closes one level down.

    Measured 2026-08-15 by an adversarial pass, with ``conn.in_transaction``
    instrumented: the whole of :func:`_written_by_hand` ran outside a
    transaction, and a second `lx` process writing a human target inside that
    window had it overwritten while the run reported ``refused: []``. The
    docstrings here, and `docs/decisions.md`, had asserted the opposite.

    ``IMMEDIATE`` rather than the default deferred begin: a deferred reader that
    later upgrades to a writer raises ``SQLITE_BUSY_SNAPSHOT`` under WAL, which
    no caller here expects and which ``BUSY_TIMEOUT`` does not retry. Taking the
    RESERVED lock up front is what that timeout is for.
    """
    conn.execute("BEGIN IMMEDIATE")


def _written_by_hand(conn, did, lang, ids):
    """Which of these ids hold a person's own words. Read inside the write.

    Inside, and not before, for the reason the lost-update token was rewritten on
    2026-08-14: a check against a snapshot read in an earlier transaction is not
    a check at all when the thing it guards is a concurrent write. That is true
    only because :func:`_begin_write` runs first — read its docstring before
    changing anything here.

    One statement per chunk rather than one per id. The per-id form was measured
    at **28% of the write and 10% of a whole `lx check`** on a 2000-segment
    document, which is the ordinary case: `do_check` writes each row's own origin
    back, so after a draft pass *every* id is `llm:*` and enters this read. The
    docstring here used to claim `lx check` "pays nothing for this at all"; that
    was true of `lx apply`, which writes `human` or `agent` and so passes an
    empty list, and false of the command that runs on every `/api/doc` request.

    Chunked at 500 because SQLite's default host-parameter limit is 999 and a
    novel has thousands of segments.
    """
    out = set()
    ids = list(ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for seg_id, body in conn.execute(
                f"SELECT seg_id, body FROM segments WHERE doc_id=? AND lang=? "
                f"AND seg_id IN ({marks})", (did, lang, *chunk)):
            if json.loads(body).get("origin") == HUMAN:
                out.add(seg_id)
    return out


def _stored_targets(conn, did, lang, ids):
    """``{seg_id: target}`` for these ids. Read inside the write, like the guard.

    Chunked and transacted for :func:`_written_by_hand`'s reasons, which its
    docstring gives; this is the same read one column over.
    """
    out = {}
    ids = list(ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        out.update(conn.execute(
            f"SELECT seg_id, target FROM segments WHERE doc_id=? AND lang=? "
            f"AND seg_id IN ({marks})", (did, lang, *chunk)).fetchall())
    return out


def save_segments(src, lang, segments, expect=None, over_human=False):
    """Write these segments and nothing else. ``(written, stale)``.

    The narrow write, and the reason this package exists. `lx apply` and
    `lx check` touch the segments they changed instead of rewriting a novel's
    whole skeleton, and — the severe half — a translation run commits each batch
    as it lands, so a Ctrl-C or a dropped connection at 90% keeps the 90%.

    A segment whose id is not in the stored document is skipped rather than
    inserted: an id that was never extracted is a caller's mistake, and inserting
    it would put a segment in the document with no node referring to it.

    ``stale`` and ``refused`` are separate lists and mean different things: the
    first lost a compare-and-swap, the second was left alone because a person had
    written it. Folding the second into the first was the first spelling of this,
    and it made `/api/save`'s ``conflicts`` — documented as "refused because its
    ``base`` token did not match" — quietly mean two things the day any endpoint
    passed an origin other than ``human``.

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

    **A segment whose stored ``origin`` is ``human`` is left alone** when the
    incoming one is ``llm:*``, unless ``over_human``. It lands in ``stale`` with
    the ids that lost a compare-and-swap, because from the caller's side the two
    are the same answer — this write did not happen and the row holds something
    else. `lx check` writes each row's own origin back unchanged, so it trips
    this only on a *stale* one — a snapshot that still says ``llm:draft`` for a
    row a reviewer has since claimed, which is the case it should trip on.
    `lx apply` trips it whenever ``--origin`` names an ``llm:*`` value, which it
    accepts as free text; an earlier version of this docstring said `lx apply`
    could not, which was a claim about its *default* origin and not about the
    command.
    """
    if not segments:
        return 0, [], []
    expect = expect or {}
    conn = _connect()
    try:
        with conn:
            _begin_write(conn)
            written, stale, refused = 0, [], []
            guard = () if over_human else _written_by_hand(
                conn, doc_id(src), lang,
                [s["id"] for s in segments if is_model_origin(s.get("origin"))])
            # **The same pop :func:`save_targets` makes, and the condition it does
            # not need.** A wording is written against the segment as it stands,
            # so whatever map an *earlier* target was written against stops being
            # provenance — left behind, `prior_targets` hands `translate.accept`
            # the wrong map at the next extract, `tm_record` banks `slots` naming
            # originals the ids do not mean (a wrong record in invariant 9's
            # source of truth), and the render substitutes the map the wording
            # was written to replace.
            #
            # `save_targets` pops unconditionally and is right to: its text has
            # been through `translate.accept` against the current segment, so it
            # is never the old wording. This function takes whatever `lx apply`
            # was handed, **including the stored target byte for byte** — an
            # agent's whole-document round trip sends every segment back — and an
            # unconditional pop there un-strands a segment nobody edited: the
            # render flips to the wrong original and the `numbering` warning that
            # was the only report of it disappears. Measured 2026-09-01 by the
            # adversarial pass over the commit that added the pop.
            was = _stored_targets(conn, doc_id(src), lang,
                                  [s["id"] for s in segments])
            for seg in segments:
                seg_id = seg["id"]
                if seg_id in guard:
                    refused.append(seg_id)
                    continue
                # A copy rather than a mutation: the caller's dict is
                # `cli.do_apply`'s own and it builds the reply from it.
                # `target_slots` and the waiver are dropped together and under
                # the same condition, because they answer the same question about
                # the same thing: both describe *this wording*, and both stop
                # describing it the moment the wording moves. Conditional, not
                # unconditional — an agent round-tripping a whole document
                # resends every segment byte for byte, and an unconditional pop
                # there would lift every waiver in the book on a save that
                # changed nothing. That is the measured reason the `target_slots`
                # pop is conditional (2026-09-01), and it is this one's too.
                if seg.get("target") != was.get(seg_id):
                    drop = {k for k in ("target_slots", "waived") if seg.get(k)}
                    if drop:
                        seg = {k: v for k, v in seg.items() if k not in drop}
                row = (*_seg_row(0, seg)[2:], doc_id(src), lang, seg_id)
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
        return written, stale, refused
    finally:
        conn.close()


def save_targets(src, lang, targets, origin, over_human=False):
    """Record translated text for these ids. ``(written, refused)``.

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

    **A segment a person has written is left alone**, and their ids come back in
    ``refused`` rather than being dropped in silence — a run that reports
    "translated 40" while having skipped four is the shape of report nobody can
    act on. The guard costs nothing here: this function already reads each row's
    ``body`` before it writes, inside the transaction that writes it, so the
    origin it compares is the one on disk at the moment of the write rather than
    one read earlier. ``over_human`` is the way past it, and it is a deliberate
    act on both surfaces rather than a default.

    Only ``llm:*`` is guarded. `AGENTS.md` treats an API model, an agent in its
    own context and a person as three equal sources, so an ``agent`` write is a
    peer's and not restricted; what this stops is the *unattended* pass, which
    runs over whatever the queue hands it and is the one that was measured
    overwriting review.
    """
    conn = _connect()
    try:
        with conn:
            _begin_write(conn)
            written, refused = 0, []
            guarded = is_model_origin(origin) and not over_human
            for seg_id, text in targets.items():
                row = conn.execute(
                    "SELECT body FROM segments WHERE doc_id=? AND lang=? AND seg_id=?",
                    (doc_id(src), lang, seg_id)).fetchone()
                if row is None:
                    continue
                body = json.loads(row[0])
                if guarded and body.get("origin") == HUMAN:
                    refused.append(seg_id)
                    continue
                body["origin"] = origin
                body.pop("issues", None)
                # This wording is being written against the segment as it stands,
                # so whatever map an *earlier* target was written against is not
                # its provenance any more. Left behind, it would make `accept`
                # re-seat a wording that never needed it. See `target_slots` in
                # `cli.do_extract`.
                body.pop("target_slots", None)
                # And the waiver, for the same reason one line up: it was granted
                # on the wording this statement is replacing. Unconditional here
                # where `save_segments` has to compare, because every caller of
                # this function feeds it `translate.accept`'s output — a proposal
                # that passed the gate — so the text is never the one that was
                # waived. Structural rather than checked: no writer has to
                # remember, and no read has to recompute a fingerprint.
                body.pop("waived", None)
                written += conn.execute(
                    "UPDATE segments SET status=?, target=?, body=? "
                    "WHERE doc_id=? AND lang=? AND seg_id=?",
                    ("translated" if (text or "").strip() else "pending",
                     text, json.dumps(body, ensure_ascii=False),
                     doc_id(src), lang, seg_id)).rowcount
        return written, refused
    finally:
        conn.close()


def save_issues(src, lang, issues, expect=None):
    """Write each segment's ``issues`` list and nothing else. ``written``.

    The narrow write `lx check` needs, and narrow for the reason
    :func:`save_review` is: :func:`save_segments` replaces the whole ``body``
    blob, and ``origin``, ``review`` and ``issues`` all live inside it. A
    compare-and-swap on the ``target`` **column** — which is what `do_check` used
    on 2026-08-16, the first attempt at this — leaves every other field writing
    unconditionally from a snapshot read earlier. Measured by an adversarial pass
    the same day: `POST /api/hold` answered ``applied: 1``, a `POST /api/check`
    already in flight put the pre-hold ``review`` back, and both clients were
    told they had won. The same window rolled an ``origin`` back from ``human``
    to ``tm``, which is how a segment silently stops being covered by the
    precedence guard.

    ``issues[seg_id]`` is the list to store, or a falsy value to remove the key —
    removal rather than an empty list, so "checked and clean" and "never checked"
    are one row, the rule :func:`save_review` follows for ``review``.

    ``expect`` is ``{seg_id: target_at_read}``. An id whose stored target has
    moved since is skipped rather than written: the issues computed for it
    describe wording that is no longer there, and the next check recomputes them
    against what is. It costs nothing — the target is read in the same statement
    as the body.
    """
    expect = expect or {}
    conn = _connect()
    try:
        with conn:
            _begin_write(conn)
            written = 0
            for seg_id, found in issues.items():
                row = conn.execute(
                    "SELECT target, body FROM segments WHERE doc_id=? AND lang=? "
                    "AND seg_id=?", (doc_id(src), lang, seg_id)).fetchone()
                if row is None:
                    continue
                if seg_id in expect and row[0] != expect[seg_id]:
                    continue
                body = json.loads(row[1])
                if found:
                    body["issues"] = found
                else:
                    body.pop("issues", None)
                written += conn.execute(
                    "UPDATE segments SET body=? WHERE doc_id=? AND lang=? AND seg_id=?",
                    (json.dumps(body, ensure_ascii=False),
                     doc_id(src), lang, seg_id)).rowcount
        return written
    finally:
        conn.close()


def save_review(src, lang, review):
    """Set or clear the review flag on these ids, touching nothing else. ``written``.

    The narrowest write in this module: one JSON key inside ``body``, and not
    ``target`` or ``status`` at all. That is the point rather than an
    optimization — a hold is placed on a segment somebody is in the middle of
    reviewing, so a writer that carried a target along would be a way for the
    hold control to undo an edit made since the page was drawn. The read and the
    write share the transaction, so a concurrent save of the same segment lands
    either side of this and neither is lost.

    ``review[seg_id]`` is the value to store, or ``None`` to remove the key
    entirely — removal rather than a stored null, so a segment that was never
    held and one whose hold was lifted are one row and not two.

    **The vocabulary is enforced here**, against :data:`checks.REVIEW_VALUES`,
    and that is a change of mind: this docstring used to say the check was the
    caller's, which named a check no caller performed while the contract
    advertised the closed set as a client-visible guarantee. Enforcing it at the
    one writer is what makes the guarantee true for a caller added later.

    ``written`` counts the rows whose value actually **changed**. A no-op is not
    reported as a release: `lx unhold` on a segment that was never held used to
    print "released 1 segment(s)", which is the only feedback that command gives.
    """
    from .checks import REVIEW_VALUES
    conn = _connect()
    try:
        with conn:
            _begin_write(conn)
            written = 0
            for seg_id, value in review.items():
                row = conn.execute(
                    "SELECT body FROM segments WHERE doc_id=? AND lang=? AND seg_id=?",
                    (doc_id(src), lang, seg_id)).fetchone()
                if row is None:
                    continue
                body = json.loads(row[0])
                if value is not None and value not in REVIEW_VALUES:
                    raise ValueError(
                        f"{value!r} is not a review state. The vocabulary is "
                        f"closed: {', '.join(REVIEW_VALUES)}, or None to clear.")
                if body.get("review") == value:
                    continue
                if value is None:
                    body.pop("review", None)
                else:
                    body["review"] = value
                written += conn.execute(
                    "UPDATE segments SET body=? WHERE doc_id=? AND lang=? AND seg_id=?",
                    (json.dumps(body, ensure_ascii=False),
                     doc_id(src), lang, seg_id)).rowcount
        return written
    finally:
        conn.close()


def save_waived(src, lang, waived, expect=None):
    """Set or clear the waiver on these ids, touching nothing else.

    ``(written, stale)``.

    :func:`save_review`'s twin, and deliberately not a widening of it: ``review``
    holds one string, so a waiver stored there would overwrite a hold — measured
    2026-09-03, ``review`` went ``held`` → ``waived``, :func:`checks.is_held`
    went false, and :func:`checks.workable` handed the segment back to the queues
    the hold had taken it out of. Two keys, two writers, and the two states
    compose the way a reviewer expects: a segment can be both.

    ``waived[seg_id]`` is ``True`` to waive and ``False`` (or ``None``) to lift.
    The key is *removed* rather than stored false, so a segment that was never
    waived and one whose waiver was lifted are one row and not two — the rule
    :func:`save_review` follows.

    **The value stored is the token of the target it was granted over**, not a
    bare ``true``. :func:`_segment` compares it on the way out and does not
    surface a waiver whose wording has moved, so the flag cannot outlive the
    sentence a reviewer read even if some writer forgets to drop it.

    ``expect`` is ``{seg_id: target_at_read}`` and makes this a
    **compare-and-swap**, the way :func:`save_segments` is one. Without it the
    write lands on whatever the row holds *now*: measured 2026-09-03, a
    translation batch committing between :func:`cli.do_waive`'s read and this
    write left the waiver on a wording the reviewer had never seen, with
    ``lx check`` green over it. A named id whose target has moved is skipped and
    comes back in ``stale``.

    The read and the write share the transaction. ``written`` counts the rows
    whose value actually changed, so ``lx unwaive`` on a segment nobody waived
    reports nothing rather than reporting a release.
    """
    expect = expect or {}
    conn = _connect()
    try:
        with conn:
            _begin_write(conn)
            written, stale = 0, []
            for seg_id, value in waived.items():
                row = conn.execute(
                    "SELECT target, body FROM segments "
                    "WHERE doc_id=? AND lang=? AND seg_id=?",
                    (doc_id(src), lang, seg_id)).fetchone()
                if row is None:
                    continue
                target, body = row[0], json.loads(row[1])
                if seg_id in expect and (target or "") != (expect[seg_id] or ""):
                    stale.append(seg_id)
                    continue
                want = target_token(target) if value else None
                if body.get("waived") == want:
                    continue
                if want is None:
                    body.pop("waived", None)
                else:
                    body["waived"] = want
                written += conn.execute(
                    "UPDATE segments SET body=? WHERE doc_id=? AND lang=? AND seg_id=?",
                    (json.dumps(body, ensure_ascii=False),
                     doc_id(src), lang, seg_id)).rowcount
        return written, stale
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


# ── forgetting a document row ──────────────────────────────────────────────
#
# The one delete of a document row in this module, and the mirror of `save_doc`:
# three tables, one transaction, `documents` last. Not one transaction per
# table — a crash between them would leave segment rows with no parent, and
# `tracked` iterates `documents` only, so a visible leftover would become
# permanently invisible garbage. See `docs/decisions.md`, 2026-09-10.

def _wording(text):
    """Text with the blanks it opens and closes with removed. ``""`` for none.

    Stripped because `lx apply` keeps a leading pair of U+3000 — the paragraph
    indent zh-TW prose is set in — and `translate.accept`, which every carry goes
    through, removes it: a wording typed with the indent comes back from a
    faithful `--from` carry without it, measured 2026-09-10 and pinned by
    `tests/test_forget.py`, so a byte comparison refused the very split this
    exists to finish. Python's `str.strip` removes U+3000 where SQLite's `trim()`
    removes only U+0020, which is why the comparison is made here and never in a
    query.
    """
    return (text or "").strip()


def _body(raw):
    """A segment `body` as a dict, or ``None`` where it is not one.

    Forgetting one document must not depend on another one's rows being well
    formed, and a body is hand-editable JSON: a list or a truncated string used
    to end `lx forget` in a traceback. ``None`` is then read as the least
    protective answer for the row it belongs to — no origin, no hold, no waiver,
    the masked wording — which is conservative on both sides: a victim's
    unreadable body claims no provenance it cannot show, and another row's cannot
    count as the person's copy that covers one.
    """
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def _as_written(target, body, render):
    """What a stored target writes into its document, as the wording compared here.

    **Unmasked against the map its `⟦n⟧` mean, then polished the way the render
    polishes it.** Comparing the stored strings was measured wrong in both
    directions on 2026-09-10. A `config/dnt.txt` edit between the translation and
    the carry renumbers the placeholders, so a faithful carry reads as a
    *different* wording and the forget is refused for nothing. And the other way
    round, which lost a sentence: the same masked string in two documents whose
    `⟦1⟧` name different terms reads as the *same* wording, and following the
    refusal's own advice to copy the wording across made the forget delete the
    only row that rendered the right name. What a row *is*, to a reader, is what
    `lx render` writes; `mask.target_map` is the map every other reader of a
    stored target uses, and ``render`` is `cli`'s `polish_rendered` for this
    language, handed in because this module reads no configuration.

    A body that cannot be read, or a slot map from before slots were records,
    falls back to the masked string — refusing more, never less.
    """
    if not _wording(target):
        return ""
    text = target
    if body is not None:
        try:
            text = unmask(target, target_map(body))
        except (TypeError, KeyError, AttributeError, ValueError):
            text = target
    return _wording(render(text) if render else text)


def as_written(target, slots, render=None):
    """:func:`_as_written` for a :class:`Carryover` entry, whose map is its fifth field.

    The one notion of "the same wording" `lx forget` and `lx extract --from`
    share: what the render writes, not the stored string, for the two reasons
    :func:`_as_written` gives — a renumbered `⟦n⟧` and a stripped U+3000 indent
    are the same words, and one string naming two different terms is not.
    """
    return _as_written(target, {"slots": slots} if slots else None, render)


def _other_labels(conn, lang, skip):
    """``{doc_id: (label, tone)}`` for every row in ``lang`` except ``skip``.

    Through :func:`_meta`, the one funnel from a stored row to a dict, so a label
    here is spelled the way every other surface spells it. A row that funnel
    cannot read — hand-edited meta whose `source` is not a string — is named by
    its identity instead of taking the command down: whether one document can be
    forgotten must not depend on another one being well formed.
    """
    out = {}
    for did, version, meta in conn.execute(
            "SELECT doc_id, state_version, meta FROM documents WHERE lang=? AND doc_id<>?",
            (lang, skip)):
        try:
            doc = _meta(meta, version)
        except (TypeError, ValueError):
            out[did] = (did, None)
            continue
        label = doc.get("source")
        out[did] = (label if isinstance(label, str) and label else did, doc.get("tone"))
    return out


def _forget_analysis(conn, did, lang, tone, render=None):
    """What forgetting ``(did, lang)`` would lose. ``dict``; read on ``conn``.

    **The question is the harm, not the population.** A row may go when every
    translation it holds is also held by some other row in the same language —
    the same paragraph (``content_hash``) writing the same words into its
    document (:func:`_as_written`). That is what makes a split finished with ``--from``
    free to clean up and a split finished without it impossible to lose, which
    is the case the package that scheduled this called its flagship: two new
    rows, both empty, and the old row the only thing holding the book.

    *Lost:* ``content_hash`` coverage alone, the predicate that package proposed.
    Measured 2026-09-10, it **allows** exactly that flagship case, because the
    empty new rows hold every paragraph and none of the wording. *Lost:* asking
    the translation memory, which `lx commit` fills only with what it may bank —
    a held segment never, and one line per key, the last — so "banked" is a
    condition a reviewer can be unable to reach and a book with one paragraph
    twice can never satisfy. *Lost:* the source file's existence, which is the
    wrong answer in both directions and differs between the two CI platforms.

    Four things refine it, each measured:

    * **A multiset, not a set.** A book holding one wording at three positions,
      cut so that two survive, loses a position — the shape the 2026-09-04 entry
      calls mechanically invisible. Each copy here must be matched by a copy
      somewhere else, so the third one is named.
    * **Translated segments only.** An untranslated paragraph loses no wording,
      and refusing it would refuse every row extracted under a mistyped `--lang`
      and every document extracted by mistake — the cases most worth forgetting.
    * **A person's word is covered only by a person's.** Where this row says
      ``origin: human``, some other copy of the wording has to say so too.
      Forgetting the last one removes the only record that a person wrote it, and
      with it *Origin precedence*, which guards `human` and nothing else — an
      `agent` copy was measured being overwritten by an `llm:polish` write the
      moment the human row was gone. The memory route turns `human` into `tm`,
      measured, which is how a book reaches this case.
    * **Where it can be carried.** For each other document holding one of these
      paragraphs, whether ``lx extract <it> --from <this>`` would be *safe* and
      whether it would help, answered by running :func:`carry_candidates` — the
      rule the carry runs — over that document's positions aligned against this
      row. Measured 2026-09-10, before HANDOFF-066, a carry replaced whatever the
      target held, so a chapter re-worded after its carry was quietly reverted
      by the carry a refusal had just recommended; the carry now keeps what a
      person or an agent wrote, and this now asks it rather than a multiset,
      which a chapter re-wording one repeated line to the wording held at the
      other copy passed while the carry reverted it. *Safe* is that nothing the
      document holds is replaced or loses a mark; *offered* is safe and leaving
      this row blocking on fewer segments. A register that differs does not
      make a carry unsafe by itself — it makes the command need `--tone`, which
      ``same_register`` says, and under it the document's own wording does not
      align, so it is safe only where what comes back from this row is what it
      held.
    * **Only rows a document owns.** A segment whose `documents` row is gone —
      reachable only by hand, but reachable — is read by no command, and counting
      it as a copy made a forget report "held by another tracked document" about
      text nothing could show.

    Every read here decides whether the caller writes, so it has to run inside
    the caller's :func:`_begin_write` — :func:`forget_doc` is the guard and
    :func:`forget_blockers` the advice, and the advice needs no lock.
    """
    # Each segment as ``(id, hash, wording, human, held, waived)``. `review` holds
    # one closed vocabulary — `held` — so any value is a hold; the waiver is the
    # token of the target it was granted on, the rule `_segment` reads it by.
    def marks(seg_id, content_hash, target, body):
        written = _as_written(target, body, render)
        body = body or {}
        return (seg_id, content_hash, written, body.get("origin") == HUMAN,
                bool(body.get("review")), body.get("waived") == target_token(target))

    # Each segment also as a `Carryover` entry whose first field is the wording
    # as written, so the carry advice below can ask `carry_candidates` — the
    # rule `lx extract --from` runs — what a carry out of this row would do.
    def entry(target, body):
        written = _as_written(target, body, render)
        if not written:
            return None
        body = body or {}
        return (written, body.get("origin") or "carryover", body.get("review"),
                body.get("waived") == target_token(target), None)

    victim, old_keys, old_entries, old_by_key = [], [], [], {}
    for seg_id, h, context, variant, target, raw in conn.execute(
            "SELECT seg_id, content_hash, context, variant, target, body FROM segments "
            "WHERE doc_id=? AND lang=? ORDER BY pos", (did, lang)):
        body = _body(raw)
        victim.append(marks(seg_id, h, target, body))
        key = tm_key(h, context, SEGMENTATION_VERSION, variant, tone) if h else None
        found = entry(target, body) if key is not None else None
        if found is not None:
            old_by_key.setdefault(key, []).append(found)
        old_keys.append(key)
        old_entries.append(found)
    mine = Counter((h, w) for _id, h, w, *_m in victim if w)
    hashes = {h for _id, h, *_rest in victim}
    labels = _other_labels(conn, lang, did)
    # **One scan, in Python**, over the cheap columns only. The correlated
    # `EXISTS` form it replaced was measured at 280 ms over 10,000 rows and 2,025
    # ms over 30,000 — seven times the time for three times the rows — against
    # 38 ms and 125 ms for this function as it ships, on a 500-segment document
    # sharing half its paragraphs with every other one, under `BEGIN IMMEDIATE`:
    # the lock `BUSY_TIMEOUT` bounds for every other writer. `segments_carry`
    # cannot serve the lookup — it leads with `doc_id`, and this goes the other way.
    others, to_read = [], []
    for rowid, other, seg_id, content_hash, target in conn.execute(
            "SELECT rowid, doc_id, seg_id, content_hash, target FROM segments "
            "WHERE lang=? AND doc_id<>? ORDER BY doc_id, pos", (lang, did)):
        if other not in labels:
            continue
        translated = bool(_wording(target))
        others.append([other, seg_id, content_hash, translated])
        if translated and content_hash in hashes:
            to_read.append((rowid, len(others) - 1))
    # Bodies only where a paragraph is shared, by rowid and in chunks rather than
    # a statement per row: decoding every body in the language to answer a
    # question about one document's paragraphs is the read `tracked` was
    # restructured to stop, and a statement each was measured at 28% of a write
    # in `_written_by_hand`.
    where = dict(to_read)
    for start in range(0, len(to_read), 500):
        chunk = [rowid for rowid, _i in to_read[start:start + 500]]
        for rowid, content_hash, target, raw in conn.execute(
                "SELECT rowid, content_hash, target, body FROM segments WHERE rowid IN "
                f"({','.join('?' * len(chunk))})", chunk):
            row = others[where[rowid]]
            row[3] = marks(row[1], content_hash, target, _body(raw))

    def refused(rows):
        """``(blocked, holding)`` over ``rows``, each ``(doc, hash, wording, human)``.

        A function because the carry advice asks it twice: of the rows as they
        are, and of the rows as a carry into one document would leave them.
        """
        elsewhere, persons, holding = Counter(), Counter(), {}
        for other, content_hash, written, human in rows:
            if content_hash not in hashes:
                continue
            holding.setdefault(content_hash, []).append((other, written))
            if written:
                elsewhere[(content_hash, written)] += 1
                persons[(content_hash, written)] += human
        blocked, seen = [], Counter()
        for seg_id, content_hash, wording, human, _held, _waived in victim:
            if not wording:
                continue
            key = (content_hash, wording)
            seen[key] += 1
            held_by = holding.get(content_hash, [])
            if seen[key] > elsewhere[key]:
                if elsewhere[key]:
                    why, docs = "fewer", {o for o, w in held_by if w == wording}
                elif any(w for _o, w in held_by):
                    why, docs = "different", {o for o, w in held_by if w}
                elif held_by:
                    why, docs = "untranslated", {o for o, _w in held_by}
                else:
                    why, docs = "nowhere", set()
            elif human:
                # **At least one**, not one per position. What is protected is
                # the record that a person wrote this wording; one surviving copy
                # keeps it. Counting per position refused a book whose two human
                # copies of a line were matched by one human and one memory copy,
                # and named the human one "a machine's" — measured by the
                # mutation pass.
                if persons[key]:
                    continue
                why, docs = "provenance", {o for o, w in held_by if w == wording}
            else:
                continue
            blocked.append({"id": seg_id, "why": why,
                            "docs": sorted(labels.get(d, (d, None))[0] for d in docs)})
        return blocked, holding

    current = [(other, content_hash,
                found[2] if isinstance(found, tuple) else "",
                found[3] if isinstance(found, tuple) else False)
               for other, _id, content_hash, found in others]
    blocked, holding = refused(current)

    # **Which documents a carry could go into, decided by running the carry.**
    # Until HANDOFF-066 this was a multiset test — the target's translations
    # matched here by a copy with the same wording and at least its marks — and
    # `--from` is positional: a chapter that re-worded one repeated line to the
    # wording this row holds at the *other* copy passed the multiset and had the
    # line reverted by the carry. It is also the wrong question now that a carry
    # keeps what a person or an agent wrote: a chapter holding a different
    # wording of its own is no longer one a carry would damage. So each document
    # is aligned against this row with `Carryover.align` — its stored segments
    # standing in for the fresh parse, which they are unless the file changed
    # since — and every position answered by `carry_candidates`, the rule the
    # carry itself runs. *Safe* is that the carry changes nothing the document
    # holds but its machine drafts: no wording a person or an agent wrote, or
    # somebody held or waived, replaced; no mark dropped; no `origin` a
    # machine's that was not. The drafts it would replace are listed apart, as
    # ``drafts``, because replacing them is what the carry is for and the offer
    # has to name them — counting them as unsafe refused the carry that puts a
    # person's `origin` back over the memory route's `tm` copies, which is the
    # one this advice exists to give. *Offered* is that it is safe and would
    # leave this row blocking on fewer segments than it does now.
    #
    # The offered command carries `--tone` whenever the registers differ, so the
    # carry runs in this row's register — and then the document's own entries do
    # not align at all, every position answers from here, and whatever it held
    # is compared against what comes back. What it does not model: a memory hit,
    # which can only fill, and a wording the acceptance path would refuse, where
    # the carry keeps the other candidate — both make the prediction more
    # cautious than the carry, never less.
    hash_of = {seg_id: h for seg_id, h, *_rest in victim}
    involved = {o for c in blocked for o, _w in holding.get(hash_of[c["id"]], [])}
    carry, old = [], Carryover(old_keys, old_entries, old_by_key)
    for other in sorted(involved, key=lambda d: labels.get(d, (d, None))[0]):
        label, other_tone = labels[other]
        same_register = canonical_tone(other_tone) == canonical_tone(tone)
        rows = conn.execute(
            "SELECT seg_id, content_hash, context, variant, target, body FROM segments "
            "WHERE doc_id=? AND lang=? ORDER BY pos", (other, lang)).fetchall()
        theirs_of = old.align([{"id": seg_id, "hash": h, "context": context,
                                "variant": variant}
                               for seg_id, h, context, variant, _t, _b in rows], tone)
        own, drafts, after = [], [], []
        for seg_id, h, _context, _variant, target, raw in rows:
            here = entry(target, _body(raw))
            theirs, _ambiguous = theirs_of[seg_id]
            ours = here if same_register else None
            same = ours is not None and theirs is not None and ours[0] == theirs[0]
            # `ours` is this document's stored row by identity, never a guess, so
            # `guessed` stays false; and the first candidate stands in for what
            # lands, because the acceptance path is not modelled here.
            candidates, _how, _keep = carry_candidates(ours, theirs, same)
            post = candidates[0][0] if candidates else None
            if here is not None and post is not None and post[0] != here[0] \
                    and not _origin_rank(here[1]) and not here[2] and not here[3]:
                # A machine draft nobody held or waived, answered by this row's
                # wording: what the carry is for, and not a loss — invariant 9 —
                # but the offer has to say it, because it is not "nothing".
                drafts.append(seg_id)
            elif here is not None and (
                    post is None or post[0] != here[0]
                    or (here[1] == HUMAN and post[1] != HUMAN)
                    or (here[2] and not post[2]) or (here[3] and not post[3])
                    or (_origin_rank(here[1]) and not _origin_rank(post[1]))):
                own.append(seg_id)
            after.append((other, h, post[0] if post else "",
                          post is not None and post[1] == HUMAN))
        still, _holding = refused([r for r in current if r[0] != other] + after)
        carry.append({"doc": label, "own": own, "drafts": drafts, "safe": not own,
                      "offer": not own and len(still) < len(blocked),
                      "same_register": same_register})
    return {"segments": len(victim), "translated": sum(mine.values()),
            "blocked": blocked, "carry": carry}


def forget_doc(src, lang, discard=False, render=None):
    """Delete one ``(document, language)`` row. ``None`` when there is no such row.

    Otherwise a ``dict`` carrying the stored ``source`` and ``tone`` beside
    :func:`_forget_analysis`'s answer, and ``refused``: ``None`` when the row is
    gone, or why nothing was deleted — ``"aim"``, ``"source"`` or ``"wording"``.
    ``discard`` overrides ``"wording"`` and nothing else. ``render`` is how the
    render polishes a restored target for this language — :func:`_as_written`.
    The sentences are `cli.do_forget`'s; this decides.

    **Aimed by the stored spelling, inside the lock.** `doc_id` flattens every
    separator, so `docs_guide.md` and `docs/guide.md` are one row here — and a
    person forgetting a file they can see would otherwise destroy the state of a
    document they cannot. The row goes only when ``doc_label(src)`` *is* its
    stored `source`, and that is compared after the write lock is taken, because
    a concurrent colliding `lx extract` can replace the row between a check made
    outside it and the delete. A row whose `source` is not a string cannot be
    confirmed and is refused the same way.

    **Nothing here opens, stats or confines a path** (invariant 11). The spelling
    is compared as a string and the delete is keyed on ``(doc_id, lang)``, and
    because `doc_id` flattens every separator no spelling can reach anything
    outside this database. Confining it would have been worse than unnecessary:
    `lx extract ../shelf/book.md` is supported and stores that source verbatim,
    `cli.confined_path` refuses it, and the documents most likely to have moved
    would have become impossible to forget.

    **Read-then-write, so the lock comes first.** Every read below decides
    whether the deletes run, and Python's `sqlite3` would otherwise run them in
    autocommit — :func:`_begin_write`'s docstring has the measured cost. A row
    from a *newer* build is refused rather than judged, since this build cannot
    know what its body means; an *older* one is judged, because what the
    judgement reads either means today what it meant then — the target and hash
    columns, `origin`, the hold, the waiver — or falls back to the masked string
    where a slot map predates the records (:func:`_as_written`), which refuses
    more and never less. Requiring a re-extract first would be requiring a source
    file that is usually gone.

    Touches nothing outside `.lx/state.db`: not `.lx/tm.*.jsonl`, not the
    rendered output, not the source file. The check report is `cli.do_forget`'s.
    """
    conn = _connect(create=False)
    if conn is None:
        return None
    did = doc_id(src)
    try:
        with conn:
            _begin_write(conn)
            doc = _read_meta(conn, src, lang)
            if doc is None:
                return None
            stored, tone = doc.get("source"), doc.get("tone")
            out = {"source": stored, "tone": tone, "refused": None}
            if not isinstance(stored, str) or not stored:
                out["refused"] = "source"
                return out
            if stored != doc_label(src):
                out["refused"] = "aim"
                return out
            if doc["state_version"] > STATE_VERSION:
                raise StateVersionError(
                    f"state for {stored} [{lang}] is version {doc['state_version']}, newer "
                    f"than the {STATE_VERSION} this build reads, so this build cannot tell "
                    f"what forgetting it would lose — upgrade scriptorium to forget it. "
                    f"Nothing was deleted.")
            out.update(_forget_analysis(conn, did, lang, tone, render))
            if out["blocked"] and not discard:
                out["refused"] = "wording"
                return out
            conn.execute("DELETE FROM segments WHERE doc_id=? AND lang=?", (did, lang))
            conn.execute("DELETE FROM nodes WHERE doc_id=? AND lang=?", (did, lang))
            conn.execute("DELETE FROM documents WHERE doc_id=? AND lang=?", (did, lang))
            return out
    finally:
        conn.close()


def forget_blockers(src, lang, render=None):
    """:func:`_forget_analysis` for a stored row, read-only. ``None`` if there is none.

    Advice rather than a guard: `lx extract --from` prints whether the document
    it carried from could now be forgotten without loss, which is the moment a
    person is looking at both. It takes no lock because nothing is written on
    its answer — :func:`forget_doc` asks the same question again under one.

    Carries the stored ``source`` beside the answer, because the command the
    advice names has to be spelled the way the row is stored: `--from` resolves
    through `doc_id`, so `--from docs_guide.md` reads `docs/guide.md`'s row, and
    advice naming the typed spelling named a forget the aim rule then refused.
    """
    conn = _connect(create=False)
    if conn is None:
        return None
    try:
        doc = _read_meta(conn, src, lang)
        if doc is None:
            return None
        found = _forget_analysis(conn, doc_id(src), lang, doc.get("tone"), render)
        return dict(found, source=doc.get("source"))
    finally:
        conn.close()


def tm_lines(lang):
    """``[(lineno, record)]`` for every readable line, in file order.

    A line that is not an object with a hash and a target is skipped rather than
    raised on. The file is append-only and hand-editable by design, and one bad
    line taking down every command that reads the memory is a poor trade for a
    diagnostic nobody asked for.

    Split out of :func:`load_tm` for `lx audit`, which needs to name the line a
    record came from and cannot get one from a dict keyed by identity: a
    corrected re-bank and the wording it supersedes are *one* entry there and
    two lines here. Nothing else reads it — the parse rule for what counts as a
    record lives here now so that the reader with line numbers and the reader
    without cannot come to disagree about which lines exist.
    """
    out = []
    p = tm_path(lang)
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or not rec.get("hash") or not rec.get("target"):
                continue
            out.append((lineno, rec))
    return out


def tm_effective(lang):
    """``([(lineno, record)], superseded)`` — the memory as anything actually reads it.

    Last write wins, so a correction supersedes its original; ``superseded`` is
    how many readable lines a later line displaced. The records come back in the
    order their key first appears, which is what :func:`load_tm` has always
    handed back and is a pure function of the file.

    It exists because "how many wordings does this memory hold" and "how many
    lines does this file have" are different numbers, and only the first one is
    a fact about the project: measured 2026-09-06 on the maintainer's tracked
    `.lx/tm.zh-TW.jsonl`, 208 lines carry 157 wordings, and 15 of the 17
    misattributed lines in it are already displaced by a later, correct re-bank.
    An audit over the lines would send a reviewer to repair fifteen records no
    command reads, and — because a repair here is an *append* and a displaced
    line stays displaced — nothing they did would change what the audit said
    the next time. See `docs/decisions.md`, 2026-09-06.
    """
    rows = {}
    total = 0
    for lineno, rec in tm_lines(lang):
        total += 1
        rows[record_key(rec)] = (lineno, rec)
    return list(rows.values()), total - len(rows)


def load_tm(lang):
    """``{key: record}``. Last write wins, so a correction supersedes its original.

    **The whole record, not the target.** It flattened to ``rec["target"]`` until
    2026-08-17, which is a smaller thing to hold and made a line's own account of
    what its placeholders meant unreachable — so a reuse could only compare id
    sets, and a wholesale renumbering satisfies that. :func:`tm_lookup` returns
    the map beside the target now, and `lx todo`'s fuzzy panel will want the
    `source` off the same line.

    Built on :func:`tm_effective` rather than reading the file itself, so that
    "which line wins" is stated once. Two loops each ending in
    ``rows[record_key(rec)] = rec`` is not a duplication anybody notices until
    one of them grows a condition.
    """
    return {record_key(rec): rec for _, rec in tm_effective(lang)[0]}


def tm_lookup(tm, seg, tone=None):
    """``(target, origin, slots, waived)`` for a segment, or all-empty.

    ``waived`` is whether the *record* was banked from a segment a reviewer had
    waived. It travels so that the caller can say so, and for nothing else: the
    receiving segment does not inherit the waiver, because one reviewer's
    judgement about one position is not a judgement about a document they have
    never seen. `cli.do_extract` names the segments it happened to, the way it
    names a `kept` or a `replaced` one.

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
        return (exact["target"], "tm", slot_map(exact.get("slots")),
                bool(exact.get("waived")))
    if seg.get("variant") is None and key_tone(tone) is None:
        legacy = tm.get(tm_key(seg["hash"], None, 0, None, None))
        if legacy is not None:
            return (legacy["target"], "tm:legacy", slot_map(legacy.get("slots")),
                    bool(legacy.get("waived")))
    return None, None, None, False


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
    # **The map this wording's placeholders were written against**, written only
    # when there are placeholders to explain. Without it a line is a target and
    # nothing else, and a reuse can only compare id *sets* — which a wholesale
    # renumbering satisfies, so wording banked under one `config/dnt.txt` renders
    # the wrong term under another with `lx check` green. `target_slots` first,
    # for the same reason `store.prior_targets` reads it first: a segment's own
    # `slots` is the last parse's map, not necessarily its target's.
    if placeholder_ids(seg.get("target") or ""):
        originals = slot_originals(_slot_map(seg.get("target_slots"))
                                   or _slot_map(seg.get("slots")))
        if originals:
            rec["slots"] = originals
    # **A banked wording says whether a reviewer had to waive it.** `lx commit`
    # gates on `checks.check_segment` at error severity, and a waiver moves
    # exactly those issues to warn — so without this field the gate would let a
    # waived wording through wearing no mark at all, and the next document would
    # receive it as an ordinary hit. The memory is read by every document in the
    # project, and one reviewer's judgement about one position does not travel:
    # the receiving segment is *not* waived, `lx check` reports it there, and
    # `lx extract` names it so the reader is told rather than left to notice.
    #
    # Written only when true, the rule every optional field here follows, so a
    # memory file with no waivers is byte-for-byte the file it was before.
    if seg.get("waived"):
        rec["waived"] = True
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
        # The whole record, not the target: a line that holds this wording but
        # not the map its placeholders were written against is not the line this
        # build writes, and comparing targets alone is what would keep it from
        # ever gaining one. So the first `lx commit` after that field arrived
        # re-banks the segments that need it, once, visibly, in a file whose
        # contract is that it only grows.
        record = tm_record(seg, tone)
        # **A wording banked as waived stays marked while it is that wording.**
        # The mark says a reviewer had to stand by these words somewhere, and
        # `tm_record` can only read the segment in front of it — which is a
        # *different* segment on every document after the first, and one this
        # build deliberately leaves unwaived. Without this the mark comes off the
        # tracked file the first time anybody commits the same wording without a
        # waiver of their own: measured 2026-09-03, waive → commit → unwaive →
        # commit erased it in one project, and the whole payment for banking a
        # waived wording at all is that the file says so.
        #
        # It also stops a reviewer's flag churning a source of truth: toggling a
        # waiver used to append a full duplicate line per commit, six for one
        # wording in the measured run, because the record differed by that field
        # alone. Now it does not differ, so the comparison below skips it.
        #
        # Keyed on the wording, so re-wording the segment produces an unmarked
        # record as it should — a new sentence has been through no reviewer.
        held = tm.get(segment_key(seg, tone))
        if held and held.get("waived") and held.get("target") == record.get("target"):
            record["waived"] = True
        if held == record:
            continue
        out.append(record)
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
