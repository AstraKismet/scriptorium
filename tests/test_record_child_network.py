"""`tests/record_child_network.py`: the verdict it reads its exit code from, and its probe.

The recorder runs the whole suite and cannot be run from inside it, so what is
pinned here is the part that would rot silently: the rule it judges a recorded
connection by must stay `tests/conftest.py`'s rule, and the probe it injects must
load nothing into a `-S` interpreter — or `tests/test_startup_imports.py`, which
counts exactly that, would measure the recorder instead of the product.
"""

import ast
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import record_child_network as rcn  # noqa: E402

TOP = "100"


def _conftest_tree():
    with open(os.path.join(_HERE, "conftest.py"), encoding="utf-8") as fh:
        return ast.parse(fh.read())


def test_the_dead_ends_are_conftests():
    """Read with `ast`, because importing `conftest` again would register a second guard."""
    for node in _conftest_tree().body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEAD_PORTS" for t in node.targets):
            assert isinstance(node.value, ast.Call) and node.value.func.id == "frozenset"
            assert frozenset(ast.literal_eval(node.value.args[0])) == rcn.DEAD_PORTS
            return
    pytest.fail("tests/conftest.py no longer assigns DEAD_PORTS")


@pytest.mark.parametrize("host", [
    "localhost", "LocalHost", "127.0.0.1", "127.9.9.9", "::1", "::ffff:127.0.0.1",
    "0.0.0.0", "::", "10.0.0.1", "192.168.1.10", "example.com", "", "localhost.",
])
def test_loopback_is_conftests_loopback(host):
    namespace = {}
    wanted = {"_text", "_loopback"}
    body = [n for n in _conftest_tree().body
            if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in body} == wanted
    module = ast.Module(body=[ast.Import(names=[ast.alias(name="ipaddress")]), *body],
                        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "conftest-excerpt", "exec"), namespace)
    assert rcn.loopback(host) is namespace["_loopback"](host)


def _rows(*rows):
    return [list(map(str, r)) for r in rows]


def _answer(*rows, top=TOP, control=5555):
    base = _rows(("install", TOP, "1", "pytest"), ("install", "200", TOP, "-m scriptorium"),
                 ("connect", "200", "127.0.0.1", control, "other", "-c"))
    return rcn.analyse(base + _rows(*rows), top, control)


def test_a_child_that_dials_a_closed_loopback_port_is_a_finding():
    answer = _answer(("connect", "200", "127.0.0.1", 80, "other", "-m scriptorium models"))
    assert [(c["host"], c["port"]) for c in answer["connects"]] == [("127.0.0.1", 80)]
    assert rcn.exit_code(answer) == 1


@pytest.mark.parametrize("row", [
    ("connect", "200", "127.0.0.1", 9, "other", "a dead end"),
    ("connect", "200", "::1", 1, "other", "a dead end over IPv6"),
    ("connect", "200", "localhost", 6000, "self", "a port the child bound"),
    ("connect", TOP, "127.0.0.1", 11434, "other", "the test process, which conftest guards"),
    ("lookup", "200", "127.0.0.1", 9, "an address literal"),
    ("lookup", "200", "localhost", 11434, "localhost"),
    ("lookup", "200", "0.0.0.0", 7, "an unspecified address"),
])
def test_what_conftest_would_allow_is_not_a_finding(row):
    answer = _answer(row)
    assert answer["connects"] == [] and answer["lookups"] == []
    assert rcn.exit_code(answer) == 0


@pytest.mark.parametrize("row", [
    ("connect", "200", "10.0.0.1", 9, "other", "a dead-end port on a host that is not loopback"),
    ("connect", "200", "0.0.0.0", 6000, "self", "a held port through an address that is not loopback"),
    ("lookup", "200", "api.openai.com", 443, "a name"),
])
def test_what_conftest_would_refuse_is_a_finding(row):
    assert rcn.exit_code(_answer(row)) == 1


def test_an_unseen_control_or_an_uninstalled_test_process_answers_nothing():
    rows = _rows(("install", TOP, "1", "pytest"), ("install", "200", TOP, "-c"))
    assert rcn.exit_code(rcn.analyse(rows, TOP, 5555)) == 2, "the control was never recorded"
    rows = _rows(("install", "200", "999", "-c"), ("connect", "200", "127.0.0.1", 5555, "other", "-c"))
    assert rcn.exit_code(rcn.analyse(rows, TOP, 5555)) == 2, "pytest never loaded the recorder"


def test_the_control_is_judged_and_then_taken_out_of_the_findings():
    answer = _answer()
    assert answer["control_seen"] and answer["connects"] == []
    # A "self" row on the control's port is a port the child bound, not the control.
    rows = _rows(("install", TOP, "1", "pytest"), ("connect", "200", "127.0.0.1", 5555, "self", "-c"))
    assert not rcn.analyse(rows, TOP, 5555)["control_seen"]


