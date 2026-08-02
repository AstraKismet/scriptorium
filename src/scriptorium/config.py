"""Project configuration, glossary, and do-not-translate list."""

import json
import os

STATE = ".lx"

#: The register a document is in when nobody said otherwise — and the value the
#: translation-memory key treats as its null, so that everything banked before
#: registers existed keeps answering (see :func:`store.key_tone`). Named once
#: rather than written as a literal in both places: the two have to be the same
#: string, and a drift between them silently invalidates the whole memory.
DEFAULT_TONE = "technical"

#: The glossary's own first two lines. Named once because two places write them:
#: `write_templates` scaffolding a fresh project, and `lx terms --append` creating
#: the file when the project never ran `lx init`. A second, drifted copy of a
#: header is the kind of thing nobody notices until two projects disagree about
#: what column three means.
GLOSSARY_HEADER = (
    "source,target,forbidden,severity\n"
    "# forbidden is ;-separated; severity is error or warn\n"
    "# a row with an empty target is a proposal from `lx terms` and enforces\n"
    "# nothing until someone writes the rendering in\n"
)

#: The plain-text format's knobs, all three of them heuristics. They live here
#: rather than in `textparse.py` for two reasons: `lx init` scaffolds
#: `DEFAULT_CONFIG` verbatim, so a knob that is not here is a knob nobody
#: discovers; and `textparse` reads these as its own fallbacks, so there is one
#: literal rather than two that drift. `config.py` imports nothing from this
#: package, which is what makes that direction of the dependency safe.
#:
#: Invariant 4 is why they are configuration at all: encoding detection, chapter
#: detection and paragraph segmentation are heuristics, and a heuristic is a
#: project's call rather than a rule `checks.py` may enforce.
TEXT_DEFAULTS = {
    # Ordered, and the order is the whole decision: tried with `errors="strict"`,
    # first success wins, after a BOM sniff that decides on its own. Every name
    # must be BOM-neutral — never `utf-16` or `utf-8-sig`, which write a mark of
    # their own — because a mark that survives is what keeps the skeleton exact.
    #
    # Measured 2026-08-02, and every entry earns its place by a measurement:
    #
    # `cp950`, not `big5`. Python's `big5` codec rejects 裏, 碁, 恒 and 墻, which
    # are ordinary characters in Windows-authored Traditional Chinese, so a real
    # Big5 novel containing one of them failed every DBCS candidate and was read
    # by `cp1252` as Latin-1 gibberish — the worst outcome available, because it
    # is durable: the mojibake is hashed and banked in the translation memory.
    # `cp950` is a superset and decodes all four.
    #
    # `shift_jis` before the Chinese candidates. It fails on the Big5 and GBK
    # samples, so it costs them nothing, and without it `gbk` swallows Japanese.
    #
    # `gbk`, not `gb18030`. The superset argument that wins for cp950 loses here:
    # gb18030 is a near-total catch-all for double-byte input and accepted the
    # Big5, Shift-JIS and Latin-1 samples too.
    #
    # `cp1252` last and still present. A Windows-authored English novel with
    # smart quotes is the commonest non-UTF-8 source there is, and dropping it
    # refuses that file outright. It accepts every *standard* Big5 and GB2312
    # byte stream — none of its five undefined bytes appears in either range — so
    # it is a catch-all rather than a near one, and only its position and the
    # cp950 repair above keep a Chinese novel from reaching it.
    #
    # What is left is the irreducible overlap: simplified Chinese reads as Big5,
    # and a Latin-1 European source reads as Shift-JIS. Both are announced by
    # `lx extract`, which prints the winning encoding, rather than fixed by a
    # cleverer rule — refusing on ambiguity was the alternative and it refuses
    # every ordinary Big5, GBK and Shift-JIS novel, since `cp1252` also accepts
    # each of them. A project reorders this list; that is what it is for.
    "encodings": ["utf-8", "shift_jis", "cp950", "gbk", "cp1252"],
    # `auto`, `blank-line`, `line`, or `indent`. Plain text arrives in three
    # shapes and only two of them can be told apart safely, so auto decides
    # between those two and the third is named rather than guessed: if some blank
    # line separates two runs of text, paragraphs are blank-line separated and a
    # hard-wrapped paragraph is one segment; otherwise every line is a paragraph,
    # which is how a great many .txt novels are written. `indent` is the third —
    # hard-wrapped with an indent marking each new paragraph and no blank lines
    # anywhere — and a project holding one says so, because the test that would
    # detect it (some lines indented, some not) is also true of a per-line file
    # with one indented line in it, where guessing wrong joins the whole book.
    "paragraph_mode": "auto",
    # Case-insensitive, matched against a block that is exactly one line, with
    # its surrounding whitespace stripped. Two patterns rather than one because
    # the keywords split into two groups with different false-positive risks.
    #
    # `chapter`, `part`, `book` and `volume` all open ordinary English sentences
    # — "Part of her wanted to run." — so they require a number after them. The
    # second group is words that essentially only ever stand as a title.
    #
    # Both directions are cheap, which is what makes a heuristic acceptable here:
    # a miss leaves a chapter title as an ordinary paragraph, still translated,
    # and a false positive costs the `heading` kind and its memory context. What
    # is *not* cheap is editing these patterns later — see `docs/decisions.md`,
    # 2026-08-02: `context` is the kind, so a block that changes kind orphans its
    # banked wording while its text stays byte-identical.
    "chapter_patterns": [
        r"(?:chapter|part|book|volume)\b[\s.:—–-]*"
        r"(?:\d{1,4}|[ivxlcdm]{1,8}|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
        r"first|second|third|last|final)\b.{0,30}$",
        r"(?:prologue|epilogue|interlude|prelude|foreword|preface|afterword|appendix)\b.{0,30}$",
    ],
}

