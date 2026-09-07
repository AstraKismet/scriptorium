"""One name, written two ways, and the report that may not decide anything.

Two properties carry this file. The first is that the command **writes
nothing** — not the state, not the glossary, not the memory — asserted on the
bytes of every file in the project, over two runs rather than one, because a
snapshot taken between two invocations hides an idempotent write.

The second is the shape of the answer. A finding names the segments a reviewer
has to open, splits them by which rendering each carries, and lists separately
the segments whose target carries no rendering at all — a name written as a
pronoun is not evidence of drift, and that is exactly the case `checks.py`'s
glossary rule reports as an error. Nothing here decides which rendering is
right: `lx glossary set` is where a person does that, and `lx check` is what
adjudicates afterwards.

The floors are asserted too. A report that cannot say `clean` has to say so, and
the two shapes it structurally cannot see — a rival rendering used once, and a
target language written with spaces — are named in the report rather than left
for somebody to discover.
"""

import hashlib
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import renderings  # noqa: E402
from scriptorium.cli import candidate_terms  # noqa: E402
from scriptorium.translate import mentions  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")

#: Two chapters, because a name drifts *across* them and a chapter is a document
#: — a per-document sweep is blind to the case this command exists for.
CH1 = (
    b"# Chapter One\n"
    b"\n"
    b"Ashcombe had never troubled himself with the accounts. Eleanor kept them.\n"
    b"\n"
    b"\"Run,\" Ashcombe said, and Mr. Darcy did not move.\n"
    b"\n"
    b"The road to Marchmont was three hours in good weather.\n")

CH2 = (
    b"# Chapter Two\n"
    b"\n"
    b"Ashcombe said nothing at all for a long while, and then he laughed.\n"
    b"\n"
    b"Ashcombe read it twice and put it in the fire.\n"
    b"\n"
    b"\"You will not go to Marchmont,\" Ashcombe said.\n"
    b"\n"
    b"Eleanor went to Marchmont in October, and Ashcombe did not stop her.\n"
    b"\n"
    b"Mr. Darcy wrote to Ashcombe twice and had no reply.\n")

#: 灰岸 in four segments, 阿什科姆 in two, and one segment where the translator
#: wrote 他 instead of the name at all.
TARGETS = {
    "ch1.md": {
        "s0001": "第一章",
        "s0002": "灰岸從來不肯為帳目費心。帳是艾蓮諾管的。",
        "s0003": "「快跑。」灰岸說，達西先生卻沒有動。",
        "s0004": "到馬奇蒙特的路，天氣好時要三個小時。",
    },
    "ch2.md": {
        "s0001": "第二章",
        "s0002": "他很久沒有說話，然後笑了出來。",
        "s0003": "阿什科姆讀了兩遍，然後把它扔進火裡。",
        "s0004": "「你不會去馬奇蒙特。」阿什科姆說。",
        "s0005": "艾蓮諾十月去了馬奇蒙特，灰岸沒有攔她。",
        "s0006": "達西先生寫了兩封信給灰岸，都沒有回音。",
    },
}


def _lx(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def _env():
    return {**os.environ, "PYTHONPATH": SRC}


def _project(tmp_path):
    (tmp_path / "book").mkdir()
    env = _env()
    assert _lx(["init"], tmp_path, env).returncode == 0
    for name, text in (("ch1.md", CH1), ("ch2.md", CH2)):
        (tmp_path / "book" / name).write_bytes(text)
        assert _lx(["extract", f"book/{name}", "--lang", "zh-TW", "--tone",
                    "literary"], tmp_path, env).returncode == 0
        (tmp_path / (name + ".json")).write_bytes(
            json.dumps(TARGETS[name], ensure_ascii=False).encode("utf-8"))
        assert _lx(["apply", f"book/{name}", "--lang", "zh-TW", "--file",
                    name + ".json", "--origin", "human"],
                   tmp_path, env).returncode == 0
    return env


def _fingerprint(root):
    """Every file in the project, by content. The whole tree, not a chosen list."""
    out = {}
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            out[os.path.relpath(path, root)] = hashlib.sha256(
                open(path, "rb").read()).hexdigest()
    return out


def _report(tmp_path, env, *args):
    result = _lx(["renderings", "--lang", "zh-TW", "--json", *args], tmp_path, env)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout.decode("utf-8"))