def test_a_launcher_pid_resolves_to_the_interpreter_it_started():
    """A Windows venv's `python.exe` starts the base interpreter, so `Popen.pid` is not pytest."""
    rows = _rows(("install", "300", "42", "pytest"), ("install", "200", "300", "-m scriptorium"),
                 ("connect", "300", "127.0.0.1", 11434, "other", "pytest"),
                 ("connect", "200", "127.0.0.1", 5555, "other", "-c"),
                 ("spawn", "300", "python -m scriptorium", ""))
    answer = rcn.analyse(rows, "42", 5555)
    assert answer["top_installed"] and answer["connects"] == []
    assert (answer["spawned_by_top"], answer["installed_below"]) == (1, 1)


def test_a_reused_pid_is_still_two_processes():
    """Windows gives a finished child's pid to the next; a set of pids undercounts."""
    rows = _rows(("install", TOP, "1", "pytest"), ("install", "200", TOP, "-m scriptorium init"),
                 ("install", "200", TOP, "-m scriptorium extract"),
                 ("spawn", TOP, "python -m scriptorium init", ""),
                 ("spawn", TOP, "python -m scriptorium extract", ""),
                 ("connect", "200", "127.0.0.1", 5555, "other", "-c"))
    answer = rcn.analyse(rows, TOP, 5555)
    assert (answer["spawned_by_top"], answer["installed_below"]) == (2, 2)


def test_a_torn_line_is_skipped_rather_than_misread():
    lines = ["install\t1\t2\tpytest\n", "connect\t3\t127.0.0.1\n", "garbage\n",
             "connect\t3\t127.0.0.1\t80\n", "connect\t3\t127.0.0.1\t80\tother\n",
             "lookup\t3\texample.com\t443\t-c\n"]
    assert [r[0] for r in rcn.parse(lines)] == ["install", "lookup"]


def test_the_probe_loads_nothing_into_a_dash_S_child_and_writes_what_it_saw(tmp_path):
    """End to end in the interpreter shape `tests/test_startup_imports.py` uses.

    The child dials only what `tests/conftest.py` allows: a server it binds itself,
    and the dead end 9.
    """
    rows_path = str(tmp_path / "rows.tsv")
    probe = rcn.PROBE.replace("__OUT__", repr(rows_path))
    source = "\n".join([
        "import json, sys",
        "before = set(sys.modules)",
        "exec(compile(" + repr(probe) + ", 'probe', 'exec'))",
        "added = sorted(set(sys.modules) - before)",
        "import socket, threading",
        "server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)",
        "t = threading.Thread(target=lambda: server.accept()[0].close(), daemon=True); t.start()",
        "socket.create_connection(server.getsockname(), 5).close(); t.join(5); server.close()",
        "try:",
        "    socket.create_connection(('127.0.0.1', 9), 3).close()",
        "except OSError:",
        "    pass",
        "print(json.dumps(added))",
    ])
    result = subprocess.run([sys.executable, "-S", "-B", "-c", source], capture_output=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert json.loads(result.stdout.decode().strip().splitlines()[-1]) == []
    with open(rows_path, encoding="utf-8") as fh:
        rows = rcn.parse(fh.readlines())
    kinds = [r[0] for r in rows]
    assert kinds[0] == "install"
    connects = [(r[3], r[4]) for r in rows if r[0] == "connect"]
    assert len(connects) == 2 and connects[0][1] == "self" and connects[1] == ("9", "other")
    assert "lookup" in kinds


def _git(toplevel, verify):
    """A stand-in for `rcn._git`: the suite also runs from an archive with no `.git`,
    which is exactly where the recorder runs it, so these tests cannot ask real git."""
    def run(*args):
        code, out = toplevel if args[:2] == ("rev-parse", "--show-toplevel") else verify
        return subprocess.CompletedProcess(["git", *args], code, stdout=out, stderr=b"")
    return run


def test_a_ref_that_names_no_commit_answers_nothing(monkeypatch, capsys):
    """Refused by name, before anything is archived: a later step also exits 2, so the
    exit code alone would pass a build that tried to archive a ref it never checked."""
    monkeypatch.setattr(rcn, "_git", _git((0, rcn.ROOT.encode()), (1, b"")))
    assert rcn.main(["--ref", "refs/heads/no-such-branch"]) == 2
    assert "does not name a commit" in capsys.readouterr().out


def test_outside_a_checkout_of_this_repository_nothing_is_measured(monkeypatch, capsys):
    monkeypatch.setattr(rcn, "_git", _git((128, b""), (0, b"0" * 40)))
    assert rcn.main([]) == 2
    assert "not a git checkout of this repository" in capsys.readouterr().out
