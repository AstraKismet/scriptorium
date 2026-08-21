"""Where one sentence ends, and the promise that makes it usable from a browser.

Two properties carry everything else. The partition is **exact** — the answer
concatenates back to the input, so a client walks a string with a cursor instead
of searching it, which is what makes two byte-identical sentences in one
paragraph locatable at all. And a `⟦n⟧` run is an **atom** — no boundary inside
one or between two adjacent members.

The battery below is the design panel's, merged and re-scored. Several rows are
here because a proposal got them wrong and did not admit it: `He didn't. She
did.` merged under two of three designs, because the token in front of the stop
was read with `str.isalnum`, which halts at the apostrophe and leaves `t` — a lone
letter. That is the case this file exists to keep fixed.
"""

import json
import os
import random
import re
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import sentences  # noqa: E402
from scriptorium.cli import (  # noqa: E402
    UnusableTarget,
    do_apply,
    do_extract,
    do_sentences,
)
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.mask import PH_RE  # noqa: E402
from scriptorium.store import load_doc  # noqa: E402

CFG = dict(DEFAULT_CONFIG)
ABBR = CFG["terms"]["abbreviations"]

CORPUS = os.path.join(os.path.dirname(__file__), "corpus")

#: `(input, expected sentences)`. Written out rather than counted, because a
#: count passes for the wrong reasons — a rule that split one character early
#: would still return two.
BATTERY = [
    ("One short sentence.", ["One short sentence."]),
    ("He left. She stayed.", ["He left. ", "She stayed."]),
    # The abbreviation list, and the two shapes that need no list at all.
    ("Dr. Adler went home. It was late.",
     ["Dr. Adler went home. ", "It was late."]),
    ("See example.com for more. Then stop.",
     ["See example.com for more. ", "Then stop."]),
    ("It cost 3.14 dollars. Really.", ["It cost 3.14 dollars. ", "Really."]),
    ("The U.S. Army moved. Then rested.",
     ["The U.S. Army moved. ", "Then rested."]),
    ("A letter J. R. R. Tolkien wrote it. Yes.",
     ["A letter J. R. R. Tolkien wrote it. ", "Yes."]),
    # The contraction. Two of three panel designs merged this and neither said so.
    ("He didn't. She did.", ["He didn't. ", "She did."]),
    ("He wouldn't. She couldn't. They can't.",
     ["He wouldn't. ", "She couldn't. ", "They can't."]),
    # Traditional Chinese, which is what a reviewer actually reads here.
    ("他走了。她留下。", ["他走了。", "她留下。"]),
    ("「不要走。」他轉身離開。", ["「不要走。」", "他轉身離開。"]),
    ("「快跑！」她沒有回頭。", ["「快跑！」", "她沒有回頭。"]),
    # A mark that cannot begin a line in Chinese typesetting keeps the sentence
    # open. Without this the reading view highlights a sentence starting at a
    # full-width comma, which is visibly wrong on a routine construction.
    ("他嘴裡念著「快跑！」，腳下卻沒動。", ["他嘴裡念著「快跑！」，腳下卻沒動。"]),
    ("標題是「你好嗎？」，副標題卻是空的。", ["標題是「你好嗎？」，副標題卻是空的。"]),
    # The ellipsis is weak: it ends a sentence only before an opening mark, a
    # capital, or the end of the text.
    ("一、二、三……十。完了。", ["一、二、三……十。", "完了。"]),
    ("「我不知道……」她輕聲說。", ["「我不知道……」她輕聲說。"]),
    ("她低聲說：「別怕……」然後熄了燈。", ["她低聲說：「別怕……」然後熄了燈。"]),
    # Emphasis reaches a segment unmasked, so it is a closing mark here. The
    # second row is the one that discriminates: with `*` out of the closing class
    # the stop is followed by a non-space and stops being a terminator at all, so
    # the whole paragraph merges. Measured — the first row alone survives that
    # mutation, which is how it was found.
    ("He left. *She never returned.*", ["He left. ", "*She never returned.*"]),
    ("*She left.* He stayed.", ["*She left.* ", "He stayed."]),
    ("~~Struck out.~~ Then plain.", ["~~Struck out.~~ ", "Then plain."]),
    # The five symmetric marks open exactly as often as they close, and until
    # 2026-08-21 the closing loop took them without asking. English escaped by
    # accident — a space follows the stop, so the loop never started — and
    # Traditional Chinese writes no space after `。`, so it always bit: every
    # piece came back carrying a stray delimiter and the emphasis pair broken
    # across the boundary. The English rows above and below are the control that
    # stops the repair over-correcting.
    ("He left. **She stayed.**", ["He left. ", "**She stayed.**"]),
    ("他走了。**她留下。**", ["他走了。", "**她留下。**"]),
    ("他走了。*她留下。*", ["他走了。", "*她留下。*"]),
    ("他走了。~~她留下。~~", ["他走了。", "~~她留下。~~"]),
    ("他走了。\"她留下。\"", ["他走了。", "\"她留下。\""]),
    ("他走了。'她留下。'", ["他走了。", "'她留下。'"]),
    ("_Emphasis._ Next.", ["_Emphasis._ ", "Next."]),
    # The run is taken whole or not at all. Per character the first row ends
    # after one asterisk of the pair; the second is the run against the end of
    # the text, where there is nothing after it to ask about.
    ("**Bold.** Next.", ["**Bold.** ", "Next."]),
    ("他走了。**", ["他走了。**"]),
    # A directional closing mark is not ambiguous and keeps absorbing with no
    # test: `”` has `“` for an opening twin, so finding one after a full stop
    # settles what it is doing there. This is the row that says the repair
    # narrowed the unconditional class rather than emptying it.
    ("“他走了。”她留下。", ["“他走了。”", "她留下。"]),
    ("He left. ’Tis done.", ["He left. ", "’Tis done."]),
    ("He counted them. 3 remained.", ["He counted them. ", "3 remained."]),
    # Placeholders.
    ("He left.⟦3⟧ She stayed.", ["He left.⟦3⟧ ", "She stayed."]),
    ("He said ⟦1⟧hello⟦2⟧. Then left.", ["He said ⟦1⟧hello⟦2⟧. ", "Then left."]),
    ("⟦1⟧⟦2⟧ only placeholders.", ["⟦1⟧⟦2⟧ only placeholders."]),
    ("Ends with a run.⟦1⟧⟦2⟧", ["Ends with a run.⟦1⟧⟦2⟧"]),
    # Degenerate input.
    ("", []),
    ("   ", ["   "]),
    ("no terminator at all", ["no terminator at all"]),
    # A wrapped block's interior newline is ordinary whitespace, and a lower-case
    # continuation after it keeps the sentence open — which is the documented
    # failure, asserted so it stays the documented one.
    ("line one ends.\nline two starts.", ["line one ends.\nline two starts."]),
    ("Line one ends.\nLine two starts.", ["Line one ends.\n", "Line two starts."]),
    ("😀 emoji then. Non-BMP 𠮷 name。下一句。",
     ["😀 emoji then. ", "Non-BMP 𠮷 name。", "下一句。"]),
]


