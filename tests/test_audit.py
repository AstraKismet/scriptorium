"""`lx audit` — whether a stored translation belongs to the source it is filed under.

`test_provider.py` owns the wire: the request shape and every refusal
`Provider._vectors` makes of an untrusted reply. This file owns what the command
does with the vectors — which records it examines, which it declines to examine
and says so, what it reports, and the two properties the package it came from
made red lines: that it writes nothing, and that it never claims the store is
clean.

The mock embedding server is imported from `test_provider.py` rather than
rebuilt, per this project's rule that a provider is exercised against that one
server. Its fallback gives every unlisted string a one-hot vector of its own, so
filler text is exactly orthogonal to everything and cannot manufacture a
finding: every assertion below is about the geometry the test wrote down.
"""

import argparse
import ast
import hashlib
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

# Before the path insert on purpose: this module does its own, so importing it
# here is what makes `scriptorium` importable below — the shape `test_memory.py`
# uses for `statedb`.
from test_provider import EMBED, EmbeddingsHandler, _embed_reset

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium import audit  # noqa: E402
from scriptorium.cli import (  # noqa: E402
    UnusableTarget,
    checked_margin,
    cmd_audit,
    do_apply,
    do_audit,
    do_extract,
)
from scriptorium.config import DEFAULT_CONFIG, ConfigError  # noqa: E402
from scriptorium.providers.errors import ProviderError  # noqa: E402
from scriptorium.store import SEGMENTATION_VERSION, append_tm, seg_hash  # noqa: E402


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), EmbeddingsHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _cfg(url, **extra):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["providers"]["bge"] = {"kind": "openai", "base_url": url,
                               "model": "bge-test", "api_key_env": ""}
    cfg["embedding"] = {"provider": "bge"}
    cfg.update(extra)
    return cfg


def _tm(*pairs, context=None):
    """Memory records for ``(source, target)`` pairs, in the order given.

    ``context`` is gettext's ``msgctxt`` and part of the key, so it is how two
    records come to hold the same source text and both survive the last-wins
    collapse — which is how 89 of the 208 records in the measured file have a
    source that is not unique.
    """
    return [{"hash": seg_hash(src), "segmentation_version": SEGMENTATION_VERSION,
             **({"context": context} if context else {}),
             "source": src, "target": tgt} for src, tgt in pairs]


#: Three records where the third holds the second's translation — the shape the
#: whole command exists for, and the smallest one that has a right answer.
#: `SRC_C` is orthogonal to its own target and identical to `TGT_B`, so C's own
#: score is 0 against a rival of 1.
A, B, C = "alpha source", "beta source", "gamma source"
TA, TB, TC = "alpha rendered", "beta rendered", "gamma rendered"
GEOMETRY = {A: [1, 0, 0], TA: [1, 0, 0],
            B: [0, 1, 0], TB: [0, 1, 0],
            C: [0, 0, 1], TC: [0, 1, 0]}   # C's target is B's translation


def _snapshot(root):
    """Every file under `.lx/`, by content.

    `state.db` is **included**, unlike `tests/test_contract.py`'s snapshot, which
    excludes it because closing the last connection to a WAL database
    checkpoints and rewrites the main file. That is a true statement about a
    server which also writes; measured 2026-09-06, `store.tracked`,
    `store.load_doc` and `store.load_tm` leave the file byte-identical, so the
    stronger claim is available here and is the one the package asked for.
    """
    out = {}
    for base, _dirs, names in os.walk(os.path.join(root, ".lx")):
        for name in sorted(names):
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


# ── what it reports ────────────────────────────────────────────────────────

