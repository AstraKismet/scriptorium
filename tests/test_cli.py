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
# code page cannot represent.
CJK_DOC = "# 標題\n\nSee [the guide](https://example.com/x) for 中文 details.\n"


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
    (tmp_path / "zh.md").write_text(CJK_DOC, encoding="utf-8", newline="\n")
    env = _ascii_stdout_env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "zh.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    r = _lx(["check", "zh.md", "--lang", "zh-TW"], tmp_path, env)
    assert b"UnicodeEncodeError" not in r.stderr
    assert "中文" in r.stdout.decode("utf-8")
