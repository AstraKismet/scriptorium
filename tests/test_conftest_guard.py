"""`tests/conftest.py` fails the right test, for the right reason, every time.

The guard's own acceptance was a manual revert — take the stub or the wait out
of a test and watch it fail — and a manual revert protects nothing after the day
it is done: a later edit that weakens the guard leaves the whole suite green,
because a green suite is exactly what a guard that fires on nothing produces.
So the reverts are written down here as tests that break the rules on purpose.

They run in a **child pytest** over a copy of the guard. In this process the
guard is already installed and a planted defect would fail *this* test rather
than be observed by it; a child is the only place a broken test can be watched.
The child imports the real `src`, so the job it starts is the workbench's own.

No planted defect sends a packet off the machine, even against a broken guard:
a refused destination is a loopback port nobody holds, or `0.0.0.0` on a port
this process holds, which is the local host on Linux and an immediate error on
Windows.
"""

import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))

_CHILD = r'''
import json
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
from scriptorium.web.server import _Handler


@pytest.fixture(scope="module")
def base():
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
    def describe(self):
        return "stub"

    def complete(self, system, user):
        items = json.loads(user[user.index("["):])
        return json.dumps({i["id"]: "山丘上站著一段文字，長度合乎一段譯文。"
                                    + "".join(re.findall(r"⟦\d+⟧", i["text"]))
                           for i in items}, ensure_ascii=False)


@pytest.fixture
def started(base, tmp_path, monkeypatch):
    """A two-paragraph document, extracted, and a function that starts a draft run."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d.md").write_bytes(b"The gate stood open when she came down the hill.\n\n"
                                    b"She went in anyway, and it swung shut behind her.\n")
    _post(base, "/api/extract", {"src": "d.md", "lang": "zh-TW"})

    def start():
        job = _post(base, "/api/translate", {"src": "d.md", "lang": "zh-TW"})
        assert job["total"] == 2
        return job["id"]
    return start


def _stub(monkeypatch):
    monkeypatch.setattr(translate_mod, "build_provider", lambda name, cfg, model=None: _Echo())


def _closed_loopback_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_follows_both_rules(base, started, monkeypatch):
    _stub(monkeypatch)
    assert _finish(base, started())["applied"] == 2


def test_forgets_the_wait(base, started, monkeypatch):
    _stub(monkeypatch)
    started()


def test_forgets_the_stub(base, started):
    job = _finish(base, started())
    assert job["applied"] == 0


def test_forgets_the_wait_on_a_job_that_selects_nothing(base, started):
    _post(base, "/api/translate", {"src": "d.md", "lang": "zh-TW", "ids": ["nope"]})


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
'''

_CHILD_OUTSIDE = r'''
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
'''


def _run_child(tmp_path, source):
    with open(os.path.join(_HERE, "conftest.py"), "rb") as f:
        (tmp_path / "conftest.py").write_bytes(f.read())
    (tmp_path / "test_child.py").write_text(
        source.replace("sys.path.insert(0, SRC)", f"sys.path.insert(0, {_SRC!r})"),
        encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-rA", "-vv", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path), "test_child.py"],
        cwd=str(tmp_path), env=env, capture_output=True, timeout=300)
    # A child's stdout is a text stream, which writes CRLF on Windows.
    return done.returncode, done.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def _outcomes(out):
    """`{test name: outcome}` from the short summary, the teardown error winning."""
    seen = {}
    for line in out.splitlines():
        m = re.match(r"(PASSED|FAILED|ERROR) test_child\.py::(\w+)", line)
        if m and seen.get(m.group(2)) != "ERROR":
            seen[m.group(2)] = m.group(1)
    return seen


def _teardown_error(out, name):
    # A section ends where the next header starts: captured output, another
    # test's section, or the next banner. Long names get a single underscore.
    m = re.search(r"_+ ERROR at teardown of " + name + r" _+\n(.*?)(?=\n-{3,} |\n_+ |\n=+ )",
                  out, re.S)
    assert m, f"no teardown error for {name}:\n{out}"
    return m.group(1)


def test_the_guard_fails_exactly_the_tests_that_break_a_rule(tmp_path):
    code, out = _run_child(tmp_path, _CHILD)
    assert code == 1, out
    assert _outcomes(out) == {
        "test_follows_both_rules": "PASSED",
        "test_forgets_the_wait": "ERROR",
        "test_forgets_the_stub": "ERROR",
        "test_forgets_the_wait_on_a_job_that_selects_nothing": "ERROR",
        "test_swallows_a_refusal_on_a_loopback_port_nobody_holds": "ERROR",
        "test_swallows_a_refusal_on_a_port_this_process_holds_at_another_host": "ERROR",
        "test_dials_a_dead_end": "PASSED",
        "test_dials_a_server_it_holds": "PASSED",
    }, out

    said = _teardown_error(out, "test_forgets_the_wait")
    assert "never saw it finish" in said and "refused" not in said, said
    assert "never saw it finish" in _teardown_error(
        out, "test_forgets_the_wait_on_a_job_that_selects_nothing")

    # The job was waited for, so the refusal is the only thing wrong, and it is
    # named first: a message blaming a wait the test has would send its reader
    # to the wrong line.
    said = _teardown_error(out, "test_forgets_the_stub")
    assert re.search(r"refused a connection to (\[::1\]|127\.0\.0\.1):11434", said), said
    assert "never saw it finish" not in said, said

    assert "refused a connection to 127.0.0.1:" in _teardown_error(
        out, "test_swallows_a_refusal_on_a_loopback_port_nobody_holds")
    assert "refused a connection to 0.0.0.0:" in _teardown_error(
        out, "test_swallows_a_refusal_on_a_port_this_process_holds_at_another_host")


def test_a_refusal_no_test_checked_still_fails_the_run(tmp_path):
    """A module fixture's teardown runs after the test's own check. Unreported,
    such a refusal was refused and the run exited 0 reading "1 passed"."""
    code, out = _run_child(tmp_path, _CHILD_OUTSIDE)
    assert _outcomes(out) == {"test_passes": "PASSED"}, out
    assert code == 1, out
    assert "connections refused outside a test's check" in out, out
