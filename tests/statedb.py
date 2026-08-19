"""Reading and doctoring the state database from a test.

Document state is SQLite since 2026-08-02, and several tests assert on its
*contents* after a subprocess wrote them in `tmp_path`. They cannot go through
`store`, whose paths are all relative to the working directory — the test process
is somewhere else. So this reads the file directly, which is also what the tests
did when the state was JSON.

Doctoring is deliberate rather than a shortcut. The version tests have to produce
state this build refuses to read, and `save_doc` will not write it: that refusal
is the thing under test.
"""

import json
import sqlite3

_SEG_COLUMNS = ("seg_id", "content_hash", "context", "variant", "status", "target")


def _open(root):
    return sqlite3.connect(str(root / ".lx" / "state.db"))


def _query(root, sql, args=()):
    conn = _open(root)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _write(root, sql, args=()):
    conn = _open(root)
    try:
        with conn:
            conn.execute(sql, args)
    finally:
        conn.close()


def documents(root):
    """Every document row, as the dict `load_doc` would have returned its meta."""
    return [{**json.loads(meta), "doc_id": did, "state_version": version}
            for did, version, meta in _query(
                root, "SELECT doc_id, state_version, meta FROM documents ORDER BY doc_id, lang")]


def segments(root):
    """Every segment, in document order, rebuilt from its columns and its body."""
    out = []
    for row in _query(root, f"SELECT {', '.join(_SEG_COLUMNS)}, body FROM segments ORDER BY pos"):
        seg = json.loads(row[-1])
        seg.update(id=row[0], hash=row[1], context=row[2], variant=row[3],
                   status=row[4], target=row[5])
        out.append(seg)
    return out


def nodes(root):
    """Skeleton nodes in order, with the raw value put back where it belongs."""
    out = []
    for raw, body in _query(root, "SELECT raw, body FROM nodes ORDER BY pos"):
        node = json.loads(body)
        if raw is not None:
            node["v"] = raw
        out.append(node)
    return out


def set_schema_version(root, version):
    """Claim the *database* has columns this build has never heard of."""
    _write(root, f"PRAGMA user_version = {int(version)}")


def set_state_version(root, version):
    """Claim every document was written by another build. Content version only."""
    _write(root, "UPDATE documents SET state_version=?", (version,))


def set_target(root, seg_id, target):
    """Write a target column directly, past every rule that guards the door.

    `do_apply` refuses an empty target and `save_targets` refuses to overwrite a
    person's words, so a test about what the *counters* do with a row that
    already exists cannot get there through the CLI. This is the same doctoring
    the version helpers above do, for the same reason: the refusal is not what is
    under test.
    """
    # `status` moves with it, which is the half that makes the doctoring
    # faithful. A row written by a build that let a whitespace target through
    # holds `status='translated'` beside text that strips to nothing — that
    # inconsistency *is* the population, and leaving `status` at `pending` made a
    # status-based counter and a text-based one agree, so a test over it could
    # not tell the two rules apart.
    _write(root, "UPDATE segments SET target=?, status='translated' WHERE seg_id=?",
           (target, seg_id))


def edit_segments(root, change):
    """Apply `change(body_dict)` to each segment's JSON body and write it back."""
    for seg_id, body in _query(root, "SELECT seg_id, body FROM segments"):
        edited = change(json.loads(body))
        _write(root, "UPDATE segments SET body=? WHERE seg_id=?",
               (json.dumps(edited, ensure_ascii=False), seg_id))
