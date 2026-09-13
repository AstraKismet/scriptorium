"""`tests/conftest.py` fails the right test, for the right reason, every time.

The guard's own acceptance was a manual revert — take the stub or the wait out
of a test and watch it fail — and a manual revert protects nothing after the day
it is done: a later edit that weakens the guard leaves the whole suite green,
because a green suite is exactly what a guard that fires on nothing produces.
So the reverts are written down here as tests that break the rules on purpose,
one per decision the guard makes. Each was checked by planting the defect it
names and watching this file fail.

They run in a **child pytest** over a copy of the guard. In this process the
guard is already installed and a planted defect would fail *this* test rather
than be observed by it; a child is the only place a broken test can be watched.
The child imports the real `src`, so a job it starts is the workbench's own.

No planted defect sends anything off the machine, even against a broken guard:
the child's backend is a loopback port it bound and closed, and the one
non-loopback destination is `0.0.0.0`, which is the local host on Linux and an
immediate error on Windows.
"""

import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))

#: Below `tests/conftest.py`'s `CLEANUP_SECONDS`, on purpose: a guard whose
#: cleanup waited for an event nothing sets would pass every assertion here,
#: slowly, and a child that outlives this bound is that defect. The child
#: normally takes a few seconds.
_CHILD_TIMEOUT = 100

_CHILD = r'''
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, SRC)

from scriptorium import translate as translate_mod

# The server is imported inside `base`, not here, so that the guard has to
# instrument it without any test module having loaded it at collection.


def _closed_loopback_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# A backend nobody holds, recorded where the parent can read it: with the guard
# broken, an unstubbed job reaches this, not whatever serves `localhost:11434`.
DEAD = _closed_loopback_port()
with open("dead_port.txt", "w") as fh:
    fh.write(str(DEAD))


@pytest.fixture(scope="module")
def base():
    from scriptorium.web.server import _Handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(base, path, obj):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _finish(base, job_id):
    until = time.monotonic() + 60
    while time.monotonic() < until:
        job = _post(base, "/api/job", {"id": job_id})
        if job["done"]:
            return job
        time.sleep(0.05)
    raise AssertionError("never finished")


class _Echo:
    def __init__(self, before=None):
        self.before = before

    def describe(self):
        return "stub"

    def complete(self, system, user):
        if self.before:
            self.before()
        items = json.loads(user[user.index("["):])
        return json.dumps({i["id"]: "山丘上站著一段文字，長度合乎一段譯文。"
                                    + "".join(re.findall(r"⟦\d+⟧", i["text"]))
                           for i in items}, ensure_ascii=False)


@pytest.fixture
def started(base, tmp_path, monkeypatch):
    """A two-paragraph document routed to `DEAD`, and a function that starts a draft run."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lx.config.json").write_text(json.dumps({
        "providers": {"dead": {"kind": "openai", "base_url": f"http://127.0.0.1:{DEAD}/v1",
                               "model": "m", "api_key_env": "", "retries": 0, "timeout": 1}},
        "routing": {"draft": "dead"}}), encoding="utf-8")
    (tmp_path / "d.md").write_bytes(b"The gate stood open when she came down the hill.\n\n"
                                    b"She went in anyway, and it swung shut behind her.\n")
    _post(base, "/api/extract", {"src": "d.md", "lang": "zh-TW"})

    def start():
        job = _post(base, "/api/translate", {"src": "d.md", "lang": "zh-TW"})
        assert job["total"] == 2
        return job["id"]
    return start


def _stub(monkeypatch, before=None):
    echo = _Echo(before)
    monkeypatch.setattr(translate_mod, "build_provider", lambda name, cfg, model=None: echo)


def test_follows_both_rules(base, started, monkeypatch):
    _stub(monkeypatch)
    assert _finish(base, started())["applied"] == 2


def test_forgets_the_wait(base, started, monkeypatch):
    _stub(monkeypatch)
    started()


def test_forgets_the_stub(base, started):
    job = _finish(base, started())
    assert job["applied"] == 0
    # The refusal is not an `OSError`, so the provider's transport branch never
    # wraps it, retries it or tries the next address — it ends the run.
    assert "cannot reach" not in json.dumps(job), job


def test_forgets_both(base, started):
    started()


def test_forgets_the_wait_on_a_job_that_selects_nothing(base, started):
    _post(base, "/api/translate", {"src": "d.md", "lang": "zh-TW", "ids": ["nope"]})


def test_polls_once_while_the_job_runs(base, started, monkeypatch):
    release = threading.Event()
    _stub(monkeypatch, before=lambda: release.wait(30))
    job_id = started()
    assert _post(base, "/api/job", {"id": job_id})["done"] is False
    release.set()


SLOW_JOB_ENTERED = threading.Event()
SLOW_JOB_OVER = threading.Event()


def _slow():
    SLOW_JOB_ENTERED.set()
    time.sleep(3)
    SLOW_JOB_OVER.set()


def test_leaves_a_slow_job_running(base, started, monkeypatch):
    _stub(monkeypatch, before=_slow)
    started()
    # Returned only once the job holds the stub, so undoing `monkeypatch` cannot
    # take it away and turn the slow job into a quick refusal.
    assert SLOW_JOB_ENTERED.wait(30)


def test_finds_the_slow_job_over_before_it_starts():
    assert SLOW_JOB_OVER.is_set(), "a job the test before forgot ran on into this test"


def test_swallows_a_refusal_on_a_loopback_port_nobody_holds():
    try:
        socket.create_connection(("127.0.0.1", _closed_loopback_port()), timeout=0.5)
    except Exception:
        pass


def test_swallows_a_refusal_on_a_port_this_process_holds_at_another_host():
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    try:
        socket.create_connection(("0.0.0.0", held.getsockname()[1]), timeout=0.5)
    except Exception:
        pass
    finally:
        held.close()


def test_dials_a_dead_end():
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
    except OSError:
        pass


def test_dials_a_server_it_holds():
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    try:
        socket.create_connection(held.getsockname(), timeout=2).close()
    finally:
        held.close()


def test_dials_a_server_it_holds_by_name():
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    client = socket.socket(socket.AF_INET)
    try:
        client.settimeout(2)
        client.connect(("localhost", held.getsockname()[1]))
    finally:
        client.close()
        held.close()
'''