def test_a_planted_misattributed_pair_is_reported_and_a_correct_one_is_not(
        tmp_path, monkeypatch, server):
    """The acceptance criterion, and the whole claim of the command.

    Three records, one of which holds its neighbour's translation. The report
    must name that record, must name the record its wording actually belongs to,
    and must leave the two correct ones alone.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    report = do_audit(_cfg(server), "zh-TW")

    assert report["records"] == 3 and report["compared"] == 3
    assert [f["ref"] for f in report["flagged"]] == [{"line": 3}]
    found = report["flagged"][0]
    assert found["source"] == C and found["target"] == TC
    assert found["belongs_to"]["ref"] == {"line": 2}
    assert found["belongs_to"]["source"] == B
    assert found["own"] == 0.0 and found["delta"] == 1.0
    assert report["skipped"] == []


def test_a_document_is_asked_the_same_question_and_names_the_segment(
        tmp_path, monkeypatch, server):
    """The second store. One rule, one report shape, two ways to name a record."""
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    (tmp_path / "d.md").write_bytes(f"{A}\n\n{B}\n\n{C}\n".encode())
    cfg = _cfg(server)
    doc, _reused, _rejected, _notes = do_extract("d.md", "zh-TW", cfg)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", cfg, dict(zip(ids, [TA, TB, TC])), origin="human")

    report = do_audit(cfg, "zh-TW", src="d.md")

    assert report["store"] == "document" and report["source"] == "d.md"
    assert [f["ref"] for f in report["flagged"]] == [{"seg": ids[2]}]
    assert report["flagged"][0]["belongs_to"]["ref"] == {"seg": ids[1]}
    assert report["flagged"][0]["origin"] == "human", (
        "the origin rides along because it answers the reader's next question: "
        "a human segment is refused to every model write, so no rerun reaches it")


def test_the_report_says_what_it_did_not_look_at_even_when_it_found_nothing(
        tmp_path, monkeypatch, server):
    """The red line: a listing must not imply completeness.

    An empty `flagged` list with nothing under it reads as a clean bill of
    health, and this instrument cannot issue one. The note is printed either way
    and the word does not appear in it.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    append_tm("zh-TW", _tm((A, TA), (B, TB)))

    report = do_audit(_cfg(server), "zh-TW")

    assert report["flagged"] == []
    assert "certifies nothing about the rest" in report["note"]
    assert "byte-identical" in report["note"]
    assert "clean" not in report["note"].lower()


def test_a_record_whose_source_is_not_unique_still_flags(
        tmp_path, monkeypatch, server):
    """Pinned because the first version of this design got it backwards.

    A byte-identical *rival* contributes exactly the diagonal and so can never
    be the argmax — but the record still flags, on some other rival. The brief
    that produced this module asserted the opposite as a property, and on the
    measured corpus 16 of the 17 true positives have a duplicated source: a
    filter that skipped them would have deleted almost every finding.

    The two records here hold **the same source text**, kept apart by `context`
    the way the measured file keeps its duplicates apart, and both are poisoned.
    Each is therefore in the other's rival pool at exactly its own score, and
    both must still be reported.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC))
              + _tm((C, TC), context="quote"))

    report = do_audit(_cfg(server), "zh-TW")

    sources = [f["source"] for f in report["flagged"]]
    assert sources.count(C) == 2, "both copies of a duplicated source are reported"
    assert {f["ref"]["line"] for f in report["flagged"]} == {3, 4}
    assert report["superseded"] == 0, "`context` keeps them apart, so neither is dead"


# ── which records it examines ──────────────────────────────────────────────

def test_the_memory_audit_reads_the_wordings_anything_reads_not_the_lines(
        tmp_path, monkeypatch, server):
    """A superseded line is not examined, and the count of them is printed.

    `store.load_tm` keeps the last record per key, so a corrected re-bank makes
    the poisoned line dead: nothing reads it, and repair here is an *append*, so
    a reviewer cannot make it go away. An audit over the lines would name
    records no command reads and would go on naming them after everything was
    repaired — the reviewer could never see their own fix land. Measured on the
    maintainer's tracked file: 17 poisoned lines, 2 of them live.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    # C is banked poisoned, then re-banked correctly under the same key.
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))
    append_tm("zh-TW", _tm((C, "gamma, correctly")))

    report = do_audit(_cfg(server), "zh-TW")

    assert report["records"] == 3 and report["superseded"] == 1
    assert report["flagged"] == [], "the poisoned line is superseded and dead"
    assert "supersedes" in report["note"]


