"""`lx status --json` is written down, and these tests keep it true.

`docs/contracts/status-json.md` is the frozen contract. The lesson
`tests/test_contract.py` records for the sibling surface is the lesson here:
nothing in this file reads the document for information. Everything is a
comparison — the field names of every shape in both directions, the three places
the version is declared, the discovery rule against a real directory tree, and
the things that are true only because something is absent and are therefore
invisible in a diff.

The field tables are parsed out of the markdown and compared against a **live
reply**, because that is the check the sibling's adversarial pass found mattered:
the version number and a section list were easy and were not where the drift
would have been. A key renamed in `cli.py` and not in the document, or the
reverse, fails here rather than at a consumer.

Every project fixture is built two levels inside `tmp_path` for the reason
`test_contract.py` gives — one level down is pytest's shared base, which it never
cleans, so one run of broken code would fail this file forever afterwards.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium  # noqa: E402
import scriptorium.cli as cli  # noqa: E402
import scriptorium.store as store  # noqa: E402
import statedb  # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")
CONTRACT = os.path.join(_ROOT, "docs", "contracts", "status-json.md")

DOC = "# Title\n\nA first sentence.\n\nA second sentence.\n"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sections():
    """`{heading: body}` for every `##`/`###` section, each body ending at the next.

    Both levels in one flat map, so `## The response`'s own body stops at
    `### project` rather than swallowing every subsection's table — which is the
    same terminator problem `_documented_response_keys` solves in the sibling
    file, and the same way it would quietly pass on a superset if it were missed.
    """
    text = _read(CONTRACT)
    parts = re.split(r"^#{2,3} (.+)$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _table_keys(body):
    """The `` `key` `` column of the first markdown table in `body`."""
    return set(re.findall(r"^\| `(\w+)` \|", body, flags=re.M))


def _documented(heading):
    body = _sections()[heading]
    # A `###` inside a `##` body cannot appear — `_sections` splits on both — so
    # the only thing to stop at is a second table under one heading. There is
    # none today; the assertion is what says so if one is ever added.
    keys = _table_keys(body)
    assert keys, f"the {heading!r} section of {CONTRACT} documents no keys"
    return keys


def _lx(capsys, *args):
    """One real invocation through `main`, returning its parsed stdout.

    Through `main` rather than `do_status` so that the flag names, the argparse
    wiring, the JSON serialization and the exit path are all part of what is
    pinned — a contract a consumer reaches by typing a command is not frozen by
    testing the function behind it.
    """
    cli.main(list(args))
    return json.loads(capsys.readouterr().out)


def _status(capsys, *args):
    return _lx(capsys, "status", "--json", *args)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "nest" / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text(DOC, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def extracted(project, capsys):
    """A project with state, two targets written by hand, one of them held.

    Held after the save, because holding refuses a segment with no target — the
    order here is the order a reviewer works in, not an arrangement.
    """
    cli.main(["init"])
    cli.main(["extract", "docs/guide.md", "--lang", "zh-TW"])
    (project / "in.json").write_text(
        json.dumps({"s0001": "標題", "s0002": "第一句。"}, ensure_ascii=False),
        encoding="utf-8")
    cli.main(["apply", "docs/guide.md", "--lang", "zh-TW",
              "--file", "in.json", "--origin", "human"])
    cli.main(["hold", "docs/guide.md", "--lang", "zh-TW", "--ids", "s0002"])
    with pytest.raises(SystemExit):  # one segment is still untranslated
        cli.main(["check", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    return project


def _extract_into(root, name, text, lang="zh-TW"):
    """Give `root` a document with content, from outside it.

    `do_status` reads a project by standing in it, so a scanned fixture built
    from the test's own working directory has to be built the same way.
    """
    here = os.getcwd()
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / name).write_text(text, encoding="utf-8")
    os.chdir(root)
    try:
        cli.main(["extract", f"docs/{name}", "--lang", lang])
    finally:
        os.chdir(here)


def _mark(path, marker):
    """Make `path` a project by way of exactly one marker."""
    path.mkdir(parents=True, exist_ok=True)
    if marker == ".lx":
        (path / ".lx").mkdir()
    else:
        (path / "lx.config.json").write_text("{}", encoding="utf-8")
    return path


# ── the version, in the three places it is written ─────────────────────────

def test_the_document_declares_exactly_one_contract_version_and_the_module_agrees():
    declared = re.findall(r"^contract_version = (\d+)$", _read(CONTRACT), re.M)
    assert declared == [str(cli.STATUS_CONTRACT_VERSION)], (
        f"{CONTRACT} declares {declared} and cli.STATUS_CONTRACT_VERSION is "
        f"{cli.STATUS_CONTRACT_VERSION}. There is one version and it is written in "
        f"both places.")


def test_the_response_table_declares_the_same_version():
    """The number is in the document twice, and the fenced regex sees one of them.

    The other is the response table's own cell, which is what an implementer
    reads to learn which integer the command will send — a second declaration,
    not prose about one. Pinned because on 2026-08-19 an adversarial pass over
    the sibling contract reverted exactly that cell and left the whole suite
    green while the document disagreed with itself and with the code.
    """
    cell = re.findall(r"^\| `contract_version` \| integer \|[^|]*?`(\d+)`",
                      _read(CONTRACT), re.M)
    assert cell == [str(cli.STATUS_CONTRACT_VERSION)], (
        f"{CONTRACT}'s response table says {cell}; the fenced declaration and "
        f"cli.STATUS_CONTRACT_VERSION say {cli.STATUS_CONTRACT_VERSION}.")


def test_the_command_reports_the_contract_version(project, capsys):
    status = _status(capsys)
    assert status["contract_version"] == cli.STATUS_CONTRACT_VERSION
    assert isinstance(status["contract_version"], int)
    # Separate fields, separate questions, separate sources. A consumer that read
    # `version` as the contract version would see it move on a release that
    # changed nothing here.
    assert status["version"] == scriptorium.__version__


def test_the_two_contracts_carry_two_independent_versions():
    """Reading one surface's integer as the other's is the misread this prevents.

    Not an assertion that the numbers differ — they may coincide — but that they
    are two constants in two modules, and that this document says which other
    one it is not. The pair were read as one contract during triage on
    2026-07-29, which is the whole reason both documents carry the paragraph.
    """
    from scriptorium.web import server

    assert server.CONTRACT_VERSION is not None
    assert "STATUS_CONTRACT_VERSION" not in vars(server), (
        "the two versions must not become one constant shared by both surfaces")
    text = _read(CONTRACT)
    assert "web.server.CONTRACT_VERSION" in text, (
        "the document must name the other version it is not")
    assert "cli.STATUS_CONTRACT_VERSION" in text, (
        "the document must name the constant a maintainer has to move with it")


_TYPES = {
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "string | null": lambda v: v is None or isinstance(v, str),
    "array of string": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
}


def _documented_types(heading):
    """`{key: type}` from a section's table, for the types this file can check.

    Composite cells — `array of *project*`, `*rollup*`, `*check* | null` — are
    skipped: their shape is what the key-set comparisons already assert. What is
    covered is every scalar, which is where a silent type change would live.
    """
    rows = re.findall(r"^\| `(\w+)` \| ([^|]+?) \|", _sections()[heading], flags=re.M)
    return {key: kind.strip() for key, kind in rows if kind.strip() in _TYPES}


def _assert_types(heading, obj):
    declared = _documented_types(heading)
    assert declared, f"{heading!r} declares no checkable type"
    for key, kind in sorted(declared.items()):
        assert key in obj, f"{heading}.{key} is documented and not emitted"
        assert _TYPES[kind](obj[key]), (
            f"{heading}.{key} is documented as `{kind}` and came back "
            f"{obj[key]!r} ({type(obj[key]).__name__})")
    return declared


def test_every_value_has_the_type_the_contract_declares(extracted, capsys):
    """The **Type** column, checked rather than read.

    Until 2026-08-19 the tables' types were prose: a mutation round changed
    `state_version` from `integer` to `string` in the document and all 35 tests
    passed. A type change is a `contract_version` bump by this document's own
    rule, and it was the one class of break nothing on either side could see.
    """
    status = _status(capsys)
    covered = {}
    covered["The response"] = _assert_types("The response", status)
    project = status["projects"][0]
    covered["project"] = _assert_types("project", project)
    covered["document"] = _assert_types("document", project["documents"][0])
    covered["check"] = _assert_types("check", project["documents"][0]["check"])
    covered["rollup"] = _assert_types("rollup", project["languages"][0])
    # A floor, so that reformatting a table into cells this parser cannot read
    # fails here rather than silently checking nothing.
    assert sum(len(v) for v in covered.values()) >= 25, covered


def test_a_null_valued_field_still_has_its_declared_type(project, capsys):
    """The nullable half. On a bare project every optional value is at its null."""
    cli.main(["extract", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["check"] is None
    _assert_types("document", document)


# ── the field names, against a live reply ──────────────────────────────────

def test_every_key_the_command_emits_is_documented_and_the_reverse(extracted, capsys):
    """Both directions, for every shape the contract defines.

    Without this, the contract's own definition of a breaking change — renaming
    or removing a key — is the one class of break nothing could see.
    """
    status = _status(capsys)
    assert set(status) == _documented("The response")

    project = status["projects"][0]
    assert set(project) == _documented("project")

    document = project["documents"][0]
    assert set(document) == _documented("document")

    assert set(document["check"]) == _documented("check")

    rollup = _documented("rollup")
    # `lang` is on a language rollup and not on `totals`, and the contract's
    # table says so in prose. Asserted rather than trusted: the two shapes come
    # from one function and a change to it moves both.
    assert set(project["languages"][0]) == rollup
    assert set(project["totals"]) == rollup - {"lang"}


def test_the_documented_values_are_the_values_and_not_only_the_keys(extracted,
                                                                    capsys):
    """Every field pinned to what it actually holds, which the key sets cannot see.

    A mutation round nulled `format`, `tone`, `source` and `source_lang` one at a
    time, and changed `source` to backslash separators — the claim this document
    makes about it explicitly — and every one of those mutants survived the whole
    suite. Names were compared; values never were.
    """
    project = _status(capsys)["projects"][0]
    assert project["name"] == "proj"
    assert project["path"] == os.getcwd()
    assert project["source_lang"] == "en"
    assert project["targets"] == ["zh-TW"]
    assert project["tone"] == "technical"

    document = project["documents"][0]
    assert document["source"] == "docs/guide.md", (
        "`/`-separated on every platform is a claim this contract makes")
    assert os.sep not in document["source"] or os.sep == "/"
    assert document["lang"] == "zh-TW"
    assert document["format"] == "markdown"
    assert document["tone"] == "technical"
    assert document["state_version"] == store.STATE_VERSION
    assert document["output"] == "i18n/zh-TW/docs/guide.md"


def test_path_is_absolute_and_is_not_resolved(tmp_path, monkeypatch, capsys):
    """`abspath`, not `realpath` — the contract distinguishes them on purpose.

    Both mutants survived the full suite: emitting a relative path, and emitting
    a resolved one. The first is worse than it looks, because `do_status` does not
    reset the working directory between projects, so a relative `path` in the
    output is a path the *next* `chdir` would resolve from the wrong place.
    """
    lib = tmp_path / "nest" / "lib"
    _mark(lib / "book", ".lx")
    monkeypatch.chdir(lib)
    for scanned in _status(capsys, "--scan", ".")["projects"]:
        assert os.path.isabs(scanned["path"]), scanned["path"]
    # `.` echoed back exactly as typed, while `path` is resolved to somewhere.
    assert _status(capsys, "--scan", ".")["scanned"] == "."


def test_a_project_that_could_not_be_read_still_carries_every_key(tmp_path, monkeypatch,
                                                                 capsys):
    """The error branch is a different construction path and must not answer a subset.

    A consumer reads the same keys off every entry; one that had to test for a
    key's presence before reading it would be implementing the union of two
    shapes this document never described.
    """
    root = tmp_path / "nest" / "lib"
    broken = root / "broken"
    broken.mkdir(parents=True)
    (broken / "lx.config.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(root)
    status = _status(capsys, "--scan", ".")
    entry = status["projects"][0]
    assert entry["error"], "the fixture did not break the project"
    assert set(entry) == _documented("project")
    assert entry["documents"] == [] and entry["targets"] == []
    assert set(entry["totals"]) == _documented("rollup") - {"lang"}
    assert entry["totals"]["segments"] == 0


def test_the_contract_names_the_cli_function_it_freezes():
    """Invariant 8 made checkable, the way each endpoint's *Backed by* line is.

    The seam is stated rather than merely intended: the document names
    `cli.do_status`, and `cli.do_status` exists.
    """
    assert getattr(cli, "do_status", None) is not None
    assert "cli.do_status" in _read(CONTRACT)


# ── what a project is ──────────────────────────────────────────────────────

def test_a_scan_finds_exactly_the_projects(tmp_path, monkeypatch, capsys):
    """Three projects and two unrelated directories, which is the package's own case.

    One project per marker and one carrying both, so the *or* in the rule is
    exercised rather than assumed; one of them a level deeper, so the walk is
    shown to descend at all.
    """
    lib = tmp_path / "nest" / "lib"
    _mark(lib / "bookA", ".lx")
    _mark(lib / "shelf" / "bookB", "lx.config.json")
    both = _mark(lib / "bookC", ".lx")
    (both / "lx.config.json").write_text("{}", encoding="utf-8")
    (lib / "notaproject" / "docs").mkdir(parents=True)
    (lib / "notaproject" / "docs" / "x.md").write_text("hi\n", encoding="utf-8")
    (lib / "random").mkdir()
    (lib / "random" / "readme.txt").write_text("junk\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    status = _status(capsys, "--scan", str(lib))
    assert {p["name"] for p in status["projects"]} == {"bookA", "bookB", "bookC"}
    # Sorted by `path`, which the contract says and which is **not** sorted by
    # `name`: `lib/bookC` precedes `lib/shelf/bookB`. Asserted as the property
    # rather than as a literal list, so the test states the rule it is checking.
    paths = [p["path"] for p in status["projects"]]
    assert paths == sorted(paths)
    assert status["scanned"] == str(lib)
    assert {p["name"]: p["markers"] for p in status["projects"]} == {
        "bookA": [".lx"], "bookB": ["lx.config.json"], "bookC": [".lx", "lx.config.json"]}


def test_projects_are_sorted_by_path_even_when_the_walk_finds_them_otherwise(
        tmp_path, monkeypatch, capsys):
    """The contract promises sorted by `path`, and most trees cannot tell.

    A depth-first walk in sorted order happens to *produce* sorted paths for
    almost any shape, so `sorted(found)` looked untested — a mutation round
    removed it and every other scan test stayed green. This is a tree where the
    two genuinely differ: `a-b` sorts before `a/z` because `-` (0x2D) is below
    `/` (0x2F), while the walk reaches `a/z` first because it descends into `a`
    before it pops `a-b`.
    """
    lib = tmp_path / "nest" / "lib"
    _mark(lib / "a" / "z", ".lx")
    _mark(lib / "a-b", ".lx")
    monkeypatch.chdir(tmp_path)
    paths = [p["path"] for p in _status(capsys, "--scan", str(lib))["projects"]]
    assert len(paths) == 2, paths
    assert paths == sorted(paths), paths
    assert paths[0].endswith("a-b"), (
        f"discovery order leaked into the output: {paths}")


def test_a_scanned_project_reports_its_real_contents(tmp_path, monkeypatch, capsys):
    """`--scan` with content in it, which nothing asserted until 2026-08-19.

    Every other scan fixture is an empty marker directory or a broken one, so a
    mutation round could make scanned projects report `documents: []`, resolve
    `check` against the wrong working directory, or zero their `totals`, and all
    three survived the **entire** suite. `--scan` is the flag this contract was
    built for and its payload was untested.
    """
    lib = tmp_path / "nest" / "lib"
    one = _mark(lib / "one", ".lx")
    two = _mark(lib / "two", ".lx")
    _extract_into(one, "a.md", "# A\n\nFirst.\n\nSecond.\n")
    _extract_into(two, "b.md", "# B\n\nOnly one.\n")
    capsys.readouterr()

    projects = _status(capsys, "--scan", str(lib))["projects"]
    assert [p["name"] for p in projects] == ["one", "two"]
    assert [p["totals"]["segments"] for p in projects] == [3, 2]
    assert [len(p["documents"]) for p in projects] == [1, 1]
    assert [p["documents"][0]["source"] for p in projects] == ["docs/a.md", "docs/b.md"]
    assert [p["documents"][0]["output"] for p in projects] == [
        "i18n/zh-TW/docs/a.md", "i18n/zh-TW/docs/b.md"]
    assert [p["languages"][0]["lang"] for p in projects] == ["zh-TW", "zh-TW"]
    assert all(p["error"] is None for p in projects)


def test_a_scanned_projects_check_resolves_against_its_own_directory(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """`.lx/reports/` is read relative to the project, not to where `lx` was run.

    The chdir is what makes that true and nothing watched it: a mutant reading
    the report from the invoking directory returned `check: null` for every
    scanned document, which reads exactly like "nobody has checked this".
    """
    lib = tmp_path / "nest" / "lib"
    book = _mark(lib / "book", ".lx")
    _extract_into(book, "a.md", "# A\n\nFirst.\n")
    here = os.getcwd()
    os.chdir(book)
    try:
        with pytest.raises(SystemExit):
            cli.main(["check", "docs/a.md", "--lang", "zh-TW"])
    finally:
        os.chdir(here)
    capsys.readouterr()

    monkeypatch.chdir(tmp_path)
    document = _status(capsys, "--scan", str(lib))["projects"][0]["documents"][0]
    assert document["check"] is not None, (
        "the report was written and the scan did not find it")
    assert document["check"]["errors"] == 2
    assert document["check"]["stale"] is False


def test_the_default_depth_is_three(tmp_path, monkeypatch, capsys):
    """Stated twice in the contract, and only bounded from above until now.

    A mutant lowering the default to 2 survived the whole suite: the existing
    test proves depth 4 finds what 3 does not, and nothing proved 3 finds what 2
    does not.
    """
    lib = tmp_path / "nest" / "lib"
    _mark(lib / "a" / "b" / "three", ".lx")
    monkeypatch.chdir(tmp_path)
    assert cli.SCAN_DEPTH == 3
    found = _status(capsys, "--scan", str(lib))["projects"]
    assert [p["name"] for p in found] == ["three"], (
        "the default must reach a project three levels under the root")
    assert _status(capsys, "--scan", str(lib), "--depth", "2")["projects"] == []


def test_a_marker_of_the_wrong_type_is_not_a_project(tmp_path, monkeypatch, capsys):
    """A *file* named `.lx` and a *directory* named `lx.config.json` are neither."""
    lib = tmp_path / "nest" / "lib"
    (lib / "filelx").mkdir(parents=True)
    (lib / "filelx" / ".lx").write_text("not a directory", encoding="utf-8")
    (lib / "dircfg" / "lx.config.json").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert _status(capsys, "--scan", str(lib))["projects"] == []


def test_the_scan_does_not_descend_into_a_project(tmp_path, monkeypatch, capsys):
    """A project nested inside a project is reported once, as the outer one.

    Every document identity here is a path relative to one working directory, so
    an inner project's state is unreachable from the outer one's — reporting both
    would be reporting a thing this storage cannot express.
    """
    lib = tmp_path / "nest" / "lib"
    outer = _mark(lib / "outer", ".lx")
    _mark(outer / "inner", ".lx")
    monkeypatch.chdir(tmp_path)
    status = _status(capsys, "--scan", str(lib))
    assert [p["name"] for p in status["projects"]] == ["outer"]


def test_the_scan_root_may_itself_be_a_project(tmp_path, monkeypatch, capsys):
    one = _mark(tmp_path / "nest" / "solo", ".lx")
    monkeypatch.chdir(tmp_path)
    status = _status(capsys, "--scan", str(one))
    assert [p["name"] for p in status["projects"]] == ["solo"]


def test_depth_bounds_the_search(tmp_path, monkeypatch, capsys):
    lib = tmp_path / "nest" / "lib"
    _mark(lib / "a" / "b" / "c" / "deep", ".lx")
    monkeypatch.chdir(tmp_path)
    assert _status(capsys, "--scan", str(lib))["projects"] == []
    found = _status(capsys, "--scan", str(lib), "--depth", "4")["projects"]
    assert [p["name"] for p in found] == ["deep"]


def test_a_dotted_directory_is_never_searched(tmp_path, monkeypatch, capsys):
    lib = tmp_path / "nest" / "lib"
    _mark(lib / ".git" / "hidden", ".lx")
    monkeypatch.chdir(tmp_path)
    assert _status(capsys, "--scan", str(lib))["projects"] == []


def test_the_working_directory_is_reported_even_without_a_marker(tmp_path, monkeypatch,
                                                                capsys):
    """"You are not in a project" is an answer, and an empty array is a puzzle."""
    bare = tmp_path / "nest" / "bare"
    bare.mkdir(parents=True)
    monkeypatch.chdir(bare)
    status = _status(capsys)
    assert status["scanned"] is None
    assert len(status["projects"]) == 1
    assert status["projects"][0]["markers"] == []
    assert status["projects"][0]["totals"]["segments"] == 0
    assert status["projects"][0]["error"] is None


def test_a_scan_root_that_is_not_a_directory_is_refused(project, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["status", "--json", "--scan", "no/such/place"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == "", "a refusal must not also write a report"


# ── the counts ─────────────────────────────────────────────────────────────

def test_the_counts_are_what_the_contract_says_they_are(extracted, capsys):
    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["segments"] == 3
    assert document["translated"] == 2
    assert document["pending"] == 1
    assert document["translated"] + document["pending"] == document["segments"]
    # Held is *inside* translated, not beside it: holding requires a non-empty
    # target, so the two are deliberately not disjoint and the contract says so.
    assert document["held"] == 1


def test_a_whitespace_target_is_not_translated(extracted, capsys):
    """The rule is a stripped target, and `lx stats` used to disagree with it.

    Written straight into the database because `do_apply` refuses an empty
    target at the door — this asserts what the *counter* does with a row that
    exists, which is the population the derived-status repair exists for.
    """
    statedb.set_target(extracted, "s0003", "   ")
    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["translated"] == 2, "three spaces is not a translation"
    assert document["pending"] == 1


def test_lang_filters_documents_and_not_targets(extracted, capsys):
    status = _status(capsys, "--lang", "ja-JP")
    project = status["projects"][0]
    assert status["lang"] == "ja-JP"
    assert project["documents"] == []
    assert project["languages"] == []
    # What the project is configured *for*, which is not what it holds.
    assert project["targets"] == ["zh-TW"]
    assert project["totals"]["documents"] == 0


def test_a_rollup_sums_only_the_documents_it_checked(extracted, capsys):
    """`checked` is what keeps "0 errors" from meaning two different things."""
    project = _status(capsys)["projects"][0]
    totals = project["totals"]
    assert totals["documents"] == 1 and totals["checked"] == 1
    assert totals["errors"] == project["documents"][0]["check"]["errors"]
    assert totals["errors"] >= 1, "the fixture leaves one segment untranslated"
    assert project["languages"][0]["lang"] == "zh-TW"
    assert project["languages"][0]["errors"] == totals["errors"]


def test_an_unchecked_document_is_null_and_not_zero(project, capsys):
    cli.main(["extract", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    status = _status(capsys)
    assert status["projects"][0]["documents"][0]["check"] is None
    assert status["projects"][0]["totals"]["checked"] == 0
    assert status["projects"][0]["totals"]["errors"] == 0


def test_stale_fires_when_the_counts_move_under_the_report(extracted, capsys):
    assert _status(capsys)["projects"][0]["documents"][0]["check"]["stale"] is False
    (extracted / "more.json").write_text(
        json.dumps({"s0003": "第二句。"}, ensure_ascii=False), encoding="utf-8")
    cli.main(["apply", "docs/guide.md", "--lang", "zh-TW",
              "--file", "more.json", "--origin", "human"])
    capsys.readouterr()
    assert _status(capsys)["projects"][0]["documents"][0]["check"]["stale"] is True


def test_a_corrupt_check_report_reads_as_an_unchecked_document(extracted, capsys):
    report = extracted / ".lx" / "reports"
    for name in os.listdir(report):
        (report / name).write_text("{ not json", encoding="utf-8")
    assert _status(capsys)["projects"][0]["documents"][0]["check"] is None


# ── one bad project does not end the report ────────────────────────────────

@pytest.mark.parametrize("break_it,fragment", [
    ("config", "line 1"),
    ("database", "not a database"),
    ("schema", "newer scriptorium"),
])
def test_an_unreadable_project_is_reported_beside_the_readable_ones(
        tmp_path, monkeypatch, capsys, break_it, fragment):
    """Three unrelated exception hierarchies, and the listing survives all three.

    This is what the broad `except` in `_project` is for, and it is the rule
    `GET /api/state` already follows for a malformed routing stage: the offending
    entry is usually the one the person most needs to be told about, and a
    traceback is the least useful way to be told it.
    """
    lib = tmp_path / "nest" / "lib"
    good = _mark(lib / "aaa-good", ".lx")
    bad = lib / "zzz-bad"
    bad.mkdir(parents=True)
    if break_it == "config":
        (bad / "lx.config.json").write_text("{not json", encoding="utf-8")
    else:
        (bad / ".lx").mkdir()
        if break_it == "database":
            (bad / ".lx" / "state.db").write_text("nope", encoding="utf-8")
        else:
            statedb.set_schema_version(bad, 99)

    monkeypatch.chdir(tmp_path)
    status = _status(capsys, "--scan", str(lib))
    assert [p["name"] for p in status["projects"]] == ["aaa-good", "zzz-bad"]
    assert status["projects"][0]["error"] is None, str(good)
    assert fragment in status["projects"][1]["error"]


def test_a_document_row_with_no_source_fails_one_project_and_not_the_command(
        tmp_path, monkeypatch, capsys):
    """The blocker the security pass found, pinned as the behaviour that replaced it.

    `store._meta` tolerates a document row whose meta carries no `source`, and
    everything on this surface treats that value as a path — so it reached
    `os.path.relpath(None)`, raised a `TypeError` `main` does not catch, and
    ended the whole command with **no report at all**, taking every healthy
    project in the scan with it. The `try` in `_project` covered the
    configuration read and stopped one line short of the projection, while the
    docstring above it said "Never raises".

    The row here is a legal schema-v1 row in an uncorrupted database, which is
    what makes this a defect rather than a curiosity.
    """
    from scriptorium.store import _SCHEMA

    lib = tmp_path / "nest" / "lib"
    good = _mark(lib / "aaa-good", ".lx")
    bad = lib / "zzz-bad"
    (bad / ".lx").mkdir(parents=True)
    conn = sqlite3.connect(str(bad / ".lx" / "state.db"))
    with conn:
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA user_version = 1")
        conn.execute("INSERT INTO documents VALUES ('d', 'zh-TW', 3, '{}')")
    conn.close()

    monkeypatch.chdir(tmp_path)
    status = _status(capsys, "--scan", str(lib))  # no SystemExit, and a report exists
    assert [p["name"] for p in status["projects"]] == ["aaa-good", "zzz-bad"], str(good)
    assert status["projects"][0]["error"] is None
    assert "carries no source path" in status["projects"][1]["error"]
    # Rebuilt rather than annotated: the failure happens *after* the config read,
    # so a half-filled entry is exactly what this asserts did not survive.
    failed = status["projects"][1]
    assert failed["documents"] == [] and failed["targets"] == []
    assert failed["totals"] == {"documents": 0, "checked": 0, "segments": 0,
                                "translated": 0, "pending": 0, "held": 0,
                                "waived": 0, "errors": 0, "warnings": 0}


@pytest.mark.parametrize("key,value,expected", [
    ("source_lang", {"a": 1}, None),
    ("source_lang", 7, None),
    ("tone", {"api_key_env": "sk-live-SECRET"}, None),
    ("tone", ["literary"], None),
    ("targets", "zh-TW", []),
    ("targets", {"zh-TW": 1}, []),
    ("targets", ["zh-TW", 5, None, "ja-JP"], ["zh-TW", "ja-JP"]),
])
def test_a_configured_value_is_typed_on_the_way_out(project, capsys, key, value,
                                                    expected):
    """`lx.config.json` is hand-edited and nothing validates it on the way in.

    So the types this contract declares are kept on the way out. The `targets`
    row is the one that bites without any security story attached: `list("zh-TW")`
    is `['z', 'h', '-', 'T', 'W']`, so the likeliest typo in that field reported
    five target languages named after its own letters.
    """
    (project / "lx.config.json").write_text(
        json.dumps({key: value}, ensure_ascii=False), encoding="utf-8")
    assert _status(capsys)["projects"][0][key] == expected


def test_the_contract_states_the_narrow_credential_guarantee(extracted, capsys):
    """The claim that was there was refuted in one hand-edit, and the replacement holds.

    Asserted as the positive rather than as the absence of the old sentence,
    which the document quotes on purpose so a reader can see what was withdrawn —
    a literal "this phrase is gone" check would fail on the quotation and teach
    the next person to delete the history. What is checked instead is that the
    guarantee written down is the one the code implements: the fields a
    credential is configured in are never read, and it is not a promise about
    what a hand-edit can put in the fields that are.
    """
    # Whitespace-folded: the claim gets re-wrapped by any edit to the paragraph,
    # and a line-sensitive match would stop seeing it.
    text = " ".join(_read(CONTRACT).split())
    assert "not a promise about their contents" in text
    assert "`providers` and `routing` are never read" in text
    # And the code agrees, which is the half a document cannot assert about itself.
    cfg = json.loads((extracted / "lx.config.json").read_text(encoding="utf-8"))
    assert "providers" in cfg and "routing" in cfg, "the fixture must configure both"
    project = _status(capsys)["projects"][0]
    assert "providers" not in project and "routing" not in project


def test_a_report_full_of_failures_still_exits_zero(tmp_path, monkeypatch, capsys):
    lib = tmp_path / "nest" / "lib"
    bad = lib / "bad"
    bad.mkdir(parents=True)
    (bad / "lx.config.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cli.main(["status", "--json", "--scan", str(lib)])  # no SystemExit
    assert json.loads(capsys.readouterr().out)["projects"][0]["error"]


def test_a_document_this_build_cannot_fully_read_is_still_listed(extracted, capsys):
    """A listing that hid it would be the one place the person could not find out."""
    statedb.set_state_version(extracted, 99)
    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["state_version"] == 99
    assert document["segments"] == 3


# ── the negative space ─────────────────────────────────────────────────────

def test_no_configured_credential_bearing_value_reaches_this_surface(extracted, capsys):
    """Invariant 6, held by carrying nothing rather than by masking something.

    The configuration is doctored to hold every shape a credential hides in — a
    `base_url` with userinfo *and* a query string, an `api_key_env` naming a
    variable that is set, and a verbatim header — and the whole report is then
    searched as text. The lesson of 2026-08-13 is that the enumerated list of
    display surfaces is a symptom of the rule and never its definition, so this
    asserts over the serialized output rather than over the keys somebody
    remembered to check.
    """
    cfg = json.loads((extracted / "lx.config.json").read_text(encoding="utf-8"))
    cfg["providers"]["local"] = {
        "kind": "openai",
        "base_url": "https://someone:hunter2@example.invalid/v1?key=SEKRIT",
        "model": "m", "api_key_env": "LX_TEST_KEY", "headers": {"X-Api-Key": "SEKRIT"},
    }
    (extracted / "lx.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    os.environ["LX_TEST_KEY"] = "SEKRIT"
    try:
        cli.main(["status", "--json"])
        out = capsys.readouterr().out
    finally:
        del os.environ["LX_TEST_KEY"]
    for forbidden in ("hunter2", "SEKRIT", "someone", "X-Api-Key",
                      "example.invalid", "LX_TEST_KEY", "base_url", "api_key_env"):
        assert forbidden not in out, (
            f"{forbidden!r} reached `lx status --json`. This surface carries no "
            f"configuration that could hold a credential — see the contract's "
            f"*Deliberately not in the contract*.")


def test_no_segment_text_reaches_this_surface(extracted, capsys):
    """A status is counts. The words are the workbench contract's, not this one's."""
    capsys.readouterr()
    cli.main(["status", "--json"])
    out = capsys.readouterr().out
    for forbidden in ("A first sentence", "A second sentence", "標題", "第一句",
                      "no translation"):
        assert forbidden not in out, f"{forbidden!r} reached `lx status --json`"


def test_no_path_inside_the_state_directory_reaches_a_structured_field(extracted,
                                                                       capsys):
    """The narrowed claim, and the exception it was narrowed for.

    Written as an absolute until 2026-08-19, when the adversarial pass refuted it
    with one `PRAGMA`: `error` carries whatever the failure said, and a newer
    schema names `.lx/state.db` twice. The old assertion could not see it,
    because it ran on the healthy fixture where `error` is `None` — its oracle
    sat downstream of the only branch that can violate the claim.
    """
    status = _status(capsys)
    text = json.dumps(status, ensure_ascii=False)
    assert "state.db" not in text
    assert "reports" not in text
    # `.lx` appears exactly where the contract says it does — the discovery
    # marker — and nowhere else.
    # Against a literal, not against the same object's own markers: both sides
    # came from `status`, so emptying `markers` made it `0 == 0` and the
    # assertion could not fail. The fixture builds exactly one project and
    # `lx init` writes `.lx/`, so the number is 1.
    assert [p["markers"] for p in status["projects"]] == [[".lx", "lx.config.json"]]
    assert text.count(".lx") == 1

    # And now the branch the claim had to be narrowed for. `error` may name it;
    # every *structured* field still may not.
    statedb.set_schema_version(extracted, 99)
    broken = _status(capsys)["projects"][0]
    assert "state.db" in broken["error"], (
        "the fixture no longer produces the message the narrowing is about")
    assert broken["documents"] == [] and broken["markers"] == [".lx",
                                                               "lx.config.json"]
    assert "state.db" not in json.dumps(
        {k: v for k, v in broken.items() if k != "error"}, ensure_ascii=False)


def test_a_fresh_report_is_not_stale_on_a_whitespace_target(extracted, capsys):
    """The permanent-stale defect, pinned as the behaviour that replaced it.

    `cli.do_check` writes its report's `translated` with no `.strip()` and this
    surface counts with one, so comparing the two made `stale` true on a report
    written one second earlier — and re-running `lx check` could never clear it,
    because on such a row the two numbers disagree by construction. Staleness is
    now compared in the report's own arithmetic.
    """
    statedb.set_target(extracted, "s0003", "   ")
    with pytest.raises(SystemExit):
        cli.main(["check", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["translated"] == 2, "the stripped count is this surface's"
    assert document["check"]["stale"] is False, (
        "a report written moments ago describes this document")


def test_the_three_translated_counters_disagree_as_the_contract_records(extracted,
                                                                       capsys):
    """Divergence (3), pinned as it is rather than as anyone would like it.

    Three counters of "translated" exist and only this surface strips. The entry
    claimed they agreed until an adversarial pass put both surfaces on one
    project and got `done: 1` against `translated: 0`. Closing it means editing
    `web/server.py` and a report shape the workbench contract freezes, so it is
    recorded — and pinned here, so that whoever does close it finds this test and
    the entry rather than only the code.
    """
    statedb.set_target(extracted, "s0003", "   ")
    with pytest.raises(SystemExit):
        cli.main(["check", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    here = _status(capsys)["projects"][0]["documents"][0]["translated"]
    report = json.loads(
        (extracted / ".lx" / "reports" / os.listdir(extracted / ".lx" / "reports")[0])
        .read_text(encoding="utf-8"))
    assert here == 2 and report["translated"] == 3, (
        f"this surface says {here} and `lx check`'s report says "
        f"{report['translated']}; if these now agree, divergence (3) has been "
        f"closed and the contract has to say so.")
    assert "The predicates genuinely differ" in _read(CONTRACT)


def test_untracked_names_the_books_nobody_has_started(project, capsys):
    """The field HANDOFF-203 was told to spell `untracked`, and the reason.

    `/api/state` renamed `candidates` to `untracked` on 2026-08-14 "so the
    command, the response key and HANDOFF-203's forthcoming field agree" — this
    is that field. `cli.do_untracked` decides it, so the three cannot drift.
    """
    (project / "docs" / "started.md").write_text(DOC, encoding="utf-8")
    (project / "docs" / "waiting.md").write_text(DOC, encoding="utf-8")
    cli.main(["extract", "docs/started.md", "--lang", "zh-TW"])
    capsys.readouterr()

    entry = _status(capsys)["projects"][0]
    assert [d["source"] for d in entry["documents"]] == ["docs/started.md"]
    # `docs/guide.md` is the fixture's own document and is untracked too, so the
    # offer is both un-extracted files and not the extracted one.
    assert entry["untracked"] == [{"source": "docs/guide.md", "lang": "zh-TW"},
                                  {"source": "docs/waiting.md", "lang": "zh-TW"}], (
        "the extracted document must not be offered and the others must be")
    # And it is the same answer `lx untracked` gives, which is the point of
    # routing both through one function.
    cli.main(["untracked", "--json"])
    assert json.loads(capsys.readouterr().out)["untracked"] == entry["untracked"]


def test_untracked_subtracts_a_document_tracked_in_another_language(project, capsys):
    """The unfiltered read, which is why `tracked()` is called without `lang`.

    `do_untracked` subtracts over (identity, language) pairs, so handing it a
    `--lang`-filtered list makes it offer a document that is tracked — just not
    in the language being reported.
    """
    (project / "lx.config.json").write_text(
        json.dumps({"targets": ["zh-TW", "ja-JP"], "sources": ["docs/**/*.md"]}),
        encoding="utf-8")
    cli.main(["extract", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()

    both = _status(capsys)["projects"][0]["untracked"]
    assert both == [{"source": "docs/guide.md", "lang": "ja-JP"}], both
    # Under `--lang zh-TW` the document is tracked and nothing is offered — the
    # subtraction still saw the zh-TW row even though `documents` filtered to it.
    assert _status(capsys, "--lang", "zh-TW")["projects"][0]["untracked"] == []
    assert _status(capsys, "--lang", "ja-JP")["projects"][0]["untracked"] == both


def test_lx_stats_does_not_glob_for_untracked_files_either(extracted, capsys):
    """`detail=False` covers both projections `lx stats` never prints."""
    calls = []
    real = cli.do_untracked

    def spy(cfg, docs=None):
        calls.append(1)
        return real(cfg, docs)

    cli.do_untracked = spy
    try:
        cli.main(["stats"])
        capsys.readouterr()
        assert calls == [], "lx stats globbed the source tree for output it discards"
        cli.main(["status", "--json"])
        capsys.readouterr()
        assert calls == [1], "lx status must carry `untracked`"
    finally:
        cli.do_untracked = real


def test_output_says_where_lx_render_actually_writes(extracted, capsys):
    """The loop from a status entry to readable text, closed and kept closed.

    The bookshelf is also the reader, and until 2026-08-19 this surface gave it
    a document's `source` and no supported way to find the translated file: the
    path comes from `output_pattern`, which is per-project configuration this
    contract did not report. Its three options were a hard-coded default that
    breaks silently, reading `lx.config.json` — the storage coupling the red line
    exists to stop — and shelling out to `lx render`, which violates "this
    contract and nothing else".

    Asserted against a **non-default** pattern, because the default and a
    hard-coded guess are the same string and a test using it would pass for the
    wrong reason.
    """
    cfg = json.loads((extracted / "lx.config.json").read_text(encoding="utf-8"))
    cfg["output_pattern"] = "out/{lang}/renamed-{name}"
    (extracted / "lx.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["output"] == "out/zh-TW/renamed-guide.md"

    # And `lx render` really writes there, or the two disagree about a path the
    # consumer then opens.
    cli.main(["render", "docs/guide.md", "--lang", "zh-TW", "--fallback"])
    capsys.readouterr()
    assert (extracted / "out" / "zh-TW" / "renamed-guide.md").exists()


def test_an_unformattable_output_pattern_costs_one_field_and_not_the_project(
        extracted, capsys):
    """A mistyped pattern still reports its counts."""
    cfg = json.loads((extracted / "lx.config.json").read_text(encoding="utf-8"))
    cfg["output_pattern"] = "out/{nosuchkey}/{path}"
    (extracted / "lx.config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    project = _status(capsys)["projects"][0]
    assert project["error"] is None
    assert project["documents"][0]["output"] is None
    assert project["documents"][0]["segments"] == 3


def test_markers_survives_an_error_and_the_contract_says_so(extracted, capsys):
    """The one list that stays populated when `error` is set, and why.

    "Every list is empty" was written as an absolute and `_empty_project` calls
    `project_markers`, so the sentence was false about the field that exists to
    diagnose exactly this. The behaviour is right — a person asking why a
    directory failed wants to know which marker made it a project — so the
    sentence was narrowed rather than the code.
    """
    statedb.set_schema_version(extracted, 99)
    entry = _status(capsys)["projects"][0]
    assert entry["error"]
    assert entry["markers"] == [".lx", "lx.config.json"]
    assert entry["targets"] == [] and entry["source_lang"] is None
    assert entry["tone"] is None and entry["documents"] == []
    assert "every list except `markers` is empty" in " ".join(_read(CONTRACT).split())


def test_totals_under_a_lang_filter_is_the_filtered_set(project, capsys):
    """The contract said "every document in the project" and meant this report.

    A consumer implementing the sentence literally shows one language's progress
    as the whole book's.
    """
    cli.main(["extract", "docs/guide.md", "--lang", "zh-TW"])
    cli.main(["extract", "docs/guide.md", "--lang", "ja-JP"])
    capsys.readouterr()
    everything = _status(capsys)["projects"][0]
    assert everything["totals"]["documents"] == 2
    filtered = _status(capsys, "--lang", "ja-JP")["projects"][0]
    assert filtered["totals"]["documents"] == 1
    assert filtered["totals"]["segments"] == everything["totals"]["segments"] // 2
    assert "in this report" in " ".join(_read(CONTRACT).split())


def test_a_green_check_is_not_a_pass_claim(extracted, capsys):
    """Invariant 10 through the second door, and the door is documented now.

    A target edited in place moves neither count, so `stale` stays `false` and
    the errors stay whatever the last check found — while `lx check` now fails.
    The contract has to say that a green reading is history, because a client
    that draws a green light is making a claim this project never made.
    """
    (extracted / "fix.json").write_text(
        json.dumps({"s0003": "第二句。"}, ensure_ascii=False), encoding="utf-8")
    cli.main(["apply", "docs/guide.md", "--lang", "zh-TW",
              "--file", "fix.json", "--origin", "human"])
    cli.main(["unhold", "docs/guide.md", "--lang", "zh-TW", "--ids", "s0002"])
    with pytest.raises(SystemExit) as ok:
        cli.main(["check", "docs/guide.md", "--lang", "zh-TW"])
    assert ok.value.code == 0, "the fixture must reach a green check first"
    capsys.readouterr()
    green = _status(capsys)["projects"][0]["documents"][0]
    assert green["check"] == {"errors": 0, "warnings": 0, "stale": False}

    # Now break one target in place. Neither count moves.
    statedb.set_target(extracted, "s0002", "第一句 ⟦7⟧。")
    after = _status(capsys)["projects"][0]["documents"][0]
    assert after["check"] == {"errors": 0, "warnings": 0, "stale": False}, (
        "the surface still reports the old green reading, which is the point")
    with pytest.raises(SystemExit) as exc:
        cli.main(["check", "docs/guide.md", "--lang", "zh-TW"])
    assert exc.value.code == 1, "lx check must now fail, or this proves nothing"
    assert "nothing on this surface is a claim that a document passes" in (
        " ".join(_read(CONTRACT).split()).lower())


def test_a_directory_with_no_configuration_reports_the_defaults(tmp_path, monkeypatch,
                                                                capsys):
    """`load_config` layers over built-in defaults, so these are never absent.

    The contract calls them *effective* rather than *configured* for this reason:
    a bare directory reports `zh-TW` because that is this build's default, and a
    consumer reading it as "somebody chose Traditional Chinese for this book"
    would be wrong about every project that has never been configured.
    """
    bare = tmp_path / "nest" / "bare"
    bare.mkdir(parents=True)
    monkeypatch.chdir(bare)
    project = _status(capsys)["projects"][0]
    assert project["markers"] == [] and project["error"] is None
    assert project["targets"] and project["source_lang"] and project["tone"]
    assert "**effective**" in _read(CONTRACT)


def test_errors_and_warnings_are_not_interchangeable(extracted, capsys):
    """Two counts that differ, so swapping the fields is visible.

    The shared fixture produces one error and one warning, so a mutation round
    swapped them in `_check` and again in `_rollup` and both survived every test
    in this file. Lifting the hold removes the warning and leaves the error.
    """
    cli.main(["unhold", "docs/guide.md", "--lang", "zh-TW", "--ids", "s0002"])
    with pytest.raises(SystemExit):
        cli.main(["check", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    project = _status(capsys)["projects"][0]
    assert project["documents"][0]["check"] == {"errors": 1, "warnings": 0,
                                                "stale": False}
    assert project["totals"]["errors"] == 1
    assert project["totals"]["warnings"] == 0
    assert project["languages"][0]["errors"] == 1
    assert project["languages"][0]["warnings"] == 0


def test_held_survives_the_rollup(extracted, capsys):
    """`held` is summed, and the word `warnings` now appears in an assertion.

    Three of the nine rollup keys were ever asserted. Dropping `held` from the
    accumulator, and dropping the `warnings` summation, both survived the full
    suite while the fixture genuinely had one of each.
    """
    project = _status(capsys)["projects"][0]
    assert project["documents"][0]["held"] == 1
    assert project["totals"]["held"] == 1
    assert project["languages"][0]["held"] == 1
    assert project["totals"]["warnings"] == 1
    assert project["languages"][0]["warnings"] == 1


def test_stale_fires_on_the_segment_count_too(extracted, capsys):
    """The other half of the predicate, which no test moved.

    `test_stale_fires_when_the_counts_move_under_the_report` only ever changes
    `translated`, so dropping the `segments` comparison survived. Adding a
    paragraph and re-extracting moves `segments` and leaves `translated` alone.
    """
    assert _status(capsys)["projects"][0]["documents"][0]["check"]["stale"] is False
    (extracted / "docs" / "guide.md").write_text(
        DOC + "\nA third sentence.\n", encoding="utf-8")
    cli.main(["extract", "docs/guide.md", "--lang", "zh-TW"])
    capsys.readouterr()
    document = _status(capsys)["projects"][0]["documents"][0]
    assert document["segments"] == 4 and document["translated"] == 2
    assert document["check"]["stale"] is True


def test_languages_are_sorted_and_lead_with_their_tag(project, capsys):
    """`languages` was always length 1, so its ordering was unassertable.

    **Two documents rather than one language each on the same document**, and
    that is the point rather than a detail: `store.tracked` orders by `doc_id`
    and then by `lang`, so one document in three languages arrives already
    sorted by tag and a rollup built in encounter order would look sorted. Here
    `a.md` is zh-TW and `b.md` is de-DE, so encounter order is `[zh-TW, de-DE]`
    and only a real sort reverses it. Measured: the unsorted mutant survived the
    one-document version of this test.
    """
    (project / "docs" / "a.md").write_text(DOC, encoding="utf-8")
    (project / "docs" / "b.md").write_text(DOC, encoding="utf-8")
    cli.main(["extract", "docs/a.md", "--lang", "zh-TW"])
    cli.main(["extract", "docs/b.md", "--lang", "de-DE"])
    capsys.readouterr()

    status = _status(capsys)["projects"][0]
    assert [d["lang"] for d in status["documents"]] == ["zh-TW", "de-DE"], (
        "the fixture must present the languages out of sorted order")
    languages = status["languages"]
    assert [row["lang"] for row in languages] == ["de-DE", "zh-TW"]
    assert list(languages[0])[0] == "lang", (
        "the contract says `lang` comes first in a language rollup")
    assert all(row["documents"] == 1 for row in languages)


def test_the_json_is_not_ascii_escaped(extracted, capsys):
    """`ensure_ascii=False` is in the contract, and it guards another test.

    Not cosmetic: `test_no_segment_text_reaches_this_surface` searches the output
    for CJK, and under `\\u` escaping those probes stop matching — so a mutant
    that escaped the output would leave that guard passing while translated text
    was on the wire. Measured by a mutation round.
    """
    (extracted / "docs" / "中文.md").write_text("# 標題\n\n句子。\n", encoding="utf-8")
    cli.main(["extract", "docs/中文.md", "--lang", "zh-TW"])
    capsys.readouterr()
    cli.main(["status", "--json"])
    out = capsys.readouterr().out
    assert "中文" in out, "a non-ASCII path must survive as itself"
    assert "\\u4e2d" not in out


def test_lx_stats_honours_its_lang_filter(extracted, capsys):
    cli.main(["stats", "--lang", "ja-JP"])
    assert "nothing tracked yet" in capsys.readouterr().out
    cli.main(["stats", "--lang", "zh-TW"])
    assert "docs/guide.md" in capsys.readouterr().out


def test_the_two_nothings_are_told_apart(tmp_path, monkeypatch, capsys):
    """A directory nobody set up, and a project set up and not yet extracted.

    They need different answers and the branch that chooses between them was
    untested — inverting it swapped the two sentences with nothing to notice.
    """
    bare = tmp_path / "nest" / "bare"
    bare.mkdir(parents=True)
    monkeypatch.chdir(bare)
    cli.main(["status"])
    assert "not a project" in capsys.readouterr().out
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "nothing tracked yet" in out and "not a project" not in out


def test_a_config_that_is_not_json_is_refused_with_a_sentence(project, capsys):
    """Exit 2 and one sentence, which the contract's exit table promises.

    `main` read the configuration before its own `try`, so a typo in
    `lx.config.json` answered every command in this CLI with a traceback and exit
    1. Found by the mutation pass over this contract, whose exit table enumerates
    0 and 2 and no third thing.
    """
    (project / "lx.config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["status", "--json"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""

    (project / "lx.config.json").write_text('["an array"]', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["status", "--json"])
    assert exc.value.code == 2


def test_status_creates_nothing(tmp_path, monkeypatch, capsys):
    """No `.lx/`, no database, no report — `store.tracked` opens with `create=False`."""
    bare = tmp_path / "nest" / "bare"
    (bare / "docs").mkdir(parents=True)
    (bare / "docs" / "guide.md").write_text(DOC, encoding="utf-8")
    monkeypatch.chdir(bare)
    _status(capsys)
    assert not os.path.exists(bare / ".lx")
    assert sorted(os.listdir(bare)) == ["docs"]


def _snapshot(root):
    """Content hashes under `root`, minus the SQLite sidecars.

    `state.db` and its `-wal` / `-shm` companions are excluded for the reason the
    sibling file gives: closing the last connection to a WAL database checkpoints
    it, which rewrites the main file for a read. That is a true statement about
    SQLite and a false one about this command — and it is written into the
    contract as the one honest exception to "no writes", rather than asserted
    away here.
    """
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            if os.path.basename(full).startswith("state.db"):
                continue
            with open(full, "rb") as f:
                out[os.path.relpath(full, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def test_status_changes_nothing_on_disk(extracted, capsys):
    before = _snapshot(extracted)
    _status(capsys)
    _status(capsys, "--scan", str(extracted.parent))
    assert _snapshot(extracted) == before


def test_the_working_directory_is_where_it_was(extracted, tmp_path, monkeypatch, capsys):
    """The scan stands in each project to read it, and it puts the cwd back.

    **Standing outside every project it will visit**, and that is the whole test
    rather than a detail. The fixture leaves the cwd inside the one project in
    the tree, which is also the last one the scan visits, so a `do_status` with
    its `finally` deleted ended exactly where it started and this assertion
    passed. Measured by a mutation round on 2026-08-19: `chdir-restore` survived
    until the cwd was moved out of the scan root.

    Asserted after a scan that *fails* partway as well as after one that
    succeeds, because the restore is in a `finally` and a test that only ever
    takes the happy path would not notice it moving.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    here = os.getcwd()
    assert not here.startswith(str(extracted.parent)), "the probe must start outside"
    _status(capsys, "--scan", str(extracted.parent))
    assert os.getcwd() == here
    (extracted.parent / "broken" / ".lx").mkdir(parents=True)
    (extracted.parent / "broken" / ".lx" / "state.db").write_text("nope", encoding="utf-8")
    _status(capsys, "--scan", str(extracted.parent))
    assert os.getcwd() == here
    with pytest.raises(SystemExit):
        cli.main(["status", "--json", "--scan", "no/such/place"])
    assert os.getcwd() == here