#: A job a module-scoped fixture starts. Such a fixture is set up before any of
#: its test's function fixtures, so a ledger reset in one of those would forget
#: the job. A module of its own, because the fixture installs its stub and its
#: working directory for the whole module.
_CHILD_MODULE_FIXTURE = r'''
import os

import pytest

from scriptorium import translate as translate_mod
from test_child import _Echo, _post, base  # noqa: F401


@pytest.fixture(scope="module")
def job_started_by_a_module_fixture(base, tmp_path_factory):
    root = tmp_path_factory.mktemp("module_job")
    (root / "d.md").write_bytes(b"One sentence stood alone on the hill.\n")
    before, cwd = translate_mod.build_provider, os.getcwd()
    translate_mod.build_provider = lambda name, cfg, model=None: _Echo()
    os.chdir(root)
    try:
        _post(base, "/api/extract", {"src": "d.md", "lang": "zh-TW"})
        assert _post(base, "/api/translate", {"src": "d.md", "lang": "zh-TW"})["total"] == 1
        yield
    finally:
        os.chdir(cwd)
        translate_mod.build_provider = before


def test_uses_a_module_fixture_that_starts_a_job(job_started_by_a_module_fixture):
    pass
'''

#: A refusal in a module fixture's teardown, which runs after its test's own
#: check, followed by an innocent test in another module that must not be blamed.
_CHILD_OUTSIDE = {
    "test_a.py": r'''
import socket

import pytest


@pytest.fixture(scope="module")
def dials_on_the_way_out():
    yield
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5)
    except Exception:
        pass


def test_passes(dials_on_the_way_out):
    pass
''',
    "test_b.py": r'''
def test_innocent():
    pass
''',
}


def _run_child(tmp_path, files):
    with open(os.path.join(_HERE, "conftest.py"), "rb") as f:
        (tmp_path / "conftest.py").write_bytes(f.read())
    for name, source in files.items():
        (tmp_path / name).write_text(
            source.replace("sys.path.insert(0, SRC)", f"sys.path.insert(0, {_SRC!r})"),
            encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-rA", "-vv", "--color=no", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path), *sorted(files)],
        cwd=str(tmp_path), env=env, capture_output=True, timeout=_CHILD_TIMEOUT)
    # A child's stdout is a text stream, which writes CRLF on Windows.
    return done.returncode, done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def _outcomes(out):
    """`{test name: "PASSED" | "FAILED" | "PASSED+ERROR" | "FAILED+ERROR"}` from the summary.

    Both halves, because a teardown error says nothing about the body: a guard
    change that made a test's own assertion fail would otherwise read the same
    as the teardown error the test was written to provoke.
    """
    seen = {}
    for line in out.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR) test_\w+\.py::(\w+)", line)
        if m:
            seen.setdefault(m.group(2), set()).add(m.group(1))
    return {name: "+".join(sorted(kinds, key=["PASSED", "FAILED", "ERROR"].index))
            for name, kinds in seen.items()}


