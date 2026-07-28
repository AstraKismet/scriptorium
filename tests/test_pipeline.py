"""Round-trip and validator tests. No network, no model."""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scriptorium.checks import check_segment  # noqa: E402
from scriptorium.config import DEFAULT_CONFIG  # noqa: E402
from scriptorium.mask import mask, repair_placeholders, unmask  # noqa: E402
from scriptorium.mdparse import parse, render  # noqa: E402
from scriptorium.normalize import normalize  # noqa: E402
from scriptorium.translate import parse_reply  # noqa: E402

SAMPLE = """\
---
title: Guide
---

# Deployment Guide

The **Celurion** server requires Go 1.22 and `postgres`.

| Option | Default |
| --- | --- |
| `port` | 8080 |

1. Clone from [the repo](https://example.com/x).
2. Run `make build`.

> Never commit secrets.

```python
print("keep me")
```

Formula $E = mc^2$ stays.
"""

CFG = dict(DEFAULT_CONFIG)


def test_identity_roundtrip_without_translation():
    nodes, segs = parse(SAMPLE, ["Celurion", "Go"])
    for s in segs:
        s["target"] = s["masked"]          # translate to itself
    doc = {"nodes": nodes, "segments": segs, "lang": "zh-TW"}
    out, missing = render(doc, CFG)
    assert missing == 0
    assert out == SAMPLE


def test_code_and_frontmatter_never_become_segments():
    _nodes, segs = parse(SAMPLE, [])
    joined = " ".join(s["source"] for s in segs)
    assert "keep me" not in joined
    assert "title: Guide" not in joined


def test_masking_is_reversible():
    text = "Run `go build` then see https://x.dev/a?b=1 for {{var}}."
    masked, slots = mask(text, [])
    assert "go build" not in masked
    assert unmask(masked, slots) == text


def test_dnt_respects_word_boundaries():
    masked, slots = mask("Go to Google with Go.", ["Go"])
    assert "Google" in unmask(masked, slots)
    assert masked.count("\u27e6") == 2


@pytest.mark.parametrize("mangled", ["\u30103\u3011", "[[3]]", "\u27e6 \uff13 \u27e7", "\u30143\u3015"])
def test_placeholder_repair(mangled):
    assert repair_placeholders(f"x{mangled}y") == "x\u27e63\u27e7y"


def _seg(masked, target):
    return {"id": "s1", "kind": "para", "masked": masked, "target": target, "slots": {}}


def _rules(masked, target, lang="zh-TW", glossary=(), dnt=()):
    issues = check_segment(_seg(masked, target), lang, CFG, list(glossary), list(dnt))
    return {i["rule"] for i in issues}


def test_dropped_placeholder_is_an_error():
    assert "tags" in _rules("Set \u27e61\u27e7 now.", "\u8acb\u8a2d\u5b9a\u3002")


def test_reordered_placeholder_is_fine():
    assert "tags" not in _rules("\u27e61\u27e7 and \u27e62\u27e7", "\u27e62\u27e7 \u8207 \u27e61\u27e7")


def test_missing_number_is_an_error():
    assert "numbers" in _rules("Requires Go 1.22 exactly.", "\u9700\u8981 Go 1.21\u3002")


def test_nonpreferred_term_is_flagged():
    assert "lexicon" in _rules("The server caches data.", "\u670d\u52d9\u5668\u6703\u7de9\u5b58\u6578\u64da\u3002")


def test_glossary_forbidden_variant():
    g = [{"source": "repository", "target": "\u5132\u5b58\u5eab", "forbidden": ["\u5009\u5eab"], "severity": "error"}]
    assert "glossary" in _rules("Clone the repository.", "\u8907\u88fd\u5009\u5eab\u3002", glossary=g)


def test_clean_segment_passes():
    assert _rules("Run the server on port 8080.", "\u5728\u9023\u63a5\u57e0 8080 \u4e0a\u57f7\u884c\u4f3a\u670d\u5668\u3002") == set()


def test_normalize_fixes_width_and_spacing():
    got = normalize("\u8b66\u544a:\u8acb\u5148\u57f7\u884cmake build\u6307\u4ee4", "zh-TW", CFG)
    assert got == "\u8b66\u544a\uff1a\u8acb\u5148\u57f7\u884c make build \u6307\u4ee4"


@pytest.mark.parametrize("reply", [
    '{"s1": "a"}',
    '```json\n{"s1": "a"}\n```',
    'Sure, here you go:\n{"s1": "a"}\nHope that helps.',
    '[{"id": "s1", "text": "a"}]',
])
def test_reply_parsing_tolerates_chatty_models(reply):
    assert parse_reply(reply)["s1"] == "a"


def test_reply_parsing_rejects_garbage():
    with pytest.raises(ValueError):
        parse_reply("I cannot help with that.")


