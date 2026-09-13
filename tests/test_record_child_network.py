"""`tests/record_child_network.py`: the verdict it reads its exit code from, its probe, and one run.

The recorder runs the whole suite and cannot be run over it from inside it, so
what is pinned here is the part that would rot silently: the rule it judges a
recorded connection by must stay `tests/conftest.py`'s rule; the probe it injects
must load nothing into a `-S` interpreter — or `tests/test_startup_imports.py`,
which counts exactly that, would measure the recorder instead of the product — and
must not lose a row, since a lost row is a false exit 0; and one real run over a
two-file repository must reach each of its answers.
"""

import ast
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import record_child_network as rcn  # noqa: E402

TOP = "100"
NAME = rcn.CONTROL_NAME


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


def _control(pid="200", port=5555):
    return (("connect", pid, "127.0.0.1", port, "other", "-c"), ("lookup", pid, NAME, port, "-c"))


def _answer(*rows, top=TOP, control=5555):
    base = _rows(("install", TOP, "1", "pytest"), ("install", "200", TOP, "-m scriptorium"),
                 *_control(port=control))
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
    ("lookup", "200", "LOCALHOST", 11434, "localhost in capitals"),
    ("lookup", "200", "0.0.0.0", 7, "an unspecified address"),
    ("lookup", "200", "", 0, "the passive address"),
    ("reverse", "200", "127.0.0.1", "a reverse lookup of loopback"),
    ("reverse", "200", "", "an empty reverse lookup"),
])
def test_what_conftest_would_allow_is_not_a_finding(row):
    answer = _answer(row)
    assert answer["connects"] == [] and answer["lookups"] == []
    assert rcn.exit_code(answer) == 0


@pytest.mark.parametrize("row", [
    ("connect", "200", "10.0.0.1", 9, "other", "a dead-end port on a host that is not loopback"),
    ("connect", "200", "0.0.0.0", 6000, "self", "a held port through an address that is not loopback"),
    ("lookup", "200", "api.openai.com", 443, "a name"),
    ("lookup", "200", "example.com", "", "gethostbyname"),
    ("reverse", "200", "10.0.0.1", "a reverse lookup of another address"),
    ("reverse", "200", "myhost", "a reverse lookup of a name"),
])
def test_what_conftest_would_refuse_is_a_finding(row):
    assert rcn.exit_code(_answer(row)) == 1


def test_the_test_process_lookups_are_counted_and_not_judged():
    answer = _answer(("lookup", TOP, "user:SECRET@127.0.0.1", 1, "pytest"),
                     ("reverse", TOP, "myhost", "pytest"))
    assert answer["top_lookups"] == 2 and answer["lookups"] == []
    assert rcn.exit_code(answer) == 0


def test_the_control_needs_both_its_connection_and_its_lookup():
    connect, lookup = _control()
    assert rcn.exit_code(_answer()) == 0 and _answer()["connects"] == []
    for rows in ((connect,), (lookup,)):
        answer = rcn.analyse(_rows(("install", TOP, "1", "pytest"), *rows), TOP, 5555)
        assert not answer["control_seen"] and rcn.exit_code(answer) == 2
    # A "self" row on the control's port is a port the child bound, and a row on
    # another host is not the control's connection.
    for other in (("connect", "200", "127.0.0.1", 5555, "self", "-c"),
                  ("connect", "200", "10.0.0.1", 5555, "other", "-c")):
        rows = _rows(("install", TOP, "1", "pytest"), other, lookup)
        assert not rcn.analyse(rows, TOP, 5555)["control_seen"]
    rows = _rows(("install", TOP, "1", "pytest"), connect, ("lookup", TOP, NAME, 5555, "pytest"))
    assert not rcn.analyse(rows, TOP, 5555)["control_seen"], "the test process is not the child"


def test_an_uninstalled_test_process_answers_nothing():
    rows = _rows(("install", "200", "999", "-c"), *_control())
    assert rcn.exit_code(rcn.analyse(rows, TOP, 5555)) == 2


