"""The opt-out, on every surface that offers it, and the selection it changes.

Written after an adversarial pass found that **neither user-facing spelling of
`--overwrite-human` had a test at all**: replacing `over_human=args.overwrite_human`
and `over_human=body.get("overwrite_human")` with a hard `False` left the whole
suite green. Only `store.save_targets`' keyword had been covered, and the two
spellings a person or a client actually types were unprotected — for a flag whose
failure direction is a reviewer deliberately asking a run to replace their own
drafts and being silently refused everything.

The second half of the file is the rule that flag turns off: **selection knows
what the write enforces**. Without that, `lx repair` pays a model for a segment
it then refuses and exits 0 with the error count unmoved, and
`lx translate --mode polish` sends a whole reviewed novel and applies none of it.
Both were measured, neither was visible at the four-segment scale everything else
here is verified at.
"""

import json
import os
import re
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import cli  # noqa: E402
from scriptorium import translate as translate_mod  # noqa: E402
from scriptorium.cli import do_apply, do_extract, do_select, do_translate  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.store import load_doc  # noqa: E402
from scriptorium.web.server import _Handler  # noqa: E402

CFG = dict(DEFAULT_CONFIG)

DOC = (b"The gate stood open when she came down the hill.\n"
       b"\n"
       b"She went in anyway, and it swung shut behind her.\n")


class _Echo:
    """Answers every id it is asked for, carrying the placeholders back."""

    def __init__(self):
        self.seen = []

    def describe(self):
        return "stub"

    def complete(self, system, user):
        items = json.loads(user[user.index("["):])
        self.seen.extend(i["id"] for i in items)
        return json.dumps(
            # Sized so `translate.misattributed` does not refuse the reply; see
            # the same note in `tests/test_select.py`.
            {i["id"]: "這是模型寫下的字，長度合乎一段譯文。" + i["id"]
                      + "".join(re.findall(r"⟦\d+⟧", i["text"]))
             for i in items}, ensure_ascii=False)


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    """Two paragraphs, both written by a person. The state polish exists for."""
    root = tmp_path / "nest" / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "dnt.txt").write_text("", encoding="utf-8")
    (root / "d.md").write_bytes(DOC)
    monkeypatch.chdir(root)
    do_extract("d.md", "zh-TW", CFG)
    ids = [s["id"] for s in load_doc("d.md", "zh-TW")["segments"]]
    do_apply("d.md", "zh-TW", CFG,
             {ids[0]: "審校者的第一句。", ids[1]: "審校者的第二句。"}, origin="human")
    return ids


def _stub(monkeypatch):
    echo = _Echo()
    monkeypatch.setattr(translate_mod, "build_provider",
                        lambda name, cfg, model=None: echo)
    return echo


# ── selection knows the rule the write enforces ────────────────────────────

def test_polish_offers_nothing_on_a_book_a_person_has_written(reviewed):
    """The measured regression, at the smallest size that shows it.

    `do_select`'s polish branch selected on `target` and `kind` and never on
    `origin`, so on a 2000-paragraph reviewed novel it selected all two thousand
    and `save_targets` refused every one — a whole book billed per invocation,
    `translated 0 segment(s)`, exit 0.
    """
    doc = load_doc("d.md", "zh-TW")
    assert do_select(doc, CFG, "polish") == []
    assert len(do_select(doc, CFG, "polish", over_human=True)) == 2


def test_repair_offers_nothing_it_would_be_refused(reviewed):
    doc = load_doc("d.md", "zh-TW")
    # Make both fail a check without changing who wrote them.
    do_apply("d.md", "zh-TW", CFG, {reviewed[0]: "審校者的第一句。⟦9⟧"}, origin="human")
    doc = load_doc("d.md", "zh-TW")
    from scriptorium.translate import failing_segments
    assert [s["id"] for s in failing_segments(doc, CFG, include_held=True)] == [reviewed[0]]
    assert do_select(doc, CFG, "repair") == []
    assert len(do_select(doc, CFG, "repair", over_human=True)) == 1


def test_an_explicit_id_still_reaches_a_person_s_segment(reviewed):
    """The exemption, and the same one the hold has: naming an id is a person
    pointing at a segment. The refusal then happens at the write, where its
    message can name the way past it."""
    doc = load_doc("d.md", "zh-TW")
    assert [s["id"] for s in do_select(doc, CFG, "polish", ids=[reviewed[0]])] == [reviewed[0]]


