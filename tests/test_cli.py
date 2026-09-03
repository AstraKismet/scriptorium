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

import statedb  # noqa: E402
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

    # Downgrade the state to the shape this build replaced: version 1, slots as
    # plain strings. The version is a per-document column now rather than a key
    # in a JSON file — `PRAGMA user_version` is the *schema*, which this test is
    # not about, and which no re-extract could fix if it were.
    statedb.set_state_version(tmp_path, 1)
    statedb.edit_segments(
        tmp_path, lambda body: {**body, "slots": {k: v["original"]
                                                  for k, v in body["slots"].items()}})

    r = _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 2, r.stdout.decode("utf-8", "replace")
    message = r.stderr.decode("utf-8")
    assert "Traceback" not in message
    assert "lx extract d.md --lang zh-TW" in message

    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    doc = statedb.documents(tmp_path)[0]
    segs = statedb.segments(tmp_path)
    assert doc["state_version"] == 3
    assert segs[0]["target"] == target
    assert segs[0]["slots"]["1"]["role"] == "open"
    # The carryover crossed the bump because `prior_targets` reads the identity
    # off its own columns, which no content bump touches. That is what replaced
    # the `kind`-for-a-missing-`context` migration rule the JSON reader needed:
    # a column is present or it is NULL, and a format whose context is
    # legitimately null keeps it.
    assert segs[0]["context"] == segs[0]["kind"]
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

    statedb.set_state_version(tmp_path, 99)
    statedb.edit_segments(tmp_path,
                          lambda body: {**body, "field_from_the_future": "must not be lost"})

    for cmd in (["check"], ["extract"], ["render", "--fallback", "-o", "out.md"]):
        r = _lx([cmd[0], "d.md", "--lang", "zh-TW", *cmd[1:]], tmp_path, env)
        assert r.returncode == 2, f"{cmd[0]} did not refuse: {r.stdout.decode('utf-8', 'replace')}"
        assert "--reset" in r.stderr.decode("utf-8")
        # And `--tone`, since 2026-08-19: a `--reset` that names no register is
        # refused, so a message naming the bare command would send the reader to
        # something that exits 2. The register is inside the row this build just
        # refused to read, so nothing but the person can supply it.
        assert "--tone" in r.stderr.decode("utf-8"), \
            "the escape hatch the message names must be the one that works"

    assert statedb.documents(tmp_path)[0]["state_version"] == 99
    assert "field_from_the_future" in statedb.segments(tmp_path)[0]

    # --reset is the escape hatch the message names, and it must actually work.
    # It still can, which is the reason the content version stayed a per-document
    # column instead of collapsing into `PRAGMA user_version`: a database-wide
    # refusal would make this sentence false for everyone with two documents.
    bare = _lx(["extract", "d.md", "--lang", "zh-TW", "--reset"], tmp_path, env)
    assert bare.returncode == 2, "a reset that names no register is refused"
    assert "Traceback" not in bare.stderr.decode("utf-8"), "one sentence, not a stack trace"
    assert "--tone" in bare.stderr.decode("utf-8")
    assert _lx(["extract", "d.md", "--lang", "zh-TW", "--reset", "--tone", "technical"],
               tmp_path, env).returncode == 0
    assert statedb.documents(tmp_path)[0]["state_version"] == 3
    assert "field_from_the_future" not in statedb.segments(tmp_path)[0]


