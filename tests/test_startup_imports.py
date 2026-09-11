"""What `lx` loads before it has decided to talk to a backend, and the name it must bind.

Every command imports `scriptorium.providers`, whichever submodule it names:
Python executes a package's `__init__` before it binds a submodule, and
`providers/__init__.py` imports `base`, `anthropic` and `openai_compat` to build
`KINDS`. So `base` keeps the transport — `urllib`, `http.client`, `socket`, and
through them `ssl` and the `email` package — out of its module scope and imports
it inside `Provider._request`, the one function that uses it.

Until 2026-09-11 `providers/errors.py` claimed that naming the class from there
kept the transport off the import path, and it never had: `lx --help` loaded all
of it on 3.9 through 3.12. A claim about what an import costs is one nothing
checks unless a test does. The other half is the name itself — `cli.main`
catches `ProviderError` by name, and nothing asserted what it does with one.
"""

import argparse
import ast
import importlib
import json
import os
import shutil
import subprocess
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)
PACKAGE = os.path.join(SRC, "scriptorium")

#: The transport `Provider._request` imports, and what comes with it. `email` is
#: the package; its fourteen submodules cannot load without it. Each of the four
#: names `_request` imports is listed itself, so moving any one of them back to
#: module scope fails here — not only the one that drags the rest in.
FORBIDDEN = ("ssl", "urllib.request", "urllib.error", "http.client", "socket", "email")

#: Seven commands covering four of the routes into the providers package, each run
#: in an interpreter of its own and each with a marker its own output must
#: contain — the proof that the command really ran, since a probe that never
#: reached `main` would otherwise report nothing loaded and pass.
#:
#: `--help` is `cli`'s module scope and everything it imports, `audit` and
#: `providers.errors` among them; `config get` and `status --json`, the frozen
#: contract the bookshelf runs, take the same route. `providers` reaches
#: `providers.available` and `config set ….kind` reaches `providers.KINDS`, both
#: through function-local imports. `extract` and `todo` import `translate`,
#: which imports `providers.build` at its own module scope — the route every
#: model-free command that shares code with a run takes.
COMMANDS = [
    (["--help"], b"usage:"),
    (["config", "get"], b'"providers"'),
    (["status", "--json"], b'"contract_version"'),
    (["providers"], b"routing:"),
    (["config", "set", "providers.local.kind", "openai"], b"providers.local.kind:"),
    (["extract", "doc.md", "--lang", "zh-TW"], b"segments 2"),
    (["todo", "doc.md", "--lang", "zh-TW"], b'"source": "doc.md"'),
]

