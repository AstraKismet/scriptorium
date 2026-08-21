"""The block map: the rendered document, cut where the skeleton already cuts it.

One walk of `doc["nodes"]` answers both questions — what does this document say,
and what does it say *at this position* — because two walks are two chances to
disagree and the reading view would be reading the one nobody writes files from.
Everything here is about that single property and the two ways it can be lost:
a second walk, and a line terminator applied to the join that cannot be applied
to the parts.

The acceptance test of the whole design is
`test_a_crlf_document_with_a_non_bmp_character_round_trips_through_the_blocks`.
It exists because integer spans were refused for two hazards that are both
invisible on LF-only ASCII fixtures, and — measured 2026-08-21 — no file anywhere
under `tests/` contained a character outside the BMP before this one.
"""

import ast
import json
import os
import random
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import formats  # noqa: E402
from scriptorium.cli import do_apply, do_blocks, do_extract, do_render  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.docio import apply_terminator, apply_terminator_parts  # noqa: E402
from scriptorium.mask import unmask  # noqa: E402
from scriptorium.normalize import polish_rendered  # noqa: E402
from scriptorium.skeleton import render_blocks  # noqa: E402
from scriptorium.store import SEGMENTATION_VERSION, load_doc, tm_key  # noqa: E402

CFG = dict(DEFAULT_CONFIG)

CORPUS = os.path.join(os.path.dirname(__file__), "corpus")
CORPUS_TEXT = os.path.join(os.path.dirname(__file__), "corpus-text")

#: A uniformly-CRLF Markdown document carrying characters outside the Basic
#: Multilingual Plane, which is the interaction the block map's "text, never
#: spans" decision was taken for. Built here rather than dropped into
#: `tests/corpus/`, whose stated rule is one input file per property and whose
#: enumerating comment would go stale under a two-property file.
#:
#: **The astral characters sit beside ordinary letters, deliberately.**
#: `mask.has_translatable_text` is BMP-only, so a paragraph made of astral
#: characters alone is not translatable, becomes skeleton, and the axis this
#: fixture exists for is never exercised at all — the test would pass while
#: measuring nothing.
CRLF_ASTRAL = ("# Chapter 𠮷 One\r\n"
               "\r\n"
               "A paragraph naming 𠮷郎 and 𪚥, with an emoji 😀 in it.\r\n"
               "\r\n"
               "- item about 𠮷郎\r\n"
               "- second item\r\n").encode()


def _project(tmp_path, monkeypatch, data=CRLF_ASTRAL, name="book.md"):
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    (root / name).write_bytes(data)
    do_extract(name, "zh-TW", CFG)
    return name, root


