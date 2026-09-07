"""The core/studio boundary, enforced where it already is rather than moved.

`docs/decisions.md`, 2026-07-28 item A3, decided one repository and two internal
packages: `core/` the engine and CLI — the artifact another repository vendors
into `tools/` — and `studio/` the workstation. HANDOFF-201 is the file move.

**Measured 2026-09-07, that move buys exactly one thing today, and it is not the
thing A3 named.** A3's requirement is that a consumer vendoring the engine does
not pay for the workbench. It already does not:

* `pyproject.toml`'s `dependencies` is `[]`, so there is nothing to partition;
* importing `scriptorium.cli` loads no `web` module at all, because the single
  edge into the workbench is deferred inside `cmd_web`;
* `src/scriptorium/web/` is 173 KB against ~740 KB for the rest, and the
  documented way to vendor is a **source-tree copy** (`README.md`,
  `pip install -e ./tools/scriptorium`), where an unimported subpackage costs a
  consumer nothing at all.

What is left is that the boundary is **observed rather than enforced**, and one
convenient import erodes it. That is what this file buys, at fifty lines instead
of a rewrite of every packaging file, CI job, test-layout constant and vendoring
instruction in the repository. HANDOFF-201 went back to `90-later/` with a
trigger; see :func:`test_the_split_trigger_has_not_fired` for the mechanical
half of it.

**This file is not a substitute for the split and does not claim to be.** It
cannot give `core/` and `studio/` separate distributions, which is the whole
point the day the studio needs a dependency the engine does not. It buys the
boundary in the meantime, and it makes the day that changes *loud*.
"""

import ast
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = os.path.join(ROOT, "src")
PACKAGE = os.path.join(SRC, "scriptorium")

#: The one permitted edge from the engine into the workbench.
#:
#: **Named by function rather than by line**, and that is a measurement rather
#: than a preference: in the single session that wrote this file, that import
#: moved from `cli.py:5128` to `cli.py:5438` because unrelated code was added
#: above it. A guard pinned to a line number reports on the day it is written
#: and never again — the same failure `skeleton.render_blocks`' one-walk guard
#: paid for when it matched a literal string and lost to a rename.
ALLOWED_EDGE = ("cli.py", "cmd_web")


def _modules():
    """Every module under `src/scriptorium/` that is not part of the workbench."""
    for dirpath, _dirs, names in os.walk(PACKAGE):
        if "__pycache__" in dirpath:
            continue
        rel_dir = os.path.relpath(dirpath, PACKAGE).replace("\\", "/")
        if rel_dir == "web" or rel_dir.startswith("web/"):
            continue
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _enclosing_functions(tree):
    """`{id(node): function name}` for every node inside a function body."""
    out = {}

    def walk(node, current):
        for child in ast.iter_child_nodes(node):
            name = (child.name if isinstance(child, (ast.FunctionDef,
                                                     ast.AsyncFunctionDef))
                    else current)
            out[id(child)] = name
            walk(child, name)

    walk(tree, None)
    return out


