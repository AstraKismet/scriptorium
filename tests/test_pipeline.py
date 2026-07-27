"""Round-trip and validator tests. No network, no model."""

import os
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


@pytest.mark.parametrize("text", [
    SAMPLE,
    "no trailing newline",
    "# Only a heading\n",
    "\n\n\n",
    "- a\n- b\n",
    "| a | b |\n| --- | --- |\n| 1 | 2 |\n",
    "Para one.\n\nPara two.",
])
def test_skeleton_reproduces_source_exactly(text):
    nodes, segs = parse(text, [])
    for s in segs:
        s["target"] = s["masked"]
    out, _ = render({"nodes": nodes, "segments": segs, "lang": "en"}, CFG)
    assert out == text
