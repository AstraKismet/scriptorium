"""A reviewer reads a translation beside its source, from a terminal.

`GET /api/doc` has shown a reviewer a segment's source beside its target since
the workbench existed — and its `origin`, `review` and `waived` since each of
those landed — while nothing on the command line could. `lx todo` carries no
target at all, whatever `--all` does; `lx blocks` projects the rebuilt output
rather than the state; `lx check --json` names a segment only where a rule
fired; `lx status --json` deliberately carries no segment text. What was left
was opening `.lx/state.db` by hand.

Three properties carry this file. The first is that it **writes nothing over a
cleanly closed project**, asserted on the bytes of every file and directory
under `tmp_path` with the snapshot taken before the first invocation —
`tests/test_renderings.py`'s idiom and for its measured reason: a snapshot in
the middle cannot see a write that is idempotent. The project sits one level
inside `tmp_path` so that a write to `os.pardir` lands inside the snapshot.

The second is that it is a **projection and not a report**. It carries no
severities and no counts of findings, it lists a held segment like any other
because a hold takes a segment out of the *queues* rather than out of view, and
no finding moves its exit code.

The third is the pair of texts. `source` is the document's own words and
`masked` is what the model was sent, spelled as `store.load_doc` spells them,
because a target holds `⟦n⟧` and it is `masked` that lines those up — except on
a `stranded` row, which is the one case where neither does and the row has to
say so. The fixture here has markup in it on purpose:
`tests/test_renderings.py`'s unit fixture sets `source` and `masked` to the same
string, so a build that swapped them would pass that whole module.
"""

import ast
import hashlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")
CLI_SRC = (ROOT / "src" / "scriptorium" / "cli.py").read_text(encoding="utf-8")

#: Five segments and every state the command has to show at once: a heading and
#: a paragraph a model drafted, a paragraph a person wrote and then held, a
#: paragraph whose wording drops a placeholder — which is what makes it
#: waivable, since `do_waive` refuses a segment `lx check` reports nothing on —
#: and a paragraph nobody has translated. The two code spans are the point of
#: the file: they are what makes `masked` differ from `source`.
DOC = (
    b"# Chapter One\n"
    b"\n"
    b"Ashcombe read the note and put it in the fire.\n"
    b"\n"
    b"Eleanor kept the accounts, and `ledger.md` was where she kept them.\n"
    b"\n"
    b"The road to Marchmont ran past `mill.md` and the old weir.\n"
    b"\n"
    b"A paragraph nobody has translated yet.\n")

DRAFTED = {
    "s0001": "第一章",
    "s0002": "灰岸讀了那張字條，然後把它丟進火裡。",
}

BY_HAND = {
    "s0003": "艾蓮諾管帳，帳就在 ⟦1⟧ 裡。",
    # Drops ⟦1⟧, so `tags` reports it at error and a waiver is admissible.
    "s0004": "去馬奇蒙特的路經過舊堤。",
}


def _lx(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _env():
    return {**os.environ, "PYTHONPATH": SRC}


def _proj(tmp_path):
    """The project root, one level **inside** ``tmp_path``.

    Not `tmp_path` itself, and that is the read-only test's whole reach: a
    fingerprint of the project root cannot see a write to `os.pardir`, and
    `dump_json(os.path.join(os.pardir, ...))` is a call this module already
    makes elsewhere. With the project one level down, the snapshot covers the
    directory a stray relative write would land in.
    """
    return tmp_path / "proj"


def _apply(tmp_path, env, name, targets, origin):
    (_proj(tmp_path) / name).write_bytes(
        json.dumps(targets, ensure_ascii=False).encode("utf-8"))
    result = _lx(["apply", "book/ch1.md", "--lang", "zh-TW", "--file", name,
                  "--origin", origin], _proj(tmp_path), env)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def _project(tmp_path):
    (_proj(tmp_path) / "book").mkdir(parents=True)
    env = _env()
    assert _lx(["init"], _proj(tmp_path), env).returncode == 0
    (_proj(tmp_path) / "book" / "ch1.md").write_bytes(DOC)
    assert _lx(["extract", "book/ch1.md", "--lang", "zh-TW", "--tone",
                "literary"], _proj(tmp_path), env).returncode == 0
    _apply(tmp_path, env, "draft.json", DRAFTED, "llm:draft")
    _apply(tmp_path, env, "hand.json", BY_HAND, "human")
    assert _lx(["hold", "book/ch1.md", "--lang", "zh-TW", "--ids", "s0003"],
               _proj(tmp_path), env).returncode == 0
    waive = _lx(["waive", "book/ch1.md", "--lang", "zh-TW", "--ids", "s0004"],
                _proj(tmp_path), env)
    assert waive.returncode == 0, waive.stderr.decode("utf-8", "replace")
    return env


def _report(tmp_path, env, *args):
    result = _lx(["segments", "book/ch1.md", "--lang", "zh-TW", "--json", *args],
                 _proj(tmp_path), env)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout.decode("utf-8"))


