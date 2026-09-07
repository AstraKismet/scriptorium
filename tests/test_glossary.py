"""Editing a file a person maintains by hand, and the bytes that survive it.

`lx terms --append` gets "never rewrite or reorder an existing row" for free,
because it only ever concatenates bytes onto the end. An editor cannot: it has
to touch bytes that are already there. So the guarantee is built here rather
than inherited, and it is asserted the only way it can be — on the bytes of a
file whose comments, blank lines, padding, mixed terminators and fifth
comma-field are all things somebody put there on purpose.

The other half is what an edit does **not** reach. `config/glossary.csv` is
configuration; `.lx/tm.*.jsonl` is a source of truth. The memory key carries no
knowledge of the glossary at all, so changing a row cannot repair a wording and
must not pretend to — what it does is arm `checks.py`'s glossary rule, and that
is asserted end to end rather than described.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.config import glossary_rows, load_glossary  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")
GLOSSARY = "config/glossary.csv"

#: Everything a hand-maintained glossary can be, in one file. Each line is a
#: separate thing an editor could destroy: the header, which `load_glossary`
#: recognizes on line 0 and nowhere else; a comment a person wrote to themselves;
#: a blank line; padding inside fields; a populated `forbidden` list; a row with
#: an empty target, which is what `lx terms --append` writes; a lone LF in a CRLF
#: file; a fifth comma-field the parser drops but nobody asked it to delete; and
#: a final row with no terminator at all.
HAND_MAINTAINED = (
    b"source,target,forbidden,severity\r\n"
    b"# the order here is a person's, not this command's\r\n"
    b"\r\n"
    b"  Snow  ,  \xe9\x9b\xaa  ,, warn \r\n"
    b"# Ashcombe is the house, not the family\r\n"
    b"Ashcombe,\xe7\x81\xb0\xe5\xb2\xb8,\xe9\x98\xbf\xe4\xbb\x80;\xe6\x98\x82\xe6\x96\xaf,error\r\n"
    b"Vance,,,error\r\n"
    b"Thornfield,\xe8\x8d\x8a\xe6\xa3\x98,,error,keep-this-note\n"
    b"Darcy,\xe9\x81\x94\xe8\xa5\xbf"
)

NOVEL = (
    b"# Chapter One\n"
    b"\n"
    b"Ashcombe had never troubled himself with the accounts.\n"
    b"\n"
    b"\"Run,\" Ashcombe said, and nobody moved.\n"
)


def _lx(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _env():
    return {**os.environ, "PYTHONPATH": SRC}


def _project(tmp_path, glossary=HAND_MAINTAINED, document=NOVEL):
    (tmp_path / "novel.md").write_bytes(document)
    env = _env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    if glossary is not None:
        (tmp_path / GLOSSARY).write_bytes(glossary)
    return env


def _bytes(tmp_path, name=GLOSSARY):
    return (tmp_path / name).read_bytes()


def _lines(raw):
    return raw.split(b"\n")


# --- the rows a write may not disturb -----------------------------------------


@pytest.mark.parametrize("args,touched", [
    (["set", "Ashcombe", "灰岸崖"], 5),
    (["set", "ashcombe", "灰岸崖"], 5),
    (["set", "Snow", "--severity", "error"], 3),
    (["set", "Vance", "凡斯"], 6),
    (["set", "Snow", ""], 3),
    (["set", "Thornfield", "棘野"], 7),
    (["set", "Darcy", "達西先生"], 8),
])
def test_glossary_set_changes_one_line_and_no_other_byte(tmp_path, args, touched):
    """The property the whole design exists for, asserted on physical lines.

    `touched` is the 0-based index of the one line allowed to differ. Everything
    else — the header, both comments, the blank line, the order, the padding in
    an untouched field, the lone LF, the missing final terminator — has to come
    back identical, and a canonical rewrite of the addressed line would lose
    several of them.
    """
    env = _project(tmp_path)
    before = _bytes(tmp_path)
    assert _lx(["glossary", *args], tmp_path, env).returncode == 0
    after = _bytes(tmp_path)
    old, new = _lines(before), _lines(after)
    assert len(old) == len(new)
    assert [i for i in range(len(old)) if old[i] != new[i]] == [touched]


def test_glossary_set_writes_only_the_field_it_was_given(tmp_path):
    """Padding, a `forbidden` list and a fifth field a person put there survive.

    The fifth field is the one the post-condition guard cannot see: it compares
    the rows `config.glossary_rows` projects, and the parse drops everything past
    the fourth comma. Splicing one field into the line's own text is what makes
    the guarantee hold; the guard is a backstop for a different failure.

    The whitespace *around* the new value is kept too, so a person's column
    alignment survives a rewrite of the value inside it — `load_glossary` strips
    on the way in, so the two spellings read back the same and only one of them
    leaves the file looking like the file.
    """
    env = _project(tmp_path)
    assert _lx(["glossary", "set", "Snow", "霜"], tmp_path, env).returncode == 0
    assert _lx(["glossary", "set", "Thornfield", "棘野"], tmp_path, env).returncode == 0
    text = _bytes(tmp_path).decode("utf-8")
    assert "  Snow  ,  霜  ,, warn " in text
    assert "Thornfield,棘野,,error,keep-this-note" in text
    assert "Ashcombe,灰岸,阿什;昂斯,error" in text


def test_glossary_set_keeps_the_terminator_the_line_already_had(tmp_path):
    """A file with mixed terminators has no single right answer, so each line keeps its own.

    `append_glossary_rows` detects one terminator for the whole file because it
    is choosing one for rows that do not exist yet. A line that already exists
    has already answered, and this fixture has a CRLF file with one LF line in it
    and a last line with no terminator at all.
    """
    env = _project(tmp_path)
    assert _lx(["glossary", "set", "Thornfield", "棘野"], tmp_path, env).returncode == 0
    assert _lx(["glossary", "set", "Darcy", "達西先生"], tmp_path, env).returncode == 0
    raw = _bytes(tmp_path)
    assert b"Thornfield,\xe6\xa3\x98\xe9\x87\x8e,,error,keep-this-note\n" in raw
    assert b"\r\nThornfield" in raw
    assert raw.endswith(b"Darcy,\xe9\x81\x94\xe8\xa5\xbf\xe5\x85\x88\xe7\x94\x9f")


def test_glossary_unset_removes_the_row_and_leaves_its_comment(tmp_path):
    """A row is deleted, not a stanza. The comment above it is a person's line."""
    env = _project(tmp_path)
    assert _lx(["glossary", "unset", "Ashcombe"], tmp_path, env).returncode == 0
    text = _bytes(tmp_path).decode("utf-8")
    assert "Ashcombe" not in text.replace("# Ashcombe is the house, not the family", "")
    assert "# Ashcombe is the house, not the family" in text
    assert [r["source"] for _, r in glossary_rows(text)] == [
        "Snow", "Vance", "Thornfield", "Darcy"]


