"""`lx extract --from` into a document that holds work, and `lx forget`'s carry
advice: the branches of HANDOFF-066's rule no other test reached.

Each test here was written against a planted defect that the rest of the suite
let through, or against a case the review of the first version found, and is
named for the rule it pins rather than for the defect. Everything runs through
`cli.main` in-process, as `test_forget.py` does, so a module constant such as
`store.ALIGN_BUDGET` can be lowered for one test.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scriptorium.cli as cli  # noqa: E402
import scriptorium.store as store  # noqa: E402
import statedb  # noqa: E402


def _lx(capsys, *args):
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
    (root / name).write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")


def _apply(root, capsys, src, wording, origin):
    (root / "in.json").write_text(json.dumps(wording, ensure_ascii=False), encoding="utf-8")
    _ok(capsys, "apply", src, "--lang", "zh-TW", "--file", "in.json", "--origin", origin,
        "--overwrite-human")


def _segs(root, did):
    return {sid: {**json.loads(body), "target": target} for sid, target, body in statedb._query(
        root, "SELECT seg_id, target, body FROM segments WHERE doc_id=? ORDER BY pos", (did,))}


def _dnt(root, *terms):
    (root / "config" / "dnt.txt").write_text("\n".join(terms) + "\n", encoding="utf-8")


def _doctor_waiver(root, did, seg_id):
    """A waiver without the finding `lx waive` needs — the mark is under test."""
    [(body, target)] = statedb._query(
        root, "SELECT body, target FROM segments WHERE doc_id=? AND seg_id=?", (did, seg_id))
    found = json.loads(body)
    found["waived"] = store.target_token(target)
    statedb._write(root, "UPDATE segments SET body=? WHERE doc_id=? AND seg_id=?",
                   (json.dumps(found, ensure_ascii=False), did, seg_id))


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def _init(capsys):
    _ok(capsys, "init")


# --- which answer a position gets ------------------------------------------

def _grown_run(project, capsys, novel_lines, chapter_wording, dnt=None):
    """A novel of one heading and ``novel_lines`` identical lines, all translated
    by an agent; a chapter of the heading and two of them, ``chapter_wording``
    applied by an agent, then grown on disk to three."""
    _init(capsys)
    _write(project, "novel.md", ["Head."] + ["Alpha line."] * novel_lines)
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    wording = {"s0001": "頭。"}
    wording.update({f"s{i + 2:04d}": w for i, w in
                    enumerate(["一甲。", "一乙。", "一丙。"][:novel_lines])})
    _apply(project, capsys, "novel.md", wording, "agent")
    _write(project, "ch1.md", ["Head.", "Alpha line.", "Alpha line."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", chapter_wording, "agent")
    if dnt:
        _dnt(project, dnt)
    _write(project, "ch1.md", ["Head.", "Alpha line.", "Alpha line.", "Alpha line."])
    return _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")


def test_a_guess_on_both_sides_is_judged_by_the_ordinary_rule(project, capsys):
    """`guessed` means the chapter's answer is a guess *where the novel's is not*.
    Here the novel's run is the chapter's old size, so for the new member it
    guesses too — its own last copy, no better evidence than the chapter's — and
    the ordinary rule answers: an agent's wording is not a draft, so it stays and
    is named."""
    out = _grown_run(project, capsys, 2, {"s0002": "代一。", "s0003": "代二。"})
    held = _segs(project, "ch1.md")
    assert held["s0004"]["target"] == "代二。", held
    assert "novel.md holds a different one: s0002, s0003, s0004." in out, out


def test_a_member_the_chapter_never_translated_takes_the_novels_placed_wording(
        project, capsys):
    """The other half of a guess: the chapter's alignment paired this member
    with a row that held no translation, at a position it could not establish,
    and handed back its last copy. That is a guess as much as a member the diff
    paired with nothing, and the novel's placed wording answers it."""
    _grown_run(project, capsys, 3, {"s0002": "代一。"})
    held = _segs(project, "ch1.md")
    assert [held[s]["target"] for s in ("s0002", "s0003", "s0004")] == \
        ["代一。", "一乙。", "一丙。"], held