#: Every command `cli.build_parser` defines except those in `NOT_SWEPT`, each in
#: a form that sends nothing, as `(directory, argv)` and run in this order in
#: one interpreter — which reaches the routes the seven above do not — so state
#: flows: `apply` before `hold`, the carry before `forget`, `--reset` last.
#: `models --provider filey` is a request refused before it is sent: the
#: scaffold's `filey` backend has a `file:///` base_url, which `_request`
#: refuses above its imports. `test_every_command_is_swept_or_named` is what
#: keeps this list complete.
SWEEP = [
    ("empty", ["init"]),
    (".", ["--version"]),
    (".", ["--help"]),
    (".", ["providers"]),
    (".", ["models", "--provider", "nope"]),
    (".", ["models", "--provider", "filey"]),
    (".", ["config", "get"]),
    (".", ["config", "get", "providers"]),
    (".", ["config", "set", "batch.size", "7"]),
    (".", ["config", "set", "providers.local.kind", "openai"]),
    (".", ["config", "unset", "batch.size"]),
    (".", ["routing", "show"]),
    (".", ["routing", "set", "draft", "local"]),
    (".", ["extract", "doc.md", "--lang", "zh-TW"]),
    (".", ["todo", "doc.md", "--lang", "zh-TW"]),
    (".", ["terms", "doc.md", "--lang", "zh-TW", "--min-count", "1"]),
    (".", ["glossary", "get"]),
    (".", ["glossary", "set", "Fox", "狐狸"]),
    (".", ["glossary", "unset", "Fox"]),
    (".", ["apply", "doc.md", "--lang", "zh-TW", "--file", "t.json"]),
    (".", ["hold", "doc.md", "--lang", "zh-TW", "--ids", "s0001"]),
    (".", ["unhold", "doc.md", "--lang", "zh-TW", "--ids", "s0001"]),
    (".", ["waive", "doc.md", "--lang", "zh-TW", "--ids", "s0001"]),
    (".", ["unwaive", "doc.md", "--lang", "zh-TW", "--ids", "s0001"]),
    (".", ["check", "doc.md", "--lang", "zh-TW"]),
    (".", ["check", "doc.md", "--lang", "zh-TW", "--json"]),
    (".", ["render", "doc.md", "--lang", "zh-TW", "-o", "-", "--fallback"]),
    (".", ["blocks", "doc.md", "--lang", "zh-TW", "--json"]),
    (".", ["bytes", "doc.md", "--lang", "zh-TW"]),
    (".", ["segments", "doc.md", "--lang", "zh-TW"]),
    (".", ["sentences", "doc.md", "--lang", "zh-TW"]),
    (".", ["style", "doc.md", "--lang", "zh-TW"]),
    (".", ["suggest", "doc.md", "--lang", "zh-TW"]),
    (".", ["audit", "--lang", "zh-TW"]),
    (".", ["audit", "doc.md", "--lang", "zh-TW"]),
    (".", ["renderings", "--lang", "zh-TW"]),
    (".", ["renderings", "--lang", "zh-TW", "--term", "Fox"]),
    (".", ["commit", "doc.md", "--lang", "zh-TW"]),
    (".", ["stats"]),
    (".", ["status"]),
    (".", ["status", "--json"]),
    (".", ["status", "--scan", "."]),
    (".", ["untracked"]),
    (".", ["translate", "doc.md", "--lang", "zh-TW", "--dry-run"]),
    (".", ["translate", "doc.md", "--lang", "zh-TW", "--mode", "polish", "--dry-run"]),
    (".", ["repair", "doc.md", "--lang", "zh-TW", "--dry-run"]),
    (".", ["run", "doc.md", "--lang", "zh-TW", "--dry-run"]),
    (".", ["translate", "doc.md", "--lang", "zh-TW", "--provider", "nope"]),
    (".", ["run", "doc.md", "--lang", "zh-TW", "--provider", "nope"]),
    (".", ["extract", "doc2.md", "--lang", "zh-TW", "--from", "doc.md"]),
    (".", ["forget", "doc2.md", "--lang", "zh-TW"]),
    (".", ["extract", "doc.md", "--lang", "zh-TW", "--reset", "--tone", "technical"]),
]

#: The commands the sweep leaves out, and why. `lx web` starts a server that
#: does not return, and `http.server` imports `ssl`, `http.client`, `socket` and
#: the `email` package itself: the workbench pays for its own transport whatever
#: `base` does, and only `urllib.request` and `urllib.error` wait there for a
#: request.
NOT_SWEPT = {("web",)}

_PROBE = """
import json, sys
import scriptorium.cli
code = None
try:
    code = scriptorium.cli.main(json.loads(sys.argv[1])) or 0
except SystemExit as e:
    code = e.code
{after}
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump({{"code": code,
               "loaded": [m for m in {forbidden!r} if m in sys.modules]}}, f)
"""

_SWEEP = """
import io, json, os, sys
import scriptorium.cli
with open(sys.argv[1], encoding="utf-8") as f:
    plan = json.load(f)
root = os.getcwd()
real = sys.stdout, sys.stderr
steps = []
for where, argv in plan["steps"]:
    os.chdir(os.path.join(root, where))
    out, err = io.BytesIO(), io.BytesIO()
    wout = io.TextIOWrapper(out, encoding="utf-8", write_through=True)
    werr = io.TextIOWrapper(err, encoding="utf-8", write_through=True)
    sys.stdout, sys.stderr = wout, werr
    code = None
    try:
        code = scriptorium.cli.main(argv) or 0
    except SystemExit as e:
        code = e.code
    finally:
        wout.flush()
        werr.flush()
        sys.stdout, sys.stderr = real
    steps.append({"argv": argv, "code": code,
                  "printed": len(out.getvalue()) + len(err.getvalue()),
                  "loaded": [m for m in plan["forbidden"] if m in sys.modules]})
os.chdir(root)
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(steps, f)
"""


