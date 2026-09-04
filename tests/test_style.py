"""The project style sheet: this book's own voice, and who gets told about it.

The register brief HANDOFF-013 built says a character keeps their own diction
and level of formality wherever they speak. It cannot say *which* character says
您 and which says 你, because that is a fact about one book rather than about
Traditional Chinese. This is where a project says it.

Two halves, and the split is the design:

* the **preamble** — everything before the first ``[name]`` header — is the
  narrator and whatever holds everywhere. It rides on every request, in the
  system prompt, after the language brief so it refines the register rather than
  replacing it;
* a **``[name]`` block** rides only on the requests whose text mentions that
  name, in the user message beside the required terminology, which is where this
  project has always put per-batch content.

Nothing inside a block is parsed. Deciding *whether* to send one is mechanical —
does this text contain this name — while deciding what good narration sounds
like is judgement, and invariant 4 keeps the second out of `config.py`. A format
with `address:` and `register:` fields was the losing alternative.

`docs/decisions.md`, 2026-08-02.
"""

import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import do_extract, do_translate  # noqa: E402
from scriptorium.config import (  # noqa: E402
    DEFAULT_CONFIG,
    STYLE_BLOCK_MAX,
    STYLE_HEADER,
    STYLE_PREAMBLE_MAX,
    StyleSheetError,
    load_style,
    write_templates,
)
from scriptorium.store import load_doc  # noqa: E402
from scriptorium.translate import _system_prompt, brief, style_notes  # noqa: E402

CFG = dict(DEFAULT_CONFIG, tone="literary")

#: Six paragraphs, and which name appears in which one is the whole fixture:
#: Eleanor in the first, Thomas in the third, nobody in the other four. A batch
#: that ends before the third paragraph must not carry Thomas's notes, and one
#: that contains it must.
BOOK = "\n\n".join([
    "The gate stood open when Eleanor came down the hill.",
    "She had not expected that, and she stopped in the road.",
    "Thomas was waiting on the step with the lamp in his hand.",
    "He said nothing at all until she had crossed the yard.",
    "The lamps were lit in only two of the windows above.",
    "She went in anyway, and the gate swung shut behind her.",
])

PREAMBLE = "The narration is close third person, past tense, anchored on Eleanor."

SHEET = f"""\
# A note to myself. If this reaches the model, comments are broken.
{PREAMBLE}

[Eleanor Vance, Eleanor]
She says 您 to her father and 你 to her sister.

[Thomas]
Working-class, warm, elliptical.

[Mrs Ashcombe]
She is not on the page anywhere in this chapter.
"""

ELEANOR_NOTE = "She says 您 to her father"
THOMAS_NOTE = "Working-class, warm, elliptical."
ABSENT_NOTE = "not on the page anywhere"
COMMENT = "If this reaches the model, comments are broken."

#: A stand-in translation, and its **length is load-bearing**. Since
#: 2026-09-04 `translate.misattributed` throws a whole reply away when an
#: answer cannot plausibly be a translation of the source it was asked
#: about, and the four characters this used to be were 8% of a fifty-character
#: sentence -- refused, correctly, and the refusal turned every test in this
#: file into an assertion about the retry path instead of about its own
#: subject. Sized at roughly the 0.45 zh-TW renders English at.
DONE = "這一段已經翻譯完成，內容僅供測試使用。"


class _Recorder:
    """A provider that answers every id it was asked for, and keeps the request."""

    def __init__(self, answer=None):
        self.requests = []          # the user message, verbatim
        self.systems = []
        self._answer = answer

    def describe(self):
        return "stub"

    def complete(self, system, user):
        self.requests.append(user)
        self.systems.append(system)
        # `indent=1` starts the payload with `[\n {`, and nothing this file puts
        # in front of it opens a bracket at end of line.
        items = json.loads(user[user.index("[\n"):])
        if self._answer is not None:
            return self._answer(items)
        return json.dumps({i["id"]: DONE + i["id"] for i in items}, ensure_ascii=False)


#: Serial, because every test here asserts which request carried what.
_SERIAL = 1


