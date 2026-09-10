"""`lx forget`: one document row removed by name, and never the last copy of anything.

What a file that was split or renamed leaves behind is a row that still counts —
`lx stats` shows three bars for a book cut into two chapters, and `lx status
--json` reports twice the segments the book has. This file pins the command that
removes it, and above all the rule that stops it removing the only copy of a
translation: a row goes when every translation it holds is also held, the same
way, by some other row in the same language.

Everything runs through `cli.main` in-process, so the flag names, the argparse
wiring and the exit path are part of what is pinned. Every project is built two
levels inside `tmp_path` for the reason `test_contract.py` gives: one level down
is pytest's shared base, which it never cleans.

No path literal here carries a backslash, and no assertion depends on whether a
file that differs only in case exists: `os.path.exists` answers that differently
on the two CI platforms, and the command is built never to ask it.
"""

import ast
import json
import os
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium.cli as cli  # noqa: E402
import scriptorium.store as store  # noqa: E402
import statedb  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "scriptorium")

#: A four-paragraph novel. Two of its wordings open with the U+3000 pair zh-TW
#: prose is indented with, which `lx apply` keeps and `translate.accept` — every
#: carry — strips. That difference is what made a byte comparison refuse the
#: very split this command exists to finish.
NOVEL = ["The first paragraph.", "The second paragraph.",
         "The third paragraph.", "The fourth paragraph."]
WORDING = {"s0001": "　　第一段。", "s0002": "第二段。",
           "s0003": "　　第三段。", "s0004": "第四段。"}

#: The shape the memory route loses: one paragraph twice, translated two ways.
#: `store.load_tm` keeps one record per key, so after `lx commit` and a plain
#: extract both halves read back the same wording and the other is gone.
SPLIT = ["# Chapter One", "Alpha sentence.", "A repeated line.",
         "# Chapter Two", "Beta sentence.", "A repeated line."]
SPLIT_WORDING = {"s0001": "第一章", "s0002": "阿爾法句。", "s0003": "重複的一行甲",
                 "s0004": "第二章", "s0005": "貝塔句。", "s0006": "重複的一行乙"}


def _lx(capsys, *args):
    """``(exit code, stdout, stderr)`` for one invocation through `main`."""
    code = 0
    try:
        cli.main(list(args))
    except SystemExit as e:
        code = e.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _ok(capsys, *args):
    code, out, err = _lx(capsys, *args)
    assert code == 0, f"lx {' '.join(args)} exited {code}: {err}"
    return out


def _write(root, name, paragraphs):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")


def _apply(root, capsys, src, wording, origin="human", lang="zh-TW"):
    (root / "in.json").write_text(json.dumps(wording, ensure_ascii=False), encoding="utf-8")
    _ok(capsys, "apply", src, "--lang", lang, "--file", "in.json", "--origin", origin)


def _book(root, capsys, paragraphs=NOVEL, wording=WORDING, cut=2, origin="human"):
    """A translated `literary` novel, cut on disk into two chapters, the original gone."""
    _ok(capsys, "init")
    _write(root, "novel.md", paragraphs)
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(root, capsys, "novel.md", wording, origin)
    _write(root, "ch1.md", paragraphs[:cut])
    _write(root, "ch2.md", paragraphs[cut:])
    (root / "novel.md").unlink()


def _carry(capsys, *names, source="novel.md"):
    out = ""
    for name in names:
        out = _ok(capsys, "extract", name, "--lang", "zh-TW", "--from", source)
    return out


def _rows(root, did, lang="zh-TW"):
    conn = sqlite3.connect(str(root / ".lx" / "state.db"))
    try:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE doc_id=? AND lang=?",
                                    (did, lang)).fetchone()[0]
                for table in ("documents", "nodes", "segments")}
    finally:
        conn.close()


def _status(capsys):
    return json.loads(_ok(capsys, "status", "--json"))["projects"][0]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


# ── the harm, and removing it ───────────────────────────────────────────────