def _rows(report):
    return {row["id"]: row for row in report["segments"]}


def _fingerprint(root):
    """Every file and every directory under ``root``, by content.

    The whole tree, not a chosen list — and directories as well as files,
    because `os.walk` records only files and a command that created an empty
    directory would otherwise be invisible.
    """
    out = {}
    for base, dirs, names in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(base, name), root)] = "<dir>"
        for name in names:
            path = os.path.join(base, name)
            out[os.path.relpath(path, root)] = hashlib.sha256(
                open(path, "rb").read()).hexdigest()
    return out


# --- acceptance criterion 3: every state, with its fields ---------------------


def test_segments_shows_translated_pending_held_and_waived_at_once(tmp_path):
    """Acceptance criterion 3, on one document holding all four states.

    The four are not variations on one shape. `status` is derived from whether
    the target has text in it, `review` is a closed vocabulary of one value,
    `waived` is its own boolean because a `review` value would have deleted the
    hold, and a segment can be both. All four have to be visible without a
    second command, or the reviewer is back to opening the database.
    """
    env = _project(tmp_path)
    rows = _rows(_report(tmp_path, env))
    assert list(rows) == ["s0001", "s0002", "s0003", "s0004", "s0005"]

    assert rows["s0002"]["status"] == "translated"
    assert rows["s0002"]["origin"] == "llm:draft"
    assert rows["s0002"]["review"] == "" and rows["s0002"]["waived"] is False

    assert rows["s0003"]["review"] == "held"
    assert rows["s0003"]["origin"] == "human"
    assert rows["s0003"]["waived"] is False

    assert rows["s0004"]["waived"] is True
    assert rows["s0004"]["status"] == "translated"

    assert rows["s0005"]["status"] == "pending"
    assert rows["s0005"]["target"] == ""
    assert rows["s0005"]["origin"] == ""


def test_segments_carries_exactly_the_keys_it_promises(tmp_path):
    """The row shape, pinned as a set rather than key by key.

    A key present on one row and absent on another is the shape a consumer
    cannot branch on, so every row carries all ten — `origin` and `review`
    empty rather than null, which is `do_renderings`' spelling and the one this
    command inherits, and `waived` and `stranded` bools rather than the target
    token and the slot map `store` holds them as.
    """
    env = _project(tmp_path)
    expected = {"id", "kind", "status", "origin", "review", "waived",
                "stranded", "source", "masked", "target"}
    for row in _report(tmp_path, env)["segments"]:
        assert set(row) == expected, row["id"]
        assert isinstance(row["waived"], bool)
        assert isinstance(row["stranded"], bool)
        for key in ("origin", "review", "source", "masked", "target"):
            assert isinstance(row[key], str), (row["id"], key)