def test_an_untranslated_segment_is_not_a_record(tmp_path, monkeypatch, server):
    """A document's pending segments are not pairs and are not counted as
    compared — there is nothing to compare them against."""
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    (tmp_path / "d.md").write_bytes(f"{A}\n\n{B}\n\n{C}\n".encode())
    cfg = _cfg(server)
    doc, _r, _j, _n = do_extract("d.md", "zh-TW", cfg)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", cfg, {ids[0]: TA, ids[1]: TB}, origin="human")

    report = do_audit(cfg, "zh-TW", src="d.md")

    assert report["records"] == 2 and report["compared"] == 2


def test_a_pair_too_long_to_offer_a_backend_is_named_rather_than_skipped_silently(
        tmp_path, monkeypatch, server):
    """A pair that was not compared is not a pair that came back clean.

    The ceiling is the *server's* physical batch size, not the model's context,
    so this is a limit the report has to state rather than a property of the
    instrument.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    long_source = "x" * (int(audit.TOKEN_CEILING / audit._OTHER_PER_CHAR) + 10)
    append_tm("zh-TW", _tm((A, TA), (long_source, "何か")))

    report = do_audit(_cfg(server), "zh-TW")

    assert report["records"] == 2 and report["compared"] == 1
    assert [s["ref"] for s in report["skipped"]] == [{"line": 2}]
    assert "ceiling" in report["skipped"][0]["reason"]
    assert "not a pair that came back clean" in report["note"]


def test_the_token_estimate_matches_what_the_measured_server_accepted():
    """Calibration, pinned against the four points that were measured live.

    English 1464 characters cost 354 real tokens and were accepted; 2196 cost
    530 and were refused. Traditional Chinese 540 cost 459 and were accepted;
    720 cost 611 and were refused. The estimate must accept both accepted cases
    and refuse both refused ones — it is allowed to be conservative in between,
    and on English it deliberately is.
    """
    en = ("The lamplighter went down the row of iron posts, and behind him the "
          "street came awake one flame at a time, so that a man walking north "
          "would have thought the light was following him. ")
    zh = "點燈人沿著那排鐵柱走下去，在他身後，街道一盞一盞地醒來，因此往北走的人會以為那光正跟著他。"

    assert not audit.oversize(en * 8), "1464 characters answered 200"
    assert audit.oversize(en * 12), "2196 characters answered 500"
    assert not audit.oversize(zh * 12), "540 characters answered 200"
    assert audit.oversize(zh * 16), "720 characters answered 500"
    # The ratio the CJK arm rests on, stated so a change to it is deliberate.
    assert 0.8 < audit.estimated_tokens(zh) / len(zh) < 0.9


def test_a_zero_vector_skips_its_record_rather_than_the_run(
        tmp_path, monkeypatch, server):
    """One degenerate input does not condemn a run, and is not silently dropped."""
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors={**GEOMETRY, TA: [0, 0, 0]})
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    report = do_audit(_cfg(server), "zh-TW")

    assert report["compared"] == 2
    assert [s["ref"] for s in report["skipped"]] == [{"line": 1}]
    assert "empty vector" in report["skipped"][0]["reason"]
    assert [f["ref"] for f in report["flagged"]] == [{"line": 3}]


# ── how it talks to the backend ────────────────────────────────────────────

def test_a_batch_that_fails_is_re_sent_one_input_at_a_time(
        tmp_path, monkeypatch, server):
    """`translate.run_batch` falling back to `retry_one`, in the same shape.

    One input a backend will not take must cost that input and not its fifteen
    neighbours — which is also what makes the token estimate a conservative
    guess rather than a promise.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)

    def mangle(body):
        texts = EMBED["seen"][-1]["payload"]["input"]
        # Refused only when this one input is in the request, alone or not.
        return {**body, "data": body["data"][:-1]} if C in texts else body

    EMBED["mangle"] = mangle
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    report = do_audit(_cfg(server), "zh-TW")

    assert report["records"] == 3 and report["compared"] == 2
    assert [s["ref"] for s in report["skipped"]] == [{"line": 3}]
    assert [f["ref"] for f in report["flagged"]] == []
    sizes = [len(s["payload"]["input"]) for s in EMBED["seen"]]
    assert 3 in sizes and sizes.count(1) >= 3, "the failed batch was isolated"