# --- the writes that must not happen -----------------------------------------


@pytest.mark.parametrize("args", [
    ["set", "Ashcombe", "灰岸,舊譯"],
    ["set", "Ashcombe", "灰岸\n阿什科姆"],
    ["set", "Ashcombe", "灰岸 阿什科姆"],
    ["set", "Ashcombe", "   "],
    ["set", "#note", "x"],
    ["set", "Ashcombe"],
    ["set", "Ashcombe", "灰岸", "--forbidden", "a,b"],
    ["unset", "Nobody"],
    ["get", "Nobody"],
])
def test_glossary_refusals_leave_the_file_byte_identical(tmp_path, args):
    """Every refusal is one sentence, exit 2, and no byte moved.

    `U+2028` is in the list because it is invisible to Python's line iterator and
    a line break to `str.splitlines` — a rendering carrying one reads as two rows
    in half the tools that open the file.
    """
    env = _project(tmp_path)
    before = _bytes(tmp_path)
    result = _lx(["glossary", *args], tmp_path, env)
    assert result.returncode == 2
    assert b"Traceback" not in result.stderr
    assert _bytes(tmp_path) == before
    assert not (tmp_path / (GLOSSARY + ".tmp")).exists()


def test_glossary_refusal_says_what_is_wrong_rather_than_that_it_gave_up(tmp_path):
    """The post-condition guard would also stop a comma, and its message is useless.

    Found by the mutation round: removing the comma refusal outright left every
    assertion above green, because the guard catches the malformed line on the
    way out and exits 2 as well. What is lost is the whole point of a refusal —
    the guard says the edit "would not read back" and tells the reader to move
    the header, which is not what happened.
    """
    env = _project(tmp_path)
    result = _lx(["glossary", "set", "Ashcombe", "灰岸,舊譯"], tmp_path, env)
    assert result.returncode == 2
    message = result.stderr.decode("utf-8")
    assert "comma" in message and "header" not in message