def test_segments_shows_the_raw_source_and_the_masked_one_apart(tmp_path):
    """The decision this package had to make, pinned on a segment with markup.

    A stored target holds `⟦n⟧`, so only `masked` lines the placeholders up
    against it and only `source` says what `⟦1⟧` stood in for. Both are carried
    under `store.load_doc`'s own names, which is also what stops one spelling
    carrying two answers: `do_renderings` calls the *masked* text `source` and
    `do_suggest` calls the *raw* text `source`, and those two already disagree.
    """
    env = _project(tmp_path)
    row = _rows(_report(tmp_path, env))["s0003"]
    assert "`ledger.md`" in row["source"] and "⟦1⟧" not in row["source"]
    assert "⟦1⟧" in row["masked"] and "`ledger.md`" not in row["masked"]
    assert "⟦1⟧" in row["target"]
    # And a segment with no markup carries the same string twice rather than
    # dropping the key, so a consumer never branches on its presence.
    plain = _rows(_report(tmp_path, env))["s0002"]
    assert plain["masked"] == plain["source"]


# --- acceptance criterion 4: it reads through the store -----------------------


def _function(name):
    """The named `cli.py` function, by syntax rather than by text.

    A `grep` over the module would match the `from .store import load_doc` at
    the top of the file and the prose in every docstring that discusses it;
    reading the function's own body is what pins the claim. The floor below is
    what makes a rename fail loudly instead of silently finding nothing.
    """
    tree = ast.parse(CLI_SRC)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == name), None)
    assert fn is not None, (
        f"cli.{name} is gone or renamed. Teach this test the new shape rather "
        f"than deleting it — the property it pins is that the state is reached "
        f"through `store` and never opened here.")
    return fn


def _calls(fn):
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                out.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                out.add(node.func.attr)
    return out


def _string_literals(fn):
    """Every string constant in ``fn`` that is not a bare string statement.

    Docstrings and the block comments Python spells as loose strings are the
    prose *about* the rule and are allowed to name what the code may not, which
    is exactly the distinction a `grep` cannot draw.
    """
    prose = {id(node.value) for node in ast.walk(fn)
             if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
             and isinstance(node.value.value, str)}
    return {node.value for node in ast.walk(fn)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in prose}


#: Every name in `cli.py` that puts bytes on disk, as `cli.py` itself spells it
#: — the `store` and `docio` writers it imports, plus the three it owns. A test
#: that forbade only `open` and `connect` was measured to miss `dump_json`, the
#: identical call `do_check` makes one function away, and a mutant using it wrote
#: a JSON file on every run with all seventeen tests green.
_WRITERS = frozenset({
    "open", "connect", "cursor", "execute", "executemany", "executescript",
    "db_path", "dump_json", "write_document", "write_document_to_stdout",
    "write_templates", "append_tm", "save_doc", "save_issues", "save_review",
    "save_segments", "save_targets", "append_glossary_rows", "_write_config",
    "_glossary_write", "write_text", "write_bytes", "mkdir", "makedirs",
    "replace", "remove", "unlink", "rename", "copy", "copyfile",
})