@pytest.mark.parametrize("text,want", BATTERY, ids=range(len(BATTERY)))
def test_the_battery(text, want):
    assert sentences.split(text, ABBR) == want


@pytest.mark.parametrize("text,_want", BATTERY, ids=range(len(BATTERY)))
def test_the_partition_is_exact(text, _want):
    assert "".join(sentences.split(text, ABBR)) == text


@pytest.mark.parametrize("text,_want", BATTERY, ids=range(len(BATTERY)))
def test_no_element_is_empty(text, _want):
    assert all(sentences.split(text, ABBR))


def test_chinese_dialogue_attribution_over_splits_and_says_so():
    """An admitted failure, asserted so it stays the admitted one.

    `return None if nxt.islower() else stop` is the only continuation test after
    a strong run, and `str.islower` is `False` for every Chinese character — so
    English is protected by it and Chinese is not, on the same construction.
    Telling an attribution verb from an ordinary one needs a verb table, which is
    judgement and therefore invariant 4's line, so this is written into
    `KNOWN_FAILURES` and the contract rather than repaired.

    The comma form is already right and is in the battery above, which is what
    makes this a gap in one construction rather than in the rule.
    """
    assert sentences.split('"Stop!" he shouted. Nobody moved.', ABBR) == \
        ['"Stop!" he shouted. ', "Nobody moved."]
    for text, admitted in [
        ("「站住！」他喊。沒有人停下。", ["「站住！」", "他喊。", "沒有人停下。"]),
        ("「你回來了。」她說。門在她身後關上。",
         ["「你回來了。」", "她說。", "門在她身後關上。"]),
        ("「你去哪裡了？」他問。她沒有回答。",
         ["「你去哪裡了？」", "他問。", "她沒有回答。"]),
    ]:
        assert sentences.split(text, ABBR) == admitted
    assert any("attribution" in f for f in sentences.KNOWN_FAILURES), \
        "the failure is admitted in the module as well as here"