DEFAULT_CONFIG = {
    "source_lang": "en",
    "targets": ["zh-TW"],
    "tone": DEFAULT_TONE,
    "glossary": "config/glossary.csv",
    "dnt": "config/dnt.txt",
    "sources": ["docs/**/*.md"],
    "output_pattern": "i18n/{lang}/{path}",
    "length_ratio": {"zh-TW": [0.25, 1.20]},
    "normalize": {"zh-TW": ["punct", "pangu", "collapse_space"]},
    "lexicon_extra": {},
    "checks_disabled": [],
    # Which parser reads a document, and the per-format knobs. `map` is the
    # explicit override on the built-in extension table in `formats.py` —
    # `{".nfo": "text"}` — and is the only override there is, deliberately: a
    # `--format` flag would let one invocation disagree with the next about what
    # a file is, and the format is frozen onto the document at extract.
    "formats": {"map": {}, "text": dict(TEXT_DEFAULTS)},
    # `lx terms` is a heuristic, so its knobs are configuration rather than a
    # fixed table — the same rule chapter detection follows. Invariant 4 keeps
    # judgement out of `checks.py`; this command proposes rather than decides,
    # and what counts as a proposal is the project's call.
    "terms": {
        # 2, not 3: a name seen once is not enforceable terminology, and anything
        # higher silently drops the secondary characters a novel is full of. The
        # output is a list a person edits, so a spare row costs one keystroke
        # and a missing one costs the whole point of the command.
        "min_count": 2,
        # A full stop after one of these does not end a sentence, so the name
        # that follows keeps its mid-sentence evidence. `Mr. Darcy` is the case:
        # without this, a character named only after an honorific never appears
        # anywhere this command would call mid-sentence.
        "abbreviations": ["Mr", "Mrs", "Ms", "Dr", "Prof", "St", "Sr", "Jr",
                          "Mt", "Capt", "Lt", "Sgt", "Col", "Gen", "Rev", "Hon"],
        # The one English word that is capitalized everywhere and is never a
        # term. Kept as a list rather than a rule because the next entry a
        # project needs is a project's own.
        "stopwords": ["I", "I'm", "I'll", "I've", "I'd"],
    },
    "providers": {
        "local": {
            "kind": "openai",
            "base_url": "http://localhost:11434/v1",
            "model": "qwen2.5:14b-instruct",
            "api_key_env": "",
            "timeout": 300,
            "temperature": 0.2,
        },
        "lmstudio": {
            "kind": "openai",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key_env": "",
            "timeout": 300,
            "temperature": 0.2,
        },
        "openai": {
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "timeout": 120,
            "temperature": 0.2,
        },
        "claude": {
            "kind": "anthropic",
            "base_url": "https://api.anthropic.com",
            "model": "claude-sonnet-4-6",
            "api_key_env": "ANTHROPIC_API_KEY",
            "timeout": 120,
            "temperature": 0.2,
        },
    },
    "routing": {"draft": "local", "polish": "local", "repair": "local"},
    "batch": {"size": 25, "concurrency": 2, "max_repair_rounds": 3},
}