def test_glossary_refuses_a_severity_before_it_opens_the_file(tmp_path):
    """`choices` rather than a free string, because every reader compares to `error`.

    A third spelling is silently non-fatal at all three comparison sites, so it
    can never be written — and argparse refuses it before a path is resolved.
    """
    env = _project(tmp_path)
    before = _bytes(tmp_path)
    result = _lx(["glossary", "set", "Snow", "--severity", "Error"], tmp_path, env)
    assert result.returncode == 2
    assert b"invalid choice" in result.stderr
    assert _bytes(tmp_path) == before


def test_glossary_refuses_a_term_two_rows_answer_for(tmp_path):
    """No reader takes the first row: both are looped over and both are enforced.

    So two rows for one term are one term with two answers, and choosing between
    them is judgement over a file whose state is already a defect. The refusal
    names the lines a person has to go and look at.
    """
    env = _project(tmp_path, glossary=(
        b"source,target,forbidden,severity\n"
        b"Snow,\xe9\x9b\xaa,,error\n"
        b"snow,\xe9\x9c\x9c,,error\n"))
    before = _bytes(tmp_path)
    for args in (["set", "Snow", "霰"], ["unset", "Snow"]):
        result = _lx(["glossary", *args], tmp_path, env)
        assert result.returncode == 2
        assert b"lines 2, 3" in result.stderr
        assert _bytes(tmp_path) == before


def test_glossary_unset_refuses_when_it_would_promote_a_row_into_the_header(tmp_path):
    """Removing a line renumbers every line after it, and line 0 is the header's.

    A file with no header whose second row's term is literally `source`: deleting
    the first row promotes the second into the position `config.glossary_row`
    tests for a header, and one command silently removes two rows. The
    post-condition guard is what catches it, by parsing the result before
    replacing the file.
    """
    env = _project(tmp_path, glossary=b"A,1,,error\nsource,x,,error\n")
    before = _bytes(tmp_path)
    result = _lx(["glossary", "unset", "A"], tmp_path, env)
    assert result.returncode == 2
    assert b"header" in result.stderr
    assert _bytes(tmp_path) == before


def test_glossary_set_writes_nothing_for_a_change_no_reader_could_see(tmp_path):
    """`load_glossary` strips, so a padded field already reads as the value asked for.

    Compared against what the loader projects rather than against the bytes: a
    tracked file must not get a diff, or an mtime, for a change nobody made.
    """
    env = _project(tmp_path)
    before = _bytes(tmp_path)
    stamp = os.stat(tmp_path / GLOSSARY).st_mtime_ns
    result = _lx(["glossary", "set", "Snow", "雪"], tmp_path, env)
    assert result.returncode == 0
    assert b"unchanged" in result.stdout
    assert _bytes(tmp_path) == before
    assert os.stat(tmp_path / GLOSSARY).st_mtime_ns == stamp


def test_glossary_set_refuses_in_one_sentence_when_the_file_cannot_be_written(tmp_path):
    """The `GlossaryWriteError` path, extended rather than replaced.

    `lx terms --append`'s own test uses this cause because it behaves the same on
    every runner: a glossary path whose parent is a file. The editor has to
    answer it the same way — one sentence, exit 2, no traceback, the file
    unchanged and no `.tmp` left behind.
    """
    env = _project(tmp_path)
    (tmp_path / "notadir").write_bytes(b"x")
    config = json.loads((tmp_path / "lx.config.json").read_bytes().decode("utf-8"))
    config["glossary"] = "notadir/glossary.csv"
    (tmp_path / "lx.config.json").write_bytes(
        json.dumps(config, ensure_ascii=False).encode("utf-8"))
    result = _lx(["glossary", "set", "Snow", "霜"], tmp_path, env)
    assert result.returncode == 2
    assert b"Traceback" not in result.stderr
    assert (tmp_path / "notadir").read_bytes() == b"x"
    assert not (tmp_path / "notadir").is_dir()


# --- reading ------------------------------------------------------------------


def test_glossary_get_output_is_still_a_glossary_fragment(tmp_path):
    """`lx glossary get > backup.csv` has to produce a file `load_glossary` reads.

    `cmd_terms`' own convention: the summary rides in `#` comments, which the
    loader skips. It is not a byte copy of the file — a padded field and a
    person's comment do not survive it — which is why the file itself stays the
    thing under version control.
    """
    env = _project(tmp_path)
    result = _lx(["glossary", "get"], tmp_path, env)
    assert result.returncode == 0
    (tmp_path / "copy.csv").write_bytes(result.stdout.replace(b"\r\n", b"\n"))
    assert ([(r["source"], r["target"], r["severity"])
             for r in load_glossary({"glossary": str(tmp_path / "copy.csv")})]
            == [(r["source"], r["target"], r["severity"])
                for r in load_glossary({"glossary": str(tmp_path / GLOSSARY)})])