def _seg(sid, masked, target):
    return {"id": sid, "kind": "paragraph", "status": "translated", "source": masked,
            "masked": masked, "target": target, "review": "", "waived": False}


def _detect(segments, glossary=()):
    terms = sorted({r["source"] for r in glossary}
                   | {r["source"] for r in candidate_terms(segments, 2)})
    return renderings.run(segments, list(glossary), terms, candidate_terms, mentions)


# --- acceptance criterion 3: it writes nothing --------------------------------


def test_renderings_writes_nothing_anywhere_in_the_project(tmp_path):
    """Acceptance criterion 3, on the bytes of every file, across two runs.

    The snapshot is taken before the **first** invocation rather than between
    two, which is `tests/test_audit.py`'s own idiom and for its reason: a
    snapshot in the middle cannot see a write that is idempotent, and a report
    that persisted its findings the way `do_check` persists issues would be
    exactly that shape.
    """
    env = _project(tmp_path)
    before = _fingerprint(tmp_path)
    for args in ([], ["--term", "Ashcombe"], ["--json"], ["--max", "0"]):
        assert _lx(["renderings", "--lang", "zh-TW", *args],
                   tmp_path, env).returncode == 0
        assert _lx(["renderings", "book/ch2.md", "--lang", "zh-TW", *args],
                   tmp_path, env).returncode == 0
    assert _fingerprint(tmp_path) == before


def test_renderings_never_moves_an_exit_code(tmp_path):
    """Findings are not an exit code, which is `lx audit`'s decision.

    Invariant 10 makes `lx check`'s exit code the evidence, and a number produced
    by a threshold is not evidence. The moment exit 1 meant "found something",
    exit 0 would mean "found nothing" — one shell script away from "clean", the
    claim this command prints its own floors to disown.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env)
    assert report["findings"]
    assert _lx(["renderings", "--lang", "zh-TW"], tmp_path, env).returncode == 0
    assert _lx(["renderings", "--lang", "zh-TW", "--term", "Nobody"],
               tmp_path, env).returncode == 0


# --- acceptance criterion 4: what a finding says ------------------------------


def test_renderings_names_the_segments_one_name_was_written_two_ways_in(tmp_path):
    """Acceptance criterion 4, across two documents, with the ids named exactly.

    A finding has to be actionable: which segments carry which rendering, in
    which chapter. `by_rendering` is that split, and the pronoun segment is not
    in it.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env)
    found = {f["term"]: f for f in report["findings"]}
    assert "Ashcombe" in found
    where = {rendering: {(r["doc"], r["seg"]) for r in refs}
             for rendering, refs in found["Ashcombe"]["by_rendering"].items()}
    assert where == {
        "灰岸": {("book/ch1.md", "s0002"), ("book/ch1.md", "s0003"),
                 ("book/ch2.md", "s0005"), ("book/ch2.md", "s0006")},
        "阿什科姆": {("book/ch2.md", "s0003"), ("book/ch2.md", "s0004")},
    }


def test_renderings_lists_a_name_written_as_a_pronoun_apart_from_the_evidence(tmp_path):
    """The case the glossary rule gets wrong, reported as what it is.

    `checks.py` calls a target that does not contain the agreed rendering an
    error, and 他 for `Ashcombe said` is a competent Chinese translation. Here it
    is `unrendered`: named beside the finding, and not counted as a rendering.
    """
    env = _project(tmp_path)
    found = {f["term"]: f for f in _report(tmp_path, env)["findings"]}
    assert [(r["doc"], r["seg"]) for r in found["Ashcombe"]["unrendered"]] == [
        ("book/ch2.md", "s0002")]
    for refs in found["Ashcombe"]["by_rendering"].values():
        assert ("book/ch2.md", "s0002") not in {(r["doc"], r["seg"]) for r in refs}


