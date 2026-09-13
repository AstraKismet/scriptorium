"""What the processes the test suite starts dial — the half `tests/conftest.py` cannot see.

    python tests/record_child_network.py [--ref REF] [--keep] [--timeout S] [-- PYTEST_ARGS...]

Run it with an interpreter that has pytest. Exit codes:

- **0** — no process the suite started connected to anything but a loopback port
  that process had bound itself or one of the dead ends in `DEAD_PORTS`, and none
  looked up a name that can leave the machine.
- **1** — at least one did. Each is printed with its process's arguments.
- **2** — the question was not answered: not run from this repository, the ref
  does not name a commit, pytest did not run, or the recorder did not see the
  connection it plants as its own control.

**Why a measurement rather than a guard.** `docs/decisions.md`, 2026-09-14. Two
guards for child processes were built and red-teamed; each let through a
connection the other caught, and each broke something that works today. After
HANDOFF-082 fixed the one child that dialled out, this recorder counted none.

**How.** `git archive REF` into a temporary directory — never the working tree,
so nothing injected here can be committed — then append a recording audit hook
to that copy's `src/scriptorium/__init__.py`, add one planted test whose child
dials a closed loopback port, and run the suite there with this interpreter. The
hook lives in the package, and its output path is written into the file, because
`tests/test_config.py::_env` hands its children a minimal environment on purpose
and `tests/test_startup_imports.py` starts its children with `-S`: an environment
variable and `sitecustomize` each miss one of those, measured on 2026-09-13.

**What it cannot see**, which is printed with every answer:

- a process that never imports `scriptorium` — a child pytest over files that do
  not import it, a shell, any non-Python program. The count of processes the suite
  started beside the count that installed the recorder is how big that is;
- a connection refused by an audit hook registered *earlier* in the same process:
  a later hook never sees an event an earlier one raised on. That is the
  `tests/conftest.py` copy inside `tests/test_conftest_guard.py`'s child pytests,
  and such a connection never reached the operating system;
- a datagram, a raw WinSock or BSD call through `ctypes`, and anything a process
  does before `scriptorium` is imported.
"""

import argparse
import importlib.util
import io
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile

#: `tests/conftest.py`'s own dead ends. A copy, because conftest is not a module
#: outside pytest; `tests/test_record_child_network.py` asserts the two are equal.
DEAD_PORTS = frozenset({1, 9})

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROL_TEST = "test_zz_record_child_network_control.py"

#: Appended to the archived copy's `scriptorium/__init__.py`. It loads nothing a
#: `-S` interpreter has not already loaded, so the modules
#: `tests/test_startup_imports.py` counts in a child are the modules it would
#: count without it, and a test asserts that. Not `os`: under `-S` it is not
#: loaded yet and importing it brings seven modules — measured — so the pid comes
#: from `nt` or `posix`, which the import system itself has already loaded. Every
#: failure inside the hook is swallowed: a recorder that raised would change the
#: branch the code under test takes, which is the thing being measured.
PROBE = r'''

def _record_child_network():
    import sys
    import _weakref
    if getattr(sys, "_record_child_network", False):
        return
    sys._record_child_network = True
    out = __OUT__
    system = sys.modules.get("nt") or sys.modules.get("posix")
    pid = system.getpid()

    def text(value):
        value = str(value)[:300]
        return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")

    def write(*fields):
        try:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("\t".join(text(f) for f in fields) + "\n")
        except Exception:
            pass

    argv = " ".join(sys.argv[:8])
    bound = []
    write("install", pid, system.getppid(), argv)

    def bound_here(port):
        for ref in list(bound):
            sock = ref()
            if sock is None:
                continue
            try:
                name = sock.getsockname()
            except Exception:
                continue
            if isinstance(name, tuple) and len(name) >= 2 and name[1] == port:
                return True
        return False

    def hook(event, args):
        try:
            if event == "socket.bind":
                bound.append(_weakref.ref(args[0]))
            elif event == "socket.connect":
                address = args[1]
                if isinstance(address, tuple) and len(address) >= 2:
                    write("connect", pid, address[0], address[1],
                          "self" if bound_here(address[1]) else "other", argv)
            elif event == "socket.getaddrinfo":
                write("lookup", pid, args[0], args[1], argv)
            elif event == "subprocess.Popen":
                write("spawn", pid, args[1], "")
        except Exception:
            pass

    sys.addaudithook(hook)


_record_child_network()
'''

