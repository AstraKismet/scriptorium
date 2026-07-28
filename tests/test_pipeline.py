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


def test_lexicon_flags_a_nonpreferred_term():
    assert "lexicon" in _rules("The server caches data.", "\u670d\u52d9\u5668\u6703\u7de9\u5b58\u6578\u64da\u3002")


# --- the zh-TW lexicon, in both directions -----------------------------------
#
# Until 2026-07-28 the table failed correct Traditional Chinese, one case at
# error severity, because a plain substring match cannot tell \u7269\u9ad4\u7684\u8cea\u91cf (mass)
# from \u54c1\u8cea. Fixtures below are written raw rather than escaped: for a lexicon
# rule the fixture *is* the specification, and \u6578\u64da is not reviewable.

def _lexicon(target, cfg=CFG):
    """Lexicon issues for a target as (severity, message).

    Severity is half the assertion here \u2014 error stops a build, warn costs a
    reviewer three seconds \u2014 so this deliberately does not collapse to rule names
    the way `_rules` does.
    """
    issues = check_segment(_seg("A source sentence.", target), "zh-TW", cfg, [], [])
    return [(i["severity"], i["message"]) for i in issues if i["rule"] == "lexicon"]


@pytest.mark.parametrize("target", [
    "\u4f9d\u7167\u6cd5\u5f8b\u7a0b\u5e8f\u8fa6\u7406",       # \u7a0b\u5e8f \u2014 a legal procedure, not \u7a0b\u5f0f
    "\u7269\u9ad4\u7684\u8cea\u91cf\u662f\u5169\u516c\u65a4",     # \u8cea\u91cf \u2014 mass, not \u54c1\u8cea
    "\u4ed6\u652f\u6301\u9019\u9805\u63d0\u6848",         # \u652f\u6301 \u2014 endorsement, not \u652f\u63f4
    "\u95b1\u8b80\u539f\u59cb\u6587\u672c",           # \u6587\u672c \u2014 the text itself, not \u6587\u5b57
    "\u5206\u6790\u9019\u6279\u6578\u64da",           # \u6578\u64da \u2014 measured readings; this one failed the build
])
def test_lexicon_passes_correct_traditional_chinese(target):
    assert _lexicon(target) == []


@pytest.mark.parametrize("target", [
    "\u6709\u7dda\u96fb\u8996\u983b\u9053\u5f88\u591a",       # \u96fb\u8996 + \u983b\u9053, not \u8996\u983b
    "\u8001\u9f20\u6a19\u672c\u5df2\u7d93\u7de8\u865f",       # \u8001\u9f20 + \u6a19\u672c, not \u9f20\u6a19
    "\u517c\u5bb9\u4e26\u84c4\u7684\u614b\u5ea6",         # the idiom, not \u517c\u5bb9
])
def test_lexicon_guard_exempts_a_longer_word(target):
    assert _lexicon(target) == []


@pytest.mark.parametrize("target", [
    "\u9ad4\u5167\u5b58\u5728\u6297\u9ad4",           # \u9ad4\u5167 + \u5b58\u5728, not \u5167\u5b58
    "\u53c3\u6578\u7d44\u5408\u592a\u591a",           # \u53c3\u6578 + \u7d44\u5408, not \u6578\u7d44
    "\u76ae\u5e36\u5bec\u5ea6\u4e0d\u8db3",           # \u76ae\u5e36 + \u5bec\u5ea6, not \u5e36\u5bec
    "\u523a\u6fc0\u6d3b\u5316\u7d30\u80de",           # \u523a\u6fc0 + \u6d3b\u5316, not \u6fc0\u6d3b
    "\u6536\u96c6\u6210\u679c\u4e26\u6b78\u6a94",         # \u6536\u96c6 + \u6210\u679c, not \u96c6\u6210
    "\u5f37\u8abf\u8a66\u7528\u671f\u7684\u898f\u5247",       # \u5f37\u8abf + \u8a66\u7528, not \u8abf\u8a66
    "\u6eab\u5ea6\u7684\u6539\u8b8a\u91cf\u5f88\u5c0f",       # \u6539\u8b8a + \u91cf, not \u8b8a\u91cf
    "\u6062\u5fa9\u7528\u96fb\u4e4b\u5f8c\u518d\u8a66",       # \u6062\u5fa9 + \u7528\u96fb, not \u5fa9\u7528
])
def test_lexicon_collision_never_fails_the_build(target):
    issues = _lexicon(target)
    assert issues, "row removed rather than demoted \u2014 retire this fixture with it"
    assert all(sev == "warn" for sev, _ in issues)