def test_config_layering_keeps_new_defaults():
    from scriptorium.config import _merge
    merged = _merge(DEFAULT_CONFIG, {"tone": "literary", "batch": {"size": 5}})
    assert merged["tone"] == "literary"
    assert merged["batch"]["size"] == 5
    assert merged["batch"]["concurrency"] == DEFAULT_CONFIG["batch"]["concurrency"]


# --- the skeleton guarantee (invariant 2a) ---------------------------------
#
# `tests/corpus/` holds one input file per property, and nothing else — no
# README, no manifest. The same reasoning as `handoff/`: anything else in the
# directory would be collected as a fixture, so the explanation lives here.
#
# The properties covered, one file each: a UTF-8 BOM, CRLF terminators, nested
# lists, wrapped list-item continuations, indented code, HTML blocks, tables with
# alignment padding and tables without a leading pipe, hard line breaks, front
# matter, reference link definitions, setext headings and thematic breaks,
# fenced and unclosed code, nested blockquotes, inline markup, CJK with
# full-width punctuation, a file with no trailing newline, whitespace-only,
# blank-lines-only, an empty file, and one 112k-character manual long enough for
# a per-block defect to hide in.
#
# `line-separator-control-chars.md` is load-bearing rather than decorative: it
# contains every character `str.splitlines()` breaks on that `str.split("\n")`
# does not. HANDOFF-002 is tempted toward `splitlines`, and this fixture is what
# proves that swap is not behaviour-neutral.
#
# Red line: a fixture is never edited to make a test pass. If one fails, either
# the parser is wrong or the fixture is not valid input — decide which, and say
# so in the commit.

CORPUS = pathlib.Path(__file__).parent / "corpus"

# Measured 2026-07-27, scheduled as HANDOFF-002. One entry per defect, not per
# failing input: six corpus-shaped inputs fail, but they collapse to these two
# root causes, and the package's acceptance criteria allow exactly two xfails.
# strict=True so that fixing a defect turns the suite red until the entry is
# removed — a silently-passing xfail is how a fix gets forgotten.
KNOWN_BROKEN = {
    "crlf-line-endings.md":
        "mdparse.py:40 rstrips the CR off every segment source",
    "list-continuation-indent.md":
        "mdparse.py:139 strips the indent off list-item continuation lines",
}


def identity_roundtrip(text, dnt=()):
    """Substitute each segment's source back into the skeleton.

    Deliberately not routed through ``render()``: render also unmasks and
    normalizes, so a failure there could be a masking defect rather than a
    skeleton defect, and this property is only about the skeleton.
    """
    nodes, segs = parse(text, dnt)
    by_id = {s["id"]: s for s in segs}
    return "".join(
        n["v"] if n["t"] == "raw" else by_id[n["id"]]["source"] for n in nodes
    )


def _corpus_files():
    return sorted(p for p in CORPUS.iterdir() if p.is_file())


def _case(path):
    marks = ([pytest.mark.xfail(strict=True, reason=KNOWN_BROKEN[path.name])]
             if path.name in KNOWN_BROKEN else [])
    return pytest.param(path, id=path.name, marks=marks)


def _explain(name, expected, actual):
    # repr() of both sides, windowed on the first difference: a CR or a trailing
    # space is invisible otherwise, and the long fixture is 112k characters.
    i = next((k for k, (a, b) in enumerate(zip(expected, actual)) if a != b),
             min(len(expected), len(actual)))
    lo, hi = max(0, i - 60), i + 60
    return (
        f"{name} did not round-trip; first difference at index {i}\n"
        f"  expected: {expected[lo:hi]!r}\n"
        f"  actual  : {actual[lo:hi]!r}\n"
        f"  lengths : expected {len(expected)}, actual {len(actual)}"
    )


@pytest.mark.parametrize("path", [_case(p) for p in _corpus_files()])
def test_corpus_roundtrips_byte_for_byte(path):
    # Bytes, then an explicit decode. utf-8-sig would eat the BOM fixture and
    # text mode would rewrite the CRLF fixture — in both cases silently hiding
    # the defect the fixture exists to expose.
    text = path.read_bytes().decode("utf-8")
    got = identity_roundtrip(text)
    assert got == text, _explain(path.name, text, got)


def test_corpus_is_present_and_known_breakage_names_real_fixtures():
    # A typo in KNOWN_BROKEN makes the real fixture run unmarked, which fails
    # loudly. A renamed or deleted fixture is the quiet case: its coverage
    # vanishes and nothing complains. This is the guard for that.
    names = {p.name for p in _corpus_files()}
    assert names, "tests/corpus/ is empty"
    missing = sorted(KNOWN_BROKEN.keys() - names)
    assert not missing, f"KNOWN_BROKEN names fixtures that do not exist: {missing}"