def _reached(names, depth=1):
    """``names``' calls, following ``depth`` levels of `cli.py`'s own functions.

    One level, and the depth is the point rather than an implementation detail:
    `do_segments` and `cmd_segments` call helpers this change newly shares —
    `_preview` is called by `cmd_blocks` too — and a write placed in one of them
    is invisible to a walk of two function bodies. Measured 2026-09-08: an
    `open(...).write(...)` at the top of `_preview` passed the two-body version
    of this test unchanged.
    """
    local = {n.name for n in ast.walk(ast.parse(CLI_SRC))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    seen, frontier = set(), set(names)
    for _ in range(depth + 1):
        seen |= frontier
        calls = set()
        for name in frontier:
            if name in local:
                calls |= _calls(_function(name))
        frontier = calls - seen
    return seen


def test_do_segments_reads_the_state_through_store_and_never_opens_it():
    """Acceptance criterion 4, at source level, over the pair and its helpers.

    `do_segments` takes a document that is already loaded, exactly as `do_style`
    and `do_suggest` do, so the half that reaches storage is `cmd_segments` —
    asserting only over the seam would be asserting over a function that touches
    no file at all. Both are read, and so is every `cli.py` function they call.
    """
    called = _reached({"do_segments", "cmd_segments"})
    assert len(called) > 10, (
        "the extractor found almost nothing; it is reading the wrong node")
    assert "load_doc" in called, (
        "`lx segments` must reach the document through `store.load_doc`. The "
        "storage layer is free to change, which is the same reason the "
        "bookshelf consumer may not read inside `.lx/`.")
    assert not (called & _WRITERS), sorted(called & _WRITERS)

    # Docstrings excluded, and that is the whole reason this reads syntax: the
    # prose above is allowed to name `.lx/state.db` — it is explaining why the
    # code may not — and a text search over the function would have caught it.
    literals = {value for fn in ("do_segments", "cmd_segments")
                for value in _string_literals(_function(fn))}
    assert literals, "no string literal at all; the extractor is reading nothing"
    assert not [t for t in literals if "state.db" in t or ".lx" in t]

    imported = {alias.name for node in ast.walk(ast.parse(CLI_SRC))
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(ast.parse(CLI_SRC))
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert "sqlite3" not in imported, (
        "cli.py has grown an sqlite3 import. Invariant 8 puts the storage layer "
        "behind `store.py`; a command reading it directly is what this asserts "
        "against.")


# --- acceptance criterion 5: it writes nothing --------------------------------


def test_segments_writes_nothing_anywhere_in_the_project(tmp_path):
    """Acceptance criterion 5, on the bytes of every file, across many runs.

    The snapshot is taken before the **first** invocation rather than between
    two, which is `tests/test_renderings.py`'s idiom and for its measured
    reason: a snapshot in the middle cannot see a write that is idempotent, and
    a projection that persisted the way `do_check` persists issues would be
    exactly that shape. Running it twice is still asserted — every argument
    shape below runs twice — so a first-read WAL checkpoint could not hide a
    later write either.

    **Every run is a subprocess, and that is load-bearing.** Opening the state
    at all runs `PRAGMA journal_mode=WAL`, which creates the `-wal` and `-shm`
    sidecars — `docs/contracts/workbench-http.md` records this as the one honest
    exception to "no read has a side effect". SQLite removes them when the last
    connection closes, so a process boundary is what makes the tree comparable;
    the same assertion made in-process would be measuring the sidecars rather
    than the command.

    **What is asserted is a clean tree, not an absolute.** Measured 2026-09-08:
    a `.lx/` left holding a *populated* `state.db-wal` — what a killed writer
    leaves — is checkpointed by the first reader, which rewrites `state.db` and
    removes both sidecars. That is `store._connect`'s behaviour and `lx blocks`,
    `lx todo` and `lx check` all share it; no translation and no document
    changes, and the second run changes nothing. The snapshot here is taken
    after a fixture whose every step exited cleanly, which is the state a person
    is in, and the claim this file makes is bounded by that on purpose.

    The project sits one level inside `tmp_path` and the fingerprint covers
    `tmp_path`, so a write to `os.pardir` — the shape that made an earlier
    version of this test pass while the command wrote a JSON file on every run —
    lands inside the snapshot.
    """
    env = _project(tmp_path)
    before = _fingerprint(tmp_path)
    assert before, "the fixture wrote no state, so this would assert nothing"
    assert any(".lx" in name for name in before), \
        "the fixture wrote no `.lx/` state; the read-only claim is untested"
    for args in ([], ["--json"], ["--brief"], ["--ids", "s0003"],
                 ["--origin", "human"], ["--origin", ""], ["--limit", "2"],
                 ["--status", "pending"]):
        for _ in range(2):
            assert _lx(["segments", "book/ch1.md", "--lang", "zh-TW", *args],
                       _proj(tmp_path), env).returncode == 0
    assert _fingerprint(tmp_path) == before


# --- a projection, not a report -----------------------------------------------


def test_segments_never_moves_an_exit_code(tmp_path):
    """`lx audit`'s rule and `lx renderings`', arriving in a third command.

    The fixture's document fails `lx check` — s0004's wording drops a
    placeholder — and this still exits 0, because invariant 10 reserves the exit
    code for `lx check` and a listing that failed the build would make every
    script that reads a document also refuse to run.
    """
    env = _project(tmp_path)
    assert _lx(["check", "book/ch1.md", "--lang", "zh-TW"],
               _proj(tmp_path), env).returncode == 1
    for args in ([], ["--json"], ["--ids", "nosuchid"], ["--status", "pending"]):
        assert _lx(["segments", "book/ch1.md", "--lang", "zh-TW", *args],
                   _proj(tmp_path), env).returncode == 0


def test_segments_carries_no_findings_of_any_kind(tmp_path):
    """It detects nothing, so it reports nothing — including what is on the row.

    `lx check --json` persists its issues onto the segment body, so the text of
    the last run's findings is sitting right there in the state. Carrying it
    would make this a report whose severities nobody had recomputed, and stale
    the moment a reviewer edits the wording.
    """
    env = _project(tmp_path)
    assert _lx(["check", "book/ch1.md", "--lang", "zh-TW"],
               _proj(tmp_path), env).returncode == 1
    report = _report(tmp_path, env)
    assert "issues" not in json.dumps(report)
    assert not {"errors", "warnings", "by_rule", "report"} & set(report)


def test_segments_lists_a_held_segment_like_any_other(tmp_path):
    """A hold takes a segment out of the queues, not out of view.

    `checks.workable` is the one exclusion every work-selecting predicate
    applies, and a reading command is not one of them — a reviewer holds a
    segment precisely because it is theirs to finish, so hiding it from the
    command they would finish it in inverts the feature.
    """
    env = _project(tmp_path)
    rows = _rows(_report(tmp_path, env))
    assert "s0003" in rows and rows["s0003"]["review"] == "held"
    assert rows["s0003"]["source"] and rows["s0003"]["target"]


def test_segments_is_in_document_order_and_sorts_by_nothing_else(tmp_path):
    """Ordering by suspicion is what a report does; this has none to order by.

    Pinned against status and origin both, because either would be a plausible
    "helpful" sort and both would put the fifth paragraph somewhere other than
    fifth.
    """
    env = _project(tmp_path)
    ids = [row["id"] for row in _report(tmp_path, env)["segments"]]
    assert ids == ["s0001", "s0002", "s0003", "s0004", "s0005"]
    kinds = [row["kind"] for row in _report(tmp_path, env)["segments"]]
    assert kinds[0] == "heading" and set(kinds[1:]) == {"para"}


# --- the filters --------------------------------------------------------------


def test_segments_filters_narrow_the_rows_and_never_the_total(tmp_path):
    """A filtered listing is otherwise indistinguishable from a complete one.

    `total` is the document and the row count is what came back, which is the
    rule `do_suggest` states for the three numbers that travel with every answer
    it gives. The request is echoed for the same reason.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env, "--origin", "llm:draft")
    assert report["total"] == 5
    assert [r["id"] for r in report["segments"]] == ["s0001", "s0002"]
    assert report["filters"] == {"ids": None, "origin": ["llm:draft"],
                                 "status": None, "limit": 0}

    both = _report(tmp_path, env, "--origin", "llm:draft,human")
    assert [r["id"] for r in both["segments"]] == ["s0001", "s0002", "s0003",
                                                   "s0004"]


def test_segments_matches_an_origin_exactly_and_never_by_prefix(tmp_path):
    """`llm` covering `llm:draft` would also cover an origin a later build invents.

    The maintainer's own case is the other half of it: "the `llm:polish` ones
    were re-run" is a request to see `llm:draft` and `human` and *not* a family,
    which a prefix reading cannot express at all.
    """
    env = _project(tmp_path)
    assert _report(tmp_path, env, "--origin", "llm")["segments"] == []
    assert _report(tmp_path, env, "--origin", "hum")["segments"] == []
    assert len(_report(tmp_path, env, "--origin", "llm:draft")["segments"]) == 2


def test_segments_selects_by_status_and_by_named_ids(tmp_path):
    """The two narrowings a novel needs, and the one that reaches the unwritten.

    A segment nothing has written carries no origin at all, so `--origin` cannot
    name that set — `--status pending` is how it is asked for, which is why the
    flag exists beside the other two rather than being left to a consumer's
    filter.
    """
    env = _project(tmp_path)
    assert [r["id"] for r in
            _report(tmp_path, env, "--status", "pending")["segments"]] == ["s0005"]
    assert len(_report(tmp_path, env, "--status", "translated")["segments"]) == 4
    named = _report(tmp_path, env, "--ids", "s0004,s0001")
    assert [r["id"] for r in named["segments"]] == ["s0001", "s0004"]
    assert _report(tmp_path, env, "--ids", "s0001", "--status",
                   "pending")["segments"] == []


def test_segments_refuses_a_shape_it_cannot_read(tmp_path):
    """One sentence and exit 2, which is what every other refusal here answers.

    These are the **seam's** guards and `lx segments` cannot reach them:
    `cmd_segments` always builds a list for `--ids` and `--origin`, and argparse
    owns `--status` through `choices`. They are exercised in process for the
    reader that can reach them — a later endpoint over this seam, which is the
    whole reason `do_segments` is one. A bare string read one character at a
    time would select nothing while looking like it worked, which is the defect
    `_named_segments` already refuses and divergence (28) records the cost of.
    The one exit code asserted below is argparse's, and it is the same 2.
    """
    from scriptorium.cli import UnusableTarget, do_segments

    env = _project(tmp_path)
    doc = {"source": "book/ch1.md", "lang": "zh-TW", "tone": "literary",
           "segments": []}
    for kwargs in ({"ids": "s0001"}, {"origins": "human"}, {"origins": 3},
                   {"status": "nope"}, {"status": "held"}):
        try:
            do_segments(doc, **kwargs)
        except UnusableTarget as exc:
            assert str(exc).strip()
        else:
            raise AssertionError(f"do_segments accepted {kwargs}")
    # argparse answers the flag's own vocabulary before the seam is reached, and
    # the exit code is the same 2 either way.
    bad = _lx(["segments", "book/ch1.md", "--lang", "zh-TW", "--status", "held"],
              _proj(tmp_path), env)
    assert bad.returncode == 2


# --- the terminal form --------------------------------------------------------


def _text(tmp_path, env, *args):
    result = _lx(["segments", "book/ch1.md", "--lang", "zh-TW", *args],
                 _proj(tmp_path), env)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8")


def test_segments_prints_the_whole_text_and_brief_is_what_cuts_it(tmp_path):
    """The decision the package expected to go the other way.

    Over a frozen corpus — tracked Markdown at `5b7b498`, 56 files, 2311
    paragraphs — the median is 380 characters and 88.5% are longer than 88, so a
    cutting default would hide most of the words of seven paragraphs in eight,
    from the one command that exists because a reviewer cannot see a translation
    beside its source. The corpus and the reason it is pinned to a revision are
    in `docs/decisions.md`.
    """
    env = _project(tmp_path)
    whole = "Eleanor kept the accounts, and `ledger.md` was where she kept them."
    assert whole in _text(tmp_path, env)
    assert whole not in _text(tmp_path, env, "--brief")
    assert "…" in _text(tmp_path, env, "--brief")
    # --json is never truncated, which is `lx untracked`'s rule for `--max`.
    assert whole in _text(tmp_path, env, "--brief", "--json")


def test_segments_prints_the_masked_line_only_where_it_differs(tmp_path):
    """Three lines a segment for a novel is noise; one where it matters is not.

    Almost no paragraph of prose carries markup, so a permanent `masked` line
    would be the source repeated under a second label on nearly every row. The
    `--json` form carries it unconditionally all the same — a consumer must not
    have to tell "no markup" from "an older build".
    """
    env = _project(tmp_path)
    text = _text(tmp_path, env, "--ids", "s0002")
    assert "source :" in text and "masked :" not in text
    assert "masked :" in _text(tmp_path, env, "--ids", "s0003")


def test_segments_names_a_segment_nobody_has_translated(tmp_path):
    """An empty target is a state, and a blank line beside `target :` is not it.

    The one place this command puts a word of its own on a row, and it is a
    description of an absence rather than a finding about one.
    """
    env = _project(tmp_path)
    assert "(untranslated)" in _text(tmp_path, env, "--ids", "s0005")
    assert "(untranslated)" not in _text(tmp_path, env, "--ids", "s0002")


def test_segments_marks_the_origin_the_hold_and_the_waiver_on_the_row(tmp_path):
    """They decide the remedy, which is `cmd_audit`'s line for the same fields.

    A `human` origin is refused to every model write and a hold is a reviewer's
    own mark that the segment is theirs to finish, so a reader deciding what to
    do about a row needs both without opening a second command.
    """
    env = _project(tmp_path)
    lines = _text(tmp_path, env).splitlines()
    row = next(line for line in lines if line.strip().startswith("s0003"))
    assert "human" in row and "held" in row
    waived = next(line for line in lines if line.strip().startswith("s0004"))
    assert "waived" in waived
    pending = next(line for line in lines if line.strip().startswith("s0005"))
    assert "pending" in pending and pending == pending.rstrip()


# --- the filters the review round added ---------------------------------------


def test_segments_selects_the_segments_nothing_has_written(tmp_path):
    """`--origin ""` is a value, not an absent flag.

    Read by truthiness it *was* the absent flag, so the request a reader types
    for "the ones nothing has written" came back with the whole document — the
    complement of what was asked, on a listing that had no bound either. This
    repository names the mirror of that defect over and over ("selects nothing
    while looking like it worked"); this is the same shape pointing the other
    way, and the flag is read by presence now.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env, "--origin", "")
    assert [r["id"] for r in report["segments"]] == ["s0005"]
    assert report["filters"]["origin"] == [""]
    # Empty fields are kept for `--origin` and dropped for `--ids`, because no
    # segment has an empty id and there is nothing for one to select.
    assert [r["id"] for r in
            _report(tmp_path, env, "--origin", "human,")["segments"]] == [
        "s0003", "s0004", "s0005"]
    assert _report(tmp_path, env, "--ids", ",,")["segments"] == []


def test_segments_matches_an_origin_it_printed_itself(tmp_path):
    """The blanks are trimmed on both sides, or the listing cannot be re-queried.

    `lx apply --origin` stores free text verbatim, so a stored `"  human  "` is
    a value this command prints and a reader copies. Trimming only the request
    made both the copied spelling and the obvious one answer zero rows and exit
    0 — the failure `--status` was given an explicit refusal to prevent, for a
    value that is not even misspelled.
    """
    env = _project(tmp_path)
    _apply(tmp_path, env, "spaced.json", {"s0005": "還沒有人翻過的一段。"},
           "  human  ")
    assert _rows(_report(tmp_path, env))["s0005"]["origin"] == "  human  "
    for asked in ("  human  ", "human", " human"):
        got = [r["id"] for r in _report(tmp_path, env, "--origin", asked)["segments"]]
        assert "s0005" in got, asked


def test_segments_limit_bounds_the_rows_after_the_filters(tmp_path):
    """A display bound, taken after the narrowing — `do_select`'s rule.

    Applied before, a run of rows the filters exclude would eat the bound and
    hand back fewer than were asked for. The reader this exists for is the agent
    HANDOFF-052 names beside the person: a person pipes a novel to a pager and
    an agent cannot.
    """
    env = _project(tmp_path)
    assert [r["id"] for r in _report(tmp_path, env, "--limit", "2")["segments"]] == [
        "s0001", "s0002"]
    bounded = _report(tmp_path, env, "--origin", "human", "--limit", "2")
    assert [r["id"] for r in bounded["segments"]] == ["s0003", "s0004"]
    assert bounded["total"] == 5 and bounded["filters"]["limit"] == 2
    # `checked_limit`'s rule, reached through this flag like every other bound:
    # a negative slice is everything except the last N, not a smaller listing.
    assert _lx(["segments", "book/ch1.md", "--lang", "zh-TW", "--limit", "-1"],
               _proj(tmp_path), env).returncode == 2


def test_segments_names_an_id_that_matched_nothing(tmp_path):
    """`_named_segments` leaves this to the caller, and a shorter listing is not it.

    An id naming nothing comes back as nothing, so a typo reads as a fact about
    the document. Both forms say which ids reached nothing rather than letting
    the row count carry it.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env, "--ids", "s0001,s9999")
    assert report["unknown"] == ["s9999"]
    assert [r["id"] for r in report["segments"]] == ["s0001"]
    assert _report(tmp_path, env)["unknown"] == []
    text = _text(tmp_path, env, "--ids", "s0001,s9999")
    assert "s9999" in text and "named nothing" in text


# --- the wording that speaks another numbering --------------------------------


#: One paragraph naming two things, only one of them protected at a time.
#: Translating it under the first `config/dnt.txt` and re-extracting under the
#: second moves the numbering out from under the stored wording, which `store`
#: records as `target_slots` — the shape `docs/decisions.md` measured on
#: 2026-09-01, where the render delivered the wrong entity with `lx check` green.
STRANDED_DOC = b"The mill at Ashcombe burned in the spring of that year.\n"


def _stranded_project(tmp_path):
    """One segment carrying `target_slots`, built the way a dnt edit builds one."""
    root = _proj(tmp_path)
    (root / "book").mkdir(parents=True)
    env = _env()
    assert _lx(["init"], root, env).returncode == 0
    (root / "book" / "ch1.md").write_bytes(STRANDED_DOC)
    (root / "config" / "dnt.txt").write_text("mill\n", encoding="utf-8")
    assert _lx(["extract", "book/ch1.md", "--lang", "zh-TW", "--tone", "literary"],
               root, env).returncode == 0
    (root / "t.json").write_text(
        json.dumps({"s0001": "⟦1⟧ 在那年春天燒掉了。"}, ensure_ascii=False),
        encoding="utf-8")
    assert _lx(["apply", "book/ch1.md", "--lang", "zh-TW", "--file", "t.json",
                "--origin", "human"], root, env).returncode == 0
    (root / "config" / "dnt.txt").write_text("Ashcombe\n", encoding="utf-8")
    assert _lx(["extract", "book/ch1.md", "--lang", "zh-TW"],
               root, env).returncode == 0
    return env


def test_segments_marks_a_wording_whose_placeholders_speak_another_numbering(tmp_path):
    """The one row this command would otherwise lie about.

    `masked` is the line whose `⟦n⟧` a reviewer compares against the target's —
    and on a segment carrying `target_slots` the two mean different maps, so
    they line up on the page and not in the document. Here `masked`'s `⟦1⟧` is
    `Ashcombe` and the target's is `mill`; the render substitutes `mill` and is
    right, and it is the reader of these two lines who is misled. `lx check`
    reports it only at warn, so exit 0 is reachable over the file.
    """
    env = _stranded_project(tmp_path)
    row = _rows(_report(tmp_path, env))["s0001"]
    assert row["stranded"] is True
    assert "⟦1⟧" in row["masked"] and "mill" in row["masked"]
    assert "Ashcombe" not in row["masked"]
    assert "⟦1⟧" in row["target"]


def test_segments_prints_the_stranded_mark_and_says_what_to_do(tmp_path):
    """`do_commit` already treats this as a state with a remedy of its own.

    Re-word the segment: that is what clears it, and this row is where a
    reviewer would have to be told. The mark is bracketed like `held` and
    `waived`, because `origin` is free text and `lx apply --origin stranded` is
    a string somebody can store beside it.
    """
    env = _stranded_project(tmp_path)
    text = _text(tmp_path, env)
    assert "[stranded]" in text
    assert "re-word" in text


def test_segments_leaves_an_ordinary_row_unstranded(tmp_path):
    """The other half, so the flag is not simply always true.

    A guard that fires on every row is indistinguishable from one that fires on
    the right ones, and the fixture above is the only document in this file that
    can produce a `target_slots` map at all.
    """
    env = _project(tmp_path)
    assert all(row["stranded"] is False
               for row in _report(tmp_path, env)["segments"])
    assert "[stranded]" not in _text(tmp_path, env)