CONTROL = r'''"""Planted by tests/record_child_network.py into an archived copy; never committed.

A child in a minimal environment dials a closed loopback port. If the recorder
does not report it, the recorder is broken and says so with exit 2.
"""
import os
import subprocess
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
PORT = __PORT__


def test_the_recorder_sees_a_child_nothing_guards():
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    env = {name: value for name, value in os.environ.items() if name in keep}
    env["PYTHONPATH"] = SRC
    source = ("import scriptorium, socket\n"
              "try:\n"
              "    socket.create_connection(('127.0.0.1', %d), 5).close()\n"
              "except OSError:\n"
              "    pass\n" % PORT)
    subprocess.run([sys.executable, "-c", source], env=env, timeout=120)
'''


def loopback(host):
    """`tests/conftest.py::_loopback`, asked of recorded text rather than a socket."""
    host = str(host)
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (getattr(ip, "ipv4_mapped", None) or ip).is_loopback


def leaves_the_machine(host):
    """Whether resolving `host` can send a query anywhere: a name that is not `localhost`.

    An address literal resolves without a query, loopback or not, and so does an
    empty host.
    """
    host = str(host)
    if not host or host.lower() == "localhost":
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


def parse(lines):
    """Rows written by `PROBE`, split into fields; a line that is not one is skipped."""
    rows = []
    widths = {"install": 4, "connect": 6, "lookup": 5, "spawn": 4}
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if fields and widths.get(fields[0]) == len(fields):
            rows.append(fields)
    return rows


def _port(value):
    try:
        return int(value)
    except ValueError:
        return None


def analyse(rows, top, control_port):
    """Answer from recorded rows: a dict the report and the exit code are read from.

    `top` is the pid `subprocess.Popen` returned for pytest, which is the one
    process `tests/conftest.py` guards, so its rows are counted and never judged.
    Where that pid never loaded the recorder and exactly one process that did
    names it as its parent, that process is pytest: a Windows virtual
    environment's `python.exe` is a launcher that starts the real interpreter.
    `control_port` is the closed port the planted control dials; its row is taken
    out of the findings and decides whether the recorder saw anything at all.
    """
    top = str(top)
    parents = {r[1]: r[2] for r in rows if r[0] == "install"}
    if top not in parents:
        started = [pid for pid, parent in parents.items() if parent == top]
        if len(started) == 1:
            top = started[0]
    # Rows, not distinct pids: Windows hands a finished child's pid to the next
    # one, so a set of pids counted 318 processes where 327 had installed —
    # measured. The test process runs throughout, so no child can reuse `top`.
    installed_below = sum(1 for r in rows if r[0] == "install" and r[1] != top)
    spawned_by_top = sum(1 for r in rows if r[0] == "spawn" and r[1] == top)
    spawned_below = sum(1 for r in rows if r[0] == "spawn" and r[1] != top)
    connects, lookups, control_seen = [], [], False
    for r in rows:
        if r[1] == top:
            continue
        if r[0] == "connect":
            _, pid, host, port, verdict, argv = r
            port = _port(port)
            if loopback(host) and port == control_port and verdict == "other":
                control_seen = True
                continue
            if loopback(host) and (port in DEAD_PORTS or verdict == "self"):
                continue
            connects.append({"pid": pid, "host": host, "port": port, "argv": argv})
        elif r[0] == "lookup":
            _, pid, host, port, argv = r
            if leaves_the_machine(host):
                lookups.append({"pid": pid, "host": host, "port": port, "argv": argv})
    return {
        "top_installed": top in parents,
        "installed_below": installed_below,
        "spawned_by_top": spawned_by_top,
        "spawned_below": spawned_below,
        "control_seen": control_seen,
        "connects": connects,
        "lookups": lookups,
    }


def exit_code(answer):
    if not answer["top_installed"] or not answer["control_seen"]:
        return 2
    return 1 if answer["connects"] or answer["lookups"] else 0


def _closed_loopback_port():
    # Bound and closed: nothing listens there, and the number is not one of
    # `DEAD_PORTS`, so a recorder that sees it has to have judged it.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True)


