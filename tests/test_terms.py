"""Terminology extraction, and the line `lx terms` does not cross.

The command proposes a `source` and leaves `target` empty. That is not a
formatting detail — how `Ashcombe` renders in Traditional Chinese is judgement,
and invariant 4 keeps judgement out of the deterministic half, so a command that
invented a target would have moved it into `checks.py`'s input one step upstream.
The emptiness is therefore a property, and it is asserted twice: in what the
command emits, and in what an unfilled row does to the rest of the pipeline.

The other half is the heuristic. English capitalizes every sentence's first word,
so a capitalized token is evidence of nothing until it appears somewhere else —
suppressing that is the substance of the work, and the shapes tested here are the
ones a novel actually has: dialogue, attribution after a closing quote, and a
name that only ever follows an honorific.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import statedb  # noqa: E402
from scriptorium.cli import candidate_terms  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG, load_glossary  # noqa: E402
from scriptorium.translate import _user_message  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")

TERMS = DEFAULT_CONFIG["terms"]
GLOSSARY = "config/glossary.csv"

#: `Winter` and `Ashcombe` are seen the same number of times and differ in one
#: respect only: every `Winter` opens a sentence. Anything the command does to
#: tell them apart has to be the sentence-initial rule, because nothing else
#: separates them.
NOVEL = (
    b"# Chapter One\n"
    b"\n"
    b"Winter came late to Ashcombe Hall that year, and the lamps burned until dawn.\n"
    b"\n"
    b"Winter had never troubled Ashcombe before. Snow was ordinary, and the house\n"
    b"was old.\n"
    b"\n"
    b"\"Run,\" Ashcombe said, and Mr. Darcy did not move.\n"
    b"\n"
    b"Winter ended. Mr. Darcy had come from Ashcombe Hall on foot.\n"
)


def _lx(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _env():
    return {**os.environ, "PYTHONPATH": SRC}


def _project(tmp_path, document=NOVEL, name="novel.md"):
    (tmp_path / name).write_bytes(document)
    env = _env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    r = _lx(["extract", name, "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return env


def _reconfigure(tmp_path, **fields):
    """Overwrite keys in the project's lx.config.json, on bytes.

    `Path.write_text` only learned `newline=` in 3.10 and the declared floor is
    3.9, so every write in this file goes through bytes — the same reason
    `test_cli.py` holds its fixtures as byte strings.
    """
    path = tmp_path / "lx.config.json"
    config = json.loads(path.read_bytes().decode("utf-8"))
    config.update(fields)
    path.write_bytes(json.dumps(config, ensure_ascii=False).encode("utf-8"))


def _rows(stdout):
    """The CSV rows of a default `lx terms`, with its `#` summary lines dropped."""
    return [line for line in stdout.decode("utf-8").splitlines()
            if line and not line.startswith("#")]


def _segments(tmp_path, name="novel.md"):
    return statedb.segments(tmp_path)


def _seg(masked, sid="s0001"):
    return {"id": sid, "masked": masked}


def _candidates(masked, min_count=2):
    return {r["source"]: r for r in candidate_terms(
        [_seg(t, f"s{i:04d}") for i, t in enumerate(masked, 1)],
        min_count, TERMS["abbreviations"], TERMS["stopwords"])}


# --- the heuristic ------------------------------------------------------------


def test_terms_sentence_initial_evidence_is_what_promotes_a_candidate(tmp_path):
    """Acceptance criterion 1, through the real command.

    `Winter` opens three sentences and appears nowhere else; `Ashcombe` appears
    mid-sentence. They are seen a comparable number of times in one document, so
    a frequency threshold cannot be what separates them.
    """
    env = _project(tmp_path)
    r = _lx(["terms", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")

    proposed = {line.split(",")[0] for line in _rows(r.stdout)}
    assert "Ashcombe" in proposed
    assert "Ashcombe Hall" in proposed
    assert "Winter" not in proposed
    assert "Snow" not in proposed
    # The heading. Its first token opens the only sentence it has, so the run
    # carries no evidence at all — a title is not discovered this way.
    assert "Chapter One" not in proposed


def test_terms_sentence_initial_rules_on_the_shapes_a_novel_actually_has():
    """The three cases the rule is built around, each isolated.

    Asserted on `candidate_terms` rather than through the CLI because each is one
    decision about one gap, and a subprocess would only obscure which of them
    fired.
    """
    # An opening quote starts a sentence: the comma before `"The` is not a
    # terminator, and without the quote rule `The` would look mid-sentence.
    assert "The" not in _candidates([
        'He said, "The door is locked."',
        'She said, "The window, then."',
    ])

    # A *closing* quote does not, and this is the commonest line in a novel.
    # Reading the `"` in `," Ashcombe` as an opening would suppress the one name
    # in the sentence.
    assert "Ashcombe" in _candidates([
        '"Run," Ashcombe said.',
        '"Wait," Ashcombe answered.',
    ])

    # An honorific's full stop does not end a sentence. Without this, a character
    # who is only ever named after one has no mid-sentence occurrence anywhere.
    assert "Darcy" in _candidates([
        "Mr. Darcy did not move.",
        "Mr. Darcy had come on foot.",
    ])

    # A run is joined by exactly one space, so a name broken across a wrapped
    # line becomes two candidates rather than one row that could never fire —
    # the glossary matches on the literal source string.
    wrapped = _candidates([
        "She crossed the Ashcombe\nHall courtyard.",
        "He left the Ashcombe\nHall gate open.",
    ])
    assert "Ashcombe Hall" not in wrapped
    assert "Ashcombe" in wrapped and "Hall" in wrapped

    # And the floor is still a floor: `--min-count` is a threshold on how often a
    # candidate was seen, not a second sentence-position rule.
    assert "Ashcombe" not in _candidates(['"Run," Ashcombe said.'], min_count=2)


def test_terms_sentence_initial_after_a_closing_quote_that_kept_its_mark():
    """`"Run!" Ashcombe said.` — the mark stays inside the quote and the
    attribution continues the sentence, which is how English punctuates it. A
    character attributed only that way has no other mid-sentence occurrence, so
    reading the `!` as a terminator loses the name entirely.

    A full stop is the deliberate non-case: English writes a *comma* when an
    attribution follows, so `"Run." Ashcombe left.` really is two sentences and
    must keep suppressing.
    """
    assert "Ashcombe" in _candidates([
        '"Run!" Ashcombe said.',
        '"Where?" Ashcombe asked.',
    ])
    assert "Ashcombe" not in _candidates([
        '"Run." Ashcombe left.',
        '"Wait." Ashcombe stayed.',
    ])


def test_terms_sentence_initial_position_is_per_token_not_per_run():
    """`Then Ashcombe spoke.` is one run, and the run opens the sentence.

    Without recording the tail, the maximal-run rule swallows the one occurrence
    of `Ashcombe` that was genuinely mid-sentence — and a name after `Then`,
    `But` or `And` is not a rare shape in a novel.
    """
    found = _candidates(["Then Ashcombe spoke.", "But Ashcombe stayed."])
    assert "Ashcombe" in found
    assert found["Ashcombe"]["mid_sentence"] == 2
    # The run itself was still counted, and still carries no evidence of its own.
    assert "Then Ashcombe" not in found


def test_terms_counts_a_possessive_as_the_name_it_belongs_to():
    """Two occurrences of one name, not one each of two — which is exactly the
    split that drops a minor character below the threshold."""
    found = _candidates(["They took Ashcombe's carriage.", "She saw Ashcombe there."])
    assert list(found) == ["Ashcombe"]
    assert found["Ashcombe"]["count"] == 2
    # The internal apostrophe still binds: `O'Brien` is one token, not `O` + `Brien`.
    assert "O'Brien" in _candidates(["She met O'Brien there.", "He greeted O'Brien."])


def test_terms_keeps_a_name_that_is_not_spelled_in_ascii():
    """An English novel is full of names that are not ASCII, and the failure was
    a *wrong* answer rather than a missing one: `René` was proposed as `Ren`."""
    assert "René" in _candidates(["She met René there.", "He greeted René warmly."])
    assert "Müller" in _candidates(["The Müller house stood empty.",
                                    "She passed the Müller gate."])


def test_terms_drops_an_honorific_and_a_single_letter():
    """Neither is a proper noun, and each is harmful in its own way.

    `Mr` occurs mid-sentence in every attribution — `said Mr. Darcy` — so left in
    it outranks the names it exists to help find. A single letter is worse than
    useless: a glossary row `J` fires on every segment holding a bare J.
    """
    found = _candidates(["and Mr. Darcy did not move.", "said Mr. Darcy again."])
    assert list(found) == ["Darcy"]
    assert "J" not in _candidates(["We saw J at the door.", "He met J again."])


def test_terms_ranks_by_frequency_and_breaks_ties_the_same_way_everywhere(tmp_path):
    """`--append` writes a file, so two machines must agree on the order."""
    env = _project(tmp_path)
    r = _lx(["terms", "novel.md", "--lang", "zh-TW", "--json"], tmp_path, env)
    report = json.loads(r.stdout.decode("utf-8"))
    ranked = [(t["count"], t["source"]) for t in report["terms"]]
    assert ranked == sorted(ranked, key=lambda t: (-t[0], t[1]))
    assert all(t["mid_sentence"] >= 1 for t in report["terms"])


# --- the line it does not cross -----------------------------------------------


def test_terms_target_is_empty_and_an_unfilled_row_enforces_nothing(tmp_path):
    """Acceptance criterion 2, and the consequence that makes it safe.

    Every proposed row has an empty target column. That is only tolerable
    because an empty target is inert in both directions — `check_segment` skips
    the rule, and `_glossary_hints` must not tell the model to render the name
    as nothing. The second half was a real defect: the hint block read
    `Ashcombe -> ` before this command existed to write such a row.
    """
    env = _project(tmp_path)
    r = _lx(["terms", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    rows = _rows(r.stdout)
    assert rows
    for line in rows:
        assert re.fullmatch(r"[^,]+,,,error", line), line

    # And in JSON, as an explicit empty string rather than an absent key: a
    # consumer should be able to assert the emptiness, which is the contract.
    j = _lx(["terms", "novel.md", "--lang", "zh-TW", "--json"], tmp_path, env)
    terms = json.loads(j.stdout.decode("utf-8"))["terms"]
    assert terms and all(t["target"] == "" for t in terms)

    assert _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"],
               tmp_path, env).returncode == 0
    glossary = load_glossary({"glossary": str(tmp_path / GLOSSARY)})
    assert glossary and all(g["target"] == "" for g in glossary)

    # Nothing reaches the model, and nothing turns the build red.
    seg = next(s for s in _segments(tmp_path) if "Ashcombe" in s["masked"])
    assert "Required terminology" not in _user_message([seg], glossary, "draft")
    todo = _lx(["todo", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    assert all("glossary" not in item
               for item in json.loads(todo.stdout.decode("utf-8"))["segments"])


def test_terms_refuses_non_english_source_rather_than_answering(tmp_path):
    """Acceptance criterion 5. The rule *is* English capitalization, so on a
    language that has none the command would report success and propose nothing —
    a wrong answer that looks like a right one."""
    env = _project(tmp_path)
    _reconfigure(tmp_path, source_lang="ja")

    r = _lx(["terms", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 2
    message = r.stderr.decode("utf-8")
    assert "Traceback" not in message
    assert "source_lang" in message
    assert not r.stdout.strip()

    # A regional English tag is still English: the refusal reads the primary
    # subtag, or `en-GB` would be turned away for spelling itself out.
    _reconfigure(tmp_path, source_lang="en-GB")
    assert _lx(["terms", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0


# --- writing into a hand-maintained file ---------------------------------------


HAND_WRITTEN = (
    "source,target,forbidden,severity\n"
    "# the order here is a person's, not this command's\n"
    "Snow,雪,,warn\n"
    "ashcombe,灰岸,,error\n"
).encode()


def test_terms_append_preserves_every_existing_row_byte_for_byte(tmp_path):
    """Acceptance criterion 3.

    The glossary is hand-maintained: its order, its comments and its severities
    are decisions this command has no business touching. So the assertion is
    byte-level and one-directional — the file before must be a prefix of the file
    after — which no amount of "rewrite it carefully" satisfies by accident.

    `ashcombe` is lower-case on purpose. `check_segment` and `_glossary_hints`
    both match a row case-insensitively, so a row that already covers `Ashcombe`
    must not be proposed again in a different casing.
    """
    env = _project(tmp_path)
    (tmp_path / GLOSSARY).write_bytes(HAND_WRITTEN)

    r = _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    after = (tmp_path / GLOSSARY).read_bytes()
    assert after.startswith(HAND_WRITTEN)

    added = after[len(HAND_WRITTEN):].decode("utf-8").splitlines()
    assert added
    assert all(line.endswith(",,,error") for line in added)
    assert not any(line.lower().startswith("ashcombe,") for line in added)
    assert not any(line.startswith("Snow,") for line in added)

    # Idempotent, which is what "only rows whose source is not already present"
    # means once the first run has happened.
    assert _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"],
               tmp_path, env).returncode == 0
    assert (tmp_path / GLOSSARY).read_bytes() == after


def test_terms_append_preserves_a_final_row_that_has_no_line_terminator(tmp_path):
    """The one way a pure append can still destroy a row.

    A hand-edited file whose last line was saved without a terminator would have
    the first appended row glued onto the end of it, turning two rows into one
    nonsense row — and the damage lands on the row a person wrote.
    """
    env = _project(tmp_path)
    (tmp_path / GLOSSARY).write_bytes(b"source,target,forbidden,severity\nSnow,\xe9\x9b\xaa,,warn")

    assert _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"],
               tmp_path, env).returncode == 0
    rows = load_glossary({"glossary": str(tmp_path / GLOSSARY)})
    snow = [g for g in rows if g["source"] == "Snow"]
    assert snow == [{"source": "Snow", "target": "雪", "forbidden": [], "severity": "warn"}]


def test_terms_append_keeps_the_line_terminator_the_file_already_had(tmp_path):
    """A glossary is hand-maintained, and an editor on Windows saves CRLF.

    Appending LF rows to it leaves one file with both — in the one place this
    command's contract is to leave alone. No invariant claims these bytes (2a
    excludes what the project writes for itself), so this asserts a choice.
    """
    env = _project(tmp_path)
    (tmp_path / GLOSSARY).write_bytes(b"source,target,forbidden,severity\r\nSnow,\xe9\x9b\xaa,,warn\r\n")

    assert _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"],
               tmp_path, env).returncode == 0
    after = (tmp_path / GLOSSARY).read_bytes()
    assert b"\n" not in after.replace(b"\r\n", b"")
    assert [g["source"] for g in load_glossary({"glossary": str(tmp_path / GLOSSARY)})]


def test_terms_append_refuses_in_one_sentence_when_the_glossary_cannot_be_written(tmp_path):
    """A CSV open in a spreadsheet, or saved read-only, is not an exotic state for
    a file a person maintains. Unhandled, the write ended the command in a
    traceback and exit 1; every other refusal here is one sentence and exit 2.

    The cause used here is a `glossary` path whose parent is a *file*, because it
    is the one cause that behaves identically on all four runners. Read-only is
    the cause that actually happens, and it only reaches this guard on Windows —
    POSIX replaces a read-only file happily, since it asks the directory's
    permissions rather than the file's. That is why the guard covers the
    operation and not the cause, and why the test does too.
    """
    env = _project(tmp_path)
    _reconfigure(tmp_path, glossary="config/glossary.csv/zh-TW.csv")
    real = tmp_path / GLOSSARY
    before = real.read_bytes()

    r = _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"], tmp_path, env)
    assert r.returncode == 2, r.stderr.decode("utf-8", "replace")
    message = r.stderr.decode("utf-8")
    assert "Traceback" not in message
    assert "zh-TW.csv" in message
    assert "lx.config.json" in message
    # The claim the message makes about the file has to be true.
    assert real.read_bytes() == before
    assert not (tmp_path / (GLOSSARY + ".tmp")).exists()


def test_terms_append_creates_the_glossary_when_the_project_has_none(tmp_path):
    """`lx init` is not a precondition, and the file it would have written and
    the file this writes have to be the same file — one header, one meaning for
    column three."""
    (tmp_path / "novel.md").write_bytes(NOVEL)
    env = _env()
    assert _lx(["extract", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    assert not (tmp_path / GLOSSARY).exists()

    assert _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"],
               tmp_path, env).returncode == 0
    written = (tmp_path / GLOSSARY).read_bytes()
    assert b"\r" not in written
    assert written.startswith(b"source,target,forbidden,severity\n")
    assert [g["source"] for g in load_glossary({"glossary": str(tmp_path / GLOSSARY)})]


# --- the whole point ----------------------------------------------------------


def test_terms_feeds_glossary_enforcement_end_to_end(tmp_path):
    """Acceptance criterion 4, which is the only one that answers "so what".

    A proposed row is worth nothing until a person writes the rendering in. Once
    they have, the term must reach the request *before* the model translates, and
    a target that drifts forty chapters later must fail the build. Both halves,
    on a row this command actually wrote.
    """
    env = _project(tmp_path)
    assert _lx(["terms", "novel.md", "--lang", "zh-TW", "--append"],
               tmp_path, env).returncode == 0

    # The hand edit, on bytes: `Path.write_text` has no `newline=` on 3.9, and a
    # text-mode write would put CRLF into the fixture on Windows.
    path = tmp_path / GLOSSARY
    text = path.read_bytes().decode("utf-8")
    assert "Ashcombe,,,error\n" in text
    path.write_bytes(
        text.replace("Ashcombe,,,error\n",
                     "Ashcombe,灰岸,阿什科姆;艾什康,error\n").encode("utf-8"))

    glossary = load_glossary({"glossary": str(path)})
    seg = next(s for s in _segments(tmp_path) if "Ashcombe" in s["masked"])
    request = _user_message([seg], glossary, "draft")
    assert "Required terminology for this batch:" in request
    assert "- Ashcombe -> 灰岸" in request

    todo = _lx(["todo", "novel.md", "--lang", "zh-TW", "--all"], tmp_path, env)
    ids = {s["text"]: s["id"] for s in json.loads(todo.stdout.decode("utf-8"))["segments"]}
    drifted = "阿什科姆的燈火燃到天亮。"
    (tmp_path / "t.json").write_bytes(
        json.dumps({ids[seg["masked"]]: drifted}, ensure_ascii=False).encode("utf-8"))
    assert _lx(["apply", "novel.md", "--lang", "zh-TW", "--file", "t.json"],
               tmp_path, env).returncode == 0

    r = _lx(["check", "novel.md", "--lang", "zh-TW", "--json"], tmp_path, env)
    assert r.returncode == 1
    report = json.loads(r.stdout.decode("utf-8"))
    glossary_issues = [i for i in report["issues"]
                       if i["rule"] == "glossary" and i["seg"] == ids[seg["masked"]]]
    assert len(glossary_issues) == 2, glossary_issues
    assert all(i["severity"] == "error" for i in glossary_issues)


def test_terms_survives_a_stdout_that_cannot_encode_it(tmp_path):
    """`lx terms novel.md --lang zh-TW > terms.csv` is the invocation this
    command's default output exists for, and a proper noun is exactly where a
    typographic apostrophe turns up. Redirected on Windows, stdout falls back to
    the console code page; reproduced here through PYTHONIOENCODING."""
    document = ("O’Brien crossed the yard. The gate held.\n"
                "\n"
                "She found O’Brien at the gate, and O’Brien said nothing.\n").encode()
    (tmp_path / "novel.md").write_bytes(document)
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONPATH": SRC}
    assert _lx(["init"], tmp_path, env).returncode == 0
    assert _lx(["extract", "novel.md", "--lang", "zh-TW"], tmp_path, env).returncode == 0

    r = _lx(["terms", "novel.md", "--lang", "zh-TW"], tmp_path, env)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert b"UnicodeEncodeError" not in r.stderr
    assert "O’Brien,,,error" in r.stdout.decode("utf-8")
