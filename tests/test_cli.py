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


def test_check_exits_zero_on_correct_traditional_chinese(tmp_path):
    """The exit code is the evidence invariant 10 rests on, so it is asserted end
    to end rather than at `check_segment`.

    分析這批數據 is ordinary Taiwanese usage — 數據 for measured readings — and
    until the 2026-07-28 lexicon audit it failed the build at error severity.
    """
    (tmp_path / "d.md").write_bytes(b"Analyse this batch of readings.\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    todo = _lx(["todo", "d.md", "--lang", "zh-TW"], tmp_path, env)
    seg_id = json.loads(todo.stdout.decode("utf-8"))["segments"][0]["id"]
    (tmp_path / "t.json").write_bytes(
        json.dumps({seg_id: "分析這批數據"}, ensure_ascii=False).encode("utf-8"))
    apply = _lx(["apply", "d.md", "--lang", "zh-TW", "--file", "t.json"], tmp_path, env)
    assert apply.returncode == 0, apply.stderr.decode("utf-8", "replace")

    r = _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


# One document carrying all five shapes the 2026-07-27 measurement damaged: a
# heading, two paragraphs, a table cell and a blockquote.
FIVE_SHAPES = (
    b"# Title\n\nSome sentence here.\n\nAnother sentence here.\n\n"
    b"| one | two |\n| --- | --- |\n\n> quoted line\n"
)

# Keyed by masked source text rather than by segment id, so the fixture does not
# encode the numbering `mdparse` happens to produce.
DAMAGING = {
    "one": "含|管線",                                   # a third column
    "Some sentence here.": "1. 這是譯文",                  # the paragraph becomes a list
    "Another sentence here.": "譯文\n# 憑空長出的標題",      # a heading from nowhere
    "Title": "上半\n\n下半",                             # the heading splits in two
    "quoted line": "第一行\n逸出引言的第二行",              # the second half leaves the quote
    "two": "二",
}
CLEAN = {
    "one": "一", "two": "二", "Title": "標題",
    "Some sentence here.": "這裡有一句話。",
    "Another sentence here.": "這裡還有一句話。",
    "quoted line": "引用的一行",
}


def _apply_by_source_text(tmp_path, env, name, mapping):
    todo = _lx(["todo", name, "--lang", "zh-TW", "--all"], tmp_path, env)
    ids = {s["text"]: s["id"] for s in json.loads(todo.stdout.decode("utf-8"))["segments"]}
    assert set(ids) == set(mapping), f"document parsed into {sorted(ids)}"
    payload = {ids[text]: target for text, target in mapping.items()}
    (tmp_path / "t.json").write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    r = _lx(["apply", name, "--lang", "zh-TW", "--file", "t.json"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


def test_check_exit_code_answers_for_structural_damage(tmp_path):
    """Invariant 10's evidence, on the half of it that used to be missing.

    Before the containment validators, all five of these rendered broken
    Markdown under a green exit code — which is what made `lx check` necessary
    and not sufficient, and what the caveat under invariant 10 said out loud.
    The same document with ordinary translations must still exit 0, because a
    validator that cannot be passed is not evidence either.
    """
    (tmp_path / "d.md").write_bytes(FIVE_SHAPES)
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    _apply_by_source_text(tmp_path, env, "d.md", DAMAGING)
    r = _lx(["check", "d.md", "--lang", "zh-TW", "--json"], tmp_path, env)
    assert r.returncode == 1
    report = json.loads(r.stdout.decode("utf-8"))
    assert report["by_rule"] == {"containment": 5}, report["by_rule"]
    assert report["errors"] == 5

    _apply_by_source_text(tmp_path, env, "d.md", CLEAN)
    r = _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


# --- the state file's own shape ----------------------------------------------


def test_state_from_before_typed_slots_is_refused_with_the_command_that_fixes_it(tmp_path):
    """A `.lx/` written by an older build must not be read as if it were current.

    Reading both slot shapes was the alternative and it loses: the document would
    load, and every pair in it would silently read as standalone — the defect the
    records exist to remove, in a file that looks current. So the door refuses it,
    and the message has to carry the way out, because a traceback is not an
    actionable message. The second half of the test is the message's own claim:
    re-extracting keeps the translations, so it must be shown keeping one.
    """
    (tmp_path / "d.md").write_bytes(b"The <b>bold</b> server is fast.\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    todo = _lx(["todo", "d.md", "--lang", "zh-TW"], tmp_path, env)
    seg_id = json.loads(todo.stdout.decode("utf-8"))["segments"][0]["id"]
    target = "這台 ⟦1⟧粗體⟦2⟧ 伺服器很快。"
    (tmp_path / "t.json").write_bytes(
        json.dumps({seg_id: target}, ensure_ascii=False).encode("utf-8"))
    assert _lx(["apply", "d.md", "--lang", "zh-TW", "--file", "t.json"],
               tmp_path, env).returncode == 0

    # Downgrade the state file to the shape this build replaced: no version key,
    # slots as plain strings.
    state = next((tmp_path / ".lx" / "docs").iterdir())
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc.pop("state_version", None)
    for seg in doc["segments"]:
        seg["slots"] = {k: v["original"] for k, v in seg["slots"].items()}
    state.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    r = _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 2, r.stdout.decode("utf-8", "replace")
    message = r.stderr.decode("utf-8")
    assert "Traceback" not in message
    assert "lx extract d.md --lang zh-TW" in message

    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    doc = json.loads(state.read_text(encoding="utf-8"))
    assert doc["state_version"] == 3
    assert doc["segments"][0]["target"] == target
    assert doc["segments"][0]["slots"]["1"]["role"] == "open"
    # The carryover crossed a file with no `context` at all, which is every state
    # file written before version 3. It works because `prior_targets` reads `kind`
    # when the key is absent — the migration rule, asserted where it is used.
    assert doc["segments"][0]["context"] == doc["segments"][0]["kind"]
    assert _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0


def test_state_from_a_newer_build_is_not_silently_overwritten(tmp_path):
    """The other direction, which is not the mirror image of the first.

    An older file is rebuilt by extract, so only readers that would misread it
    refuse. A newer one holds fields this build cannot represent, and extract
    *writes* — so the read that lets extract migrate an old file was also, at
    first, what let it downgrade a new one and exit 0, while `lx check` on the
    same file refused to touch it. Two scriptorium versions on one machine is
    all it takes: an installed `lx` beside a source checkout.
    """
    (tmp_path / "d.md").write_bytes(b"A sentence to extract.\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    state = next((tmp_path / ".lx" / "docs").iterdir())
    doc = json.loads(state.read_text(encoding="utf-8"))
    doc["state_version"] = 99
    doc["segments"][0]["field_from_the_future"] = "must not be lost"
    state.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    for cmd in (["check"], ["extract"], ["render", "--fallback", "-o", "out.md"]):
        r = _lx([cmd[0], "d.md", "--lang", "zh-TW", *cmd[1:]], tmp_path, env)
        assert r.returncode == 2, f"{cmd[0]} did not refuse: {r.stdout.decode('utf-8', 'replace')}"
        assert "--reset" in r.stderr.decode("utf-8")

    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["state_version"] == 99
    assert "field_from_the_future" in after["segments"][0]

    # --reset is the escape hatch the message names, and it must actually work.
    assert _lx(["extract", "d.md", "--lang", "zh-TW", "--reset"], tmp_path, env).returncode == 0
    after = json.loads(state.read_text(encoding="utf-8"))
    assert after["state_version"] == 3
    assert "field_from_the_future" not in after["segments"][0]


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
