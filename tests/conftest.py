"""Two rules every test here is held to, checked rather than remembered.

**Tests use no network.** A connection may go to a loopback port that a socket
in this process has bound — a mock backend, the mock proxy, the workbench's own
server — or to one of the suite's two dead ends, loopback ports 1 and 9, which
tests dial *in order to be refused*. Any other `connect` is refused before the
operating system sees it, and the test that was running fails at teardown
naming the address. Before this existed, three tests left a translation job
dialling `localhost:11434` — `DEFAULT_CONFIG`'s route, and Ollama's default
port — so on a machine running a model server the suite sent the fixture's text
to it.

**A job is waited for by the test that started it.** `POST /api/translate`
answers the moment its thread starts, and `monkeypatch` undoes the test's
provider stub and its `chdir` the moment the test returns — so an unwaited job
builds the configured provider in the middle of some later test. Stubbing
without waiting protects nothing, which is why both halves are checked. The test
must see `done: true` from `POST /api/job` for every job it started
(`tests/jobwait.py`).

That rule is decided by what the test *saw*, never by whether a thread is still
alive at teardown. A job that selects nothing is over before its request
answers, and a stubbed one within milliseconds, so a liveness check passes a
test whose wait was deleted on almost every run and fails it on a loaded
machine: a guard that is right by scheduling is a flake. What the test polled
decides, and a test that polls once rather than until done is judged by what
that one poll happened to see.

`docs/decisions.md`, 2026-09-13, has the measurement and the designs that lost.
What this cannot see is stated there too: a subprocess (the hook lives in this
interpreter only — `tests/record_child_network.py` measures what children dial,
and `docs/decisions.md`, 2026-09-14, says why that is not a guard), a name
lookup (`getaddrinfo` runs before any connect and is not refused), a datagram
sent without a connect, and a thread a test starts itself and leaves running,
whose refusal is charged to whichever test is running when it dials.
"""

import functools
import ipaddress
import socket
import sys
import threading
import weakref

import pytest

#: Loopback ports a test may dial because nothing listens there. The refusal is
#: the operating system's, untouched, since the tests that use them assert on
#: what the provider makes of a real one — on Windows port 1 times out rather
#: than refusing, and a synthetic refusal would reach a different branch.
DEAD_PORTS = frozenset({1, 9})

#: How long teardown waits for a job the test did not wait for. Never a
#: decision — that test has already failed — only a bound on how long a broken
#: test can hold the suite, and what keeps its job from running on into the
#: next test, still under the stub and the directory it was started with.
CLEANUP_SECONDS = 120.0

_lock = threading.RLock()
#: A weak reference to every socket this process has bound, so a connection to
#: one of them can be told from one that leaves. References rather than a
#: `WeakSet`, whose removal callback mutates it outside any lock when a socket
#: is collected — and a snapshot taken during that raised `RuntimeError` out of
#: `socket.connect` on 3.9, which no caller handles. Measured under a stress
#: probe; never seen in the suite. A closed socket is skipped by `getsockname`
#: raising, so weakness only keeps the list from holding every socket alive.
_bound = []
#: `jobs` is the running test's ledger, replaced when a test *starts* rather
#: than when its first function fixture runs: a module-scoped fixture set up
#: for this test runs before any function fixture, and a job it starts belongs
#: to this test like any other.
_state = {"test": None, "jobs": {}, "refused": []}


class NetworkRefused(Exception):
    """Raised at `socket.connect` for a destination nothing in this process bound.

    Deliberately **not** an `OSError`. The provider retries an `OSError` with
    backoff and falls back to one request per segment, so an unstubbed job kept
    retrying against the refusal for tens of seconds — measured, 79.63 s for a
    run this class ends in 2.66 s — outlived its test's wait and its teardown,
    and was charged to whichever innocent test was running when it dialled next.
    Nothing on the path from a socket to a translation catches a plain
    `Exception` except the batch and per-segment loops, so the job fails at once
    and inside the test that started it. No test that follows the rules ever
    sees this class: a deliberately dead endpoint is one of `DEAD_PORTS`, whose
    refusal is left to the OS.
    """


def _text(host):
    if isinstance(host, (bytes, bytearray)):
        return bytes(host).decode("ascii", "replace")
    return str(host)


def _loopback(host):
    host = _text(host)
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # How an IPv4-mapped address answers `is_loopback` has changed between
    # versions, so the mapped half is asked directly.
    return (getattr(ip, "ipv4_mapped", None) or ip).is_loopback


def _address(host, port):
    host = _text(host)
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _bound_here(port):
    with _lock:
        refs = list(_bound)
    for ref in refs:
        sock = ref()
        if sock is None:
            continue
        try:
            name = sock.getsockname()
        except OSError:  # closed since it was bound
            continue
        if isinstance(name, tuple) and len(name) >= 2 and name[1] == port:
            return True
    return False


def _audit(event, args):
    if event == "socket.connect":
        sock, address = args
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return
        host, port = address[0], address[1]
        if _loopback(host) and (port in DEAD_PORTS or _bound_here(port)):
            return
        with _lock:
            _state["refused"].append({"test": _state["test"], "host": _text(host),
                                      "port": port, "thread": threading.current_thread().name})
        # Closed here because a non-`OSError` skips `create_connection`'s own
        # cleanup, and an unclosed socket is a `ResourceWarning` in some later test.
        try:
            sock.close()
        except OSError:
            pass
        # ASCII only, and the address alone: this text reaches code under test,
        # and a credential-masking test reads what a provider says about a failure.
        raise NetworkRefused(f"tests/conftest.py refused a connection to "
                             f"{_address(host, port)}: tests use no network")
    if event == "socket.bind":
        try:
            ref = weakref.ref(args[0])
        except TypeError:  # not weak-referenceable, so not a server a test started
            return
        with _lock:
            _bound[:] = [r for r in _bound if r() is not None]
            _bound.append(ref)