def test_a_reset_that_names_no_register_is_refused_before_the_file_is_looked_for(tmp_path):
    """Exit 2, one sentence, and *which* sentence.

    Its own test rather than three lines inside the state-version scenario above,
    where the property was reachable only after planting a row from the future —
    restructure that test and "the CLI exits 2 rather than printing a traceback"
    goes silent. Both halves are pinned here: the exit code and the traceback,
    which is what `cli.main`'s handled tuple decides, and the ordering, which is
    what the guard's placement decides. `missing.md` does not exist, so a guard
    below `read_document` reports the file instead — still exit 2, still no
    traceback, and about the wrong thing.
    """
    env = {**os.environ, "PYTHONPATH": SRC}
    r = _lx(["extract", "missing.md", "--lang", "zh-TW", "--reset"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "Traceback" not in err, "a handled refusal, not an escaped exception"
    assert "--tone" in err
    assert "No such file" not in err, \
        "the absent --tone is the defect in the command as typed, and is named first"


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

    # The terminator is a document-level fact and lives in one place. Asserted on
    # the skeleton rather than on the state file's bytes, which is what this said
    # while the state was JSON: a CR anywhere in a node is the defect — the model
    # and the reviewer would both have to reproduce it — and the state file no
    # longer has bytes of its own to inspect.
    assert statedb.documents(tmp_path)[0]["eol"] == "\n"
    assert not any("\r" in n.get("v", "") for n in statedb.nodes(tmp_path)), "a node carries a CR"

    r = _lx(["render", "zh.md", "--lang", "zh-TW", "--fallback", "-o", "out.md"],
            tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert (tmp_path / "out.md").read_bytes() == CJK_DOC


def test_extract_names_a_non_default_register_and_stays_quiet_about_the_default(tmp_path):
    """The register decides the brief and which half of the memory answers, so a
    document that is in one says so on the line reporting what carried over.

    Only when it is not the default, for the same reason `rejected` is only
    printed when it happened: a line every document prints is a line nobody
    reads. The second half also pins the stickiness — the third command names no
    register and must not silently return the document to the configured one.
    """
    (tmp_path / "d.md").write_bytes(b"He left without a word.\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0

    r = _lx(["extract", "d.md", "--lang", "zh-TW", "--tone", "literary"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "tone literary" in r.stdout.decode("utf-8")

    r = _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env)
    assert "tone literary" in r.stdout.decode("utf-8")

    r = _lx(["extract", "d.md", "--lang", "zh-TW", "--tone", "technical"], tmp_path, env)
    assert "tone" not in r.stdout.decode("utf-8")


def test_run_says_what_extract_says_about_the_wording_it_stopped_holding(tmp_path):
    """`lx run` begins with `do_extract` and carries the same `--tone`.

    The four lines the carryover prints lived inside `cmd_extract` for one
    afternoon, and in that state `lx run d.md --tone literary` emptied a reviewed
    book and printed `0 reused · 2 to translate` — a line indistinguishable from
    a first run on a document nobody had translated. A register change is the one
    of the four with no downstream check behind it: kept wording turns `lx check`
    red, and a register change just returns the document to `pending`, where
    everything looks normal.

    Found by the adversarial pass over the change that added the reporting, on
    the axis that change had not varied: which commands do the reporting.
    """
    (tmp_path / "d.md").write_bytes(b"He left without a word.\n\nShe did not answer.\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    ids = [s["id"] for s in statedb.segments(tmp_path)]
    (tmp_path / "t.json").write_bytes(json.dumps(
        {ids[0]: "他一言不發地走了。", ids[1]: "她沒有回答。"}, ensure_ascii=False).encode())
    assert _lx(["apply", "d.md", "--lang", "zh-TW", "--file", "t.json",
                "--origin", "human"], tmp_path, env).returncode == 0

    # `--dry-run` so nothing reaches a provider; the register move happens in
    # `do_extract`, before any of that. `lx run` exits 1 because the document it
    # just emptied fails `lx check`, which is itself the point.
    r = _lx(["run", "d.md", "--lang", "zh-TW", "--tone", "literary", "--dry-run"],
            tmp_path, env)
    said = r.stdout.decode("utf-8")
    assert "the register moved from technical to literary" in said, said
    assert "the 2 this document held are not in it any more" in said


def test_hold_and_unhold_report_what_they_did_and_refuse_an_empty_segment(tmp_path):
    """`lx hold` / `lx unhold` end to end, including the exit code of a refusal.

    Two commands rather than `lx hold --lift`, because a verb command is named
    for what it does and `lx hold --lift` reads as the opposite of what it would
    do. One handler behind both, so the pair cannot drift.
    """
    (tmp_path / "d.md").write_bytes(b"First sentence.\n\nSecond sentence.\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    ids = [s["id"] for s in statedb.segments(tmp_path)]

    # Nothing to hold yet: refused with a sentence and exit 2, not a traceback.
    r = _lx(["hold", "d.md", "--lang", "zh-TW", "--ids", ids[0]], tmp_path, env)
    assert r.returncode == 2, r.stdout.decode("utf-8", "replace")
    message = r.stderr.decode("utf-8")
    assert "lx translate" in message, "the refusal must name the way forward"
    assert "Traceback" not in message
    assert all(s.get("review") is None for s in statedb.segments(tmp_path))

    payload = json.dumps({ids[0]: "第一句。"}, ensure_ascii=False)
    (tmp_path / "t.json").write_bytes(payload.encode("utf-8"))
    assert _lx(["apply", "d.md", "--lang", "zh-TW", "--file", "t.json",
                "--origin", "human"], tmp_path, env).returncode == 0

    r = _lx(["hold", "d.md", "--lang", "zh-TW", "--ids", f"{ids[0]},nope"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8")
    assert "held 1 segment(s)" in out
    assert "nope" in out, "an unknown id is reported, not refused"
    assert statedb.segments(tmp_path)[0]["review"] == "held"

    # A held segment is a warning and never an error, so `lx check` still passes
    # on a document whose every segment has a target.
    (tmp_path / "t.json").write_bytes(
        json.dumps({ids[1]: "第二句。"}, ensure_ascii=False).encode("utf-8"))
    assert _lx(["apply", "d.md", "--lang", "zh-TW", "--file", "t.json"],
               tmp_path, env).returncode == 0
    r = _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")
    assert "1 warning(s)" in r.stdout.decode("utf-8")

    r = _lx(["unhold", "d.md", "--lang", "zh-TW", "--ids", ids[0]], tmp_path, env)
    assert r.returncode == 0
    assert "released 1 segment(s)" in r.stdout.decode("utf-8")
    assert "review" not in statedb.segments(tmp_path)[0]
    assert _lx(["check", "d.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0


# --- `lx extract --from`, and the two things the memory route loses ----------

#: A book whose two chapters share one byte-identical paragraph, translated
#: **differently** in each. That is the whole population: `store.tm_key` excludes
#: position and `doc_id`, so both wordings bank under one key and `store.load_tm`
#: keeps the last. A novel reaches it through repeated dialogue and chapter
#: formulae; the fixture reaches it in six segments.
_SPLIT_DOC = (b"# Chapter One\n\nAlpha sentence.\n\nA repeated line.\n\n"
              b"# Chapter Two\n\nBeta sentence.\n\nA repeated line.\n")
_SPLIT_CH1 = b"# Chapter One\n\nAlpha sentence.\n\nA repeated line.\n"
_SPLIT_CH2 = b"# Chapter Two\n\nBeta sentence.\n\nA repeated line.\n"
_SPLIT_TARGETS = {"s0001": "第一章", "s0002": "阿爾法句。", "s0003": "重複的一行甲",
                  "s0004": "第二章", "s0005": "貝塔句。", "s0006": "重複的一行乙"}


def _split_project(tmp_path, hold=None):
    """A translated two-chapter book, split on disk, ready to re-extract."""
    env = {**os.environ, "PYTHONPATH": SRC}
    (tmp_path / "novel.md").write_bytes(_SPLIT_DOC)
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "novel.md", "--lang", "zh-TW", "--tone", "literary"],
               tmp_path, env).returncode == 0
    (tmp_path / "t.json").write_bytes(
        json.dumps(_SPLIT_TARGETS, ensure_ascii=False).encode("utf-8"))
    assert _lx(["apply", "novel.md", "--lang", "zh-TW", "--file", "t.json"],
               tmp_path, env).returncode == 0
    if hold:
        assert _lx(["hold", "novel.md", "--lang", "zh-TW", "--ids", hold],
                   tmp_path, env).returncode == 0
    (tmp_path / "ch1.md").write_bytes(_SPLIT_CH1)
    (tmp_path / "ch2.md").write_bytes(_SPLIT_CH2)
    return env


def _rendered(tmp_path, env, name):
    r = _lx(["render", name, "--lang", "zh-TW", "-o", "-"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8")
    return r.stdout.decode("utf-8")


def test_extract_from_carries_a_split_across_with_no_commit_at_all(tmp_path):
    """The property the whole re-aiming of HANDOFF-041 rests on.

    `lx commit` is not run. Both halves come back fully translated out of the
    other document's state, which is what makes a split lossless rather than
    merely cheap.
    """
    env = _split_project(tmp_path)
    (tmp_path / "novel.md").unlink()
    for half in ("ch1.md", "ch2.md"):
        r = _lx(["extract", half, "--lang", "zh-TW", "--from", "novel.md"], tmp_path, env)
        out = r.stdout.decode("utf-8")
        assert r.returncode == 0, r.stderr.decode("utf-8")
        assert "segments 3 | reused 3 | pending 0" in out, out
        # The line that reframes the counts above it. `lx run` prints the same
        # block, so a reader is never left reading another document's numbers as
        # this one's.
        assert "carried from novel.md" in out, out
        assert _lx(["check", half, "--lang", "zh-TW"], tmp_path, env).returncode == 0


def test_extract_from_keeps_two_identical_paragraphs_apart_where_the_memory_cannot(tmp_path):
    """The measured reason `--from` exists rather than "run `lx commit` first".

    `store.load_tm` is `tm[record_key(rec)] = rec` — last write wins, over a key
    that carries no position and no `doc_id`. Two byte-identical sources with
    different targets bank two lines and read back as one, so the memory route
    gives *both* halves the same wording and one translation is gone. The
    carryover is a diff over a position sequence and keeps them apart.
    """
    env = _split_project(tmp_path)
    (tmp_path / "novel.md").unlink()
    for half in ("ch1.md", "ch2.md"):
        assert _lx(["extract", half, "--lang", "zh-TW", "--from", "novel.md"],
                   tmp_path, env).returncode == 0
    assert "重複的一行甲" in _rendered(tmp_path, env, "ch1.md")
    assert "重複的一行乙" in _rendered(tmp_path, env, "ch2.md")


def test_the_memory_route_collapses_them_and_drops_a_held_wording(tmp_path):
    """What `--from` is measured *against*. This test pins the defect, not a fix.

    It is here because HANDOFF-041 arrived asserting that a split is free once
    `lx commit` has run, and that is false in two independent ways. If either
    ever stops being true this test goes red and the decision entry that cites it
    has to be re-read — which is the point of pinning a loss.
    """
    env = _split_project(tmp_path, hold="s0002")
    r = _lx(["commit", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8")
    # Banked five of six: `lx commit` refuses a held segment, deliberately.
    assert "+= 5 entries" in r.stdout.decode("utf-8")
    (tmp_path / "novel.md").unlink()
    for half in ("ch1.md", "ch2.md"):
        assert _lx(["extract", half, "--lang", "zh-TW", "--tone", "literary"],
                   tmp_path, env).returncode == 0
    # One wording answered for both positions: the first is gone from the project.
    assert "重複的一行乙" in _rendered(tmp_path, env, "ch1.md")
    assert "重複的一行甲" not in _rendered(tmp_path, env, "ch1.md")
    # And the held segment was never banked, so it comes back untranslated and
    # `lx check` fails — a reviewer's in-progress wording, lost by this route.
    assert _lx(["check", "ch1.md", "--lang", "zh-TW"], tmp_path, env).returncode == 1


def test_extract_from_carries_a_hold_across(tmp_path):
    """A hold is about the wording, so it travels with it. The memory cannot."""
    env = _split_project(tmp_path, hold="s0002")
    (tmp_path / "novel.md").unlink()
    assert _lx(["extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md"],
               tmp_path, env).returncode == 0
    r = _lx(["check", "ch1.md", "--lang", "zh-TW"], tmp_path, env)
    out = r.stdout.decode("utf-8")
    assert r.returncode == 0, out          # a hold is a warning, never an error
    assert "held" in out, out


def test_extract_from_leaves_the_document_it_read_untouched(tmp_path):
    """It is a copy, not a move. Nothing about `--from` is destructive."""
    env = _split_project(tmp_path)
    before = {s["id"]: s["target"] for s in statedb.segments(tmp_path)}
    assert _lx(["extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md"],
               tmp_path, env).returncode == 0
    kept = {s["id"]: s["target"] for s in statedb.segments(tmp_path)}
    for seg_id, target in before.items():
        assert kept[seg_id] == target


def test_extract_from_never_writes_to_the_translation_memory(tmp_path):
    """Invariant 9: the memory is a source of truth and this path is not a writer."""
    env = _split_project(tmp_path)
    assert _lx(["commit", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    tm = tmp_path / ".lx" / "tm.zh-TW.jsonl"
    before = tm.read_bytes()
    assert _lx(["extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md"],
               tmp_path, env).returncode == 0
    assert tm.read_bytes() == before, "byte-identical, not merely equivalent"


def test_extract_from_refuses_a_document_with_no_state(tmp_path):
    env = {**os.environ, "PYTHONPATH": SRC}
    (tmp_path / "a.md").write_bytes(b"# T\n\nOne.\n")
    assert _lx(["init"], tmp_path, env).returncode == 0
    r = _lx(["extract", "a.md", "--lang", "zh-TW", "--from", "gone.md"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "Traceback" not in err, "in `main`'s tuple, or it exits 1 with a stack trace"
    assert "no state in zh-TW" in err
    # Nothing was written, and the state database was never even created: the
    # refusal is above `store._connect`. Asserted on the file rather than by
    # reading the tables, because `sqlite3.connect` would create the file to
    # look — the probe is what would falsify the property.
    assert not (tmp_path / ".lx" / "state.db").exists()


def test_extract_from_refuses_the_document_being_extracted(tmp_path):
    env = _split_project(tmp_path)
    r = _lx(["extract", "novel.md", "--lang", "zh-TW", "--from", "novel.md"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "names the document being extracted" in err


def test_extract_from_refuses_a_register_that_would_carry_nothing(tmp_path):
    """The silent-failure case: mismatched registers miss every key and say `reused 0`."""
    env = _split_project(tmp_path)
    r = _lx(["extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md",
             "--tone", "technical"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "register" in err and "literary" in err


def test_extract_from_takes_the_register_of_the_document_it_reads(tmp_path):
    """No `--tone`, and a config that says otherwise: the carryover still lands.

    Without this the flag fails on the ordinary case — a `literary` novel in a
    project whose config still says `technical` — and reports `reused 0`, which
    reads exactly like a first extract.
    """
    env = _split_project(tmp_path)
    r = _lx(["extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md"], tmp_path, env)
    out = r.stdout.decode("utf-8")
    assert r.returncode == 0, r.stderr.decode("utf-8")
    assert "reused 3" in out and "tone literary" in out, out


def test_extract_from_and_reset_are_refused_together(tmp_path):
    env = _split_project(tmp_path)
    r = _lx(["extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md",
             "--reset", "--tone", "literary"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "opposite things" in err


# --- the message a missing source gets --------------------------------------


def test_a_missing_source_names_what_still_works_and_the_flag_that_moves_it(tmp_path):
    """The first door a person hits after moving a file.

    It used to be a bare `[Errno 2] No such file or directory: 'novel.md'`, which
    reads as lost work about a document whose translations are intact — `lx
    render` on it still writes the correct file, measured 2026-09-04.
    """
    env = _split_project(tmp_path)
    (tmp_path / "novel.md").unlink()
    r = _lx(["extract", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "Traceback" not in err
    assert "lx render novel.md --lang zh-TW" in err, "names what still works"
    assert "--from novel.md" in err, "names the flag that carries them to the new file"


def test_a_missing_source_with_no_state_gets_the_plain_message(tmp_path):
    """A typo is not a moved book, and must not be told it has translations."""
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    r = _lx(["extract", "typo.md", "--lang", "zh-TW"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "lx untracked" in err
    assert "--from" not in err, "there is nothing to carry across"


def test_a_missing_source_is_reported_before_its_extension_is_judged(tmp_path):
    """Placement, not wording. `formats.for_path` runs before `read_document`.

    A guard sitting above `read_document` is below the format lookup, so a
    missing file whose extension this project does not know was answered with
    "has no format this project knows how to read" — about a file that is not
    there.
    """
    env = {**os.environ, "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    r = _lx(["extract", "gone.xyz", "--lang", "zh-TW"], tmp_path, env)
    err = r.stderr.decode("utf-8")
    assert r.returncode == 2, err
    assert "is not there" in err
    assert "no format" not in err, "the absent file is named first"