def test_renderings_carries_the_origin_and_review_of_every_segment_it_names(tmp_path):
    """`cmd_audit`'s line: they decide the remedy and cannot be re-derived.

    A `human` origin is refused to every model write, and a hold is a reviewer's
    own mark that the segment is theirs to finish — so a reader deciding what to
    do about a finding needs both beside it.
    """
    env = _project(tmp_path)
    assert _lx(["hold", "book/ch2.md", "--lang", "zh-TW", "--ids", "s0003"],
               tmp_path, env).returncode == 0
    found = {f["term"]: f for f in _report(tmp_path, env)["findings"]}
    held = [r for r in found["Ashcombe"]["ids"]
            if (r["doc"], r["seg"]) == ("book/ch2.md", "s0003")]
    assert held and held[0]["review"] == "held" and held[0]["origin"] == "human"


def test_renderings_reports_the_whole_project_when_no_document_is_named(tmp_path):
    """A name drifts across chapters, so the sweep's subject is the book.

    `lx audit` is the shape — presence or absence of `src` chooses the subject —
    and the difference worth pinning is that one document cannot see this drift:
    chapter one uses only 灰岸.
    """
    env = _project(tmp_path)
    assert _report(tmp_path, env)["documents"] == 2
    one = _report(tmp_path, env, "book/ch1.md")
    assert one["documents"] == 1
    assert [f["term"] for f in one["findings"]] == []


# --- the mode with no inference in it -----------------------------------------


def test_renderings_term_lists_every_segment_and_infers_nothing(tmp_path):
    """The lower bound, and the only path to a variant used once.

    `--term` answers over every segment whose masked source mentions the name,
    whatever the floors say — including the pronoun segment and any segment the
    sweep attributed to a longer term.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env, "--term", "Ashcombe")
    assert report["term"] == "Ashcombe"
    assert [(r["doc"], r["seg"]) for r in report["occurrences"]] == [
        ("book/ch1.md", "s0002"), ("book/ch1.md", "s0003"),
        ("book/ch2.md", "s0002"), ("book/ch2.md", "s0003"),
        ("book/ch2.md", "s0004"), ("book/ch2.md", "s0005"),
        ("book/ch2.md", "s0006")]
    assert all(r["source"] and r["target"] for r in report["occurrences"])


def test_renderings_term_answers_for_a_name_the_sweep_never_proposed(tmp_path):
    """A term nobody named is still askable, and an absent one is not a failure.

    `candidate_terms` suppresses a run every occurrence of which opens a
    sentence, and the floor drops a term named twice — so the sweep's vocabulary
    is smaller than the book's on purpose, and this mode may not inherit it.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env, "--term", "Eleanor")
    assert len(report["occurrences"]) == 2
    assert _report(tmp_path, env, "--term", "Nobody")["occurrences"] == []


# --- the floors ---------------------------------------------------------------


def test_renderings_prints_its_floors_and_never_says_clean(tmp_path):
    """A report that cannot say clean has to say what it did not look at.

    `audit.py`'s rule, arriving in the second command that needs it. The word
    `clean` appears in the human output exactly once, in the sentence denying it.
    """
    env = _project(tmp_path)
    result = _lx(["renderings", "--lang", "zh-TW"], tmp_path, env)
    text = result.stdout.decode("utf-8")
    assert text.count("clean") == 1
    assert "this report never says clean" in text
    assert "not examined" in text
    assert set(_report(tmp_path, env)) >= {"findings", "unexamined", "no_finding",
                                           "floors"}