def test_what_lands_is_what_decides_whether_a_position_is_named_ambiguous(project, capsys):
    """Two answers per position, and only the one that landed says whether its
    position was established. The chapter's two members landed from a run that
    changed size; the new member landed from the novel, which placed it."""
    out = _grown_run(project, capsys, 3, {"s0002": "代一。", "s0003": "代二。"})
    held = _segs(project, "ch1.md")
    assert [held[s]["target"] for s in ("s0002", "s0003", "s0004")] == \
        ["代一。", "代二。", "一丙。"], held
    assert "which stored wording belongs to which position is not established: " \
           "s0002, s0003." in out, out


def test_when_nothing_fits_a_guess_keeps_the_novels_placed_wording(project, capsys):
    """Every candidate refused — a do-not-translate term arrived after both were
    written. The chapter held a wording at its two members, which stay; at the
    new one it held only a guess, so what stays there is the novel's placed
    wording, and the keep path names ambiguity by the entry it kept."""
    out = _grown_run(project, capsys, 3, {"s0002": "代一。", "s0003": "代二。"}, dnt="Alpha")
    held = _segs(project, "ch1.md")
    assert [held[s]["target"] for s in ("s0002", "s0003", "s0004")] == \
        ["代一。", "代二。", "一丙。"], held
    assert "which stored wording belongs to which position is not established: " \
           "s0002, s0003." in out, out