def test_glossary_get_names_the_two_states_the_writers_refuse_on(tmp_path):
    """A reader meets the condition before they meet the refusal.

    Two notes and no others, because these two are exactly what `set` and `unset`
    refuse on. A header knocked off line 1 — by a byte-order mark, which Excel
    writes on every save, or by a comment above it — is read as an ordinary row,
    and nothing else in the project would ever say so.
    """
    env = _project(tmp_path, glossary=(
        b"\xef\xbb\xbfsource,target,forbidden,severity\n"
        b"Snow,\xe9\x9b\xaa,,error\n"
        b"snow,\xe9\x9c\x9c,,error\n"))
    result = _lx(["glossary", "get", "--json"], tmp_path, env)
    assert result.returncode == 0
    notes = json.loads(result.stdout.decode("utf-8"))["notes"]
    assert sorted(n["note"] for n in notes) == ["duplicate", "header"]


def test_glossary_get_json_carries_only_the_four_documented_fields(tmp_path):
    """Projected key by key, because `HANDOFF-037` attaches a compiled pattern to a row.

    That package's one stated way of breaking something is a reader that
    serializes a glossary row to JSON, and this is the only reader in the tree
    that emits a whole row. Asserted rather than commented, so the constraint
    survives whoever writes that repair.
    """
    env = _project(tmp_path)
    result = _lx(["glossary", "get", "--json"], tmp_path, env)
    assert result.returncode == 0
    rows = json.loads(result.stdout.decode("utf-8"))["rows"]
    assert rows
    for row in rows:
        assert set(row) == {"source", "target", "forbidden", "severity", "line"}


# --- what an edit does not reach ----------------------------------------------


def test_a_glossary_edit_leaves_the_translation_memory_byte_identical(tmp_path):
    """Acceptance criterion 5, and the reason it is worth asserting.

    The memory key is `(content_hash, context, segmentation_version, variant,
    tone)` and knows nothing about the glossary, so a banked wording still says
    what it said. What the edit does instead is arm `checks.py`'s glossary rule —
    which is the other half of the assertion, because "the memory is untouched"
    read alone would suggest the edit did nothing at all.
    """
    env = _project(tmp_path, glossary=b"source,target,forbidden,severity\n")
    assert _lx(["extract", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    (tmp_path / "t.json").write_bytes(json.dumps({
        "s0001": "第一章", "s0002": "灰岸從來不肯為帳目費心。",
        "s0003": "「快跑。」灰岸說，沒有人動。",
    }, ensure_ascii=False).encode("utf-8"))
    assert _lx(["apply", "novel.md", "--lang", "zh-TW", "--file", "t.json",
                "--origin", "human"], tmp_path, env).returncode == 0
    assert _lx(["commit", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    memory = tmp_path / ".lx" / "tm.zh-TW.jsonl"
    state = tmp_path / ".lx" / "state.db"
    before = (memory.read_bytes(), state.read_bytes())
    assert _lx(["glossary", "set", "Ashcombe", "灰岸崖"], tmp_path, env).returncode == 0
    assert (memory.read_bytes(), state.read_bytes()) == before

    checked = _lx(["check", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    assert checked.returncode == 1
    assert b"should render as" in checked.stdout


def test_a_zero_byte_glossary_gets_a_header_before_the_first_appended_row(tmp_path):
    """An empty file is a file with no header, and the header is line 0's alone.

    Reproduced on the parent build 2026-09-07: `lx terms --append` wrote its
    first row at raw line index 0, `load_glossary` read the file as empty because
    the term happened to be `Source`, `lx terms` therefore reported the same
    candidate as new on the next run, and the file quietly grew a duplicate of a
    row nothing could read. `os.path.exists` is true for an empty file, which is
    why the missing-file branch did not already cover it.
    """
    env = _project(tmp_path, glossary=b"", document=(
        b"# Notes\n"
        b"\n"
        b"The Source of the river is a spring. Nobody visits the Source now.\n"
        b"\n"
        b"Everyone agrees the Source is cold.\n"))
    assert _lx(["extract", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    first = _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"], tmp_path, env)
    assert first.returncode == 0
    rows = load_glossary({"glossary": str(tmp_path / GLOSSARY)})
    assert [r["source"] for r in rows] == ["Source"]
    second = _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"], tmp_path, env)
    assert second.returncode == 0
    assert b"0 new candidate(s)" in second.stdout
    assert [r["source"] for r in
            load_glossary({"glossary": str(tmp_path / GLOSSARY)})] == ["Source"]