def test_renderings_reports_a_term_it_could_not_examine_rather_than_passing_it(tmp_path):
    """A floor buried in a count is a floor a reviewer cannot act on.

    A term named twice cannot have a rendering separated from a longer one
    containing it, so it goes in its own bucket with the reason and is never
    filed as though it had been checked.
    """
    env = _project(tmp_path)
    report = _report(tmp_path, env)
    thin = {row["term"]: row for row in report["unexamined"]}
    assert "Darcy" in thin
    assert "two occurrences" in thin["Darcy"]["why"]
    assert not any(row["term"] in thin for row in report["no_finding"])


def test_renderings_does_not_split_a_name_a_longer_source_run_explains(tmp_path):
    """艾蓮諾 against 艾蓮諾·凡斯 is `Marchmont`'s shape and is not a drift.

    What separates them is on the source side: every segment carrying the longer
    rendering names a longer source run. Written as a unit test because the
    difference is a property of the rule rather than of the command around it.
    """
    segments = [
        # Two things the fixture has to arrange, and both are the case under
        # test. `Eleanor Vance` opens every sentence it is in, so
        # `candidate_terms` suppresses it and attribution never takes those two
        # segments away — which is the only path on which the explanation pass
        # is reached at all. And the short form stands *terminated* in the three
        # segments the long one does not reach, or `_boundary_support` blocks
        # the split before anything has to be explained.
        _seg("s0001", "Eleanor Vance came in the spring.", "春天來的是艾蓮諾凡斯。"),
        _seg("s0002", "Eleanor Vance had one trunk.", "帶著一只箱子的人叫艾蓮諾凡斯。"),
        _seg("s0003", "The accounts were kept by Eleanor.", "帳目歸艾蓮諾。"),
        _seg("s0004", "Nobody answered but Eleanor.", "沒有人回答，只有艾蓮諾。"),
        _seg("s0005", "At the door stood Eleanor.", "站在門口的是艾蓮諾。"),
    ]
    report = _detect(segments)
    assert [f["term"] for f in report["findings"]] == []
    assert any(row["term"] == "Eleanor" and row.get("explained_by") == "Eleanor Vance"
               for row in report["no_finding"])


def test_renderings_does_not_read_a_dialogue_tag_as_a_second_rendering(tmp_path):
    """灰岸說 is 灰岸 plus the commonest verb in a novel, not a variant of the name.

    Measured 2026-09-07: without the requirement that the short form be seen
    *terminated* somewhere the long one extends, a Chinese name followed by 的 or
    說 reaches support on any long book and splits itself in two — 184 of 200
    terms reported on a synthetic novel with 8 real drifts.
    """
    segments = [
        _seg("s0001", "Ashcombe said nothing.", "灰岸說什麼也沒說。"),
        _seg("s0002", "Ashcombe said the road was long.", "灰岸說那條路很長。"),
        _seg("s0003", "Ashcombe said it again.", "灰岸說晚餐時又講了一次。"),
        _seg("s0004", "The lamps of Ashcombe burned late.", "灰岸的燈點到很晚。"),
        _seg("s0005", "The gates of Ashcombe were shut.", "灰岸的大門關著。"),
        _seg("s0006", "Then Ashcombe understood.", "然後灰岸就明白了。"),
        _seg("s0007", "Then Ashcombe laughed.", "然後灰岸笑了出來。"),
    ]
    assert [f["term"] for f in _detect(segments)["findings"]] == []


def test_renderings_does_not_read_a_possessive_particle_as_a_second_rendering(tmp_path):
    """灰岸的 is 灰岸 plus the commonest particle in Chinese, and 的 ends phrases too.

    The rule that survived is symmetric: **both** forms have to be seen written
    at the end of a phrase, in two segments each. Asking it of the short form
    alone leaves this case wide open, and asking the long form for a single
    occurrence is not enough either — measured 2026-09-07 on five 240-segment
    synthetic books with nothing drifted, `X的` against `X` was reported for
    **all thirty names in all five** under that weaker rule, because a Chinese
    clause ends on 的 often enough to supply the one occurrence.
    """
    segments = [
        _seg("s0001", "The lamps of Ashcombe burned late.", "灰岸的燈點到很晚。"),
        _seg("s0002", "The gates of Ashcombe were shut.", "灰岸的大門關著。"),
        _seg("s0003", "The letter was addressed to Ashcombe.", "那封信是寫給灰岸的。"),
        _seg("s0004", "Nobody came from Ashcombe.", "沒有人來自灰岸。"),
        _seg("s0005", "They spoke of Ashcombe.", "他們談起灰岸。"),
        _seg("s0006", "Everyone knew Ashcombe.", "人人都知道灰岸。"),
    ]
    assert [f["term"] for f in _detect(segments)["findings"]] == []