def _say(message=""):
    print(message, flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="record_child_network.py",
        description="Measure what the processes the test suite starts dial.")
    parser.add_argument("--ref", default="HEAD", help="commit to measure (default HEAD)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the archived copy and the raw rows, and print where")
    parser.add_argument("--timeout", type=float, default=3600.0,
                        help="seconds the suite may run (default 3600)")
    parser.add_argument("pytest_args", nargs="*",
                        help="after --, arguments for pytest (default: the whole suite)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")

    top_level = _git("rev-parse", "--show-toplevel")
    if top_level.returncode != 0 or (
            os.path.normcase(os.path.realpath(top_level.stdout.decode().strip()))
            != os.path.normcase(os.path.realpath(ROOT))):
        _say("record_child_network: not a git checkout of this repository; nothing measured.")
        return 2
    commit = _git("rev-parse", "--verify", "--quiet", args.ref + "^{commit}")
    if commit.returncode != 0:
        _say(f"record_child_network: {args.ref!r} does not name a commit; nothing measured.")
        return 2
    commit = commit.stdout.decode().strip()
    if importlib.util.find_spec("pytest") is None:
        _say(f"record_child_network: {sys.executable} has no pytest; run this with the "
             "interpreter the suite runs on.")
        return 2
    if args.ref == "HEAD" and _git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        _say("record_child_network: the working tree has uncommitted changes, and they are "
             "not measured — only the commit is archived.")

    work = tempfile.mkdtemp(prefix="record-child-network-")
    try:
        return _measure(args, commit, work)
    finally:
        if args.keep:
            _say(f"kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def _measure(args, commit, work):
    copy = os.path.join(work, "copy")
    rows_path = os.path.join(work, "rows.tsv")
    blob = _git("archive", "--format=tar", commit)
    if blob.returncode != 0:
        _say("record_child_network: git archive failed; nothing measured.")
        return 2
    with tarfile.open(fileobj=io.BytesIO(blob.stdout)) as tar:
        # `filter` is 3.12's spelling and absent on 3.9; the archive is this
        # repository's own commit, so the permissive default is not a hazard.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(copy, filter="data")
        else:
            tar.extractall(copy)
    with open(os.path.join(copy, "src", "scriptorium", "__init__.py"), "ab") as fh:
        fh.write(PROBE.replace("__OUT__", repr(rows_path)).encode("utf-8"))
    control_port = _closed_loopback_port()
    with open(os.path.join(copy, "tests", CONTROL_TEST), "wb") as fh:
        fh.write(CONTROL.replace("__PORT__", str(control_port)).encode("utf-8"))

    selection = list(args.pytest_args)
    if selection:
        selection.append(os.path.join("tests", CONTROL_TEST))
    env = dict(os.environ)
    # `tests/test_cli.py` reproduces a stdout that cannot encode its output, and
    # this variable is what removes that condition.
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONPATH"] = os.path.join(copy, "src")
    log_path = os.path.join(work, "pytest.log")
    _say(f"record_child_network: measuring {args.ref} ({commit[:12]}) in an archived copy; "
         "the suite takes a few minutes.")
    with open(log_path, "wb") as log:
        proc = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                 *selection], cwd=copy, env=env, stdout=log,
                                stderr=subprocess.STDOUT)
        try:
            status = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _say(f"record_child_network: the suite ran past {args.timeout:g} s and was "
                 "stopped; nothing is claimed.")
            return 2
    with open(log_path, "rb") as log:
        tail = [line for line in log.read().decode("utf-8", "replace").splitlines()
                if line.strip()][-1:]
    _say(f"suite: {tail[0] if tail else '(no output)'} — pytest exit {status}")
    if status not in (0, 1):
        _say("record_child_network: pytest did not run the suite; nothing is claimed.")
        return 2
    if status == 1:
        _say("  the suite failed, so what its failing tests would have started was not measured.")

    lines = []
    if os.path.exists(rows_path):
        with open(rows_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    answer = analyse(parse(lines), proc.pid, control_port)
    return _report(answer, control_port)


def _report(answer, control_port):
    _say(f"processes: the test process started {answer['spawned_by_top']}, and those started "
         f"{answer['spawned_below']}; {answer['installed_below']} of them imported scriptorium "
         "and were recorded.")
    uncovered = answer["spawned_by_top"] + answer["spawned_below"] - answer["installed_below"]
    if uncovered > 0:
        _say(f"  at least {uncovered} started process(es) never imported scriptorium and were "
             "not seen at all.")
    _say("  not seen in any process: a connection an earlier audit hook refused, a datagram, "
         "a call through ctypes.")
    if not answer["top_installed"]:
        _say("record_child_network: the test process never loaded the recorder, so no count "
             "above means anything.")
        return 2
    if not answer["control_seen"]:
        _say(f"record_child_network: the planted child dialled 127.0.0.1:{control_port} and "
             "the recorder did not report it. The instrument is broken; nothing is claimed.")
        return 2
    _say(f"control: the planted child's connection to 127.0.0.1:{control_port} was recorded.")
    for c in answer["connects"]:
        _say(f"CONNECT pid {c['pid']} -> {c['host']}:{c['port']}  ({c['argv']})")
    for c in answer["lookups"]:
        _say(f"LOOKUP  pid {c['pid']} -> {c['host']}:{c['port']}  ({c['argv']})")
    code = exit_code(answer)
    if code == 0:
        _say("no started process connected to anything but a dead end or a port it bound "
             "itself, and none looked up a name.")
    else:
        _say(f"{len(answer['connects'])} connection(s) and {len(answer['lookups'])} name "
             "lookup(s) by started processes reached past what tests/conftest.py allows.")
    return code


if __name__ == "__main__":
    sys.exit(main())
