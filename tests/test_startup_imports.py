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

#: One command per route into the providers package, each with a marker its own
#: output must contain — the proof that the command really ran, since a probe
#: that never reached `main` would otherwise report nothing loaded and pass.
#:
#: `--help` is `cli`'s module scope and everything it imports, `audit` and
#: `providers.errors` among them. `config get` and `status --json` read and
#: print, and the second is the frozen contract the bookshelf runs. `providers` reaches
#: `providers.available`, and `config set ….kind` reaches `providers.KINDS`,
#: both through function-local imports. `extract` and `todo` import
#: `translate`, which imports `providers.build` at its own module scope — the
#: route every model-free command that shares code with a run takes.
COMMANDS = [
    (["--help"], b"usage:"),
    (["config", "get"], b'"providers"'),
    (["status", "--json"], b'"contract_version"'),
    (["providers"], b"routing:"),
    (["config", "set", "providers.local.kind", "openai"], b"providers.local.kind:"),
    (["extract", "doc.md", "--lang", "zh-TW"], b"segments 1"),
    (["todo", "doc.md", "--lang", "zh-TW"], b'"source": "doc.md"'),
]

_PROBE = """
import json, sys
import scriptorium.cli
code = None
try:
    scriptorium.cli.main(json.loads(sys.argv[1]))
    code = 0
except SystemExit as e:
    code = e.code
{after}
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump({{"code": code,
               "loaded": [m for m in {forbidden!r} if m in sys.modules]}}, f)
"""


def _env():
    return dict(os.environ, PYTHONPATH=SRC, PYTHONDONTWRITEBYTECODE="1")


@pytest.fixture(scope="module")
def scaffold(tmp_path_factory):
    """A project from `lx init` holding one extracted paragraph, built once.

    Built by separate processes, so nothing it imports reaches a probe.
    """
    root = tmp_path_factory.mktemp("startup") / "proj"
    root.mkdir()
    (root / "doc.md").write_bytes(b"The quick brown fox jumps over the lazy dog.\n")
    for argv in (["init"], ["extract", "doc.md", "--lang", "zh-TW"]):
        out = subprocess.run([sys.executable, "-B", "-m", "scriptorium", *argv],
                             env=_env(), cwd=str(root), capture_output=True, timeout=60)
        assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    return root


@pytest.fixture
def project(scaffold, tmp_path):
    """A private copy, because two of the commands write to the project."""
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


def test_the_probe_sees_the_transport_when_it_is_loaded(project):
    """The positive control, so the test above cannot pass by seeing nothing.

    Asserted on the three modules `urllib.request` imports unconditionally.
    `ssl` is imported inside a `try` by both it and `http.client`, so on an
    interpreter built without OpenSSL — the locked-down machine invariant 1
    names — the control would fail for a reason that is not a defect.
    """
    report, stdout = _probe(["--help"], project, after="import urllib.request")
    assert report["code"] == 0 and b"usage:" in stdout, report
    missing = {"urllib.request", "http.client", "socket"} - set(report["loaded"])
    assert not missing, (
        f"after `import urllib.request` the probe reports {report['loaded']}, so it "
        f"cannot see {sorted(missing)} load, and the test above proves nothing.")


def _module_level_bindings(name):
    """``{module: "class" | "import"}`` for each module that binds `name` at module scope."""
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
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == name:
                    found[module] = "class"
                elif isinstance(node, ast.ImportFrom) and any(
                        (alias.asname or alias.name) == name for alias in node.names):
                    found.setdefault(module, "import")
    return found


def test_provider_error_is_one_class_bound_at_module_scope_wherever_it_is_caught():
    """`cli.main`, `audit` and the workbench server catch `ProviderError` by name.

    Each `except` tuple is evaluated only once something has been raised, so the
    name has to be bound by then on every path — module scope is the placement
    that cannot get it wrong — and it has to be the class the providers raise.
    Moved into `main` after the first statement that can raise, the tuple names
    an unbound local and every refusal it lists becomes a traceback; a second
    class of the same name catches nothing the providers raise.

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
        f"scope, and each of them catches it by name. Found: {sorted(bindings)}.")
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
    refuses a scheme it will not send — the second is inside the function that
    imports the transport, before any of it is imported. Neither needs a network.
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