def _project(tmp_path, monkeypatch, sheet=SHEET, text=BOOK, name="novel.md",
             glossary=None):
    """A literary project holding one document, extracted and ready to translate."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "dnt.txt").write_text("", encoding="utf-8")
    if glossary is not None:
        (tmp_path / "config" / "glossary.csv").write_text(glossary, encoding="utf-8")
    if sheet is not None:
        (tmp_path / "config" / "style.txt").write_text(sheet, encoding="utf-8")
    (tmp_path / name).write_bytes((text + "\n").encode("utf-8"))
    do_extract(name, "zh-TW", CFG)
    return name, load_doc(name, "zh-TW")


def _run(src, segments, stub, monkeypatch, batch=6, cfg=CFG):
    monkeypatch.setattr(translate_mod, "build_provider", lambda name, _cfg, model=None: stub)
    return do_translate(src, "zh-TW", cfg, segments, "draft", batch=batch,
                        concurrency=_SERIAL)


# ── the four the package names ─────────────────────────────────────────────

def test_style_sheet_absent_noop_leaves_the_system_prompt_byte_identical(
        tmp_path, monkeypatch):
    """A project with no sheet must be unable to tell this feature was added.

    Asserted against the prompt the five-argument call produces — the shape
    every caller used before this landed — rather than against a copy of the
    expected text, which would pass while both drifted together.
    """
    src, doc = _project(tmp_path, monkeypatch, sheet=None)
    assert load_style(CFG) == ("", [])

    stub = _Recorder()
    _run(src, doc["segments"], stub, monkeypatch)

    assert stub.systems[0] == _system_prompt("en", "zh-TW", "literary", "draft", True)
    # And the user message is the payload with nothing in front of it but the
    # terminology head this project already had — here, not even that.
    assert stub.requests[0].startswith("[\n")


def test_style_sheet_ordering_puts_the_preamble_after_the_language_brief(
        tmp_path, monkeypatch):
    """Last read wins a contradiction, so the project's own voice goes last.

    The order under test is `_BASE_RULES` → `_CONTEXT_RULES` → language brief →
    style sheet. D4 measured what happens when it is wrong: an unconditional
    documentation-register sentence sitting below `Tone:` overrode it for
    months.
    """
    src, doc = _project(tmp_path, monkeypatch)
    stub = _Recorder()
    _run(src, doc["segments"], stub, monkeypatch)

    system = stub.systems[0]
    literary = brief("zh-TW", "literary")
    assert literary in system
    assert PREAMBLE in system
    assert system.index(PREAMBLE) > system.index(literary), "the sheet must refine the brief"
    assert system.index(literary) > system.index("Tone: literary.")
    # The per-character halves are not here: they are per-batch, and per-batch
    # content lives in the user message.
    assert ELEANOR_NOTE not in system
    assert THOMAS_NOTE not in system


@pytest.mark.parametrize("over,limit,names", [
    ("preamble", STYLE_PREAMBLE_MAX, None),
    ("block", STYLE_BLOCK_MAX, "Thomas"),
])
def test_style_sheet_size_limit_refuses_and_names_the_limit(
        tmp_path, monkeypatch, over, limit, names):
    """Refused at load, with the number in the message and advice on what to cut.

    Both halves are capped and the numbers differ, because the preamble is paid
    on every request and a block is paid only where its name appears. A message
    that said "too long" without the limit would leave the person editing by
    bisection.
    """
    body = "x" * (limit + 1)
    sheet = body if over == "preamble" else f"{PREAMBLE}\n\n[Thomas]\n{body}\n"
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "style.txt").write_text(sheet, encoding="utf-8")

    with pytest.raises(StyleSheetError) as e:
        load_style(CFG)
    assert str(limit) in str(e.value)
    assert "config/style.txt" in str(e.value).replace("\\", "/")
    if names:
        assert names in str(e.value)


def test_style_sheet_size_limit_is_measured_after_comments_are_stripped(
        tmp_path, monkeypatch):
    """A sheet is annotated by the person reading the book; that is what it is for.

    Measuring before stripping would refuse a sheet for prose nobody will ever
    send, and the person would have no way to see why — the file they are
    looking at is mostly notes.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    comments = "\n".join(f"# {'y' * 60}" for _ in range(STYLE_PREAMBLE_MAX // 30))
    (tmp_path / "config" / "style.txt").write_text(
        f"{comments}\n{PREAMBLE}\n", encoding="utf-8")

    preamble, blocks = load_style(CFG)
    assert preamble == PREAMBLE
    assert blocks == []


# ── selection: the reason the format has blocks at all ─────────────────────

def test_a_block_rides_only_on_the_batches_that_mention_its_name(
        tmp_path, monkeypatch):
    """The whole point. A cast of forty costs forty notes per request otherwise.

    Batch one holds paragraphs 1–3, which name Eleanor and Thomas; batch two
    holds 4–6, which name nobody. Mrs Ashcombe is in the sheet and never in the
    book, so she is never sent at all.
    """
    src, doc = _project(tmp_path, monkeypatch)
    stub = _Recorder()
    _run(src, doc["segments"], stub, monkeypatch, batch=3)

    first, second = stub.requests
    assert ELEANOR_NOTE in first
    assert THOMAS_NOTE in first
    assert ELEANOR_NOTE not in second
    assert THOMAS_NOTE not in second
    assert all(ABSENT_NOTE not in r for r in stub.requests)
    # Nothing matched in the second batch, so the head is absent too rather than
    # standing empty over nothing.
    assert second.startswith("[\n")


def test_a_retried_segment_carries_only_its_own_names(tmp_path, monkeypatch):
    """Retry sends one segment, so the selection narrows with it — for free.

    `retry_one` is where a hard sentence ends up. It builds its message from a
    list of one, and the notes are a function of that list, so the segment that
    failed is briefed on the characters *it* names and no others.
    """
    src, doc = _project(tmp_path, monkeypatch)
    segs = doc["segments"]
    thomas = next(s for s in segs if "Thomas" in s["masked"])

    def answer(items):
        # Fail the whole batch once so every segment is retried alone.
        if len(items) > 1:
            return "not json at all"
        return json.dumps({i["id"]: DONE + i["id"] for i in items}, ensure_ascii=False)

    stub = _Recorder(answer)
    _run(src, segs, stub, monkeypatch)

    retries = {r for r in stub.requests if r.count('"id"') == 1}
    assert len(retries) == len(segs), "every segment should have been retried alone"
    for request in retries:
        items = json.loads(request[request.index("[\n"):])
        if items[0]["id"] == thomas["id"]:
            assert THOMAS_NOTE in request
        else:
            assert THOMAS_NOTE not in request


def test_the_system_prompt_is_identical_across_every_batch(tmp_path, monkeypatch):
    """Why the per-character half is in the user message and not up here.

    A system prompt that changes per batch gives a local runtime's prefix cache
    nothing to reuse across a book of eighty requests — and forces `retry_one`
    to assemble a second one. Putting the batch-varying half where per-batch
    content already lives costs neither.
    """
    src, doc = _project(tmp_path, monkeypatch)
    stub = _Recorder()
    _run(src, doc["segments"], stub, monkeypatch, batch=2)

    assert len(stub.systems) == 3
    assert len(set(stub.systems)) == 1


def test_comments_never_reach_the_model(tmp_path, monkeypatch):
    """`#` is a note to yourself, in the preamble and inside a block alike."""
    src, doc = _project(tmp_path, monkeypatch, sheet=SHEET.replace(
        "[Thomas]\n", "[Thomas]\n# TODO: does he ever use 您?\n"))
    stub = _Recorder()
    _run(src, doc["segments"], stub, monkeypatch)

    everything = stub.systems[0] + stub.requests[0]
    assert COMMENT not in everything
    assert "TODO" not in everything
    assert THOMAS_NOTE in stub.requests[0], "the rest of the block still arrives"


def test_a_name_matches_as_a_whole_word_only():
    """`Eleanor` inside `Eleanora` is a different character.

    The same rule the glossary has always applied to a term, and now from the
    same function — three copies of it is how the two come to disagree about
    whether `Ashcombe's` mentions Ashcombe.
    """
    sheet_blocks = [{"names": ["Eleanor"], "notes": "n"}]
    assert style_notes([{"masked": "Eleanora crossed the yard."}], sheet_blocks) == []
    assert style_notes([{"masked": "Eleanor crossed the yard."}], sheet_blocks) == sheet_blocks
    assert style_notes([{"masked": "eleanor, in lower case."}], sheet_blocks) == sheet_blocks
    assert style_notes([{"masked": "Eleanor's carriage."}], sheet_blocks) == sheet_blocks
    # The boundary class has to reach past ASCII, and the discriminating case is
    # an accented letter *after* the name rather than inside it: with a bare
    # `[A-Za-z]` lookahead, `ï` is not a letter, so `Ana` matches inside `Anaïs`
    # and a minor character inherits the leading lady's notes. A novel in
    # English is full of names like this — `cli._LETTER` was widened for the
    # same reason and records the same finding.
    ana = [{"names": ["Ana"], "notes": "n"}]
    assert style_notes([{"masked": "Anaïs waited by the door."}], ana) == []
    assert style_notes([{"masked": "Ana waited by the door."}], ana) == ana


def test_an_unfilled_block_is_silent_rather_than_harmful(tmp_path, monkeypatch):
    """A header with nothing under it is a person part-way through a thought.

    The same answer a glossary row with an empty target gets: dropped, never
    sent as an instruction to render the character as nothing.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "style.txt").write_text(
        f"{PREAMBLE}\n\n[Eleanor]\n\n# nothing decided yet\n\n[Thomas]\nWarm.\n",
        encoding="utf-8")

    preamble, blocks = load_style(CFG)
    assert preamble == PREAMBLE
    assert [b["names"] for b in blocks] == [["Thomas"]]


def test_a_block_header_with_no_name_is_refused(tmp_path, monkeypatch):
    """`[]` cannot be matched against anything, so it would be silently dead."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "style.txt").write_text("a\n\n[ , ]\nnotes\n", encoding="utf-8")

    with pytest.raises(StyleSheetError) as e:
        load_style(CFG)
    assert "line 3" in str(e.value)


def test_a_sheet_that_is_not_utf8_is_refused_by_name(tmp_path, monkeypatch):
    """A zh-TW project's style sheet is full of Chinese, and editors still offer cp950.

    Named, because "which file" is the whole of the fix; a traceback out of
    `open` names the codec instead.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "style.txt").write_bytes(
        "敘事者是第三人稱。\n".encode("cp950"))

    with pytest.raises(StyleSheetError) as e:
        load_style(CFG)
    assert "UTF-8" in str(e.value)
    assert "config/style.txt" in str(e.value).replace("\\", "/")


# ── the agent path, which AGENTS.md treats as a peer of the model ──────────

def test_lx_todo_carries_the_same_voice_the_model_path_gets(tmp_path, monkeypatch, capsys):
    """An agent is one of three equal sources of a translation, so it is told the same.

    Both fields are built from the same two functions the model path uses, and
    the notes are selected against the whole emitted set — a batch, by another
    name. Before this, `lx todo` emitted `tone` and a placeholder rule, so an
    agent translating a novel was briefed on nothing at all.
    """
    src, doc = _project(tmp_path, monkeypatch)
    from scriptorium.cli import cmd_todo
    cmd_todo(argparse.Namespace(src=src, lang="zh-TW", all=False, limit=0), CFG)
    payload = json.loads(capsys.readouterr().out)

    assert payload["tone"] == "literary"
    assert brief("zh-TW", "literary") in payload["voice"]
    assert PREAMBLE in payload["voice"]
    assert [b["names"] for b in payload["voice_notes"]] == [
        ["Eleanor Vance", "Eleanor"], ["Thomas"]]
    assert ABSENT_NOTE not in json.dumps(payload, ensure_ascii=False)


def test_one_matcher_governs_the_glossary_and_the_style_sheet_alike(
        tmp_path, monkeypatch, capsys):
    """Three copies of a matching rule was two too many, and one was untested.

    `translate._glossary_hints`, `cmd_todo` and now the style sheet all ask the
    same question — does this text contain this name — and until they shared one
    function, `cmd_todo` answered it with its own inline regex. That the hint is
    attached only to the segments that *mention* the term was never asserted
    anywhere: a mutation sweep removing the filter left the suite green. So this
    covers both surfaces at once, which is also what stops them drifting.
    """
    src, doc = _project(tmp_path, monkeypatch,
                        glossary="source,target,forbidden,severity\nThomas,湯瑪士,,error\n")
    from scriptorium.cli import cmd_todo
    cmd_todo(argparse.Namespace(src=src, lang="zh-TW", all=False, limit=0), CFG)
    payload = json.loads(capsys.readouterr().out)

    hinted = [s["id"] for s in payload["segments"] if s.get("glossary")]
    thomas = next(s["id"] for s in doc["segments"] if "Thomas" in s["masked"])
    assert hinted == [thomas], "a term must ride only on the segments naming it"
    # And the style sheet's own block for the same character, selected by the
    # same function over the same text.
    assert [b["names"] for b in payload["voice_notes"]] == [
        ["Eleanor Vance", "Eleanor"], ["Thomas"]]


def test_lx_todo_keeps_both_keys_when_there_is_nothing_to_say(
        tmp_path, monkeypatch, capsys):
    """A stable shape, because HANDOFF-203 and HANDOFF-207 will freeze this.

    A consumer that has to branch on a missing key breaks the first time it
    meets a project with no style sheet.
    """
    src, _ = _project(tmp_path, monkeypatch, sheet=None)
    from scriptorium.cli import cmd_todo
    cmd_todo(argparse.Namespace(src=src, lang="zh-TW", all=False, limit=0), CFG)
    payload = json.loads(capsys.readouterr().out)

    assert payload["voice_notes"] == []
    assert brief("zh-TW", "literary") in payload["voice"]


def test_the_scaffolded_style_sheet_injects_nothing(tmp_path, monkeypatch):
    """`lx init` writes one so the format is discoverable, and it is all comments.

    Present-but-silent is the same trade `config/dnt.txt` already makes. A file
    nobody can find is a feature nobody uses; a scaffolded file that reached the
    model would brief every fresh project with an example about Eleanor.
    """
    monkeypatch.chdir(tmp_path)
    assert "config/style.txt" in write_templates()
    assert load_style(DEFAULT_CONFIG) == ("", [])
    assert all(line.startswith("#") or not line.strip()
               for line in STYLE_HEADER.splitlines())
