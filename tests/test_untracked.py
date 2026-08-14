"""`lx untracked`, and the subtraction that never fired on this machine.

The command answers one question — which files matching the configured `sources`
globs have no state yet — and it exists because the workbench was answering it
alone, inside `web/server.py`, which invariant 8 does not allow. So the tests
come in two halves: what the list *is*, which is `do_untracked`'s, and what the
two surfaces do with it, which must be the same list twice.

The defect that made this urgent is a separator. A state row records
`os.path.relpath`, which is `docs\\guide.md` on Windows, and the glob side spelled
its key `docs/guide.md` — two spellings of one file, compared as strings, so the
subtraction was a no-op on any platform whose separator is not `/`. It was green
on Linux and wrong on the development machine, which is why several tests here
hand in a backslash-spelled source deliberately: `store.doc_id` flattens every
non-alphanumeric, so those two spellings collapse to one identity **on both
platforms** and the regression is visible on either runner.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium.cli as cli  # noqa: E402
from scriptorium.cli import build_parser, cmd_untracked, do_untracked  # noqa: E402
from scriptorium.config import ConfigError  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")

#: What `lx init` scaffolds, narrowed to the two keys this command reads.
CFG = {"sources": ["docs/**/*.md"], "targets": ["zh-TW"]}


def _project(tmp_path, monkeypatch, *names):
    """A cwd holding the named files, and nothing else. No state, no database."""
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# Title\n\nA sentence.\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _pairs(rows):
    return [(r["source"], r["lang"]) for r in rows]


def _lx(args, cwd):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args], cwd=str(cwd),
                          env={**os.environ, "PYTHONPATH": SRC}, capture_output=True)


# ── what the list is ───────────────────────────────────────────────────────

def test_a_file_with_no_state_is_listed_once_per_target_language(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "docs/guide.md")
    rows = do_untracked({"sources": ["docs/**/*.md"], "targets": ["zh-TW", "ja-JP"]}, docs=[])
    assert _pairs(rows) == [("docs/guide.md", "zh-TW"), ("docs/guide.md", "ja-JP")]


def test_a_tracked_document_is_not_listed_however_its_separator_is_spelled(
        tmp_path, monkeypatch):
    """The Windows defect, reproduced on both runners.

    `docs[].source` is `os.path.relpath` verbatim, so the state row really does
    say `docs\\guide.md` on this machine. Comparing that string against the glob
    side's `docs/guide.md` never matched, and the workbench went on offering to
    extract a document it was already showing in the list above. Both sides go
    through `store.doc_id` now, which flattens the separator — so this test means
    the same thing on Linux, where the backslash is an ordinary character and the
    two spellings still collapse to one identity.
    """
    _project(tmp_path, monkeypatch, "docs/guide.md")
    tracked = [{"source": "docs\\guide.md", "lang": "zh-TW"}]
    assert do_untracked(CFG, docs=tracked) == []
    # And the plain spelling, so the fix is not a trade of one platform for the
    # other.
    assert do_untracked(CFG, docs=[{"source": "docs/guide.md", "lang": "zh-TW"}]) == []


def test_tracked_in_one_language_is_still_untracked_in_another(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "docs/guide.md")
    cfg = {"sources": ["docs/**/*.md"], "targets": ["zh-TW", "ja-JP"]}
    rows = do_untracked(cfg, docs=[{"source": "docs\\guide.md", "lang": "zh-TW"}])
    assert _pairs(rows) == [("docs/guide.md", "ja-JP")]


def test_two_globs_matching_one_file_propose_it_once(tmp_path, monkeypatch):
    """One identity, one offer.

    `doc_id` is what `.lx/state.db` keys a document row on, so two entries for
    one file would be two offers of a row the database can only hold once. The
    subtraction that removes a tracked document removes a repeat for the same
    reason and in the same set.
    """
    _project(tmp_path, monkeypatch, "docs/guide.md")
    cfg = {"sources": ["docs/**/*.md", "docs/*.md", "**/*.md"], "targets": ["zh-TW"]}
    assert _pairs(do_untracked(cfg, docs=[])) == [("docs/guide.md", "zh-TW")]


def test_the_list_is_not_capped(tmp_path, monkeypatch):
    """The 200-entry cap was silent, which is the half that made it a defect.

    101 files in two languages is 202 pairs — above the old cap, and cheap: the
    command globs names and never opens one.
    """
    names = [f"docs/ch{i:03d}.md" for i in range(101)]
    _project(tmp_path, monkeypatch, *names)
    cfg = {"sources": ["docs/**/*.md"], "targets": ["zh-TW", "ja-JP"]}
    assert len(do_untracked(cfg, docs=[])) == 202


def test_two_distinct_files_sharing_one_identity_are_proposed_once(
        tmp_path, monkeypatch):
    """The cost of comparing identities, pinned by an exit code rather than prose.

    `doc_id` replaces every character outside `A-Za-z0-9._-`, not only the
    separator, so `docs/a/b.md` and `docs/a_b.md` are one identity — and
    `.lx/state.db` keys a document row on it, so they really are one document:
    extracting both in sequence leaves one row and the second has overwritten the
    first. Proposing both would be offering that overwrite as new work, which is
    what the code this replaced did.

    The suppression is unconditional — the first spelling reached wins, whether
    or not anything is tracked — and it is silent, which is the half neither
    surface can currently say. Recorded as divergence (18) in
    `docs/contracts/workbench-http.md`, because saying it needs a response field.
    This test exists so that decision cannot be reversed by accident.
    """
    _project(tmp_path, monkeypatch, "docs/a/b.md", "docs/a_b.md")
    cfg = {"sources": ["docs/**/*.md"], "targets": ["zh-TW"]}
    assert _pairs(do_untracked(cfg, docs=[])) == [("docs/a/b.md", "zh-TW")]
    # And with the twin tracked, neither is offered — the same subtraction.
    assert do_untracked(cfg, docs=[{"source": "docs/a/b.md", "lang": "zh-TW"}]) == []


def test_a_sources_pattern_on_another_volume_is_a_sentence_rather_than_a_traceback(
        tmp_path, monkeypatch):
    """`os.path.relpath` raises across volumes, and this command turns paths into ids.

    A library on a second drive or a UNC share is an ordinary thing to point
    `sources` at on this platform, and the raw `ValueError` is not in `main`'s
    handled list — so it reached the terminal as a traceback and exit 1, where
    every other configuration mistake here is one sentence and exit 2. The raise
    is simulated rather than mounted: only Windows has a second volume to fail
    across, and a check that runs on one runner is a check nobody sees fail.
    """
    _project(tmp_path, monkeypatch, "docs/guide.md")

    def foreign(_path):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(cli, "doc_id", foreign)
    with pytest.raises(ConfigError) as e:
        do_untracked(CFG, docs=[])
    assert "docs/**/*.md" in str(e.value), "the refusal names the pattern to fix"


def test_the_emitted_source_carries_forward_slashes_on_every_platform(
        tmp_path, monkeypatch):
    # The spelling a client already receives, unchanged by this work: `glob`
    # hands back `docs\guide.md` on Windows and the wire has always carried
    # `docs/guide.md`. Normalizing the *other* side — `docs[].source` — is a
    # meaning change and waits for the contract's next version bump.
    _project(tmp_path, monkeypatch, "docs/guide.md")
    assert do_untracked(CFG, docs=[])[0]["source"] == "docs/guide.md"


def test_a_configured_pattern_matching_nothing_yields_nothing(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, "notes.txt")
    assert do_untracked(CFG, docs=[]) == []


def test_the_tracked_list_is_read_once_when_the_caller_already_holds_it(
        tmp_path, monkeypatch):
    """`/api/state` loaded every segment of every document twice to draw a page.

    The parameter is the whole fix, so the property worth pinning is that passing
    it really does replace the read rather than merely preceding one.
    """
    _project(tmp_path, monkeypatch, "docs/guide.md")

    def refuse():
        raise AssertionError("do_untracked re-read the tracked list it was given")

    monkeypatch.setattr(cli, "tracked", refuse)
    assert len(do_untracked(CFG, docs=[])) == 1

    calls = []

    def counted():
        calls.append(1)
        return []

    monkeypatch.setattr(cli, "tracked", counted)
    assert len(do_untracked(CFG)) == 1
    assert calls == [1], "with no list in hand the command must fetch exactly one"


# ── what the command prints ────────────────────────────────────────────────

def _run(cfg, **flags):
    """The command as argparse would have called it: defaults, then the overrides."""
    cmd_untracked(argparse.Namespace(**{"json": False, "max": 25, **flags}), cfg)


def test_the_human_display_truncates_and_says_how_to_see_the_rest(
        tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, *[f"docs/ch{i:03d}.md" for i in range(30)])
    _run(CFG, max=4)
    out = capsys.readouterr().out
    assert "30 untracked (source, language) pair(s)" in out
    assert out.count("[zh-TW]") == 4
    # `lx check`'s sentence verbatim: one promise, one wording, two commands.
    assert "... 26 more (use --max or --json)" in out


@pytest.mark.parametrize("cap", [0, -1])
def test_a_max_below_zero_does_not_invent_rows_it_did_not_print(
        tmp_path, monkeypatch, capsys, cap):
    # A negative slice counts from the tail while the "N more" arithmetic counts
    # from the head, so `--max -1` printed 29 of 30 rows and then claimed 31
    # more. The two numbers have to add up to the header's.
    _project(tmp_path, monkeypatch, *[f"docs/ch{i:03d}.md" for i in range(30)])
    _run(CFG, max=cap)
    out = capsys.readouterr().out
    assert out.count("[zh-TW]") == 0
    assert "... 30 more (use --max or --json)" in out


def test_json_is_never_truncated_and_carries_what_decided_the_answer(
        tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, *[f"docs/ch{i:03d}.md" for i in range(30)])
    _run(CFG, json=True, max=4)
    emitted = json.loads(capsys.readouterr().out)
    assert len(emitted["untracked"]) == 30
    assert emitted["sources"] == CFG["sources"]
    assert emitted["targets"] == CFG["targets"]
    assert emitted["untracked"][0] == {"source": "docs/ch000.md", "lang": "zh-TW"}


@pytest.mark.parametrize("cfg,marks", [
    ({"sources": ["docs/**/*.md"], "targets": []},
     ("no target language is configured", "lx config set targets")),
    ({"sources": [], "targets": ["zh-TW"]},
     ("`sources` is empty", "lx config set sources")),
])
def test_an_empty_configuration_key_is_named_rather_than_reported_as_done(
        tmp_path, monkeypatch, capsys, cfg, marks):
    """Both empties produce an empty list, and neither means "all tracked".

    A person who has not set `targets` yet would otherwise be told there is
    nothing to do, by the one command whose job is to say what there is.

    Asserted on a phrase only the intended branch can produce, never on the key
    name. Measured: with both guards deleted the `sources` half still passed,
    because the fallback sentence it must not reach — "nothing new matches
    sources (…)" — contains the word `sources` itself, and the `targets` half
    only died by the accident that the same sentence does not contain `targets`.
    An assertion that a mutant survives is not a test.
    """
    _project(tmp_path, monkeypatch, "docs/guide.md")
    _run(cfg)
    out = capsys.readouterr().out
    for mark in marks:
        assert mark in out
    assert "untracked (source, language)" not in out
    assert "nothing new matches" not in out


def test_nothing_new_names_the_globs_it_looked_through(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, "notes.txt")
    _run(CFG)
    out = capsys.readouterr().out
    assert "docs/**/*.md" in out and "zh-TW" in out


def test_the_parser_registers_the_command_with_check_s_own_default():
    args = build_parser().parse_args(["untracked"])
    assert args.fn is cmd_untracked
    assert args.max == 25 and args.json is False


# ── the two surfaces answer with one list ──────────────────────────────────

def test_an_extracted_document_leaves_the_listing_on_this_machine(tmp_path):
    """The acceptance criterion, end to end, through the real state database.

    A subprocess rather than an in-process call: this is the path a person takes,
    and `os.path.relpath` inside `do_extract` is what writes the separator the
    comparison used to trip over. On Windows that is `docs\\guide.md` in the
    database and `docs\\guide.md` out of `glob`, meeting as `docs_guide.md`.
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_bytes(b"# Title\n\nA sentence.\n")
    assert _lx(["init"], tmp_path).returncode == 0

    before = _lx(["untracked", "--json"], tmp_path)
    assert before.returncode == 0, before.stderr.decode("utf-8", "replace")
    assert json.loads(before.stdout)["untracked"] == [
        {"source": "docs/guide.md", "lang": "zh-TW"}]

    extracted = _lx(["extract", "docs/guide.md", "--lang", "zh-TW"], tmp_path)
    assert extracted.returncode == 0, extracted.stderr.decode("utf-8", "replace")

    after = _lx(["untracked", "--json"], tmp_path)
    assert after.returncode == 0, after.stderr.decode("utf-8", "replace")
    assert json.loads(after.stdout)["untracked"] == []