def test_renderings_does_not_call_an_ordinary_phrase_a_rendering(tmp_path):
    """The filter that keeps ordinary words out, and the only one that can be stated.

    A gram is this term's rendering only if it appears in **no** segment whose
    source never names the term. Here 那棟房子 stands in two of Ashcombe's
    segments — the two whose target writes 他 instead of the name — and in two
    segments that never mention him. Without the filter, coverage takes 灰岸 for
    the first pair and 那棟房子 for the second, and the report says the surname is
    rendered two ways.

    It is the guard that gets stronger as the book grows, which is why the floors
    call it weakest on a short one: on a long book an ordinary phrase turns up
    somewhere the name does not, and here it had to be put there by hand.
    """
    segments = [
        _seg("s0001", "Ashcombe kept no accounts.", "灰岸不記帳。"),
        _seg("s0002", "Nobody answered Ashcombe.", "沒有人回答灰岸。"),
        _seg("s0003", "Ashcombe said nothing at all.", "他什麼也沒說。那棟房子關著門。"),
        _seg("s0004", "Ashcombe laughed and went in.", "他笑了笑。那棟房子關著門。"),
        _seg("s0005", "The rain fell all night.", "雨下了一整夜。那棟房子關著門。"),
        _seg("s0006", "The road was empty.", "路上空無一人。那棟房子關著門。"),
    ]
    assert [f["term"] for f in _detect(segments)["findings"]] == []


def test_renderings_asks_the_book_which_characters_are_grammar(tmp_path):
    """的 extends every name in the book, so it is not part of any of them.

    The per-term evidence — both forms written at the end of a phrase, twice
    each — is satisfied here, because a Chinese clause does end on 的. What
    settles it is the corpus: a character sequence that extends more than one
    term is the language's grammar rather than anybody's rendering, and no table
    of particles is written down for any language to know that.

    Measured 2026-09-07 on 1200-segment synthetic books with a 3000-word Zipf
    vocabulary: 22 to 27 false findings per 30 names without this pass, 1 to 2
    with it, and the same five or six real drifts found either way.
    """
    segments = []
    for i, (name, rendered) in enumerate((("Ashcombe", "灰岸"), ("Marchmont", "馬奇蒙"))):
        for j, (src, tgt) in enumerate((
                ("The lamps of {n} burned late.", "{r}的燈點到很晚。"),
                ("The gates of {n} were shut.", "{r}的大門關著。"),
                ("The letter was addressed to {n}.", "那封信是寫給{r}的。"),
                ("The house was left to {n}.", "那棟房子留給{r}的。"),
                ("Nobody had ever seen {n}.", "從來沒有人見過{r}。"),
                ("They spoke of {n} until dawn.", "他們談起{r}，一直談到天亮。"))):
            segments.append(_seg(f"s{i}{j:02d}", src.format(n=name),
                                 tgt.format(r=rendered)))
    assert [f["term"] for f in _detect(segments)["findings"]] == []