def test_a_carried_split_is_forgotten_and_the_book_stops_counting_twice(project, capsys):
    """The measured harm and its repair, end to end.

    Before: three rows for a four-segment book, `totals.segments` eight. After:
    two rows and four. The chapters were carried with `--from`, so every wording
    the old row holds is held by one of them and nothing needs a flag.
    """
    _book(project, capsys)
    _carry(capsys, "ch1.md", "ch2.md")
    assert _status(capsys)["totals"]["segments"] == 8
    out = _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert "forgot novel.md [zh-TW] — 4 segment(s), 4 translated" in out, out
    assert "every one of them also held by another tracked document" in out, out
    status = _status(capsys)
    assert [d["source"] for d in status["documents"]] == ["ch1.md", "ch2.md"]
    assert status["totals"]["segments"] == 4


def test_forget_empties_all_three_tables_for_that_pair_and_no_other(project, capsys):
    """Keyed on both halves of the key. A DELETE missing its `lang` takes the
    document out of every language; one missing a table leaves a child behind."""
    _book(project, capsys)
    _ok(capsys, "extract", "ch1.md", "--lang", "ja-JP")
    _carry(capsys, "ch1.md", "ch2.md")
    chapters = {name: _rows(project, name) for name in ("ch1.md", "ch2.md")}
    japanese = _rows(project, "ch1.md", "ja-JP")
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert _rows(project, "novel.md") == {"documents": 0, "nodes": 0, "segments": 0}
    assert {name: _rows(project, name) for name in chapters} == chapters
    assert _rows(project, "ch1.md", "ja-JP") == japanese


def test_the_memory_is_byte_identical_after_a_forget_and_after_a_discard(project, capsys):
    """Invariant 9: `.lx/tm.*.jsonl` is a source of truth and this is not a writer."""
    _book(project, capsys)
    _carry(capsys, "ch1.md")
    _ok(capsys, "commit", "ch1.md", "--lang", "zh-TW")
    memory = project / ".lx" / "tm.zh-TW.jsonl"
    before = memory.read_bytes()
    # ch2 is never carried, so two wordings exist only in the old row and the
    # flag is what lets this forget run at all.
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW", "--discard-wording")
    assert memory.read_bytes() == before
    # And the path with no flag, which deletes only what is held elsewhere.
    _write(project, "solo.md", ["Solo."])
    _ok(capsys, "extract", "solo.md", "--lang", "zh-TW")
    _ok(capsys, "forget", "solo.md", "--lang", "zh-TW")
    assert memory.read_bytes() == before


def test_the_rendered_output_and_the_source_file_are_left_as_they_were(project, capsys):
    _ok(capsys, "init")
    _write(project, "novel.md", NOVEL)
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW")
    _ok(capsys, "render", "novel.md", "--lang", "zh-TW")
    rendered = project / "i18n" / "zh-TW" / "novel.md"
    before = (rendered.read_bytes(), (project / "novel.md").read_bytes())
    out = _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert (rendered.read_bytes(), (project / "novel.md").read_bytes()) == before
    # Said, because it is the first thing a person would worry went as well.
    assert "i18n/zh-TW/novel.md from an earlier render" in out, out


def test_the_source_file_plays_no_part_in_the_decision(project, capsys):
    """The same forget with the old file still on disk. Nothing stats it."""
    _book(project, capsys)
    _write(project, "novel.md", NOVEL)
    _carry(capsys, "ch1.md", "ch2.md")
    out = _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert "every one of them also held" in out, out


# ── the syntax guard and the transaction ────────────────────────────────────

def _deletes(tree, table):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str) and n.value.startswith(f"DELETE FROM {table} ")]


def test_the_three_deletes_sit_in_one_with_block_behind_the_write_lock():
    """Acceptance criterion 4, by syntax.

    One transaction per table would leave segment rows with no parent after a
    crash between them, and `store.tracked` iterates `documents` only — so the
    visible leftover this command removes would become invisible garbage. Read
    with `ast`, never grepped: a rename defeats a grep, which is how the node-walk
    guard in `test_blocks.py` failed the first time.
    """
    sources = sorted(pathlib.Path(SRC).rglob("*.py"))
    trees = {p: ast.parse(p.read_text(encoding="utf-8"), filename=str(p)) for p in sources}
    found = [(p, n) for p, t in trees.items() for n in _deletes(t, "documents")]
    assert len(found) == 1, f"one DELETE FROM documents in the source tree, found {found}"
    # A formatted statement would slip past every string check here.
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and node.values and isinstance(
                    node.values[0], ast.Constant) and str(node.values[0].value).startswith(
                    "DELETE FROM"):
                pytest.fail(f"{path}:{node.lineno} builds a DELETE with an f-string")
    path, doc_delete = found[0]
    tree = trees[path]
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    block = doc_delete
    while not isinstance(block, ast.With):
        block = parents[block]
    inside = set(ast.walk(block))
    segs = [n for n in _deletes(tree, "segments") if n in inside]
    nodes = [n for n in _deletes(tree, "nodes") if n in inside]
    assert len(segs) == 1 and len(nodes) == 1, "all three in the same with block"
    assert segs[0].lineno < nodes[0].lineno < doc_delete.lineno, "documents last"
    first = block.body[0]
    assert (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
            and getattr(first.value.func, "id", None) == "_begin_write"), (
        "the write lock is taken before the first read the delete depends on")