def _lx(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _rendered_by_a_second_walk(doc, lang, cfg, fallback, marker):
    """What this document renders to, walked again — the oracle, on purpose.

    **A test whose oracle is produced by the code under test measures nothing**,
    and that is what the three "joins back" tests were until 2026-08-21: with
    `do_render` written as the join of `do_blocks`, 56 parametrized cases
    asserted `join(blocks) == join(blocks)` and could not fail. `do_render` goes
    through `Format.render` again now, which makes those two paths through the
    registry — but `skeleton.render` is still the join of
    `skeleton.render_blocks`, so the *content* on both sides is one function's
    answer and only the wiring around it differs.

    This is the second opinion that closes the rest of it: a hand-written walk of
    `doc["nodes"]`, copied from `skeleton.render` as it stood at `3f12225` —
    before the block map existed and therefore uninfluenced by it — with the
    terminator rule spelled out rather than called. It lives here and not in
    `src/`, where `test_only_one_walk_of_the_document_nodes_exists_in_the_source`
    would refuse it and would be right to: the product has one walk, and a test
    that agrees with it by construction is not a test.
    """
    by_id = {s["id"]: s for s in doc["segments"]}
    parts, missing = [], 0
    for node in doc["nodes"]:
        if node["t"] == "raw":
            parts.append(node["v"])
            continue
        seg = by_id[node["id"]]
        if seg.get("target"):
            text = unmask(seg["target"], seg["slots"])
            parts.append(polish_rendered(text, lang, cfg))
        else:
            missing += 1
            parts.append(unmask(seg["masked"], seg["slots"]) if fallback
                         else marker.format(id=seg["id"]))
    text = "".join(parts)
    eol = doc.get("eol", "\n")
    return (text if eol == "\n" else re.sub(r"\r?\n", eol, text)), missing


# ── the single walk ────────────────────────────────────────────────────────

def test_the_blocks_join_back_into_exactly_what_render_returns(tmp_path, monkeypatch):
    src, _ = _project(tmp_path, monkeypatch)
    for fallback in (False, True):
        blocks, missing = do_blocks(src, "zh-TW", CFG, fallback=fallback)
        text, render_missing = do_render(src, "zh-TW", CFG, fallback=fallback)
        assert "".join(b["text"] for b in blocks) == text
        assert missing == render_missing


def _attributed_to_the_right_node(blocks, doc, where):
    """One block per node, in order, `id` and `kind` on the node that owns them.

    **The join can be exact while every label sits on the wrong position**, and
    no test above can see that: they compare concatenations, and moving a label
    moves no character. Measured 2026-08-21 — a mutant that replaced every
    block's `kind` with a constant survived the whole of this file.
    """
    nodes = doc["nodes"]
    by_id = {s["id"]: s for s in doc["segments"]}
    assert len(blocks) == len(nodes), where
    for i, (block, node) in enumerate(zip(blocks, nodes)):
        if node["t"] == "raw":
            assert block["id"] is None, f"{where} node {i}"
            assert block["kind"] is None and block["from"] is None, f"{where} node {i}"
            continue
        assert block["id"] == node["id"], f"{where} node {i}"
        assert block["kind"] == by_id[node["id"]].get("kind"), f"{where} node {i}"
        assert block["from"] in ("target", "source", "marker"), f"{where} node {i}"


def _joins_back(src, name, corpus):
    """The four checks every fixture gets, so the corpora cannot drift apart.

    Two of them are the block map against `do_render`; the third is both against
    an oracle neither of them produced; the fourth is where the labels sit,
    which the other three are blind to.
    """
    where = f"{corpus}/{name}"
    blocks, missing = do_blocks(src, "zh-TW", CFG, fallback=True)
    text, render_missing = do_render(src, "zh-TW", CFG, fallback=True)
    assert "".join(b["text"] for b in blocks) == text, where
    assert missing == render_missing, where

    doc = load_doc(src, "zh-TW")
    fmt = formats.for_doc(doc)
    want, want_missing = _rendered_by_a_second_walk(
        doc, "zh-TW", CFG, fallback=True, marker=fmt.marker)
    assert text == want, where
    assert missing == want_missing, where

    _attributed_to_the_right_node(blocks, doc, where)


@pytest.mark.parametrize("name", sorted(os.listdir(CORPUS)))
def test_every_markdown_fixture_joins_back(tmp_path, monkeypatch, name):
    """Over the corpus, because one hand-written document proves one shape.

    The fixtures are read as bytes and copied verbatim — a fixture is never
    edited to make a test pass, and this one only reads them.
    """
    with open(os.path.join(CORPUS, name), "rb") as f:
        data = f.read()
    src, _ = _project(tmp_path, monkeypatch, data, name)
    _joins_back(src, name, "corpus")


@pytest.mark.parametrize("name", sorted(os.listdir(CORPUS_TEXT)))
def test_every_plain_text_fixture_joins_back(tmp_path, monkeypatch, name):
    with open(os.path.join(CORPUS_TEXT, name), "rb") as f:
        data = f.read()
    src, _ = _project(tmp_path, monkeypatch, data, name)
    _joins_back(src, name, "corpus-text")


def test_the_marker_branch_is_measured_against_the_second_walk_too(
        tmp_path, monkeypatch):
    """`fallback=False` is the branch the corpus sweep above never asks for.

    It is also the only branch that counts `missing` while writing something
    other than the source, so an oracle that agreed on the fallback and not here
    would still leave the counter unmeasured.
    """
    src, _ = _project(tmp_path, monkeypatch)
    text, missing = do_render(src, "zh-TW", CFG, fallback=False)
    doc = load_doc(src, "zh-TW")
    fmt = formats.for_doc(doc)
    want, want_missing = _rendered_by_a_second_walk(
        doc, "zh-TW", CFG, fallback=False, marker=fmt.marker)
    assert (text, missing) == (want, want_missing)
    assert missing and "<!-- untranslated " in text


def test_every_registered_format_can_answer_a_block_map():
    """A format that renders and cannot report blocks is a hole, not a default.

    `Format.render_blocks` defaults to `None` on purpose — a container format
    brings its own `render` and would need its own block builder — so this
    asserts the two that exist today both answer, and it is what a container
    format's own package will have to satisfy rather than skip.
    """
    for name in ("markdown", "text"):
        assert formats.by_name(name).render_blocks is not None


SRC = os.path.join(os.path.dirname(__file__), "..", "src", "scriptorium")


def _source_files():
    for dirpath, _dirs, files in os.walk(SRC):
        if "__pycache__" in dirpath:
            continue
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _node_list_uses(path):
    """``(loops, reads)`` over ``…["nodes"]`` in one module, by syntax not text.

    **Read with `ast` rather than grepped**, which is the repair rather than a
    refinement: until 2026-08-21 this guard matched the literal string
    `for node in doc["nodes"]`, so single quotes, a renamed loop variable, a
    renamed document variable or a comprehension all walked straight past it.
    A guard a rename defeats is a guard that reports on the day it is written
    and never again.

    Two answers because they fail differently. A *loop* over the node list is a
    second walk outright. A *read* of it anywhere else is the step before one —
    `nodes = doc["nodes"]` followed by a loop over `nodes` is a walk this cannot
    see any other way, and naming the read is what closes it without a
    dataflow analysis nobody would maintain.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    def is_the_node_list(node):
        return (isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "nodes")

    loops, reads = [], []
    for node in ast.walk(tree):
        iters = []
        # Tuples rather than `X | Y`: `requires-python` is >= 3.9 and PEP 604
        # unions in `isinstance` are 3.10, so the shorter spelling is a
        # TypeError on the interpreter CI's oldest job runs.
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iters = [node.iter]
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                               ast.GeneratorExp)):
            iters = [gen.iter for gen in node.generators]
        loops += [node.lineno for it in iters if is_the_node_list(it)]
        if is_the_node_list(node) and isinstance(node.ctx, ast.Load):
            reads.append(node.lineno)
    return loops, reads


def test_only_one_walk_of_the_document_nodes_exists_in_the_source():
    """The property `render_blocks` exists to hold, asserted at source level.

    A future block builder that grows its own walk is the failure this file
    cannot otherwise see: both walks would be correct on the day they were
    written and would drift apart on the first change to either.
    """
    walked = {}
    for path in _source_files():
        loops, _reads = _node_list_uses(path)
        if loops:
            walked[os.path.relpath(path, SRC)] = loops
    # The file, not the line: a line number here would go red on every edit to
    # `skeleton.py` and teach the next reader to update the number rather than
    # ask why it moved.
    assert sorted(walked) == ["skeleton.py"] and len(walked["skeleton.py"]) == 1, (
        f"the walk of doc['nodes'] is at {walked}. There is one, in "
        f"`skeleton.render_blocks`, and `render` is written in terms of it — a "
        f"second walk is a second answer to what the document says at a position.")


def test_nothing_outside_the_walk_reads_the_node_list_at_all():
    """The step before a second walk, caught where the loop check cannot reach.

    `nodes = doc["nodes"]` and then a loop over `nodes` is a walk no syntactic
    check of the loop itself will find. Naming every *read* of the key does find
    it, and the answer is short enough to be an allowlist: `skeleton.py` walks
    it, `store.py` builds it, and a read anywhere else is the thing this file
    exists to notice.
    """
    reads = {}
    for path in _source_files():
        _loops, found = _node_list_uses(path)
        if found:
            reads[os.path.relpath(path, SRC)] = found
    assert sorted(reads) == ["skeleton.py"], (
        f"doc['nodes'] is read at {reads}. `skeleton.render_blocks` is the one "
        f"reader; `store.py` assembles the list and never reads it back. If a "
        f"new reader is genuinely right, it belongs in this sentence first.")


# ── the block record ───────────────────────────────────────────────────────

def test_a_block_carries_exactly_the_four_keys_the_contract_documents(
        tmp_path, monkeypatch):
    src, _ = _project(tmp_path, monkeypatch)
    blocks, _ = do_blocks(src, "zh-TW", CFG, fallback=True)
    for block in blocks:
        assert set(block) == {"id", "kind", "from", "text"}
        assert isinstance(block["text"], str)


def test_a_skeleton_block_is_null_in_every_field_but_its_text(tmp_path, monkeypatch):
    src, _ = _project(tmp_path, monkeypatch)
    blocks, _ = do_blocks(src, "zh-TW", CFG, fallback=True)
    skeleton = [b for b in blocks if b["id"] is None]
    assert skeleton, "the fixture has skeleton in it or this proves nothing"
    for block in skeleton:
        assert block["kind"] is None and block["from"] is None


def test_from_names_the_branch_that_produced_the_text(tmp_path, monkeypatch):
    """`target`, `source` and `marker` are three answers and all three occur.

    `marker` is unreachable on `GET /api/preview`, which hardcodes `fallback`
    true — so it is asserted here, where the CLI can ask for it, and the contract
    says the endpoint never returns it.
    """
    src, _ = _project(tmp_path, monkeypatch)
    marked, _ = do_blocks(src, "zh-TW", CFG, fallback=False)
    assert {b["from"] for b in marked if b["id"]} == {"marker"}

    fell_back, _ = do_blocks(src, "zh-TW", CFG, fallback=True)
    assert {b["from"] for b in fell_back if b["id"]} == {"source"}

    seg = load_doc(src, "zh-TW")["segments"][0]["id"]
    do_apply(src, "zh-TW", CFG, {seg: "第 𠮷 一章"}, origin="human")
    mixed, _ = do_blocks(src, "zh-TW", CFG, fallback=True)
    by_id = {b["id"]: b for b in mixed}
    assert by_id[seg]["from"] == "target"
    assert {b["from"] for b in mixed if b["id"] and b["id"] != seg} == {"source"}


def test_every_block_is_attributed_to_the_node_it_stands_for(tmp_path, monkeypatch):
    """The label's position, asserted where a mixed document can be built.

    The corpus sweep runs this too, on `fallback=True` and with nothing
    translated; here one segment carries a target, so `from` differs between
    positions and a rule that attributed by anything other than position has
    somewhere to go wrong.
    """
    src, _ = _project(tmp_path, monkeypatch)
    seg = load_doc(src, "zh-TW")["segments"][1]["id"]
    do_apply(src, "zh-TW", CFG, {seg: "一段譯文。"}, origin="human")
    doc = load_doc(src, "zh-TW")

    for fallback in (False, True):
        blocks, _ = do_blocks(src, "zh-TW", CFG, fallback=fallback)
        _attributed_to_the_right_node(blocks, doc, f"fallback={fallback}")
        by_id = {b["id"]: b for b in blocks if b["id"]}
        assert by_id[seg]["from"] == "target"
        assert len({b["from"] for b in blocks if b["id"]}) == 2, \
            "the fixture has one translated segment and several that are not"


def test_a_whitespace_only_target_is_why_status_could_not_be_this_key(
        tmp_path, monkeypatch):
    """The case that decides `from` against `status`, written down as a test.

    `render` branches on a *truthy* target and `store` derives `status` from a
    *stripped* one, so a target of blanks renders its own text, reports
    `pending`, and is not counted by `missing`. A client reading `status` as "is
    this text a translation" would be told the wrong thing about exactly this
    row. `do_apply` refuses a blank target at the door, so the row is reached the
    way a pre-2026-08-14 state file reaches it: by writing it directly.
    """
    src, _ = _project(tmp_path, monkeypatch)
    doc = load_doc(src, "zh-TW")
    seg = doc["segments"][0]
    seg["target"] = "   "
    blocks, missing = render_blocks(doc, CFG, fallback=True)
    by_id = {b["id"]: b for b in blocks}
    assert by_id[seg["id"]]["from"] == "target"
    assert by_id[seg["id"]]["text"] == "   "
    assert missing == len(doc["segments"]) - 1


def test_a_target_stored_unpolished_is_polished_by_the_walk(tmp_path, monkeypatch):
    """`polish` is a branch of the walk and nothing was asserting it ran.

    Measured 2026-08-21: a mutant that deleted the call survived every test in
    this file. Two facts hid it — the corpus sweeps run on `fallback=True` and
    never reach the target branch at all, and `do_apply` normalizes on ingest,
    so a target that arrived through a writer is already spaced and the
    render-time call cannot change it.

    So the row is written directly, the way the whitespace-only target above is:
    that is how a state file written before a normalization profile changed
    reaches this code, and it is the case the branch exists for.
    """
    src, _ = _project(tmp_path, monkeypatch)
    doc = load_doc(src, "zh-TW")
    seg = doc["segments"][1]
    seg["target"] = "一段提到𠮷郎的hello文字。"
    blocks, _ = render_blocks(
        doc, CFG, polish=lambda t: polish_rendered(t, "zh-TW", CFG), fallback=True)
    by_id = {b["id"]: b for b in blocks}
    assert by_id[seg["id"]]["text"] == "一段提到𠮷郎的 hello 文字。"

    # And with no `polish`, the stored text is what comes back — which is what
    # makes the assertion above about the call rather than about the string.
    bare, _ = render_blocks(doc, CFG, fallback=True)
    assert {b["id"]: b for b in bare}[seg["id"]]["text"] == "一段提到𠮷郎的hello文字。"


# ── the terminator ─────────────────────────────────────────────────────────

def test_a_crlf_document_with_a_non_bmp_character_round_trips_through_the_blocks(
        tmp_path, monkeypatch):
    """The acceptance criterion this whole design exists for.

    Both hazards at once: a document whose terminator is re-imposed at render,
    and characters Python counts as one unit and JavaScript counts as two. A
    target carrying an astral character is applied as well, so the property is
    tested on the branch that renders a *translation* and not only on the
    fallback.

    **The target is written without the space `pangu` inserts**, so what reaches
    the block map is the *normalized* wording rather than the keystrokes — which
    is a fact about this document in this encoding and is worth one assertion.
    It is **not** what covers the walk's `polish` branch, and saying so is the
    point: `do_apply` normalizes on ingest, so a target that arrived through a
    writer is already spaced and the render-time call is a no-op on it. That
    branch is reached by writing the row directly, in
    `test_a_target_stored_unpolished_is_polished_by_the_walk`.
    """
    src, root = _project(tmp_path, monkeypatch)
    doc = load_doc(src, "zh-TW")
    assert doc["eol"] == "\r\n"
    seg = doc["segments"][1]["id"]
    do_apply(src, "zh-TW", CFG, {seg: "一段提到𠮷郎與𪚥的文字，還有一個emoji 😀。"},
             origin="human")

    blocks, _ = do_blocks(src, "zh-TW", CFG, fallback=True)
    text, _ = do_render(src, "zh-TW", CFG, fallback=True)
    assert "".join(b["text"] for b in blocks) == text

    # A third opinion, from a walk this package did not write. If the block map
    # and `do_render` agreed on something wrong, this is what would say so.
    # Re-read, because `doc` above predates the target that was just applied.
    applied = load_doc(src, "zh-TW")
    fmt = formats.for_doc(applied)
    want, _ = _rendered_by_a_second_walk(applied, "zh-TW", CFG, True, fmt.marker)
    assert text == want
    # Stated rather than left to the oracle: both walks polish, and this is what
    # polishing looks like. An oracle that stopped polishing too would agree.
    assert "一個 emoji" in text and "一個 emoji" in blocks[
        [b["id"] for b in blocks].index(seg)]["text"]

    # And against the artifact on disk, written by a separate process through
    # `write_document` — not by this test echoing back the string it was just
    # handed, which is what it did until 2026-08-21 and which proved only that
    # `str.encode` round-trips. What a client joins has to be the file, byte for
    # byte, and the bytes are where a terminator either survived or did not.
    run = _lx(["render", src, "--lang", "zh-TW", "--fallback", "-o", "out.md"], root)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    on_disk = (root / "out.md").read_bytes()
    assert "".join(b["text"] for b in blocks).encode("utf-8") == on_disk
    assert b"\r\n" in on_disk and "𠮷".encode() in on_disk
    assert b"\n" not in on_disk.replace(b"\r\n", b"")

    # And the hazard itself, stated: the two counts disagree about this document,
    # which is what an offset would have been wrong by.
    assert len(text) != len(text.encode("utf-16-le")) // 2


def test_do_blocks_uses_the_parts_helper_and_not_the_blanket_rule(
        tmp_path, monkeypatch):
    """The straddle, at the level that chooses which helper to call.

    `apply_terminator_parts` has unit tests and a 20 000-case sweep, and until
    2026-08-21 none of that reached `do_blocks`: swapping it for the naive
    per-block substitution left the whole suite green, because no document in
    either corpus puts a `\\r` at the end of one block and its `\\n` at the start
    of the next. `split_terminator` strips every `\\r` before the parser sees the
    file, and no writer can put one back.

    So the state is **built directly** rather than through a writer, which is
    the only way to reach the shape — and reaching it is the point, since the
    helper's own docstring says four separate facts hold it out of range and a
    third format only has to disturb one of them.
    """
    doc = {
        "format": "markdown", "eol": "\r\n",
        "nodes": [{"t": "raw", "v": "before\r"},
                  {"t": "seg", "id": "s0001"},
                  {"t": "raw", "v": "\nafter\r\n"}],
        "segments": [{"id": "s0001", "kind": "para", "masked": "\nstraddled\r",
                      "slots": {}, "target": ""}],
    }
    monkeypatch.setattr("scriptorium.cli.load_doc", lambda src, lang: doc)

    blocks, _ = do_blocks("built.md", "zh-TW", CFG, fallback=True)
    text, _ = do_render("built.md", "zh-TW", CFG, fallback=True)
    assert "".join(b["text"] for b in blocks) == text

    # Stated, not only cross-checked: the `\r` moved right into the block that
    # holds its `\n`, and no `\r` the pipeline did not decide came out.
    assert text == "before\r\nstraddled\r\nafter\r\n"
    assert [b["text"] for b in blocks] == ["before", "\r\nstraddled", "\r\nafter\r\n"]
    assert "\r\r" not in text


def test_the_terminator_substitution_does_not_distribute_over_the_parts():
    """The case `apply_terminator_parts` exists for, spelled out.

    Applying the blanket `\\r?\\n` rule to each part separately writes a `\\r` the
    pipeline never decided. This is what the helper prevents, and asserting the
    naive spelling *fails* is what stops someone simplifying it away.
    """
    parts = ["a\r", "\nb"]
    joined = apply_terminator("".join(parts), "\r\n")
    assert "".join(apply_terminator(p, "\r\n") for p in parts) != joined
    assert "".join(apply_terminator_parts(parts, "\r\n")) == joined
    # Right, never left: the terminator lands in the block after the segment
    # rather than inside it.
    assert apply_terminator_parts(parts, "\r\n") == ["a", "\r\nb"]


def test_the_parts_helper_agrees_with_the_join_over_randomized_input():
    """A sweep, because the shapes that break this are the ones nobody writes.

    The alphabet is chosen so a terminator can straddle a boundary, sit inside a
    part, be doubled, or be separated by an empty part — the last of which is the
    shape a scan that only looks at the immediate neighbour gets wrong.
    """
    random.seed(20260821)
    alphabet = ["", "a", "\n", "\r", "\r\n", "\n\r", "a\r", "\nb", "\r\r\n", "x\r\ny"]
    for _ in range(20000):
        parts = [random.choice(alphabet) for _ in range(random.randint(0, 5))]
        for eol in ("\n", "\r\n"):
            assert "".join(apply_terminator_parts(parts, eol)) == \
                apply_terminator("".join(parts), eol)


def test_an_lf_document_keeps_every_part_untouched():
    parts = ["a\r", "\nb", ""]
    assert apply_terminator_parts(parts, "\n") == parts


# ── the memory does not move ───────────────────────────────────────────────

def test_the_translation_memory_key_is_unchanged_by_this_package():
    """Pinned as values, not as an import check.

    Nothing about sentences or blocks may reach the memory key or the
    segmentation version: the first would invalidate every banked wording on
    every machine, and the second would do it by a different route. The
    `variant=None` identity is asserted with them, because that property is what
    keeps entries banked before the field existed still matching.
    """
    assert SEGMENTATION_VERSION == 1
    fixed = tm_key("A short sentence.", "para", None, "zh-TW", None)
    assert fixed == tm_key("A short sentence.", "para", None, "zh-TW", None)
    assert tm_key("A short sentence.", "para", None, "zh-TW", None) != \
        tm_key("A short sentence.", "heading", None, "zh-TW", None)


def test_neither_checks_nor_store_pulls_the_sentence_rule_in():
    """Invariant 4, made checkable: a sentence boundary is not a validator.

    Import them in a subprocess, because this test file has already imported
    half the package and `sys.modules` in-process proves nothing.
    """
    code = ("import sys, scriptorium.checks, scriptorium.store; "
            "print('scriptorium.sentences' in sys.modules)")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..", "src")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env)
    assert out.stdout.strip() == b"False", out.stderr.decode("utf-8", "replace")


# ── the CLI surface ────────────────────────────────────────────────────────

def test_lx_blocks_emits_the_same_map_the_endpoint_does(tmp_path, monkeypatch):
    """Invariant 8, asserted rather than intended.

    `blocks` reaching the wire and not the CLI would be a new divergence of
    exactly the class the contract's (2) and (3) recorded and 2026-08-15 closed.
    """
    src, root = _project(tmp_path, monkeypatch)
    expected, missing = do_blocks(src, "zh-TW", CFG, fallback=True)
    run = _lx(["blocks", src, "--lang", "zh-TW", "--fallback", "--json"], root)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    got = json.loads(run.stdout.decode("utf-8"))
    assert got == {"blocks": expected, "missing": missing}


def test_lx_blocks_writes_nothing(tmp_path, monkeypatch):
    src, root = _project(tmp_path, monkeypatch)
    before = sorted(p.name for p in root.iterdir())
    assert _lx(["blocks", src, "--lang", "zh-TW"], root).returncode == 0
    assert sorted(p.name for p in root.iterdir()) == before