def test_a_failure_that_reaches_every_input_ends_the_run(
        tmp_path, monkeypatch, server):
    """A wrong shape, a refused credential or a server that is not there fails
    on every input alike. Grinding the whole store through one request at a time
    to report two hundred copies of one sentence is slower, ruder to the server
    and a worse answer than the sentence."""
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    EMBED["mangle"] = lambda body: [{"index": r["index"], "embedding": [r["embedding"]]}
                                    for r in body["data"]]
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    with pytest.raises(ProviderError, match="did not answer an embeddings list"):
        do_audit(_cfg(server), "zh-TW")


def test_the_backend_and_the_margin_travel_with_the_report(
        tmp_path, monkeypatch, server):
    """A finding always carries the instrument and the threshold that produced
    it, which is what makes `--margin` safe as a per-run flag rather than a
    project setting two runs could disagree over in silence."""
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    report = do_audit(_cfg(server), "zh-TW", margin=0.5, model="other-model")

    assert report["provider"] == "bge" and report["model"] == "other-model"
    assert report["margin"] == 0.5 and report["batch"] == audit.BATCH
    assert report["dimensions"] == EMBED["dims"]
    assert report["comparisons"] == 3 * 2


# ── the refusals ───────────────────────────────────────────────────────────

def test_an_audit_with_no_embedding_backend_says_which_key_and_refuses(
        tmp_path, monkeypatch):
    """The honest degradation, and it happens before anything leaves the machine.

    `ConfigError` is in `cli.main`'s exit-2 tuple, so this is one sentence and
    the project's refusal code rather than a traceback.
    """
    monkeypatch.chdir(tmp_path)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    with pytest.raises(ConfigError) as e:
        do_audit(cfg, "zh-TW")
    assert "embedding.provider" in str(e.value) and "--provider" in str(e.value)
    assert not (tmp_path / ".lx").exists(), "a refusal reads nothing and writes nothing"


def test_an_unknown_embedding_backend_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["embedding"] = {"provider": "nope"}
    with pytest.raises(ProviderError, match="unknown provider"):
        do_audit(cfg, "zh-TW")


def test_the_audit_never_resolves_a_route(tmp_path, monkeypatch, server):
    """It must not reach `config.resolve_route`, and this says so by `ast`.

    That function falls back to the `draft` entry for any stage it does not
    recognise, so an unwritten key would silently POST every source and every
    translation in the project to the *translation* backend — a data-egress
    default nobody chose. A grep would match the import at the top of `cli.py`;
    reading the function's own body is what pins it.
    """
    source = open(os.path.join(os.path.dirname(__file__), "..", "src",
                               "scriptorium", "cli.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.FunctionDef) and n.name == "do_audit")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "resolve_route" not in called and "route_entry" not in called


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 2.5, True, "0.1"])
def test_a_margin_that_would_report_everything_or_nothing_is_refused(bad):
    """A NaN margin makes every comparison false, so the command would report a
    clean store over a poisoned one; a negative one reports almost every record.
    Both are silent, which is why neither is left to `type=float`."""
    with pytest.raises(UnusableTarget):
        checked_margin(bad)


def test_a_margin_that_is_a_number_in_range_is_kept():
    assert checked_margin(None) is None
    assert checked_margin(0) == 0.0
    assert checked_margin(0.1) == 0.1
    assert checked_margin(2) == 2.0