def test_a_failing_third_delete_rolls_the_first_two_back(project, capsys):
    """Acceptance criterion 4, by behaviour. A trigger fails the `documents`
    DELETE from inside SQLite, so nothing about the code under test is patched."""
    _book(project, capsys)
    _carry(capsys, "ch1.md", "ch2.md")
    before = _rows(project, "novel.md")
    assert all(before.values()), before
    conn = sqlite3.connect(str(project / ".lx" / "state.db"))
    with conn:
        conn.execute("CREATE TRIGGER boom BEFORE DELETE ON documents "
                     "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    conn.close()
    with pytest.raises(sqlite3.IntegrityError):
        store.forget_doc("novel.md", "zh-TW")
    assert _rows(project, "novel.md") == before


# ── aim: the row named is the row removed ───────────────────────────────────

def _collision(project, capsys):
    _ok(capsys, "init")
    _write(project, "docs/guide.md", ["A guide."])
    _ok(capsys, "extract", "docs/guide.md", "--lang", "zh-TW")
    _apply(project, capsys, "docs/guide.md", {"s0001": "一份指南。"})
    _write(project, "docs_guide.md", ["Something else."])


@pytest.mark.parametrize("flag", [[], ["--discard-wording"]])
def test_a_colliding_spelling_never_deletes_the_other_documents_state(project, capsys, flag):
    """Acceptance criterion 5. `doc_id` maps both spellings to one row, so a
    person forgetting a file they can see would destroy one they cannot. The
    override answers a different refusal and does not reach this one."""
    _collision(project, capsys)
    before = _rows(project, "docs_guide.md")
    code, _out, err = _lx(capsys, "forget", "docs_guide.md", "--lang", "zh-TW", *flag)
    assert code == 2, err
    assert "Traceback" not in err
    assert "docs/guide.md [zh-TW] is" in err, err
    assert "lx forget docs/guide.md --lang zh-TW" in err, "names the stored spelling"
    assert _rows(project, "docs_guide.md") == before


def test_the_stored_spelling_forgets_it_and_frees_the_identity(project, capsys):
    """The other file becomes offerable, which `do_untracked` already computes."""
    _collision(project, capsys)
    config = json.loads((project / "lx.config.json").read_text(encoding="utf-8"))
    config["sources"] = ["docs/**/*.md", "*.md"]
    (project / "lx.config.json").write_text(json.dumps(config), encoding="utf-8")
    held = json.loads(_ok(capsys, "untracked", "--json"))["collisions"]
    assert held and held[0]["offered"] is None, held
    out = _ok(capsys, "forget", "docs/guide.md", "--lang", "zh-TW", "--discard-wording")
    assert "lx untracked` offers it again" in out, out
    freed = json.loads(_ok(capsys, "untracked", "--json"))["collisions"]
    assert freed and freed[0]["offered"] is not None, freed


def test_names_are_matched_exactly_and_the_near_miss_is_named(project, capsys):
    """Edge 3. Built so that both CI legs see the same files: the row is `Book.md`,
    and `Book.md` is deleted before `book.md` is written. Only the policy is
    asserted — an exact match, and a hint that is string work."""
    _ok(capsys, "init")
    _write(project, "Book.md", ["A line."])
    _ok(capsys, "extract", "Book.md", "--lang", "zh-TW")
    (project / "Book.md").unlink()
    _write(project, "book.md", ["A line."])
    code, _out, err = _lx(capsys, "forget", "book.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "nothing to forget" in err and "differs from it only in case: Book.md" in err, err
    assert _rows(project, "Book.md")["documents"] == 1
    _ok(capsys, "forget", "Book.md", "--lang", "zh-TW")
    assert _rows(project, "Book.md")["documents"] == 0


def test_another_language_is_named_when_the_pair_is_not_tracked(project, capsys):
    _ok(capsys, "init")
    _write(project, "a.md", ["One."])
    _ok(capsys, "extract", "a.md", "--lang", "zh-TW")
    code, _out, err = _lx(capsys, "forget", "a.md", "--lang", "zh-tw")
    assert code == 2, err
    assert "It is tracked in zh-TW" in err, err
    assert _rows(project, "a.md")["documents"] == 1


def test_a_directory_with_no_state_is_told_so_and_given_none(project, capsys):
    code, _out, err = _lx(capsys, "forget", "a.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "nothing to forget" in err
    assert not (project / ".lx").exists(), "a refusal creates no state"


def test_a_row_with_no_source_path_is_refused_and_kept(project, capsys):
    """Nothing to compare the spelling against, so the aim cannot be confirmed."""
    _ok(capsys, "init")
    _write(project, "a.md", ["One."])
    _ok(capsys, "extract", "a.md", "--lang", "zh-TW")
    statedb._write(project, "UPDATE documents SET meta=?", (json.dumps({"lang": "zh-TW"}),))
    code, _out, err = _lx(capsys, "forget", "a.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "carries no source path" in err, err
    assert _rows(project, "a.md")["documents"] == 1


def test_a_row_from_a_newer_build_is_refused_rather_than_judged(project, capsys):
    _ok(capsys, "init")
    _write(project, "a.md", ["One."])
    _ok(capsys, "extract", "a.md", "--lang", "zh-TW")
    statedb.set_state_version(project, 99)
    code, _out, err = _lx(capsys, "forget", "a.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "upgrade scriptorium to forget it" in err, err
    assert "--reset" not in err, "a reset is extract's escape, and it discards"
    assert _rows(project, "a.md")["documents"] == 1


def test_lang_is_required():
    with pytest.raises(SystemExit) as e:
        cli.build_parser().parse_args(["forget", "a.md"])
    assert e.value.code == 2


# ── the refusal: what would be lost, and nothing else ────────────────────────

def test_a_split_that_was_not_carried_is_refused_and_offered_the_carry(project, capsys):
    """The case the package that scheduled this called its flagship: two new
    rows, both empty, and the old row the only thing holding the book.

    `content_hash` coverage — the predicate that package proposed — allows it,
    because the empty rows hold every paragraph and none of the wording. The
    chapters were extracted without `--tone` in a project configured `technical`,
    so the carry the message offers has to name the novel's register.
    """
    _book(project, capsys)
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _ok(capsys, "extract", "ch2.md", "--lang", "zh-TW")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "holds 4 translated segment(s)" in err, err
    assert "s0001, s0002 — translated here, untranslated in ch1.md" in err, err
    for chapter in ("ch1.md", "ch2.md"):
        command = f"lx extract {chapter} --lang zh-TW --from novel.md --tone literary"
        assert command in err, err
        _ok(capsys, *command.split()[1:])
    assert _rows(project, "novel.md")["documents"] == 1
    # Following the advice loses nothing, which is the whole claim of offering it.
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")


def test_indented_human_wording_carried_across_is_still_the_same_wording(project, capsys):
    """`lx apply` keeps the U+3000 indent and every carry strips it. A byte
    comparison refused this — the ordinary split — on its two indented segments."""
    _book(project, capsys)
    _carry(capsys, "ch1.md", "ch2.md")
    # A list, not a map by id: three documents each have an `s0001`.
    stored = [s["target"] for s in statedb.segments(project)]
    # The premise, asserted rather than assumed: the old row kept the indent and
    # the carry did not, so the two rows really do hold different bytes.
    assert "　　第一段。" in stored and "第一段。" in stored, stored
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")


def test_a_chapter_reworded_after_its_carry_is_refused_and_never_offered_a_carry(
        project, capsys):
    """The case every design scored as rare and the critique showed is the
    common one: the double count is noticed after the chapters have been worked
    on. Offering `--from` here is what reverted the re-wording when it was
    measured, so the message has to say not to."""
    _book(project, capsys)
    _carry(capsys, "ch1.md", "ch2.md")
    _apply(project, capsys, "ch1.md", {"s0002": "改寫過的第二段。"})
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "s0002 — ch1.md holds a different wording" in err, err
    assert "do not carry into ch1.md" in err and "(s0002)" in err, err
    assert "lx extract ch1.md" not in err, "the carry would revert the re-wording"


def test_a_wording_the_memory_route_lost_is_named(project, capsys):
    """Commit, cut, extract without `--from`: the memory holds one wording per
    key and gives both chapters the second translation of the repeated line.
    The first exists only in the old row now. Origin `agent`, so only the
    wording is under test here."""
    _book(project, capsys, SPLIT, SPLIT_WORDING, cut=3, origin="agent")
    # `lx commit` reads the stored state, never the file, so the cut can
    # already have happened.
    _ok(capsys, "commit", "novel.md", "--lang", "zh-TW")
    for chapter in ("ch1.md", "ch2.md"):
        _ok(capsys, "extract", chapter, "--lang", "zh-TW", "--tone", "literary")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "holds 1 translated segment(s)" in err, err
    assert "s0003 — ch1.md, ch2.md hold a different wording" in err, err


def test_a_persons_wording_is_not_covered_by_a_machine_copy(project, capsys):
    """The memory route turns `human` into `tm`. Forgetting the only row that
    says a person wrote a wording removes what origin precedence protects, so
    it is refused — and the carry it offers puts the provenance back."""
    _book(project, capsys, SPLIT, SPLIT_WORDING, cut=3, origin="human")
    _ok(capsys, "commit", "novel.md", "--lang", "zh-TW")
    for chapter in ("ch1.md", "ch2.md"):
        _ok(capsys, "extract", chapter, "--lang", "zh-TW", "--tone", "literary")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "a person wrote it here, and every other copy of the wording is a machine's" in err
    assert "lx extract ch1.md --lang zh-TW --from novel.md" in err, err
    _carry(capsys, "ch1.md", "ch2.md")
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")


def test_a_repeated_wording_is_counted_per_position(project, capsys):
    """A multiset, not a set. Three copies here and two elsewhere loses one —
    the cut that dropped a repeated paragraph, which nothing else can see."""
    _book(project, capsys, ["A line."] * 3, {f"s000{i}": "一行。" for i in (1, 2, 3)},
          cut=2)
    _carry(capsys, "ch1.md")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "holds 1 translated segment(s)" in err, err
    assert "held elsewhere fewer times than here (ch1.md)" in err, err


def test_two_rows_that_cover_each_other_cannot_both_be_forgotten(project, capsys):
    """Checked against what survives each forget, one row per call — so the
    second is refused, where a two-row verdict computed at once would allow both."""
    _ok(capsys, "init")
    _write(project, "a.md", ["One.", "Two."])
    _ok(capsys, "extract", "a.md", "--lang", "zh-TW")
    _apply(project, capsys, "a.md", {"s0001": "一。", "s0002": "二。"})
    _write(project, "b.md", ["One.", "Two."])
    _carry(capsys, "b.md", source="a.md")
    _ok(capsys, "forget", "a.md", "--lang", "zh-TW")
    code, _out, err = _lx(capsys, "forget", "b.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "in no other tracked document" in err, err


def test_another_language_never_covers_this_one(project, capsys):
    _book(project, capsys)
    _write(project, "novel.md", NOVEL)
    _ok(capsys, "extract", "novel.md", "--lang", "ja-JP", "--tone", "literary")
    _apply(project, capsys, "novel.md", WORDING, lang="ja-JP")
    for chapter in ("ch1.md", "ch2.md"):
        _ok(capsys, "extract", chapter, "--lang", "ja-JP", "--from", "novel.md")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    _ok(capsys, "forget", "novel.md", "--lang", "ja-JP")
    assert _rows(project, "novel.md")["documents"] == 1, "zh-TW is still tracked"


@pytest.mark.parametrize("lang", ["zh-TW", "zh_tw"])
def test_a_row_with_nothing_translated_is_forgotten_without_the_flag(project, capsys, lang):
    """Nothing translated is nothing lost — a document extracted by mistake, or
    under a mistyped tag, is the case most worth forgetting."""
    _ok(capsys, "init")
    _write(project, "a.md", ["One.", "Two."])
    _ok(capsys, "extract", "a.md", "--lang", lang)
    out = _ok(capsys, "forget", "a.md", "--lang", lang)
    assert "2 segment(s), 0 translated" in out, out


def test_whitespace_is_not_wording(project, capsys):
    _ok(capsys, "init")
    _write(project, "a.md", ["One."])
    _ok(capsys, "extract", "a.md", "--lang", "zh-TW")
    statedb.set_target(project, "s0001", "   ")
    _ok(capsys, "forget", "a.md", "--lang", "zh-TW")


def test_the_discard_flag_names_exactly_what_it_discarded(project, capsys):
    _book(project, capsys)
    _carry(capsys, "ch1.md")
    out = _ok(capsys, "forget", "novel.md", "--lang", "zh-TW", "--discard-wording")
    assert "discarded, as asked, the 2 held nowhere else: s0003, s0004" in out, out


# ── the check report ────────────────────────────────────────────────────────

def test_the_check_report_goes_with_the_row_and_stays_with_a_refusal(project, capsys):
    _book(project, capsys)
    _carry(capsys, "ch1.md")
    report = project / ".lx" / "reports" / "novel.md.zh-TW.json"
    _lx(capsys, "check", "novel.md", "--lang", "zh-TW")
    assert report.exists()
    assert _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")[0] == 2
    assert report.exists(), "a refusal deletes nothing"
    _carry(capsys, "ch2.md")
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert not report.exists()


def test_no_file_name_is_built_from_a_lang_that_is_not_a_tag(project, capsys):
    """`report_path` interpolates `lang` raw and the CLI does not validate it:
    under this tag the report's name normalizes to the project's own
    `lx.config.json`. The row goes — its key is an SQL parameter — and no file
    is removed. On Windows this is the gate; on POSIX the path would not resolve
    either way, so only the policy is asserted."""
    _ok(capsys, "init")
    config = (project / "lx.config.json").read_bytes()
    _write(project, "x.md", ["One."])
    lang = "../../../../lx.config"
    _ok(capsys, "extract", "x.md", "--lang", lang)
    out = _ok(capsys, "forget", "x.md", "--lang", lang)
    assert "is not a language tag" in out, out
    assert (project / "lx.config.json").read_bytes() == config
    assert _rows(project, "x.md", lang)["documents"] == 0


# ── what the other commands say ─────────────────────────────────────────────

def test_the_missing_source_message_names_forget_after_the_carry(project, capsys):
    _book(project, capsys)
    code, _out, err = _lx(capsys, "extract", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    carry = err.index("--from novel.md")
    forget = err.index("lx forget novel.md --lang zh-TW")
    assert carry < forget, "forget is named after the carry, never instead of it"


def test_the_carry_says_whether_the_old_row_can_now_be_forgotten(project, capsys):
    """Asked after each save, so the second chapter's own rows count."""
    _book(project, capsys)
    first = _carry(capsys, "ch1.md")
    assert "2 translated segment(s) of novel.md are held by no other tracked document yet" \
        in first, first
    second = _carry(capsys, "ch2.md")
    assert "lx forget novel.md --lang zh-TW` removes it without losing any of them" \
        in second, second


def test_status_and_stats_mark_the_row_whose_file_is_gone(project, capsys):
    """Display only, and only in the human output: `--json` gains no key, which
    the contract test's both-directions key comparison enforces separately."""
    _book(project, capsys)
    _carry(capsys, "ch1.md", "ch2.md")
    for command in ("status", "stats"):
        out = _ok(capsys, command)
        marked = [line for line in out.splitlines() if "(no file at this path)" in line]
        assert len(marked) == 1 and "novel.md [zh-TW]" in marked[0], out
        assert "1 tracked document(s) have no file at their path" in out, out
        assert "not evidence it is still the document that was extracted" in out, out
    document = _status(capsys)["documents"][0]
    assert not any("file" in key for key in document), document
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert "no file at this path" not in _ok(capsys, "status")


def test_a_rowless_document_error_no_longer_recommends_deleting_the_database(
        project, capsys):
    """`cli._document`'s sentence said the state was rebuildable and told the
    reader to delete it — the lossy route the 2026-09-04 entry measured."""
    _ok(capsys, "init")
    _write(project, "a.md", ["One."])
    _ok(capsys, "extract", "a.md", "--lang", "zh-TW")
    statedb._write(project, "UPDATE documents SET meta=?", (json.dumps({"lang": "zh-TW"}),))
    error = _status(capsys)["error"]
    assert "carries no source path" in error
    assert "Do not delete" in error and "not rebuildable" in error, error