@pytest.mark.parametrize("target", [
    "\u8acb\u5148\u5b89\u88dd\u9019\u500b\u8edf\u4ef6",       # \u8edf\u9ad4
    "\u8abf\u6574\u87a2\u5e55\u7684\u8996\u983b\u8a2d\u5b9a",     # \u5f71\u7247 \u2014 guarded, but \u8a2d is not a \u983bX continuation
    "\u9ede\u64ca\u9f20\u6a19\u53f3\u9375",           # \u6ed1\u9f20 \u2014 guarded, but \u53f3 is not \u672c
    "\u9019\u500b\u7248\u672c\u4e0d\u517c\u5bb9",         # \u76f8\u5bb9 \u2014 guarded, but \u4e26 does not follow
])
def test_lexicon_still_fails_the_build_on_an_unambiguous_form(target):
    assert [sev for sev, _ in _lexicon(target)] == ["error"]


def test_lexicon_leaves_a_clean_translation_alone():
    assert _lexicon("\u9019\u53f0\u4f3a\u670d\u5668\u7684\u8edf\u9ad4\u5f88\u5feb") == []


def test_lexicon_extra_still_adds_a_project_term_at_error():
    # A list gives the severity; a bare string means error. The second form is how
    # a project restores a row the audit removed, in a domain where it is decidable.
    cfg = {**CFG, "lexicon_extra": {"\u5143\u6578\u64da": ["\u4e2d\u7e7c\u8cc7\u6599", "error"], "\u767b\u9304": "\u767b\u5165"}}
    assert [sev for sev, _ in _lexicon("\u5beb\u5165\u5143\u6578\u64da", cfg)] == ["error"]
    assert [sev for sev, _ in _lexicon("\u5b8c\u6210\u767b\u9304\u7a0b\u5e8f", cfg)] == ["error"]


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
# The properties covered, one file each: a UTF-8 BOM, CRLF terminators, CRLF
# mixed with bare LF, CR-only terminators, CRLF list items, nested lists, wrapped
# list-item continuations at four spaces and at two, indented code, HTML blocks,
# tables with alignment padding and tables without a leading pipe, hard line
# breaks, front matter, reference link definitions, setext headings and thematic
# breaks, fenced and unclosed code, nested blockquotes, inline markup, CJK with
# full-width punctuation, a file with no trailing newline, whitespace-only,
# blank-lines-only, an empty file, and one 112k-character manual long enough for
# a per-block defect to hide in.
#
# Two of them are load-bearing rather than decorative.
#
# `cr-only-terminators.md` is what separates a real terminator fix from one that
# special-cases "\r\n": the latter passes every other fixture here and still
# loses this file's bytes. Note what it does *not* assert — the parser treats a
# lone CR as ordinary text, not as a line ending, so this file is one segment.
# CommonMark would call it two lines. Reinterpreting it would move a segment
# boundary, which is a decision separate from preserving the bytes, and
# `docs/decisions.md` records why it was not taken.
#
# `line-separator-control-chars.md` contains every character `str.splitlines()`
# breaks on that `str.split("\n")` does not. `parse` splits on "\n" alone and
# `splitlines` looks like a tidier way to handle terminators; this fixture is
# the standing proof that the swap is not behaviour-neutral.
#
# Red line: a fixture is never edited to make a test pass. If one fails, either
# the parser is wrong or the fixture is not valid input — decide which, and say
# so in the commit.

CORPUS = pathlib.Path(__file__).parent / "corpus"

# Empty, and that is the state to keep it in. It held the two round-trip defects
# measured 2026-07-27, both fixed 2026-07-28 — see `docs/decisions.md`. The
# entries came out in the same commit as the fix, because strict=True turns a
# fixed-but-still-listed defect into a build failure. The machinery stays for the
# next measured defect:
# an xfail here is a scheduled repair, never a permanent exemption, and a file
# that is simply not in the corpus is the alternative that hides it instead.
KNOWN_BROKEN = {}


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