def test_the_margin_is_checked_where_the_command_takes_it(tmp_path, monkeypatch, server):
    """The rule exists **and is wired**, which are two facts.

    `checked_margin` had a test of its own and `do_audit` could still have
    ignored it: a mutant that dropped the call escaped the first version of this
    file entirely. Checked before anything is embedded, `checked_limit`'s rule —
    a malformed argument is malformed whether or not the run would have reached
    the point where it mattered.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    with pytest.raises(UnusableTarget):
        do_audit(_cfg(server), "zh-TW", margin=float("nan"))
    with pytest.raises(UnusableTarget):
        do_audit(_cfg(server), "zh-TW", margin=-1.0)
    assert EMBED["seen"] == [], "refused before anything was sent"


# ── the two red lines ──────────────────────────────────────────────────────

def test_the_audit_writes_nothing(tmp_path, monkeypatch, server, capsys):
    """Red line one, asserted on the bytes of both stores.

    **The snapshot is taken before the first audit, not between two of them.**
    That ordering is the whole test: a mutant that writes a report file beside
    `lx check`'s escaped the first version of this, because it wrote the same
    bytes on every run and a before-and-after taken *after* two runs saw an
    artifact that had already settled. An oracle downstream of the defect sees
    nothing — so the audit runs four times here and is compared against a
    project it has never touched.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    (tmp_path / "d.md").write_bytes(f"{A}\n\n{B}\n\n{C}\n".encode())
    cfg = _cfg(server)
    doc, _r, _j, _n = do_extract("d.md", "zh-TW", cfg)
    ids = [s["id"] for s in doc["segments"]]
    do_apply("d.md", "zh-TW", cfg, dict(zip(ids, [TA, TB, TC])), origin="human")
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))

    before = _snapshot(str(tmp_path))
    assert before, "the fixture wrote some state or this asserts nothing"
    names = set(before)

    args = argparse.Namespace(src=None, lang="zh-TW", provider=None, model=None,
                              margin=None, json=False, max=25)
    for _round in range(2):
        cmd_audit(args, cfg)
        cmd_audit(argparse.Namespace(**{**vars(args), "src": "d.md"}), cfg)
    out = capsys.readouterr().out

    after = _snapshot(str(tmp_path))
    assert set(after) == names, "the audit created or removed a file"
    assert after == before, "the audit changed a file it had no business writing"
    assert "flagged" in out


def test_the_audit_creates_no_state_where_there_is_none(tmp_path, monkeypatch, server):
    """`store._connect(create=True)` is the *default*, and this is the guard
    against it reaching the read path. An audit of a project with no memory
    reports an empty one; it does not bring `.lx/` into existence."""
    monkeypatch.chdir(tmp_path)
    _embed_reset()

    report = do_audit(_cfg(server), "zh-TW")

    assert report["records"] == 0 and report["flagged"] == []
    assert not (tmp_path / ".lx").exists()
    assert EMBED["seen"] == [], "nothing to compare means nothing is sent"


def test_a_flagged_audit_still_exits_zero(tmp_path, monkeypatch, server, capsys):
    """Red line two, and the decision it forced.

    A nonzero exit on findings is a CI hook waiting to be written, and this
    instrument's findings are statistical where `lx check`'s are mechanically
    decidable. And the moment exit 1 means "found something", exit 0 means
    "found nothing", which is one shell script away from "clean". Anyone who
    wants a gate reads `flagged` out of `--json` and writes one on purpose.
    """
    monkeypatch.chdir(tmp_path)
    _embed_reset(vectors=GEOMETRY)
    append_tm("zh-TW", _tm((A, TA), (B, TB), (C, TC)))
    args = argparse.Namespace(src=None, lang="zh-TW", provider=None, model=None,
                              margin=None, json=False, max=25)

    cmd_audit(args, _cfg(server))   # returns rather than raising SystemExit

    out = capsys.readouterr().out
    assert "1 flagged" in out and "belongs to line 2" in out


def test_the_checks_module_still_performs_no_io():
    """The audit must not become a gate, and the shortest way for it to become
    one is a rule in `checks.py` — whose exit code invariant 10 calls the
    evidence. Asserted by reading the module's imports with `ast` rather than by
    grep, which is this project's idiom for a guard a rename can defeat."""
    path = os.path.join(os.path.dirname(__file__), "..", "src", "scriptorium",
                        "checks.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported == {"re", "collections", "mask", "mdparse"}, imported
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in called and "urlopen" not in called