def test_repair_says_why_it_declined_rather_than_nothing_failing(reviewed, capsys):
    """`lx check` exits 1 on these errors and `lx repair` must not answer silence."""
    do_apply("d.md", "zh-TW", CFG, {reviewed[0]: "審校者的第一句。⟦9⟧"}, origin="human")
    cli.cmd_repair(cli.build_parser().parse_args(
        ["repair", "d.md", "--lang", "zh-TW"]), CFG)
    said = capsys.readouterr().out
    assert "nothing failing" not in said
    assert reviewed[0] in said
    assert "--overwrite-human" in said


# ── the flag, on each surface that offers it ───────────────────────────────

def test_the_cli_flag_reaches_both_the_selection_and_the_write(reviewed, monkeypatch):
    """Through `build_parser`, because half of what this asserts is that the
    subcommand carries the flag at all."""
    echo = _stub(monkeypatch)
    cli.cmd_translate(cli.build_parser().parse_args(
        ["translate", "d.md", "--lang", "zh-TW", "--mode", "polish"]), CFG)
    assert echo.seen == [], "a run without the flag must not even ask"

    echo = _stub(monkeypatch)
    cli.cmd_translate(cli.build_parser().parse_args(
        ["translate", "d.md", "--lang", "zh-TW", "--mode", "polish",
         "--overwrite-human"]), CFG)
    assert sorted(echo.seen) == sorted(reviewed), "the flag did not reach selection"
    after = {s["id"]: s for s in load_doc("d.md", "zh-TW")["segments"]}
    assert all(s["origin"] == "llm:polish" for s in after.values()), (
        "the flag did not reach the write")


def test_apply_carries_the_flag_too(reviewed):
    """`--origin` takes free text, so `lx apply --origin llm:draft` reaches the
    same guard — and until 2026-08-16 it was refused with no way past it."""
    payload = {reviewed[0]: "模型改寫的句子。"}
    applied, _u, _s, _c, refused = do_apply("d.md", "zh-TW", CFG, payload,
                                            origin="llm:draft")
    assert (applied, refused) == (0, [reviewed[0]])

    applied, _u, _s, _c, refused = do_apply("d.md", "zh-TW", CFG, payload,
                                            origin="llm:draft", over_human=True)
    assert (applied, refused) == (1, [])
    assert cli.build_parser().parse_args(
        ["apply", "d.md", "--lang", "zh-TW"]).overwrite_human is False


def test_do_translate_reports_what_the_flag_let_through(reviewed, monkeypatch):
    _stub(monkeypatch)
    doc = load_doc("d.md", "zh-TW")
    both = doc["segments"]
    applied, failures, refused = do_translate("d.md", "zh-TW", CFG, both, "polish")
    assert (applied, failures, refused) == (0, [], sorted(reviewed))

    applied, failures, refused = do_translate("d.md", "zh-TW", CFG, both, "polish",
                                              over_human=True)
    assert (applied, failures, refused) == (2, [], [])


# ── and on the wire ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(base, path, obj):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_wire_flag_is_validated_rather_than_coerced(base, reviewed):
    """`bool("false")` is `True`. This is the opt-out for a rule whose failure
    direction is destructive and silent, and a form or a `URLSearchParams` body
    sends the string — so it is refused, the way `do_apply` refuses a mis-shaped
    `base` rather than interpreting it."""
    for bad in ("false", "true", 1, 0, None, []):
        code, body = _post(base, "/api/translate", {
            "src": "d.md", "lang": "zh-TW", "ids": ["nope"], "overwrite_human": bad})
        assert code == 400, f"{bad!r} was accepted"
        assert "overwrite_human" in body["error"]

    code, body = _post(base, "/api/translate", {
        "src": "d.md", "lang": "zh-TW", "ids": ["nope"], "overwrite_human": True})
    assert code == 200 and body["total"] == 0


def test_the_wire_flag_reaches_selection(base, reviewed):
    """`total` is fixed at creation and is the endpoint's own answer to "which
    segments", so it shows the flag reaching selection without a model."""
    _c, without = _post(base, "/api/translate",
                        {"src": "d.md", "lang": "zh-TW", "mode": "polish"})
    assert without["total"] == 0
    _c, with_flag = _post(base, "/api/translate", {
        "src": "d.md", "lang": "zh-TW", "mode": "polish", "overwrite_human": True})
    assert with_flag["total"] == 2
