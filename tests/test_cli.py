"""What the CLI itself emits, measured on bytes.

`test_docio.py` owns the documents the pipeline writes — invariant 2a, at the
file boundary. This file owns the two things that invariant deliberately does
*not* claim, which is exactly why nothing was watching them: the diagnostics the
commands print, and the files `lx init` scaffolds into a user's project.
"""

import io
import json
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.cli import force_utf8  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")
SAMPLE = ROOT / "examples" / "sample.md"

# A link to mask and CJK to encode: the two things `check` echoes that a narrow
# code page cannot represent. Held as bytes and written as bytes — `write_text`
# only learned the `newline` keyword in 3.10, and 3.9 is the declared floor.
CJK_DOC = "# 標題\n\nSee [the guide](https://example.com/x) for 中文 details.\n".encode()


def _lx(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _ascii_stdout_env():
    # PYTHONIOENCODING is what the interpreter reads at start-up to choose
    # stdout's codec, so this reproduces on any platform the condition a Windows
    # console code page creates the moment the command is redirected.
    return {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONPATH": SRC}


def test_force_utf8_replaces_a_narrower_codec():
    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    force_utf8(stream)
    assert stream.encoding == "utf-8"
    stream.write("⟦n⟧")          # raises UnicodeEncodeError on ascii


def test_force_utf8_leaves_a_stand_in_without_reconfigure_alone():
    # io.StringIO has no `reconfigure`, and neither do several embedding shims.
    # Doing nothing is the right answer: a stand-in has already chosen its codec.
    stream = io.StringIO()
    force_utf8(stream)
    stream.write("ok")


def test_todo_survives_a_stdout_that_cannot_encode_it(tmp_path):
    """`lx todo doc.md --lang zh-TW > todo.json` on Windows, reproduced anywhere.

    `todo` prints a literal ⟦n⟧ in its rules line, so before `main` reconfigured
    the stream this exited non-zero with UnicodeEncodeError and wrote a truncated
    file.
    """
    shutil.copyfile(SAMPLE, tmp_path / "sample.md")
    env = _ascii_stdout_env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "sample.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    r = _lx(["todo", "sample.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "⟦n⟧" in json.loads(r.stdout.decode("utf-8"))["rules"]


def test_check_survives_a_stdout_that_cannot_encode_it(tmp_path):
    """The other half of the same defect: validator output echoes the source.

    `check` exits 1 on an untranslated document by design, so the exit code
    cannot distinguish "reported errors" from "died encoding them" — the
    assertion is on what reached stdout.
    """
    (tmp_path / "zh.md").write_bytes(CJK_DOC)
    env = _ascii_stdout_env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "zh.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    r = _lx(["check", "zh.md", "--lang", "zh-TW"], tmp_path, env)
    assert b"UnicodeEncodeError" not in r.stderr
    assert "中文" in r.stdout.decode("utf-8")


# --- what `lx init` scaffolds, and what the pipeline writes back -------------


def test_scaffolding_carries_no_carriage_return(tmp_path):
    """`lx init` writes into someone else's repository.

    No invariant claims these bytes — 2a excludes the files this project writes
    for itself — so this is asserting a choice rather than a guarantee: one
    command must not produce two different trees depending on the machine that
    ran it. Asserted on bytes, because a text-mode read normalizes and would
    report success either way.
    """
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    for name in ("lx.config.json", "config/glossary.csv", "config/dnt.txt"):
        blob = (tmp_path / name).read_bytes()
        assert blob, f"{name} was not created"
        assert b"\r" not in blob, f"{name} carries a CR"


def test_state_and_rendered_output_keep_the_source_terminator(tmp_path):
    # The end of criterion (5): a source with no CRLF must not gain one on the
    # way out. The round-trip itself is covered in test_docio.py at the function
    # level; this runs it through the real commands and a real filesystem.
    (tmp_path / "zh.md").write_bytes(CJK_DOC)
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "zh.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    state = next((tmp_path / ".lx" / "docs").iterdir())
    assert b"\r" not in state.read_bytes(), f"{state.name} carries a CR"

    r = _lx(["render", "zh.md", "--lang", "zh-TW", "--fallback", "-o", "out.md"],
            tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert (tmp_path / "out.md").read_bytes() == CJK_DOC