def _env():
    return dict(os.environ, PYTHONPATH=SRC, PYTHONDONTWRITEBYTECODE="1")


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory):
    """A project from `lx init`: two extracted documents and one applied wording.

    Built once, by separate processes, so nothing they import reaches a probe.
    It also carries a backend whose `base_url` is `file:///x`, written by hand
    because `lx config set` refuses that shape, and an empty directory for the
    sweep's `lx init`.
    """
    root = tmp_path_factory.mktemp("startup") / "proj"
    root.mkdir()
    (root / "empty").mkdir()
    (root / "doc.md").write_bytes(
        b"The quick brown fox jumps over the lazy dog.\n\nFox runs. Fox sleeps.\n")
    (root / "doc2.md").write_bytes(b"The quick brown fox jumps over the lazy dog.\n")
    (root / "t.json").write_text(json.dumps({"s0001": "敏捷的狐狸"}),
                                 encoding="utf-8")
    for argv in (["init"], ["extract", "doc.md", "--lang", "zh-TW"],
                 ["extract", "doc2.md", "--lang", "zh-TW"],
                 ["apply", "doc.md", "--lang", "zh-TW", "--file", "t.json"]):
        out = subprocess.run([sys.executable, "-B", "-m", "scriptorium", *argv],
                             env=_env(), cwd=str(root), capture_output=True, timeout=60)
        assert out.returncode == 0, (argv, out.stderr.decode("utf-8", "replace"))
    config = root / "lx.config.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    data["providers"]["filey"] = {"kind": "openai", "base_url": "file:///x", "model": "m"}
    config.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return root


@pytest.fixture
def project(scaffold, tmp_path):
    """A private copy: some commands write to the project, and so does the sweep."""
    return shutil.copytree(str(scaffold), str(tmp_path / "proj"))


def _probe(argv, cwd, after=""):
    """Run one `lx` command in a clean interpreter; ``(report, stdout bytes)``.

    A subprocess because `sys.modules` in this one is whatever the rest of the
    suite has already imported, which would make the answer depend on test
    ordering. `-S` keeps `site` and every `.pth` file from importing anything —
    an editable install's finder, a coverage hook — so what the probe sees is
    what `scriptorium` itself loaded. The report goes to a file and stdout is
    kept as bytes, so no platform's default encoding decides whether the
    command's own output can be read back.
    """
    report_path = os.path.join(str(cwd), "probe.json")
    source = _PROBE.format(after=after, forbidden=FORBIDDEN)
    out = subprocess.run(
        [sys.executable, "-S", "-B", "-c", source, json.dumps(argv), report_path],
        env=_env(), cwd=str(cwd), capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    with open(report_path, encoding="utf-8") as f:
        return json.load(f), out.stdout


@pytest.mark.parametrize("argv, marker", COMMANDS,
                         ids=["-".join(argv) for argv, _ in COMMANDS])
def test_a_command_that_sends_nothing_loads_no_transport(project, argv, marker):
    """None of these commands reaches a backend, so none of them loads one.

    A failure means something on the command's path imports the transport at
    module scope again, and every `lx` command pays for what only a request
    needs. The deferral lives in `providers/base.py`, `Provider._request`, and
    `base.py` says so beneath its imports: look there first, then at any module
    `cli`, `providers/__init__.py` or `translate` imports.
    """
    report, stdout = _probe(argv, project)
    shown = f"`lx {' '.join(argv)}`"
    assert report["code"] == 0, f"{shown} did not finish: {report}"
    assert marker in stdout, f"{shown} did not print {marker!r}: {stdout[-400:]!r}"
    assert report["loaded"] == [], (
        f"{shown} loaded {report['loaded']}. The transport is imported inside "
        f"`Provider._request` in providers/base.py so that a command which sends "
        f"nothing does not pay for it; something on this command's import path "
        f"now imports it at module scope.")


def test_no_command_that_sends_nothing_loads_the_transport(project):
    """Every command the parser defines, in a form that sends nothing, in one interpreter.

    The seven above name the route that failed; this is the net for everything
    else — a transport import inside one command's own function, or on the way
    to a provider that is built and then refuses to send. One interpreter for
    the lot, so it costs one start-up. A module one command loads stays loaded
    for the next, so the first command after which a forbidden module appears
    is the one named.

    Each command must reach an exit code this CLI uses — 0, 1 or 2, and which
    one depends on what the commands before it left behind, so it is not pinned
    — and must print something, captured per step. The second is the proof that
    it ran: a probe that stopped calling `main` would still record an exit code
    if it recorded one after the call, and it cannot make the command print.
    """
    plan = os.path.join(str(project), "sweep.json")
    with open(plan, "w", encoding="utf-8") as f:
        json.dump({"forbidden": FORBIDDEN, "steps": SWEEP}, f)
    report = os.path.join(str(project), "sweep-report.json")
    out = subprocess.run([sys.executable, "-S", "-B", "-c", _SWEEP, plan, report],
                         env=_env(), cwd=str(project), capture_output=True, timeout=300)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-2000:]
    with open(report, encoding="utf-8") as f:
        steps = json.load(f)
    assert [s["argv"] for s in steps] == [argv for _, argv in SWEEP]
    unfinished = [(" ".join(s["argv"]), s["code"]) for s in steps
                  if s["code"] not in (0, 1, 2)]
    assert not unfinished, f"these did not reach an exit code: {unfinished}"
    silent = [" ".join(s["argv"]) for s in steps if not s["printed"]]
    assert not silent, f"these printed nothing, so nothing shows they ran: {silent}"
    first = next((s for s in steps if s["loaded"]), None)
    assert first is None, (
        f"`lx {' '.join(first['argv'])}` loaded {first['loaded']}, and it sends "
        f"nothing. The transport belongs inside `Provider._request`, after the "
        f"scheme check — not in a command, not in `Provider.__init__`, not on any "
        f"path a refused request takes.")