def test_a_launcher_pid_resolves_to_the_one_interpreter_it_started():
    """A Windows venv's `python.exe` starts the base interpreter, so `Popen.pid` is not pytest."""
    rows = _rows(("install", "300", "42", "pytest"), ("install", "200", "300", "-m scriptorium"),
                 ("connect", "300", "127.0.0.1", 11434, "other", "pytest"),
                 *_control(), ("spawn", "300", "python -m scriptorium", ""))
    answer = rcn.analyse(rows, "42", 5555)
    assert answer["top_installed"] and answer["connects"] == []
    assert (answer["spawned_by_top"], answer["installed_below"]) == (1, 1)
    rows.append(["install", "301", "42", "another"])
    assert rcn.exit_code(rcn.analyse(rows, "42", 5555)) == 2, "two candidates are a guess"


def test_a_reused_pid_is_still_two_processes():
    """Windows gives a finished child's pid to the next; a set of pids undercounts."""
    rows = _rows(("install", TOP, "1", "pytest"), ("install", "200", TOP, "-m scriptorium init"),
                 ("install", "200", TOP, "-m scriptorium extract"),
                 ("spawn", TOP, "python -m scriptorium init", ""),
                 ("spawn", TOP, "python -m scriptorium extract", ""), *_control())
    answer = rcn.analyse(rows, TOP, 5555)
    assert (answer["spawned_by_top"], answer["installed_below"]) == (2, 2)


def test_a_torn_line_is_skipped_rather_than_misread():
    lines = ["install\t1\t2\tpytest\n", "connect\t3\t127.0.0.1\n", "garbage\n",
             "connect\t3\t127.0.0.1\t80\n", "connect\t3\t127.0.0.1\t80\tother\n",
             "reverse\t3\t10.0.0.1\n", "spawn\t1\tpython -c\t\n",
             "lookup\t3\texample.com\t443\t-c\n"]
    assert [r[0] for r in rcn.parse(lines)] == ["install", "spawn", "lookup"]


def test_bytes_that_are_not_utf8_do_not_stop_the_reading(tmp_path):
    (tmp_path / "1-a.tsv").write_bytes(b"install\t1\t2\tpy\xb8test\n")
    (tmp_path / "2-b.tsv").write_bytes(b"lookup\t2\texample.com\t443\t-c\n")
    assert [r[0] for r in rcn.read_rows(str(tmp_path))] == ["install", "lookup"]


def _probe_source(rows_dir, *lines):
    probe = rcn.PROBE.replace("__OUT__", repr(rows_dir))
    # A lone surrogate is what POSIX hands Python for an argument it cannot decode;
    # written strictly it made every row of its process vanish, measured.
    return "\n".join(["import json, sys", "sys.argv.append('\\udcff')", "before = set(sys.modules)",
                      "exec(compile(" + repr(probe) + ", 'probe', 'exec'))",
                      "added = sorted(set(sys.modules) - before)", *lines,
                      "print(json.dumps(added))"])


