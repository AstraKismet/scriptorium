"""On-disk state: per-document segment stores and the translation memory."""

import difflib
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter

from .config import DEFAULT_TONE, STATE, canonical_tone
from .mask import placeholder_ids

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
        #: document order. The fallback, and the old rule's whole world. An entry
        #: is ``(target, origin, review, waived, slots)``, where ``slots`` is the
        #: map the target's placeholders were written against and ``waived`` is
        #: whether a reviewer had answered that wording's report.
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

        What the diff cannot place — text that moved, and a *new* occurrence of a
        sentence the document already had — falls back to the last translated
        entry under that key, which is the rule that carried everything before
        2026-08-17. **The fallback does not carry a hold.** A hold is one
        reviewer's statement about a position, and this is the branch that could
        not establish one; carrying it would take a paragraph nobody has looked at
        out of every queue, silently, which is how a run of new dialogue came back
        `held` and rendered into the book.

        **A block with no anchor is not evidence.** When every element of a
        matching block carries the same key, a run has been matched against a run
        and the diff simply took the first offset that fitted; if that run also
        changed size, one of its members was added or removed and the offset is a
        coin toss. Those blocks are refused rather than believed, and their
        segments fall to the fallback — which is the answer this build gave
        before, so the degenerate document (a file that is one sentence repeated,
        with one occurrence deleted) is no worse than it was rather than newly
        wrong in the direction that locks a model out of a position. A run whose
        size did not change is placed, which is what carries forty identical
        paragraphs across an insertion.

        ``ambiguous`` is then simply "the diff could not place this and something
        was carried anyway": a new occurrence of a sentence the document already
        had, a lone paragraph that moved, or a member of a run nothing could tell
        apart. `lx extract` names them.
        """
        fresh = [(seg["id"], segment_key(seg, tone)) for seg in segments]
        keys = [key for _, key in fresh]
        prior_runs, fresh_runs = Counter(self.keys), Counter(keys)

        placed = {}
        for i, j, size in self._blocks(keys):
            if not size:
                continue                      # the sentinel block
            block = {keys[j + d] for d in range(size)}
            if len(block) == 1:
                key = next(iter(block))
                if prior_runs.get(key, 0) != fresh_runs[key]:
                    continue
            for d in range(size):
                placed[fresh[j + d][0]] = self.entries[i + d]

        out = {}
        for sid, key in fresh:
            if sid in placed:
                out[sid] = (placed[sid], False)
                continue
            rows = self.by_key.get(key)
            # Neither `review` nor the waiver survives the fallback, and for
            # one reason: both are a reviewer's statement about a *position*, and
            # this is the branch that could not establish one. Carrying a hold in
            # took a paragraph nobody had looked at out of every queue; carrying a
            # waiver in would go one worse and answer the report on a paragraph
            # nobody had read, which is the one thing a waiver must never do by
            # itself. The provenance map does travel, because it describes the
            # wording rather than the position.
            entry = ((rows[-1][0], rows[-1][1], None, False, rows[-1][4])
                     if rows else None)
            out[sid] = (entry, entry is not None)
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


def load_tm(lang):
    """``{key: record}``. Last write wins, so a correction supersedes its original.

    A line that is not an object with a hash and a target is skipped rather than
    raised on. The file is append-only and hand-editable by design, and one bad
    line taking down every command that reads the memory is a poor trade for a
    diagnostic nobody asked for.

    **The whole record, not the target.** It flattened to ``rec["target"]`` until
    2026-08-17, which is a smaller thing to hold and made a line's own account of
    what its placeholders meant unreachable — so a reuse could only compare id
    sets, and a wholesale renumbering satisfies that. :func:`tm_lookup` returns
    the map beside the target now, and `lx todo`'s fuzzy panel will want the
    `source` off the same line.
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
                    tm[record_key(rec)] = rec
    return tm


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