def _edges_into_the_workbench():
    """`[(module, function, lineno, detail)]` for every engine → studio import.

    **`ast.walk`, not `tree.body`.** The only edge that exists is
    function-local, so a module-level-only reader answers "none" and is wrong;
    so is a grep over `^from` / `^import`, which never sees an indented line.
    """
    found = []
    for path in _modules():
        with open(path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        where = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
                names = [a.name for a in node.names]
                hit = "web" in target.split(".")
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
                target = ""
                hit = any("web" in n.split(".") for n in names)
            else:
                continue
            if hit:
                found.append((os.path.basename(path), where.get(id(node)),
                              node.lineno, f"{target} {names}"))
    return found


def test_the_extractor_actually_walked_the_engine():
    """A floor, so a clean answer cannot come from having read nothing.

    Without this the boundary test passes over a renamed directory, over a glob
    that stopped matching, and over an empty tree — the failure the contract
    suite spells twice, at `len(served) >= 10` and at
    `assert imported, "the extractor is wrong"`, both of whose messages say to
    teach the extractor the new shape rather than to lower the floor.
    """
    walked = list(_modules())
    assert len(walked) >= 18, (
        f"the extractor found {len(walked)} engine modules under {PACKAGE}, "
        f"which cannot be right — the package has been restructured and this "
        f"file no longer reads it. Teach it the new shape rather than lowering "
        f"this floor.")


def test_the_engine_imports_the_workbench_in_exactly_one_named_place():
    """`core/` does not import `studio/`, with one exception and it is named.

    Invariant 4 applies: "does this module import that one" is mechanically
    decidable, so it belongs in a test rather than in a reviewer reading a
    diagram. A boundary nothing enforces is a boundary that erodes on the first
    convenient import.

    **The exception is `cmd_web` and it is one function, not a pattern.** `lx
    web` has to reach the server somehow, and until the packaging is actually
    split the honest spelling of that is a deferred import in the command
    handler. Widening this allowance — a second entry, a module-level
    allowlist, a prefix — is the erosion this test exists to catch, so it is
    written as an equality against a one-element set.
    """
    edges = _edges_into_the_workbench()
    actual = {(module, function) for module, function, _line, _detail in edges}
    assert actual == {ALLOWED_EDGE}, (
        f"engine → workbench imports are {sorted(actual)} and the only permitted "
        f"one is {ALLOWED_EDGE}. Full detail: {edges}. If a new one is "
        f"deliberate it is not a line in this test — it is HANDOFF-201, the "
        f"core/studio split, whose trigger is in this file.")


def test_the_one_exception_is_deferred_so_the_engine_never_loads_the_workbench():
    """What makes the exception harmless, asserted rather than assumed.

    The edge above is permitted **because it is deferred**: a consumer that
    vendors the engine and never runs `lx web` does not import the server, does
    not pay for it, and does not have an HTTP server on the import path. Move
    that import to module scope and the allowance above still passes while the
    property it was granted for is gone.

    In a subprocess with a clean interpreter, because `sys.modules` in this one
    is whatever the rest of the suite has already imported — an in-process check
    would pass or fail depending on test ordering, which is the shape of a guard
    that reports on nothing.
    """
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONDONTWRITEBYTECODE="1")
    probe = ("import sys, scriptorium.cli;"
             "print(sorted(m for m in sys.modules if m.startswith('scriptorium')))")
    out = subprocess.run([sys.executable, "-B", "-c", probe], env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    loaded = out.stdout.strip()
    assert "scriptorium.web" not in loaded, (
        f"importing `scriptorium.cli` loaded a workbench module: {loaded}. The "
        f"import in `cmd_web` is permitted only because it is deferred; at "
        f"module scope every consumer of the engine pays for the server.")


def test_the_split_trigger_has_not_fired():
    """HANDOFF-201's trigger, and the reason it is a test rather than a sentence.

    **The trigger is: a runtime dependency appears.** `pyproject.toml`'s
    `dependencies` is `[]` today, on purpose and with a comment saying so, and
    that emptiness is exactly why the core/studio split buys nothing at the
    packaging layer right now — there is nothing to keep apart. The day a
    dependency is declared, somebody has to decide whether it is the engine's or
    the workbench's, and if it is the workbench's then a consumer vendoring the
    engine starts paying for the workstation. That is the moment A3's argument
    stops being about the future.

    So this fails the moment the list is non-empty. It is deliberately **not**
    clever about it: mapping a distribution name to an import name is guesswork
    (`pyyaml` imports as `yaml`), and a guard that guesses is a guard that will
    one day be confidently wrong. Adding a runtime dependency is already a rare,
    deliberate act that invariant 1 requires a `docs/decisions.md` entry for —
    so this asks for one sentence more in that entry, and nothing else.

    A prose trigger was the alternative and it lost on this package's own
    history: HANDOFF-201 carried "do not split until the contract is stable" as
    a sentence while `blocked-by` was empty, and the pickup rule — which cannot
    read prose — ranked it first and pointed a session straight at the move that
    sentence forbade. A constraint the machine cannot read is not a constraint.

    **What is deliberately not a trigger:** size. `src/scriptorium/web/` is
    173 KB against ~740 KB for the rest, and HANDOFF-204 will grow the committed
    frontend bundle further — but the documented way to vendor is a source-tree
    copy, where bytes nobody imports cost nothing. A threshold on size would
    sound decidable while tracking nothing that hurts anyone.

    **The second trigger is editorial and cannot be a test**, so it lives in the
    package and in the decision entry: a consumer appears that installs the
    engine *from an index* rather than copying the tree. `README.md` documents
    `pip install -e ./tools/scriptorium` — a copy. The day anyone needs
    `pip install` of a published core, two distributions stop being optional.
    """
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
    assert match, "pyproject.toml declares no `dependencies` key at all"
    declared = match.group(1).strip()
    assert declared == "", (
        f"a runtime dependency appeared: {declared}\n\n"
        f"This is HANDOFF-201's trigger. Decide which side of the core/studio "
        f"boundary it belongs to and record it in docs/decisions.md:\n"
        f"  * the engine needs it       -> say so, and relax this test to name it;\n"
        f"  * the workbench needs it    -> the split now buys something real. "
        f"Promote HANDOFF-201 out of handoff/90-later/ and run it, because a "
        f"consumer vendoring the engine is otherwise installing a dependency "
        f"only the workstation uses — which is the thing invariant 1 and A3 "
        f"both exist to prevent.")


@pytest.mark.parametrize("module", ["audit", "renderings", "sentences", "suggest"])
def test_the_reviewer_facing_modules_stay_on_the_engine_side(module):
    """The three the boundary decision found genuinely ambiguous, pinned as core.

    `sentences.py` exists so a reading view can point at a sentence — a studio
    motive — and belongs to the engine because the rule has to be one rule that
    `lx`, an agent and CI can all see. `audit.py` and `renderings.py` are
    reviewer-facing report commands with no endpoint at all. `suggest.py` is the
    same shape.

    Pinned because a boundary drawn from *motive* puts every one of them on the
    wrong side, and that is the mistake this test is cheap insurance against —
    not because anything is currently trying to move them.
    """
    path = os.path.join(PACKAGE, f"{module}.py")
    assert os.path.exists(path), f"{module}.py is not where the boundary expects it"
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "web" not in (node.module or "").split("."), (
                f"{module}.py imports the workbench, so it is no longer an "
                f"engine module — decide the boundary rather than the import.")