def test_the_probe_loads_nothing_into_a_dash_S_child_and_writes_every_event(tmp_path):
    """End to end in the interpreter shape `tests/test_startup_imports.py` uses.

    The two real connections go only where `tests/conftest.py` allows: a server
    the child binds itself, and the dead end 9. Every lookup is an audit event
    raised by hand, so no resolver is asked anything.
    """
    rows_dir = str(tmp_path)
    source = _probe_source(
        rows_dir,
        "import socket, threading",
        "server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(1)",
        "t = threading.Thread(target=lambda: server.accept()[0].close(), daemon=True); t.start()",
        "socket.create_connection(server.getsockname(), 5).close(); t.join(5); server.close()",
        "try:",
        "    socket.create_connection(('127.0.0.1', 9), 3).close()",
        "except OSError:",
        "    pass",
        "sys.audit('socket.getaddrinfo', b'localhost', 9, 0, 0, 0)",
        "sys.audit('socket.gethostbyname', 'example.invalid')",
        "sys.audit('socket.gethostbyaddr', '10.0.0.1')",
        "sys.audit('socket.getnameinfo', ('10.0.0.2', 80), 0)",
        "sys.audit('subprocess.Popen', 'exe', ['exe', '-c'], None, None)",
        "def burst():",
        "    for _ in range(500):",
        "        sys.audit('socket.getaddrinfo', '127.0.0.1', 1, 0, 0, 0)",
        "threads = [threading.Thread(target=burst) for _ in range(4)]",
        "[x.start() for x in threads]; [x.join() for x in threads]",
    )
    result = subprocess.run([sys.executable, "-S", "-B", "-c", source, "an\nargument"],
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert json.loads(result.stdout.decode().strip().splitlines()[-1]) == []
    rows = rcn.read_rows(rows_dir)
    install = [r for r in rows if r[0] == "install"]
    assert len(install) == 1 and install[0][2] != install[0][1], "ppid is the parent's"
    assert "an\\nargument" in install[0][3], "a newline in argv stays inside its row"
    assert "\\udcff" in install[0][3], "an undecodable argument is written, not the row dropped"
    connects = [(r[3], r[4]) for r in rows if r[0] == "connect"]
    assert len(connects) == 2 and connects[0][1] == "self" and connects[1] == ("9", "other")
    lookups = [r[2] for r in rows if r[0] == "lookup"]
    assert "localhost" in lookups and "example.invalid" in lookups
    assert lookups.count("127.0.0.1") >= 2000, "no thread's row was lost"
    assert sorted(r[2] for r in rows if r[0] == "reverse") == ["10.0.0.1", "10.0.0.2"]
    assert [r[0] for r in rows].count("spawn") == 1


def test_two_processes_writing_at_once_lose_no_row(tmp_path):
    rows_dir = str(tmp_path)
    source = _probe_source(rows_dir, "for i in range(1500):",
                           "    sys.audit('socket.getaddrinfo', 'localhost', i, 0, 0, 0)")
    children = [subprocess.Popen([sys.executable, "-S", "-B", "-c", source],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(2)]
    for child in children:
        out, err = child.communicate(timeout=120)
        assert child.returncode == 0, err.decode("utf-8", "replace")
    rows = rcn.read_rows(rows_dir)
    assert sum(1 for r in rows if r[0] == "lookup") == 3000
    assert sum(1 for r in rows if r[0] == "install") == 2


def _git(toplevel, verify, archive=b""):
    """A stand-in for `rcn._git`: the suite also runs from an archive with no `.git`,
    which is exactly where the recorder runs it, so these tests cannot ask real git."""
    def run(*args):
        if args[:2] == ("rev-parse", "--show-toplevel"):
            code, out = toplevel
        elif args[0] == "rev-parse":
            code, out = verify
        elif args[0] == "archive":
            code, out = 0, archive
        else:
            code, out = 0, b""
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


def test_an_exception_while_measuring_is_not_a_finding(monkeypatch, capsys):
    """Uncaught, it would exit 1 — the code that means a child reached the network."""
    monkeypatch.setattr(rcn, "_git", _git((0, rcn.ROOT.encode()), (0, b"0" * 40)))

    def broken(*args):
        raise UnicodeDecodeError("utf-8", b"\xb8", 0, 1, "invalid start byte")
    monkeypatch.setattr(rcn, "_measure", broken)
    assert rcn.main([]) == 2
    assert "nothing is claimed" in capsys.readouterr().out


_DIAL = '''import os, socket, subprocess, sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")


def test_a_child_dials_a_closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    source = ("import scriptorium, socket\\n"
              "try:\\n"
              "    socket.create_connection(('127.0.0.1', %d), 5).close()\\n"
              "except OSError:\\n"
              "    pass\\n" % port)
    subprocess.run([sys.executable, "-c", source], env=dict(os.environ, PYTHONPATH=SRC),
                   timeout=120)
'''


def _archive():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in (("src/scriptorium/__init__.py", ""), ("tests/test_dial.py", _DIAL)):
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_one_run_over_a_two_file_repository_reaches_every_answer(monkeypatch, capsys):
    """A child that dials a closed port is found; a selection that drops the control,
    and a pytest that cannot start, answer nothing. Every child here dials a closed
    loopback port or nothing, and no lookup leaves the machine."""
    monkeypatch.setattr(rcn, "_git", _git((0, rcn.ROOT.encode()), (0, b"f" * 40), _archive()))
    assert rcn.main(["--timeout", "300"]) == 1
    out = capsys.readouterr().out
    assert "lookup were both recorded" in out and "CONNECT pid" in out
    assert "the test process started 2" in out and "2 of them imported scriptorium" in out

    # A selection still gets the control, and so still gets an answer.
    assert rcn.main(["--timeout", "300", "--", "tests/test_dial.py"]) == 1
    assert "lookup were both recorded" in capsys.readouterr().out

    assert rcn.main(["--timeout", "300", "--", "-k", "dial", "tests/test_dial.py"]) == 2
    assert "the planted control never ran" in capsys.readouterr().out

    assert rcn.main(["--timeout", "300", "--", "--no-such-option"]) == 2
    assert "pytest did not run the suite" in capsys.readouterr().out