def _alpha_book(project, capsys, novel_origin="agent"):
    _init(capsys)
    _write(project, "novel.md", ["# Chapter One", "Alpha sentence."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "novel.md", {"s0001": "第一章", "s0002": "阿爾法句。"},
           novel_origin)
    _write(project, "ch1.md", ["# Chapter One", "Alpha sentence."])


def test_the_memory_answers_over_a_draft_both_carry_candidates_refuse(project, capsys):
    """The memory competes with what would *stay*. Under `took` the novel's
    wording is tried first, but what stays when it is refused is the chapter's
    draft — so the memory is asked, and a banked wording that fits replaces the
    draft, exactly as it would without `--from`."""
    _alpha_book(project, capsys)
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0002": "草稿二"}, "llm:draft")
    _dnt(project, "Alpha")
    _write(project, "other.md", ["Alpha sentence."])
    _ok(capsys, "extract", "other.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "other.md", {"s0001": "⟦1⟧句。"}, "agent")
    _ok(capsys, "commit", "other.md", "--lang", "zh-TW")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0002"]
    assert (seg["target"], seg["origin"]) == ("⟦1⟧句。", "tm"), seg
    assert "a banked wording replaced it: s0002." in out, out


def test_a_refused_wording_carried_into_an_empty_segment_is_kept(project, capsys):
    """Into a segment holding nothing, the novel's wording is all there is, so
    when the acceptance path refuses it — a do-not-translate term arrived since —
    it is kept and reported rather than dropped: divergence (24)'s rule, which
    `keep` carries for the one side that answered."""
    _alpha_book(project, capsys)
    _dnt(project, "Alpha")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0002"]
    assert (seg["target"], seg["origin"]) == ("阿爾法句。", "agent"), seg
    assert "kept a stored target whose placeholders no longer match this document: " \
           "s0002." in out, out


def test_a_draft_that_fits_where_the_novels_wording_does_not_is_a_difference(
        project, capsys):
    """`took` is only what happened when the novel's wording landed. Refused,
    the chapter's own draft is what landed, and saying the novel's replaced it
    would be false."""
    _alpha_book(project, capsys)
    _dnt(project, "Alpha")
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0002": "⟦1⟧草稿。"}, "llm:draft")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0002"]
    assert (seg["target"], seg["origin"]) == ("⟦1⟧草稿。", "llm:draft"), seg
    assert "novel.md holds a different one: s0002." in out, out
    assert "replaced it" not in out, out


def test_a_persons_wording_kept_where_both_are_refused_is_named_as_a_difference(
        project, capsys):
    """The keep path names every difference, not only a draft that could not be
    replaced: a person's wording both sides of which are refused is still one
    the novel holds differently."""
    _alpha_book(project, capsys)
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0002": "我的阿爾法句。"}, "human")
    _dnt(project, "Alpha")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0002"]
    assert (seg["target"], seg["origin"]) == ("我的阿爾法句。", "human"), seg
    assert "kept a stored target whose placeholders no longer match" in out, out
    assert "novel.md holds a different one: s0002." in out, out


# --- who wrote it ------------------------------------------------------------

def _carried_chapter(project, capsys, novel_origin="agent"):
    """`novel.md` translated by ``novel_origin``, `ch1.md` its first half, carried."""
    _alpha_book(project, capsys, novel_origin)
    return _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")


def test_a_first_carry_and_a_plain_recarry_say_only_what_happened(project, capsys):
    """Into a chapter holding nothing, nothing "stays". And a re-carry of the same
    words under the same `origin` moved no `origin`, so it names none."""
    out = _carried_chapter(project, capsys)
    assert "carried from novel.md" in out and "already held" not in out, out
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert "ch1.md kept its own wording at 2 segment(s)" in out, out
    assert "records who wrote it more strongly" not in out, out


@pytest.mark.parametrize("origin", ["agent", "editor"])
def test_only_a_machine_draft_gives_way(project, capsys, origin):
    """An agent's wording is a peer's words, and an `origin` this build does not
    know is kept rather than replaced — neither is a draft, whatever wrote the
    novel's."""
    _carried_chapter(project, capsys, novel_origin="human")
    _apply(project, capsys, "ch1.md", {"s0002": "改過的句子。"}, origin)
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0002"]
    assert (seg["target"], seg["origin"]) == ("改過的句子。", origin), seg
    assert "novel.md holds a different one: s0002." in out, out


def test_a_lifted_origin_does_not_bring_the_novels_waiver(project, capsys):
    """The same words, and the novel says a person wrote them and a reviewer
    waived them. The `origin` is about the words and comes across; the waiver is
    about a position this chapter's reviewer has not waived, and does not."""
    _init(capsys)
    _write(project, "novel.md", ["# Chapter One", "Alpha sentence."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "novel.md", {"s0001": "第一章", "s0002": "阿發句。"}, "human")
    _ok(capsys, "glossary", "set", "Alpha", "阿爾法", "--severity", "error")
    _ok(capsys, "waive", "novel.md", "--lang", "zh-TW", "--ids", "s0002")
    _write(project, "ch1.md", ["# Chapter One", "Alpha sentence."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0001": "第一章", "s0002": "阿發句。"}, "agent")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0002"]
    assert seg["origin"] == "human" and "waived" not in seg, seg
    assert "records who wrote it more strongly" in out, out
    code, _out, _err = _lx(capsys, "check", "ch1.md", "--lang", "zh-TW")
    assert code == 1


def test_the_same_words_spelled_differently_are_not_a_difference(project, capsys):
    """"The same words" is what the render writes. Carried under a normalization
    profile the novel was not translated under, the chapter stores the line with
    the spaces the carry added; a re-carry compares what the two render, and
    finds nothing to name."""
    _init(capsys)
    _ok(capsys, "config", "set", "normalize.zh-TW", '["punct"]')
    _write(project, "novel.md", ["He wrote it in Python."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW")
    _apply(project, capsys, "novel.md", {"s0001": "他用Python寫了它。"}, "agent")
    _ok(capsys, "config", "set", "normalize.zh-TW", '["punct", "pangu", "collapse_space"]')
    _write(project, "ch1.md", ["He wrote it in Python."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    stored = {s["target"] for s in statedb.segments(project)}
    assert len(stored) == 2, f"the fixture needs two spellings of one line: {stored}"
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert "holds a different one" not in out, out


def _renumbered(project, capsys):
    """A do-not-translate term added between the novel's translation and the
    chapter's carry renumbers the chapter's `⟦n⟧`: two stored strings, each under
    its own map, rendering one line."""
    _init(capsys)
    _dnt(project, "Alice")
    _write(project, "novel.md", ["Bob met Alice."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW")
    _apply(project, capsys, "novel.md", {"s0001": "Bob遇見了⟦1⟧。"}, "human")
    _dnt(project, "Alice", "Bob")
    _write(project, "ch1.md", ["Bob met Alice."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    stored = {s["target"] for s in statedb.segments(project)}
    assert len(stored) == 2, f"the fixture needs two spellings of one line: {stored}"


def _rendered(capsys, name):
    return _ok(capsys, "render", name, "--lang", "zh-TW", "-o", "-")


def test_a_renumbered_wording_recarried_is_not_a_difference(project, capsys):
    """Each side is unmasked against the map its own `⟦n⟧` mean, so the two
    stored strings compare as the one line they render."""
    _renumbered(project, capsys)
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert "holds a different one" not in out, out


def test_a_lifted_origin_keeps_the_map_the_chapters_wording_was_written_in(
        project, capsys):
    """The lift moves the `origin` and nothing else: the words are the chapter's
    and so is the map they mean, or its `⟦2⟧` would be read against the novel's
    one-slot map."""
    _renumbered(project, capsys)
    before = _rendered(capsys, "ch1.md")
    [(target,)] = statedb._query(
        project, "SELECT target FROM segments WHERE doc_id='ch1.md' AND seg_id='s0001'")
    _apply(project, capsys, "ch1.md", {"s0001": target}, "agent")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert "records who wrote it more strongly" in out, out
    seg = _segs(project, "ch1.md")["s0001"]
    assert (seg["target"], seg["origin"]) == (target, "human"), seg
    assert _rendered(capsys, "ch1.md") == before


def test_the_same_string_naming_another_term_is_named_as_a_difference(project, capsys):
    """The mirror: one stored string under two maps is two lines. The novel's
    string pasted into the chapter renders one name twice there; a re-carry must
    name the position, because that line is where a person learns of it."""
    _init(capsys)
    _dnt(project, "Al")
    _write(project, "novel.md", ["Bob met Al."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW")
    _apply(project, capsys, "novel.md", {"s0001": "Bob遇見了⟦1⟧。"}, "human")
    _dnt(project, "Al", "Bob")
    _write(project, "ch1.md", ["Bob met Al."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    _apply(project, capsys, "ch1.md", {"s0001": "Bob遇見了⟦1⟧。"}, "human")
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert "novel.md holds a different one: s0001." in out, out


# --- what the refusals and the report say ------------------------------------

def test_the_register_refusal_is_silent_where_its_remedy_keeps_the_register(
        project, capsys):
    """`--tone technical` on a chapter frozen `literary`, from a `literary` novel:
    the refusal's remedy is `--tone literary`, which leaves the chapter where it
    is, so it drops nothing and must not say it does."""
    _alpha_book(project, capsys)
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0001": "第一章"}, "human")
    code, _out, err = _lx(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from",
                          "novel.md", "--tone", "technical")
    assert code == 2, err
    assert "--tone literary" in err, err
    assert "would not be in it any more" not in err, err


def _notes(**over):
    notes = {"kept": [], "ambiguous": [], "replaced": [], "waived_source": [],
             "register": None, "carried_from": "novel.md", "carried_from_left": None,
             "carried_from_stored": None, "own_kept": 3, "differs": [], "took": [],
             "origin": []}
    notes.update(over)
    return notes


@pytest.mark.parametrize("n", [20, 21])
def test_the_difference_lines_name_every_id_and_offer_a_listing_while_pasteable(
        capsys, n):
    """Every id, the way `kept`, `replaced` and `ambiguous` name theirs: a list
    capped at eight named eight and counted the rest, and the review of the first
    version measured the claim "every place the two differ is named" false past
    it. The `lx segments --ids` listing is offered up to twenty ids and not past."""
    ids = [f"s{i:04d}" for i in range(1, n + 1)]
    cli.report_extract("ch1.md", "zh-TW", _notes(differs=ids, took=ids, origin=ids))
    out = capsys.readouterr().out
    every = ", ".join(ids)
    assert f"novel.md holds a different one: {every}." in out, out
    assert f"novel.md's wording replaced it: {every}." in out, out
    assert f"either over a machine: {every}." in out, out
    assert "more." not in out, out
    listing = f"`lx segments ch1.md --lang zh-TW --ids {','.join(ids)}` for these"
    bare = "`lx segments ch1.md --lang zh-TW` for these"
    assert (listing in out, bare in out) == ((True, False) if n <= 20 else (False, True)), out


# --- lx forget's carry advice ---------------------------------------------------

def _two(project, capsys, novel_origin="human", novel_tone=None):
    _init(capsys)
    _write(project, "novel.md", ["First.", "Second."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW",
        *(("--tone", novel_tone) if novel_tone else ()))
    _apply(project, capsys, "novel.md", {"s0001": "第一。", "s0002": "第二。"}, novel_origin)


@pytest.mark.parametrize("mark", ["held", "waived"])
def test_a_marked_draft_across_registers_is_not_a_draft_a_carry_may_replace(
        project, capsys, mark):
    """Across a register line nothing the chapter holds aligns, so a carry puts
    the novel's wording over all of it. A draft somebody held or waived is not
    what a carry is for, and losing its mark makes the carry unsafe."""
    _two(project, capsys, novel_tone="literary")
    _write(project, "ch1.md", ["First.", "Second."])
    (project / "novel.md").unlink()
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _apply(project, capsys, "ch1.md", {"s0001": "草一。"}, "llm:draft")
    if mark == "held":
        _ok(capsys, "hold", "ch1.md", "--lang", "zh-TW", "--ids", "s0001")
    else:
        _doctor_waiver(project, "ch1.md", "s0001")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "do not carry into ch1.md" in err and "(s0001)" in err, err
    assert "lx extract ch1.md" not in err, err


@pytest.mark.parametrize("novel_origin,chapter_origin", [
    ("agent", "human"),      # a person's mark would become an agent's
    ("llm:draft", "agent"),  # an agent's words would become a machine's
])
def test_a_carry_across_registers_that_would_weaken_an_origin_is_not_offered(
        project, capsys, novel_origin, chapter_origin):
    _two(project, capsys, novel_origin=novel_origin, novel_tone="literary")
    _write(project, "ch1.md", ["First.", "Second."])
    (project / "novel.md").unlink()
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _apply(project, capsys, "ch1.md", {"s0001": "第一。"}, chapter_origin)
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "do not carry into ch1.md" in err and "(s0001)" in err, err
    assert "lx extract ch1.md" not in err, err


def test_a_carry_across_registers_that_would_empty_a_paragraph_is_not_offered(
        project, capsys):
    """A paragraph the novel does not have answers from neither side under the
    `--tone` such a carry needs, so the chapter's wording of it would go."""
    _two(project, capsys, novel_tone="literary")
    _write(project, "ch1.md", ["First.", "Second.", "Extra."])
    (project / "novel.md").unlink()
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _apply(project, capsys, "ch1.md", {"s0003": "額外。"}, "human")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "Traceback" not in err
    assert "do not carry into ch1.md" in err and "(s0003)" in err, err


def test_a_carry_that_lifts_an_agent_copy_to_a_persons_is_offered(project, capsys):
    """The chapter holds the person's words under an agent's `origin` — the
    default of `lx apply`. The forget refuses on provenance, and a carry answers
    exactly that: the same words, the stronger `origin` lifted. Run as printed,
    the forget then passes."""
    _two(project, capsys)
    _write(project, "ch1.md", ["First.", "Second."])
    (project / "novel.md").unlink()
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _apply(project, capsys, "ch1.md", {"s0001": "第一。", "s0002": "第二。"}, "agent")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    command = "lx extract ch1.md --lang zh-TW --from novel.md"
    assert f"`{command}`" in err, err
    _ok(capsys, *command.split()[1:])
    assert {s: b["origin"] for s, b in _segs(project, "ch1.md").items()} == \
        {"s0001": "human", "s0002": "human"}
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")


def test_a_carry_is_offered_for_a_wording_that_carries_a_placeholder(project, capsys):
    """What the carry would leave is compared as what the render writes — the
    novel's `⟦1⟧` unmasked — or a wording with a protected term in it never
    matches the copy it would put there."""
    _init(capsys)
    _dnt(project, "Alice")
    _write(project, "novel.md", ["Bob met Alice."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW")
    _apply(project, capsys, "novel.md", {"s0001": "Bob遇見了⟦1⟧。"}, "human")
    _write(project, "ch1.md", ["Bob met Alice."])
    (project / "novel.md").unlink()
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert "`lx extract ch1.md --lang zh-TW --from novel.md`" in err, err


def test_an_offered_carry_names_the_drafts_it_replaces_and_does_what_it_says(
        project, capsys):
    _two(project, capsys)
    _write(project, "ch1.md", ["First.", "Second."])
    (project / "novel.md").unlink()
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _apply(project, capsys, "ch1.md", {"s0001": "草一。"}, "llm:draft")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    command = "lx extract ch1.md --lang zh-TW --from novel.md"
    assert (f"replaces only the machine draft(s) s0001 with novel.md's wording where it "
            f"fits: `{command}`") in err, err
    _ok(capsys, *command.split()[1:])
    seg = _segs(project, "ch1.md")["s0001"]
    assert (seg["target"], seg["origin"]) == ("第一。", "human"), seg
    _ok(capsys, "forget", "novel.md", "--lang", "zh-TW")


# --- what the review of the first version found ------------------------------
#
# Each of these failed on the commit the four review lanes read (8deaeff).

#: A novel whose first chapter a person will translate out of order: Alpha moves
#: from the front of the chapter to the back, where the novel has it.
_NOVEL_M = ["# One", "Beta sentence.", "Gamma sentence.", "Alpha sentence.",
            "# Two", "Delta sentence."]
_NOVEL_M_T = {"s0001": "第一章", "s0002": "貝塔句。", "s0003": "伽瑪句。", "s0004": "阿爾法句。",
              "s0005": "第二章", "s0006": "德爾塔句。"}


@pytest.mark.parametrize("carried_first", [False, True])
def test_a_paragraph_that_moved_keeps_the_persons_wording(project, capsys, carried_first):
    """A paragraph that moved is not a guess. The chapter held Alpha first and a
    person wrote it; the file was then put in the novel's order, so the chapter's
    own diff pairs Alpha's new position with nothing and the key fallback hands
    back Alpha's own and only wording. The first version called that a guess and
    put the novel's wording over the person's, naming nothing — while the plain
    extract keeps it. Named here as a difference, as it should be."""
    _init(capsys)
    _write(project, "novel.md", _NOVEL_M)
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "novel.md", _NOVEL_M_T, "agent")
    _write(project, "ch1.md", ["# One", "Alpha sentence.", "Beta sentence.", "Gamma sentence."])
    if carried_first:
        _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    else:
        _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0002": "我改的阿爾法句。"}, "human")
    _write(project, "ch1.md", ["# One", "Beta sentence.", "Gamma sentence.", "Alpha sentence."])
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    seg = _segs(project, "ch1.md")["s0004"]
    assert (seg["target"], seg["origin"]) == ("我改的阿爾法句。", "human"), seg
    assert "novel.md holds a different one" in out and "s0004" in out, out


def test_past_the_alignment_budget_nothing_the_target_holds_is_a_guess(
        project, capsys, monkeypatch):
    """Over `ALIGN_BUDGET` the diff makes no pair at all and every position falls
    to the key fallback. The first version called each of those a guess, so an
    idempotent re-carry of a target larger than its source put the source's
    wording over every person's sentence in it, naming none.

    The budget has to sit between the two products — `len(prior keys) ×
    commonest fresh key` — so that only the target's own alignment is over it:
    with both over, both answers are guesses and the ordinary rule answers, which
    is what the first spelling of this test measured, and it passed on the
    defective build."""
    _init(capsys)
    head = ["# One", "Alpha sentence.", "Beta sentence."]
    _write(project, "part.md", head)
    _ok(capsys, "extract", "part.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "part.md",
           {"s0001": "第一章", "s0002": "阿爾法句。", "s0003": "貝塔句。"}, "agent")
    _write(project, "whole.md", head + [f"Extra sentence {n}." for n in range(7)])
    _ok(capsys, "extract", "whole.md", "--lang", "zh-TW", "--from", "part.md")
    _apply(project, capsys, "whole.md", {"s0003": "人改的貝塔句。"}, "human")
    # The target's alignment is 10 × 1 and the source's 3 × 1.
    monkeypatch.setattr(store, "ALIGN_BUDGET", 5)
    out = _ok(capsys, "extract", "whole.md", "--lang", "zh-TW", "--from", "part.md")
    seg = _segs(project, "whole.md")["s0003"]
    assert (seg["target"], seg["origin"]) == ("人改的貝塔句。", "human"), seg
    assert "part.md holds a different one" in out and "s0003" in out, out


def test_a_held_draft_the_alignment_could_not_place_is_not_given_away(project, capsys):
    """A run of identical lines changed size, so the chapter's own alignment could
    not place its members and dropped the hold, as a plain extract does. The first
    version then read the draft as one nobody held and replaced it with the
    novel's wording, under a line saying a held draft never is."""
    _init(capsys)
    _write(project, "novel.md", ["# One", "Open.", "Yes.", "Yes.", "Close."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "novel.md", {"s0001": "一", "s0002": "開場。", "s0003": "是甲。",
                                         "s0004": "是乙。", "s0005": "收尾。"}, "agent")
    _write(project, "ch1.md", ["# One", "Open.", "Yes.", "Yes.", "Yes.", "Close."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    _apply(project, capsys, "ch1.md",
           {"s0003": "草稿甲", "s0004": "草稿乙", "s0005": "草稿丙"}, "llm:draft")
    _ok(capsys, "hold", "ch1.md", "--lang", "zh-TW", "--ids", "s0004")
    _write(project, "ch1.md", ["# One", "Open.", "Yes.", "Yes.", "Close."])
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert _segs(project, "ch1.md")["s0004"]["target"] == "草稿乙"
    took = [line for line in out.splitlines() if "wording replaced it:" in line]
    assert not any("s0004" in line for line in took), out


@pytest.mark.parametrize("raw", ["[1]", '{"origin": "hum'])
def test_a_broken_body_in_the_target_is_read_not_raised_on(project, capsys, raw):
    """A body that is not a JSON object claims no origin, no hold and no waiver —
    the way `lx forget` already reads one. `lx forget` offered this carry and the
    carry ended in a traceback, since `--from` began reading the target's rows."""
    _alpha_book(project, capsys)
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "ch1.md", {"s0001": "我的第一章"}, "human")
    statedb._write(project, "UPDATE segments SET body=? WHERE doc_id='ch1.md' "
                            "AND seg_id='s0001'", (raw,))
    code, _out, err = _lx(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert code == 0 and "Traceback" not in err, err


def test_a_chapter_frozen_with_an_empty_register_is_refused_rather_than_moved(
        project, capsys):
    """`lx config set tone ""` is accepted, and an extract then freezes `""` —
    the default register to `canonical_tone` and to the memory key, but "no
    register" to a truthiness test. `--from` resolved past it to the novel's,
    so nothing was refused, the chapter's own keys missed, and a person's held
    wording was replaced under a line saying it stayed. Found by the mutation
    pass: a mutant that dropped the truthiness test was the correct code."""
    _init(capsys)
    _ok(capsys, "config", "set", "tone", "")
    _write(project, "novel.md", ["# Chapter One", "Alpha sentence."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "novel.md", {"s0001": "第一章", "s0002": "阿爾法句。"}, "agent")
    _write(project, "ch1.md", ["# Chapter One", "Alpha sentence."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    _apply(project, capsys, "ch1.md", {"s0002": "我改寫的阿爾法句。"}, "human")
    _ok(capsys, "hold", "ch1.md", "--lang", "zh-TW", "--ids", "s0002")
    code, _out, err = _lx(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert code == 2, err
    assert "--tone literary" in err and "none of the 1 it holds would carry over" in err, err
    seg = _segs(project, "ch1.md")["s0002"]
    assert (seg["target"], seg.get("review")) == ("我改寫的阿爾法句。", "held"), seg
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md",
              "--tone", "literary")
    assert "the register moved from technical to literary" in out, out
    assert "the 1 this document held" in out and "kept its own wording" not in out, out


def test_the_write_lock_is_never_held_for_the_carry_advice(project, capsys, monkeypatch):
    """Behaviour cannot see this one, so it is pinned by what the calls look like.
    The advice runs the carry's rule over every document sharing a paragraph —
    four times the old wall time with a hundred chapters, measured by the review
    — and under `BEGIN IMMEDIATE` every other writer waits on `BUSY_TIMEOUT`.
    So the delete asks without it inside the transaction, a refusal reads it
    outside, and `lx extract --from`'s note, which needs only a count, never asks."""
    calls = []
    real = store._forget_analysis

    def spy(conn, *args, **kwargs):
        calls.append((kwargs.get("advise", True), conn.in_transaction))
        return real(conn, *args, **kwargs)

    monkeypatch.setattr(store, "_forget_analysis", spy)
    _two(project, capsys)
    _write(project, "ch1.md", ["First.", "Second."])
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW")
    assert calls == [], "a plain extract asks nothing"
    _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert calls == [(False, False)], calls
    calls.clear()
    _apply(project, capsys, "ch1.md", {"s0001": "改過的第一。"}, "human")
    code, _out, err = _lx(capsys, "forget", "novel.md", "--lang", "zh-TW")
    assert code == 2, err
    assert calls == [(False, True), (True, False)], calls


def test_into_an_empty_segment_the_carry_names_what_it_always_named(project, capsys):
    """Out of the package's scope, and kept that way: into a segment the target
    holds nothing for, an unplaced carried entry is named `ambiguous` whatever
    lands, even the memory — which the first version stopped doing."""
    _init(capsys)
    _write(project, "novel.md", ["# One", "Open.", "Alpha says yes.", "Alpha says yes.",
                                 "Close."])
    _ok(capsys, "extract", "novel.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "novel.md", {"s0001": "一", "s0002": "開場。", "s0003": "阿爾法說是甲。",
                                         "s0004": "阿爾法說是乙。", "s0005": "收尾。"},
           "llm:draft")
    _dnt(project, "Alpha")
    _write(project, "other.md", ["# Other", "Alpha says yes."])
    _ok(capsys, "extract", "other.md", "--lang", "zh-TW", "--tone", "literary")
    _apply(project, capsys, "other.md", {"s0001": "別", "s0002": "⟦1⟧說是（記憶）。"}, "agent")
    _ok(capsys, "commit", "other.md", "--lang", "zh-TW")
    _write(project, "ch1.md", ["# One", "Open.", "Alpha says yes.", "Alpha says yes.",
                               "Alpha says yes.", "Close."])
    out = _ok(capsys, "extract", "ch1.md", "--lang", "zh-TW", "--from", "novel.md")
    assert "which stored wording belongs to which position is not established" in out, out