def test_only_the_empty_string_answers_with_nothing():
    assert sentences.split("", ABBR) == []
    assert sentences.split("\n", ABBR) == ["\n"]


def test_splitting_is_idempotent_over_its_own_output():
    """A sentence, split again, is itself. Otherwise the boundary moved."""
    for text, _ in BATTERY:
        for piece in sentences.split(text, ABBR):
            assert sentences.split(piece, ABBR) == [piece]


# ── the cost of the rule ───────────────────────────────────────────────────

#: One paragraph's worth of prose, repeated to whatever length a test asks for.
#: Full stops are what cost — `_stop_is_terminal` runs for a lone ASCII stop and
#: for nothing else — so the text is English rather than Chinese on purpose.
_PROSE_UNIT = "He walked home. She waited by the door. Nobody spoke of it again. "


def _prose(kb):
    return (_PROSE_UNIT * ((kb * 1024) // len(_PROSE_UNIT) + 1))[: kb * 1024]


#: The ids are named rather than derived, because a parametrization over 64 KB
#: strings puts all 64 KB of each into the test's name.
@pytest.mark.parametrize("text", [
    _prose(64),
    # The shape the leading guard in `_word_before` exists for: one enormous run
    # of letters and in-word marks ending in a mark rather than a letter, so the
    # pattern cannot match and every start position in the window would be tried
    # without it. Not prose — but `POST /api/sentences` takes what it is sent.
    "a-" * 32000 + ". Next.",
    "a" * 65536 + ". Next.",
], ids=["64kb-of-prose", "one-run-of-in-word-marks", "one-run-of-letters"])
def test_splitting_is_linear_in_the_length_of_the_input(text):
    """A wall clock, because the defect this replaced was a wall-clock defect.

    `_WORD_RE.search(text[:at])` copied the whole prefix at every full stop, and
    a chapter-sized segment is ordinary input here: measured 2026-08-21 at 2 KB
    0.013 s, 8 KB 0.200 s, 32 KB 3.198 s and 64 KB 12.387 s — four times the
    input for sixteen times the time. The same 64 KB takes 0.025 s now.

    The bound is two orders of magnitude of headroom over that and still fails
    the quadratic spelling by a factor of two, which is the trade a timing test
    has to make: tight enough to catch the regression, loose enough that a
    loaded machine does not turn it red. It asserts the answer as well as the
    clock, so a rule that got fast by getting wrong does not pass it.
    """
    start = time.perf_counter()
    pieces = sentences.split(text, ABBR)
    elapsed = time.perf_counter() - start
    assert "".join(pieces) == text
    assert elapsed < 5.0, f"{len(text)} characters took {elapsed:.3f}s"


def test_the_bounded_window_reads_the_same_word_the_pattern_does():
    """`_word_before` is `_WORD_RE.search(text[:at])`, and this is the proof.

    The spelling it replaced is inlined here rather than described, because the
    repair was a performance repair and a performance repair that quietly moves
    a boundary is worse than the cost it removed. The alphabet carries what the
    two disagree about if the walk and the pattern part company: an in-word mark
    at either end of the run, a digit and an underscore (`_LETTER` excludes both
    while `\\w` does not), a Roman numeral and a fraction (`Nl` and `No`, which
    `str.isalpha` excludes and `[^\\W\\d_]` admits), a combining mark, and an
    astral ideograph.
    """
    def with_a_prefix_copy(text, at):
        found = sentences._WORD_RE.search(text[:at])
        return found.group(0) if found else None

    alphabet = list("abzAZ'’- .0369_中〇Ⅷ½₁́") + ["𠮷", "😀", "　", "\n", "é"]
    random.seed(20260821)
    for _ in range(4000):
        text = "".join(random.choice(alphabet) for _ in range(random.randint(0, 14)))
        for at in range(len(text) + 1):
            assert sentences._word_before(text, at) == with_a_prefix_copy(text, at), \
                f"{text!r} at {at}"


# ── placeholders are atoms ─────────────────────────────────────────────────

def test_a_placeholder_run_is_never_split(tmp_path, monkeypatch):
    """The acceptance criterion, on a *parsed* segment rather than a literal.

    `tests/corpus/html-block.md` is the one fixture whose masked source holds
    adjacent placeholders — measured — which is what makes this a test of the
    pipeline rather than of a regular expression.
    """
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    with open(os.path.join(CORPUS, "html-block.md"), "rb") as f:
        (root / "html-block.md").write_bytes(f.read())
    do_extract("html-block.md", "zh-TW", CFG)
    doc = load_doc("html-block.md", "zh-TW")

    adjacent = [s for s in doc["segments"]
                if re.search(r"⟧⟦", s["masked"])]
    assert adjacent, "no segment holds two adjacent placeholders; this proves nothing"
    for seg in adjacent:
        pieces = sentences.split(seg["masked"], ABBR)
        assert "".join(pieces) == seg["masked"]
        # Every id survives, in order, and no piece holds a half-placeholder.
        assert [i for p in pieces for i in PH_RE.findall(p)] == \
            PH_RE.findall(seg["masked"])
        for piece in pieces:
            assert piece.count("⟦") == piece.count("⟧")


@pytest.mark.parametrize("text", [
    "⟦1⟧⟦2⟧", "a⟦1⟧⟦2⟧b", "⟦1⟧. ⟦2⟧", "Go.⟦1⟧⟦2⟧⟦3⟧ Then stop.",
    "先看⟦1⟧⟦2⟧。再看⟦3⟧。",
])
def test_a_run_survives_whole_wherever_it_sits(text):
    pieces = sentences.split(text, ABBR)
    assert "".join(pieces) == text
    assert [i for p in pieces for i in PH_RE.findall(p)] == PH_RE.findall(text)


# ── the partition holds over every segment the corpus produces ─────────────

def test_the_partition_holds_over_every_corpus_segment(tmp_path, monkeypatch):
    """A property sweep, because the battery is only the shapes somebody thought of.

    Every masked segment of every Markdown fixture. It asserts the partition and
    the placeholder ids, which are the two promises a client depends on; it does
    not assert *where* the boundaries fall, because that is what the battery is
    for and a corpus cannot say.
    """
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    seen = 0
    for name in sorted(os.listdir(CORPUS)):
        with open(os.path.join(CORPUS, name), "rb") as f:
            (root / name).write_bytes(f.read())
        do_extract(name, "zh-TW", CFG)
        for seg in load_doc(name, "zh-TW")["segments"]:
            pieces = sentences.split(seg["masked"], ABBR)
            assert "".join(pieces) == seg["masked"], f"{name} {seg['id']}"
            assert [i for p in pieces for i in PH_RE.findall(p)] == \
                PH_RE.findall(seg["masked"]), f"{name} {seg['id']}"
            seen += 1
    assert seen > 100, f"only {seen} segments swept; the corpus did not load"


# ── the seam ───────────────────────────────────────────────────────────────

def test_do_sentences_answers_by_index():
    assert do_sentences(["A. B.", "", "他走了。她留下。"], CFG) == [
        ["A. B."], [], ["他走了。", "她留下。"]]


def test_do_sentences_reads_the_abbreviation_list_the_terms_command_reads():
    """One list, two commands. A project that extends it changes both answers."""
    cfg = json.loads(json.dumps(CFG))
    assert do_sentences(["He met Dr. Adler there. Later."], cfg)[0] == \
        ["He met Dr. Adler there. ", "Later."]
    cfg["terms"]["abbreviations"] = []
    assert do_sentences(["He met Dr. Adler there. Later."], cfg)[0] == \
        ["He met Dr. ", "Adler there. ", "Later."]


def test_an_abbreviation_carrying_an_apostrophe_is_recognized_as_one_token():
    """What `_WORD_RE` is actually for, pinned so the guard is load-bearing.

    Walking back over `str.isalnum` stops at the apostrophe and offers `l`, which
    matches no entry in any list — so the abbreviation is not seen and the
    sentence splits inside a name. A mutation pass on 2026-08-21 found the earlier
    battery could not tell the two spellings apart, and the pattern itself was
    silently excluding the characters its own docstring said it included.

    A contraction is deliberately *not* the case that proves this: `He didn't.`
    is protected by the initials rule requiring an upper-case letter, whichever
    spelling found the token.
    """
    assert sentences.split("The Int'l. Ltd bought it.", ["Int'l"]) == \
        ["The Int'l. Ltd bought it."]
    assert sentences.split("The Int'l. Ltd bought it.", []) == \
        ["The Int'l. ", "Ltd bought it."]
    assert sentences.split("He lives on Rue-St. Marie is next.", ["Rue-St"]) == \
        ["He lives on Rue-St. Marie is next."]


@pytest.mark.parametrize("payload", [None, "a string", {"a": 1}, 3])
def test_a_texts_that_is_not_an_array_is_refused(payload):
    with pytest.raises(UnusableTarget) as e:
        do_sentences(payload, CFG)
    assert "texts" in str(e.value)


def test_an_element_that_is_not_a_string_is_refused_by_index():
    with pytest.raises(UnusableTarget) as e:
        do_sentences(["fine", 7], CFG)
    assert "texts[1]" in str(e.value)


def test_a_refusal_never_repeats_what_it_refused():
    """A reviewer's editor buffer is what lands here, so it is not echoed back.

    The rule invariant 6 holds for a credential, applied to a field with no
    reason to make an exception of itself.
    """
    secret = "sk-live-0123456789abcdef"
    with pytest.raises(UnusableTarget) as e:
        do_sentences([secret.encode()], CFG)
    assert secret not in str(e.value) and "sk-live" not in str(e.value)


# ── the CLI ────────────────────────────────────────────────────────────────

def _lx(args, cwd):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-m", "scriptorium", *args],
                          cwd=str(cwd), env=env, capture_output=True)


def test_lx_sentences_answers_over_a_document(tmp_path, monkeypatch):
    """Invariant 8: the rule exists so `lx`, an agent and CI can see it.

    A rule reachable only from the browser is the second rule this module was
    written to prevent, so the command is asserted rather than assumed.
    """
    root = tmp_path / "nest" / "proj"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    (root / "book.md").write_bytes(b"# Title\n\nOne short sentence. And another.\n")
    do_extract("book.md", "zh-TW", CFG)
    doc = load_doc("book.md", "zh-TW")
    para = [s for s in doc["segments"] if s["kind"] == "para"][0]["id"]
    do_apply("book.md", "zh-TW", CFG, {para: "他走了。她留下。"}, origin="human")

    run = _lx(["sentences", "book.md", "--lang", "zh-TW", "--ids", para, "--json"], root)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    got = json.loads(run.stdout.decode("utf-8"))
    assert got["segments"] == [{"id": para, "sentences": ["他走了。", "她留下。"]}]

    run = _lx(["sentences", "book.md", "--lang", "zh-TW", "--ids", para,
               "--source", "--json"], root)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    got = json.loads(run.stdout.decode("utf-8"))
    assert got["segments"][0]["sentences"] == ["One short sentence. ", "And another."]