def canonical_tone(value):
    """What counts as the same register: the tone, stripped and lowercased.

    One normalizer for the two readers that must agree — `translate` picks the
    language brief with it, `store` builds the memory key from it — because
    ``--tone Literary`` and ``--tone literary`` naming two registers, and
    therefore two sets of banked wording, is a split nobody would ever find.

    It decides sameness only. The user's own string still reaches the model on
    the ``Tone:`` line, and an unrecognized one still selects the default
    register's brief, so this narrows nothing the field could say before.
    """
    return str(value or "").strip().lower() or DEFAULT_TONE


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is None:
            raise FileNotFoundError(path)
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    """Write JSON atomically, with LF whatever platform ran the command.

    The terminator is a *choice* here, not an invariant: `docio` exists because
    invariant 2a claims the bytes of user documents, and it explicitly excludes
    the files this project writes for itself. What argues for it anyway is that
    two of these land in someone's repository — `lx.config.json` from `lx init`,
    and the `.lx/` state for anyone who tracks it — so leaving the default meant
    one command producing a different tree depending on the machine that ran it,
    and the whole diff showing up the first time two of them shared a project.
    One keyword per site is a cheap price for that, and it costs nothing to read.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _merge(base, override):
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path="lx.config.json"):
    """User config layered over defaults, so new keys never break old projects."""
    return _merge(DEFAULT_CONFIG, load_json(path, {}))


def load_glossary(cfg):
    path = cfg.get("glossary", "config/glossary.csv")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if i == 0 and parts[0].lower() == "source":
                continue
            if len(parts) < 2:
                continue
            rows.append({
                "source": parts[0],
                "target": parts[1],
                "forbidden": [x for x in (parts[2].split(";") if len(parts) > 2 and parts[2] else []) if x],
                "severity": parts[3] if len(parts) > 3 and parts[3] else "error",
            })
    return rows


def load_dnt(cfg):
    path = cfg.get("dnt", "config/dnt.txt")
    if not os.path.exists(path):
        return []
    terms = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return sorted(set(terms), key=len, reverse=True)


def write_templates():
    """Idempotent scaffolding for a fresh project.

    `newline="\\n"` for the same reason as `dump_json`: these two files are
    edited and committed by the user, so a scaffolder that emits CRLF on one
    machine and LF on another hands them a diff they did not make.
    """
    created = []
    if not os.path.exists("lx.config.json"):
        dump_json("lx.config.json", DEFAULT_CONFIG)
        created.append("lx.config.json")
    for d in (os.path.join(STATE, "docs"), os.path.join(STATE, "reports"), "config"):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists("config/glossary.csv"):
        with open("config/glossary.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write(GLOSSARY_HEADER)
        created.append("config/glossary.csv")
    if not os.path.exists("config/dnt.txt"):
        with open("config/dnt.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write("# verbatim terms, one per line; longest match wins\n")
        created.append("config/dnt.txt")
    return created