def _command_paths(parser, prefix=()):
    """Every command path `parser` accepts: ``("config", "get")``, ``("stats",)``."""
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        return {prefix}
    paths = set()
    for name, child in subs[0].choices.items():
        paths |= _command_paths(child, prefix + (name,))
    return paths


def test_every_command_is_swept_or_named():
    """A command added later reaches the sweep by being classified, not remembered.

    Read from `cli.build_parser()` itself, so a new subcommand — or a new verb
    under `config`, `routing` or `glossary` — fails here until it is in `SWEEP`
    in a form that sends nothing, or in `NOT_SWEPT` with the reason beside it.
    """
    from scriptorium import cli

    paths = _command_paths(cli.build_parser())
    assert len(paths) >= 30, (
        f"the parser walk found {len(paths)} commands, which cannot be right — "
        f"teach `_command_paths` the parser's new shape rather than lowering this.")
    swept = {path for path in paths
             if any(tuple(argv[:len(path)]) == path for _, argv in SWEEP)}
    unclassified = sorted(" ".join(p) for p in paths - swept - NOT_SWEPT)
    assert not unclassified, (
        f"`lx {'`, `lx '.join(unclassified)}` is in neither SWEEP nor NOT_SWEPT. Add "
        f"it to SWEEP in a form that sends nothing, or to NOT_SWEPT with the reason "
        f"it cannot be run there.")
    assert NOT_SWEPT <= paths, (
        f"NOT_SWEPT names {sorted(NOT_SWEPT - paths)}, which the parser no longer has.")


def test_the_probe_sees_the_transport_when_it_is_loaded(project):
    """The positive control, so the tests above cannot pass by seeing nothing.

    Asserted on `urllib.request` and two of the forbidden modules it imports
    unconditionally, `http.client` and `socket`. `ssl` is imported inside a
    `try` by both it and `http.client`, so on an interpreter built without
    OpenSSL — the locked-down machine invariant 1 names — a control asserting it
    would fail for a reason that is not a defect.
    """
    report, stdout = _probe(["--help"], project, after="import urllib.request")
    assert report["code"] == 0 and b"usage:" in stdout, report
    missing = {"urllib.request", "http.client", "socket"} - set(report["loaded"])
    assert not missing, (
        f"after `import urllib.request` the probe reports {report['loaded']}, so it "
        f"cannot see {sorted(missing)} load, and the tests above prove nothing.")


def _module_scope(body):
    """Every statement Python executes at module scope — inside `if`, `try` and
    `with` blocks too, but never inside a function or a class body."""
    for node in body:
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            yield from _module_scope(getattr(node, field, None) or [])
        for handler in getattr(node, "handlers", None) or []:
            yield from _module_scope(handler.body)