def test_renderings_credits_a_segment_to_the_longest_source_run_that_matches(tmp_path):
    """`Ashcombe Hall` is 灰岸莊園, and those segments are not Ashcombe's evidence.

    Without the attribution step the maximal gram in them is 灰岸莊園 and the
    report says the surname has three renderings — the trap the case set was
    built around. It is per segment rather than per occurrence, which is a floor
    the module states: a paragraph naming both runs is credited to the longer.
    """
    segments = [
        _seg("s0001", "Winter came late to Ashcombe Hall.", "灰岸莊園的冬天來得晚。"),
        _seg("s0002", "The stair at Ashcombe Hall was dark.", "灰岸莊園的樓梯很暗。"),
        _seg("s0003", "Ashcombe kept no accounts.", "灰岸不記帳。"),
        _seg("s0004", "Ashcombe said nothing.", "灰岸什麼也沒說。"),
        _seg("s0005", "Nobody answered Ashcombe.", "沒有人回答灰岸。"),
    ]
    report = _detect(segments)
    assert [f["term"] for f in report["findings"]] == []
    rows = {row["term"]: row for row in report["no_finding"] + report["unexamined"]}
    assert rows["Ashcombe"]["renderings"] == ["灰岸"]
    assert [r for r in rows["Ashcombe"]["ids"]] == ["s0003", "s0004", "s0005"]


def test_renderings_never_names_a_single_character_of_a_longer_one(tmp_path):
    """灰, 岸 and 灰岸 sit in the same targets; only the longest is a rendering.

    The coverage stage is what decides it: at equal coverage it takes the longer
    gram. A separate pruning pass did the same job and was removed on 2026-09-07
    because the mutation round could not kill it and no measurement could see it.
    """
    segments = [
        _seg("s0001", "Ashcombe kept no accounts.", "灰岸不記帳。"),
        _seg("s0002", "Ashcombe said nothing.", "灰岸什麼也沒說。"),
        _seg("s0003", "Nobody answered Ashcombe.", "沒有人回答灰岸。"),
        _seg("s0004", "Ashcombe read it twice.", "阿什科姆讀了兩遍。"),
        _seg("s0005", "Ashcombe put it in the fire.", "阿什科姆把它扔進火裡。"),
    ]
    found = {f["term"]: f for f in _detect(segments)["findings"]}
    assert sorted(found["Ashcombe"]["renderings"]) == ["灰岸", "阿什科姆"]


def test_renderings_is_the_same_report_whatever_the_hash_seed_is(tmp_path):
    """Sets and dicts are iterated here, and the answer may not depend on the order.

    A report a reviewer cannot reproduce is a report they cannot act on, and CI
    pins no hash seed on any of its four legs — so a test asserting segment ids
    would flake rather than fail.
    """
    env = _project(tmp_path)
    seen = set()
    for seed in ("1", "7", "13"):
        result = _lx(["renderings", "--lang", "zh-TW", "--json"], tmp_path,
                     {**env, "PYTHONHASHSEED": seed})
        assert result.returncode == 0
        seen.add(result.stdout)
    assert len(seen) == 1


# --- what a finding is for ----------------------------------------------------


def test_a_finding_becomes_a_glossary_row_and_then_an_error(tmp_path):
    """The whole loop, and the reason the inference is allowed to be advisory.

    The report finds; a person decides with `lx glossary set`; `checks.py`
    adjudicates mechanically from then on. Nothing between those steps is
    automatic, which is the maintainer's own stated requirement — flag, review,
    decide — and this is where it is asserted rather than described.
    """
    env = _project(tmp_path)
    found = {f["term"]: f for f in _report(tmp_path, env)["findings"]}
    losing = [r for r in found["Ashcombe"]["by_rendering"]["阿什科姆"]]
    assert _lx(["check", "book/ch2.md", "--lang", "zh-TW"],
               tmp_path, env).returncode == 0

    assert _lx(["glossary", "set", "Ashcombe", "灰岸"], tmp_path, env).returncode == 0
    after = _lx(["check", "book/ch2.md", "--lang", "zh-TW", "--json"], tmp_path, env)
    assert after.returncode == 1
    flagged = {i["seg"] for i in json.loads(after.stdout.decode("utf-8"))["issues"]
               if i["rule"] == "glossary"}
    assert {r["seg"] for r in losing} <= flagged