def test_a_non_integer_in_a_check_report_reads_as_zero(extracted, capsys):
    """`errors` and `warnings` are typed `integer`, over a file a person can edit.

    `.lx/reports/` is a rebuildable artifact (invariant 9) and nothing validates
    it on the way in, so the type promise in the contract has to be kept on the
    way out. `True` is an `int` in Python and is not one here — a consumer
    rendering "True errors" would be showing a number this project never had.
    """
    report = extracted / ".lx" / "reports"
    name = os.listdir(report)[0]
    data = json.loads((report / name).read_text(encoding="utf-8"))
    data["errors"], data["warnings"] = True, "many"
    (report / name).write_text(json.dumps(data), encoding="utf-8")
    check = _status(capsys)["projects"][0]["documents"][0]["check"]
    assert check["errors"] == 0 and check["warnings"] == 0
    assert not isinstance(check["errors"], bool)


def _can_symlink(tmp_path):
    target = tmp_path / "symlink-probe"
    target.mkdir()
    try:
        os.symlink(target, tmp_path / "symlink-probe-link", target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return False
    return True


def test_a_shelf_of_symlinks_reports_one_book_once(tmp_path, monkeypatch, capsys):
    """The dedupe is on `realpath`, and nothing else in this file can reach it.

    A plain tree walk never arrives at one directory twice, so the only way to
    exercise the `seen` set is a symlink — which on Windows needs a privilege
    this account may not have. Skipped rather than asserted away, the way this
    suite already carries one POSIX-only test and one that runs where the
    filesystem folds case. Found by a mutation round on 2026-08-19: deleting the
    dedupe changed nothing any test could see.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("this account or filesystem cannot create a directory symlink")
    lib = tmp_path / "nest" / "lib"
    book = _mark(lib / "book", ".lx")
    os.symlink(book, lib / "alias", target_is_directory=True)
    os.symlink(book, lib / "zzz-alias", target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    status = _status(capsys, "--scan", str(lib))
    assert len(status["projects"]) == 1, (
        f"one book reached by two paths is one project: "
        f"{[p['path'] for p in status['projects']]}")
    # And under the **first** path that reached it, which the contract states and
    # which only holds because siblings are visited in sorted order. On a LIFO
    # frontier fed in sorted order the winner was the alphabetically *last*
    # alias, so adding a `zzz-alias` later silently changed the reported `path`
    # of a book nobody had touched — and `path` is what a consumer keys on.
    assert status["projects"][0]["path"].endswith("alias"), status["projects"][0]["path"]
    assert status["projects"][0]["name"] == "alias", (
        "the dedupe winner must be the first path in sorted order, not the last")
    # And `path` is `abspath`, **not** `realpath`: resolving it here would report
    # the book under `book` rather than under the alias the scan actually walked,
    # which is the distinction the contract draws and the only place it shows.
    assert status["projects"][0]["path"] == str(lib / "alias"), (
        f"path was resolved: {status['projects'][0]['path']}")


def test_the_human_output_is_not_the_contract(extracted, capsys):
    """It exists, it is not JSON, and the contract says not to parse it."""
    cli.main(["status"])
    out = capsys.readouterr().out
    assert out.strip()
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert "must not be parsed" in _read(CONTRACT)


def test_lx_stats_reads_the_same_counts(extracted, capsys):
    """One computation behind both commands, against an oracle neither produced.

    The fixture translates two of three segments by hand, so `2/3` is known from
    **outside** `do_status`. Asserting `lx stats` against `lx status`'s own
    numbers was asserting `do_status == do_status`, and a mutation round proved
    it blind: reverting the strip in `_counts` — the exact defect the rewire
    exists to fix — failed the status test and left this one green. It could
    only ever have caught `_bar` formatting drift.
    """
    cli.main(["stats"])
    line = capsys.readouterr().out.strip()
    assert "2/3" in line, line
    assert "docs/guide.md [zh-TW]" in line, line
    # 2 of 3, floor-divided, which is the incumbent's own arithmetic preserved.
    assert line.startswith("66%"), line


def test_lx_stats_does_not_pay_for_the_reports_it_never_prints(extracted, capsys):
    """One computation, and not one projection more than the caller reads.

    `cmd_stats` prints translated/segments/source/lang and nothing else, so the
    per-document read of `.lx/reports/` is pure waste for it — measured at six
    times `store.tracked`'s own cost on 2000 documents. Asserted structurally
    rather than by timing, which is what a timing test would be lying about on a
    loaded machine: with `checks=False` no report is opened at all, and the
    printed line is unchanged.
    """
    opened = []
    real = cli.load_json

    def spy(path, default=None):
        opened.append(str(path))
        return real(path, default)

    cli.load_json = spy
    try:
        cli.main(["stats"])
    finally:
        cli.load_json = real
    line = capsys.readouterr().out.strip()
    assert "2/3" in line, line
    assert not [p for p in opened if "reports" in p], opened


def test_lx_stats_still_fails_on_a_project_it_cannot_read(project, capsys):
    """The rewire took this away once, and CI is the caller that reads it.

    `.github/workflows/ci.yml` ends a `set -euo pipefail` step with a bare
    `lx stats` and asserts nothing about its output, so the exit code is the
    whole assertion — and for one commit the command answered 0 on a database it
    could not open. The sentence belongs on stderr for the same reason: `lx
    stats > coverage.txt` must not capture the failure into the report and leave
    the terminal empty.
    """
    (project / ".lx").mkdir()
    (project / ".lx" / "state.db").write_text("nope", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["stats"])
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert out == "", f"the failure must not reach stdout: {out!r}"
    assert "not a database" in err


def test_lx_status_still_reports_that_project_rather_than_failing(project, capsys):
    """The asymmetry with `lx stats` above is deliberate, so it is pinned.

    `lx status` has a per-project `error` field and a `--scan` that must survive
    one bad book; `lx stats` has neither and one project. The two commands answer
    the same condition differently on purpose — the contract's exit-code table
    says so for this one, and *Known divergences* (5) argues it.
    """
    (project / ".lx").mkdir()
    (project / ".lx" / "state.db").write_text("nope", encoding="utf-8")
    status = _status(capsys)  # no SystemExit
    assert "not a database" in status["projects"][0]["error"]