def _module_level_bindings(name):
    """``{module: how}`` for each module that binds `name` at module scope.

    `how` is ``"class"`` for a class statement, ``"import"`` for an import and
    ``"assign"`` for an assignment; the identity check in the test is what
    refuses an assignment that makes a class of its own.
    """
    found = {}
    for dirpath, _dirs, files in os.walk(PACKAGE):
        if "__pycache__" in dirpath:
            continue
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(dirpath, fname)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            dotted = os.path.relpath(path, SRC)[:-len(".py")].replace(os.sep, ".")
            module = dotted[:-len(".__init__")] if dotted.endswith(".__init__") else dotted
            for node in _module_scope(tree.body):
                if isinstance(node, ast.ClassDef) and node.name == name:
                    found[module] = "class"
                elif isinstance(node, ast.ImportFrom) and any(
                        (alias.asname or alias.name) == name for alias in node.names):
                    found.setdefault(module, "import")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                        found.setdefault(module, "assign")
    return found


def test_provider_error_is_one_class_bound_at_module_scope_wherever_it_is_caught():
    """`cli.main`, `audit` and the workbench server catch `ProviderError` by name.

    Each `except` tuple is evaluated only once something has been raised, so the
    name has to be bound by then on every path — at module scope no reordering
    of the catching function can leave it unbound — and it has to be the class
    the providers raise. Moved into `main` after the first statement that can raise, the
    tuple names an unbound local for anything raised before the import runs,
    and that refusal becomes a traceback; a second class of the same name
    catches nothing the providers raise.

    Read with `ast` over every module rather than from a list, so a module that
    starts naming the class, or defines its own, is covered without an edit here.
    """
    bindings = _module_level_bindings("ProviderError")
    defined = sorted(m for m, how in bindings.items() if how == "class")
    assert defined == ["scriptorium.providers.errors"], (
        f"`ProviderError` is defined in {defined}. Two classes of one name are two "
        f"exceptions, and an `except` naming one does not catch the other.")
    catching = {"scriptorium.cli", "scriptorium.audit", "scriptorium.web.server"}
    assert catching <= set(bindings), (
        f"{sorted(catching - set(bindings))} no longer bind `ProviderError` at module "
        f"scope — by import, definition or assignment — and each of them catches it "
        f"by name. Found: {sorted(bindings)}.")
    from scriptorium.providers.errors import ProviderError
    assert issubclass(ProviderError, RuntimeError)
    for module in sorted(bindings):
        assert importlib.import_module(module).ProviderError is ProviderError, module


def test_a_provider_error_inside_a_command_is_one_line_and_exit_2(
        tmp_path, monkeypatch, capsys):
    """What binding the name buys, with the class raised by construction.

    The command is replaced with one that raises the providers' own class, so no
    other refusal can stand in for it: `ConfigError` is in the same tuple and a
    message that happened to read the same would pass a test that only matched
    text.
    """
    from scriptorium import cli
    from scriptorium.providers.base import ProviderError

    def refuse(cfg, provider=None):
        raise ProviderError("backend said no")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "do_models", refuse)
    with pytest.raises(SystemExit) as e:
        cli.main(["models"])
    assert e.value.code == 2
    assert capsys.readouterr().err == "lx: backend said no\n"


@pytest.mark.parametrize("config, argv, start", [
    (None, ["models", "--provider", "nope"], "lx: unknown provider 'nope'"),
    ({"providers": {"local": {"kind": "openai", "base_url": "file:///x", "model": "m"}}},
     ["models", "--provider", "local"],
     "lx: local: base_url must be an http:// or https:// address"),
], ids=["raised-by-build", "raised-by-request"])
def test_a_real_provider_failure_reaches_main_as_one_line_and_exit_2(
        tmp_path, monkeypatch, capsys, config, argv, start):
    """The same, through the two places the providers raise from.

    `providers.build` refuses a name nobody configured, and `Provider._request`
    refuses a scheme it will not send — the second inside the function that
    imports the transport, before it imports any of it, which the sweep above
    holds. Neither needs a network.
    """
    from scriptorium import cli

    monkeypatch.chdir(tmp_path)
    if config is not None:
        (tmp_path / "lx.config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    err = capsys.readouterr().err
    assert e.value.code == 2, err
    assert err.startswith(start), err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1, err