def _teardown_error(out, name):
    # A section ends where the next header starts: captured output, another
    # test's section, or the next banner. Long names get a single underscore.
    m = re.search(r"_+ ERROR at teardown of " + name + r" _+\n(.*?)(?=\n-{3,} |\n_+ |\n=+ )",
                  out, re.S)
    assert m, f"no teardown error for {name}:\n{out}"
    return m.group(1)


def test_the_guard_fails_exactly_the_tests_that_break_a_rule(tmp_path):
    code, out = _run_child(tmp_path, {"test_child.py": _CHILD,
                                      "test_child_module_fixture.py": _CHILD_MODULE_FIXTURE})
    dead = (tmp_path / "dead_port.txt").read_text()
    assert code == 1, out
    assert _outcomes(out) == {
        "test_follows_both_rules": "PASSED",
        "test_forgets_the_wait": "PASSED+ERROR",
        "test_forgets_the_stub": "PASSED+ERROR",
        "test_forgets_both": "PASSED+ERROR",
        "test_forgets_the_wait_on_a_job_that_selects_nothing": "PASSED+ERROR",
        "test_polls_once_while_the_job_runs": "PASSED+ERROR",
        "test_leaves_a_slow_job_running": "PASSED+ERROR",
        "test_finds_the_slow_job_over_before_it_starts": "PASSED",
        "test_swallows_a_refusal_on_a_loopback_port_nobody_holds": "PASSED+ERROR",
        "test_swallows_a_refusal_on_a_port_this_process_holds_at_another_host": "PASSED+ERROR",
        "test_dials_a_dead_end": "PASSED",
        "test_dials_a_server_it_holds": "PASSED",
        "test_dials_a_server_it_holds_by_name": "PASSED",
        "test_uses_a_module_fixture_that_starts_a_job": "PASSED+ERROR",
    }, out

    said = _teardown_error(out, "test_forgets_the_wait")
    assert "never saw it finish" in said and "refused" not in said, said
    for name in ("test_forgets_the_wait_on_a_job_that_selects_nothing",
                 "test_polls_once_while_the_job_runs", "test_leaves_a_slow_job_running",
                 "test_uses_a_module_fixture_that_starts_a_job"):
        assert "never saw it finish" in _teardown_error(out, name), name

    # The job was waited for, so the refusal is the only thing wrong: a message
    # blaming a wait the test has would send its reader to the wrong line.
    said = _teardown_error(out, "test_forgets_the_stub")
    assert f"refused a connection to 127.0.0.1:{dead} " in said, said
    assert "never saw it finish" not in said, said

    # Both wrong: the refusal is named first, because it is the cause.
    said = _teardown_error(out, "test_forgets_both")
    assert said.index("refused a connection") < said.index("never saw it finish"), said

    assert "refused a connection to 127.0.0.1:" in _teardown_error(
        out, "test_swallows_a_refusal_on_a_loopback_port_nobody_holds")
    assert "refused a connection to 0.0.0.0:" in _teardown_error(
        out, "test_swallows_a_refusal_on_a_port_this_process_holds_at_another_host")

    # Every refusal above was consumed by the test it belonged to.
    assert "connections refused outside a test's check" not in out, out


def test_a_refusal_no_test_checked_fails_the_run_and_blames_no_test(tmp_path):
    """A module fixture's teardown runs after the test's own check. Unreported,
    such a refusal was refused and the run exited 0 reading "2 passed"; charged
    to the test running next, it failed a test that did nothing."""
    code, out = _run_child(tmp_path, _CHILD_OUTSIDE)
    assert _outcomes(out) == {"test_passes": "PASSED", "test_innocent": "PASSED"}, out
    assert code == 1, out
    assert "connections refused outside a test's check" in out, out