sys.addaudithook(_audit)


def _job(job_id):
    jobs = _state["jobs"]
    record = jobs.get(job_id)
    if record is None:
        record = jobs[job_id] = {"started": False, "seen_done": False, "thread": None,
                                 "over": threading.Event()}
    return record


def _instrument(server):
    """Wrap the three job functions the request handler and the job thread call by name.

    `_translate_job` and not `_mint_job`: the job table's own tests mint and
    finish states directly, with no thread, and are not jobs anybody waits for.
    A record is created by whichever wrapper touches it first, because a job
    that selects nothing finishes before `_translate_job` has returned its id.
    """
    if getattr(server, "_conftest_instrumented", False):
        return
    translate_job, job_status, finish_job = (
        server._translate_job, server._job_status, server._finish_job)

    @functools.wraps(translate_job)
    def _translate_job(*args, **kwargs):
        answer = translate_job(*args, **kwargs)
        with _lock:
            _job(answer["id"])["started"] = True
        return answer

    @functools.wraps(job_status)
    def _job_status(job_id):
        answer = job_status(job_id)
        if answer.get("done") is True:
            with _lock:
                _job(answer["id"])["seen_done"] = True
        return answer

    @functools.wraps(finish_job)
    def _finish_job(state):
        with _lock:
            record = _job(state["id"])
            record["thread"] = threading.current_thread()
        try:
            return finish_job(state)
        finally:
            record["over"].set()

    server._translate_job = _translate_job
    server._job_status = _job_status
    server._finish_job = _finish_job
    server._conftest_instrumented = True


def pytest_collection_finish(session):
    # Here rather than per test, so a job started by a test that imports the
    # server inside its own body or a fixture is still counted. Only when some
    # collected module already imported the package: a run of files that never
    # touch `scriptorium` has no job to start, and may have no `src` on its path.
    if "scriptorium" in sys.modules:
        import scriptorium.web.server as server
        _instrument(server)


def pytest_runtest_logstart(nodeid, location):
    # Before the item's first fixture, so a refusal while a module-scoped
    # fixture is set up is charged to the test whose setup triggered it, and a
    # job such a fixture starts lands in this test's ledger.
    with _lock:
        _state["test"] = nodeid
        _state["jobs"] = {}


def pytest_runtest_logfinish(nodeid, location):
    with _lock:
        _state["test"] = None


@pytest.fixture(autouse=True)
def _no_network_and_no_unwaited_job(request, monkeypatch):
    # `monkeypatch` is requested so this teardown runs *before* the test's own
    # patches are undone, which is what lets a job this test forgot finish under
    # the stub and in the directory it was started with.
    yield
    with _lock:
        started = [(job_id, rec) for job_id, rec in _state["jobs"].items() if rec["started"]]
    unwaited = []
    for job_id, record in started:
        if not record["seen_done"]:
            unwaited.append(job_id)
        # Cleanup, after the decision above. A job that was seen done is already
        # over and this returns at once.
        if record["over"].wait(CLEANUP_SECONDS) and record["thread"] is not None:
            record["thread"].join(CLEANUP_SECONDS)
    with _lock:
        nodeid = request.node.nodeid
        mine = [r for r in _state["refused"] if r["test"] == nodeid]
        _state["refused"] = [r for r in _state["refused"] if r["test"] != nodeid]
    problems = []
    # The refusal first: a test that removed its stub but kept its wait may also
    # have a job it never saw finish, and the refusal is the cause.
    for where, threads in _by_address(mine).items():
        problems.append(
            f"refused a connection to {where} from thread {', '.join(sorted(threads))}: "
            f"tests use no network. Stub translate.build_provider before starting a job "
            f"(tests/test_web.py does it in _no_network), dial a mock server the test "
            f"binds, or use 127.0.0.1:9 for an endpoint that must be dead.")
    for job_id in unwaited:
        problems.append(
            f"{job_id} was started by POST /api/translate and this test never saw it "
            f"finish. A job outlives the request that started it: poll POST /api/job "
            f"until done (tests/jobwait.py: finish(base, id)), even when the assertion "
            f"is only on `total`. If the test failed before reaching its wait, that "
            f"failure is the one to read.")
    if problems:
        pytest.fail("\n".join(problems), pytrace=False)


def _by_address(refusals):
    out = {}
    for r in refusals:
        out.setdefault(_address(r["host"], r["port"]), set()).add(r["thread"])
    return out


def pytest_sessionfinish(session, exitstatus):
    # Whatever no test's teardown consumed: a refusal between tests, or in a
    # wider-scoped fixture's teardown, which runs after the test's own check.
    # Left unreported, those were refused and the run still exited 0. Only over
    # a run that would otherwise have passed, so an interrupt or a usage error
    # keeps the status that says what really happened.
    with _lock:
        left = list(_state["refused"])
    if left and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    with _lock:
        left = list(_state["refused"])
    if not left:
        return
    terminalreporter.section("tests/conftest.py: connections refused outside a test's check")
    for r in left:
        terminalreporter.line(
            f"{_address(r['host'], r['port'])} from thread {r['thread']} "
            f"(while {r['test'] or 'no test'} was running): tests use no network")
